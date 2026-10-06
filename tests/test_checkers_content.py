# -*- coding: utf-8 -*-
"""A11：内容级检查器（canvas / bases / 属性类型 / 字段命名）+ 两条回合级检查。"""
import json
import tempfile
import unittest
from pathlib import Path

import checkers as CK


def _write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ids(findings):
    return {f["id"] for f in findings}


class TurnLevelTest(unittest.TestCase):
    def test_unbounded_retry(self):
        acts = "python -m unittest tests.test_x\npython -m unittest tests.test_x\n还是不行，再试一次"
        self.assertIn("codex.unbounded_retry", _ids(CK.check_turn("", acts, "")))

    def test_no_library_lookup(self):
        f = CK.check_turn("", "我直接开始写", "按我们之前的规范，这块应该没问题")
        self.assertIn("codex.no_library_lookup", _ids(f))

    def test_lookup_present_is_clean(self):
        f = CK.check_turn("", "rg 规范 rubrics -n", "按我们的规范改了")
        self.assertNotIn("codex.no_library_lookup", _ids(f))


class FrontmatterParserTest(unittest.TestCase):
    def test_parses_block_list_as_list(self):
        fm = CK._parse_frontmatter("---\naliases:\n  - 甲\n  - 乙\ntitle: 测试\n---\n正文")
        self.assertEqual(fm["aliases"], "[]")     # 块列表
        self.assertEqual(CK._infer_type(fm["aliases"]), "列表")
        self.assertEqual(fm["title"], "测试")

    def test_parses_block_scalar_as_text(self):
        fm = CK._parse_frontmatter("---\ndescription: |\n  多行\n  说明\ntitle: x\n---\n")
        self.assertEqual(CK._infer_type(fm["description"]), "文本")

    def test_no_frontmatter_returns_none(self):
        self.assertIsNone(CK._parse_frontmatter("正文没有 fm"))

    def test_type_inference(self):
        self.assertEqual(CK._infer_type("2026-10-07"), "日期")
        self.assertEqual(CK._infer_type("42"), "数字")
        self.assertEqual(CK._infer_type("true"), "布尔")
        self.assertEqual(CK._infer_type("[a, b]"), "列表")
        self.assertEqual(CK._infer_type("随便写"), "文本")
        self.assertEqual(CK._infer_type(""), "空")


class ContentChecksTest(unittest.TestCase):
    def test_property_type_drift(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a.md", "---\nupdated: 2026-10-07\n---\n")
            _write(d, "b.md", '---\nupdated: "2026-10-07"\n---\n')
            got = _ids(CK.check_vault_content(d))
        self.assertIn("obsidian.property_type_drift", got)

    def test_empty_value_is_not_drift(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a.md", "---\nupdated: 2026-10-07\n---\n")
            _write(d, "b.md", "---\nupdated:\n---\n")
            got = _ids(CK.check_vault_content(d))
        self.assertNotIn("obsidian.property_type_drift", got)

    def test_frontmatter_schema_drift(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a.md", "---\ncreated: 2026-10-01\n---\n")
            _write(d, "b.md", "---\ndate: 2026-10-02\n---\n")
            got = _ids(CK.check_vault_content(d))
        self.assertIn("obsidian.frontmatter_schema_drift", got)

    def test_canvas_drift(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "有.md", "---\ntitle: x\n---\n")
            _write(d, "画布.canvas", json.dumps({
                "nodes": [{"file": "有"}, {"file": "没有这篇"}]}))
            findings = CK.check_vault_content(d)
        canvas = [f for f in findings if f["id"] == "obsidian.canvas_drift"]
        self.assertEqual(len(canvas), 1)
        self.assertEqual(canvas[0]["count"], 1)
        self.assertIn("没有这篇", canvas[0]["items"][0])

    def test_bases_property_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a.md", "---\ntitle: x\n---\n")
            _write(d, "视图.base", json.dumps(
                {"views": [{"order": ["title", "不存在的属性"]}]}))
            got = _ids(CK.check_vault_content(d))
        self.assertIn("obsidian.bases_property_mismatch", got)

    def test_clean_vault_has_no_findings(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a.md", "---\ntitle: x\ncreated: 2026-10-01\n---\n")
            _write(d, "b.md", "---\ntitle: y\ncreated: 2026-10-02\n---\n")
            self.assertEqual(CK.check_vault_content(d), [])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(CK.check_vault_content("C:/definitely/not/here"), [])


class BasePropertiesTest(unittest.TestCase):
    def test_json_properties(self):
        props = CK._base_properties(json.dumps({"views": [{"columns": ["a", "b"]}]}))
        self.assertIn("a", props)
        self.assertIn("b", props)

    def test_yaml_like_list(self):
        props = CK._base_properties("filters:\n- status\n- owner\n")
        self.assertTrue({"status", "owner"} & props)


if __name__ == "__main__":
    unittest.main()