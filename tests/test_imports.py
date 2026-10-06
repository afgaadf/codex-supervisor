# -*- coding: utf-8 -*-
"""导入守卫：把每个模块都导入一遍。

为什么加：模块级代码若"用了还没定义的名字"（比如 import 加在了使用点之后），
编译期查不出来，但**一导入就 NameError**。2026-10-07 拆分时真踩过
（`supervisor_gui.py` 用了 DATA_DIR 却把 import 放在了后面）。这条测试把它挡在提交前。

跑法：  python -m unittest discover -s tests -v
"""
from __future__ import annotations
import importlib
import os
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# 只跳过纯设计预览（不参与运行）
SKIP = {"_gui_preview"}


class ImportAllTest(unittest.TestCase):
    def test_every_module_imports(self):
        mods = sorted(f[:-3] for f in os.listdir(APP_DIR)
                      if f.endswith(".py") and f[:-3] not in SKIP)
        self.assertGreater(len(mods), 10, "模块太少，路径不对？")
        for m in mods:
            with self.subTest(module=m):
                importlib.import_module(m)


if __name__ == "__main__":
    unittest.main(verbosity=2)
