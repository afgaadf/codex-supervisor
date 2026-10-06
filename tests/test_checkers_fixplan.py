# -*- coding: utf-8 -*-
"""0.9.0：修复建议 / 修复计划 + 4 个新检查器 + "auto=yes 必须有检查器"守卫。"""
import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path

import checkers as CK
import failure_modes as FM

NL = chr(10)


def _w(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _fm(**kw):
    return NL.join(["---"] + ["%s: %s" % (k, v) for k, v in kw.items()] + ["---"]) + NL


def _ids(f):
    return {x["id"] for x in f}


class FixHintTest(unittest.TestCase):
    def test_finding_carries_fix(self):
        f = CK._finding("obsidian.vault_no_gitignore", "obsidian", "medium", "t", ["x"])
        self.assertIn("fix", f)
        self.assertTrue(f["fix_auto"])

    def test_finding_without_hint_has_no_fix(self):
        f = CK._finding("codex.unknown_thing", "codex", "low", "t", ["x"])
        self.assertNotIn("fix", f)

    def test_fix_plan_sorted_by_severity_then_auto(self):
        findings = [
            CK._finding("obsidian.note_no_h1", "obsidian", "low", "t", ["a"]),
            CK._finding("obsidian.broken_link", "obsidian", "high", "t", ["b"]),
            CK._finding("obsidian.missing_frontmatter", "obsidian", "high", "t", ["c"]),
        ]
        plan = CK.fix_plan(findings)
        self.assertEqual(plan[0]["id"], "obsidian.broken_link")      # high + 可脚本 排最前
        self.assertEqual(plan[-1]["id"], "obsidian.note_no_h1")      # low 垫底
        self.assertTrue(all("hint" in x and "how" in x for x in plan))

    def test_render_fix_plan_text(self):
        txt = CK.render_fix_plan(CK.fix_plan(
            [CK._finding("obsidian.backup_dir_in_vault", "obsidian", "medium", "t", ["备份"])]))
        self.assertIn("修复计划", txt)
        self.assertIn("需人工", txt)

    def test_render_empty(self):
        self.assertIn("没有需要修", CK.render_fix_plan([]))


class NewCheckerTest(unittest.TestCase):
    def test_tool_error_pileup(self):
        acts = "error: a" + NL + "failed b" + NL + "Traceback c"
        self.assertIn("codex.tool_error_pileup", _ids(CK.check_turn("", acts, "")))

    def test_single_error_not_flagged(self):
        self.assertNotIn("codex.tool_error_pileup", _ids(CK.check_turn("", "error: a", "")))

    def test_note_bloat(self):
        files = [{"path": "a.md", "name": "a.md", "has_frontmatter": True,
                  "links": ["b"], "tags": ["x"], "size": 300 * 1024},
                 {"path": "b.md", "name": "b.md", "has_frontmatter": True,
                  "links": ["a"], "tags": ["x"], "size": 1000}]
        self.assertIn("obsidian.note_bloat", _ids(CK.check_vault(files)))

    def test_link_direction(self):
        files = [{"path": "hub.md", "name": "hub.md", "has_frontmatter": True,
                  "links": [], "tags": ["x"], "size": 100}]
        for i in range(3):
            files.append({"path": "n%d.md" % i, "name": "n%d.md" % i,
                          "has_frontmatter": True, "links": ["hub"], "tags": ["x"], "size": 100})
        self.assertIn("obsidian.link_direction", _ids(CK.check_vault(files)))

    def test_template_unused(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, ".gitignore", "x" + NL)
            _w(d, "30_模板/标准.md", _fm(type="模板", title="t", updated="2026-10-07", 专属字段="1") + "# t" + NL)
            for i in range(3):
                _w(d, "%d.md" % i, _fm(type="笔记", title="x", updated="2026-10-07") + "# t" + NL)
            self.assertIn("obsidian.template_unused", _ids(CK.check_vault_text(d)))

    def test_template_used_ok(self):
        with tempfile.TemporaryDirectory() as d:
            _w(d, ".gitignore", "x" + NL)
            _w(d, "30_模板/标准.md", _fm(type="模板", 专属字段="1") + "# t" + NL)
            _w(d, "a.md", _fm(type="笔记", 专属字段="1") + "# t" + NL)
            self.assertNotIn("obsidian.template_unused", _ids(CK.check_vault_text(d)))


class CoverageGuardTest(unittest.TestCase):
    """守护：标了 auto=yes 的条目，checkers.py 里必须真有对应检测。"""

    def test_every_auto_yes_has_checker(self):
        src = inspect.getsource(CK)
        have = set(re.findall(r'"((?:codex|obsidian)\.[a-z_0-9]+)"', src))
        missing = [m["id"] for m in FM.load_modes()
                   if m.get("auto") == "yes" and m["id"] not in have]
        self.assertEqual(missing, [], "这些 auto=yes 条目没有检查器：%s" % missing)

    def test_fix_hints_ids_exist(self):
        known = {m["id"] for m in FM.load_modes()}
        unknown = [k for k in CK.FIX_HINTS if k not in known]
        self.assertEqual(unknown, [], "FIX_HINTS 里有库中不存在的 id：%s" % unknown)


if __name__ == "__main__":
    unittest.main()