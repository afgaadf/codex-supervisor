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
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from string import Template

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from gui_data import (ALERT_ZH, APP_DIR, AUDIT_ZH, GLOSSARY, GLOSSARY_LONG,  # noqa: E402
                      SIZE_HIST)
from paths import CODEX_HOME, DATA_DIR, VAULT_DIR   # noqa: E402

LOG_DIR = DATA_DIR / "logs"
HEARTBEAT = LOG_DIR / "app.heartbeat"     # 看门狗靠它判断"窗口还在"
STOPPED = LOG_DIR / "app.stopped"         # 你主动退出 → 看门狗不再拉起
SETTINGS = DATA_DIR / "settings.json"
ICON = APP_DIR / "home.ico"          # 电脑管家图标（盾+勾）

from PySide6.QtCore import QPointF, Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPen,
                           QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QHBoxLayout,
                               QGridLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import supervisor_ui as ui
from gui_util import (RANK, elide, fmt_bytes, hhmmss, read_json, safe, tail_jsonl,
                      worse, _alive, _set_text, _set_enabled, fresh, _load_pc_state)
from version import __version__
from gui_pages import PagesMixin   # Stage A1 拆出的页面方法
from gui_widgets import (LEVELS, chip, tag, card, line, empty, kpi, four_verdict,
                          ToolTile, Sparkline, page_header)   # Stage 2b 拆出的小部件


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

CSS_T = Template("""
QWidget { color: $text; font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 10pt; }
QWidget#root { background: $win; }
QFrame#card { background: $card; border: 1px solid $line; border-radius: 8px; }
QFrame#hdr { background: $card; border-bottom: 1px solid $line; }
QFrame#bar { background: $card; border-top: 1px solid $line; }
QLabel#h1 { font-size: 20pt; font-weight: 600; }
QLabel#h2 { font-size: 13pt; font-weight: 600; }
QLabel#h1 { font-size: 18pt; font-weight: 600; }
QLabel#sub { color: $muted; font-size: 10pt; }
QFrame#hdrcard { background: $card; border: 1px solid $line; border-left: 3px solid $accent; border-radius: 10px; }
QLabel#kpi { font-size: 20pt; font-weight: 600; }
QLabel#kpiLab { font-size: 9pt; color: $muted; }
QLabel#small { font-size: 9pt; color: $faint; }
QLabel#muted { color: $muted; }
QListWidget#nav { background: $card; border: 1px solid $line; border-radius: 10px; outline: none; padding: 8px 6px; }
QListWidget#nav::item { padding: 8px 10px; border-radius: 6px; margin: 1px 2px; }
QListWidget#nav::item:hover { background: $hover; }
QListWidget#nav::item:selected { background: $sel; font-weight: 600; }
QListWidget#nav::item:disabled { color: $faint; font-size: 9pt; padding: 10px 10px 4px 10px; }
QPushButton { background: $card; border: 1px solid $line; border-radius: 6px; padding: 6px 12px; min-height: 18px; }
QPushButton:hover { background: $hover; }
QPushButton:disabled { color: $faint; }
QPushButton#primary { background: $accent; color: #ffffff; border: 1px solid $accent; font-weight: 600; }
QPushButton#primary:hover { background: $accent2; }
QPushButton#primary:disabled { background: $line; color: $faint; border-color: $line; }
QPushButton#link { border: none; background: transparent; color: $accent; text-align: left; padding: 2px 0; }
QLineEdit#cmd { border: 1px solid $line; border-radius: 6px; padding: 8px 10px; background: $card; }
QTableWidget { background: $card; border: 1px solid $line; border-radius: 8px; gridline-color: $line; alternate-background-color: $hover; }
QTableWidget::item { padding: 4px 6px; }
QHeaderView::section { background: $win; border: none; border-bottom: 1px solid $line; padding: 7px; color: $muted; }
QTableWidget::item:selected { background: $sel; color: $text; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: $line; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
""")


def css(t):
    return CSS_T.substitute(t)


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


def _pc_loop():
    """电脑管家采样：每 5 分钟记一点（CPU/内存/C盘），越线写告警。"""
    import pc_guard
    while True:
        try:
            pc_guard.sample()
            pc_guard.check_alerts()
        except Exception as e:
            _log("pc 采样失败：%s" % e)
        time.sleep(300)


def _vault_ops_loop():
    """主动运维：每 30 分钟自己跑一轮（安全动作自己做，歧义的才留给人）。"""
    while True:
        try:
            import importlib
            import vault_ops as VO
            importlib.reload(VO)          # 用最新逻辑
            r = VO.auto_ops()
            _log("自动运维：健康分=%s 做了=%s 需要人=%s" % (r.get("health"), r.get("did"), r.get("need_human")))
        except Exception as e:
            _log("自动运维失败：%s" % e)
        time.sleep(30 * 60)


GUI_PID = LOG_DIR / "gui.pid"


def _other_gui_alive():
    """单实例：已经有窗口在跑就不再开第二个。"""
    import ctypes
    try:
        pid = int(GUI_PID.read_text(encoding="utf-8").strip())
    except Exception:
        return 0
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return pid
    except Exception:
        pass
    return 0


def _butler_alive():
    """后台管家在不在：读心跳（30 秒一次）+ 探本机 API。"""
    try:
        hb = LOG_DIR / "butler.heartbeat"
        if hb.exists() and (time.time() - hb.stat().st_mtime) < 120:
            return True
    except Exception:
        pass
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:8765/api/state", timeout=2).read(64)
        return True
    except Exception:
        return False


def start_backend():
    """监管引擎：监控循环 / 大脑 / 判定循环 / 本机 API。

    旧 Tk 版（backup/supervisor_app.py.retired-20261006 的 start_backend）就干这四件事；
    PySide6 版重写时漏了这一段，窗口就变成一个只读展示板：不监控、不判定、不监听 8765。
    """
    try:
        ui.audit("app_start", "-", host=ui.HOST, port=ui.PORT)
        ui.write_plugin_token()
    except Exception:
        pass
    try:
        ui.export_corrections()
    except Exception:
        _log("export_corrections 失败\n" + traceback.format_exc())
    # 如果独立后台管家在跑，窗口不重复起引擎（避免双份监控/双份 API）
    if _butler_alive():
        _log("检测到后台管家在跑，窗口只做看板")
        return
    _log("后台管家没在跑，窗口临时兼起引擎（建议双击桌面「电脑管家-后台」）")
    threading.Thread(target=_guard("monitor_loop", ui.monitor_loop), daemon=True).start()
    threading.Thread(target=_guard("brain", _brain_loop), daemon=True).start()
    threading.Thread(target=_guard("judge_loop", ui.judge_loop), daemon=True).start()
    threading.Thread(target=_guard("api", _serve_api), daemon=True).start()
    threading.Thread(target=_guard("pc_loop", _pc_loop), daemon=True).start()
    threading.Thread(target=_guard("vault_ops", _vault_ops_loop), daemon=True).start()


# ============================================================ 主窗口
class Main(PagesMixin, QMainWindow):
    PAGES = (("home", "工具箱"), ("overview", "总览"), ("codex", "Codex 监管"), ("vault", "Obsidian 库"),
             ("classes", "知识库分类"), ("rules", "硬性规则"), ("trust", "小工具信任"),
             ("corr", "责令改正"), ("changes", "改监督者的申请"), ("logs", "告警与审计"),
             ("learn", "学习与成长"), ("pc", "本机环境"), ("help", "怎么看"))

    def __init__(self):
        super().__init__()
        self.setWindowTitle("管家 v%s" % __version__)
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
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
        ttl = QLabel("管家")
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
        self.nav.setFixedWidth(196)
        self.nav_rows = {}
        groups = {"home": "管家", "overview": "核心监管", "classes": "知识库", "rules": "规则", "trust": "信任与整改",
                  "learn": "自我提升", "pc": "本机", "help": "帮助"}
        for key, name in self.PAGES:
            if key in groups:
                g = QListWidgetItem(groups[key])
                g.setFlags(Qt.NoItemFlags)
                self.nav.addItem(g)
            it = QListWidgetItem("  " + name)
            it.setData(Qt.UserRole, key)
            self.nav.addItem(it)
            self.nav_rows[key] = self.nav.count() - 1
        self.nav.setCurrentRow(self.nav_rows["home"])
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
        self.cmd.setPlaceholderText("说人话：查资料 / 刷新 / 看它在盯谁")
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
            brain=read_json(DATA_DIR / "brain.json", {}),
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
        for k, name in self.PAGES:
            row = self.nav_rows.get(k)
            if row is None:
                continue
            c = counts.get(k, 0)
            self.nav.item(row).setText("  " + name + (("  (%d)" % c) if c else ""))
        try:
            HEARTBEAT.write_text(str(int(time.time())), encoding="utf-8")
        except Exception:
            pass
        self.rebuild()

    def current_key(self):
        it = self.nav.currentItem()
        return it.data(Qt.UserRole) if it else "overview"

    def next_page(self):
        self.nav.setCurrentRow(self._next_row(1))

    def rebuild(self):
        while self.pv.count():
            it = self.pv.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        key = self.current_key()
        builder = {"home": self.pg_home, "overview": self.pg_overview, "codex": self.pg_codex, "vault": self.pg_vault,
                   "classes": self.pg_classes, "rules": self.pg_rules,
                   "learn": self.pg_learn, "pc": self.pg_pc,
                   "trust": self.pg_trust, "corr": self.pg_corr, "changes": self.pg_changes,
                   "logs": self.pg_logs, "help": self.pg_help}.get(key)
        if builder:
            try:
                builder(self.pv)
            except Exception:
                _log("页面 %s 渲染失败\n%s" % (key, traceback.format_exc()))
                card(self.pv, "页面渲染失败", traceback.format_exc()[-400:])
        self.pv.addStretch(1)

    # -------------------------------------------------- 工具箱首页

    # -------------------------------------------------- 小工具信任

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

    # -------------------------------------------------- Codex 监管

    def _ad_thresholds(self):
        try:
            import json as _j
            r = _j.loads((CODEX_HOME / "anti-degradation" / "rules" / "rules.json").read_text(encoding="utf-8"))
            th = r.get("thresholds") or {}
            lv = r.get("levels") or {}
            return "返工≥%s 计分 · 降智线 %s · 上下文警戒 %s 字" % (
                th.get("rework_degraded", "—"), lv.get("degraded", "—"), th.get("context_chars_watch", "—"))
        except Exception as e:
            return "读不到 rules.json：%s" % e

    def _inject_24h(self):
        try:
            import datetime as _dt
            p2 = CODEX_HOME / "anti-degradation" / "logs" / "hook.log"
            lines = p2.read_text(encoding="utf-8", errors="replace").splitlines()[-4000:]
            cut = _dt.datetime.now() - _dt.timedelta(hours=24)
            n = 0
            for ln in lines:
                if "INJECT" not in ln:
                    continue
                try:
                    ts = _dt.datetime.fromisoformat(ln[:19])
                    if ts >= cut:
                        n += 1
                except Exception:
                    pass
            return n
        except Exception:
            return 0

    def _level_changes(self):
        try:
            return len([a for a in tail_jsonl(DATA_DIR / "alerts.jsonl", 500)
                        if a.get("kind") == "level_change"])
        except Exception:
            return 0

    def codex_judge_now(self):
        def _run():
            import supervisor_ui as _ui
            return _ui.judge_latest_turn() or {"ok": True}
        self.pc_bg(_run, "判定中…", lambda r: self.pc_note("已判一次：%s" % str(r)[:160]))

    # -------------------------------------------------- Obsidian 库
    def _vault_state(self):
        idx = read_json(DATA_DIR / "vault_index.json", {}) or {}
        chk = read_json(DATA_DIR / "vault_checks.json", {}) or {}
        evs = tail_jsonl(DATA_DIR / "vault_events.jsonl", 60)
        return idx, chk, evs

    def _vault_level(self, chk):
        s = chk.get("summary") or {}
        try:
            broken = int(s.get("broken_links") or 0)
            miss = int(s.get("missing_frontmatter") or 0)
            stale = int(s.get("inbox_stale") or 0)
            unc = int(s.get("uncommitted") or 0)
        except Exception:
            return "WATCH"
        if broken >= 50 or miss >= 1500 or stale >= 10 or unc >= 300:
            return "DEGRADED"
        if broken or miss or stale or unc:
            return "WATCH"
        return "NORMAL"

    def vault_local_scan(self):
        """兜底扫描：直接读库文件；拿不到双链。"""
        import os as _os
        root = VAULT_DIR
        items = []
        miss = stale = big = n = 0
        now = time.time()
        for dirpath, dirnames, filenames in _os.walk(root):
            if ".obsidian" in dirpath or ".git" in dirpath:
                continue
            for fn in filenames:
                if not fn.endswith(".md"):
                    continue
                n += 1
                fp = Path(dirpath) / fn
                rel = str(fp.relative_to(root)).replace("\\", "/")
                try:
                    st = fp.stat()
                    head = fp.read_text(encoding="utf-8-sig", errors="replace")[:400]
                except Exception:
                    continue
                if not head.lstrip().startswith("---"):
                    miss += 1
                    if len(items) < 80:
                        items.append({"kind": "missing_frontmatter", "path": rel, "detail": "缺 frontmatter"})
                if rel.startswith("00_Inbox/") and now - st.st_mtime > 7 * 86400:
                    stale += 1
                    items.append({"kind": "inbox_stale", "path": rel, "detail": "%d 天没动" % ((now - st.st_mtime) // 86400)})
                if st.st_size > 200 * 1024:
                    big += 1
        unc = 0
        try:
            import subprocess as _sp
            out = _sp.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=20).stdout
            unc = len([x for x in out.splitlines() if x.strip()])
        except Exception:
            pass
        return {"summary": {"notes": n, "broken_links": 0, "missing_frontmatter": miss,
                            "inbox_stale": stale, "big_note": big, "uncommitted": unc},
                "items": items, "local": True}

    def vault_ops_run(self):
        def _run():
            VO = fresh("vault_ops")
            return VO.auto_ops()
        self.pc_bg(_run, "运维中…", lambda r: self.pc_note("它自己跑完了：%s｜需要你：%s"
                                                          % ("；".join(r.get("did") or []) or "无需动手",
                                                             "；".join(r.get("need_human") or []) or "不用你管")))

    def vault_ops_rollback(self):
        def _run():
            VO = fresh("vault_ops")
            return VO.rollback()
        self.pc_bg(_run, "回滚中…", lambda r: self.pc_note("回滚结果：%s" % str(r)[:180]))

    def vault_priority_report(self):
        def _run():
            import vault_priority as VP
            return VP.report_to_vault()
        self.pc_bg(_run, "导出中…", lambda r: self.pc_note("报告已写入：%s" % r.get("path")))


    def vault_details(self):
        """细节（优先级/明细/事件流）收进弹窗，平时不占地方。"""
        import json as _json
        from PySide6.QtWidgets import QDialog, QTextBrowser
        VO = fresh("vault_ops")
        last = VO.last_auto() or {}
        try:
            pri = __import__("vault_priority").compute()
        except Exception as e:
            pri = {"ok": False, "error": str(e), "groups": {}, "counts": {}}
        idx, chk, evs = self._vault_state()
        L = []
        L.append("健康分 %s /100　上次自动 %s" % (VO.health_score(), last.get("ts") or "—"))
        L.append("它自己做了：%s" % ("；".join(last.get("did") or []) or "—"))
        L.append("需要你：%s" % ("；".join(last.get("need_human") or []) or "无"))
        L.append("")
        L.append("【优先级】" + "　".join("P%s %s" % (k, v) for k, v in (pri.get("counts") or {}).items()))
        for lv in ("P0", "P1"):
            for x in (pri.get("groups", {}).get(lv) or [])[:8]:
                L.append("　%s %s —— %s" % (lv, x.get("path"), x.get("why")))
        L.append("")
        L.append("【体检明细】共 %d 条" % len(chk.get("items") or []))
        for it in (chk.get("items") or [])[:20]:
            L.append("　[%s] %s —— %s" % (it.get("kind"), it.get("path"), it.get("detail")))
        L.append("")
        L.append("【最近事件】共 %d 条" % len(evs))
        for e in (evs or [])[-10:]:
            L.append("　%s %s %s" % (str(e.get("ts"))[11:19], e.get("type") or e.get("command"), e.get("path")))
        dlg = QDialog(self)
        dlg.setWindowTitle("Obsidian 库 · 详情")
        dlg.resize(900, 640)
        lay = QVBoxLayout(dlg)
        tb = QTextBrowser()
        tb.setPlainText("\n".join(L))
        lay.addWidget(tb)
        b = QPushButton("关闭")
        b.clicked.connect(dlg.accept)
        lay.addWidget(b)
        dlg.exec()


    # -------------------------------------------------- 硬性规则
    def rules_toggle(self, rid, enabled):
        try:
            import supervisor_ui as ui
            ui.toggle_hard_rule(rid, enabled)
            ui.export_corrections()
            self.pc_note("规则 %s 已%s（下一轮生效）" % (rid, "启用" if enabled else "停用"))
        except Exception as e:
            self.pc_note("切换失败：%s" % e)
        self.rebuild()


    # -------------------------------------------------- 学习与成长
    def _learn_scan(self):
        import learn as L
        return L.learn_scan()

    def learn_decide(self, cid, adopt):
        try:
            import learn as L
            rows = L.load_candidates()
            hit = next((x for x in rows if x.get("id") == cid), None)
            if hit and adopt:
                L.add_lesson("adopted", hit.get("title"), hit.get("why"), source="human",
                             extra={"kind_src": hit.get("kind"), "action": hit.get("action")})
            L.save_candidates([x for x in rows if x.get("id") != cid])
            self.pc_note("已%s：%s" % ("采纳" if adopt else "丢弃", (hit or {}).get("title", cid)))
        except Exception as e:
            self.pc_note("处理失败：%s" % e)
        self.rebuild()


    # -------------------------------------------------- 电脑管家
    def pc_bg(self, fn, label, after=None):
        """后台跑（体检 7 秒左右，别卡界面）。"""
        class _J(QThread):
            done = Signal(object)

            def run(self):
                try:
                    self.done.emit(fn())
                except Exception as e:
                    self.done.emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

        _set_text(getattr(self, "btn_pc_busy", None), label)
        _set_enabled(getattr(self, "btn_pc_busy", None), False)
        job = _J()
        jobs = getattr(self, "_bg_jobs", None)
        if jobs is None:
            jobs = self._bg_jobs = []
        jobs.append(job)                 # 持引用：线程还在跑时不能被回收
        job.done.connect(lambda r: self.pc_done(r, after))
        job.finished.connect(job.deleteLater)
        job.finished.connect(lambda: jobs.remove(job) if job in jobs else None)
        job.start()

    def pc_done(self, r, after=None):
        _set_enabled(getattr(self, "btn_pc_busy", None), True)
        _set_text(getattr(self, "btn_pc_busy", None), "一键体检")
        if after:
            after(r)
        self.refresh()

    def pc_note(self, msg):
        _set_text(getattr(self, "lbl_pc_note", None), str(msg)[:300])
        _set_text(getattr(self, "lbl_need", None), str(msg)[:150])

    def pc_kill(self, pid, name):
        txt, ok = QInputDialog.getText(self, "结束进程（要二次确认）",
                                       "确认结束 %s (pid=%s)？输入该 pid 再按确定：" % (name, pid))
        if not ok or str(txt).strip() != str(pid):
            self.pc_note("已取消：pid 没对上，不动手。")
            return
        self.pc_bg(lambda: self._pc_act("kill_process", pid=int(pid), name=name), "处理中…",
                   lambda r: self.pc_note("结果：" + json.dumps(r, ensure_ascii=False)[:200]))

    def _pc_act(self, action, dry_run=True, **kw):
        import pc_guard
        return pc_guard.act(action, dry_run=dry_run, **kw)

    def _pc_scan(self):
        import pc_guard
        return pc_guard.scan()


    def _pc_junk_scan(self):
        import pc_guard
        return pc_guard.junk_scan()

    def _pc_temps(self):
        import pc_guard
        return pc_guard.temps()

    def pc_junk_clean(self, key, name, dry_run):
        if dry_run:
            self.pc_bg(lambda: self._pc_act("junk_clean", dry_run=True, target=key), "预览中…",
                       lambda r: self.pc_note("预览 %s：%s" % (name, json.dumps(r, ensure_ascii=False)[:260])))
            return
        txt, ok = QInputDialog.getText(self, "清理（要二次确认）",
                                       "将清理「%s」。输入 清理 两个字再按确定：" % name)
        if not ok or str(txt).strip() != "清理":
            self.pc_note("已取消：没输入「清理」。")
            return
        self.pc_bg(lambda: self._pc_act("junk_clean", dry_run=False, target=key), "清理中…",
                   lambda r: self.pc_note("清理 %s：%s" % (name, json.dumps(r, ensure_ascii=False)[:260])))

    def pc_folder_disable(self, path, name):
        txt, ok = QInputDialog.getText(self, "禁用启动文件夹项（要二次确认）",
                                       "将把「%s」移到备份目录（可恢复）。输入 禁用 两个字再按确定：" % name)
        if not ok or str(txt).strip() != "禁用":
            self.pc_note("已取消：没输入「禁用」。")
            return
        self.pc_bg(lambda: self._pc_act("startup_folder_disable", dry_run=False, path=path), "处理中…",
                   lambda r: self.pc_note("启动文件夹禁用：" + json.dumps(r, ensure_ascii=False)[:240]))

    def pc_folder_restore(self, name):
        txt, ok = QInputDialog.getText(self, "移回启动文件夹（要二次确认）",
                                       "把「%s」移回启动文件夹。输入 恢复 两个字再按确定：" % name)
        if not ok or str(txt).strip() != "恢复":
            self.pc_note("已取消：没输入「恢复」。")
            return
        self.pc_bg(lambda: self._pc_act("startup_folder_restore", dry_run=False, path=name), "处理中…",
                   lambda r: self.pc_note("启动文件夹恢复：" + json.dumps(r, ensure_ascii=False)[:240]))

    def pc_startup(self, name, where, command):
        txt, ok = QInputDialog.getText(self, "禁用开机自启（要二次确认）",
                                       "将备份原值后禁用「%s」。输入 禁用 两个字再按确定：" % name)
        if not ok or str(txt).strip() != "禁用":
            self.pc_note("已取消：没输入「禁用」。")
            return
        self.pc_bg(lambda: self._pc_act("startup_disable", dry_run=False, name=name, where=where, command=command),
                   "处理中…", lambda r: self.pc_note("禁用结果：" + json.dumps(r, ensure_ascii=False)[:240]))

    def pc_startup_restore(self, name, where):
        txt, ok = QInputDialog.getText(self, "恢复开机自启（要二次确认）",
                                       "把「%s」按备份写回 %s。输入 恢复 两个字再按确定：" % (name, where))
        if not ok or str(txt).strip() != "恢复":
            self.pc_note("已取消：没输入「恢复」。")
            return
        self.pc_bg(lambda: self._pc_act("startup_restore", dry_run=False, name=name, where=where),
                   "处理中…", lambda r: self.pc_note("恢复结果：" + json.dumps(r, ensure_ascii=False)[:240]))

    def pc_clear_temp(self):
        txt, ok = QInputDialog.getText(self, "清理临时文件（要二次确认）",
                                       "只会删 %%TEMP%% 下 7 天前的文件。输入 清理 两个字再按确定：")
        if not ok or str(txt).strip() != "清理":
            self.pc_note("已取消：没输入「清理」。")
            return
        self.pc_bg(lambda: self._pc_act("clear_temp", dry_run=False), "清理中…",
                   lambda r: self.pc_note("清理结果：" + json.dumps(r, ensure_ascii=False)[:200]))

    # -------------------------------------------------- 怎么看

    # -------------------------------------------------- 指令框
    def goto(self, page):
        row = self.nav_rows.get(page)
        if row is not None:
            self.nav.setCurrentRow(row)

    def _next_row(self, step=1):
        rows = sorted(self.nav_rows.values())
        cur = self.nav.currentRow()
        nxt = [r for r in rows if r > cur]
        prv = [r for r in rows if r < cur]
        return (nxt[0] if nxt else rows[0]) if step > 0 else (prv[-1] if prv else rows[-1])

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

        worker = _R(q)
        self.worker = worker
        workers = getattr(self, "_research_jobs", None)
        if workers is None:
            workers = self._research_jobs = []
        workers.append(worker)           # 持引用：线程还在跑时不能被回收
        worker.done.connect(self.on_research)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: workers.remove(worker) if worker in workers else None)
        worker.start()

    def on_research(self, r):
        _set_enabled(getattr(self, "btn_go", None), True)
        _set_text(getattr(self, "btn_go", None), "执行")
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
    other = _other_gui_alive()
    if other:
        return 0                      # 已有窗口在跑，直接退出（防重复弹窗）
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        GUI_PID.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("管家")
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    start_backend()
    w = Main()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
