# -*- coding: utf-8 -*-
"""brain.py —— 监督者自己的「大脑」。

它为什么存在
------------
在这之前，监督者只会**照着人写死的规则**报数（"工具调用 >= 25 次 = 可能绕圈"），
规则写得糙、误报多；而"怎么改规则"全得靠外面的人和 AI 来维护。
这个模块让监督者**自己**看、**自己**判、**自己**做能做的维护。

两条依据（在线核对过，见 设计依据.md）
--------------------------------------
1. IBM Autonomic Computing（2001）的 **MAPE-K**：
   Monitor（采集）→ Analyze（分析）→ Plan（决定）→ Execute（执行），
   共享一份 Knowledge。人不再直接操控系统，而是**只定策略**。
2. Reference Monitor（Anderson 1972）的 **NEAT**：执法机制必须
   *必须被调用 · 可验证（足够小）· 不可篡改*。

所以这里划一条硬线：
    大脑 = MAPE-K，能看、能判、能做**机械动作**；
    内核 = 判等级 / 硬阻断 / 信任写入 —— 大脑**碰不到**。
大脑**永远不能**改 rules.json（策略只能人批准）、不能给自己或别人发信任、
不能改自己的执法代码。它发现自己的毛病，只能**如实报出来**并留证据，
改不改由人决定。越过这条线，"监督者"就变成了"自己给自己发通行证"。

它现在会「想一轮」了（2026-10-06 加）
-------------------------------------
之前的大脑只会跑写死的统计式；缺口要靠外面的人/AI 来补。现在多了一个环节：

    Monitor → Analyze → **Think（走本机网关想一轮）** → Plan/Execute（只做机械动作）

想什么：**"我是不是漏了一个能从数据里看出来的信号？"**
想到之后**不是自己动手改代码**，而是——
    大脑写提案 → 人点同意 → 监督者代写 →（下次动脑时）自己回读确认。

权限边界（守卫写在代码里，不靠提示词自觉）：
  · 大脑只能碰一件东西：`signals.json` —— 一份**数据**（信号清单），不是代码；
  · 能在里面加的信号，必须落在固定的源/运算符/严重度白名单里（见下）；
  · 提案里出现 rules.json / 信任 / 策略 / 代码字样，一律**当场挡下**并记进 brain.json，
    连提交都不给提交（"守住内核"）。
  · 判等级、发信任、改硬阻断、改执法代码 —— 大脑**没有**任何通道。

用法
----
  python brain.py            # 想一轮 + 写结论到 brain.json，打印
  python brain.py --think    # 强制「想一轮」（忽略节流），用于调试
  python brain.py --loop     # 常驻（由 supervisor_app 起线程调用 run_once 即可）
"""
from __future__ import annotations
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from paths import CODEX_HOME, DATA_DIR, APP_DIR

LOG_DIR = DATA_DIR / "logs"
BRAIN_JSON = DATA_DIR / "brain.json"
BRAIN_LOG = LOG_DIR / "brain.jsonl"          # 大脑自己做过什么（审计用）
SIGNALS = DATA_DIR / "signals.json"           # 大脑**可以**提议扩充的信号清单（数据）
THINK_LOG = LOG_DIR / "brain_thinks.jsonl"   # 每次「想一轮」的结果（含被守卫挡下的）
THINK_TS = LOG_DIR / "brain_last_think"      # 上次动脑的时间（节流用）
AD = CODEX_HOME / "anti-degradation"

# 只读的 Knowledge 来源
K_RULES = AD / "rules" / "rules.json"
K_STATE = AD / "state" / "session.json"
K_EVENTS = AD / "state" / "events.jsonl"
K_JUDGE = DATA_DIR / "judgments.jsonl"
K_SIZE = LOG_DIR / "size_history.jsonl"
K_SCORE = LOG_DIR / "score_history.jsonl"
K_APP = LOG_DIR / "app.log"
K_ERR = LOG_DIR / "app.err.log"
K_WD = LOG_DIR / "watchdog.log"

SEV = {"要紧": "error", "注意": "warn", "还好": "ok"}

# ---------------------------------------------------------------- NEAT 守卫（硬线）
# 大脑能碰的**唯一**目标：信号清单。它是一份数据，不是代码。
ALLOWED_TARGETS = {"signals.json"}
# 信号可以用哪些数据源
ALLOWED_SOURCES = {"score", "size", "events", "judgments"}
# 信号可以用哪些判断（固定集合 —— 没有 eval，没有任意表达式）
ALLOWED_OPS = {"slope_gt", "slope_lt", "last_gt", "last_lt", "count_gt", "count_ge"}
ALLOWED_SEV = {"要紧", "注意", "还好"}
# 出现这些词 = 想碰内核 / 想跑代码 → 当场挡下
FORBIDDEN = ("rules.json", "trusted_hash", "sandbox", "config.toml", "levels",
             "thresholds", "break_glass", "resume", "set_trust", "resolve_request",
             "apply_change", "reject_change", "subprocess", "os.system", "eval(",
             "exec(", "__import__", "import os")


def research_rubric(question, rubric="2d-video.md", title=None):
    """联网查证 → 写成「判据补充提案」（人在界面同意后由监督者代写）。

    为什么不自己改：判据就是判定标准，算半个内核 —— 只能提案，人点头才落地。
    另：结论必须带出处（URL），没出处就不往判据里写。
    """
    import research as R
    r = R.research(question)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or "联网查证没成功"}
    lines = ["", "---", "", "## 联网补充：%s" % question, "",
             "> 大脑于 %s 联网查证（结论只依据检索到的材料）。"
             % datetime.now().strftime("%Y-%m-%d %H:%M"), "", str(r.get("answer") or "").strip(), ""]
    for pt in (r.get("points") or []):
        src = " ".join("[%d](%s)" % (x["id"], x["url"]) for x in (pt.get("sources") or [])) or "（未标来源）"
        lines.append("- %s  %s" % (pt.get("claim"), src))
    if r.get("gaps"):
        lines += ["", "**材料没覆盖**："] + ["- %s" % g for g in r["gaps"]]
    lines += ["", "**来源**："]
    for x in (r.get("sources") or []):
        lines.append("- [%d] %s — %s" % (x["id"], x["title"][:70], x["url"]))
    block = "\n".join(lines)

    f = RUBRIC_DIR / rubric
    cur = f.read_text(encoding="utf-8", errors="replace").rstrip()
    new = cur + "\n" + block + "\n"
    try:
        import supervisor_ui as ui
        rec = ui.add_change_request(
            by="大脑（联网查证）",
            why=str(r.get("answer") or "")[:400],
            title=title or ("给判据补一条：" + question[:30]),
            files=[{"path": "rubrics/" + rubric, "content": new}])
    except Exception as e:
        return {"ok": False, "error": "提案没发出去：%s" % e}
    return {"ok": True, "id": rec.get("id"), "rubric": rubric,
            "answer": r.get("answer"), "sources": r.get("sources"),
            "gaps": r.get("gaps")}


def _json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default if default is not None else {}


def _jsonl(path, n=2000):
    out = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    except Exception:
        return []
    return out[-n:]


def _series(path, n=200):
    vals = []
    for r in _jsonl(path, n):
        v = r.get("v", r.get("bytes"))
        if isinstance(v, (int, float)):
            vals.append((r.get("ts"), v))
    return vals


def _slope(pts):
    """每采样点的平均变化量（最小二乘太小题大做，用首尾差/段数）。"""
    if len(pts) < 2:
        return 0.0
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    return (v1 - v0) / float(len(pts) - 1)


# ------------------------------------------------------------------ Monitor
def observe():
    """把大脑要看的东西**一次性**收齐（只读）。"""
    return {
        "rules": _json(K_RULES, {}),
        "state": _json(K_STATE, {}),
        "events": _jsonl(K_EVENTS, 20000),
        "judgments": _jsonl(K_JUDGE, 500),
        "size": _series(K_SIZE, 300),
        "score": _series(K_SCORE, 300),
    }


# ------------------------------------------------------------------ 宣言式信号
def _load_signals():
    """读 signals.json 里的信号清单（大脑**只能提议**往里加，写入由监督者代劳）。"""
    d = _json(SIGNALS, {}) or {}
    sigs = d.get("signals")
    return sigs if isinstance(sigs, list) else []


def _eval_signal(sig, k):
    """按固定的源/运算符评估一条宣言式信号。不命中返回 None。"""
    if not isinstance(sig, dict):
        return None
    src = str(sig.get("source") or "")
    op = str(sig.get("op") or "")
    sev = str(sig.get("sev") or "注意")
    if src not in ALLOWED_SOURCES or op not in ALLOWED_OPS:
        return None
    try:
        val = float(sig.get("value"))
    except Exception:
        return None
    try:
        win = int(sig.get("window") or 6)
    except Exception:
        win = 6
    win = max(2, min(200, win))

    fmt = {}
    hit = False
    if src in ("score", "size"):
        pts = (k.get(src) or [])[-win:]
        if len(pts) < 2:
            return None
        (_, v0), (_, v1) = pts[0], pts[-1]
        sl = _slope(pts)
        if op == "slope_gt":
            hit = sl > val
        elif op == "slope_lt":
            hit = sl < val
        elif op == "last_gt":
            hit = v1 > val
        elif op == "last_lt":
            hit = v1 < val
        else:
            return None
        fmt = {"first": v0, "last": v1, "n": len(pts), "slope": round(sl, 2)}
    else:
        # count 类：在**最近 window 条原始记录**里数"符合条件的条数"
        rows = (k.get(src) or [])[-win:]
        kind = sig.get("kind")
        flt = sig.get("filter")
        if isinstance(flt, dict):
            kind = flt.get("kind") or kind
            flt = flt.get("only")
        if src == "events" and kind:
            rows = [r for r in rows if r.get("kind") == kind]
        if src == "judgments":
            if flt == "violations_empty":
                rows = [r for r in rows if not r.get("violations")]
            elif flt == "violations_present":
                rows = [r for r in rows if r.get("violations")]
        cnt = len(rows)
        if op == "count_gt":
            hit = cnt > val
        elif op == "count_ge":
            hit = cnt >= val
        else:
            return None
        fmt = {"count": cnt, "n": cnt, "window": win}
    if not hit:
        return None

    def _fill(s):
        s = str(s or "")
        for a, b in fmt.items():
            s = s.replace("{" + a + "}", str(b))
        return s

    return {"id": "signal:" + str(sig.get("id") or "?"),
            "sev": sev if sev in ALLOWED_SEV else "注意",
            "title": _fill(sig.get("title")) or "自定义信号命中",
            "evidence": _fill(sig.get("evidence")) or "（命中，但没有写证据模板）",
            "advice": _fill(sig.get("advice")) or "（没有写建议）"}


# ------------------------------------------------------------------ Analyze
def analyze(k):
    """从原始数据里得出**带证据**的结论。宁可少报，不许瞎报。"""
    out = []
    rules = k.get("rules") or {}
    levels = rules.get("levels") or {}
    th = rules.get("thresholds") or {}

    # ① 评分在爬 —— 这是最早能看出"在变差"的信号
    sc = k.get("score") or []
    if len(sc) >= 3:
        tail = sc[-6:]
        d = _slope(tail)
        cur = tail[-1][1]
        watch = levels.get("watch")
        if d > 0:
            sev = "注意" if (watch is None or cur < watch) else "要紧"
            out.append({
                "id": "score_rising", "sev": sev,
                "title": "评分在往上爬（每采样 +%.1f 分）" % d,
                "evidence": "评分从 %s 涨到 %s，共 %d 个采样点。"
                            % (tail[0][1], cur, len(tail)),
                "advice": "趁还没到观察线（%s）先收尾：把手上这件做完、别再开新任务。"
                          % watch if watch is not None else "先收尾，别再开新任务。"})
        elif d < 0:
            out.append({"id": "score_falling", "sev": "还好",
                        "title": "评分在回落（每采样 %.1f 分）" % d,
                        "evidence": "评分从 %s 降到 %s。" % (tail[0][1], cur),
                        "advice": "保持这样。"})

    # ② 记录体积的涨速
    sz = k.get("size") or []
    if len(sz) >= 3:
        tail = sz[-40:]
        (t0, v0), (t1, v1) = tail[0], tail[-1]
        try:
            mins = max(0.1, (datetime.fromisoformat(t1) - datetime.fromisoformat(t0))
                       .total_seconds() / 60.0)
            per_min = (v1 - v0) / mins
        except Exception:
            per_min = 0.0
        cw = th.get("context_chars_watch")
        if per_min > 0 and cw:
            left = (cw - v1) / per_min if per_min else 0
            if left < 60:
                out.append({"id": "ctx_growth", "sev": "注意",
                            "title": "记录每分钟涨 %.0f KB" % (per_min / 1024.0),
                            "evidence": "按这个速度，约 %.0f 分钟后到观察线。" % left,
                            "advice": "该收尾了 —— 换个新线程继续，比硬撑强。"})

    # ③ 判断器抓到过的"声称做完却没做" —— 这是最实的证据
    vs = [r for r in (k.get("judgments") or []) if r.get("violations")]
    if vs:
        last = vs[-1]
        names = "；".join(str(v.get("rule")) for v in (last.get("violations") or [])[:3])
        out.append({"id": "unbacked_claim", "sev": "注意",
                    "title": "累计 %d 次抓到「说了没做 / 没做透」" % len(vs),
                    "evidence": "最近一次（%s）：%s" % (str(last.get("ts"))[:19], names),
                    "advice": "这几次的整改要求是不是没真做？把「已改好」按钮留到真改完再点。"})

    # ④ 报错密集
    ev = k.get("events") or []
    recent = [e for e in ev if e.get("kind") == "tool_error"][-60:]
    if len(recent) >= (th.get("tool_errors_watch") or 5):
        out.append({"id": "tool_errors", "sev": "注意",
                    "title": "最近工具报错 %d 次" % len(recent),
                    "evidence": "观察线是 %s 次。" % th.get("tool_errors_watch"),
                    "advice": "连续报错通常不是运气差，是方向错了 —— 停下来先看清楚。"})

    # ⑤ 返工：同一批文件被反复改（从事件流里看写入目标的重复度）
    files = [e.get("path") or e.get("file") for e in ev if e.get("kind") == "write"]
    files = [f for f in files if f]
    if files:
        from collections import Counter
        c = Counter(files)
        top, n = c.most_common(1)[0]
        if n >= (th.get("rework_degraded") or 4):
            out.append({"id": "rework", "sev": "注意",
                        "title": "同一个文件改了 %d 次" % n,
                        "evidence": "%s" % top,
                        "advice": "反复改同一个地方，八成是没想清楚 —— 先读一遍再动手。"})

    # ⑥ 宣言式信号：大脑提议、**人批准后**由监督者写进 signals.json 的那些
    for sig in _load_signals():
        try:
            f = _eval_signal(sig, k)
        except Exception:
            f = None
        if f:
            out.append(f)

    order = {"要紧": 0, "注意": 1, "还好": 2}
    out.sort(key=lambda x: order.get(x["sev"], 3))
    return out


# ------------------------------------------------------------------ 自检
def selfcheck():
    """大脑检查**它自己这套东西**还好不好用。"""
    res, notes = "还好", []
    try:
        err = K_ERR.read_text(encoding="utf-8", errors="replace")
        crash = len(re.findall(r"Traceback \(most recent call last\)", err))
        if crash:
            res = "注意"
            notes.append("自己崩过 %d 次（logs/app.err.log）" % crash)
    except Exception:
        crash = 0
    try:
        wd = K_WD.read_text(encoding="utf-8", errors="replace")
        pulled = len(re.findall(r"心跳过期", wd))
        if pulled:
            res = "注意"
            notes.append("看门狗把窗口拉起来过 %d 次" % pulled)
    except Exception:
        pass
    try:
        hb = LOG_DIR / "app.heartbeat"
        age = time.time() - hb.stat().st_mtime
        if age > 60:
            res = "要紧"
            notes.append("心跳已经停了 %.0f 秒" % age)
    except Exception:
        res = "要紧"
        notes.append("找不到心跳文件")
    try:
        j = _jsonl(K_JUDGE, 50)
        judged = [r for r in j if r.get("action") == "judged"]
        if len(judged) >= 3 and all(not r.get("violations") for r in judged[-3:]):
            notes.append("判断器连续 %d 轮没报任何问题（可能是真没问题，也可能是它瞎了）"
                         % len(judged[-3:]))
    except Exception:
        pass
    if not K_RULES.exists():
        res = "要紧"
        notes.append("读不到策略文件 rules.json")
    if not notes:
        notes.append("没有发现自己的毛病。")
    return {"result": res, "notes": notes}


# ------------------------------------------------------------------ Think（想一轮）
THINK_MIN_INTERVAL = int(os.environ.get("SUP_BRAIN_THINK_EVERY") or 900)   # 最短间隔（秒）
THINK_EFFORT = os.environ.get("SUP_BRAIN_EFFORT", "low")
THINK_MAX_TOKENS = int(os.environ.get("SUP_BRAIN_MAX_TOKENS") or 2000)
THINK_TIMEOUT = int(os.environ.get("SUP_BRAIN_TIMEOUT") or 90)
_think_lock = threading.Lock()

_SYS_THINK = (
    "你是「监督者」的大脑。你唯一的权限是给自己的**信号清单**（signals.json 里的数据）"
    "加信号；判等级、发信任、改策略、改代码你都没有权限，提了也不会生效。\n"
    "【数据源的语义】\n"
    "- score：内部降智风险评分。**0 = 正常，越高越糟**（不是越高越好！不要为 score=0 报警）\n"
    "- size：Codex 记录体积（字节），只期望看涨速\n"
    "- events：事件流，每行有 kind（如 command / write / tool_error / context）\n"
    "- judgments：判断器结论，每行有 violations（空数组 = 这轮没报问题）\n"
    "【运算符的语义】\n"
    "- slope_gt / slope_lt：取最近 window 个点，算「首尾差 / 段数」的平均变化量\n"
    "- last_gt / last_lt：最近 window 个点里的**最后一个值**\n"
    "- count_gt / count_ge：在**最近 window 条原始记录**里，**符合过滤条件的条数** 大于/大于等于 value\n"
    "【过滤】events 可写 kind=\"tool_error\"；judgments 可写 filter=\"violations_empty\"（该轮没报问题）"
    "或 violations_present。不会过滤就不要用 count_*（会恒命中）。\n"
    "【严重度】只能是 要紧 / 注意 / 还好。\n"
    "【铁律】只在**现有信号确实漏掉了某个能从数据里看出的问题**时才提议；"
    "**不要把正常状态当问题**；没把握就空手（宁可不提)。\n"
    "只输出 JSON。"
)


def _due():
    try:
        last = float(THINK_TS.read_text(encoding="utf-8").strip())
    except Exception:
        return True
    return (time.time() - last) >= THINK_MIN_INTERVAL


def _mark_think():
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        THINK_TS.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def _already_proposed():
    """已经提过（不管有没有被批准）的信号 id —— 防止同一提案反复堆积。"""
    ids = set()
    for r in _jsonl(THINK_LOG, 300):
        for i in (r.get("kept_ids") or []):
            ids.add(str(i))
    return ids


def _human_verdicts(limit=6):
    """人否决 / 撤回过的提案 —— 让大脑从人的否决里学，不要反复提同类。"""
    log = _jsonl(DATA_DIR / "changes" / "log.jsonl", 2000)
    req = {}
    for r in log:
        if r.get("action") == "request":
            req[str(r.get("id"))] = r
    out = []
    for r in log:
        a = r.get("action")
        if a not in ("rejected", "reverted"):
            continue
        q = req.get(str(r.get("id"))) or {}
        out.append({"verdict": "撤回" if a == "reverted" else "否决",
                    "title": str(q.get("title") or r.get("id") or ""),
                    "why": str(r.get("why") or q.get("why") or "（没写理由）")})
    return out[-limit:]


def _verdict_text():
    v = _human_verdicts()
    if not v:
        return "（还没有）"
    return "\n".join("- [%s] %s：%s" % (x["verdict"], x["title"], x["why"]) for x in v)


def _think_prompt(findings, health, taken_ids):
    fin = "\n".join("- [%s] %s（证据：%s）" % (f.get("sev"), f.get("title"), f.get("evidence"))
                    for f in (findings or [])[:8]) or "（这一轮没发现什么）"
    return (
        _SYS_THINK + "\n\n"
        "【已有的信号 id（不要重复）】\n" + (", ".join(sorted(taken_ids)) or "（无）") + "\n\n"
        "【人否决 / 撤回过的提案 —— 别重提同类，理由要看进去】\n" + _verdict_text() + "\n\n"
        "【这一轮它自己发现的】\n" + fin + "\n\n"
        "【自检】" + str(health.get("result") or "") + "：" + "；".join(health.get("notes") or []) + "\n\n"
        '只输出 JSON：{"title":"一句话说清要加什么(≤30字)","why":"证据/理由",'
        '"new_signals":[{"id":"小写下划线2-32位","source":"score|size|events|judgments",'
        '"op":"slope_gt|slope_lt|last_gt|last_lt|count_gt|count_ge","value":0,"window":6,'
        '"kind":"source=events 时可填事件类型，否则省略","filter":"source=judgments 时可填 violations_empty|violations_present，否则省略","sev":"要紧|注意|还好",'
        '"title":"≤20字","evidence":"可用 {first}/{last}/{n}/{slope}/{count} 占位","advice":"一句建议"}]}\n'
        "没有值得加的就写 \"new_signals\":[]。"
    )


def _guard_signals(sigs, taken_ids):
    """**守卫**：只放行落在白名单里的信号；想碰内核的当场挡下。返回 (ok, blocked)。"""
    ok, blocked = [], []
    for s in (sigs or []):
        if not isinstance(s, dict):
            blocked.append({"id": "?", "why": "信号不是对象"})
            continue
        sid = str(s.get("id") or "").strip().lower()
        bad = None
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", sid or ""):
            bad = "id 要是 2-32 位小写字母/数字/下划线、字母开头"
        elif sid in taken_ids:
            bad = "这个 id 已经存在或提过了"
        elif str(s.get("source") or "") not in ALLOWED_SOURCES:
            bad = "source 不在 %s" % "/".join(sorted(ALLOWED_SOURCES))
        elif str(s.get("op") or "") not in ALLOWED_OPS:
            bad = "op 不在 %s" % "/".join(sorted(ALLOWED_OPS))
        elif str(s.get("sev") or "") not in ALLOWED_SEV:
            bad = "sev 不在 要紧/注意/还好"
        if not bad:
            try:
                float(s.get("value"))
            except Exception:
                bad = "value 不是数字"
        if not bad:
            try:
                wi = int(s.get("window", 6))
                if not (2 <= wi <= 200):
                    bad = "window 要在 2-200"
            except Exception:
                bad = "window 不是整数"
        if not bad:
            flt = s.get("filter")
            if isinstance(flt, dict):
                flt = flt.get("only") or flt.get("kind")
            if flt not in (None, "", "violations_empty", "violations_present"):
                bad = "filter 只能是 violations_empty / violations_present"
        if not bad:
            text = " ".join(str(s.get(k2) or "") for k2 in
                            ("id", "title", "evidence", "advice", "kind", "filter")).lower()
            hit = next((w for w in FORBIDDEN if w.lower() in text), None)
            if hit:
                bad = "内容里出现了不许碰的东西：%s" % hit
        if not bad:
            for kk in ("title", "evidence", "advice"):
                if len(str(s.get(kk) or "")) > 200:
                    bad = kk + " 太长"
                    break
        if bad:
            blocked.append({"id": sid or "?", "why": bad})
        else:
            ok.append(s)
    return ok, blocked


def _log_think(rec):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with THINK_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def think(k, findings, health, force=False):
    """想一轮：走本机网关看"是不是缺一个信号"。缺就**写提案**，绝不自己改代码。"""
    with _think_lock:
        if not force and not _due():
            return {"ran": False, "why": "还没到下一次动脑的时间", "proposals": []}
        taken = {str(s.get("id")) for s in _load_signals() if isinstance(s, dict)} | _already_proposed()
        prompt = _think_prompt(findings, health, taken)
        raw = ""
        try:
            import judge as J
            raw = J._llm(prompt, max_tokens=THINK_MAX_TOKENS, effort=THINK_EFFORT,
                         timeout=THINK_TIMEOUT)
            data = J._json_of(raw)
            if not data:
                raw = J._llm(prompt, max_tokens=THINK_MAX_TOKENS, effort="none",
                             timeout=THINK_TIMEOUT)
                data = J._json_of(raw)
        except Exception as e:
            _mark_think()
            rec = {"ran": True, "title": "（没想出东西）", "why": "调本机网关失败：%s" % e,
                   "proposals": [{"title": "（没想出东西）", "status": "网关没通",
                                  "ids": [], "why": str(e)}], "kept_ids": []}
            _log_think(rec)
            return rec
        _mark_think()
        title = str((data or {}).get("title") or "给大脑加信号")[:60]
        why = str((data or {}).get("why") or "")[:400]
        ok, blocked = _guard_signals((data or {}).get("new_signals"), taken)
        proposals = []
        if ok:
            base = _json(SIGNALS, {}) or {}
            merged = {"version": int(base.get("version") or 1),
                      "note": "信号清单（数据）。由大脑提案、人批准后写入；大脑改不到代码。",
                      "signals": (_load_signals() + ok)}
            try:
                import supervisor_ui as ui
                ui.add_change_request(by="大脑", why=why or title, title=title,
                                      files=[{"path": "signals.json",
                                              "content": json.dumps(merged, ensure_ascii=False, indent=1)}])
                proposals.append({"title": title, "status": "已提交申请（等人同意）",
                                  "ids": [s.get("id") for s in ok]})
            except Exception as e:
                proposals.append({"title": title, "status": "提交失败：%s" % e,
                                  "ids": [s.get("id") for s in ok]})
        if blocked:
            proposals.append({"title": title, "status": "被守卫挡下（不许碰内核）",
                              "ids": [b.get("id") for b in blocked],
                              "why": "；".join("%s：%s" % (b.get("id"), b.get("why")) for b in blocked)})
        if not ok and not blocked:
            proposals.append({"title": title or "（无）", "status": "这一轮没提议加信号", "ids": []})
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "ran": True,
               "title": title, "why": why, "proposals": proposals,
               "kept_ids": [s.get("id") for s in ok], "blocked": blocked}
        _log_think(rec)
        return rec


# ------------------------------------------------------------------ Execute
MAX_LOG = 2 * 1024 * 1024        # 单个日志超过 2MB 就修剪


def act(findings, health):
    """只做**机械动作**：删自己的旧日志、重导出整改、留缺陷报告。

    绝不碰：rules.json / 等级判定 / 信任写入 / 自己的执法代码。
    """
    did = []
    # ① 自己的日志太大就砍一半
    for p in (K_APP, K_ERR, LOG_DIR / "watchdog.log"):
        try:
            if p.exists() and p.stat().st_size > MAX_LOG:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                p.write_text("\n".join(lines[-len(lines) // 2:]) + "\n", encoding="utf-8")
                did.append("修剪了自己的日志 %s" % p.name)
        except Exception:
            pass
    # ② 整改文件丢了就重导出（这是它该保证的事）
    try:
        import supervisor_ui as ui
        ui.export_corrections()
    except Exception:
        pass
    # ③ 自己崩过 —— 如实写一份缺陷报告，等**人**决定改不改
    if any("崩过" in n for n in health.get("notes") or []):
        try:
            err = K_ERR.read_text(encoding="utf-8", errors="replace")[-4000:]
            (LOG_DIR / "self_defect.md").write_text(
                "# 监督者自查：我崩过\n\n生成：%s\n\n"
                "我不会改自己的代码（改了自己给自己发通行证）。下面是原始报错，"
                "请人来判断要不要改：\n\n```\n%s\n```\n"
                % (datetime.now().isoformat(timespec="seconds"), err), encoding="utf-8")
            did.append("写了一份自我缺陷报告 logs/self_defect.md")
        except Exception:
            pass
    return did


# ------------------------------------------------------------------ 主循环
def run_once(force_think=False):
    k = observe()
    findings = analyze(k)
    health = selfcheck()
    did = act(findings, health)
    try:
        th = think(k, findings, health, force=force_think)
    except Exception as e:
        th = {"ran": False, "why": "想一轮的时候出错了：%s" % e, "proposals": []}
    doc = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "findings": findings,
        "health": health,
        "did": did,
        "proposals": th.get("proposals") or [],
        "thought": {"ran": bool(th.get("ran")), "why": th.get("why") or "",
                    "title": th.get("title") or ""},
        "summary": (findings[0]["title"] if findings else "没看出什么值得说的。"),
        "boundary": ("大脑只能改自己的信号清单（数据），且必须走「提案→人同意→监督者代写」；"
                     "判等级、发信任、改策略、改执法代码都不归它管。"),
    }
    try:
        BRAIN_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with BRAIN_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": doc["ts"], "summary": doc["summary"],
                                "did": did, "health": health["result"],
                                "proposals": doc["proposals"]},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass
    return doc


def loop(every=45):
    while True:
        try:
            run_once()
        except Exception:
            pass
        time.sleep(max(15, every))


def _cli_research(argv):
    q = " ".join(a for a in argv if not a.startswith("--"))
    if not q:
        print("用法: python brain.py --research \"要查的问题\"")
        return 2
    r = research_rubric(q)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    if "--research" in sys.argv:
        sys.exit(_cli_research(sys.argv[1:]))
    if "--loop" in sys.argv:
        loop()
    else:
        print(json.dumps(run_once(force_think="--think" in sys.argv),
                         ensure_ascii=False, indent=1))