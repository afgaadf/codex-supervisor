# -*- coding: utf-8 -*-
"""merge_archive.py —— 把 90_归档/Nexus开发历史 里的"每版本一套同名文档"合并成"每版本一篇"

规矩（库内 AGENTS.md）：
  · 不删除正文 —— 每个原始文件的内容都整段搬进合并笔记（加 ## 文件名 小节）
  · 原文件夹**先整包备份到库外**，再从库里移走（是移动，不是删除）
用法：
  python merge_archive.py plan     # 只算不改
  python merge_archive.py run      # 备份 + 合并 + 移走原文件夹
"""
from __future__ import annotations
import json, shutil, sys, time
from datetime import datetime
from pathlib import Path

V = Path(r"C:\Users\taich\Documents\Obsidian Vault")
ROOT = V / "90_归档" / "Nexus开发历史"
MERGED = ROOT / "_合并"
BACKUP = Path(r"C:\Users\taich\.codex\supervisor\vault_merge_backup") / datetime.now().strftime("%Y%m%d-%H%M%S")
SKIP = {"_合并", "概览"}


def plan():
    groups = []
    if not ROOT.exists():
        return {"ok": False, "error": "找不到目录：%s" % ROOT}
    for d in sorted(p for p in ROOT.iterdir() if p.is_dir() and p.name not in SKIP):
        mds = sorted(d.rglob("*.md"))
        if not mds:
            continue
        groups.append({"folder": d.name, "files": len(mds),
                       "bytes": sum(f.stat().st_size for f in mds)})
    total_files = sum(g["files"] for g in groups)
    return {"ok": True, "groups": len(groups), "files": total_files,
            "bytes": sum(g["bytes"] for g in groups),
            "top": sorted(groups, key=lambda g: -g["files"])[:8]}


def run():
    p = plan()
    if not p.get("ok"):
        return p
    BACKUP.mkdir(parents=True, exist_ok=True)
    MERGED.mkdir(parents=True, exist_ok=True)
    made = moved = 0
    for g in p["top"] if False else []:
        pass
    for d in sorted(x for x in ROOT.iterdir() if x.is_dir() and x.name not in SKIP):
        mds = sorted(d.rglob("*.md"))
        if not mds:
            continue
        # 1) 合并成一篇
        parts = ["---", "type: 归档合并", "title: \"%s\"" % d.name, "updated: %s" % datetime.now().strftime("%Y-%m-%d"),
                 "tags:", "  - 归档/Nexus开发历史", "---", "",
                 "# %s（合并自 %d 个文档）" % (d.name, len(mds)), "",
                 "> 由电脑管家自动合并：%s ｜ 原始文件已整包备份到库外（不删除）" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
                 "## 本包文件清单", ""]
        for f in mds:
            parts.append("- `%s`（%.1f KB）" % (f.relative_to(d).as_posix(), f.stat().st_size / 1024))
        for f in mds:
            rel = f.relative_to(d).as_posix()
            try:
                body = f.read_text(encoding="utf-8-sig", errors="replace")
            except Exception as e:
                body = "（读不到：%s）" % e
            parts += ["", "---", "", "## %s" % rel, "", body.strip()]
        out = MERGED / ("%s.md" % d.name)
        if out.exists():
            out = MERGED / ("%s-2.md" % d.name)
        out.write_text("\n".join(parts) + "\n", encoding="utf-8")
        made += 1
        # 2) 原文件夹 → 备份（移动，不删除）
        dest = BACKUP / d.name
        shutil.move(str(d), str(dest))
        moved += 1
    return {"ok": True, "merged_notes": made, "moved_folders": moved,
            "backup": str(BACKUP), "merged_dir": str(MERGED)}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    r = run() if cmd == "run" else plan()
    print(json.dumps(r, ensure_ascii=False, indent=1))
