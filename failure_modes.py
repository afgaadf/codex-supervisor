# -*- coding: utf-8 -*-
"""可机读失败模式库：把已知的 Codex / Obsidian 问题变成可筛选、可注入提示词的清单。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from paths import RUBRIC_DIR, RUBRIC_SRC

AREAS = ("codex", "obsidian")
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
REQUIRED = ("id", "area", "title", "severity", "triggers", "detect", "fix", "source", "auto")


def _path(path=None):
    if path:
        return Path(path)
    p = RUBRIC_DIR / "_failure_modes.json"
    return p if p.exists() else RUBRIC_SRC / "_failure_modes.json"


def load_modes(path=None):
    try:
        data = json.loads(_path(path).read_text(encoding="utf-8"))
        rows = data.get("modes") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def validate_modes(rows):
    errors, seen = [], set()
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict):
            errors.append("mode[%d] not object" % i)
            continue
        for key in REQUIRED:
            if key not in row:
                errors.append("%s missing %s" % (row.get("id") or i, key))
        mid = str(row.get("id") or "")
        if not mid or mid in seen:
            errors.append("duplicate/empty id: %s" % mid)
        seen.add(mid)
        if row.get("area") not in AREAS:
            errors.append("%s bad area" % mid)
        if row.get("severity") not in SEVERITY_ORDER:
            errors.append("%s bad severity" % mid)
        if not isinstance(row.get("triggers"), list):
            errors.append("%s triggers not list" % mid)
    return errors


def _score(row, text):
    low = str(text or "").lower()
    score = 0
    for t in row.get("triggers") or []:
        t = str(t).lower().strip()
        if t and t in low:
            score += 1
    # 高严重度在同分时优先；不把 severity 直接当命中分，避免无关模式挤进来。
    return score


def select(text, area=None, limit=10, include_default=True):
    rows = load_modes()
    if area:
        rows = [r for r in rows if r.get("area") == area]
    hit = [r for r in rows if _score(r, text) > 0]
    hit.sort(key=lambda r: (-_score(r, text), SEVERITY_ORDER.get(r.get("severity"), 9), r.get("id")))
    if not hit and include_default:
        hit = [r for r in rows if r.get("severity") in ("critical", "high")]
        hit.sort(key=lambda r: (SEVERITY_ORDER.get(r.get("severity"), 9), r.get("id")))
    return hit[: max(0, int(limit))]


def render_modes(rows):
    lines = []
    for r in rows or []:
        lines.append("- [%s|%s|auto=%s] %s；查：%s；修：%s" % (
            r.get("id"), r.get("severity"), r.get("auto"),
            r.get("title"), r.get("detect"), r.get("fix")))
    return "\n".join(lines)


def render_for_prompt(text, limit=10):
    rows = select(text, limit=limit)
    if not rows:
        return ""
    return "================ 已知失败模式（按本轮文本筛选）================\n" + render_modes(rows) + "\n================ 失败模式结束 ================\n"


def describe():
    rows = load_modes()
    return {"total": len(rows), "codex": sum(1 for r in rows if r.get("area") == "codex"),
            "obsidian": sum(1 for r in rows if r.get("area") == "obsidian"),
            "errors": validate_modes(rows)}