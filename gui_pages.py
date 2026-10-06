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
