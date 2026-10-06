# -*- coding: utf-8 -*-
"""Codex 插件 check_text 的两种模式：纯文本不下动作类结论；结构化才可信。"""
import importlib.util
import unittest
from pathlib import Path

import plugins_status as PS

SRV = PS.CODEX_PLUGIN_SRC / "mcp_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("_sv_mcp", SRV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CheckTextModeTest(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_schema_has_actions_and_say(self):
        tools = {t["name"]: t for t in self.m.TOOLS}
        props = tools["check_text"]["inputSchema"]["properties"]
        self.assertIn("actions", props)
        self.assertIn("say", props)

    def test_text_only_is_honest(self):
        """纯文本模式：像收尾声明那样的文字，不该被判"没有动作"。"""
        out = self.m.tool_check_text({"text": "本轮已完成，全部已验证，肯定没问题。"})
        self.assertTrue(out["ok"])
        self.assertTrue(out["text_only"])
        ids = {f["id"] for f in out["findings"]}
        self.assertNotIn("codex.claim_without_action", ids)
        self.assertNotIn("codex.fake_verification", ids)
        self.assertNotIn("codex.no_evidence_claim", ids)
        self.assertNotIn("codex.no_test_after_change", ids)
        self.assertGreaterEqual(out.get("suppressed_action_checks", 0), 1)
        self.assertIn("note", out)
        # 纯文本可证的条目仍然要报
        self.assertIn("codex.unsupported_certainty", ids)

    def test_structured_mode_can_flag(self):
        out = self.m.tool_check_text({"text": "x", "actions": "", "say": "本轮已完成"})
        self.assertFalse(out["text_only"])
        self.assertIn("codex.claim_without_action", {f["id"] for f in out["findings"]})

    def test_structured_clean_turn(self):
        out = self.m.tool_check_text({
            "text": "x", "actions": "python -m unittest discover -s tests",
            "say": "跑完了，127 条测试通过"})
        ids = {f["id"] for f in out["findings"]}
        self.assertNotIn("codex.claim_without_action", ids)
        self.assertNotIn("codex.fake_verification", ids)

    def test_empty_input_errors(self):
        out = self.m.tool_check_text({"text": "   "})
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()