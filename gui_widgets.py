# -*- coding: utf-8 -*-
"""gui_widgets.py —— 管家界面用到的 Qt 小部件工厂（Stage 2b，2026-10-07 拆出）。

自 supervisor_gui.py 拆出，**行为与拆前一致**；从 supervisor_gui 里按原名字再次导出。
把这些"无状态、只按主题 token 造控件"的工厂独立出来，是为了后续把巨型 Main 类
按页面拆成 mixin 时，有稳定的依赖底座（Google《Small CLs》）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from gui_util import elide


# 等级 = 形状 + 颜色 + 文字 + "要你做什么"（规则 2、3、7）
LEVELS = {
    "NORMAL":   ("●", "正常",     "ok", "不需要你做什么"),
    "WATCH":    ("◆", "观察",     "w",  "盯着就行，先别开新任务"),
    "DEGRADED": ("▲", "降智风险", "d",  "先收尾；需要人在 Codex 之外 resume 才算解锁"),
    "BLOCKED":  ("■", "已阻断",   "b",  "已被拦下；需要人在 Codex 之外 resume 才算解锁"),
}


def chip(level, t, big=False):
    shape, label, key, _need = LEVELS.get(level, ("·", level or "未知", "w", ""))
    fg, bg = t[key + "f"], t[key + "b"]
    lab = QLabel("%s %s" % (shape, label))
    lab.setStyleSheet("color:%s;background:%s;border-radius:%dpx;padding:%s;font-weight:%s;"
                      % (fg, bg, 6 if big else 5, "6px 12px" if big else "2px 8px",
                         "600" if big else "400"))
    lab.setAlignment(Qt.AlignCenter)
    f = QFont()
    f.setPointSize(20 if big else 10)
    lab.setFont(f)
    return lab


def tag(text, t, level=None):
    lab = QLabel(text)
    if level:
        fg, bg = t[level + "f"], t[level + "b"]
        lab.setStyleSheet("color:%s;background:%s;border-radius:5px;padding:2px 8px;" % (fg, bg))
    else:
        lab.setStyleSheet("color:%s;background:%s;border-radius:5px;padding:2px 8px;" % (t["muted"], t["hover"]))
    return lab


def card(parent, title=None, hint=None):
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(8)
    if title:
        t = QLabel(title)
        t.setObjectName("h2")
        v.addWidget(t)
    if hint:
        h = QLabel(hint)
        h.setObjectName("small")
        h.setWordWrap(True)
        h.setMinimumWidth(0)
        h.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(h)
    parent.addWidget(f)
    return f, v


def line(v, label, value, t, wrap=110, link=None):
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    a = QLabel(label)
    a.setObjectName("muted")
    a.setFixedWidth(96)
    h.addWidget(a)
    if link:
        b = QPushButton(elide(value, wrap))
        b.setObjectName("link")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False, u=link: QDesktopServices.openUrl(QUrl(u)))
        h.addWidget(b, 1)
    else:
        b = QLabel(elide(value, wrap))
        b.setWordWrap(True)
        b.setMinimumWidth(0)
        b.setToolTip(str(value))
        b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h.addWidget(b, 1)
    v.addWidget(row)
    return row


def empty(v, text):
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    lab.setMinimumWidth(0)
    lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    v.addWidget(lab)


def kpi(v, label, value, hint, t, level=None, plain=None):
    f = QFrame()
    f.setObjectName("card")
    f.setMinimumHeight(114)
    f.setMinimumWidth(0)
    box = QVBoxLayout(f)
    box.setContentsMargins(16, 12, 16, 12)
    box.setSpacing(2)
    lab = QLabel(label)
    lab.setObjectName("kpiLab")
    box.addWidget(lab)
    plain = plain if plain is not None else GLOSSARY.get(label)
    if plain:
        pl = QLabel(plain)
        pl.setToolTip(GLOSSARY_LONG.get(label, plain))
        pl.setObjectName("small")
        pl.setWordWrap(True)
        pl.setMinimumWidth(0)
        pl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        box.addWidget(pl)
    val = QLabel(value)
    if level:
        val.setStyleSheet("color:%s;" % t[level + "f"])
    val.setObjectName("kpi")
    box.addWidget(val)
    h = QLabel(hint)
    h.setObjectName("small")
    h.setWordWrap(True)
    h.setMinimumWidth(0)
    h.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    box.addWidget(h)
    box.addStretch(1)
    v.addWidget(f, 1)
    return f

def four_verdict(check, kind):
    """四问结论：模型判的优先（行为检测），没有模型判定才退回机械检查。

    返回 (bad, verdict_text, evidence)
    kind: online / vault / lazy / degrade
    """
    c = check or {}
    m = c.get("model") or {}
    v = str(m.get("verdict") or "").strip()
    ev = str(m.get("evidence") or "")
    if v:
        if kind in ("online", "vault"):
            bad = (v == "否")
        else:
            bad = (v == "是")
        if v == "不适用":
            return False, "不适用", ev
        return bad, v, ev
    # 机械兜底
    if kind in ("online", "vault"):
        bad = bool(c.get("needed")) and not bool(c.get("did"))
        return bad, ("否" if bad else "是"), str(c.get("evidence") or "")
    if kind == "lazy":
        return bool(c.get("lazy")), ("是" if c.get("lazy") else "否"), str(c.get("evidence") or "")
    bad = bool(c.get("risky"))
    return bad, ("是" if bad else "否"), str(c.get("evidence") or "")


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


class ToolTile(QFrame):
    """工具箱里的一格：名字 + 一句说明 + 状态，点一下进对应工具。"""
    clicked = Signal()

    def __init__(self, title, desc, badge, tokens, level=""):
        super().__init__()
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(3)
        t = QLabel(title)
        t.setObjectName("h2")
        box.addWidget(t)
        d = QLabel(desc)
        d.setObjectName("small")
        d.setWordWrap(True)
        d.setMinimumWidth(0)
        d.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        box.addWidget(d)
        box.addStretch(1)
        b = QLabel(badge)
        col = {"b": tokens.get("bf"), "w": tokens.get("df"), "ok": tokens.get("okf")}.get(level, tokens.get("muted"))
        b.setStyleSheet("color:%s;font-weight:600;" % col)
        box.addWidget(b)

    def mouseReleaseEvent(self, e):
        self.clicked.emit()


class Sparkline(QWidget):
    """两条线的走势图（CPU / 内存）。不引依赖，自己画。"""

    def __init__(self, points, tokens):
        super().__init__()
        self.points = points or []
        self.t = tokens
        self.setMinimumHeight(120)

    def paintEvent(self, ev):
        t = self.t
        pt = QPainter(self)
        pt.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        pt.fillRect(0, 0, w, h, QColor(t["card"]))
        if len(self.points) < 2:
            pt.setPen(QPen(QColor(t["faint"])))
            pt.drawText(10, h // 2, "采样点还不够：每 5 分钟记一个点，攒够两个就出线")
            return
        pt.setPen(QPen(QColor(t["line"]), 1))
        for lvl in (0, 25, 50, 75, 100):
            y = int((h - 14) * (1 - lvl / 100.0)) + 7
            pt.drawLine(0, y, w, y)
            pt.setPen(QPen(QColor(t["faint"])))
            pt.drawText(2, y - 2, str(lvl))
            pt.setPen(QPen(QColor(t["line"]), 1))
        n = len(self.points)
        for key, color, label in (("cpu", t["accent"], "CPU"), ("mem", t["df"], "内存")):
            fill = QColor(color)
            fill.setAlpha(28)
            poly = []
            for i, row in enumerate(self.points):
                try:
                    val = float(row.get(key) or 0)
                except Exception:
                    val = 0.0
                x = int(i * (w - 3) / max(1, n - 1)) + 1
                y = int((h - 14) * (1 - min(100.0, max(0.0, val)) / 100.0)) + 7
                poly.append(QPointF(x, y))
            if poly:
                pts = list(poly) + [QPointF(poly[-1].x(), h - 7), QPointF(poly[0].x(), h - 7)]
                pt.setPen(Qt.NoPen)
                pt.setBrush(fill)
                pt.drawPolygon(pts)
            pt.setPen(QPen(QColor(color), 2))
            prev = None
            for i, row in enumerate(self.points):
                try:
                    val = float(row.get(key) or 0)
                except Exception:
                    val = 0.0
                x = int(i * (w - 3) / max(1, n - 1)) + 1
                y = int((h - 14) * (1 - min(100.0, max(0.0, val)) / 100.0)) + 7
                if prev is not None:
                    pt.drawLine(prev[0], prev[1], x, y)
                prev = (x, y)
        last = self.points[-1]
        pt.setPen(QPen(QColor(t["accent"])))
        pt.drawText(8, 14, "CPU %s%%" % last.get("cpu"))
        pt.setPen(QPen(QColor(t["df"])))
        pt.drawText(110, 14, "内存 %s%%" % last.get("mem"))
        pt.setPen(QPen(QColor(t["faint"])))
        pt.drawText(w - 150, 14, "首点 %s" % str(self.points[0].get("ts") or "")[11:16])


def _load_pc_state():
    try:
        import json as _j
        return _j.loads((APP_DIR / "pc_state.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def page_header(parent, title, subtitle, tokens, actions=None):
    """每页统一的页头：标题 + 一句话说明 + 右侧动作。规则 5（说明不拉满行宽）。"""
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 12, 16, 12)
    v.setSpacing(4)
    top = QWidget()
    h = QHBoxLayout(top)
    h.setContentsMargins(0, 0, 0, 0)
    t = QLabel(title)
    t.setObjectName("h1")
    h.addWidget(t)
    h.addStretch(1)
    for item in (actions or []):
        lab, cb = item[0], item[1]
        primary = len(item) > 2 and item[2]
        b = QPushButton(lab)
        if primary:
            b.setObjectName("primary")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False, f_=cb: f_())
        h.addWidget(b)
    v.addWidget(top)
    if subtitle:
        s2 = QLabel(subtitle)
        s2.setObjectName("small")
        s2.setWordWrap(True)
        s2.setMinimumWidth(0)
        s2.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(s2)
    parent.addWidget(f)
    return f, v


