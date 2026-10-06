# -*- coding: utf-8 -*-
"""gui_util 的单元测试 —— 纯函数，无 Qt、无副作用。

跑法：  python -m unittest discover -s tests -v
"""
from __future__ import annotations
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import gui_util as U  # noqa: E402


class ElideTest(unittest.TestCase):
    def test_none_and_whitespace(self):
        self.assertEqual(U.elide(None, 10), "")
        self.assertEqual(U.elide("  a   b ", 10), "a b")

    def test_truncation_uses_ellipsis(self):
        self.assertEqual(U.elide("hello world", 5), "hell…")

    def test_short_passthrough(self):
        self.assertEqual(U.elide("abc", 10), "abc")


class FmtBytesTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(U.fmt_bytes(0), "0 B")
        self.assertEqual(U.fmt_bytes(2048), "2 KB")
        self.assertEqual(U.fmt_bytes(1024 ** 2), "1.0 MB")
        self.assertEqual(U.fmt_bytes(1024 ** 3), "1.0 GB")

    def test_invalid(self):
        self.assertEqual(U.fmt_bytes("nope"), "—")


class HhmmssTest(unittest.TestCase):
    def test_iso_with_tz_returns_clock(self):
        v = U.hhmmss("2026-10-07T00:00:00+00:00")
        self.assertTrue(re.match(r"^\d{2}:\d{2}:\d{2}$", v), v)

    def test_garbage_falls_back(self):
        s = "xxxx-xx-xxT01:02:03"
        self.assertEqual(U.hhmmss(s), "01:02:03")


class SafeWorseTest(unittest.TestCase):
    def test_safe(self):
        self.assertEqual(U.safe(lambda: 5, "X"), 5)
        self.assertEqual(U.safe(lambda: None, "X"), "X")
        self.assertEqual(U.safe(lambda: 1 / 0, "X"), "X")

    def test_worse(self):
        self.assertEqual(U.worse("NORMAL", "BLOCKED"), "BLOCKED")
        self.assertEqual(U.worse("BLOCKED", "NORMAL"), "BLOCKED")
        self.assertEqual(U.worse(None, "WATCH"), "WATCH")


class JsonIoTest(unittest.TestCase):
    def test_read_json_missing_returns_default(self):
        self.assertEqual(U.read_json(Path("no_such_file_xyz.json"), {"d": 1}), {"d": 1})

    def test_read_json_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            p.write_text(json.dumps({"k": 2}), encoding="utf-8")
            self.assertEqual(U.read_json(p, {}), {"k": 2})

    def test_tail_jsonl_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            p.write_text('{"a":1}\nnot-json\n{"a":2}\n', encoding="utf-8")
            self.assertEqual(U.tail_jsonl(p, 10), [{"a": 1}, {"a": 2}])
            self.assertEqual(U.tail_jsonl(p, 1), [{"a": 2}])

    def test_tail_jsonl_missing(self):
        self.assertEqual(U.tail_jsonl(Path("no_such_xyz.jsonl"), 5), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)