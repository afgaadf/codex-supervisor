# -*- coding: utf-8 -*-
"""Incremental tail reader contract tests."""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from jsonl_tail import tail_jsonl, tail_lines, tail_text  # noqa: E402


class JsonlTailTest(unittest.TestCase):
    def test_tail_lines_keeps_last_records_without_trailing_newline(self):
        p = Path(tempfile.mkdtemp()) / "a.jsonl"
        p.write_text("one\ntwo\nthree", encoding="utf-8")
        self.assertEqual(tail_lines(p, 2), ["two", "three"])

    def test_tail_jsonl_skips_bad_lines(self):
        p = Path(tempfile.mkdtemp()) / "b.jsonl"
        p.write_text('{"n":1}\nnot-json\n{"n":2}\n', encoding="utf-8")
        self.assertEqual(tail_jsonl(p, 3), [{"n": 1}, {"n": 2}])

    def test_large_tail_is_correct(self):
        p = Path(tempfile.mkdtemp()) / "c.jsonl"
        with p.open("w", encoding="utf-8") as f:
            for i in range(10000):
                f.write(json.dumps({"i": i}) + "\n")
        got = tail_jsonl(p, 5, chunk_size=1024)
        self.assertEqual(got, [{"i": i} for i in range(9995, 10000)])

    def test_tail_text_drops_partial_first_line(self):
        p = Path(tempfile.mkdtemp()) / "d.txt"
        p.write_bytes(b"x" * 100 + b"\nlast-line\n")
        got = tail_text(p, max_bytes=20)
        self.assertEqual(got, "last-line\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)