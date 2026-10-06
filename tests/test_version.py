# -*- coding: utf-8 -*-
"""版本号测试 —— 保证 version.py 与界面导出的版本一致，且是合法 SemVer。"""
from __future__ import annotations
import os
import re
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import version as V  # noqa: E402

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class VersionTest(unittest.TestCase):
    def test_is_semver(self):
        self.assertTrue(SEMVER.match(V.__version__), V.__version__)

    def test_gui_exports_same_version(self):
        import supervisor_gui as G
        self.assertEqual(G.__version__, V.__version__)


if __name__ == "__main__":
    unittest.main(verbosity=2)