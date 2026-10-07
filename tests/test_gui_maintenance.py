# -*- coding: utf-8 -*-
"""维护模式在界面上的呈现：必须显示"维护中"，且**不能把底层判定洗白**。

用户明确要求：维护模式下管家也要有对应状态。
"""
from __future__ import annotations
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from PySide6.QtWidgets import QApplication          # noqa: E402
_app = QApplication.instance() or QApplication([])

import supervisor_gui as G                          # noqa: E402
from gui_widgets import LEVELS, effective_level, underlying_level, maintenance_line  # noqa: E402


def _maint_data(level="BLOCKED", score=33):
    return {
        "sup": {"level": level, "score": score, "maintenance_active": True,
                "maintenance": {"by": "taich", "reason": "外部维护",
                                "until": "2026-10-07T08:44:00+08:00",
                                "remaining_min": 44},
                "reasons": ["测试"]},
        "mon": {}, "hooks": [], "corr": [], "changes": [], "requests": [],
        "alerts": [], "audit": [], "judged": [], "brain": {}, "win": {}, "sups": [],
        "prot": {},
        "mon_level": "NORMAL", "sup_level": level,
        "level": "MAINTENANCE", "underlying_level": level,
        "maintenance": {"by": "taich", "reason": "外部维护",
                        "until": "2026-10-07T08:44:00+08:00", "remaining_min": 44},
        "maintenance_active": True,
        "need_hooks": [], "pending": 0,
    }


class MaintenanceUiTest(unittest.TestCase):
    def test_levels_has_maintenance(self):
        self.assertIn("MAINTENANCE", LEVELS)
        self.assertIn("m", LEVELS["MAINTENANCE"][2])

    def test_theme_has_maintenance_colors(self):
        from supervisor_gui import TOKENS
        for name in ("light", "dark"):
            self.assertIn("mf", TOKENS[name])
            self.assertIn("mb", TOKENS[name])

    def test_effective_and_underlying(self):
        self.assertEqual(effective_level({"maintenance_active": True, "level": "BLOCKED"}),
                         "MAINTENANCE")
        self.assertEqual(effective_level({"level": "WATCH"}), "WATCH")
        self.assertEqual(underlying_level({"underlying_level": "BLOCKED",
                                           "sup": {"level": "BLOCKED"}}), "BLOCKED")

    def test_maintenance_line_has_who_why_when(self):
        txt = maintenance_line(_maint_data())
        for frag in ("taich", "外部维护", "还剩"):
            self.assertIn(frag, txt)

    def test_pages_render_with_maintenance_on(self):
        saved = {}
        tmp = Path(tempfile.mkdtemp(prefix="sup_maint_"))
        for name, val in (("LOG_DIR", tmp), ("HEARTBEAT", tmp / "app.heartbeat"),
                          ("STOPPED", tmp / "app.stopped"), ("SETTINGS", tmp / "settings.json"),
                          ("GUI_PID", tmp / "gui.pid"), ("SIZE_HIST", tmp / "size_history.jsonl")):
            saved[name] = getattr(G, name)
            setattr(G, name, val)
        errors, orig = [], G._log
        G._log = lambda msg: errors.append(str(msg))
        try:
            w = G.Main()
            w.gather = lambda: _maint_data()
            w.refresh()
            # 显示等级必须是维护中，底层必须还在
            self.assertEqual(w.data.get("level"), "MAINTENANCE")
            self.assertEqual(w.data.get("underlying_level"), "BLOCKED")
            self.assertIn("维护中", w.windowTitle())
            self.assertIn("维护中", w.chip_big.text())
            for key in ("overview", "codex", "home"):
                w.nav.setCurrentRow(w.nav_rows[key])
                w.rebuild()
            fails = [e for e in errors if "渲染失败" in e]
            self.assertEqual(fails, [], "维护模式下有页面渲染失败：\n" + "\n".join(fails))
        finally:
            G._log = orig
            for name, val in saved.items():
                setattr(G, name, val)


if __name__ == "__main__":
    unittest.main(verbosity=2)