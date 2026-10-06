# -*- coding: utf-8 -*-
"""Failure-mode registry contract tests."""
from __future__ import annotations
import os
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import failure_modes as FM  # noqa: E402


class FailureModesTest(unittest.TestCase):
    def test_registry_is_valid_and_covers_both_domains(self):
        info = FM.describe()
        self.assertEqual(info["errors"], [])
        self.assertGreaterEqual(info["codex"], 14)
        self.assertGreaterEqual(info["obsidian"], 14)

    def test_select_matches_codex_and_obsidian_problems(self):
        rows = FM.select("Codex 说完成了但没测试；Obsidian 双链断了，缺 frontmatter", limit=12)
        ids = {r["id"] for r in rows}
        self.assertIn("codex.claim_without_action", ids)
        self.assertIn("codex.no_test_after_change", ids)
        self.assertIn("obsidian.broken_link", ids)
        self.assertIn("obsidian.missing_frontmatter", ids)

    def test_prompt_render_has_actionable_fields(self):
        text = FM.render_for_prompt("删除文件前没备份", limit=3)
        self.assertIn("codex.destructive_without_backup", text)
        self.assertIn("查：", text)
        self.assertIn("修：", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)