# -*- coding: utf-8 -*-
"""A7 回归：同一个崩溃不许反复重写缺陷报告；出现新崩溃才重写。"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import brain

TB = "Traceback (most recent call last):\n  File \"x.py\", line 1, in <module>\nValueError: boom\n"


def _stub_ui():
    m = types.ModuleType("supervisor_ui")
    m.export_corrections = lambda: None
    return m


class SelfDefectReportTest(unittest.TestCase):
    def _patches(self, d):
        log = Path(d)
        return [
            mock.patch.object(brain, "LOG_DIR", log, create=True),
            mock.patch.object(brain, "K_APP", log / "app.log"),
            mock.patch.object(brain, "K_ERR", log / "app.err.log"),
            mock.patch.object(brain, "K_WD", log / "watchdog.log"),
            mock.patch.object(brain, "SELF_DEFECT_STATE", log / "self_defect_state.json"),
            mock.patch.dict(sys.modules, {"supervisor_ui": _stub_ui()}),
        ]

    def test_same_crash_reported_once(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d)
            (log / "app.err.log").write_text(TB, encoding="utf-8")
            patches = self._patches(d)
            for p in patches:
                p.start()
            try:
                did1 = brain.act([], {"notes": ["自己崩过 1 次"]})
                self.assertTrue(any("自我缺陷报告" in x for x in did1), "第一次应当写报告")
                did2 = brain.act([], {"notes": ["自己崩过 1 次"]})
                self.assertFalse(any("自我缺陷报告" in x for x in did2), "同一崩溃不该重写")
                state = json.loads((log / "self_defect_state.json").read_text(encoding="utf-8"))
                self.assertEqual(state["times"], 2)
                self.assertTrue((log / "self_defect.md").exists())
            finally:
                for p in patches:
                    p.stop()

    def test_new_crash_reports_again(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d)
            (log / "app.err.log").write_text(TB, encoding="utf-8")
            patches = self._patches(d)
            for p in patches:
                p.start()
            try:
                brain.act([], {"notes": ["自己崩过 1 次"]})
                with open(log / "app.err.log", "a", encoding="utf-8") as f:
                    f.write(TB.replace("ValueError: boom", "KeyError: new"))
                did = brain.act([], {"notes": ["自己崩过 2 次"]})
                self.assertTrue(any("自我缺陷报告" in x for x in did), "新崩溃应当重写")
                state = json.loads((log / "self_defect_state.json").read_text(encoding="utf-8"))
                self.assertEqual(state["times"], 1)
            finally:
                for p in patches:
                    p.stop()

    def test_no_crash_no_report(self):
        with tempfile.TemporaryDirectory() as d:
            patches = self._patches(d)
            for p in patches:
                p.start()
            try:
                did = brain.act([], {"notes": []})
                self.assertFalse(any("自我缺陷报告" in x for x in did))
            finally:
                for p in patches:
                    p.stop()


if __name__ == "__main__":
    unittest.main()