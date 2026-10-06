# -*- coding: utf-8 -*-
"""vault_ops.py —— 知识库运维（电脑管家负责）

运维闭环（每步可查、可回滚）：
  ① 感知  插件事件 + 索引
  ② 体检  断链 / 缺 frontmatter / 收件箱堆积 / 未提交 / 未分类
  ③ 执行  受控动作：move_note(归位) / add_frontmatter(补元数据) —— 动手前备份，可回滚
  ④ 复检  动作后再体检，比对指标
  ⑤ 日报  写进库里 00_Inbox/_运维日报-YYYY-MM-DD.md + 指标历史 vault_metrics.jsonl
  ⑥ 边界  不删除正文（H9）；不确定的只列"待你拍板"
"""
from __future__ import annotations
import json, shutil, subprocess, time
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
V = Path(r"C:\Users\taich\Documents\Obsidian Vault")
IDX = APP_DIR / "vault_index.json"
CHK = APP_DIR / "vault_checks.json"
CLS = APP_DIR / "vault_classes.json"
METRICS = APP_DIR / "vault_metrics.jsonl"
OPS_LOG = APP_DIR / "vault_ops_log.jsonl"
BACKUP_ROOT = APP_DIR / "vault_ops_backup"


def _load(p, d=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return d if d is not None else {}


def metrics():
    chk = _load(CHK)
    cls = _load(CLS)
    s = chk.get("summary") or {}
    counts = cls.get("counts") or {}
    return {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "notes": s.get("notes") or cls.get("notes") or 0,
            "broken_links": int(s.get("broken_links") or 0),
            "missing_frontmatter": int(s.get("missing_frontmatter") or 0),
            "inbox_stale": int(s.get("inbox_stale") or 0),
            "uncommitted": int(s.get("uncommitted") or 0),
            "unclassified": int(counts.get("未分类") or 0)}


def record_metrics():
    m = metrics()
    try:
        with METRICS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
        if METRICS.stat().st_size > 2 * 1024 * 1024:
            lines = METRICS.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
            METRICS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass
    return m


def trend(n=14):
    try:
        return [json.loads(x) for x in METRICS.read_text(encoding="utf-8", errors="replace").splitlines()[-n:] if x.strip()]
    except Exception:
        return []


def health_score(m=None):
    m = m or metrics()
    score = 100.0
    score -= min(30, m["broken_links"] * 3)
    score -= min(30, m["missing_frontmatter"] * 0.2)
    score -= min(15, m["inbox_stale"] * 5)
    score -= min(15, m["uncommitted"] * 0.2)
    score -= min(10, m["unclassified"] * 1)
    return max(0, round(score))


def pending_actions(limit=20):
    cls = _load(CLS)
    chk = _load(CHK)
    out = []
    for c in cls.get("classes") or []:
        if c.get("cat") == "未分类":
            for it in (c.get("items") or [])[:limit]:
                out.append({"kind": "classify", "path": it.get("path"), "why": "未分类",
                            "suggest": c.get("suggest")})
    for it in (chk.get("items") or [])[:limit]:
        if it.get("kind") == "missing_frontmatter":
            out.append({"kind": "frontmatter", "path": it.get("path"), "why": "缺 frontmatter",
                        "suggest": "补 type/title/updated"})
        elif it.get("kind") == "broken_link":
            out.append({"kind": "link", "path": it.get("path"), "why": it.get("detail") or "断链",
                        "suggest": "改成现有笔记名或删掉链接"})
    return out[:limit * 2]


def _backup(paths):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = BACKUP_ROOT / stamp
    d.mkdir(parents=True, exist_ok=True)
    saved = []
    for rel in paths:
        src = V / rel
        if src.exists():
            dst = d / Path(rel).name
            shutil.copy2(src, dst)
            saved.append({"src": str(src), "bak": str(dst)})
    return stamp, saved


def _log(rec):
    try:
        with OPS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def move_note(rel, to_dir, dry_run=True):
    src = V / rel
    if not src.exists():
        return {"ok": False, "error": "找不到：%s" % rel}
    dst = V / to_dir / src.name
    if dst.exists():
        return {"ok": False, "error": "目标已存在，不覆盖：%s" % dst}
    if dry_run:
        return {"ok": True, "dry_run": True, "would": "%s → %s" % (rel, dst.relative_to(V))}
    stamp, saved = _backup([rel])
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    _log({"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "action": "move_note",
          "from": rel, "to": str(dst.relative_to(V)), "backup": stamp, "saved": saved})
    return {"ok": True, "moved": str(dst.relative_to(V)), "backup": stamp}


def add_frontmatter(rel, fields, dry_run=True):
    src = V / rel
    if not src.exists():
        return {"ok": False, "error": "找不到：%s" % rel}
    body = src.read_text(encoding="utf-8-sig", errors="replace")
    if body.lstrip().startswith("---"):
        return {"ok": False, "error": "已有 frontmatter，不重复加（要改请人工）"}
    fm = ["---"]
    for k in ("type", "title", "updated", "tags"):
        if fields.get(k) is not None:
            v = fields[k]
            if isinstance(v, list):
                fm.append("%s:" % k)
                for x in v:
                    fm.append("  - %s" % x)
            else:
                fm.append('%s: "%s"' % (k, v))
    fm.append("---")
    new = "\n".join(fm) + "\n\n" + body
    if dry_run:
        return {"ok": True, "dry_run": True, "would": "在 %s 顶部加 %d 行 frontmatter" % (rel, len(fm)),
                "preview": "\n".join(fm)}
    stamp, saved = _backup([rel])
    src.write_text(new, encoding="utf-8")
    _log({"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "action": "add_frontmatter",
          "path": rel, "backup": stamp, "saved": saved})
    return {"ok": True, "path": rel, "backup": stamp}


def rollback(stamp=None):
    if not BACKUP_ROOT.exists():
        return {"ok": False, "error": "没有备份"}
    dirs = sorted([d for d in BACKUP_ROOT.iterdir() if d.is_dir()])
    if not dirs:
        return {"ok": False, "error": "没有备份"}
    d = BACKUP_ROOT / stamp if stamp else dirs[-1]
    if not d.exists():
        return {"ok": False, "error": "没有这个备份：%s" % stamp}
    logs = []
    try:
        for ln in OPS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[::-1]:
            r = json.loads(ln)
            if r.get("backup") == d.name:
                logs.append(r)
            if len(logs) >= 50:
                break
    except Exception:
        pass
    restored = 0
    for r in logs:
        for s in (r.get("saved") or []):
            try:
                tgt = Path(s["src"])
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s["bak"], tgt)
                restored += 1
            except Exception:
                pass
    return {"ok": True, "stamp": d.name, "restored": restored}


SKIP_DIRS = {".agents", "copilot", ".opencode", ".obsidian", ".git", "20_附件", "30_模板"}
PINNED = {"AGENTS.md", "CLAUDE.md", "maintenance_prompt.md"}


def local_rescan():
    """像插件那样体检一遍，但直接用磁盘：写回 vault_checks.json（插件数据在时以插件为准可覆盖）。"""
    import re, subprocess
    notes = miss = stale = 0
    items = []
    now = time.time()
    for fp in V.rglob("*.md"):
        rel = fp.relative_to(V)
        if set(rel.parts) & SKIP_DIRS or fp.name in PINNED:
            continue
        notes += 1
        try:
            t = fp.read_text(encoding="utf-8-sig", errors="replace")
            m = re.match(r"^---\s*\n(.*?)\n---", t, re.S)
            keys = m.group(1) if m else ""
            lack = [k for k in ("type", "title", "updated")
                    if not re.search(r"^%s\s*:" % k, keys, re.M)]
            if lack:
                miss += 1
                if len(items) < 120:
                    items.append({"kind": "missing_frontmatter", "path": rel.as_posix(),
                                  "detail": "缺 " + "/".join(lack)})
            if rel.as_posix().startswith("00_Inbox/") and now - fp.stat().st_mtime > 7 * 86400:
                stale += 1
                items.append({"kind": "inbox_stale", "path": rel.as_posix(),
                              "detail": "%d 天没动" % ((now - fp.stat().st_mtime) // 86400)})
        except Exception:
            pass
    unc = 0
    try:
        out = subprocess.run(["git", "-C", str(V), "status", "--porcelain"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=60).stdout
        unc = len([x for x in out.splitlines() if x.strip()])
    except Exception:
        pass
    # 自己算真实断链（索引含全部文件，附件也算；支持 [[路径/笔记.md]] 与 [[别名]]）
    try:
        import re as _re2
        allf = [q for q in V.rglob("*") if q.is_file()
                and not (set(q.relative_to(V).parts) & {".git", ".obsidian"})]
        paths2, stems2 = set(), set()
        for q in allf:
            rel2 = q.relative_to(V).as_posix()
            paths2.add(rel2.lower())
            if q.suffix:
                paths2.add(rel2[: -len(q.suffix)].lower())
            stems2.add(q.stem.lower())
        PLACE = _re2.compile(r"\.\.\.|条目名|相关条目|相关主题|^笔记$|^xxx$")
        broken = 0
        for q in allf:
            if q.suffix.lower() != ".md" or (set(q.relative_to(V).parts) & SKIP_DIRS):
                continue
            if q.name in PINNED or q.name.startswith("_") or q.name in ("CLAUDE.md", "AGENTS.md"):
                continue
            body2 = q.read_text(encoding="utf-8-sig", errors="replace")
            for m2 in _re2.finditer(r"\[\[([^\]\|]+?)(\|[^\]]*)?\]\]", body2):
                t2 = m2.group(1).split("#")[0].strip()
                if not t2 or PLACE.search(t2):
                    continue
                cand2 = {t2.lower(), t2.split("/")[-1].lower(), Path(t2).stem.lower()}
                for sfx in (".md", ".png", ".jpg", ".tar.gz", ".pdf"):
                    cand2.add((t2[: -len(sfx)] if t2.lower().endswith(sfx) else t2 + sfx).lower())
                if not (cand2 & paths2 or cand2 & stems2):
                    broken += 1
    except Exception:
        oldchk = _load(CHK)
        broken = int((oldchk.get("summary") or {}).get("broken_links") or 0)
    rec = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
           "summary": {"notes": notes, "broken_links": broken, "missing_frontmatter": miss,
                       "inbox_stale": stale, "big_note": 0, "uncommitted": unc, "git_available": 1,
                       "source": "local_rescan"},
           "items": items}
    CHK.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    try:
        import vault_classify as VC
        VC.compute()          # 同步刷新分类
    except Exception:
        pass
    return rec["summary"]


def _fix_links_deterministic():
    """只修"可确定映射"的断链：概览→正本锚点、版本文件夹→_合并笔记、合并笔记自引用→篇内锚点。"""
    import re
    ARCH = V / "90_归档" / "Nexus开发历史"
    MERGED = ARCH / "_合并"
    TOC = ARCH / "Nexus 架构总览（正本）.md"
    if not MERGED.exists():
        return 0
    ALL = [q for q in V.rglob("*") if q.is_file() and not (set(q.relative_to(V).parts) & {".git", ".obsidian"})]
    paths, stems = set(), set()
    for q in ALL:
        rel = q.relative_to(V).as_posix()
        paths.add(rel.lower()); stems.add(q.stem.lower())
    def ok(t):
        t = t.strip()
        if not t:
            return True
        cand = {t.lower(), t.split("/")[-1].lower(), Path(t).stem.lower(), t + ".md", t[:-3].lower()}
        return bool(cand & paths or cand & stems)
    folders = {d.name for d in MERGED.glob("*.md")}
    changed = 0
    for f in list(MERGED.glob("*.md")) + ([TOC] if TOC.exists() else []):
        t = f.read_text(encoding="utf-8-sig", errors="replace")
        def rep(m):
            nonlocal changed
            raw, alias = m.group(1), (m.group(2) or "")
            tgt = raw.split("#")[0].strip()
            if ok(tgt) or not tgt:
                return m.group(0)
            mm = re.match(r"(?:.*/)?概览/(.+?)(?:\.md)?$", tgt)
            if mm:
                changed += 1
                name = Path(mm.group(1)).stem
                if f == TOC:
                    return "[[#%s%s]]" % (name, alias)
                return "[[90_归档/Nexus开发历史/Nexus 架构总览（正本）#%s%s]]" % (name, alias)
            mm2 = re.match(r"90_归档/Nexus开发历史/([^/]+)/(.+?)(?:\.md)?$", tgt)
            if mm2:
                folder, rest = mm2.group(1), mm2.group(2)
                if folder in folders:
                    changed += 1
                    return "[[90_归档/Nexus开发历史/_合并/%s#%s%s]]" % (folder, Path(rest).stem, alias)
                changed += 1
                return "[[#%s%s]]" % (Path(rest).stem, alias)
            return m.group(0)
        new = re.sub(r"\[\[([^\]\|]+?)(\|[^\]]*)?\]\]", rep, t)
        if new != t:
            (BACKUP_ROOT / "auto").mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, BACKUP_ROOT / "auto" / f.name)
            f.write_text(new, encoding="utf-8-sig")
    return changed


def auto_ops(commit=True):
    """主动运维：体检 → 分类 → 修可确定的断链 → 补 frontmatter → 日报 → 提交。安全动作自己做。"""
    did, need = [], []
    before = metrics()
    local_rescan()
    try:
        import vault_classify as VC
        c = VC.compute()
        if (c.get("counts") or {}).get("未分类"):
            need.append("未分类 %d 篇待归位" % c["counts"]["未分类"])
    except Exception as e:
        need.append("分类失败：%s" % str(e)[:60])
    n = _fix_links_deterministic()
    if n:
        did.append("修可确定断链 %d 处" % n)
    # 补 frontmatter（只加键）
    import re
    fm_fixed = 0
    for q in V.rglob("*.md"):
        rel = q.relative_to(V)
        if set(rel.parts) & SKIP_DIRS or q.name in PINNED:
            continue
        try:
            t = q.read_text(encoding="utf-8-sig", errors="replace")
        except Exception:
            continue
        m = re.match(r"^---\s*\n(.*?)\n---", t, re.S)
        keys = m.group(1) if m else ""
        lack = [k for k in ("type", "title", "updated") if not re.search(r"^%s\s*:" % k, keys, re.M)]
        if not lack:
            continue
        title = q.stem
        today = datetime.now().strftime("%Y-%m-%d")
        if m:
            add = "".join('\n%s: "%s"' % (k, title if k == "title" else (today if k == "updated" else "笔记")) for k in lack)
            newt = "---\n" + m.group(1).rstrip() + add + "\n---" + t[m.end():]
        else:
            newt = '---\ntype: "笔记"\ntitle: "%s"\nupdated: "%s"\n---\n\n%s' % (title, today, t)
        try:
            (BACKUP_ROOT / "auto").mkdir(parents=True, exist_ok=True)
            shutil.copy2(q, BACKUP_ROOT / "auto" / q.name)
            q.write_text(newt, encoding="utf-8-sig")
            fm_fixed += 1
        except Exception:
            pass
    if fm_fixed:
        did.append("补 frontmatter %d 篇" % fm_fixed)
    rep = daily_report(rescan=False)
    did.append("写运维日报 + 记指标")
    after = metrics()
    if after["broken_links"] or after["inbox_stale"]:
        need.append("断链 %d · 收件箱堆积 %d" % (after["broken_links"], after["inbox_stale"]))
    if commit:
        try:
            st = subprocess.run(["git", "-C", str(V), "status", "--porcelain"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60).stdout
            if [x for x in st.splitlines() if x.strip()]:
                subprocess.run(["git", "-C", str(V), "add", "-A"], capture_output=True, timeout=120)
                msg = "自动运维：%s（健康分 %d）" % ("；".join(did) or "体检", rep.get("health", 0))
                subprocess.run(["git", "-C", str(V), "commit", "-m", msg], capture_output=True, timeout=180)
                did.append("git 提交")
        except Exception:
            pass
    rec = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "health": rep.get("health"),
           "did": did, "need_human": need,
           "before": {k: before.get(k) for k in ("broken_links", "missing_frontmatter", "unclassified", "uncommitted")},
           "after": {k: after.get(k) for k in ("broken_links", "missing_frontmatter", "unclassified", "uncommitted")}}
    try:
        import json as _j
        with (APP_DIR / "vault_auto_ops.jsonl").open("a", encoding="utf-8") as f:
            f.write(_j.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


def last_auto():
    try:
        lines = (APP_DIR / "vault_auto_ops.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
        return json.loads(lines[-1]) if lines else {}
    except Exception:
        return {}


def daily_report(rescan=True):
    if rescan:
        try:
            local_rescan()
        except Exception:
            pass
    m = record_metrics()
    score = health_score(m)
    pend = pending_actions(30)
    L = ["---", "type: 运维日报", "title: \"知识库运维日报\"",
         "updated: %s" % datetime.now().strftime("%Y-%m-%d"), "tags:", "  - 知识库/运维", "---", "",
         "# 知识库运维日报（%s）" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
         "**健康分 %d/100**" % score, "",
         "| 指标 | 现在 | 说明 |", "|---|---|---|",
         "| 笔记 | %d | 插件索引 |" % m["notes"],
         "| 断链 | %d | 别人引用了打不开 |" % m["broken_links"],
         "| 缺 frontmatter | %d | 机器读不懂 |" % m["missing_frontmatter"],
         "| 收件箱堆积 | %d | 00_Inbox 超 7 天 |" % m["inbox_stale"],
         "| 未提交 | %d | 库规：维护完要提交 |" % m["uncommitted"],
         "| 未分类 | %d | 分类引擎归不到类 |" % m["unclassified"], "",
         "## 待你拍板（%d 条）" % len(pend), ""]
    for a in pend[:30]:
        L.append("- [%s] `%s` —— %s（建议：%s）" % (a["kind"], a["path"], a["why"], a.get("suggest") or "—"))
    L += ["", "## 可以自动做（都先备份、可回滚）", "",
          "- 未分类归位：`vault_ops.move_note(路径, 目标目录)`",
          "- 缺 frontmatter 补元数据：`vault_ops.add_frontmatter(路径, {...})`",
          "- 回滚最近一次：`vault_ops.rollback()`（原件在 vault_ops_backup）"]
    d = V / "00_Inbox"
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("_运维日报-%s.md" % datetime.now().strftime("%Y-%m-%d"))
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(p), "health": score, "metrics": m, "pending": len(pend)}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "report":
        print(json.dumps(daily_report(), ensure_ascii=False, indent=1))
    elif cmd == "pending":
        print(json.dumps({"pending": pending_actions(10)}, ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"metrics": metrics(), "health": health_score(), "trend": trend(5)},
                         ensure_ascii=False, indent=1))
