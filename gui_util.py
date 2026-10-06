# -*- coding: utf-8 -*-
"""gui_util.py —— 管家界面的纯工具函数（无 Qt、无副作用，易测）。

自 supervisor_gui.py 拆出（Stage 2a，2026-10-07），**行为与拆前完全一致**。
拆的理由见知识库《成熟公司软件工程心得（对管家系统的适用）》：
单文件 2400+ 行难维护，先把"能独立测试的纯函数"分出来（Google《Small CLs》）。
"""
from __future__ import annotations

import json
from pathlib import Path

from paths import DATA_DIR

from gui_data import APP_DIR

RANK = {"NORMAL": 0, "WATCH": 1, "DEGRADED": 2, "BLOCKED": 3}


def elide(s, n):
    s = "" if s is None else str(s)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:max(1, n - 1)] + "…"


def hhmmss(ts):
    """ISO 时间戳 → 本机时区的 HH:MM:SS（内部那套记的是 UTC，直接切字符串会差 8 小时）。"""
    s = str(ts or "")
    try:
        import datetime as _dt
        t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is not None:
            t = t.astimezone()
        return t.strftime("%H:%M:%S")
    except Exception:
        return s[11:19] if len(s) >= 19 else (s or "—")


def fmt_bytes(n):
    try:
        n = float(n)
    except Exception:
        return "—"
    if n < 1024:
        return "%.0f B" % n
    if n < 1024 ** 2:
        return "%.0f KB" % (n / 1024)
    if n < 1024 ** 3:
        return "%.1f MB" % (n / 1024 ** 2)
    return "%.1f GB" % (n / 1024 ** 3)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def tail_jsonl(path, n):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    out = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def safe(fn, default):
    try:
        r = fn()
        return default if r is None else r
    except Exception:
        return default


def worse(a, b):
    return a if RANK.get(a, -1) >= RANK.get(b, -1) else b


def _alive(w):
    """控件是否仍有效（没被 refresh()→rebuild() 的 deleteLater() 销毁）。

    依据（T1）：Qt 官方《Threads and QObjects》——GUI 类只能在主线程用；
    控件销毁后再访问会抛
    RuntimeError: libshiboken: Internal C++ object (...) already deleted.
    https://doc.qt.io/qt-6/threads-qobject.html （访问 2026-10-07）
    """
    if w is None:
        return False
    try:
        import shiboken6
        return bool(shiboken6.isValid(w))
    except Exception:
        return True          # 取不到 shiboken6 时保守放行，交给调用处


def _set_text(w, text):
    """安全设置文字：控件已销毁就跳过；不吞掉别的异常。"""
    if _alive(w):
        w.setText(text)


def _set_enabled(w, on):
    """安全启用/禁用控件：控件已销毁就跳过。"""
    if _alive(w):
        w.setEnabled(on)


def fresh(modname):
    """按文件 mtime 就地重载模块 —— 避免"改了代码但窗口还跑旧逻辑"。"""
    import importlib
    import sys as _sys
    m = _sys.modules.get(modname)
    if m is None:
        return __import__(modname)
    try:
        f = Path(getattr(m, "__file__", ""))
        if f.exists():
            mt = f.stat().st_mtime
            if mt > float(getattr(m, "_loaded_mtime", 0) or 0):
                m = importlib.reload(m)
                m._loaded_mtime = mt
    except Exception:
        pass
    return m


def _load_pc_state():
    try:
        import json as _j
        return _j.loads((DATA_DIR / "pc_state.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
