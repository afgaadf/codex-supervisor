# -*- coding: utf-8 -*-
"""0.8.0：不可逆锁定检测 + 插件启用清单/安装目录一致性。"""
import json
import tempfile
import unittest
from pathlib import Path

import checkers as CK

NL = chr(10)


def _ids(f):
    return {x["id"] for x in f}


class LockoutTest(unittest.TestCase):
    def test_flags_lockout_without_rescue(self):
        self.assertIn("codex.irreversible_lockout",
                      _ids(CK.check_turn("", "把这块禁用掉", "")))

    def test_ok_when_rescue_mentioned(self):
        self.assertNotIn("codex.irreversible_lockout",
                         _ids(CK.check_turn("", "禁用该功能，并给出回滚步骤", "")))


class PluginConsistencyTest(unittest.TestCase):
    def _mk(self, enabled, folders):
        d = tempfile.mkdtemp()
        obs = Path(d) / ".obsidian"
        (obs / "plugins").mkdir(parents=True)
        (obs / "community-plugins.json").write_bytes(
            json.dumps(enabled).encode("utf-8"))
        for f in folders:
            (obs / "plugins" / f).mkdir()
            (obs / "plugins" / f / "manifest.json").write_bytes(b"{}")
        return d

    def test_ghost_plugin_flagged(self):
        d = self._mk(["a", "b"], ["a"])
        self.assertIn("obsidian.plugin_disabled_unnoticed", _ids(CK.check_vault_plugins(d)))

    def test_consistent_no_finding(self):
        d = self._mk(["a", "b"], ["a", "b"])
        self.assertEqual(CK.check_vault_plugins(d), [])

    def test_plugin_status_reports_both_lists(self):
        d = self._mk(["a"], ["a", "z"])
        st = CK.plugin_status(d)
        self.assertEqual(st["enabled"], ["a"])
        self.assertIn("z", st["disabled"])
        self.assertEqual(st["ghost"], [])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(CK.check_vault_plugins("C:/definitely/not/here"), [])


if __name__ == "__main__":
    unittest.main()
