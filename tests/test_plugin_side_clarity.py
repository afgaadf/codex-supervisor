# -*- coding: utf-8 -*-
"""两个插件必须写明"装在哪个软件里"，别让人猜（用户要求）。"""
import unittest

import plugins_status as PS


class PluginSideClarityTest(unittest.TestCase):
    def test_codex_side_is_codex(self):
        st = PS.codex_status()
        self.assertEqual(st["side"], "codex")
        self.assertEqual(st["host"], "Codex")
        self.assertIn("skills", st["installed_into"])
        self.assertIn("Codex", st["name"])

    def test_obsidian_side_is_obsidian(self):
        st = PS.obsidian_status()
        self.assertEqual(st["side"], "obsidian")
        self.assertEqual(st["host"], "Obsidian")
        self.assertIn(".obsidian/plugins", st["installed_into"])
        self.assertIn("Obsidian", st["name"])

    def test_each_has_what_it_does(self):
        for st in (PS.codex_status(), PS.obsidian_status()):
            self.assertTrue(st.get("what"))


if __name__ == "__main__":
    unittest.main()