# -*- coding: utf-8 -*-
"""gui_pages.py —— 管家各页面的渲染方法（PagesMixin，Stage A1 拆出）。

这些方法原来都堆在 supervisor_gui.py 的 Main 类里（~2000 行）。按 Google《Small CLs》
分批搬到这里，由 Main 多重继承；方法体一字未改，只换了个文件。
依赖只指向 gui_util / gui_widgets / gui_data，不反向依赖 supervisor_gui（无循环）。
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from gui_data import ALERT_ZH, APP_DIR, AUDIT_ZH, GLOSSARY, GLOSSARY_LONG, SIZE_HIST
from gui_util import (_load_pc_state, elide, fmt_bytes, fresh, hhmmss, read_json, tail_jsonl)
from gui_widgets import (LEVELS, Sparkline, card, chip, empty, four_verdict, kpi, line,
                         page_header, tag)


class PagesMixin:
    """各页面的 pg_* 渲染方法。由 Main(PagesMixin, QMainWindow) 继承。"""


    def pg_classes(self, v):
        t = self.t
        try:
            VC = fresh("vault_classify")
            r = VC.compute()
        except Exception as e:
            page_header(v, "知识库分类", "分类引擎没起来：%s" % e, t)
            return
        page_header(v, "知识库分类", "电脑管家按规则自动分类；未分类的要归位。", t,
                    [("导出分类索引到库", lambda: self.pc_bg(lambda: __import__("vault_classify").report_to_vault(),
                                                              "导出中…", lambda x: self.pc_note("分类索引已写入：" + str(x.get("path")))))])
        if not r.get("ok"):
            card(v, "拿不到索引", str(r.get("error")))
            return
        k = QHBoxLayout()
        k.setSpacing(8)
        for c in r.get("classes", [])[:5]:
            kpi(k, c["cat"], str(c["count"]), c.get("suggest", "")[:24], t)
        wrap = QWidget()
        wrap.setLayout(k)
        v.addWidget(wrap)
        for c in r.get("classes", []):
            f, bv = card(v, "%s（%d 篇）" % (c["cat"], c["count"]), "建议放哪：%s" % c.get("suggest", ""))
            for it in c["items"][:6]:
                line(bv, it.get("size_kb") and ("%.0f KB" % it["size_kb"]) or "", "%s%s" % (
                    elide(it.get("path"), 60), "" if it.get("fm") else "  ⚠ 缺 frontmatter"), t, 96)
            if c["count"] > 6:
                line(bv, "…", "还有 %d 篇（导出后在库里的《分类索引》看全）" % (c["count"] - 6), t, 80)


    def pg_rules(self, v):
        t = self.t
        try:
            ui = fresh("supervisor_ui") if False else __import__("supervisor_ui")
            rows = ui.load_hard_rules(only_enabled=False)
        except Exception as e:
            page_header(v, "硬性规则", "规则库没读到：%s" % e, t)
            return
        on = sum(1 for r in rows if r.get("enabled"))
        page_header(v, "硬性规则", "电脑管家自己保存的 %d 条硬规则（启用 %d 条）；启用的每轮注入给 AI。" % (len(rows), on), t,
                    [("立刻同步注入", lambda: self.pc_bg(lambda: (__import__("supervisor_ui").export_corrections(), {"ok": True})[1],
                                                      "同步中…", lambda x: self.pc_note("注入已更新（%d 条启用）" % on)))])
        f, bv = card(v, "规则清单", "编号 · 名称 · 要求 · 出处 · 状态")
        tb = QTableWidget(len(rows), 4)
        tb.setHorizontalHeaderLabels(["编号/名称", "要求（硬性）", "出处", "开关"])
        tb.verticalHeader().setVisible(False)
        tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tb.setAlternatingRowColors(True)
        tb.setMinimumHeight(min(520, 44 + 52 * max(3, len(rows))))
        hh = tb.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for i, r in enumerate(rows):
            tb.setItem(i, 0, QTableWidgetItem("%s %s" % (r.get("id"), r.get("name"))))
            tb.setItem(i, 1, QTableWidgetItem(elide(r.get("requirement"), 90)))
            tb.setItem(i, 2, QTableWidgetItem(elide(r.get("source"), 34)))
            b = QPushButton("启用中" if r.get("enabled") else "已停用")
            if r.get("enabled"):
                b.setObjectName("primary")
            b.clicked.connect(lambda _=False, rid=str(r.get("id")), en=bool(r.get("enabled")): self.rules_toggle(rid, not en))
            tb.setCellWidget(i, 3, b)
        bv.addWidget(tb)
        line(bv, "存哪", "rules_hard.json（电脑管家目录，人可改；改完下一轮生效）", t, 90)


    def pg_help(self, v):
        page_header(v, "怎么看这个界面", "术语表 + 图例", self.t)
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
        line(bv, "颜色之外", "形状+颜色+文字，色盲也分得出", t, 110)

        f, bv = card(v, "界面每一块是什么")
        for a, b in (
                ("顶栏", "现在什么等级 + 要你做什么 + 正在盯哪个对话"),
                ("左栏", "六个分页；括号里的数字是等你处理的条数"),
                ("总览", "判定 · 待办 · 大脑发现"),
                ("小工具信任", "hooks 清单：点了才执行"),
                ("责令改正", "要 AI 改正的东西：继续责令 或 划掉"),
                ("改监督者的申请", "提案：同意（先备份）/ 拒绝"),
                ("告警与审计", "异常、你的每次决定、每轮判断记录"),
                ("指令框", "说人话让它去查；查证结果回到总览"),
        ):
            line(bv, a, b, t, 110)

        f, bv = card(v, "术语表（专业名 → 大白话）",
                     "全站名词解释，自动生成。")
        for name, plain in GLOSSARY.items():
            line(bv, name, "%s —— %s" % (plain, GLOSSARY_LONG.get(name, "")), t, 150)

        prot = d.get("prot") or {}
        f, bv = card(v, "本程序自己受不受保护")
        line(bv, "沙箱", "sandbox_mode = %s" % prot.get("sandbox_mode", "—"), t, 60)
        line(bv, "结论", "✅ 已受保护" if prot.get("protected") else "⚠️ 还没受保护", t, 60)
        line(bv, "快捷键", "F5 刷新　·　Ctrl+Tab 切页　·　Esc 收进后台", t, 80)
        line(bv, "本机网页版", "http://127.0.0.1:8765/（同一份数据的备用视图）", t, 70,
             link="http://127.0.0.1:8765/")


    def pg_trust(self, v):
        page_header(v, "小工具信任", "点了才执行；脚本改过就失效", self.t)
        t, d = self.t, self.data
        hooks = d.get("hooks") or []
        need = d.get("need_hooks") or []
        f, bv = card(v, "Codex 能自动运行的小工具",
                     "按 Codex 官方算法重算；点过才执行")
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
        tbl.setAlternatingRowColors(True)
        tbl.setSortingEnabled(True)
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
        line(bv, "为什么", "只有你能点，AI 没有令牌", t, 120)


    def pg_corr(self, v):
        page_header(v, "责令改正", "改完自动核销", self.t)
        t, d = self.t, self.data
        corr = d.get("corr") or []
        f, bv = card(v, "责令改正（%d）" % len(corr),
                     "留着会一直提醒 AI；划掉就不再提")
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


    def pg_changes(self, v):
        page_header(v, "改监督者的申请", "要你同意才动，先备份", self.t)
        t, d = self.t, self.data
        chs = d.get("changes") or []
        f, bv = card(v, "改监督者的申请（%d）" % len(chs),
                     "它自己动手前会先备份")
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


    def pg_logs(self, v):
        page_header(v, "告警与审计", "异常 + 你的决定", self.t, [("刷新", self.refresh)])
        t, d = self.t, self.data
        f, bv = card(v, "告警", "只记异常")
        alerts = d.get("alerts") or []
        if not alerts:
            empty(bv, "没有告警。")
        for a in reversed(alerts[-8:]):
            line(bv, hhmmss(a.get("ts")), "%s　%s" % (ALERT_ZH.get(a.get("kind"), a.get("kind")),
                                                          elide(a.get("detail"), 80)), t, 110)

        f, bv = card(v, "审计", "只能追加，不能改")
        audit = d.get("audit") or []
        if not audit:
            empty(bv, "还没有审计记录。")
        for a in reversed(audit[-12:]):
            line(bv, hhmmss(a.get("ts")), "%s　%s" % (AUDIT_ZH.get(a.get("action"), a.get("action")),
                                                          elide(a.get("key") or a.get("files") or "", 70)), t, 110)

        f, bv = card(v, "判断记录", "做了没有 · 按标准做没有 · 敷衍没有")
        judged = d.get("judged") or []
        if not judged:
            empty(bv, "还没有判断记录。")
        for j in reversed(judged[-6:]):
            vv = j.get("violations") or []
            onl = j.get("online") or {}
            box = QVBoxLayout()
            box.setSpacing(2)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(chip("NORMAL" if not vv else "WATCH", t))
            h.addWidget(QLabel(hhmmss(j.get("ts"))))
            lab = QLabel("问：%s　·　%s" % (elide(j.get("ask"), 40),
                                           ("%d 条违规" % len(vv)) if vv else "没发现违规"))
            lab.setWordWrap(True)
            h.addWidget(lab, 1)
            box.addWidget(row)
            if onl.get("needed"):
                c = onl.get("tier_counts") or {}
                txt = "联网核实：%s　·　T1×%d T2×%d T3×%d T4×%d" % (
                    onl.get("verdict") or ("已核查" if onl.get("ok") else "没查成"),
                    c.get("T1", 0), c.get("T2", 0), c.get("T3", 0), c.get("T4", 0))
                gaps = onl.get("gaps") or []
                if gaps:
                    txt += "　·　缺口 %d 条（%s）" % (len(gaps), elide(gaps[0], 40))
                l2 = QLabel(elide(txt, 150))
                l2.setObjectName("small")
                l2.setWordWrap(True)
                l2.setMinimumWidth(0)
                l2.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                box.addWidget(l2)
                for src in (onl.get("sources") or [])[:2]:
                    url = src.get("url") if isinstance(src, dict) else str(src)
                    if not url:
                        continue
                    b = QPushButton("%s ↗ %s" % ((src.get("tier_name") if isinstance(src, dict) else "来源") or "来源",
                                                 elide(url, 58)))
                    b.setObjectName("link")
                    b.setCursor(Qt.PointingHandCursor)
                    b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
                    box.addWidget(b)
            bv.addLayout(box)
