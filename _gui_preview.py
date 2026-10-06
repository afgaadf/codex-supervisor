# -*- coding: utf-8 -*-
"""supervisor_gui.py —— 监督者界面（PySide6，2026-10-06 重新设计版）。

设计规则见 `设计依据.md`（7 条，全部指得到出处）。信息架构：

    顶栏   状态指示器（形状+颜色+文字） + "要你做什么"
    左栏   分页导航（带待办计数）
    主区   总览 / 小工具信任 / 责令改正 / 改监督者的申请 / 告警与审计 / 怎么看
    底栏   指令框（说人话 → 监督者去查、去办）

动作都是真接的：
    · 小工具信任：信任/同意、撤销信任  → 写 config.toml（AI 没有令牌，改不了）
    · 责令改正：继续责令 / 划掉
    · 改监督者的申请：同意并应用（监督者代写，先备份）/ 拒绝（问理由，喂给大脑）
"""
from __future__ import annotations
import json
import sys
import threading
import time
import traceback
from pathlib import Path
from string import Template

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

LOG_DIR = APP_DIR / "logs"
HEARTBEAT = LOG_DIR / "app.heartbeat"     # 看门狗靠它判断"窗口还在"
STOPPED = LOG_DIR / "app.stopped"         # 你主动退出 → 看门狗不再拉起
SETTINGS = APP_DIR / "settings.json"
SIZE_HIST = LOG_DIR / "size_history.jsonl"

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import supervisor_ui as ui


# ============================================================ 主题 token
# 浅色那套是 设计依据.md §三 实测过的色对（WCAG 2.2 SC 1.4.3 要求 ≥ 4.5:1）
TOKENS = {
    "light": dict(
        win="#f4f6f8", card="#ffffff", line="#d8dee4", text="#1b1b1b",
        muted="#5c5c5c", faint="#767676", accent="#0b5394", accent2="#0a4780",
        sel="#e8f0fe", hover="#f0f3f6", okf="#0e6b30", okb="#e6f4ea",
        wf="#7a4b00", wb="#fff4e0", df="#8a3a00", db="#fdece0",
        bf="#a80000", bb="#fde7e9",
    ),
    "dark": dict(
        win="#1c1c1c", card="#282828", line="#3d3d3d", text="#f2f2f2",
        muted="#d6d6d6", faint="#a8a8a8", accent="#7cb0ff", accent2="#9cc4ff",
        sel="#2f3b4d", hover="#333333", okf="#9fe3b4", okb="#16351f",
        wf="#ffd08a", wb="#3a2a10", df="#ffb183", db="#3d2415",
        bf="#ff9f9f", bb="#401a1c",
    ),
}

# 等级 = 形状 + 颜色 + 文字 + "要你做什么"（规则 2、3、7）
LEVELS = {
    "NORMAL":   ("●", "正常",     "ok", "不需要你做什么"),
    "WATCH":    ("◆", "观察",     "w",  "盯着就行，先别开新任务"),
    "DEGRADED": ("▲", "降智风险", "d",  "先收尾；需要人在 Codex 之外 resume 才算解锁"),
    "BLOCKED":  ("■", "已阻断",   "b",  "已被拦下；需要人在 Codex 之外 resume 才算解锁"),
}
RANK = {"NORMAL": 0, "WATCH": 1, "DEGRADED": 2, "BLOCKED": 3}

ALERT_ZH = {"level_change": "等级变化", "supervisor_stale": "内部监督者停摆",
            "supervisor_on": "启用监管者", "tool_error": "工具报错"}
AUDIT_ZH = {"app_start": "窗口启动", "trust": "你同意了小工具", "revoke": "你取消了同意",
            "deny": "你拒绝了一次申请", "request": "收到一次申请",
            "change_request": "有人申请改监督者", "change_applied": "你同意并应用了改监督者",
            "change_rejected": "你拒绝了改监督者", "conv_close": "你关闭了一个对话"}

CSS_T = Template("""
QWidget { color: $text; font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 10pt; }
QWidget#root { background: $win; }
QFrame#card { background: $card; border: 1px solid $line; border-radius: 8px; }
QFrame#hdr { background: $card; border-bottom: 1px solid $line; }
QFrame#bar { background: $card; border-top: 1px solid $line; }
QLabel#h1 { font-size: 20pt; font-weight: 600; }
QLabel#h2 { font-size: 13pt; font-weight: 600; }
QLabel#kpi { font-size: 20pt; font-weight: 600; }
QLabel#kpiLab { font-size: 9pt; color: $muted; }
QLabel#small { font-size: 9pt; color: $faint; }
QLabel#muted { color: $muted; }
QListWidget#nav { background: $card; border: 1px solid $line; border-radius: 8px; outline: none; padding: 6px; }
QListWidget#nav::item { padding: 9px 10px; border-radius: 6px; }
QListWidget#nav::item:selected { background: $sel; font-weight: 600; }
QPushButton { background: $card; border: 1px solid $line; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: $hover; }
QPushButton:disabled { color: $faint; }
QPushButton#primary { background: $accent; color: #ffffff; border: 1px solid $accent; font-weight: 600; }
QPushButton#primary:hover { background: $accent2; }
QPushButton#primary:disabled { background: $line; color: $faint; border-color: $line; }
QPushButton#link { border: none; background: transparent; color: $accent; text-align: left; padding: 2px 0; }
QLineEdit#cmd { border: 1px solid $line; border-radius: 6px; padding: 8px 10px; background: $card; }
QTableWidget { background: $card; border: 1px solid $line; border-radius: 8px; gridline-color: $line; }
QHeaderView::section { background: $win; border: none; border-bottom: 1px solid $line; padding: 7px; color: $muted; }
QTableWidget::item:selected { background: $sel; color: $text; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: $line; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
""")


def css(t):
    return CSS_T.substitute(t)


# ============================================================ 数据小工具
def elide(s, n):
    s = "" if s is None else str(s)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:max(1, n - 1)] + "…"


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


def load_settings():
    d = read_json(SETTINGS, {})
    return d if isinstance(d, dict) else {}


def save_settings(**kw):
    d = load_settings()
    d.update(kw)
    try:
        SETTINGS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass

# ============================================================ 后台（监管引擎）
def _log(msg):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "app.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    except Exception:
        pass


def _guard(name, fn):
    def run():
        try:
            fn()
        except Exception:
            _log(name + " 线程异常\n" + traceback.format_exc())
    return run


def _brain_loop():
    import brain as B
    B.run_once()
    B.loop(45)


def _serve_api():
    import socketserver
    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.TCPServer((ui.HOST, ui.PORT), ui.H)
    except OSError as e:
        _log("本机 API 端口被占，跳过：%s" % e)
        return
    _log("本机 API 已监听 http://%s:%s/" % (ui.HOST, ui.PORT))
    httpd.serve_forever()


def start_backend():
    """监管引擎：监控循环 / 大脑 / 判定循环 / 本机 API。

    旧 Tk 版（backup/supervisor_app.py.retired-20261006 的 start_backend）就干这四件事；
    PySide6 版重写时漏了这一段，窗口就变成一个只读展示板：不监控、不判定、不监听 8765。
    """
    try:
        ui.audit("app_start", "-", host=ui.HOST, port=ui.PORT)
    except Exception:
        pass
    try:
        ui.export_corrections()
    except Exception:
        _log("export_corrections 失败\n" + traceback.format_exc())
    threading.Thread(target=_guard("monitor_loop", ui.monitor_loop), daemon=True).start()
    threading.Thread(target=_guard("brain", _brain_loop), daemon=True).start()
    threading.Thread(target=_guard("judge_loop", ui.judge_loop), daemon=True).start()
    threading.Thread(target=_guard("api", _serve_api), daemon=True).start()


# ============================================================ 小部件
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


def kpi(v, label, value, hint, t, level=None):
    f = QFrame()
    f.setObjectName("card")
    f.setMinimumHeight(104)
    f.setMinimumWidth(0)
    box = QVBoxLayout(f)
    box.setContentsMargins(16, 12, 16, 12)
    box.setSpacing(2)
    lab = QLabel(label)
    lab.setObjectName("kpiLab")
    box.addWidget(lab)
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

# ============================================================ 主窗口
class Main(QMainWindow):
    PAGES = (("overview", "总览"), ("trust", "小工具信任"), ("corr", "责令改正"),
             ("changes", "改监督者的申请"), ("logs", "告警与审计"), ("help", "怎么看"))

    def __init__(self):
        super().__init__()
        self.setWindowTitle("监督者")
        self.st = load_settings()
        self.theme = self.st.get("theme") if self.st.get("theme") in TOKENS else "light"
        self.t = TOKENS[self.theme]
        self.data = {}
        self.notes = []
        self.trust_key = None
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if STOPPED.exists():
                STOPPED.unlink()
        except Exception:
            pass

        self.resize(1200, 800)
        placed = False
        try:
            import re as _re
            m = _re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", str(self.st.get("geometry") or ""))
            if m:
                w, h, x, y = (int(x) for x in m.groups())
                self.resize(max(940, w), max(620, h))
                self.move(x, y)
                placed = True
        except Exception:
            pass
        if not placed:
            scr = QApplication.primaryScreen()
            if scr is not None:
                a = scr.availableGeometry()
                self.resize(max(940, min(1200, a.width() - 80)), max(620, min(800, a.height() - 80)))
                self.move(a.center().x() - self.width() // 2,
                          max(a.top() + 16, a.center().y() - self.height() // 2))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 顶栏：状态 + 要你做什么 + 换外观
        hdr = QFrame()
        hdr.setObjectName("hdr")
        h = QHBoxLayout(hdr)
        h.setContentsMargins(18, 12, 14, 12)
        h.setSpacing(12)
        ttl = QLabel("监督者")
        ttl.setObjectName("h2")
        h.addWidget(ttl)
        self.chip_big = chip("WATCH", self.t, big=True)
        h.addWidget(self.chip_big)
        self.lbl_need = QLabel("…")
        self.lbl_need.setObjectName("muted")
        self.lbl_need.setWordWrap(True)
        self.lbl_need.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        h.addWidget(self.lbl_need, 1)
        self.btn_theme = QPushButton("换外观")
        self.btn_theme.clicked.connect(self.toggle_theme)
        h.addWidget(self.btn_theme)
        self.btn_quit = QPushButton("退出")
        self.btn_quit.clicked.connect(self.close)
        h.addWidget(self.btn_quit)
        outer.addWidget(hdr)

        # ---- 主体：左导航 + 主区
        body = QHBoxLayout()
        body.setContentsMargins(14, 12, 14, 8)
        body.setSpacing(12)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(190)
        for key, name in self.PAGES:
            it = QListWidgetItem(name)
            it.setData(Qt.UserRole, key)
            self.nav.addItem(it)
        self.nav.setCurrentRow(0)
        self.nav.currentItemChanged.connect(lambda *_: self.rebuild())
        body.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.page = QWidget()
        self.page.setObjectName("page")
        self.page.setMinimumWidth(0)
        self.pv = QVBoxLayout(self.page)
        self.pv.setContentsMargins(0, 0, 6, 0)
        self.pv.setSpacing(12)
        self.pv.addStretch(1)
        self.scroll.setWidget(self.page)
        self.stack.addWidget(self.scroll)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        # ---- 底栏：指令框
        bar = QFrame()
        bar.setObjectName("bar")
        b = QHBoxLayout(bar)
        b.setContentsMargins(14, 10, 14, 10)
        b.setSpacing(8)
        self.cmd = QLineEdit()
        self.cmd.setObjectName("cmd")
        self.cmd.setPlaceholderText("直接说人话：监督者去查/去办。例：查一下 xxx 的最新规范 / 刷新 / 在盯哪个")
        self.cmd.setMinimumHeight(34)
        self.cmd.returnPressed.connect(self.run_cmd)
        b.addWidget(self.cmd, 1)
        self.btn_go = QPushButton("执行")
        self.btn_go.setObjectName("primary")
        self.btn_go.setMinimumHeight(34)
        self.btn_go.clicked.connect(self.run_cmd)
        b.addWidget(self.btn_go)
        outer.addWidget(bar)

        self.worker = None
        QShortcut(QKeySequence("F5"), self, activated=self.refresh)
        QShortcut(QKeySequence("Ctrl+Tab"), self, activated=self.next_page)
        QShortcut(QKeySequence("Esc"), self, activated=self.showMinimized)

        self.apply_theme()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    # -------------------------------------------------- 主题
    def apply_theme(self):
        self.t = TOKENS[self.theme]
        self.setStyleSheet(css(self.t))
        QApplication.setFont(QFont("Microsoft YaHei UI", 10))

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        save_settings(theme=self.theme)
        self.apply_theme()
        self.rebuild()

    # -------------------------------------------------- 数据
    def gather(self):
        sup = safe(ui.supervisor_status, {}) or {}
        mon = ui._cache.get("monitor") or {}
        if not mon:
            mon = safe(ui.compute_monitor, {}) or {}
        try:
            import codex_trust as ct
            hooks = ct.list_handlers()
        except Exception:
            hooks = []
        d = dict(
            sup=sup, mon=mon, hooks=hooks,
            corr=safe(ui.open_corrections, []),
            changes=safe(ui.change_requests, []),
            requests=safe(ui.open_requests, []),
            alerts=tail_jsonl(ui.ALERTS, 40),
            audit=tail_jsonl(ui.AUDIT, 40),
            judged=tail_jsonl(ui.JUDGE, 8),
            brain=read_json(APP_DIR / "brain.json", {}),
            win=safe(ui.running_window, {}),
            sups=safe(ui.active_supervisors, []),
            prot=safe(ui.protection_status, {}),
        )
        d["mon_level"] = str(mon.get("independent_level") or "")
        d["sup_level"] = str(sup.get("level") or "")
        d["level"] = worse(d["mon_level"] or "NORMAL", d["sup_level"] or "NORMAL")
        d["need_hooks"] = [x for x in d["hooks"] if x.get("status") != "trusted"]
        d["pending"] = (len(d["need_hooks"]) + len(d["corr"]) + len(d["changes"]) + len(d["requests"]))
        return d

    def refresh(self):
        try:
            self.data = self.gather()
        except Exception:
            self.data = {}
        d = self.data
        lvl = d.get("level") or "WATCH"
        shape, label, key, need = LEVELS.get(lvl, ("·", "未知", "w", ""))
        self.chip_big.setText("%s %s" % (shape, label))
        fg, bg = self.t[key + "f"], self.t[key + "b"]
        self.chip_big.setStyleSheet("color:%s;background:%s;border-radius:6px;padding:6px 12px;font-weight:600;"
                                    % (fg, bg))
        win = d.get("win") or {}
        pend = d.get("pending", 0)
        extra = ("　·　待你处理 %d 条" % pend) if pend else "　·　没有等你处理的事"
        self.lbl_need.setText(elide("%s%s　·　正在盯：%s" % (need, extra, win.get("title") or "—"), 120))
        counts = {"trust": len(d.get("need_hooks") or []), "corr": len(d.get("corr") or []),
                  "changes": len(d.get("changes") or [])}
        for i, (k, name) in enumerate(self.PAGES):
            c = counts.get(k, 0)
            self.nav.item(i).setText(name + (("  (%d)" % c) if c else ""))
        try:
            HEARTBEAT.write_text(str(int(time.time())), encoding="utf-8")
        except Exception:
            pass
        self.rebuild()

    def current_key(self):
        it = self.nav.currentItem()
        return it.data(Qt.UserRole) if it else "overview"

    def next_page(self):
        self.nav.setCurrentRow((self.nav.currentRow() + 1) % self.nav.count())

    def rebuild(self):
        while self.pv.count():
            it = self.pv.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        key = self.current_key()
        builder = {"overview": self.pg_overview, "trust": self.pg_trust, "corr": self.pg_corr,
                   "changes": self.pg_changes, "logs": self.pg_logs, "help": self.pg_help}.get(key)
        if builder:
            try:
                builder(self.pv)
            except Exception:
                _log("页面 %s 渲染失败\n%s" % (key, traceback.format_exc()))
                card(self.pv, "页面渲染失败", traceback.format_exc()[-400:])
        self.pv.addStretch(1)

    # -------------------------------------------------- 总览
    def pg_overview(self, v):
        t, d = self.t, self.data
        mon, sup = d.get("mon") or {}, d.get("sup") or {}
        size = mon.get("transcript_bytes")
        hist = tail_jsonl(SIZE_HIST, 2)
        delta = ""
        if hist and size:
            try:
                old = float(hist[-1].get("v"))
                if old > 0:
                    pct = (float(size) - old) / old * 100
                    delta = "%s %.1f%%（自 %s）" % ("↑" if pct >= 0 else "↓", abs(pct),
                                                    str(hist[-1].get("ts"))[11:19])
            except Exception:
                delta = ""
        counters = sup.get("counters") or {}
        lvl = d.get("level") or "WATCH"
        k = QHBoxLayout()
        k.setSpacing(12)
        kpi(k, "判定等级", "%s %s" % (LEVELS.get(lvl, ("·", "—"))[0], LEVELS.get(lvl, ("", "—"))[1]),
            "取两个监督者里更坏的那个", t, LEVELS.get(lvl, ("", "", "w"))[2])
        kpi(k, "记录大小", fmt_bytes(size), delta or "transcript 字节数（越大越接近上下文爆炸）", t)
        kpi(k, "已跑轮次", str(counters.get("turns") or (mon.get("counters") or {}).get("turns") or "—"),
            "命令 %s 次 · 工具报错 %s 次" % ((mon.get("events") or {}).get("command", "—"),
                                            (mon.get("events") or {}).get("tool_error", "—")), t)
        kpi(k, "待你处理", str(d.get("pending", 0)),
            "未信任小工具 %d · 责令 %d · 提案 %d" % (len(d.get("need_hooks") or []),
                                                    len(d.get("corr") or []), len(d.get("changes") or [])),
            t, "ok" if not d.get("pending") else "w")
        wrap = QWidget()
        wrap.setLayout(k)
        v.addWidget(wrap)

        f, bv = card(v, "两个监督者，按更坏的那个算",
                     "左边是本程序自己读 Codex 原始记录算的；右边是 Codex 内部 supervisor 算的。")
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        h.addWidget(QLabel("本程序独立判定"))
        h.addWidget(chip(d.get("mon_level") or "NORMAL", t))
        h.addSpacing(6)
        h.addWidget(QLabel("Codex 内部判定"))
        h.addWidget(chip(d.get("sup_level") or "NORMAL", t))
        h.addStretch(1)
        bv.addWidget(row)
        tp = str(mon.get("transcript") or "—")
        line(bv, "记录文件", "…\\%s（从 %s 起算）" % (tp.replace("/", "\\").split("\\")[-1],
                                                str(mon.get("window_start") or "—")[11:19]), t, 90)
        line(bv, "内部分数", "score=%s　原因：%s" % (sup.get("score", "—"),
                                              "；".join(sup.get("reasons") or []) or "—"), t, 110)
        line(bv, "内部节拍", "%s（%s 分钟前）%s" % (str((sup.get("counters") or {}).get("last_turn_end_ts") or "—")[11:19],
                                               "%.0f" % (mon.get("stale_min") or 0),
                                               "　·　内部监督者可能停摆" if mon.get("stale") else ""), t, 110)

        pend_items = []
        for hk in (d.get("need_hooks") or []):
            pend_items.append(("小工具信任", "%s：%s" % (hk.get("event_label"), hk.get("command")), "trust"))
        for c in (d.get("corr") or []):
            pend_items.append(("责令改正", elide(c.get("ask") or c.get("turn"), 60), "corr"))
        for c in (d.get("changes") or []):
            pend_items.append(("改监督者的申请", elide(c.get("title") or c.get("id"), 60), "changes"))
        for r in (d.get("requests") or []):
            pend_items.append(("授权申请", elide(r.get("reason") or r.get("key"), 60), "trust"))
        f, bv = card(v, "等你处理（%d）" % len(pend_items))
        if not pend_items:
            empty(bv, "没有等你处理的事。")
        for label, text, page in pend_items[:8]:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(tag(label, t))
            lab = QLabel(elide(text, 90))
            lab.setWordWrap(True)
            h.addWidget(lab, 1)
            go = QPushButton("去处理")
            go.setObjectName("link")
            go.setCursor(Qt.PointingHandCursor)
            go.clicked.connect(lambda _=False, p=page: self.goto(p))
            h.addWidget(go)
            bv.addWidget(row)

        br = d.get("brain") or {}
        findings = br.get("findings") or []
        f, bv = card(v, "监督者大脑：最近发现",
                     "大脑只能改自己的信号清单（数据）；要动别的，得走「提案 → 你同意 → 监督者代写」。")
        if not findings:
            empty(bv, "这一轮没有发现。（没发现不等于没问题。）")
        for x in findings[:4]:
            sev = {"要紧": "BLOCKED", "注意": "WATCH", "还好": "NORMAL"}.get(x.get("sev"), "WATCH")
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(chip(sev, t))
            box = QVBoxLayout()
            box.setSpacing(2)
            a = QLabel(str(x.get("title") or ""))
            a.setWordWrap(True)
            box.addWidget(a)
            b = QLabel("证据：%s" % elide(x.get("evidence"), 90))
            b.setObjectName("small")
            b.setWordWrap(True)
            box.addWidget(b)
            c = QLabel("怎么办：%s" % elide(x.get("advice"), 90))
            c.setObjectName("small")
            c.setWordWrap(True)
            box.addWidget(c)
            h.addLayout(box, 1)
            bv.addWidget(row)
        health = (br.get("health") or {}).get("notes") or []
        if health:
            line(bv, "体检", "；".join(elide(x, 44) for x in health[:3]), t, 120)
        line(bv, "上次动脑", str(br.get("ts") or "—"), t, 40)

        if self.notes:
            f, bv = card(v, "我让它查的")
            for n in self.notes[:5]:
                a = QLabel(str(n.get("title") or ""))
                a.setObjectName("h2")
                a.setWordWrap(True)
                bv.addWidget(a)
                b = QLabel(elide(n.get("body"), 600))
                b.setWordWrap(True)
                bv.addWidget(b)
                for src in (n.get("sources") or [])[:3]:
                    url = src.get("url") if isinstance(src, dict) else src
                    if not url:
                        continue
                    btn = QPushButton("来源 ↗  " + elide(url, 70))
                    btn.setObjectName("link")
                    btn.setCursor(Qt.PointingHandCursor)
                    btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
                    bv.addWidget(btn)

        evs = []
        for a in (d.get("alerts") or []):
            evs.append((a.get("ts"), ALERT_ZH.get(a.get("kind"), a.get("kind")), a.get("detail")))
        for a in (d.get("audit") or []):
            evs.append((a.get("ts"), AUDIT_ZH.get(a.get("action"), a.get("action")), a.get("key")))
        evs = [e for e in evs if e[0]]
        evs.sort(key=lambda x: str(x[0]), reverse=True)
        f, bv = card(v, "最近动静")
        if not evs:
            empty(bv, "还没有记录。")
        for ts, what, detail in evs[:6]:
            line(bv, str(ts)[11:19], "%s　%s" % (what, elide(detail, 70)), t, 110)

    # -------------------------------------------------- 小工具信任
    def pg_trust(self, v):
        t, d = self.t, self.data
        hooks = d.get("hooks") or []
        need = d.get("need_hooks") or []
        f, bv = card(v, "Codex 能自动运行的小工具",
                     "信任状态是本程序按 Codex 官方算法重算的。点过「信任」才会执行；脚本被改动过的旧信任会失效。")
        head = QWidget()
        h = QHBoxLayout(head)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(tag("%d 条：%d 已信任，%d 待处理" % (len(hooks), len(hooks) - len(need), len(need)),
                        t, "ok" if not need else "w"))
        h.addStretch(1)
        for shape, text in (("✔", "已信任"), ("＋", "新增·未信任"), ("✎", "被改动·需重信任")):
            h.addWidget(QLabel("%s %s" % (shape, text)))
        bv.addWidget(head)

        tbl = QTableWidget(len(hooks), 3)
        tbl.setHorizontalHeaderLabels(["什么时候跑", "什么工具", "状态"])
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setMinimumHeight(min(400, 34 + 26 * max(3, len(hooks))))
        hh = tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for i, hk in enumerate(hooks):
            mark = {"trusted": "✔ 已信任", "new": "＋ 新增·未信任"}.get(hk.get("status"), "✎ 被改动·需重信任")
            when = "%s" % hk.get("event_label")
            if hk.get("matcher"):
                when += " · %s" % hk.get("matcher")
            a = QTableWidgetItem(when)
            a.setData(Qt.UserRole, hk.get("key"))
            tbl.setItem(i, 0, a)
            tbl.setItem(i, 1, QTableWidgetItem(elide(hk.get("command"), 120)))
            tbl.setItem(i, 2, QTableWidgetItem(mark))
        tbl.itemSelectionChanged.connect(self.on_trust_sel)
        bv.addWidget(tbl)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.btn_trust = QPushButton("信任 / 同意")
        self.btn_trust.setObjectName("primary")
        self.btn_trust.setEnabled(False)
        self.btn_trust.clicked.connect(self.do_trust)
        self.btn_revoke = QPushButton("撤销信任")
        self.btn_revoke.setEnabled(False)
        self.btn_revoke.clicked.connect(self.do_revoke)
        h.addWidget(self.btn_trust)
        h.addWidget(self.btn_revoke)
        self.lbl_trust = QLabel("选中一行再点按钮。")
        self.lbl_trust.setObjectName("muted")
        h.addSpacing(8)
        h.addWidget(self.lbl_trust, 1)
        bv.addWidget(row)
        line(bv, "信任写哪", "C:\\Users\\taich\\.codex\\config.toml → [hooks.state.*] trusted_hash", t, 120)
        line(bv, "为什么", "Codex 自己的信任哈希不覆盖脚本内容，所以这一页只由你点；AI 没有令牌。", t, 120)

    def on_trust_sel(self):
        tbl = self.sender()
        try:
            r = tbl.currentRow()
            it = tbl.item(r, 0)
            self.trust_key = it.data(Qt.UserRole) if it else None
        except Exception:
            self.trust_key = None
        ok = bool(self.trust_key)
        self.btn_trust.setEnabled(ok)
        self.btn_revoke.setEnabled(ok)
        self.lbl_trust.setText(("选中：%s" % elide(self.trust_key, 80)) if ok else "选中一行再点按钮。")

    def _hook(self):
        return next((x for x in (self.data.get("hooks") or []) if x.get("key") == self.trust_key), {})

    def do_trust(self):
        if not self.trust_key:
            return
        hk = self._hook()
        try:
            ui.set_trust(self.trust_key, hk.get("current_hash"))
            ui.audit("trust", self.trust_key, event_label=hk.get("event_label"),
                     trusted_hash=hk.get("current_hash"))
            self.lbl_trust.setText("已信任：%s" % elide(hk.get("command"), 70))
        except Exception as e:
            self.lbl_trust.setText("信任失败：%s" % e)
        self.refresh()

    def do_revoke(self):
        if not self.trust_key:
            return
        hk = self._hook()
        try:
            ui.revoke_trust(self.trust_key)
            ui.audit("revoke", self.trust_key, event_label=hk.get("event_label"))
            self.lbl_trust.setText("已撤销：%s" % elide(hk.get("command"), 70))
        except Exception as e:
            self.lbl_trust.setText("撤销失败：%s" % e)
        self.refresh()

    # -------------------------------------------------- 责令改正
    def pg_corr(self, v):
        t, d = self.t, self.data
        corr = d.get("corr") or []
        f, bv = card(v, "责令改正（%d）" % len(corr),
                     "这里是监督者要 AI 改正的东西。留着 → 每次都注入给 AI；划掉 → 不再责令。")
        if not corr:
            empty(bv, "没有未完成的整改要求。")
        for c in corr:
            box = QFrame()
            box.setStyleSheet("border-top:1px solid %s;" % t["line"])
            b = QVBoxLayout(box)
            b.setContentsMargins(0, 10, 0, 6)
            a = QLabel("第 %s 轮：%s" % (elide(c.get("turn") or "—", 12), elide(c.get("ask") or "（没记下问题）", 80)))
            a.setWordWrap(True)
            b.addWidget(a)
            for x in (c.get("violations") or [])[:3]:
                s = QLabel("· %s" % elide(x.get("what") or x.get("title") or str(x), 100))
                s.setObjectName("small")
                s.setWordWrap(True)
                b.addWidget(s)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addStretch(1)
            keep = QPushButton("继续责令")
            keep.setObjectName("primary")
            keep.clicked.connect(lambda _=False, dd=c: self.keep_corr(dd))
            dis = QPushButton("划掉，不再责令")
            dis.clicked.connect(lambda _=False, dd=c: self.dismiss_corr(dd))
            h.addWidget(keep)
            h.addWidget(dis)
            b.addWidget(row)
            bv.addWidget(box)

    def keep_corr(self, _d):
        try:
            ui.export_corrections()
        except Exception:
            pass
        self.refresh()

    def dismiss_corr(self, d):
        try:
            ui.dismiss_corrections(str(d.get("turn") or ""))
        except Exception:
            pass
        self.refresh()

    # -------------------------------------------------- 改监督者的申请
    def pg_changes(self, v):
        t, d = self.t, self.data
        chs = d.get("changes") or []
        f, bv = card(v, "改监督者的申请（%d）" % len(chs),
                     "AI 和脚本只能提案。你点「同意并应用」之后，监督者自己动手、先备份到 changes\\<id>\\backup\\。")
        if not chs:
            empty(bv, "没有等你决定的提案。")
        for c in chs:
            box = QFrame()
            box.setStyleSheet("border-top:1px solid %s;" % t["line"])
            b = QVBoxLayout(box)
            b.setContentsMargins(0, 10, 0, 6)
            a = QLabel(elide(c.get("title") or c.get("id"), 100))
            b.addWidget(a)
            line(b, "提案人", str(c.get("by") or "—"), t, 60)
            line(b, "为什么", str(c.get("why") or "—"), t, 100)
            line(b, "要动", "、".join(str(x) for x in (c.get("files") or [])), t, 80)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addStretch(1)
            ok = QPushButton("同意并应用")
            ok.setObjectName("primary")
            ok.clicked.connect(lambda _=False, dd=c: self.apply_change(dd))
            no = QPushButton("拒绝")
            no.clicked.connect(lambda _=False, dd=c: self.reject_change(dd))
            h.addWidget(ok)
            h.addWidget(no)
            b.addWidget(row)
            bv.addWidget(box)

    def apply_change(self, d):
        rid = str(d.get("id") or "")
        try:
            ok, info = ui.apply_change(rid, by="你（界面）")
            self.say("已应用 %s：%s" % (rid, info))
        except Exception as e:
            self.say("应用失败：%s" % e)

    def reject_change(self, d):
        rid = str(d.get("id") or "")
        why, ok = QInputDialog.getText(self, "拒绝这条提案",
                                       "为什么拒绝？（会喂给大脑，让它别再提同类；可留空）")
        if not ok:
            return
        try:
            ui.reject_change(rid, why or "")
            self.say("已拒绝 %s（理由已记下）" % rid)
        except Exception as e:
            self.say("拒绝失败：%s" % e)

    # -------------------------------------------------- 告警与审计
    def pg_logs(self, v):
        t, d = self.t, self.data
        f, bv = card(v, "告警", "只记异常：等级变化、内部监督者停摆、启用监管者。")
        alerts = d.get("alerts") or []
        if not alerts:
            empty(bv, "没有告警。")
        for a in reversed(alerts[-8:]):
            line(bv, str(a.get("ts"))[11:19], "%s　%s" % (ALERT_ZH.get(a.get("kind"), a.get("kind")),
                                                          elide(a.get("detail"), 80)), t, 110)

        f, bv = card(v, "审计", "每一次信任 / 撤销 / 放行 / 应用都落在这里，append-only。")
        audit = d.get("audit") or []
        if not audit:
            empty(bv, "还没有审计记录。")
        for a in reversed(audit[-12:]):
            line(bv, str(a.get("ts"))[11:19], "%s　%s" % (AUDIT_ZH.get(a.get("action"), a.get("action")),
                                                          elide(a.get("key") or a.get("files") or "", 70)), t, 110)

        f, bv = card(v, "判断记录", "每轮由判断器核对：做了没有 / 按标准做了没有 / 是不是敷衍。")
        judged = d.get("judged") or []
        if not judged:
            empty(bv, "还没有判断记录。")
        for j in reversed(judged[-6:]):
            vv = j.get("violations") or []
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(chip("NORMAL" if not vv else "WATCH", t))
            h.addWidget(QLabel(str(j.get("ts"))[11:19]))
            lab = QLabel("问：%s　·　%s" % (elide(j.get("ask"), 40),
                                           ("%d 条违规" % len(vv)) if vv else "没发现违规"))
            lab.setWordWrap(True)
            h.addWidget(lab, 1)
            bv.addWidget(row)

    # -------------------------------------------------- 怎么看
    def pg_help(self, v):
        t, d = self.t, self.data
        f, bv = card(v, "三十秒看懂这一页")
        for lvl, what in (("NORMAL", "不需要你做什么"), ("WATCH", "盯着就行"),
                          ("DEGRADED", "先收尾；要人在 Codex 之外 resume"),
                          ("BLOCKED", "已被拦下；要人在 Codex 之外 resume")):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(chip(lvl, t))
            lab = QLabel(what)
            lab.setWordWrap(True)
            h.addWidget(lab, 1)
            bv.addWidget(row)
        line(bv, "颜色之外", "状态一律 形状 + 颜色 + 文字 三样都不同（色盲也能分辨）。", t, 110)

        f, bv = card(v, "界面每一块是什么")
        for a, b in (
                ("顶栏", "现在什么等级 + 要你做什么 + 正在盯哪个对话"),
                ("左栏", "六个分页；括号里的数字是等你处理的条数"),
                ("总览", "两个监督者的判定、记录大小、待办、大脑发现、最近动静"),
                ("小工具信任", "Codex 的 hooks 清单：信任 / 撤销。只有这里点了才会执行"),
                ("责令改正", "要 AI 改正的东西：继续责令 或 划掉"),
                ("改监督者的申请", "AI 的提案：同意并应用（先备份）或拒绝（写理由喂大脑）"),
                ("告警与审计", "异常、你的每次决定、每轮判断记录"),
                ("指令框", "说人话让它去查；查证结果回到总览"),
        ):
            line(bv, a, b, t, 110)

        prot = d.get("prot") or {}
        f, bv = card(v, "本程序自己受不受保护")
        line(bv, "沙箱", "sandbox_mode = %s" % prot.get("sandbox_mode", "—"), t, 60)
        line(bv, "结论", "✅ 已受保护" if prot.get("protected") else "⚠️ 还没受保护", t, 60)
        line(bv, "快捷键", "F5 刷新　·　Ctrl+Tab 切页　·　Esc 收进后台", t, 80)
        line(bv, "本机网页版", "http://127.0.0.1:8765/（同一份数据的备用视图）", t, 70,
             link="http://127.0.0.1:8765/")

    # -------------------------------------------------- 指令框
    def goto(self, page):
        for i, (k, _n) in enumerate(self.PAGES):
            if k == page:
                self.nav.setCurrentRow(i)
                return

    def say(self, msg):
        self.lbl_need.setText(msg)

    def run_cmd(self):
        q = (self.cmd.text() or "").strip()
        if not q:
            return
        self.cmd.clear()
        if q in ("刷新", "refresh"):
            self.refresh()
            return
        low = q.lower()
        if ("盯哪个" in q) or ("在盯谁" in q) or ("window" in low):
            w = safe(ui.running_window, {})
            self.notes.insert(0, dict(title="现在在盯哪个窗口",
                                      body="%s\n会话：%s\n目录：%s" % (w.get("title"), w.get("thread_id"), w.get("cwd"))))
            self.goto("overview")
            self.rebuild()
            return
        if q.startswith("应用提案"):
            self.say("去「改监督者的申请」页点「同意并应用」更安全。")
            return
        self.btn_go.setEnabled(False)
        self.btn_go.setText("查证中…")
        self.notes.insert(0, dict(title="监督者正在联网查证：" + elide(q, 40), body=q))
        self.goto("overview")
        self.rebuild()

        class _R(QThread):
            done = Signal(object)

            def __init__(self, q):
                super().__init__()
                self.q = q

            def run(self):
                try:
                    import research
                    self.done.emit(research.research(self.q, fetch_n=2))
                except Exception as e:
                    self.done.emit({"ok": False, "error": str(e)})

        self.worker = _R(q)
        self.worker.done.connect(self.on_research)
        self.worker.start()

    def on_research(self, r):
        self.btn_go.setEnabled(True)
        self.btn_go.setText("执行")
        title = self.notes[0].get("title") if self.notes else "联网查证"
        if not r or not r.get("ok"):
            self.notes.insert(0, dict(title="没查成", body=str((r or {}).get("error") or "未知原因")))
        else:
            self.notes.insert(0, dict(title=title, body=r.get("answer") or "（没答出来）",
                                      sources=r.get("sources") or [], gaps=r.get("gaps") or []))
        self.rebuild()

    # -------------------------------------------------- 收尾
    def closeEvent(self, e):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            STOPPED.write_text("user closed at %s" % time.ctime(), encoding="utf-8")
            save_settings(geometry="%dx%d+%d+%d" % (self.width(), self.height(), self.x(), self.y()))
        except Exception:
            pass
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("监督者")
    start_backend()
    w = Main()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
