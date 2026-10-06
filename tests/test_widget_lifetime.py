# -*- coding: utf-8 -*-
"""监督者界面 —— 控件生命周期回归测试。

对应真实缺陷（logs/self_defect.md，2026-10-07 记录）：
  RuntimeError: libshiboken: Internal C++ object
  (PySide6.QtWidgets.QPushButton) already deleted.

机制：界面 refresh() -> rebuild() 会把整页控件 setParent(None)+deleteLater()；
此时若后台线程的回调再访问这些控件，PySide6 就抛上面这个 RuntimeError。

依据（T1）：Qt 官方《Threads and QObjects》
  "the GUI classes, notably QWidget and all its subclasses, are not reentrant.
   They can only be used from the main thread."
  https://doc.qt.io/qt-6/threads-qobject.html  （访问 2026-10-07）

跑法：  python -m unittest discover -s tests -v
"""
from __future__ import annotations
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 无窗口也能跑

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from PySide6.QtCore import QCoreApplication, QEvent            # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton        # noqa: E402

_app = QApplication.instance() or QApplication([])

import supervisor_gui as G                                     # noqa: E402


def flush_deletes():
    """真正执行 deleteLater()（只调 processEvents 不会触发删除）。"""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _app.processEvents()


class WidgetLifetimeTest(unittest.TestCase):
    def test_01_reproduce_recorded_crash(self):
        """先复现记录过的崩溃：控件销毁后再用 -> RuntimeError。"""
        b = QPushButton("x")
        b.deleteLater()
        flush_deletes()
        with self.assertRaises(RuntimeError):
            b.setText("y")

    def test_02_alive_detects_destroyed(self):
        """_alive() 必须能分辨「活控件 / 已销毁 / None」。"""
        b = QPushButton("x")
        self.assertTrue(G._alive(b))
        self.assertFalse(G._alive(None))
        b.deleteLater()
        flush_deletes()
        self.assertFalse(G._alive(b))

    def test_03_set_text_safe(self):
        """已销毁控件上 _set_text 不得抛异常；活控件照常生效。"""
        dead = QPushButton("old")
        dead.deleteLater()
        flush_deletes()
        G._set_text(dead, "new")            # 关键：不得抛 RuntimeError
        live = QPushButton()
        G._set_text(live, "ok")
        self.assertEqual(live.text(), "ok")

    def test_04_set_enabled_safe(self):
        dead = QPushButton()
        dead.deleteLater()
        flush_deletes()
        G._set_enabled(dead, True)          # 关键：不得抛异常
        live = QPushButton()
        G._set_enabled(live, False)
        self.assertFalse(live.isEnabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)

class GuardedMethodsTest(unittest.TestCase):
    """直接测改动过的方法：控件缺失 / 控件存活两条路径。"""

    def test_05_missing_widgets_do_not_crash(self):
        class Fake:
            pass
        f = Fake()                       # 没有任何控件属性
        f.refresh = lambda: None         # pc_done 末尾会调 refresh
        G.Main.pc_note(f, "hi")          # 不得抛
        G.Main.pc_done(f, {"ok": True})  # 不得抛

    def test_06_live_widgets_still_update(self):
        from PySide6.QtWidgets import QLabel
        class Fake:
            pass
        f = Fake()
        f.lbl_pc_note = QLabel()
        f.lbl_need = QLabel()
        G.Main.pc_note(f, "hello")
        self.assertEqual(f.lbl_pc_note.text(), "hello")
        self.assertEqual(f.lbl_need.text(), "hello")

    def test_07_on_research_missing_button_does_not_crash(self):
        class Fake:
            pass
        f = Fake()
        f.notes = []
        f.rebuild = lambda: None
        G.Main.on_research(f, {"ok": True, "answer": "a"})   # 不得抛
