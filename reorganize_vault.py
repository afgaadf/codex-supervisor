# -*- coding: utf-8 -*-
"""reorganize_vault.py —— 重新整理一份：① 概览合并成一篇正本 ② 生成全库总目录

规矩：正文不进垃圾桶 —— 原件整包备份到库外，再从库里移走（移动，不删除）。
"""
from __future__ import annotations
import json, shutil, sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from paths import APP_DIR, VAULT_DIR as V, DATA_DIR
ARCH = V / "90_归档" / "Nexus开发历史"
GLANCE = ARCH / "概览"
BACKUP = DATA_DIR / "vault_merge_backup" / ("reorg-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
TOC = V / "知识库总目录.md"


def merge_glance():
    if not GLANCE.exists():
        return {"ok": False, "error": "没有 概览 目录"}
    mds = sorted(GLANCE.rglob("*.md"))
    if not mds:
        return {"ok": False, "error": "概览里没有 md"}
    BACKUP.mkdir(parents=True, exist_ok=True)
    parts = ["---", "type: 项目总览", "title: \"Nexus 架构总览（正本）\"",
             "updated: %s" % datetime.now().strftime("%Y-%m-%d"),
             "tags:", "  - Nexus", "  - 项目/总览", "---", "",
             "# Nexus 架构总览（正本）", "",
             "> 由电脑管家合并：%s ｜ 合并自 %d 篇各文档最新版；原件已备份到库外" % (
                 datetime.now().strftime("%Y-%m-%d %H:%M"), len(mds)), "",
             "## 目录", ""]
    for f in mds:
        parts.append("- [[#%s]]" % f.stem)
    for f in mds:
        try:
            body = f.read_text(encoding="utf-8-sig", errors="replace").strip()
        except Exception as e:
            body = "（读不到：%s）" % e
        parts += ["", "---", "", "## %s" % f.stem, "", body]
    out = ARCH / "Nexus 架构总览（正本）.md"
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    dest = BACKUP / "概览"
    shutil.move(str(GLANCE), str(dest))
    return {"ok": True, "merged_from": len(mds), "out": str(out), "backup": str(dest)}


def build_toc():
    """按"项目 / 主题 / 归档"重新组织成一份总目录。"""
    SKIP_DIRS = {".obsidian", ".git", ".opencode", "copilot", ".agents", ".claudian", ".copilot", "20_附件", "_合并"}
    notes = [p for p in V.rglob("*.md") if not (set(p.relative_to(V).parts) & SKIP_DIRS)]
    tree = defaultdict(int)
    for p in notes:
        rel = p.relative_to(V)
        tree[rel.parent.as_posix()] += 1
    top = sorted(((k, v) for k, v in tree.items() if k != "."), key=lambda kv: -kv[1])[:14]

    def exists(*names):
        return [n for n in names if (V / n).exists()]

    lines = ["---", "type: 首页", "title: \"知识库总目录\"",
             "updated: %s" % datetime.now().strftime("%Y-%m-%d"),
             "tags:", "  - 知识库/首页", "---", "",
             "# 知识库总目录", "",
             "> 由电脑管家整理：%s ｜ 当前 %d 篇笔记（不含归档合并件）" % (
                 datetime.now().strftime("%Y-%m-%d %H:%M"), len(notes)), "",
             "## 三块地", ""]
    for title, cands, desc in (
            ("抖音视频制作", ["10_笔记/项目/抖音视频制作/MOC_抖音视频制作.md", "10_笔记/项目/抖音视频制作"],
             "规范/标准/选题/复盘，77 篇"),
            ("Nexus", ["10_笔记/项目/Nexus", "90_归档/Nexus开发历史/Nexus 架构总览（正本）.md"],
             "网站与价格雷达；架构正本 + 开发历史"),
            ("电脑管家", ["00_Inbox/2026-10-06 监督者系统复盘（界面重设计 + 评估体系v2）.md"],
             "监管 Codex 与 Obsidian 本身")):
        lines.append("### %s" % title)
        lines.append("- %s" % desc)
        for c in exists(*cands):
            p = V / c
            lines.append("  - %s" % (("[[%s]]" % (c[:-3] if c.endswith(".md") else c)) if p.is_file() else "`%s/`（%d 篇）" % (c, tree.get(c, 0))))
        lines.append("")
    lines += ["## 归档（合并后）", "",
              "- `90_归档/Nexus开发历史/_合并/` —— 156 篇版本合并笔记（每版本一篇，原文全在）",
              "- `90_归档/Nexus开发历史/Nexus 架构总览（正本）.md` —— 各文档最新版合成的一篇",
              "- 原件备份在库外：`vault_merge_backup/`（不删除）", "",
              "## 维护入口", "",
              "- 优先级清单：`00_Inbox/_知识库优先级-*.md`（P0 立刻 / P1 核心 / P2 常规 / P3 归档）",
              "- 库规：`AGENTS.md`（不删正文 / 无法归类进 00_Inbox / 维护完提交）",
              "- 体检：电脑管家 → Obsidian 库（断链 / 缺 frontmatter / 收件箱 / 未提交）", "",
              "## 笔记最多的 14 个目录", ""]
    for k, v in top:
        lines.append("- `%s/` —— %d 篇" % (k, v))
    TOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "toc": str(TOC), "notes": len(notes), "dirs": len(tree)}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "toc"
    if cmd == "merge":
        print(json.dumps(merge_glance(), ensure_ascii=False, indent=1))
    elif cmd == "all":
        print(json.dumps({"merge": merge_glance(), "toc": build_toc()}, ensure_ascii=False, indent=1))
    else:
        print(json.dumps(build_toc(), ensure_ascii=False, indent=1))
