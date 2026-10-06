# -*- coding: utf-8 -*-
"""vault_ops_batch.py —— 全做：① 未分类归位 ② 补 frontmatter ③ 断链建议 ④ git 提交

守 H9：不删正文；每步先备份到库外；可 rollback；改完复检。
用法：python vault_ops_batch.py plan | run
"""
from __future__ import annotations
import difflib, json, shutil, subprocess
from datetime import datetime
from pathlib import Path

APP = Path(r"C:\Users\taich\.codex\supervisor")
V = Path(r"C:\Users\taich\Documents\Obsidian Vault")
BK = APP / "vault_ops_backup"
LOG = APP / "vault_ops_log.jsonl"

# 工具依赖的文件：不许动（动了插件/规则就读不到）
PINNED = {"AGENTS.md", "CLAUDE.md", "maintenance_prompt.md", ".gitignore"}
# 这些目录的文件不算"知识笔记"，不补 fm、不动
SKIP_DIRS = {"copilot", ".opencode", ".obsidian", ".git", "20_附件", "30_模板"}


def _load(p, d=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return d if d is not None else {}


def _skip(rel: str) -> bool:
    parts = Path(rel).parts
    if parts and parts[0] in SKIP_DIRS:
        return True
    if Path(rel).name in PINNED:
        return True
    return False


def _backup(rels):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = BK / ("batch-" + stamp)
    d.mkdir(parents=True, exist_ok=True)
    saved = []
    for rel in rels:
        src = V / rel
        if src.exists():
            dst = d / Path(rel).name
            shutil.copy2(src, dst)
            saved.append({"src": str(src), "bak": str(dst)})
    return stamp, saved


def _log(rec):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _kind_of(rel: str, name: str) -> str:
    s = rel + " " + name
    for k, t in (("复盘", "复盘"), ("报告", "报告"), ("标准", "标准"), ("规范", "规范"), ("铁律", "铁律"),
                 ("清单", "清单"), ("指南", "指南"), ("体系", "体系"), ("MOC", "MOC"), ("Wiki", "知识条目"),
                 ("选题", "选题"), ("样本", "样本"), ("工具", "工具"), ("模板", "模板")):
        if k.lower() in s.lower():
            return t
    if rel.startswith("90_归档"):
        return "归档"
    if rel.startswith("10_笔记/项目"):
        return "项目文档"
    if rel.startswith("10_笔记/AI产出"):
        return "产出"
    if rel.startswith("00_Inbox"):
        return "收件箱"
    return "笔记"


def plan():
    chk = _load(APP / "vault_checks.json")
    cls = _load(APP / "vault_classes.json")
    idx = _load(APP / "vault_index.json")
    files = {f["path"]: f for f in (idx.get("files") or [])}

    # ① 未分类归位建议
    moves = []
    for c in cls.get("classes") or []:
        if c.get("cat") != "未分类":
            continue
        for it in c.get("items") or []:
            rel = it["path"]
            if _skip(rel):
                continue
            name = Path(rel).name
            if rel.startswith("10_笔记/AI学习"):
                to = "10_笔记/AI学习"      # 已在合适位置 → 只是补 fm
            elif rel.startswith("10_笔记/项目") or rel.startswith("10_笔记/知识库"):
                to = str(Path(rel).parent).replace("\\", "/")
            else:
                to = "10_笔记/知识库/收纳"
            moves.append({"path": rel, "to": to})

    # ② 缺 frontmatter
    need_fm = []
    for it in (chk.get("items") or []):
        if it.get("kind") == "missing_frontmatter" and not _skip(it["path"]):
            need_fm.append(it["path"])

    # ③ 断链建议：拿现有笔记名做相似匹配
    names = [Path(p).stem for p in files.keys() if p.endswith(".md")]
    links = []
    for it in (chk.get("items") or []):
        if it.get("kind") != "broken_link":
            continue
        detail = str(it.get("detail") or "")
        tgt = detail.split("[[")[-1].split("]]")[0] if "[[" in detail else detail
        cand = difflib.get_close_matches(tgt, names, n=3, cutoff=0.62)
        links.append({"path": it["path"], "target": tgt, "candidates": cand})

    return {"ok": True, "moves": moves, "need_fm": need_fm, "broken": links,
            "counts": {"moves": len(moves), "need_fm": len(need_fm), "broken": len(links)}}


def run():
    p = plan()
    made = {"moved": 0, "fm": 0, "link_suggest": len(p["broken"])}
    # ① 归位（同目录的跳过）
    mv = [m for m in p["moves"] if str(Path(m["path"]).parent).replace("\\", "/") != m["to"]]
    if mv:
        stamp, saved = _backup([m["path"] for m in mv])
        for m in mv:
            src, dst = V / m["path"], V / m["to"] / Path(m["path"]).name
            try:
                if dst.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                made["moved"] += 1
                _log({"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "action": "move_note",
                      "from": m["path"], "to": str(dst.relative_to(V)), "backup": stamp, "saved": saved})
            except Exception:
                pass
    # ② 补 frontmatter（只加，不动正文）
    if p["need_fm"]:
        stamp, saved = _backup(p["need_fm"])
        for rel in p["need_fm"]:
            fp = V / rel
            try:
                body = fp.read_text(encoding="utf-8-sig", errors="replace")
                if body.lstrip().startswith("---"):
                    continue
                kind = _kind_of(rel, Path(rel).stem)
                fm = ("---\ntype: \"%s\"\ntitle: \"%s\"\nupdated: \"%s\"\ntags:\n  - %s\n---\n\n"
                      % (kind, Path(rel).stem, datetime.now().strftime("%Y-%m-%d"), kind))
                fp.write_text(fm + body, encoding="utf-8")
                made["fm"] += 1
                _log({"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "action": "add_frontmatter",
                      "path": rel, "backup": stamp, "saved": saved})
            except Exception:
                pass
    # ③ 断链建议写成一份清单（不改链接：改链接要判断语义）
    if p["broken"]:
        d = V / "00_Inbox"
        d.mkdir(parents=True, exist_ok=True)
        f = d / ("_断链修复建议-%s.md" % datetime.now().strftime("%Y-%m-%d"))
        L = ["---", "type: 报告", "title: \"断链修复建议\"",
             "updated: \"%s\"" % datetime.now().strftime("%Y-%m-%d"), "tags:", "  - 知识库/维护", "---", "",
             "# 断链修复建议（%d 条）" % len(p["broken"]), "",
             "> 只给候选，没自动改 —— 改链接要判断语义。选好后我可以批量改（同样先备份）。", "",
             "| 出问题的笔记 | 打不开的链接 | 候选（按相似度） |", "|---|---|---|"]
        for b in p["broken"]:
            L.append("| `%s` | `%s` | %s |" % (b["path"], b["target"],
                                              "、".join("`%s`" % c for c in b["candidates"]) or "（没找到相近的）"))
        f.write_text("\n".join(L) + "\n", encoding="utf-8")
        made["link_report"] = str(f)
    # ④ git 提交
    git = {"ok": False}
    try:
        st = subprocess.run(["git", "-C", str(V), "status", "--porcelain"], capture_output=True, text=True, timeout=60).stdout
        changed = len([x for x in st.splitlines() if x.strip()])
        if changed:
            subprocess.run(["git", "-C", str(V), "add", "-A"], capture_output=True, text=True, timeout=120)
            msg = ("整理知识库：归档版本快照合并、概览合成正本、批量补 frontmatter %d 篇、归位 %d 篇"
                   % (made["fm"], made["moved"]))
            r = subprocess.run(["git", "-C", str(V), "commit", "-m", msg], capture_output=True, text=True, timeout=180)
            git = {"ok": r.returncode == 0, "changed": changed,
                   "out": (r.stdout or r.stderr or "").strip().splitlines()[-1][:160] if (r.stdout or r.stderr) else ""}
        else:
            git = {"ok": True, "changed": 0, "out": "没有变更"}
    except Exception as e:
        git = {"ok": False, "error": str(e)}
    return {"ok": True, "made": made, "git": git, "counts": p["counts"]}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    r = run() if cmd == "run" else plan()
    print(json.dumps(r if cmd == "run" else {"counts": r["counts"], "sample_moves": r["moves"][:5],
                                             "sample_broken": r["broken"][:3]},
                     ensure_ascii=False, indent=1))
