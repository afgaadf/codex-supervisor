# -*- coding: utf-8 -*-
"""静态守卫：模块里不能有"用了但没定义/没导入"的裸名字。

为什么加：Stage 2b 拆分时，`_load_pc_state`、`GLOSSARY`、`APP_DIR`、`Path` 被搬到别的
模块却忘了在新家 import，导致界面**运行到那一页才报 NameError**（编译期查不出来）。
本测试把这类"名不达"挡在提交前。

保守策略：只要某名字在文件里**任何地方**被赋值/作参数/import/def 过，就算已定义 ——
这样会放过少数跨作用域的假阴性，但**绝不误报闭包变量**（`run`/`four`/`group` 那类）。

跑法：  python -m unittest discover -s tests -v
"""
from __future__ import annotations
import ast
import builtins
import os
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

FILES = ("supervisor_gui.py", "gui_util.py", "gui_widgets.py", "gui_data.py",
         "version.py", "codex_trust.py")

BUILTINS = set(dir(builtins)) | {"self", "cls", "__file__", "__name__", "__doc__", "__builtins__"}


def _bound_names(tree):
    """文件里任何 "被绑定" 的名字（赋值/参数/def/class/import/except/for/comprehension）。"""
    got = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            got.add(n.id)
        elif isinstance(n, ast.arg):
            got.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            got.add(n.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                got.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                got.add(a.asname or a.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            got.add(n.name)
        elif isinstance(n, ast.Global):
            got.update(n.names)
    return got


class NoUndefinedNamesTest(unittest.TestCase):
    def test_no_undefined_module_level_names(self):
        problems = []
        for fname in FILES:
            path = os.path.join(APP_DIR, fname)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8-sig") as fh:
                tree = ast.parse(fh.read(), filename=fname)
            defined = BUILTINS | _bound_names(tree)
            for fn in [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                for node in ast.walk(fn):
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                        if node.id not in defined:
                            problems.append("%s:%d %s 里用了未定义的名字 %r"
                                            % (fname, node.lineno, fn.name, node.id))
        self.assertEqual(sorted(set(problems)), [],
                         "有裸名字没定义/没导入：\n" + "\n".join(sorted(set(problems))))


if __name__ == "__main__":
    unittest.main(verbosity=2)