# -*- coding: utf-8 -*-
"""失败模式库（28→60）扩充后的契约测试：数据完整性 + 来源合规 + 选择/渲染。"""
from __future__ import annotations
import os
import re
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import failure_modes as FM  # noqa: E402

OBSIDIAN_HELP_URL = re.compile(r"https://help\.obsidian\.md/\S+")


class FailureModesLibraryTest(unittest.TestCase):
    def test_describe_reports_full_registry_without_errors(self):
        info = FM.describe()
        self.assertEqual(info["errors"], [])
        self.assertEqual(info["total"], 84)
        self.assertEqual(info["codex"], 42)
        self.assertEqual(info["obsidian"], 42)

    def test_ids_unique_and_every_source_nonempty(self):
        rows = FM.load_modes()
        self.assertEqual(len(rows), 84)
        ids = [str(r.get("id") or "") for r in rows]
        self.assertEqual(len(ids), len(set(ids)), "失败模式 id 必须唯一")
        for r in rows:
            mid = str(r.get("id") or "").strip()
            self.assertTrue(mid, "存在空 id 的条目")
            self.assertTrue(str(r.get("source") or "").strip(), "%s 缺来源" % mid)

    def test_obsidian_sources_use_official_help_url(self):
        for r in FM.load_modes():
            if r.get("area") != "obsidian":
                continue
            src = str(r.get("source") or "")
            if "obsidian.md" in src:
                self.assertRegex(src, OBSIDIAN_HELP_URL, r["id"])

    def test_codex_sources_marked_non_authoritative(self):
        for r in FM.load_modes():
            if r.get("area") != "codex":
                continue
            self.assertIn("非权威", str(r.get("source") or ""), r["id"])

    def test_select_codex_hits_destructive_pattern(self):
        ids = {r["id"] for r in FM.select("删除文件 taskkill", area="codex")}
        self.assertIn("codex.destructive_without_backup", ids)

    def test_select_obsidian_hits_broken_link_and_frontmatter(self):
        ids = {r["id"] for r in FM.select("断链 frontmatter", area="obsidian")}
        self.assertIn("obsidian.broken_link", ids)
        self.assertIn("obsidian.missing_frontmatter", ids)

    def test_render_for_prompt_is_nonempty_and_labeled(self):
        text = FM.render_for_prompt("测试通过")
        self.assertTrue(text.strip(), "render_for_prompt 不应返回空串")
        self.assertIn("失败模式", text)

    def test_validate_reports_bad_rows(self):
        good = {"id": "x.one", "area": "codex", "title": "t", "severity": "high",
                "triggers": ["a"], "detect": "d", "fix": "f", "source": "s", "auto": "yes"}
        self.assertEqual(FM.validate_modes([good]), [])
        self.assertEqual(FM.validate_modes([]), [])

        missing = dict(good, id="x.missing")
        missing.pop("source")
        cases = {
            "缺字段": [missing],
            "area 非法": [dict(good, area="pc")],
            "severity 非法": [dict(good, severity="fatal")],
            "id 重复": [good, dict(good)],
            "triggers 非 list": [dict(good, triggers="a")],
        }
        for label, rows in cases.items():
            with self.subTest(label=label):
                self.assertTrue(FM.validate_modes(rows), "%s 未被检出" % label)


if __name__ == "__main__":
    unittest.main(verbosity=2)
