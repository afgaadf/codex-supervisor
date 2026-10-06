# -*- coding: utf-8 -*-
"""GUI 冒烟测试 —— 离屏把整个界面建出来，并逐页渲染一遍。

**关键**：不仅要能"切到"每一页，还要断言每一页**真的渲染成功**。
界面 rebuild() 会把页面渲染异常吞掉、改用一张"页面渲染失败"卡片显示，
所以只看 current_key() 是不够的 —— 这里改成拦截 _log，断言没有"渲染失败"。

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
        self._errors = []
        self._orig_log = G._log
        G._log = lambda msg: self._errors.append(str(msg))

    def tearDown(self):
        G._log = self._orig_log
        for name, val in self._saved.items():
            setattr(G, name, val)

    def test_build_and_render_every_page(self):
        w = G.Main()
        self.assertEqual(len(w.PAGES), len(ALL_PAGES))
        for key in ALL_PAGES:
            with self.subTest(page=key):
                w.nav.setCurrentRow(w.nav_rows[key])
                w.rebuild()
                self.assertEqual(w.current_key(), key)
        fails = [e for e in self._errors if "渲染失败" in e]
        self.assertEqual(fails, [], "有页面渲染失败：\n" + "\n".join(fails))


if __name__ == "__main__":
    unittest.main(verbosity=2)