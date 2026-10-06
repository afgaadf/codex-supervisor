# -*- coding: utf-8 -*-
"""0.9.1：工具回答自报版本/进程号 —— 让"跑的是旧版"这件事可见。

背景：MCP 服务器只在 Codex 启动时加载一次，改了 mcp_server.py 不会热重载。
本轮就是被这件事咬到的（磁盘是修好的版本，但 Codex 里那个进程还是旧的，
于是 check_text 又误报了一次）。
"""
import importlib.util
import json
import unittest
from pathlib import Path

import plugins_status as PS

SRV = PS.CODEX_PLUGIN_SRC / "mcp_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("_sv_mcp_ver", SRV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SelfVersionTest(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_tools_list_reports_version_and_pid(self):
        r = self.m.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        res = r["result"]
        self.assertEqual(res.get("plugin_version"), self.m.PLUGIN_VERSION)
        self.assertIsInstance(res.get("mcp_pid"), int)

    def test_tool_call_result_reports_version(self):
        r = self.m.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "check_text",
                                        "arguments": {"text": "x", "actions": "python -m unittest",
                                                      "say": "完成"}}})
        body = json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(body.get("plugin_version"), self.m.PLUGIN_VERSION)
        self.assertIsInstance(body.get("mcp_pid"), int)

    def test_initialize_reports_version(self):
        r = self.m.dispatch({"jsonrpc": "2.0", "id": 3, "method": "initialize",
                             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "t", "version": "0"}}})
        self.assertEqual(r["result"]["serverInfo"]["version"], self.m.PLUGIN_VERSION)

    def test_import_os_available(self):
        """回归：曾漏 import os，导致 tools/list 直接 NameError。"""
        self.assertTrue(hasattr(self.m, "os"))


if __name__ == "__main__":
    unittest.main()