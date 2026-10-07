# -*- coding: utf-8 -*-
"""fix_runner.py —— 把“修复计划”变成**可预览、可备份、范围明确**的安全动作。

边界（说做一致）：
  · 只自动补 frontmatter / 建 .gitignore / 写断链候选报告；
  · 不自动改链接（语义候选必须人看）、不删正文、不自动提交 Git；
  · 每个会被改写的旧文件，动手前逐份备份到库外；
  · 计划只读，执行单独走 apply()。
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import checkers
from paths import DATA_DIR, VAULT_DIR

AUTO_ACTIONS = {
    "obsidian.missing_frontmatter": "frontmatter",
    "obsidian.vault_no_gitignore": "gitignore",
    "obsidian.broken_link": "link_report",
    "obsidian.rename_without_link_repair": "link_report",
}
ACTION_LABEL = {
    "frontmatter": "补 frontmatter（先备份）",
    "gitignore": "补 .gitignore",
    "link_report": "写断链候选报告（不改链接）",
    "manual": "需人工",
}
DEFAULT_GITIGNORE = """# Obsidian 运行缓存 / 临时文件
.obsidian/workspace*.json
.obsidian/cache/
.trash/
.DS_Store
Thumbs.db
*.tmp
__pycache__/

# 本库的大批原始附件（可按需调整；已跟踪文件不会因此被删）
20_附件/原始资料/
"""


def _ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _action_for(mode_id):
    return AUTO_ACTIONS.get(str(mode_id or ""))


def _fresh_findings(vault_dir=None, max_files=4000):
    """按当前磁盘内容重扫，不依赖可能过期的 vault_index.json。"""
    root = Path(vault_dir or VAULT_DIR)
    files = checkers._index_from_dir(root, max_files=max_files)
    checks = checkers.load_vault_checks()
    return (checkers.check_vault(files, checks)
            + checkers.check_vault_content(root, max_files=max_files)
            + checkers.check_vault_text(root, max_files=max_files)
            + checkers.check_vault_plugins(root))


def build_plan(findings=None, limit=40, vault_dir=None):
    """读检查结果并转成界面可直接展示的安全执行计划。"""
    if findings is None:
        findings = _fresh_findings(vault_dir=vault_dir)
    raw = checkers.fix_plan(findings, limit=limit)
    plan = []
    for it in raw:
        rec = dict(it)
        action = _action_for(rec.get("id"))
        rec["action"] = action or "manual"
        rec["auto_apply"] = action in {"frontmatter", "gitignore", "link_report"}
        rec["action_note"] = ACTION_LABEL.get(rec["action"], ACTION_LABEL["manual"])
        plan.append(rec)
    auto_classes = [x for x in plan if x.get("auto_apply")]
    fm_files = {p for x in auto_classes if x.get("action") == "frontmatter"
                for p in (x.get("targets") or [])}
    link_classes = [x for x in auto_classes if x.get("action") == "link_report"]
    summary = {
        "classes": len(plan),
        "auto_classes": len(auto_classes),
        "manual_classes": len(plan) - len(auto_classes),
        "frontmatter_files": len(fm_files),
        "gitignore": sum(1 for x in auto_classes if x.get("action") == "gitignore"),
        "link_report_classes": len(link_classes),
    }
    return {"ts": _ts(), "findings": len(findings), "plan": plan, "summary": summary}


def render_plan(data):
    """把 build_plan 的结果渲染成大白话文本。"""
    if not isinstance(data, dict):
        return "读不到修复计划：%s" % data
    if data.get("error"):
        return "读不到修复计划：%s" % data.get("error")
    plan = data.get("plan") or []
    if not plan:
        return "没有需要修的项目。"
    s = data.get("summary") or {}
    lines = [
        "修复计划（%d 类问题；本页只读，点“执行安全修复”才会动手）" % s.get("classes", len(plan)),
        "",
        "会自动做的：%d 类；需要人工的：%d 类。" % (s.get("auto_classes", 0), s.get("manual_classes", 0)),
        "安全动作只包括：补 frontmatter、补 .gitignore、写断链候选报告。",
        "不会改链接、不会删正文、不会自动提交 Git。",
        "",
    ]
    auto = [x for x in plan if x.get("auto_apply")]
    manual = [x for x in plan if not x.get("auto_apply")]
    if auto:
        lines.append("【会自动执行】")
        for i, it in enumerate(auto, 1):
            lines.append("%d. %s（%s 条）" % (i, it.get("hint") or it.get("id"), it.get("count")))
            lines.append("   动作：%s" % it.get("action_note"))
            if it.get("how"):
                lines.append("   做法：%s" % it.get("how"))
            for smp in (it.get("samples") or [])[:2]:
                lines.append("   例：%s" % str(smp)[:120])
        lines.append("")
    if manual:
        lines.append("【需要人工】")
        for i, it in enumerate(manual, 1):
            lines.append("%d. %s（%s 条）" % (i, it.get("hint") or it.get("id"), it.get("count")))
            if it.get("how"):
                lines.append("   做法：%s" % it.get("how"))
            for smp in (it.get("samples") or [])[:1]:
                lines.append("   例：%s" % str(smp)[:120])
    return "\n".join(lines)


def _safe_rel(vault: Path, rel) -> Path:
    s = str(rel or "").replace("\\", "/").strip()
    if not s:
        raise ValueError("空路径")
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s):
        raise ValueError("不允许绝对路径：%s" % s)
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("不允许越界路径：%s" % s)
    target = vault.joinpath(*parts).resolve()
    base = vault.resolve()
    if target != base and base not in target.parents:
        raise ValueError("路径越出知识库：%s" % s)
    return target


def _backup(vault: Path, rels, backup_root: Path):
    stamp = _stamp()
    out = Path(backup_root) / stamp
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for rel in sorted(set(rels)):
        try:
            src = _safe_rel(vault, rel)
            if src.is_file():
                dst = out / str(rel).replace("\\", "/")
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                saved.append({"src": str(src), "bak": str(dst)})
        except Exception:
            continue
    return out, saved


def _kind_of(rel: str):
    s = str(rel or "").lower()
    for key, value in (("复盘", "复盘"), ("报告", "报告"), ("标准", "标准"),
                       ("规范", "规范"), ("铁律", "铁律"), ("清单", "清单"),
                       ("指南", "指南"), ("体系", "体系"), ("moc", "MOC"),
                       ("wiki", "知识条目"), ("选题", "选题"), ("样本", "样本"),
                       ("工具", "工具"), ("模板", "模板")):
        if key in s:
            return value
    if s.startswith("90_归档"):
        return "归档"
    if s.startswith("00_inbox"):
        return "收件箱"
    return "笔记"


def _frontmatter_bytes(rel: str, newline: bytes):
    title = Path(str(rel).replace("\\", "/")).stem
    title_json = json.dumps(title, ensure_ascii=False)
    rows = [
        "---",
        "type: \"%s\"" % _kind_of(rel),
        "title: %s" % title_json,
        "updated: \"%s\"" % datetime.now().strftime("%Y-%m-%d"),
        "---",
        "",
    ]
    return newline.join(x.encode("utf-8") for x in rows)


def _add_frontmatter(vault: Path, rels, changed, skipped, errors):
    for rel in sorted(set(rels)):
        try:
            src = _safe_rel(vault, rel)
            if not src.is_file():
                skipped.append("%s（不存在）" % rel)
                continue
            raw = src.read_bytes()
            text = raw.decode("utf-8-sig", errors="replace")
            if text.lstrip().startswith("---"):
                skipped.append("%s（已有 frontmatter）" % rel)
                continue
            newline = b"\r\n" if b"\r\n" in raw else b"\n"
            body = raw
            if body.startswith(b"\xef\xbb\xbf"):
                body = body[3:]
            src.write_bytes(_frontmatter_bytes(rel, newline) + body)
            changed["frontmatter"] += 1
        except Exception as e:
            errors.append("%s：%s" % (rel, e))


def _add_gitignore(vault: Path, changed, skipped, errors):
    try:
        dst = _safe_rel(vault, ".gitignore")
        if dst.exists():
            skipped.append(".gitignore（已存在，不覆盖）")
            return
        dst.write_bytes(DEFAULT_GITIGNORE.encode("utf-8"))
        changed["gitignore"] += 1
    except Exception as e:
        errors.append(".gitignore：%s" % e)


def _link_rows(plan):
    rows = []
    for it in plan:
        if it.get("action") != "link_report":
            continue
        for target in it.get("targets") or []:
            rows.append((it.get("id"), str(target)))
    return rows


def _write_link_report(vault: Path, plan, changed, errors):
    rows = _link_rows(plan)
    if not rows:
        return None
    try:
        d = _safe_rel(vault, "00_Inbox")
        d.mkdir(parents=True, exist_ok=True)
        p = d / ("_断链与改名候选-%s.md" % datetime.now().strftime("%Y%m%d-%H%M%S"))
        lines = [
            "---",
            "type: 报告",
            "title: \"断链与改名候选\"",
            "updated: \"%s\"" % datetime.now().strftime("%Y-%m-%d"),
            "tags:",
            "  - 知识库/维护",
            "---",
            "",
            "# 断链与改名候选",
            "",
            "> 由管家安全修复执行器生成；**没有自动改链接**。",
            "> 选好候选后请人工确认，或另走一条明确授权的批量改链流程。",
            "",
            "| 类型 | 出问题的笔记 / 链接 |",
            "|---|---|",
        ]
        for kind, target in rows:
            lines.append("| %s | `%s` |" % (kind, target.replace("|", "\\|")))
        p.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        changed["link_report"] += 1
        return str(p.relative_to(vault)).replace("\\", "/")
    except Exception as e:
        errors.append("断链报告：%s" % e)
        return None


def apply(plan_data=None, vault_dir=None, backup_root=None, log_path=None):
    """执行一个 build_plan 结果；默认重新扫描，保证按当下事实动手。"""
    vault = Path(vault_dir or VAULT_DIR).resolve()
    if not vault.is_dir():
        return {"ok": False, "error": "知识库目录不存在：%s" % vault}
    if plan_data is None:
        plan_data = build_plan(vault_dir=vault)
    if isinstance(plan_data, list):
        plan_data = {"plan": plan_data}
    plan = (plan_data or {}).get("plan") or []
    auto = [x for x in plan if x.get("auto_apply")]
    if not auto:
        return {"ok": True, "changed": {"frontmatter": 0, "gitignore": 0, "link_report": 0},
                "skipped": ["没有可自动执行的项目"], "errors": [], "backup_dir": None,
                "backed_up": [], "plan": plan}

    fm_rels = {p for x in auto if x.get("action") == "frontmatter" for p in (x.get("targets") or [])}
    backup_dir = None
    backed_up = []
    if fm_rels:
        backup_dir, backed_up = _backup(vault, fm_rels, Path(backup_root or (DATA_DIR / "vault_ops_backup")))
    changed = {"frontmatter": 0, "gitignore": 0, "link_report": 0}
    skipped, errors = [], []
    if fm_rels:
        _add_frontmatter(vault, fm_rels, changed, skipped, errors)
    if any(x.get("action") == "gitignore" for x in auto):
        _add_gitignore(vault, changed, skipped, errors)
    report = _write_link_report(vault, auto, changed, errors)
    result = {"ok": not errors, "changed": changed, "skipped": skipped, "errors": errors,
              "backup_dir": str(backup_dir) if backup_dir else None,
              "backed_up": backed_up, "link_report": report, "plan": plan}
    try:
        lp = Path(log_path or (DATA_DIR / "fix_runner_log.jsonl"))
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _ts(), "result": result}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return result
