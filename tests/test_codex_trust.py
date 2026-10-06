# -*- coding: utf-8 -*-
"""codex_trust 的单元测试 —— 锁住"官方哈希引擎"的契约。

为什么重要：整个「信任闸门」的安全性都建立在这个哈希算得对之上。
算法来源（T1，openai/codex 官方源码，2026-10-06 在线核对）：
  codex-rs/hooks/src/engine/discovery.rs  ·  codex-rs/config/src/fingerprint.rs

跑法：  python -m unittest discover -s tests -v
"""
from __future__ import annotations
import json
import os
import re
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import codex_trust as ct  # noqa: E402

HEX64 = re.compile(r"^sha256:[0-9a-f]{64}$")


class EventLabelTest(unittest.TestCase):
    def test_camel_to_snake(self):
        self.assertEqual(ct.event_label("SessionEnd"), "session_end")
        self.assertEqual(ct.event_label("PreToolUse"), "pre_tool_use")
        self.assertEqual(ct.event_label("UserPromptSubmit"), "user_prompt_submit")
        self.assertEqual(ct.event_label("Stop"), "stop")


class NormalizeTimeoutTest(unittest.TestCase):
    def test_session_end_and_interrupt_clamp(self):
        for label in ("session_end", "interrupt"):
            self.assertEqual(ct._normalize_timeout(label, None), 1)   # 默认 1s
            self.assertEqual(ct._normalize_timeout(label, 600), 3)    # 上限 3s
            self.assertEqual(ct._normalize_timeout(label, 0), 1)      # 下限 1s
            self.assertEqual(ct._normalize_timeout(label, 2), 2)

    def test_other_events_default_600_min_1(self):
        self.assertEqual(ct._normalize_timeout("stop", None), 600)
        self.assertEqual(ct._normalize_timeout("stop", 0), 1)
        self.assertEqual(ct._normalize_timeout("stop", 45), 45)


class NormalizeHandlerTest(unittest.TestCase):
    def test_minimal_handler(self):
        d = ct.normalize_handler("stop", {"type": "command", "command": "echo hi"})
        self.assertEqual(d["type"], "command")
        self.assertEqual(d["command"], "echo hi")
        self.assertEqual(d["timeout"], 600)          # 缺省补齐
        self.assertFalse(d["async"])
        self.assertNotIn("statusMessage", d)          # 没给就不塞
        self.assertNotIn("commandWindows", d)

    def test_optional_fields_kept_when_present(self):
        d = ct.normalize_handler("stop", {
            "type": "command", "command": "x", "commandWindows": "y",
            "timeout": 5, "async": True, "statusMessage": "s",
            "additionalContextLimit": 100,
        })
        self.assertEqual(d["commandWindows"], "y")
        self.assertEqual(d["timeout"], 5)
        self.assertTrue(d["async"])
        self.assertEqual(d["statusMessage"], "s")
        self.assertEqual(d["additionalContextLimit"], 100)


class HookHashTest(unittest.TestCase):
    GROUP = {"matcher": "Bash",
             "hooks": [{"type": "command", "command": "echo hi", "timeout": 30}]}

    def test_format(self):
        h = ct.hook_hash("PreToolUse", self.GROUP)
        self.assertTrue(HEX64.match(h), h)

    def test_deterministic(self):
        self.assertEqual(ct.hook_hash("PreToolUse", self.GROUP),
                         ct.hook_hash("PreToolUse", self.GROUP))

    def test_sensitive_to_command(self):
        g = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo bye", "timeout": 30}]}
        self.assertNotEqual(ct.hook_hash("PreToolUse", self.GROUP), ct.hook_hash("PreToolUse", g))

    def test_sensitive_to_matcher(self):
        g = {"matcher": "Read", "hooks": self.GROUP["hooks"]}
        self.assertNotEqual(ct.hook_hash("PreToolUse", self.GROUP), ct.hook_hash("PreToolUse", g))

    def test_sensitive_to_timeout_and_event(self):
        g = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi", "timeout": 31}]}
        self.assertNotEqual(ct.hook_hash("PreToolUse", self.GROUP), ct.hook_hash("PreToolUse", g))
        self.assertNotEqual(ct.hook_hash("PreToolUse", self.GROUP), ct.hook_hash("Stop", self.GROUP))

    def test_timeout_normalized_into_hash(self):
        """PreToolUse 给 0 应被规范成 1，与显式 1 同哈希。"""
        a = {"matcher": "Bash", "hooks": [{"type": "command", "command": "c", "timeout": 0}]}
        b = {"matcher": "Bash", "hooks": [{"type": "command", "command": "c", "timeout": 1}]}
        self.assertEqual(ct.hook_hash("PreToolUse", a), ct.hook_hash("PreToolUse", b))


class LiveFileSanityTest(unittest.TestCase):
    """只读地核对本机真实 hooks.json（文件不存在就跳过）。"""

    def setUp(self):
        if not ct.HOOKS_JSON.exists():
            self.skipTest("本机没有 hooks.json")

    def test_all_handlers_hash_well_formed(self):
        data = ct.load_hooks()
        hooks = data.get("hooks", {})
        n = 0
        for ev, groups in hooks.items():
            for g in groups:
                for hi in range(len(g.get("hooks", []) or [])):
                    h = ct.hook_hash(ev, g, hi)
                    self.assertTrue(HEX64.match(h), "%s -> %s" % (ev, h))
                    n += 1
        self.assertGreater(n, 0, "本机 hooks.json 里一个 handler 都没有？")

    def test_status_consistent_with_stored_hash(self):
        for item in ct.list_handlers():
            cur, stored, st = item["current_hash"], item["trusted_hash"], item["status"]
            self.assertIn(st, ("new", "trusted", "modified"))
            if stored is None:
                self.assertEqual(st, "new")
            elif stored == cur:
                self.assertEqual(st, "trusted")
            else:
                self.assertEqual(st, "modified")


if __name__ == "__main__":
    unittest.main(verbosity=2)