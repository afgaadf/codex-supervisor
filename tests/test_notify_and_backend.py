# -*- coding: utf-8 -*-
"""Regression tests for duplicate popups and duplicate backend threads."""
from __future__ import annotations
import os
import sys
import unittest
from unittest import mock

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import supervisor_ui as ui          # noqa: E402
import supervisor_gui as G          # noqa: E402


class ViolationNotificationDedupeTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(ui._cache.get("violation_notified") or {})
        ui._cache["violation_notified"] = {}

    def tearDown(self):
        ui._cache["violation_notified"] = self._saved

    def test_same_violation_is_suppressed_inside_window(self):
        vs = [{"rule": "H2"}, {"rule": "H5"}]
        self.assertTrue(ui._violation_notify_once(vs, now=1000.0))
        self.assertFalse(ui._violation_notify_once([{"rule": "H5"}, {"rule": "H2"}], now=1001.0))

    def test_expired_or_changed_violation_is_allowed(self):
        vs = [{"rule": "H2"}]
        self.assertTrue(ui._violation_notify_once(vs, now=1000.0))
        self.assertTrue(ui._violation_notify_once([], now=1001.0))
        self.assertTrue(ui._violation_notify_once(vs, now=1000.0 + ui.VIOLATION_NOTIFY_SEC + 1))


class BackendStartTest(unittest.TestCase):
    def _common(self):
        return [
            mock.patch.object(G, "_log"),
            mock.patch.object(G.ui, "audit", return_value={}),
            mock.patch.object(G.ui, "write_plugin_token"),
            mock.patch.object(G.ui, "export_corrections"),
        ]

    def test_gui_skips_engine_when_butler_is_alive(self):
        patches = self._common() + [mock.patch.object(G, "_butler_alive", return_value=True)]
        with patches[0], patches[1], patches[2], patches[3], patches[4],              mock.patch.object(G.threading, "Thread") as thread_cls:
            G.start_backend()
        thread_cls.assert_not_called()

    def test_gui_starts_one_engine_when_butler_is_absent(self):
        patches = self._common() + [mock.patch.object(G, "_butler_alive", return_value=False)]
        with patches[0], patches[1], patches[2], patches[3], patches[4],              mock.patch.object(G, "_guard", side_effect=lambda name, fn: name),              mock.patch.object(G.threading, "Thread") as thread_cls:
            G.start_backend()
        targets = [call.kwargs.get("target") for call in thread_cls.call_args_list]
        self.assertEqual(targets, ["monitor_loop", "brain", "judge_loop", "api", "pc_loop", "vault_ops"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
