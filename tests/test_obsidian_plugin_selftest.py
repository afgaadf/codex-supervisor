# -*- coding: utf-8 -*-
"""跑 Obsidian 插件的 Node 自测（没装 node 就跳过，不误报失败）。"""
import shutil
import subprocess
import unittest
from pathlib import Path

import plugins_status as PS

SELFTEST = PS.OBSIDIAN_PLUGIN_SRC / "selftest.js"


class ObsidianPluginSelfTest(unittest.TestCase):
    def test_selftest_script_exists(self):
        self.assertTrue(SELFTEST.exists(), "缺少 plugins/obsidian/selftest.js")

    def test_node_selftest_passes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("本机没有 node，跳过 JS 自测")
        proc = subprocess.run([node, str(SELFTEST)], capture_output=True, text=True, timeout=120)
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, "插件自测失败：\n" + out[-2000:])
        self.assertIn("全部通过", out)

    def test_main_js_scopes_checks(self):
        """回归守卫：真库有数万文件的归档目录，不能被算进体检。"""
        js = (PS.OBSIDIAN_PLUGIN_SRC / "main.js").read_text(encoding="utf-8")
        self.assertIn("attachmentIgnoreFolders", js)
        self.assertIn("原始资料", js)
        self.assertIn("ORPHAN_ATTACHMENT_MAX_LIST", js)


if __name__ == "__main__":
    unittest.main()