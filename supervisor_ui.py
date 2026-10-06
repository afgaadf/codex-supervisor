# -*- coding: utf-8 -*-
"""监督者（Supervisor） —— 独立于 Codex、替用户盯住 Codex 的程序。

流程：需要信任 → 向监督者提交「申请」→ 监督者通知用户 → 用户在界面「同意/拒绝」。

v2 新增：**本程序自己的监控循环**（不依赖 Codex 内 supervisor 的结论）：
  · 直接读 Codex 的 transcript（primary 证据）→ 体积
  · 自己重算事件流（turns/compactions/parse_failures/...）
  · 用官方阈值自己判等级，与 Codex 内 supervisor **交叉比对**
  · 检测「Codex 内 supervisor 停摆 / 结论不一致」并告警
信任：**只能由人在界面上授予**（写接口需内存会话令牌），严格按官方标准写入。
"""
from __future__ import annotations
import hashlib, http.server, json, re as _re, secrets, socketserver, subprocess, sys, threading, time, webbrowser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from paths import APP_DIR, CODEX_HOME, DATA_DIR, is_packaged, APP_DIR

CODEX_CFG = str(CODEX_HOME / "config.toml")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_trust as ct  # noqa: E402

AUDIT = DATA_DIR / "audit.jsonl"
ALERTS = DATA_DIR / "alerts.jsonl"
REQUESTS = DATA_DIR / "requests.jsonl"
CONV = DATA_DIR / "conversations.jsonl"
CHANGES = DATA_DIR / "changes"
JUDGE = DATA_DIR / "judgments.jsonl"
CORRECTIONS = DATA_DIR / "corrections.json"
VAULT_INDEX = DATA_DIR / "vault_index.json"      # Obsidian 插件推来的全库索引
VAULT_EVENTS = DATA_DIR / "vault_events.jsonl"   # 插件推来的实时事件流
VAULT_CHECKS = DATA_DIR / "vault_checks.json"    # 插件跑出来的规矩体检结果
VAULT_CMDS = DATA_DIR / "vault_commands.jsonl"   # 监督者下发给插件的命令（只允许新建/追加/移动）
PLUGIN_TOKEN = DATA_DIR / "plugin_token.txt"     # 插件读它来鉴权（同用户可读）
STATE_DB = CODEX_HOME / "state_5.sqlite"
HOST, PORT = "127.0.0.1", 8765
TOKEN = secrets.token_urlsafe(24)
AD = CODEX_HOME / "anti-degradation"
AD_STATE, AD_EVENTS, AD_RULES = AD / "state" / "session.json", AD / "state" / "events.jsonl", AD / "rules" / "rules.json"
SESSIONS = CODEX_HOME / "sessions"
SUPERVISOR = AD / "supervisor.py"
STALE_MIN = 10

_cache = {"monitor": None, "sup": None, "sup_ts": 0.0, "size0": None, "level": None}
_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _jsonl(path, tail=200000):
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    return out[-tail:]


def write_plugin_token():
    """把会话令牌写一份给 Obsidian 插件用（同用户可读；换进程即换令牌）。"""
    try:
        PLUGIN_TOKEN.write_text(TOKEN, encoding="utf-8")
    except Exception:
        pass


def fresh_mod(modname):
    """按 mtime 重载模块（避免 API 侧跑旧缓存代码）。"""
    import importlib, sys as _sys
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


def vault_state():
    """给插件看的：监督者现在的等级 + 待办 + 上次索引时间。"""
    sup = supervisor_status() or {}
    try:
        idx = json.loads(VAULT_INDEX.read_text(encoding="utf-8"))
    except Exception:
        idx = {}
    try:
        chk = json.loads(VAULT_CHECKS.read_text(encoding="utf-8"))
    except Exception:
        chk = {}
    cmds = []
    try:
        lines = VAULT_CMDS.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, len(lines) - 50)
        for i, ln in enumerate(lines[start:], start=start + 1):
            try:
                rec = json.loads(ln)
                rec["i"] = i
                cmds.append(rec)
            except Exception:
                pass
    except Exception:
        pass
    # 最近一次库体检的明细（插件状态面板要用）
    recent = _jsonl(VAULT_EVENTS, 5)
    return {"level": sup.get("level"), "score": sup.get("score"),
            "reasons": sup.get("reasons") or [],
            "pending_corrections": len(open_corrections()),
            "index_ts": idx.get("ts"), "notes": idx.get("count"),
            "checks": chk.get("summary") or {}, "checks_ts": chk.get("ts"),
            "commands": cmds[-20:], "recent_events": recent}


def vault_command(kind, **kw):
    """给插件下命令（只允许 create_note / append_note / move_note，禁止删除）。"""
    allow = {"create_note", "append_note", "move_note"}
    if kind not in allow:
        return {"ok": False, "error": "命令不在白名单：%s" % kind}
    rec = {"ts": now_iso(), "action": kind}
    rec.update(kw)
    try:
        with VAULT_CMDS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        audit("vault_command", kind, **{k: str(v)[:120] for k, v in kw.items()})
        return {"ok": True, "queued": rec}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def audit(action, key, **extra):
    rec = {"ts": now_iso(), "action": action, "key": key, **extra}
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def alert(kind, detail):
    rec = {"ts": now_iso(), "kind": kind, "detail": detail}
    with ALERTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec

# ---------------------------------------------------------------- 授权申请（申请→通知→界面授权）
def _append(path, rec):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def requests_latest():
    latest = {}
    for r in _jsonl(REQUESTS, 5000):
        k = r.get("key")
        if k:
            latest[k] = r
    return latest


def open_requests():
    """还没处理的申请（最新一条是 request，且该小工具现在仍未获信任）。"""
    latest = requests_latest()
    hs = {h["key"]: h for h in ct.list_handlers()}
    out = []
    for k, r in latest.items():
        h = hs.get(k) or {}
        if r.get("action") == "request" and h.get("status") != "trusted":
            item = dict(r); item["event"] = h.get("event") or ""; item["status"] = h.get("status") or "?"
            out.append(item)
    out.sort(key=lambda x: x.get("ts") or "")
    return out


def add_request(key, reason, by, current_hash):
    rec = {"ts": now_iso(), "action": "request", "key": key, "reason": reason, "by": by,
           "current_hash": current_hash, "event": next((h["event"] for h in ct.list_handlers() if h["key"] == key), "")}
    _append(REQUESTS, rec)
    return rec


def resolve_request(key, action):
    _append(REQUESTS, {"ts": now_iso(), "action": action, "key": key})


# ---------------------------------------------------------------- 授权单位 = 对话
def threads_list():
    """读 Codex 的对话列表（只读它自己的数据库）。"""
    import sqlite3
    out = []
    try:
        con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
        cur = con.cursor()
        for r in cur.execute("select id,title,archived,created_at,updated_at,cwd "
                             "from threads order by updated_at desc limit 300"):
            out.append({"id": r[0], "title": (r[1] or "（无标题）"), "archived": bool(r[2]),
                        "created_at": r[3], "updated_at": r[4], "cwd": r[5]})
        con.close()
    except Exception:
        pass
    return out


def conv_state():
    latest = {}
    for r in _jsonl(CONV, 5000):
        if r.get("id"):
            latest[r["id"]] = r
    return latest


def conversations():
    """正在被监督的对话：未归档、也没被你在界面上关掉。
    —— 新对话**默认就在监督**，不需要你逐个批准。"""
    latest = conv_state()
    out = []
    for th in threads_list():
        if th["archived"]:
            continue                       # 归档 → 自动不列
        if (latest.get(th["id"]) or {}).get("action") == "close":
            continue                       # 你点过关闭 → 不列
        item = dict(th)
        item["since"] = (latest.get(th["id"]) or {}).get("ts")
        out.append(item)
    return out


def current_conversation_id():
    try:
        return json.loads(AD_STATE.read_text(encoding="utf-8")).get("codex_session_id")
    except Exception:
        return None


def protection_status():
    """监督者自己有没有被保护（Codex 里的 AI 能不能读到/改到它）。"""
    import tomllib
    mode = None
    try:
        mode = tomllib.loads(ct.CONFIG_TOML.read_text(encoding="utf-8")).get("sandbox_mode")
    except Exception:
        pass
    protected = mode in ("workspace-write", "read-only")
    return {"sandbox_mode": mode, "protected": protected, "self_path": str(APP_DIR),
            "guard_path": str(AD)}


def _text_of(msg):
    try:
        return "".join(c.get("text", "") for c in (msg.get("content") or []) if isinstance(c, dict))
    except Exception:
        return ""


# ---------------------------------------------------------------- 改监督者：申请 → 你同意 → 监督者代改
def _safe_rel(p):
    """只允许监督者自己目录内的相对路径，挡掉 ../ 和绝对路径。"""
    p = str(p or "").replace("\\", "/").strip().lstrip("/")
    if not p or ".." in p.split("/"):
        return None
    return p


def add_change_request(by, why, title, files):
    CHANGES.mkdir(parents=True, exist_ok=True)
    rid = secrets.token_hex(6)
    d = CHANGES / rid
    saved = []
    for f in (files or []):
        rel = _safe_rel(f.get("path"))
        if not rel:
            continue
        dst = d / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(str(f.get("content") or ""), encoding="utf-8")
        saved.append(rel)
    rec = {"ts": now_iso(), "id": rid, "action": "request", "by": str(by or ""),
           "why": str(why or ""), "title": str(title or "（未命名改动）"), "files": saved}
    if saved:                       # 没有有效文件就不入账（避免空提案堆在列表里）
        _append(CHANGES / "log.jsonl", rec)
    return rec


def change_requests():
    """还没处理的改监督者申请。"""
    recs = _jsonl(CHANGES / "log.jsonl", 2000)
    latest = {}
    for r in recs:
        latest[r.get("id")] = r
    out = [r for r in latest.values() if r.get("action") == "request" and r.get("files")]
    out.sort(key=lambda x: x.get("ts") or "")
    return out


def _change_dir(rid):
    rid = "".join(ch for ch in str(rid) if ch.isalnum())
    return CHANGES / rid if rid else None


def apply_change(rid, by="你"):
    """由监督者自己动手：先备份，再写入。**谁批的也记账**（窗口里点的也记）。"""
    # 【大厂标准】打包版 = 安装目录只读，不做"自改代码"；改动走配置或版本更新。
    if is_packaged():
        return False, "打包版不支持改程序代码（安装目录只读）。请改配置，或安装新版本。"
    rec = next((r for r in _jsonl(CHANGES / "log.jsonl", 2000)
                if r.get("id") == rid and r.get("action") == "request"), None)
    if not rec:
        return False, "找不到这条申请"
    d = _change_dir(rid)
    done = []
    for rel in rec.get("files") or []:
        src = d / rel
        if not src.exists():
            continue
        dst = APP_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():                                  # 备份原文件
            b = d / "backup" / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            b.write_bytes(dst.read_bytes())
        dst.write_bytes(src.read_bytes())
        done.append(rel)
    _append(CHANGES / "log.jsonl",
            {"ts": now_iso(), "id": rid, "action": "applied", "by": str(by or ""), "files": done})
    audit("change_applied", rid, files=str(done), by=str(by or ""))
    return True, done


def reject_change(rid, why=""):
    try:
        import learn as L
        L.add_lesson("reject", "提案 %s" % rid, why or "（没写理由）", source="human")
    except Exception:
        pass
    """人拒绝一条改动申请。why 会被喂给大脑，让它别再提同类。"""
    _append(CHANGES / "log.jsonl",
            {"ts": now_iso(), "id": rid, "action": "rejected", "by": "你", "why": str(why or "")})
    audit("change_rejected", rid, why=str(why or ""))
    return True


# ---------------------------------------------------------------- 规矩监督 + 责令改正
def _call_summary(name, args):
    """**一行**说清这次工具调用干了什么，并把涉及的文件名挑出来。

    —— 这一步是为了判断器：它只看得到摘要，摘要里必须有关键证据（文件名），
    否则会出现"明明写了文件，判断器却说没写"的冤枉（真实踩过）。
    """
    s = _re.sub(r"\s+", " ", str(args or "")).strip()
    files = _re.findall(r"[A-Za-z]:\\[^\"\'<>|]+|\b[\w\-.\u4e00-\u9fff]+\.(?:py|md|json|jsonl|ps1|cmd|txt|toml|html)\b", s)
    seen, uniq = set(), []
    for x in files:
        b = x.rstrip(".,;:")
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    head = s[:120]
    tail = ("　→ 涉及文件: " + ", ".join(uniq[:6])) if uniq else ""
    return "%s(%s)%s" % (name or "?", head, tail)


def summarize_actions(actions, max_lines=40):
    """把工具调用列表压成**完整但简短**的结构化清单。

    依据：Liu et al. 2023《Lost in the Middle》—— 相关信息落在长上下文的**中间**时
    最容易被忽略；加上 LLM-as-a-judge 的 verbosity bias。所以既不能整段截断（证据会丢），
    也不能把全文堆进去（关键证据会被淹）。做法是：一行一条、大量时掐中间留两头、
    并且**明确写出省略了多少条**，免得判断器把"没看到"当成"没做"。
    """
    acts = list(actions or [])
    total = len(acts)
    omitted = 0
    if total > max_lines:
        head_n, tail_n = 8, max_lines - 9
        omitted = total - head_n - tail_n
        acts = acts[:head_n] + ["…（此处省略 %d 条，未列出≠没做）" % omitted] + acts[-tail_n:]
    lines = ["共 %d 次工具调用%s：" % (
        total, "（中间省略 %d 条，已注明）" % omitted if omitted else "（完整清单，无省略）")]
    for i, a in enumerate(acts, 1):
        lines.append("%d. %s" % (i, a))
    return "\n".join(lines)


def _cid_of_transcript(p):
    m = _re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                   p.name if p else "")
    return m.group(1) if m else None


def running_transcript():
    """**正在运行的窗口** = 最近被写入的 rollout。监督只盯它。"""
    hits = list(SESSIONS.rglob("rollout-*.jsonl"))
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def running_window():
    """给界面看：现在到底在盯哪个窗口。"""
    p = running_transcript()
    if not p:
        return {}
    cid = _cid_of_transcript(p) or current_conversation_id()
    th = next((x for x in threads_list() if x["id"] == cid), None)
    try:
        age = round(time.time() - p.stat().st_mtime, 1)
    except Exception:
        age = None
    return {"thread_id": cid, "title": (th or {}).get("title") or "（认不出是哪个对话）",
            "cwd": (th or {}).get("cwd") or "", "age_sec": age,
            "transcript": str(p)}


def latest_turn():
    """取**正在运行的那个窗口**的最后一轮：你的要求 / 它做了什么 / 它说了什么。"""
    tp = running_transcript() or find_transcript(current_conversation_id())
    if not tp:
        return None
    try:
        lines = tp.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    turns, cur = [], None
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("type") != "response_item":
            continue
        pl = r.get("payload") or {}
        ty = pl.get("type")
        if ty == "message" and pl.get("role") == "user":
            cur = {"ts": r.get("timestamp"), "ask": _text_of(pl), "actions": [], "say": "", "calls": 0}
            turns.append(cur)
        elif cur is not None:
            if ty == "function_call":
                a = pl.get("arguments") or ""
                cur["calls"] += 1
                cur["actions"].append(_call_summary(pl.get("name"), a))
            elif ty == "message" and pl.get("role") == "assistant":
                cur["say"] = _text_of(pl)
    # 只判**已经结束**的那一轮（最后一条用户消息之后的那轮还在进行，不能判）
    return turns[-2] if len(turns) >= 2 else None


def _judged_turns():
    return {r.get("turn") for r in _jsonl(JUDGE, 3000) if r.get("action") == "judged"}


def _dismissed():
    return {r.get("turn") for r in _jsonl(JUDGE, 3000) if r.get("action") == "dismiss"}


def open_corrections():
    """还没被你划掉、也没过期的整改要求。"""
    dismissed = _dismissed()
    out, now = [], time.time()
    for r in _jsonl(JUDGE, 3000):
        if r.get("action") != "judged" or r.get("turn") in dismissed or r.get("selftest"):
            continue                      # 自检记录只作演示，不生成责令改正
        try:
            t0 = datetime.fromisoformat(r.get("ts") or "")
            if (datetime.now(timezone.utc) - t0).total_seconds() > 7200:
                continue                      # 超过 2 小时自动过期，避免一直唠叨
        except Exception:
            pass
        for v in (r.get("violations") or []):
            out.append({"turn": r.get("turn"), "ts": r.get("ts"), "ask": r.get("ask", ""),
                        "rule": v.get("rule"), "evidence": v.get("evidence"), "fix": v.get("fix")})
    return out


def active_supervisors():
    """最近一轮判定命中的监督者（界面显示用）。"""
    for r in reversed(_jsonl(JUDGE, 500)):
        if r.get("action") == "judged":
            return r.get("supervisors") or []
    return []


# 常驻规矩：从**电脑管家自己保存的** rules_hard.json 生成（每轮注入给 AI）
RULES_HARD = DATA_DIR / "rules_hard.json"


def load_hard_rules(only_enabled=True):
    try:
        if not RULES_HARD.exists():                 # 首次运行：从随包只读副本播种
            try:
                RULES_HARD.write_text((APP_DIR / "rules_hard.json").read_text(encoding="utf-8"),
                                      encoding="utf-8")
            except Exception:
                pass
        d = json.loads(RULES_HARD.read_text(encoding="utf-8"))
        rows = d.get("rules") or []
    except Exception:
        rows = []
    return [r for r in rows if (r.get("enabled") if only_enabled else True)]


def save_hard_rules(rows):
    try:
        d = {"version": 1, "note": "硬性规则库（电脑管家保存）", "updated": now_iso()[:10], "rules": rows}
        RULES_HARD.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except Exception:
        return False


def toggle_hard_rule(rid, enabled):
    rows = load_hard_rules(only_enabled=False)
    hit = False
    for r in rows:
        if str(r.get("id")) == str(rid):
            r["enabled"] = bool(enabled)
            hit = True
    if not hit:
        return {"ok": False, "error": "没有这条规则：%s" % rid}
    save_hard_rules(rows)
    audit("hard_rule_toggle", str(rid), enabled=bool(enabled))
    return {"ok": True, "id": rid, "enabled": bool(enabled)}


def standing_rules():
    rows = load_hard_rules()
    out = []
    for r in rows:
        out.append({"standing": True,
                    "rule": "%s %s" % (r.get("id"), r.get("name")),
                    "evidence": r.get("source") or "电脑管家硬性规则",
                    "fix": str(r.get("requirement") or "")[:300]})
    return out or [{"standing": True, "rule": "H2 联网核实",
                    "evidence": "电脑管家硬性规则",
                    "fix": "涉及规格/版本/API/官方要求/数值，先联网查再写；给来源URL+T1–T4+访问日期。"}]


def export_corrections():
    """给 hook 读：常驻规矩 + 未完成的整改要求（hook 会注入给 AI）。"""
    try:
        CORRECTIONS.write_text(json.dumps({"open": standing_rules() + open_corrections()},
                                          ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def dismiss_corrections(turn=""):
    _append(JUDGE, {"ts": now_iso(), "action": "dismiss", "turn": turn})
    try:
        import learn as L
        rule = ""
        for r in _jsonl(JUDGE, 400):
            if r.get("turn") == turn and r.get("action") == "judged":
                vs = r.get("violations") or []
                rule = str((vs[0] or {}).get("rule") or "") if vs else ""
                break
        L.add_lesson("dismiss", rule or ("turn=%s" % turn), "人把这条责令改正划掉了", source="human")
    except Exception:
        pass
    export_corrections()


def judge_latest_turn():
    t = latest_turn()
    if not t or not (t.get("say") or t.get("actions")):
        return
    h = hashlib.sha1(((t.get("ask") or "") + (t.get("say") or "") + str(t.get("calls"))).encode("utf-8")).hexdigest()[:12]
    if h in _judged_turns():
        return
    import judge as J
    res = J.judge_turn_ex(t.get("ask", ""), summarize_actions(t.get("actions")), t.get("say", ""))
    vs = res.get("violations") or []
    sups = res.get("supervisors") or []
    acts = summarize_actions(t.get("actions"))
    mech = {"online": res.get("online") or {},
            "vault": J.check_vault(t.get("ask", ""), acts, t.get("say", "")),
            "lazy": J.check_lazy(t.get("ask", ""), acts, t.get("say", ""))}
    model = res.get("behavior") or {}
    checks = {}
    for k in ("online", "vault", "lazy"):
        if (model.get(k) or {}).get("verdict"):
            checks[k] = dict(mech.get(k) or {})
            checks[k]["model"] = model[k]          # 模型判的（行为检测）
        else:
            checks[k] = dict(mech.get(k) or {})
    checks["online"].setdefault("model", {})
    try:
        c = (supervisor_status() or {}).get("counters") or {}
        bad = []
        if int(c.get("rework") or 0) >= 4:
            bad.append("返工 %s" % c.get("rework"))
        if int(c.get("tool_errors") or 0) >= 5:
            bad.append("工具错 %s" % c.get("tool_errors"))
        if int(c.get("compactions") or 0) >= 2:
            bad.append("压缩 %s" % c.get("compactions"))
        checks["degrade"] = {"risky": bool(bad), "verdict": "有降智迹象" if bad else "未见降智迹象",
                             "evidence": "、".join(bad) or "返工/工具错/压缩都在线下",
                             "model": (model.get("degrade") or {})}
    except Exception as e:
        checks["degrade"] = {"risky": False, "verdict": "取不到计数", "evidence": str(e)[:60]}
    _append(JUDGE, {"ts": now_iso(), "action": "judged", "turn": h,
                    "ask": (t.get("ask") or "")[:200], "violations": vs,
                    "supervisors": sups, "online": res.get("online") or {},
                    "checks": checks})
    export_corrections()
    # 检测到相关内容 → 启用监督者 → 界面内通知（同一个不重复吵）
    if sups:
        names = "、".join(s.get("name") or s.get("id") for s in sups)
        cache = _cache.get("sup_notified") or []
        if names not in cache:
            _cache["sup_notified"] = (cache + [names])[-20:]
            alert("supervisor_on", names)
            notify("监督者", "检测到相关内容，已启用监督者：%s" % names)
    if vs:
        notify("监督者", "发现 %d 处不守规矩，已责令改正" % len(vs))


def judge_loop():
    time.sleep(20)
    while True:
        try:
            judge_latest_turn()
        except Exception:
            pass
        time.sleep(30)


def behavior_scan(limit=6):
    """保守的“可疑行为”检查：只报**能说清楚**的迹象，判不判由你。"""
    import re as _re
    cid = current_conversation_id()
    tp = find_transcript(cid)
    if not tp:
        return []
    try:
        lines = tp.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    turns, cur = [], None
    done_words = ("完成", "已修复", "搞定了", "做好了", "已改好", "已解决", "done")
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("type") != "response_item":
            continue
        pl = r.get("payload") or {}
        ty = pl.get("type")
        if ty == "message" and pl.get("role") == "user":
            cur = {"ts": r.get("timestamp"), "ask": _text_of(pl)[:120], "calls": [],
                   "writes": 0, "errors": 0, "say": ""}
            turns.append(cur)
        elif cur is not None:
            if ty == "function_call":
                name = pl.get("name") or ""
                args = pl.get("arguments") or ""
                cur["calls"].append(name)
                if _re.search(r"set-content|add-content|write_text|write_text|new-item|remove-item|>\s*\S|apply_patch|tee-object", args, _re.I):
                    cur["writes"] += 1
            elif ty == "function_call_output":
                o = pl.get("output")
                o = o if isinstance(o, str) else json.dumps(o, ensure_ascii=False)
                # 只认**明确的失败**标记，避免把搜索结果里的字样算成报错
                if _re.search(r"Process exited with code [1-9]|Exit code: [1-9]|Traceback \(most recent call last\)|is not recognized as", o or ""):
                    cur["errors"] += 1
            elif ty == "message" and pl.get("role") == "assistant":
                cur["say"] = _text_of(pl)
    out = []
    for tn in turns[-limit:]:
        flags = []
        claim = any(w in (tn["say"] or "") for w in done_words)
        if claim and not tn["calls"]:
            flags.append("说“完成”了，但这一轮**一次工具都没用**（可能没干活就说完成）")
        if claim and tn["calls"] and tn["writes"] == 0 and len(tn["calls"]) <= 1:
            flags.append("说“完成”了，但这一轮**几乎没动手**（可能偷懒）")
        if tn["errors"] >= 3:
            flags.append(f"这一轮**出错 {tn['errors']} 次**（可能在瞎试 / 越弄越砸）")
        if len(tn["calls"]) >= 25:
            flags.append(f"这一轮**工具调用 {len(tn['calls'])} 次**（可能绕圈）")
        if flags:
            out.append({"ts": tn["ts"], "ask": tn["ask"], "say": (tn["say"] or "")[:160],
                        "calls": len(tn["calls"]), "errors": tn["errors"], "flags": flags})
    return out[-limit:]


def conv_action(cid, action, title=""):
    rec = {"ts": now_iso(), "id": cid, "action": action, "title": title}
    _append(CONV, rec)
    return rec


def notify(title, msg):
    """弹一条 Windows 通知（托盘气泡）。失败就静默。"""
    def _run():
        try:
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                  "$n.ShowBalloonTip(12000,'" + title.replace("'", "''") + "','" + msg.replace("'", "''") + "',"
                  "[System.Windows.Forms.ToolTipIcon]::Info);"
                  "Start-Sleep -Seconds 13;$n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                             creationflags=0x08000000)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()




# ---------------------------------------------------------------- 监控（独立判定）
def find_transcript(cid):
    if cid:
        hits = [p for p in SESSIONS.rglob(f"*{cid}*.jsonl")]
        if hits:
            return max(hits, key=lambda p: p.stat().st_mtime)
    hits = list(SESSIONS.rglob("rollout-*.jsonl"))
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def _ind_level(v, watch, deg, blk):
    if blk and v >= blk:
        return "BLOCKED"
    if deg and v >= deg:
        return "DEGRADED"
    if watch and v >= watch:
        return "WATCH"
    return "NORMAL"


def compute_monitor():
    th, s = {}, {}
    try:
        th = json.loads(AD_RULES.read_text(encoding="utf-8")).get("thresholds", {})
    except Exception:
        pass
    try:
        s = json.loads(AD_STATE.read_text(encoding="utf-8"))
    except Exception:
        pass
    cid = s.get("codex_session_id")
    tp = find_transcript(cid)
    ctx = tp.stat().st_size if tp else 0
    started = s.get("started_ts")
    ev = _jsonl(AD_EVENTS, 20000)
    if started:
        ev = [r for r in ev if str(r.get("ts") or "") >= str(started)]
    cnt = Counter(r.get("kind") for r in ev)
    ind = _ind_level(ctx, th.get("context_chars_watch"), th.get("context_chars_degraded"), th.get("context_chars_blocked"))
    # 停摆检测
    stale_min = None
    lu = s.get("last_updated")
    if lu:
        try:
            t = datetime.fromisoformat(lu)
            stale_min = round((datetime.now(timezone.utc) - t).total_seconds() / 60, 1)
        except Exception:
            pass
    grew = _cache["size0"] is not None and ctx > _cache["size0"]
    return {
        "ts": now_iso(), "independent_level": ind, "transcript_bytes": ctx, "window_start": s.get("started_ts"),
        "transcript": (str(tp) if tp else None), "codex_session_id": cid,
        "events": {k: cnt.get(k, 0) for k in
                   ("turn", "compact", "parse_failure", "tool_error", "command", "turn_end", "session_start", "subagent_start")},
        "last_updated": lu, "stale_min": stale_min,
        "stale": bool(stale_min is not None and stale_min > STALE_MIN and grew),
        "counters": {k: s.get(k) for k in ("turns", "context_chars", "compactions", "rework", "parse_failures")},
        "limits": {"context_watch": th.get("context_chars_watch"),
                   "context_degraded": th.get("context_chars_degraded"),
                   "compactions_watch": th.get("compactions_watch")},
    }


def supervisor_status(force=False):
    if not force and _cache["sup"] is not None and (time.time() - _cache["sup_ts"]) < 30:
        return _cache["sup"]
    try:
        import subprocess
        r = subprocess.run([sys.executable, str(SUPERVISOR), "status", "--json"],
                           capture_output=True, text=True, encoding="utf-8", timeout=20)
        _cache["sup"] = json.loads(r.stdout or "{}")
    except Exception as e:
        _cache["sup"] = {"error": str(e)[:200]}
    _cache["sup_ts"] = time.time()
    return _cache["sup"]


def monitor_loop():
    while True:
        try:
            m = compute_monitor()
            if _cache["size0"] is None:
                _cache["size0"] = m["transcript_bytes"]
            _cache["size0"] = m["transcript_bytes"]
            lvl = m["independent_level"]
            if _cache["level"] is not None and lvl != _cache["level"]:
                alert("level_change", f"{_cache['level']} -> {lvl} (transcript {m['transcript_bytes']}B)")
            if m["stale"]:
                alert("supervisor_stale", f"Codex 内 supervisor {m['stale_min']} 分钟未更新，但 transcript 在增长")
            # 需要信任 → 自动生成「授权申请」并通知用户
            added = 0
            try:
                latest = requests_latest()
                for h in ct.list_handlers():
                    if h["status"] == "trusted":
                        continue
                    last = latest.get(h["key"])
                    if last and last.get("current_hash") == h["current_hash"]:
                        continue  # 这次变更已经申请过（在等，或你已处理）
                    add_request(h["key"],
                                "Codex 的小工具清单有变化（%s：%s）" % (h["event"], "新增" if h["status"] == "new" else "被改动"),
                                "监督者自动检测", h["current_hash"])
                    added += 1
                if added:
                    notify("监督者", "有 %d 条新的授权申请，请打开界面同意或拒绝" % added)
            except Exception:
                pass
            # 新对话**默认就在监督**，不再需要你逐个批准
            _cache["level"] = lvl
            with _lock:
                _cache["monitor"] = m
        except Exception as e:
            with _lock:
                _cache["monitor"] = {"error": str(e)[:200]}
        time.sleep(5)


# ---------------------------------------------------------------- config.toml 写（官方标准）
def _cfg_lines():
    return ct.CONFIG_TOML.read_text(encoding="utf-8").splitlines()


def _section(lines, header):
    for i, l in enumerate(lines):
        if l.strip() == header:
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if lines[j].lstrip().startswith("["):
                    end = j
                    break
            return i, end
    return None, None


def set_trust(key, hashval):
    lines = _cfg_lines()
    header = f"[hooks.state.'{key}']"
    i, end = _section(lines, header)
    if i is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [header, f'trusted_hash = "{hashval}"']
    else:
        for j in range(i + 1, end):
            if lines[j].strip().startswith("trusted_hash"):
                lines[j] = f'trusted_hash = "{hashval}"'
                break
        else:
            lines.insert(i + 1, f'trusted_hash = "{hashval}"')
    ct.CONFIG_TOML.write_text("\n".join(lines) + "\n", encoding="utf-8")


def revoke_trust(key):
    lines = _cfg_lines()
    header = f"[hooks.state.'{key}']"
    i, end = _section(lines, header)
    if i is None:
        return False
    del lines[i:end]
    while lines and not lines[-1].strip():
        lines.pop()
    ct.CONFIG_TOML.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


PAGE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>监督者</title><style>
:root{--bg:#0e1117;--card:#161b23;--line:#252c38;--fg:#eaeef5;--mut:#9aa4b6;--ok:#31c48d;--warn:#e3b341;--bad:#f2585b;--orange:#e88a3a;--acc:#4b8cf7}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 "Microsoft YaHei","Segoe UI",system-ui,sans-serif}
header{padding:20px 28px 16px;border-bottom:1px solid var(--line)}
h1{font-size:23px;margin:0 0 4px}h1 span{font-size:15px;color:var(--mut);font-weight:400}
main{padding:22px;max-width:1000px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:16px}
.hero{text-align:center;padding:30px 22px}.hero .light{font-size:64px;line-height:1}
.hero .big{font-size:28px;font-weight:700;margin:8px 0 4px}.hero .say{color:var(--mut);font-size:16px}
.title{font-size:17px;font-weight:700;margin-bottom:4px}.sub{color:var(--mut);font-size:14px;margin-bottom:14px}
.lst{display:grid;grid-template-columns:1fr auto;gap:10px 18px;align-items:center;font-size:15.5px}
.lst .v{font-weight:600;text-align:right}
.pk{color:var(--ok)}.pw{color:var(--warn)}.pb{color:var(--bad)}.po{color:var(--orange)}
.bar{height:9px;background:#0b0e13;border-radius:6px;overflow:hidden;border:1px solid var(--line);min-width:150px}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--ok),var(--warn))}
.btn{display:inline-block;background:var(--acc);color:#fff;border:0;border-radius:10px;padding:8px 16px;cursor:pointer;font-size:14.5px;font-family:inherit}
.btn.gray{background:transparent;border:1px solid var(--line);color:var(--fg)}.btn:hover{filter:brightness(1.12)}
.flag{border:1px solid rgba(227,179,65,.5);background:rgba(227,179,65,.07);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.flag .q{font-size:15px;font-weight:600;margin-bottom:6px;word-break:break-all}
.flag .r{color:var(--warn);font-size:14.5px;margin:3px 0}
.flag .m{color:var(--mut);font-size:13px;margin-top:6px}
.auth{border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin-bottom:9px;display:flex;justify-content:space-between;gap:14px;align-items:center}
.auth .h{font-size:15px;font-weight:600;word-break:break-all}.auth .m{color:var(--mut);font-size:13px}
details{margin-top:8px}summary{cursor:pointer;color:var(--mut);font-size:14px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
th{color:var(--mut);font-weight:600;font-size:13px}
code{background:#0b0d12;padding:2px 6px;border-radius:5px;color:#9dd0ff;font-size:12.5px;word-break:break-all}
.pill{padding:3px 11px;border-radius:999px;font-size:13px;white-space:nowrap;border:1px solid var(--line)}
.p-ok{background:rgba(49,196,141,.15);color:var(--ok);border-color:rgba(49,196,141,.45)}
.p-warn{background:rgba(227,179,65,.15);color:var(--warn);border-color:rgba(227,179,65,.45)}
.mut{color:var(--mut)}.logline{padding:5px 0;border-bottom:1px dashed var(--line);font-size:14px}
</style></head><body>
<header><h1>监督者 <span>—— 盯着 Codex 的 AI：有没有偷懒、瞎猜、越弄越砸</span></h1></header>
<main>
  <div class="card hero">
    <div class="light" id="light">⏳</div>
    <div class="big" id="heroTitle">正在读取…</div>
    <div class="say" id="heroSay">请稍候</div>
  </div>

  <div class="card">
    <div class="title">监督者自身防护</div>
    <div class="sub">保护<b>监督者自己</b>：Codex 里的 AI <b>不该能读到、更不能改到</b>它。否则它可以先把监督者改坏，再看你就都是"正常"了。</div>
    <div id="protect"></div>
  </div>

  <div class="card">
    <div class="title">改监督者的申请</div>
    <div class="sub">AI 想改监督者时，<b>它自己写不进去</b>，只能<b>提交申请</b>。<b>你同意后，监督者才动手改</b>，而且会先备份、留记录。</div>
    <div id="changes"></div>
  </div>

  <div class="card">
    <div class="title">规矩检查 · 责令改正</div>
    <div class="sub">监督者每轮会问一次模型：<b>该联网的联网了吗、该翻笔记的翻了吗、用的是不是最新稳定版、有没有造车轮、有没有按权威标准</b>。<br>
    发现违规 → <b>已自动把「责令改正」注入给 AI</b>，AI 下一条消息必须先改正并说明。这里也能看到原文。</div>
    <div id="corr"></div>
  </div>

  <div class="card">
    <div class="title">正在监督的对话</div>
    <div class="sub"><b>新对话默认就在监督里</b>，不用你批准。你在 Codex 里归档它、或在这里点「关闭」，就自动移出。</div>
    <div id="convList"></div>
  </div>

  <div class="card">
    <div class="title">可疑行为</div>
    <div class="sub">监督者挑出"看着不对劲"的地方，<b>判不判由你</b>（它只报能说清楚的，不乱扣帽子）。</div>
    <div id="behave"></div>
  </div>

  <div class="card">
    <div class="title">现在怎么样</div>
    <div class="sub">这几项是自动检查的，你不用操作。</div>
    <div class="lst" id="now"></div>
  </div>

  <div class="card">
    <div class="title">最近发生过什么</div>
    <div class="sub">你的每一次操作，和它发现的每一次异常，都记在这里。</div>
    <div id="logs" class="mut"></div>
  </div>

  <div class="card"><details><summary>技术详情（给懂电脑的人看，你可以忽略）</summary>
    <div class="lst" id="tech" style="margin-top:12px"></div>
    <div class="title" style="margin-top:18px">Codex 能自动运行的小工具</div>
    <div id="trustHead" class="mut" style="margin-bottom:8px"></div>
    <table><thead><tr><th>什么时候跑</th><th>什么工具</th><th>状态</th></tr></thead><tbody id="rows"></tbody></table>
  </details></div>
</main>
<script>
const TOKEN="__TOKEN__";
const AT={trust:"你同意了小工具",revoke:"你取消了小工具同意",deny:"你拒绝了一次申请",request:"收到一次申请",app_start:"监督者启动了一次",conv_close:"你关闭了一个对话（不再监督）",change_request:"有人申请改监督者",change_applied:"你同意并应用了改监督者的申请",change_rejected:"你拒绝了改监督者的申请",conv_authorize:"你授权了一个对话"};
const KD={level_change:"Codex 的状态发生了变化",supervisor_stale:"里面那套防护可能已经停了"};
const NM={PreToolUse:"每次用工具前",PostToolUse:"每次用完工具",UserPromptSubmit:"你发消息时",PreCompact:"对话要压缩前",PostCompact:"对话压缩后",SessionStart:"开始会话时",SessionEnd:"结束会话时",SubagentStart:"子助手启动",SubagentStop:"子助手结束",Stop:"一轮结束时"};
function esc(s){return String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}
function mb(n){return (n/1048576).toFixed(1)+" MB"}
function row(k,v){return `<div>${k}</div><div class="v">${v}</div>`}
function when(t){return String(t||"").replace("T"," ").slice(0,19)}
function ts(sec){try{return new Date(sec*1000).toLocaleString("zh-CN",{hour12:false,month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"})}catch(e){return "?"}}
function short(s,n){s=String(s||"");return s.length>n?s.slice(0,n)+"…":s}
async function load(){
  const s=await (await fetch("/api/state")).json(), m=s.monitor||{}, sup=s.supervisor||{}, L=m.limits||{};
  const C=s.conv||[], B=s.behave||[], PR=s.protect||{}, CH=s.changes||[], CO=s.corrections||[];
  const lv=m.independent_level||"?";
  let mm;
  if(CO.length) mm=["🟡","已责令 AI 改正 "+CO.length+" 处问题","往下看「规矩检查」——违规已自动发给 AI，要它改。"];
  else if(CH.length) mm=["🟡","有 "+CH.length+" 个改监督者的申请","往下看「改监督者的申请」——你同意后它才会改。"];
  else if(B.length) mm=["🟡","发现 "+B.length+" 处可疑行为","往下看「可疑行为」，判一判要不要管。"];
  else mm={NORMAL:["🟢","一切正常","Codex 状态很好，你不用管它。"],WATCH:["🟡","有点苗头","不用管，留意一下就行。"],DEGRADED:["🟠","Codex 状态变差了","它可能开始绕圈或记不住事，建议看一眼。"],BLOCKED:["🔴","Codex 已被暂停","等安全机制挡住，需要按下面提示处理。"]}[lv]||["⚪","读不到状态","稍等一下。"];
  document.getElementById("light").textContent=mm[0];
  document.getElementById("heroTitle").textContent=mm[1];
  document.getElementById("heroSay").textContent=mm[2];

  document.getElementById("protect").innerHTML = PR.protected
    ? '<span class="pill p-ok">✅ 已受保护</span> <span class="mut">AI 只能在它自己的工作区里写，碰不到监督者。</span>'
    : '<span class="pill p-warn">⚠️ 还没受保护</span> <div style="margin-top:8px">现在 AI 有<b>完整权限</b>，理论上可以读改监督者。开启保护要改一处设置：<details><summary>看怎么改（给懂电脑的人）</summary><div style="margin-top:8px">在 <code>' + esc(CODEX_CFG) + '</code> 里加：<br><code>sandbox_mode = "workspace-write"</code><br>然后重启 Codex。监督者装在 <code>'+esc(PR.self_path)+'</code>（工作区之外），所以收紧后它就写不到了。</div></details></div>';

  document.getElementById("corr").innerHTML = CO.length ? CO.map(v=>`
    <div class="flag">
      <div class="q">⚠️ 违反规矩：${esc(v.rule||"")}</div>
      <div class="r">依据：${esc(v.evidence||"（无）")}</div>
      <div class="m">已责令 AI：${esc(v.fix||"（未写）")}<br><span class="mut">发生在：${esc(short(v.ask,50))} ｜ ${esc(when(v.ts))}</span></div>
      <div style="margin-top:10px"><button class="btn gray" onclick="doneCorr('${esc(v.turn)}')">已改好，划掉</button></div>
    </div>`).join("") : '<span class="pk">✅ 没有违规，AI 现在守规矩。</span>';

  document.getElementById("changes").innerHTML = CH.length ? CH.map(r=>`
    <div class="flag">
      <div class="q">🔧 ${esc(r.title||"（未命名改动）")}</div>
      <div class="r">谁申请的：${esc(short(r.by,40)||"未知")} ｜ 时间：${esc(when(r.ts))}</div>
      <div class="m">为什么：${esc(r.why||"（未说明）")}<br>要改的文件：${(r.files||[]).map(f=>"<code>"+esc(f)+"</code>").join(" ")}</div>
      <div style="margin-top:10px"><button class="btn ok" onclick="applyCh('${esc(r.id)}')">同意并应用</button>
      <button class="btn bad" onclick="rejectCh('${esc(r.id)}')">拒绝</button></div>
    </div>`).join("") : '<span class="pk">✅ 没有人申请改监督者。</span>';

  document.getElementById("convList").innerHTML = C.length ? C.map(c=>`
    <div class="auth"><div><div class="h">《${esc(short(c.title,56))}》</div>
      <div class="m">最近活跃：${ts(c.updated_at)}</div></div>
      <button class="btn gray" onclick="closeConv('${esc(c.id)}')">关闭</button></div>`).join("")
    : '<span class="mut">暂时没有正在监督的对话。</span>';

  document.getElementById("behave").innerHTML = B.length ? B.map(x=>`
    <div class="flag">
      <div class="q">问：${esc(short(x.ask,70))}</div>
      ${x.flags.map(f=>`<div class="r">⚠️ ${f.replace(/\*\*/g,"")}</div>`).join("")}
      <div class="m">这一轮：工具调用 ${x.calls} 次 ｜ 明确报错 ${x.errors} 次 ｜ 时间 ${esc(when(x.ts))}</div>
    </div>`).join("") : '<span class="pk">✅ 暂时没发现可疑行为。</span>';

  const bytes=m.transcript_bytes||0, watch=L.context_watch||0, pct=watch?Math.min(100,Math.round(bytes/watch*100)):0, ev=m.events||{};
  document.getElementById("now").innerHTML=
    row("Codex 干活正常吗", lv==="NORMAL"?'<span class="pk">✅ 正常</span>':`<span class="${lv==="BLOCKED"?"pb":"po"}">⚠️ ${esc(lv)}</span>`)
   +row("对话记到多长了", `<div style="display:flex;gap:10px;align-items:center;justify-content:flex-end"><div class="bar"><i style="width:${pct}%"></i></div><span>${mb(bytes)}（用了 ${pct}%）</span></div>`)
   +row("有没有卡住重复", /rework|repeat/.test((sup.reasons||[]).join(" "))?'<span class="pw">⚠️ 有迹象</span>':'<span class="pk">✅ 没有</span>')
   +row("防护程序还活着吗", m.stale?'<span class="pb">⚠️ 可能停了</span>':'<span class="pk">✅ 正常</span>')
   +row("有没有出错被漏掉", ev.parse_failure?'<span class="pw">有 '+esc(ev.parse_failure)+' 次没看到</span>':'<span class="pk">✅ 没有</span>');

  const hs=s.hooks||[], need=hs.filter(h=>h.status!=="trusted");
  document.getElementById("trustHead").innerHTML = need.length?`<span class="pill p-warn">${need.length} 个还没同意</span>`:`<span class="pill p-ok">✅ ${hs.length} 个全部已同意</span>`;
  document.getElementById("rows").innerHTML=hs.map(h=>{const ok=h.status==="trusted";
    return `<tr><td>${esc(NM[h.event]||h.event)}</td><td><code>${esc(h.command).slice(0,80)}…</code></td>
      <td>${ok?'<span class="pk">✅ 已同意</span>':`<button class="btn" style="padding:4px 10px" onclick="approve('${esc(h.key)}')">同意</button>`}</td></tr>`;}).join("");

  const lg=await (await fetch("/api/logs")).json();
  document.getElementById("logs").innerHTML=(lg.items||[]).slice(-12).reverse().map(x=>
    `<div class="logline">🕒 ${esc(when(x.ts))} — ${x.kind?("发现异常："+esc(KD[x.kind]||x.kind)):esc(AT[x.action]||x.action||"")}</div>`).join("")||"（还没有记录）";

  document.getElementById("tech").innerHTML=row("内部程序判定", esc(sup.level||"?")+"（分数 "+esc(sup.score??"?")+"）")
    +row("内部程序原因", esc((sup.reasons||[]).join("；")||"无"))
    +row("记录文件大小", bytes.toLocaleString()+" 字节")
    +row("当前会话", "<code>"+esc(m.codex_session_id||"—")+"</code>")
    +row("沙箱设置", esc(PR.sandbox_mode||"（未设置）"));
}
async function applyCh(id){
  if(!confirm("同意后，监督者会立刻把这份改动写进自己（会先备份）。\n\n确定吗？")) return;
  const r=await (await fetch("/api/apply-change",{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},body:JSON.stringify({id})})).json();
  if(!r.ok) alert("没成功："+(r.error||r.info||"")); else alert("已应用。改动要重启监督者后完全生效。"); load();
}
async function rejectCh(id){
  if(!confirm("确定拒绝这份改动吗？")) return;
  const r=await (await fetch("/api/reject-change",{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},body:JSON.stringify({id})})).json();
  if(!r.ok) alert("没成功："+(r.error||"")); load();
}
async function doneCorr(turn){
  if(!confirm("确定这条已经改好了吗？划掉后就不再提醒 AI。")) return;
  const r=await (await fetch("/api/dismiss-correction",{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},body:JSON.stringify({turn})})).json();
  if(!r.ok) alert("没成功："+(r.error||"")); load();
}
async function closeConv(id){
  if(!confirm("关闭后，这个对话就不再被监督、也不再列出。确定吗？")) return;
  const r=await (await fetch("/api/conv",{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},body:JSON.stringify({id,action:"close"})})).json();
  if(!r.ok) alert("没成功："+(r.error||"")); load();
}
async function approve(key){
  if(!confirm("确定要「同意」这个小工具吗？")) return;
  const r=await (await fetch("/api/approve",{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},body:JSON.stringify({key})})).json();
  if(!r.ok) alert("没成功："+(r.error||"")); load();
}
load(); setInterval(load,4000);
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE.replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        if self.path == "/api/state":
            with _lock:
                m = dict(_cache["monitor"] or {})
            return self._send(200, json.dumps({"hooks": ct.list_handlers(), "monitor": m, "requests": open_requests(), "conv": conversations(),
                                               "behave": behavior_scan(), "protect": protection_status(), "changes": change_requests(),
                                               "corrections": open_corrections(),
                                               "supervisor": supervisor_status()}, ensure_ascii=False))
        if self.path == "/api/pc/state":
            try:
                st = json.loads((DATA_DIR / "pc_state.json").read_text(encoding="utf-8"))
            except Exception:
                st = {"ok": False, "error": "还没体检过（点「一键体检」）"}
            return self._send(200, json.dumps(st, ensure_ascii=False))
        if self.path == "/api/learn/state":
            import learn as L
            return self._send(200, json.dumps({"metrics": L.metrics(), "candidates": L.load_candidates(),
                                               "lessons": L.lessons(50)}, ensure_ascii=False))
        if self.path == "/api/pc/junk":
            import pc_guard
            return self._send(200, json.dumps(pc_guard.junk_scan(), ensure_ascii=False))
        if self.path == "/api/pc/services":
            import pc_guard
            return self._send(200, json.dumps(pc_guard.services_overview(), ensure_ascii=False))
        if self.path == "/api/pc/temps":
            import pc_guard
            return self._send(200, json.dumps(pc_guard.temps(), ensure_ascii=False))
        if self.path == "/api/pc/history":
            import pc_guard
            return self._send(200, json.dumps({"points": pc_guard.history(180),
                                               "alerts": pc_guard.alerts(50)},
                                              ensure_ascii=False))
        if self.path == "/api/pc/backups":
            import pc_guard
            return self._send(200, json.dumps({"backups": pc_guard.startup_backups()}, ensure_ascii=False))
        if self.path == "/api/vault/ops":
            VO = fresh_mod("vault_ops")
            return self._send(200, json.dumps({"metrics": VO.metrics(), "health": VO.health_score(),
                                               "trend": VO.trend(14), "pending": VO.pending_actions(12)},
                                              ensure_ascii=False))
        if self.path == "/api/vault/classes":
            VC = fresh_mod("vault_classify")
            return self._send(200, json.dumps(VC.compute(), ensure_ascii=False))
        if self.path == "/api/rules":
            return self._send(200, json.dumps({"rules": load_hard_rules(only_enabled=False),
                                               "enabled": len(load_hard_rules())}, ensure_ascii=False))
        if self.path == "/api/vault/priority":
            import vault_priority as VP
            return self._send(200, json.dumps(VP.compute(), ensure_ascii=False))
        if self.path == "/api/vault/state":
            return self._send(200, json.dumps(vault_state(), ensure_ascii=False))
        if self.path == "/api/logs":
            items = _jsonl(ALERTS, 200) + _jsonl(AUDIT, 200)
            items.sort(key=lambda r: r.get("ts", ""))
            return self._send(200, json.dumps({"items": items[-200:]}, ensure_ascii=False))
        self._send(404, '{"error":"not found"}')

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            req = {}
        key = req.get("key") or ""
        # ① 「申请」：脚本 / Codex 都可以提交，不需要界面令牌 —— 它只是申请，决定权仍在人
        if self.path == "/api/request":
            want = next((h for h in ct.list_handlers() if h["key"] == key), None)
            if not want:
                return self._send(404, '{"ok":false,"error":"未知的小工具"}')
            add_request(key, str(req.get("reason") or "（未说明原因）"),
                        str(req.get("by") or "未知来源"), want["current_hash"])
            notify("监督者", "有新的授权申请，请打开界面同意或拒绝")
            audit("request", key, event_label=want["event_label"], by=req.get("by"))
            return self._send(200, '{"ok":true}')
        # ①b 「改监督者」的提案：AI / 脚本可以提，但**只是提案**，决定权在人
        if self.path == "/api/change-request":
            rec = add_change_request(req.get("by"), req.get("why"), req.get("title"), req.get("files"))
            if not rec.get("files"):
                return self._send(400, '{"ok":false,"error":"没有有效的文件改动"}')
            notify("监督者", "有人申请改动监督者，请打开界面同意或拒绝")
            audit("change_request", rec["id"], title=rec.get("title"), by=rec.get("by"))
            return self._send(200, json.dumps({"ok": True, "id": rec["id"]}, ensure_ascii=False))
        # ② 同意 / 拒绝 / 撤销：必须由界面上的人发起（带令牌）

        if self.headers.get("X-Token") != TOKEN:
            return self._send(403, '{"ok":false,"error":"bad token（必须从本界面操作）"}')
        # ---- 电脑管家（体检只读；动作白名单 + 默认先预演）
        if self.path == "/api/pc/scan":
            import pc_guard
            r = pc_guard.scan()
            audit("pc_scan", str(r.get("level")), score=r.get("score"), items=len(r.get("items") or []))
            return self._send(200, json.dumps(r, ensure_ascii=False))
        if self.path == "/api/vault/ops/run":
            VO = fresh_mod("vault_ops")
            r = VO.daily_report()
            audit("vault_ops_run", str(r.get("health")))
            return self._send(200, json.dumps(r, ensure_ascii=False))
        if self.path == "/api/vault/ops/rollback":
            VO = fresh_mod("vault_ops")
            r = VO.rollback(req.get("stamp"))
            audit("vault_ops_rollback", str(r.get("stamp")))
            return self._send(200, json.dumps(r, ensure_ascii=False))
        if self.path == "/api/vault/ops/move":
            VO = fresh_mod("vault_ops")
            r = VO.move_note(str(req.get("path") or ""), str(req.get("to") or ""), dry_run=bool(req.get("dry_run", True)))
            audit("vault_ops_move", str(req.get("path"))[:120], dry_run=bool(req.get("dry_run", True)))
            return self._send(200, json.dumps(r, ensure_ascii=False))
        if self.path == "/api/vault/ops/frontmatter":
            VO = fresh_mod("vault_ops")
            r = VO.add_frontmatter(str(req.get("path") or ""), req.get("fields") or {}, dry_run=bool(req.get("dry_run", True)))
            audit("vault_ops_fm", str(req.get("path"))[:120], dry_run=bool(req.get("dry_run", True)))
            return self._send(200, json.dumps(r, ensure_ascii=False))
        if self.path == "/api/vault/classes-report":
            VC = fresh_mod("vault_classify")
            r = VC.report_to_vault()
            audit("vault_classes_report", str(r.get("path")))
            return self._send(200, json.dumps(r, ensure_ascii=False))
        if self.path == "/api/rules/toggle":
            r = toggle_hard_rule(str(req.get("id") or ""), bool(req.get("enabled")))
            return self._send(200 if r.get("ok") else 404, json.dumps(r, ensure_ascii=False))
        if self.path == "/api/vault/priority-report":
            import vault_priority as VP
            r = VP.report_to_vault()
            audit("vault_priority_report", str(r.get("path")))
            return self._send(200, json.dumps(r, ensure_ascii=False))
        if self.path == "/api/learn/scan":
            import learn as L
            new = L.learn_scan()
            audit("learn_scan", str(len(new)))
            return self._send(200, json.dumps({"new": new, "metrics": L.metrics()}, ensure_ascii=False))
        if self.path == "/api/learn/decide":
            import learn as L
            cid = str(req.get("id") or "")
            adopt = bool(req.get("adopt"))
            rows = L.load_candidates()
            hit = next((x for x in rows if x.get("id") == cid), None)
            if not hit:
                return self._send(404, '{"ok":false,"error":"没有这个候选"}')
            if adopt:
                L.add_lesson("adopted", hit.get("title"), hit.get("why"), source="human",
                             extra={"kind_src": hit.get("kind"), "action": hit.get("action")})
            rows = [x for x in rows if x.get("id") != cid]
            L.save_candidates(rows)
            audit("learn_decide", cid, adopt=adopt)
            return self._send(200, json.dumps({"ok": True, "adopted": adopt}, ensure_ascii=False))
        if self.path == "/api/pc/junk-clean":
            import pc_guard
            res = pc_guard.junk_clean(str(req.get("target") or ""), dry_run=bool(req.get("dry_run", True)))
            audit("pc_junk", str(req.get("target") or ""), dry_run=bool(req.get("dry_run", True)), ok=bool(res.get("ok")))
            return self._send(200, json.dumps(res, ensure_ascii=False))
        if self.path == "/api/pc/service":
            import pc_guard
            res = pc_guard.service_start(str(req.get("name") or ""), dry_run=bool(req.get("dry_run", True)))
            audit("pc_service_start", str(req.get("name") or ""), dry_run=bool(req.get("dry_run", True)))
            return self._send(200, json.dumps(res, ensure_ascii=False))
        if self.path == "/api/pc/startup-folder":
            import pc_guard
            res = pc_guard.startup_folder_action(str(req.get("path") or ""),
                                                 enable=bool(req.get("restore")),
                                                 dry_run=bool(req.get("dry_run", True)))
            audit("pc_startup_folder", str(req.get("path") or "")[:120], dry_run=bool(req.get("dry_run", True)))
            return self._send(200, json.dumps(res, ensure_ascii=False))
        if self.path == "/api/pc/startup":
            import pc_guard
            dry = bool(req.get("dry_run", True))
            kind = str(req.get("action") or "disable")
            res = pc_guard.startup_action(str(req.get("where") or ""), str(req.get("name") or ""),
                                          str(req.get("command") or ""),
                                          enable=(kind == "restore"), dry_run=dry)
            audit("pc_startup", kind, name=str(req.get("name") or ""), dry_run=dry, ok=bool(res.get("ok")))
            return self._send(200, json.dumps(res, ensure_ascii=False))
        if self.path == "/api/pc/action":
            import pc_guard
            dry = bool(req.get("dry_run", True))
            res = pc_guard.act(str(req.get("action") or ""), dry_run=dry,
                               **{k: v for k, v in req.items() if k not in ("action", "dry_run")})
            audit("pc_action", str(req.get("action")), dry_run=dry, ok=bool(res.get("ok")))
            return self._send(200, json.dumps(res, ensure_ascii=False))

        # ---- Obsidian 插件专用（写接口，要令牌；令牌文件在 plugin_token.txt）
        if self.path == "/api/vault/index":
            idx = req.get("index") or {}
            files = idx.get("files") or []
            rec = {"ts": now_iso(), "vault": idx.get("vault"), "count": idx.get("count", len(files)),
                   "files": files[:4000], "stats": idx.get("stats") or {}}
            try:
                VAULT_INDEX.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}))
            audit("vault_index", str(rec.get("count")), vault=rec.get("vault"))
            return self._send(200, '{"ok":true}')
        if self.path == "/api/vault/event":
            ev = req.get("event") or req
            rec = {"ts": now_iso(), "source": "obsidian"}
            rec.update({k: ev.get(k) for k in ("type", "path", "oldPath", "size", "mtime") if k is not None})
            try:
                with VAULT_EVENTS.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if VAULT_EVENTS.stat().st_size > 8 * 1024 * 1024:      # 超 8MB 留最后 2000 行
                    lines = VAULT_EVENTS.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
                    VAULT_EVENTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}))
            return self._send(200, '{"ok":true}')
        if self.path == "/api/vault/check":
            chk = req.get("checks") or {}
            rec = {"ts": now_iso(), "summary": chk.get("summary") or {}, "items": (chk.get("items") or [])[:300]}
            try:
                VAULT_CHECKS.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)}))
            s_ = rec["summary"]
            bad = sum(int(s_.get(k) or 0) for k in ("broken_links", "missing_frontmatter", "inbox_stale", "uncommitted"))
            if bad:
                notify("监督者", "Obsidian 库体检发现 %d 处待处理" % bad)
            audit("vault_check", str(bad), **{k: str(v)[:40] for k, v in s_.items()})
            return self._send(200, '{"ok":true}')
        if self.path == "/api/vault/command-result":
            memo = {"ts": now_iso(), "source": "obsidian", "type": "command_result",
                    "path": req.get("path"), "command": req.get("action"),
                    "ok": bool(req.get("ok")), "detail": str(req.get("detail") or "")[:400]}
            try:
                with VAULT_EVENTS.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(memo, ensure_ascii=False) + "\n")
            except Exception:
                pass
            return self._send(200, '{"ok":true}')
        if self.path == "/api/vault/command":
            return self._send(200, json.dumps(vault_command(str(req.get("action") or ""),
                                                           **{k: v for k, v in req.items() if k != "action"}),
                                              ensure_ascii=False))
        if self.path == "/api/dismiss-correction":
            dismiss_corrections(str(req.get("turn") or ""))
            audit("correction_dismissed", str(req.get("turn") or ""))
            return self._send(200, '{"ok":true}')
        if self.path == "/api/apply-change":
            ok, info = apply_change(str(req.get("id") or ""), by="你（网页接口）")
            return self._send(200 if ok else 404, json.dumps({"ok": ok, "info": info}, ensure_ascii=False))
        if self.path == "/api/reject-change":
            reject_change(str(req.get("id") or ""), why=str(req.get("why") or ""))
            return self._send(200, '{"ok":true}')
        if self.path == "/api/conv":
            cid = str(req.get("id") or ""); act = str(req.get("action") or "")
            if act not in ("close",) or not cid:
                return self._send(400, '{"ok":false,"error":"参数不对"}')
            title = str(req.get("title") or "") or next(
                (x["title"] for x in threads_list() if x["id"] == cid), "")
            conv_action(cid, act, title)
            audit("conv_" + act, cid)
            return self._send(200, '{"ok":true}')
        want = next((h for h in ct.list_handlers() if h["key"] == key), None)
        if not want:
            return self._send(404, '{"ok":false,"error":"未知的小工具"}')
        if self.path in ("/api/approve", "/api/trust"):
            set_trust(key, want["current_hash"])
            resolve_request(key, "approve")
            audit("trust", key, event_label=want["event_label"], trusted_hash=want["current_hash"])
            return self._send(200, '{"ok":true}')
        if self.path == "/api/deny":
            resolve_request(key, "deny")
            audit("deny", key, event_label=want["event_label"])
            return self._send(200, '{"ok":true}')
        if self.path == "/api/revoke":
            revoke_trust(key); audit("revoke", key, event_label=want["event_label"])
            return self._send(200, '{"ok":true}')
        self._send(404, '{"ok":false}')


def main():
    audit("app_start", "-", host=HOST, port=PORT)
    write_plugin_token()
    threading.Thread(target=monitor_loop, daemon=True).start()
    export_corrections()
    threading.Thread(target=judge_loop, daemon=True).start()
    url = f"http://{HOST}:{PORT}/"
    print(f"监督者已启动: {url}")
    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, PORT), H) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
