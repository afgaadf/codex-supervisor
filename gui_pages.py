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


    def pg_home(self, v):
        """首页 = 单列清单：一行一件事（名称 · 现状 · 一个按钮），从上往下扫完就知道要干嘛。"""
        t = self.t
        d = self.data
        sup = d.get("sup") or {}
        try:
            VO = fresh("vault_ops")
            m = VO.metrics()
            hs = VO.health_score(m)
            last = VO.last_auto() or {}
        except Exception:
            m, hs, last = {}, 0, {}
        js = [j for j in tail_jsonl(APP_DIR / "judgments.jsonl", 40)
              if j.get("action") == "judged" and not j.get("selftest")]
        lastj = next((j for j in reversed(js) if j.get("checks")), {})
        ck = lastj.get("checks") or {}
        def four(kind):
            c = ck.get(kind)
            if not c:
                return "—"
            bad, verdict, _ = four_verdict(c, kind)
            if verdict == "不适用":
                return "—"
            return "有问题" if bad else "正常"
        try:
            L = fresh("learn")
            lm = L.metrics()
        except Exception:
            lm = {}
        try:
            pri = __import__("vault_priority").compute()
            pc0 = (pri.get("counts") or {}).get("P0", 0)
        except Exception:
            pc0 = "—"
        pcs = (_load_pc_state() or {}).get("summary") or {}
        pend = len(d.get("need_hooks") or []) + len(d.get("corr") or []) + len(d.get("changes") or [])
        need = (last.get("need_human") or [])

        page_header(v, "管家", "后台自己在跑；这一页是清单，一行一件事，从上往下看。", t,
                    [("刷新", self.refresh)])

        # 一句话：现在需要你做什么
        if need or pend:
            txt = "；".join(need) if need else ""
            if pend:
                txt = (txt + "；" if txt else "") + "%d 条等你处理" % pend
            msg = "⚠ 需要你：" + txt
        else:
            msg = "✅ 现在不用你管，管家自己在跑"
        lab = QLabel(msg)
        lab.setStyleSheet("font-weight:600;color:%s;" % (t["df"] if (need or pend) else t["okf"]))
        lab.setWordWrap(True)
        v.addWidget(lab)

        def row(name, status, page, action="打开"):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(2, 6, 2, 6)
            a = QLabel(name)
            a.setFixedWidth(150)
            a.setStyleSheet("font-weight:600;")
            h.addWidget(a)
            b = QLabel(str(status))
            b.setObjectName("muted")
            b.setWordWrap(True)
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            h.addWidget(b, 1)
            btn = QPushButton(action)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, p_=page: self.goto(p_))
            h.addWidget(btn)
            return w

        def group(title, rows):
            f, bv = card(v, title)
            for i, item in enumerate(rows):
                n, st, pg = item[0], item[1], item[2]
                act = item[3] if len(item) > 3 else "打开"
                if i:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.HLine)
                    sep.setStyleSheet("color:%s;" % t["line"])
                    bv.addWidget(sep)
                bv.addWidget(row(n, st, pg, act))

        group("监管 Codex", [
            ("Codex 监管", "%s · 分数 %s · 最近判定 %s" % (
                sup.get("level") or "—", sup.get("score", "—"),
                (str(lastj.get("ts"))[11:19] if lastj else "还没判过")), "codex"),
            ("四问检测（最近一轮）", "联网 %s · 查库 %s · 偷懒 %s · 降智 %s" % (
                four("online"), four("vault"), four("lazy"), four("degrade")), "codex"),
            ("硬性规则", "%d 条启用（含 H10 每轮必联网）" % len(fresh("supervisor_ui").load_hard_rules()), "rules"),
            ("小工具信任", "%d 条待处理" % len(d.get("need_hooks") or []), "trust",
             "去处理" if d.get("need_hooks") else "打开"),
            ("责令改正", "%d 条未完成" % len(d.get("corr") or []), "corr",
             "去处理" if d.get("corr") else "打开"),
            ("改监督者的申请", "%d 条待批" % len(d.get("changes") or []), "changes",
             "去批" if d.get("changes") else "打开"),
            ("告警与审计", "%d 条审计记录" % len(tail_jsonl(APP_DIR / "audit.jsonl", 5000)), "logs"),
        ])
        group("知识库（自动运维）", [
            ("Obsidian 库", "健康分 %s /100 · 断链 %s · 缺 fm %s · 收件箱 %s · 未提交 %s" % (
                hs, m.get("broken_links", "—"), m.get("missing_frontmatter", "—"),
                m.get("inbox_stale", "—"), m.get("uncommitted", "—")), "vault"),
            ("知识库分类", "未分类 %s 篇 · 上次分类 %s" % (m.get("unclassified", "—"), "见详情"), "classes"),
            ("优先级", "P0 %s 条（先看这些）" % pc0, "vault"),
        ])
        group("本机", [
            ("本机环境", "CPU %s%% · 内存 %s%% · C盘剩余 %s GB" % (
                pcs.get("cpu_pct", "—"), pcs.get("mem_pct", "—"),
                next((x.get("free_gb") for x in (pcs.get("disks") or []) if x.get("id") == "C:"), "—")), "pc"),
            ("开机自启 / 服务", "%s 项自启 · 见本机环境" % pcs.get("startup", "—"), "pc"),
        ])
        group("自我提升", [
            ("学习与成长", "%s 条教训 · %s 条候选 · 误报率 %s%%" % (
                lm.get("lessons", 0), lm.get("candidates_pending", 0), lm.get("false_alarm_rate", 0)), "learn"),
            ("怎么看（术语表）", "%d 个词条" % len(GLOSSARY), "help"),
        ])
        f, bv = card(v, "管家自己在干什么")
        line(bv, "上次自动", (last.get("ts") or "还没跑过")[:19], t, 80)
        line(bv, "它自己做了", "；".join(last.get("did") or []) or "—", t, 110)


    def pg_overview(self, v):
        page_header(v, "总览", "① Codex　② Obsidian　③ 本机（辅助）",
                    self.t, [("刷新", self.refresh)])
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
                                                    hhmmss(hist[-1].get("ts")))
            except Exception:
                delta = ""
        # ---- 两个核心（这才是这个软件在干的事）
        idx, chk, vevs = self._vault_state()
        vsum = chk.get("summary") or {}
        vlvl = self._vault_level(chk)
        rowc = QHBoxLayout()
        rowc.setSpacing(12)
        f1, b1 = card(v, "① 监管 Codex 会话")
        r1 = QWidget()
        h1 = QHBoxLayout(r1)
        h1.setContentsMargins(0, 0, 0, 0)
        h1.addWidget(chip(d.get("sup_level") or "NORMAL", t))
        h1.addWidget(QLabel("分数 %s" % (sup.get("score", "—"))))
        h1.addStretch(1)
        b1.addWidget(r1)
        line(b1, "正在盯", elide((d.get("win") or {}).get("title"), 46) or "—", t, 70)
        line(b1, "原因", "；".join(sup.get("reasons") or []) or "没有扣分项", t, 70)
        c2 = sup.get("counters") or {}
        try:
            _jj = [j for j in tail_jsonl(APP_DIR / "judgments.jsonl", 30) if j.get("action") == "judged"]
            _lastj = _jj[-1] if _jj else {}
        except Exception:
            _lastj = {}
        line(b1, "最近判定", ("%s · %s" % (hhmmss(_lastj.get("ts")),
                                        ("%d 条违规" % len(_lastj.get("violations") or [])) if _lastj.get("violations") else "没发现违规"))
             if _lastj else "还没判过", t, 70)
        line(b1, "这一轮", "%s 轮 · 命令 %s 次 · 工具错 %s 次" % (
            c2.get("turns", "—"), (mon.get("events") or {}).get("command", "—"),
            (mon.get("events") or {}).get("tool_error", "—")), t, 70)
        line(b1, "等你处理", "未信任小工具 %d · 责令 %d · 提案 %d" % (
            len(d.get("need_hooks") or []), len(d.get("corr") or []), len(d.get("changes") or [])), t, 70)
        f2, b2 = card(v, "② 监管 Obsidian 库")
        r2 = QWidget()
        h2 = QHBoxLayout(r2)
        h2.setContentsMargins(0, 0, 0, 0)
        h2.addWidget(chip(vlvl, t))
        h2.addWidget(QLabel("笔记 %s 篇" % (idx.get("count", "—"))))
        h2.addStretch(1)
        go = QPushButton("去处理")
        go.setObjectName("link")
        go.clicked.connect(lambda: self.goto("vault"))
        h2.addWidget(go)
        b2.addWidget(r2)
        line(b2, "库体检", "断链 %s · 缺 frontmatter %s · 收件箱堆积 %s · 未提交 %s" % (
            vsum.get("broken_links", "—"), vsum.get("missing_frontmatter", "—"),
            vsum.get("inbox_stale", "—"), vsum.get("uncommitted", "—")), t, 70)
        line(b2, "最近改动", (vevs[-1].get("ts") or "—")[:19] if vevs else "还没收到插件事件", t, 70)
        line(b2, "上次索引", (idx.get("ts") or "还没同步（去 Obsidian 启用插件）")[:19], t, 70)
        line(b2, "今天进收件箱", str(sum(1 for e in vevs if e.get("type") == "create" and str(e.get("path") or "").startswith("00_Inbox/"))), t, 70)

        row0 = QWidget()
        h0 = QHBoxLayout(row0)
        h0.setContentsMargins(0, 0, 0, 0)
        h0.setSpacing(12)
        w1 = QWidget()
        l1 = QVBoxLayout(w1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.addWidget(f1)
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 0, 0, 0)
        l2.addWidget(f2)
        h0.addWidget(w1, 1)
        h0.addWidget(w2, 1)
        v.addWidget(row0)

        # ---- 四个必答（这就是它存在的理由）
        _js = [j for j in tail_jsonl(APP_DIR / "judgments.jsonl", 40) if j.get("action") == "judged"]
        _last = next((j for j in reversed(_js) if j.get("checks")), {})
        _ck = _last.get("checks") or {}
        f, bv = card(v, "四个必答（最近一轮）",
                     "它每轮就问这四件事，答不出证据就不下结论")
        _vs = _last.get("violations") or []
        _rules = " ".join(str(v.get("rule") or "") for v in _vs)
        _onl = dict(_ck.get("online") or {})
        if "did" not in _onl and "ai_searched" in _onl:
            _onl["did"] = _onl.get("ai_searched")
        rows4 = [
            ("① 联网了吗", _onl, "needed", "did", None, None),
            ("② 查库了吗", _ck.get("vault") or {}, "needed", "did", None, None),
            ("③ 偷懒了吗", _ck.get("lazy") or {}, "claimed", "lazy", None, None),
            ("④ 降智了吗", _ck.get("degrade") or {}, None, "risky", None, None),
        ]
        if not _ck:
            empty(bv, "还没有带四问的判定记录，点「Codex 监管 → 立刻判一次」")
        for name, c, kn, dn, _a, _b in rows4:
            kind = {"①": "online", "②": "vault", "③": "lazy", "④": "degrade"}[name[:1]]
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            if not c:
                h.addWidget(chip("WATCH", t))
                h.addWidget(QLabel("%s：这轮没记录" % name))
            else:
                bad, verdict, ev = four_verdict(c, kind)
                h.addWidget(chip("BLOCKED" if bad else "NORMAL", t))
                label = {"online": "联网了吗", "vault": "查库了吗", "lazy": "偷懒了吗", "degrade": "降智了吗"}[kind]
                txt = "%s：%s" % (label, verdict)
                if kind == "lazy":
                    txt = "%s：%s" % (label, "有" if bad else "没有")
                elif kind in ("online", "vault"):
                    txt = "%s：%s" % (label, "查了" if not bad else "没查")
                elif bad:
                    txt = "%s：有迹象" % label
                else:
                    txt = "%s：没迹象" % label
                a = QLabel(txt)
                a.setWordWrap(True)
                h.addWidget(a, 1)
                ev2 = QLabel(elide(ev, 52))
                ev2.setObjectName("small")
                ev2.setMinimumWidth(0)
                ev2.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                h.addWidget(ev2, 1)
            bv.addWidget(row)

        # 本机环境（辅助，不是核心）
        try:
            import pc_guard
            _pc = read_json(APP_DIR / "pc_state.json", {}) or {}
            _pcs = _pc.get("summary") or {}
            _pcl = _pc.get("level") or "—"
        except Exception:
            _pcs, _pcl = {}, "—"
        f0, b0 = card(v, "③ 本机环境（辅助）",
                      "系统资源只影响①②能不能好好干活，所以放在这里，不占主位。")
        line(b0, "概况", "CPU %s%% · 内存 %s%% · C盘剩余 %s GB · 管家分 %s（%s）" % (
            _pcs.get("cpu_pct", "—"), _pcs.get("mem_pct", "—"),
            next((x.get("free_gb") for x in (_pcs.get("disks") or []) if x.get("id") == "C:"), "—"),
            _pc.get("score", "—"), _pcl), t, 90)
        go2 = QPushButton("打开本机环境")
        go2.setObjectName("link")
        go2.clicked.connect(lambda: self.goto("pc"))
        b0.addWidget(go2)

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

        pair1 = QWidget()
        ph1 = QHBoxLayout(pair1)
        ph1.setContentsMargins(0, 0, 0, 0)
        ph1.setSpacing(12)
        colA = QWidget()
        ca = QVBoxLayout(colA)
        ca.setContentsMargins(0, 0, 0, 0)
        colB = QWidget()
        cb = QVBoxLayout(colB)
        cb.setContentsMargins(0, 0, 0, 0)
        ph1.addWidget(colA, 1)
        ph1.addWidget(colB, 1)
        v.addWidget(pair1)
        f, bv = card(ca, "判定来源：两个监督者，按更坏的那个算",
                     "左＝本程序算的，右＝Codex 内部算的，取更坏")
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
                                                hhmmss(mon.get("window_start"))), t, 90)
        line(bv, "内部分数", "score=%s　原因：%s" % (sup.get("score", "—"),
                                              "；".join(sup.get("reasons") or []) or "—"), t, 110)
        line(bv, "内部节拍", "%s（%s 分钟前）%s" % (hhmmss((sup.get("counters") or {}).get("last_turn_end_ts")),
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
        f, bv = card(cb, "等你处理（%d）" % len(pend_items))
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
        pair2 = QWidget()
        ph2 = QHBoxLayout(pair2)
        ph2.setContentsMargins(0, 0, 0, 0)
        ph2.setSpacing(12)
        colC = QWidget()
        cc = QVBoxLayout(colC)
        cc.setContentsMargins(0, 0, 0, 0)
        colD = QWidget()
        cd = QVBoxLayout(colD)
        cd.setContentsMargins(0, 0, 0, 0)
        ph2.addWidget(colC, 1)
        ph2.addWidget(colD, 1)
        v.addWidget(pair2)
        f, bv = card(cc, "监督者大脑：最近发现",
                     "它只能改自己的信号清单，别的要走提案")
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
        f, bv = card(cd, "最近动静")
        if not evs:
            empty(bv, "还没有记录。")
        for ts, what, detail in evs[:6]:
            line(bv, hhmmss(ts), "%s　%s" % (what, elide(detail, 70)), t, 110)


    def pg_codex(self, v):
        t = self.t
        d = self.data
        sup = d.get("sup") or {}
        mon = d.get("mon") or {}
        c2 = sup.get("counters") or {}
        try:
            import learn as L
            _m = L.metrics()
        except Exception:
            _m = {}
        judged = tail_jsonl(APP_DIR / "judgments.jsonl", 40)
        judged = [j for j in judged if j.get("action") == "judged"]
        last = judged[-1] if judged else {}
        corr = d.get("corr") or []
        page_header(v, "Codex 监管", "它每 30 秒判一轮；判定历史、注入内容、干预次数都在这页。", t,
                    [("立刻判一次", lambda: self.codex_judge_now())])

        k = QHBoxLayout()
        k.setSpacing(12)
        kpi(k, "当前等级", "%s %s" % (LEVELS.get(d.get("level") or "WATCH", ("·", "—"))[0],
                                    LEVELS.get(d.get("level") or "WATCH", ("", "—"))[1]),
            "分数 %s" % sup.get("score", "—"), t, LEVELS.get(d.get("level") or "WATCH", ("", "", "w"))[2])
        kpi(k, "最近判定", hhmmss(last.get("ts")) if last else "—",
            ("%d 条违规" % len(last.get("violations") or [])) if last.get("violations") else "没发现违规", t)
        kpi(k, "判定轮数", str(_m.get("judged_turns", len(judged))), "累计判过多少轮", t)
        kpi(k, "正在注入", "%d 条" % (1 + len(corr)),
            "常驻规矩 1 条 + 责令改正 %d 条，每条消息都带着走" % len(corr), t)
        wrap = QWidget()
        wrap.setLayout(k)
        v.addWidget(wrap)

        f, bv = card(v, "判定历史（最近 %d 轮）" % min(len(judged), 12),
                     "时间 · 结论 · 是否联网核对 · 判据")
        if not judged:
            empty(bv, "还没有判定记录。点右上角「立刻判一次」。")
        for j in reversed(judged[-12:]):
            vv = j.get("violations") or []
            onl = j.get("online") or {}
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(chip("WATCH" if vv else "NORMAL", t))
            h.addWidget(QLabel(hhmmss(j.get("ts"))))
            who = QLabel("问：%s" % elide(j.get("ask"), 34))
            who.setWordWrap(True)
            h.addWidget(who, 1)
            h.addWidget(QLabel("违规 %d" % len(vv)))
            tagt = "已联网核 %s" % (onl.get("verdict") or "") if onl.get("needed") else "无需联网"
            lab = QLabel(elide(tagt, 26))
            lab.setObjectName("small")
            h.addWidget(lab)
            bv.addWidget(row)
            for x in vv[:2]:
                line(bv, "　", "%s ｜ %s" % (elide(x.get("rule"), 42), elide(x.get("fix"), 44)), t, 110)

        f, bv = card(v, "逐轮四问（联网 / 查库 / 偷懒 / 降智）",
                     "✅ 没问题 · ⚠ 有问题 · 空=这轮没记录")
        t4 = QTableWidget(min(len(judged), 12), 5)
        t4.setHorizontalHeaderLabels(["时间", "① 联网", "② 查库", "③ 偷懒", "④ 降智"])
        t4.verticalHeader().setVisible(False)
        t4.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t4.setAlternatingRowColors(True)
        t4.setMinimumHeight(min(340, 40 + 24 * max(3, min(len(judged), 12))))
        t4.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for i, j in enumerate(reversed(judged[-12:])):
            ck = j.get("checks") or {}
            t4.setItem(i, 0, QTableWidgetItem(hhmmss(j.get("ts"))))
            def mark(c, kind):
                if not c:
                    return "—"
                bad, verdict, _ev = four_verdict(c, kind)
                if verdict == "不适用":
                    return "—"
                return "⚠" if bad else "✅"
            for k, name in enumerate(("online", "vault", "lazy", "degrade")):
                it = QTableWidgetItem(mark(ck.get(name), name))
                it.setTextAlignment(Qt.AlignCenter)
                t4.setItem(i, k + 1, it)
        bv.addWidget(t4)

        f, bv = card(v, "这一轮会注入什么（你在每条消息里能看到）",
                     "下面这段就是它塞给 AI 的内容；你能看到 [监督者·常驻规矩] 说明这条链是通的。")
        line(bv, "常驻规矩", "D1 联网核实（涉及规格/版本/API/标准/官方要求/数值，先联网查再写）", t, 110)
        line(bv, "责令改正", "未完成 %d 条%s" % (len(corr), ("：" + elide((corr[0] or {}).get("rule"), 40)) if corr else ""), t, 110)
        line(bv, "注入方式", "UserPromptSubmit hook → additionalContext（每轮一次）", t, 110)

        f, bv = card(v, "为什么是这个等级")
        line(bv, "原因", "；".join(sup.get("reasons") or []) or "没有扣分项", t, 110)
        line(bv, "计数", "轮数 %s · 返工 %s · 上下文 %s 字 · 压缩 %s 次" % (
            c2.get("turns", "—"), c2.get("rework", "—"), c2.get("context_chars", "—"), c2.get("compactions", "—")), t, 110)
        line(bv, "阈值", self._ad_thresholds(), t, 110)
        line(bv, "内部节拍", "%s（内部 supervisor 上次动的时间）" % hhmmss((sup.get("counters") or {}).get("last_turn_end_ts")), t, 110)

        f, bv = card(v, "干预统计（它到底动过几次手）")
        line(bv, "24h 注入", "%d 次（hook 日志 INJECT 计数）" % self._inject_24h(), t, 110)
        line(bv, "等级变化", "%d 次（alerts 里的 level_change）" % self._level_changes(), t, 110)
        line(bv, "小工具信任", "%d 条 handler，%d 条待处理" % (len(d.get("hooks") or []), len(d.get("need_hooks") or [])), t, 110)
        line(bv, "审计条数", "%d 条（audit.jsonl）" % len(tail_jsonl(APP_DIR / "audit.jsonl", 5000)), t, 110)


    def pg_vault(self, v):
        """一屏：它自己干了什么 + 需要你什么；细节收进「详情」。"""
        t = self.t
        VO = fresh("vault_ops")
        last = VO.last_auto() or {}
        m = VO.metrics()
        hs = VO.health_score(m)
        page_header(v, "Obsidian 库 · 自动运维中",
                    "它每 30 分钟自己跑一轮；下面的都是它自己想出来的结论。", t)
        f, bv = card(v, "健康分 %s / 100" % hs,
                     "断链 · 缺 frontmatter · 收件箱 · 未提交 · 未分类 —— 五项越低越好")
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        for lab, val in (("断链", m.get("broken_links")), ("缺 fm", m.get("missing_frontmatter")),
                         ("收件箱", m.get("inbox_stale")), ("未提交", m.get("uncommitted")),
                         ("未分类", m.get("unclassified"))):
            lab2 = QLabel("%s %s" % (lab, val))
            lab2.setStyleSheet("font-weight:600;color:%s;" % (t["okf"] if not val else t["df"]))
            h.addWidget(lab2)
            h.addSpacing(12)
        h.addStretch(1)
        b1 = QPushButton("让它现在跑一轮")
        b1.setObjectName("primary")
        b1.clicked.connect(self.vault_ops_run)
        h.addWidget(b1)
        b2 = QPushButton("详情")
        b2.clicked.connect(self.vault_details)
        h.addWidget(b2)
        bv.addWidget(row)

        line(bv, "上次自动", (str(last.get("ts") or "")[11:19] or "还没跑过") +
             ("　健康分 %s" % last.get("health") if last.get("health") is not None else ""), t, 80)
        did = (last.get("did") or [])[:3]
        line(bv, "它自己做了", "；".join(did) if did else "（本轮没有需要动手的）", t, 110)
        need = (last.get("need_human") or [])[:2]
        line(bv, "需要你", "；".join(need) if need else "不用你管", t, 110)


    def pg_pc(self, v):
        page_header(v, "本机环境（辅助）", "只作辅助，别喧宾夺主", self.t)
        t = self.t
        try:
            import pc_guard  # noqa: F401
            have = True
        except Exception as e:
            have = False
            card(v, "电脑管家不可用", "导入 pc_guard 失败：%s" % e)
        st = read_json(APP_DIR / "pc_state.json", {}) or {}
        sm = st.get("summary") or {}
        lvl = st.get("level") or "WATCH"
        items = st.get("items") or []

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.btn_pc_busy = QPushButton("一键体检")
        self.btn_pc_busy.setObjectName("primary")
        self.btn_pc_busy.setEnabled(bool(have))
        self.btn_pc_busy.clicked.connect(lambda: self.pc_bg(self._pc_scan, "体检中…"))
        h.addWidget(self.btn_pc_busy)
        b1 = QPushButton("打开任务管理器")
        b1.clicked.connect(lambda: self.pc_bg(lambda: self._pc_act("open_task_manager", dry_run=False), "打开中…"))
        h.addWidget(b1)
        b2 = QPushButton("预览可清理的临时文件")
        b2.clicked.connect(lambda: self.pc_bg(lambda: self._pc_act("clear_temp", dry_run=True), "扫描临时文件…",
                                              lambda r: self.pc_note("预览：" + json.dumps(r, ensure_ascii=False)[:260])))
        h.addWidget(b2)
        b3 = QPushButton("清理 7 天前的临时文件")
        b3.clicked.connect(self.pc_clear_temp)
        h.addWidget(b3)
        h.addStretch(1)
        v.addWidget(row)

        k = QHBoxLayout()
        k.setSpacing(12)
        kpi(k, "管家评分", "%s %s" % (LEVELS.get(lvl, ("·", "—"))[0], "%s / 100" % (st.get("score", "—"))),
            "体检时间：%s" % (str(st.get("ts") or "—")[11:19] or "还没体检"), t,
            LEVELS.get(lvl, ("", "", "w"))[2])
        kpi(k, "CPU", "%s%%" % (sm.get("cpu_pct", "—")), "瞬时占用（CIM LoadPercentage）", t)
        kpi(k, "内存", "%s%%" % (sm.get("mem_pct", "—")),
            "已用 %s GB / 共 %s GB" % (sm.get("mem_used_gb", "—"), sm.get("mem_total_gb", "—")), t,
            "w" if float(sm.get("mem_pct") or 0) >= 85 else None)
        disks = sm.get("disks") or []
        d0 = next((x for x in disks if x.get("id") == "C:"), disks[0] if disks else {})
        pct = 0.0
        try:
            pct = float(d0.get("free_gb") or 0) / max(0.1, float(d0.get("total_gb") or 1)) * 100
        except Exception:
            pass
        kpi(k, "C 盘剩余", "%s GB" % (d0.get("free_gb", "—")),
            "占 %.1f%%（低于 10%% 就该清）" % pct, t, "b" if pct < 10 else ("w" if pct < 20 else None))
        wrap = QWidget()
        wrap.setLayout(k)
        v.addWidget(wrap)

        try:
            import pc_guard
            pts = pc_guard.history(60)
            als = pc_guard.alerts(12)
            bks = pc_guard.startup_backups()
        except Exception:
            pts, als, bks = [], [], []
        f, bv = card(v, "走势（最近 %d 个采样点 · 每 5 分钟一个）" % len(pts),
                     "每 5 分钟一个点，越线才记告警")
        sp = Sparkline(pts, t)
        bv.addWidget(sp)

        f, bv = card(v, "告警历史（%d）" % len(als), "只在刚越过阈值时记一条，不会刷屏。")
        if not als:
            empty(bv, "还没有越过阈值的记录。")
        for a in reversed(als[-8:]):
            row = QWidget()
            hh = QHBoxLayout(row)
            hh.setContentsMargins(0, 0, 0, 0)
            hh.addWidget(chip("BLOCKED" if a.get("sev") == "要紧" else "WATCH", t))
            hh.addWidget(QLabel(str(a.get("ts") or "")[11:19]))
            lab = QLabel(str(a.get("detail") or ""))
            lab.setWordWrap(True)
            hh.addWidget(lab, 1)
            bv.addWidget(row)

        f, bv = card(v, "体检结果（%d 项）" % len(items),
                     "阈值：内存88 · 盘10 · 自启15；动作全审计")
        if not items:
            empty(bv, "没问题（先点一键体检）")
        for it in items[:8]:
            sev = {"要紧": "BLOCKED", "注意": "WATCH", "还好": "NORMAL"}.get(it.get("sev"), "WATCH")
            r = QWidget()
            hh = QHBoxLayout(r)
            hh.setContentsMargins(0, 0, 0, 0)
            hh.addWidget(chip(sev, t))
            box = QVBoxLayout()
            box.setSpacing(2)
            a = QLabel(str(it.get("title") or ""))
            a.setWordWrap(True)
            box.addWidget(a)
            b = QLabel("证据：%s" % elide(it.get("evidence"), 90))
            b.setObjectName("small")
            b.setWordWrap(True)
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            box.addWidget(b)
            c = QLabel("怎么办：%s" % elide(it.get("advice"), 90))
            c.setObjectName("small")
            c.setWordWrap(True)
            c.setMinimumWidth(0)
            c.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            box.addWidget(c)
            hh.addLayout(box, 1)
            bv.addWidget(r)

        self.lbl_pc_note = QLabel("")
        self.lbl_pc_note.setObjectName("muted")
        self.lbl_pc_note.setWordWrap(True)
        f, bv = card(v, "动作与结果")
        bv.addWidget(self.lbl_pc_note)

        # ---- 垃圾清理（扫描要 8 秒左右，放后台）
        js = getattr(self, "pc_junk", None)
        f, bv = card(v, "垃圾清理（只清白名单：临时文件 / 浏览器缓存）",
                     "回收站不可用（本机实测）"
                     "下载目录只列不动")
        row = QWidget()
        hh = QHBoxLayout(row)
        hh.setContentsMargins(0, 0, 0, 0)
        sb = QPushButton("扫描垃圾")
        sb.setObjectName("primary")
        sb.clicked.connect(lambda: self.pc_bg(self._pc_junk_scan, "扫描中…",
                                              lambda r: setattr(self, "pc_junk", r)))
        hh.addWidget(sb)
        hh.addWidget(QLabel("可清理合计：%.1f MB" % (((js or {}).get("cleanable_bytes") or 0) / 1024 ** 2)))
        hh.addStretch(1)
        bv.addWidget(row)
        if not js:
            empty(bv, "点「扫描垃圾」看看能清多少。")
        else:
            t2 = QTableWidget(len(js.get("categories") or []), 4)
            t2.setHorizontalHeaderLabels(["类别", "大小", "文件", "操作"])
            t2.verticalHeader().setVisible(False)
            t2.setEditTriggers(QAbstractItemView.NoEditTriggers)
            t2.setMinimumHeight(min(300, 40 + 26 * max(3, len(js.get("categories") or []))))
            h4 = t2.horizontalHeader()
            h4.setSectionResizeMode(0, QHeaderView.Stretch)
            h4.setSectionResizeMode(3, QHeaderView.ResizeToContents)
            for i, c in enumerate(js.get("categories") or []):
                t2.setItem(i, 0, QTableWidgetItem(str(c.get("name"))))
                t2.setItem(i, 1, QTableWidgetItem("%.1f MB" % ((c.get("bytes") or 0) / 1024 ** 2)))
                t2.setItem(i, 2, QTableWidgetItem(str(c.get("files") if c.get("files") is not None else "—")))
                w2 = QWidget()
                hh2 = QHBoxLayout(w2)
                hh2.setContentsMargins(0, 0, 0, 0)
                if c.get("cleanable"):
                    for lab, dry in (("预览", True), ("清理", False)):
                        b = QPushButton(lab)
                        if not dry:
                            b.setObjectName("primary")
                        b.clicked.connect(lambda _=False, k=c.get("key"), d=dry, nm2=c.get("name"):
                                          self.pc_junk_clean(k, nm2, d))
                        hh2.addWidget(b)
                else:
                    lab = QLabel("只显示")
                    lab.setObjectName("small")
                    hh2.addWidget(lab)
                t2.setCellWidget(i, 3, w2)
            bv.addWidget(t2)
            for c in (js.get("categories") or []):
                if c.get("sample"):
                    line(bv, str(c.get("name"))[:10], "；".join(
                        "%s（%.0f MB）" % (str(s2.get("path") or "")[-36:], s2.get("mb") or 0)
                        for s2 in c["sample"][:4]), t, 120)

        # ---- 服务（用最近一次体检里的"自启但没运行"列表，不额外采集）
        svc = st.get("services_stopped") or []
        f, bv = card(v, "自启但没在跑的服务（%d）" % len(svc),
                     "需要管理员")
        t3 = QTableWidget(len(svc), 2)
        t3.setHorizontalHeaderLabels(["服务", "显示名"])
        t3.verticalHeader().setVisible(False)
        t3.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t3.setMinimumHeight(min(320, 40 + 26 * max(3, len(svc))))
        t3.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i, x in enumerate(svc):
            t3.setItem(i, 0, QTableWidgetItem(str(x.get("Name") or "")))
            t3.setItem(i, 1, QTableWidgetItem(elide(x.get("DisplayName"), 60)))
        bv.addWidget(t3)

        row = QWidget()
        hh = QHBoxLayout(row)
        hh.setContentsMargins(0, 0, 0, 0)
        tb = QPushButton("读一次温度（本机可能不支持）")
        tb.clicked.connect(lambda: self.pc_bg(self._pc_temps, "读温度…",
                                              lambda r: self.pc_note("温度：" + json.dumps(r, ensure_ascii=False)[:240])))
        hh.addWidget(tb)
        hh.addStretch(1)
        bv.addWidget(row)

        tbl = QTableWidget(len(st.get("top") or []), 4)
        tbl.setHorizontalHeaderLabels(["PID", "进程", "内存", "操作"])
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        tbl.setSortingEnabled(True)
        tbl.setMinimumHeight(min(360, 40 + 26 * max(3, len(st.get("top") or []))))
        hh2 = tbl.horizontalHeader()
        hh2.setSectionResizeMode(1, QHeaderView.Stretch)
        hh2.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        for i, pr in enumerate(st.get("top") or []):
            pid = str(pr.get("Id") or "")
            tbl.setItem(i, 0, QTableWidgetItem(pid))
            tbl.setItem(i, 1, QTableWidgetItem(str(pr.get("ProcessName") or "")))
            try:
                mem = float(pr.get("WorkingSet64") or 0) / 1024 ** 3
            except Exception:
                mem = 0.0
            tbl.setItem(i, 2, QTableWidgetItem("%.2f GB" % mem))
            btn = QPushButton("结束")
            btn.clicked.connect(lambda _=False, p_=pid, n_=str(pr.get("ProcessName") or ""): self.pc_kill(p_, n_))
            tbl.setCellWidget(i, 3, btn)
        f, bv = card(v, "内存占用 Top %d" % len(st.get("top") or []),
                     "只能结束这里列出的进程")
        bv.addWidget(tbl)

        su = st.get("startup") or []
        tbl2 = QTableWidget(len(su), 4)
        tbl2.setHorizontalHeaderLabels(["启动项", "位置", "命令", "操作"])
        tbl2.verticalHeader().setVisible(False)
        tbl2.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl2.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl2.setAlternatingRowColors(True)
        tbl2.setMinimumHeight(min(340, 40 + 26 * max(3, len(su))))
        hh3 = tbl2.horizontalHeader()
        hh3.setSectionResizeMode(2, QHeaderView.Stretch)
        hh3.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        for i, x in enumerate(su):
            nm = str(x.get("name") or "")
            wh = str(x.get("where") or "")
            tbl2.setItem(i, 0, QTableWidgetItem(nm))
            tbl2.setItem(i, 1, QTableWidgetItem(elide(wh, 40)))
            tbl2.setItem(i, 2, QTableWidgetItem(elide(x.get("command"), 80)))
            reg = wh.startswith("HK")
            is_folder = str(x.get("source") or "") == "startup_folder"
            btn = QPushButton("禁用" if (reg or is_folder) else "—")
            btn.setEnabled(reg or is_folder)
            if reg:
                btn.clicked.connect(lambda _=False, n=nm, w=wh, c=str(x.get("command") or ""): self.pc_startup(n, w, c))
            elif is_folder:
                btn.clicked.connect(lambda _=False, p=str(x.get("command") or ""), n=nm: self.pc_folder_disable(p, n))
            tbl2.setCellWidget(i, 3, btn)
        f, bv = card(v, "开机自启（%d）" % len(su),
                     "注册表按 Microsoft Learn；文件夹项本机实测。"
                     "禁用前先存原值；HKLM 要管理员。")
        bv.addWidget(tbl2)

        try:
            import pc_guard
            fbk = pc_guard.startup_folder_backups()
        except Exception:
            fbk = []
        f, bv = card(v, "启动文件夹项备份 %d 个" % len(fbk),
                     "移动备份，可移回")
        if not fbk:
            empty(bv, "启动文件夹里没有项被移走。")
        for b in fbk:
            row = QWidget()
            hh = QHBoxLayout(row)
            hh.setContentsMargins(0, 0, 0, 0)
            hh.addWidget(QLabel("%s（%.1f KB）" % (b.get("name"), b.get("mb") or 0)))
            hh.addStretch(1)
            rb = QPushButton("移回启动文件夹")
            rb.clicked.connect(lambda _=False, n=str(b.get("name") or ""): self.pc_folder_restore(n))
            hh.addWidget(rb)
            bv.addWidget(row)

        f, bv = card(v, "已禁用（可一键恢复）· 备份 %d 条" % len(bks),
                     "禁用前的原值，一键恢复")
        if not bks:
            empty(bv, "还没禁用过任何启动项。")
        for b in reversed(bks[-6:]):
            row = QWidget()
            hh = QHBoxLayout(row)
            hh.setContentsMargins(0, 0, 0, 0)
            lab = QLabel("%s　%s" % (b.get("name"), elide(b.get("command"), 60)))
            lab.setObjectName("small")
            lab.setWordWrap(True)
            hh.addWidget(lab, 1)
            rb = QPushButton("恢复")
            rb.clicked.connect(lambda _=False, n=str(b.get("name") or ""), w=str(b.get("where") or ""):
                               self.pc_startup_restore(n, w))
            hh.addWidget(rb)
            bv.addWidget(row)
