# -*- coding: utf-8 -*-
"""vault_priority.py —— 知识库优先级（摆明规则，不猜）

输入（都是插件推来的真实数据）：
  · vault_index.json  —— 794 篇笔记的路径/大小/时间/frontmatter/出链
  · vault_checks.json —— 断链/缺 frontmatter/收件箱堆积/未提交（插件体检结果）

优先级规则（权重可调，写在 RULES 里，界面会显示）：
  P0 立刻处理：断链所在文件 · 00_Inbox 超期 · "规范/标准/铁律/清单"类缺 frontmatter · 库有未提交改动
  P1 核心资产：10_笔记/项目 下的、或被引用 ≥3 次的笔记，缺 frontmatter 或最近改过
  P2 常规整理：其余缺 frontmatter · 孤立笔记（无出链也无反链）
  P3 归档参考：90_归档 / 20_附件 下的同类问题
"""
from __future__ import annotations
import json, time
from pathlib import Path
from datetime import datetime, timezone

APP_DIR = Path(__file__).resolve().parent
IDX = APP_DIR / "vault_index.json"
CHK = APP_DIR / "vault_checks.json"
OUT_MD = None  # 由 report_to_vault() 设置

RULES = [
    ("P0", "断链：别人引用了它，点进去打不开", 10),
    ("P0", "00_Inbox 超期没归类", 8),
    ("P0", "库里有未提交改动（库规：维护完要提交）", 6),
    ("P1", "规范/标准/铁律/清单 类缺 frontmatter", 5),
    ("P1", "10_笔记/项目 下的笔记（核心资产）", 4),
    ("P1", "被引用 ≥3 次（别人依赖它）", 3),
    ("P2", "缺 frontmatter（机器读不懂）", 2),
    ("P2", "孤立笔记（没有入链也没出链）", 1),
    ("P3", "90_归档 / 20_附件 里的同类问题", 0),
]

KEYWORDS = ("规范", "标准", "铁律", "清单", "体系", "指南", "SOP", "手册", "要求")


def _load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _basename(link):
    s = str(link or "").split("|")[0].split("#")[0].strip()
    return s.split("/")[-1].lower()


def compute():
    idx = _load(IDX, {}) or {}
    chk = _load(CHK, {}) or {}
    files = idx.get("files") or []
    items = chk.get("items") or []
    summary = chk.get("summary") or {}
    if not files:
        return {"ok": False, "error": "还没收到插件索引（vault_index.json 空）", "groups": {}, "rules": RULES}

    by_name = {}
    for f in files:
        by_name.setdefault(str(f.get("name") or "").lower().replace(".md", ""), []).append(f)
    back = {}
    for f in files:
        for l in (f.get("links") or []):
            b = _basename(l)
            back[b] = back.get(b, 0) + 1

    now = time.time()
    issues = {}
    for it in items:
        issues.setdefault(str(it.get("path") or ""), []).append(it)

    rows = []
    for f in files:
        path = str(f.get("path") or "")
        name = str(f.get("name") or "")
        key = name.lower().replace(".md", "")
        mt = float(f.get("mtime") or 0) / 1000.0
        sc, why = 0, []
        for it in issues.get(path, []):
            k = it.get("kind")
            if k == "broken_link":
                sc += 10; why.append("P0 断链：%s" % (it.get("detail") or ""))
            elif k == "inbox_stale":
                sc += 8; why.append("P0 收件箱超期：%s" % (it.get("detail") or ""))
            elif k == "missing_frontmatter":
                if any(w in name for w in KEYWORDS):
                    sc += 5; why.append("P1 标准/规范类缺 frontmatter")
                elif path.startswith(("90_归档", "20_附件")):
                    why.append("P3 归档区缺 frontmatter")
                else:
                    sc += 2; why.append("P2 缺 frontmatter")
        if path.startswith("10_笔记/项目"):
            sc += 4; why.append("P1 项目核心资产")
        nb = back.get(key, 0)
        if nb >= 3:
            sc += 3; why.append("P1 被引用 %d 次" % nb)
        if not (f.get("links") or []) and nb == 0:
            sc += 1; why.append("P2 孤立笔记")
        if (summary.get("uncommitted") or 0) > 0 and (now - mt) < 7 * 86400:
            sc += 2
        if sc <= 0:
            continue
        level = "P0" if sc >= 10 else ("P1" if sc >= 5 else ("P2" if sc >= 2 else "P3"))
        rows.append({"path": path, "name": name, "score": sc, "level": level,
                     "why": "；".join(dict.fromkeys(why)), "backlinks": nb,
                     "mtime": datetime.fromtimestamp(mt).strftime("%Y-%m-%d") if mt else "—",
                     "size_kb": round((f.get("size") or 0) / 1024, 1)})
    rows.sort(key=lambda r: (-r["score"], r["path"]))
    groups = {lv: [r for r in rows if r["level"] == lv] for lv in ("P0", "P1", "P2", "P3")}
    counts = {lv: len(groups[lv]) for lv in groups}
    git_line = "库有 %s 个未提交改动（库规第 7 条：维护完必须提交）" % summary.get("uncommitted", 0) \
        if summary.get("uncommitted") else "工作区干净"
    return {"ok": True, "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "counts": counts, "groups": groups, "rules": RULES, "git": git_line,
            "index_ts": idx.get("ts"), "notes": idx.get("count") or len(files),
            "summary": summary}


def report_to_vault(vault_dir=None):
    """把优先级写成一篇笔记，放进库里的 00_Inbox（人可读）。"""
    r = compute()
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    root = Path(vault_dir or r"C:\Users\taich\Documents\Obsidian Vault")
    wd = root / "00_Inbox"
    wd.mkdir(parents=True, exist_ok=True)
    p = wd / ("_知识库优先级-%s.md" % datetime.now().strftime("%Y-%m-%d"))
    lines = ["---", "type: 报告", "title: \"知识库优先级\"", "updated: %s" % datetime.now().strftime("%Y-%m-%d"),
             "tags:", "  - 知识库/维护", "---", "", "# 知识库优先级（自动生成）", "",
             "> 生成时间：%s ｜ 笔记 %s 篇 ｜ %s" % (r["ts"], r["notes"], r["git"]), "",
             "## 排序规则（权重）", ""]
    for lv, desc, w in RULES:
        lines.append("- **%s**（+%d）%s" % (lv, w, desc))
    lines += ["", "## 统计", ""]
    for lv in ("P0", "P1", "P2", "P3"):
        lines.append("- %s：%d 条" % (lv, r["counts"].get(lv, 0)))
    for lv in ("P0", "P1", "P2", "P3"):
        g = r["groups"].get(lv) or []
        if not g:
            continue
        lines += ["", "## %s（%d 条）" % (lv, len(g)), ""]
        lines.append("| 文件 | 分 | 为什么 | 被引 | 最近改 |")
        lines.append("|---|---|---|---|---|")
        for x in g[:40]:
            lines.append("| `%s` | %d | %s | %d | %s |" % (x["path"], x["score"], x["why"], x["backlinks"], x["mtime"]))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(p), "counts": r["counts"]}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        print(json.dumps(report_to_vault(), ensure_ascii=False, indent=1))
    else:
        r = compute()
        print(json.dumps({"ok": r.get("ok"), "counts": r.get("counts"), "git": r.get("git")},
                         ensure_ascii=False, indent=1))
        for lv in ("P0", "P1"):
            for x in (r.get("groups", {}).get(lv) or [])[:8]:
                print("  %s %-46s %s" % (lv, x["path"][:46], x["why"][:60]))
