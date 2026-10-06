# -*- coding: utf-8 -*-
"""GUI 冒烟测试 —— 离屏把整个界面建出来，并逐页渲染一遍。

价值：任何对 supervisor_gui.py 的结构改动（拆模块、搬方法）都能被它兜住。
安全：把界面会写盘的路径（logs/、settings.json）全部改到临时目录，
      绝不碰真实 logs/ 与 app.stopped。

跑法：  python -m unittest discover -s tests -v
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

ALL_PAGES = ("home", "overview", "codex", "vault", "classes", "rules",
             "trust", "corr", "changes", "logs", "learn", "pc", "help")


class GuiSmokeTest(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        tmp = Path(tempfile.mkdtemp(prefix="sup_smoke_"))
        for name, val in (("LOG_DIR", tmp), ("HEARTBEAT", tmp / "app.heartbeat"),
                          ("STOPPED", tmp / "app.stopped"), ("SETTINGS", tmp / "settings.json"),
                          ("GUI_PID", tmp / "gui.pid"), ("SIZE_HIST", tmp / "size_history.jsonl")):
            self._saved[name] = getattr(G, name)
            setattr(G, name, val)

    def tearDown(self):
        for name, val in self._saved.items():
            setattr(G, name, val)

    def test_build_and_render_every_page(self):
        w = G.Main()
        self.assertEqual(len(w.PAGES), len(ALL_PAGES))
        for key in ALL_PAGES:
            with self.subTest(page=key):
                w.nav.setCurrentRow(w.nav_rows[key])
                self.assertEqual(w.current_key(), key)


if __name__ == "__main__":
    unittest.main(verbosity=2)