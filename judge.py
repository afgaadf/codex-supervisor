# -*- coding: utf-8 -*-
"""judge.py —— 监督者的「工作监督」判断器。

它盯的是**项目规范**，不是"AI 自己说要做的事"：

  1. 读 `rubrics/_registry.json`（**监管者登记表**）—— 每个监管者 = 一份判据；
  2. 拿**这一轮的内容**去比对每个监管者的触发词：命中了才启用它，没命中就不启用
     （免得拿视频的规范去冤枉"改快捷方式"这类任务）；
  3. 启用的监管者，把它的判据整份喂给模型，核对 AI **这一轮的实际动作**，报四轴问题：
        A 合理规范 / B 偷懒糊弄 / C 舍近求远 / D 联网学习
  4. 一个监管者都没命中时，退化成**通用核对**（只说没做 / 敷衍），不套领域规范。

原则（不变）：**只报能从材料里指出证据的违规**；没证据不报（宁可漏，不许冤枉）。

时间语义（避免冤枉）：
  ·【已声称完成 done_claim】= "已完成 / 已核对 / 已修复 / ✅ …" —— 本轮就该有对应动作；说了没做 = 报。
  ·【计划待办 plan】          = "下一步 / 我会 / 待办 …" —— 本轮还没轮到，**不报**。

实现注意（踩过的坑，别删）：
  · 本机网关的 deepseek 模型会先"想"很多；提示词一旦又长又绕，推理就把输出预算烧光，
    结果 status=incomplete、正文为空 —— 判断器"看着在跑，其实什么都没判"。所以：
    提示词保持**短而直接**，显式压低推理强度（reasoning.effort），并留足 max_output_tokens；
    拿不到有效 JSON（正文为空 / 被截断 / 网关抖动）时，用更低的推理强度重试一次。
  · **Python 字符串里不许出现未转义的双引号**（真踩过：整份文件语法崩了，判断器静默死掉）。
"""
from __future__ import annotations
import json, os, re, time, urllib.request
from pathlib import Path
from urllib.parse import urlparse

from paths import CODEX_HOME as CODEX, RUBRIC_DIR, seed_rubrics, DATA_DIR, APP_DIR
import failure_modes as FM
import checkers as CK

# 判据走可写副本（首次运行由 seed_rubrics 播种）
seed_rubrics()
REGISTRY = RUBRIC_DIR / "_registry.json"
LOG_DIR = DATA_DIR / "logs"
RUBRIC_MAX = int(os.environ.get("SUP_JUDGE_RUBRIC_MAX") or 5200)

# 推理强度 / 输出预算 / 超时：可用环境变量覆盖（自检用）
EFFORT = os.environ.get("SUP_JUDGE_EFFORT", "low")
MAX_TOKENS = int(os.environ.get("SUP_JUDGE_MAX_TOKENS") or 3000)
TIMEOUT = int(os.environ.get("SUP_JUDGE_TIMEOUT") or 90)

_COMMON = (
    "【这一轮实际做的事】那份清单是系统给的**完整**工具调用记录：\n"
    "  · 写了「完整清单，无省略」→ 清单里没有的动作，才可以断定它没做。\n"
    "  · 写了「中间省略 N 条」→ 省略的部分**不许当成没做**，只能存疑，别下结论。\n"
    "  · 每行末尾的「涉及文件:」是这次调用碰过的文件，核对产物时以它为准。\n"
    "铁律：\n"
    "  1) **只报你能从材料里指出证据的问题**；指不出证据就别报（宁可漏，不许冤枉）。\n"
    "  2) 计划类（下一步/我会/待办）**不报**；只报「声称已完成，却没有对应动作」的。\n"
    "必答四问（每条都要给结论，能指出证据才下判断）：\n"
    "  ① 联网了吗？涉及规格/版本/API/标准/官方要求/数值时必须查，没查=违规；\n"
    "  ② 查库了吗？涉及笔记/知识库/资料/规范时必须先查库再写，没查=违规；\n"
    "  ③ 偷懒了吗？声称做完却没有对应动作、省工序、拿「说了」当「做了」=违规；\n"
    "  ④ 降智了吗？返工、工具错误堆积、绕圈、上下文膨胀=风险。\n"
)

_SYS_DOMAIN = (
    "你是「工作监督员」。现已启用的监管者（判据）如下。你按这些判据核对 AI **这一轮的实际动作**，\n"
    "只从四个方向找问题：\n"
    "  A 合理规范 —— 没按标准做（跳关卡、数值不达标、没出处）；\n"
    "  B 偷懒糊弄 —— 省工序、抽帧代全量、拿「说了」当「做了」、交付让人看不到、只给路径；\n"
    "  C 舍近求远 —— 库里有现成的却自己造、该先读的没读、同一件事反复瞎试；\n"
    "  D 联网学习 —— 涉及平台规格/版本/API/权威出处却凭记忆断言，没真去查。\n"
    + _COMMON +
    "  3) 每条问题都要能指到判据编号（如 B1 / A3 / C2）。\n"
    "  4) **只有当【这一轮实际做的事】确实是在做视频制作时才套判据**；\n"
    "     如果这一轮是在做工具、写文档、改设置、或只是在讨论视频，就不要套（否则会冤枉）。\n"
    "  5) **D 轴按【监督者自己联网核到的】材料判**：AI 自己说查过不算数。\n"
    "     ① 涉及规格/版本/API/标准/官方要求/具体数值而本轮没有检索动作 → 必报；\n"
    "     ② 关键数值没有 T1 一手权威来源（官方文档/标准组织/官方公告）→ 必报「未充分查证」；\n"
    "     ③ AI 的数值与材料冲突 → 必报，fix 里给材料中的正确值 + 来源 URL；\n"
    "     ④ 材料没覆盖的点不许编，只能写「没查到一手来源」。\n"
    "只输出 JSON。"
)

_SYS_GENERIC = (
    "你是「工作监督员」。本轮**没有命中任何监管者**，所以**不要**套用领域规范（会冤枉）。\n"
    "你只做一件事：核对 AI 有没有「说了做完却没做」或明显敷衍。\n"
    + _COMMON +
    "只输出 JSON。"
)


# ---------------------------------------------------------------- 监管者登记表
def _registry():
    try:
        d = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"supervisors": []}
    return d if isinstance(d, dict) else {"supervisors": []}


def _enabled():
    out = []
    for s in (_registry().get("supervisors") or []):
        if isinstance(s, dict) and s.get("enabled", True) and s.get("id"):
            out.append(s)
    return out


def pick_supervisors(text):
    """拿这一轮的内容去比对触发词：命中才启用。返回 [(supervisor), ...]。"""
    t = str(text or "").lower()
    hit = []
    for s in _enabled():
        for w in (s.get("triggers") or []):
            w = str(w).strip().lower()
            if w and w in t:
                hit.append(s)
                break
    return hit


def _rubric_text(sups):
    """把命中的监管者的判据拼起来（人改判据文件，下一轮就生效）。"""
    parts = []
    for s in sups:
        f = RUBRIC_DIR / str(s.get("file") or "")
        try:
            t = f.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            t = ""
        if t:
            parts.append("### 监管者：%s（%s）\n%s" % (s.get("name") or s.get("id"), s.get("id"), t))
    if not parts:
        return "（监管者判据文件读不到。）"
    return ("\n\n".join(parts))[:RUBRIC_MAX]


# ---------------------------------------------------------------- 网关
def _llm(prompt, max_tokens=None, effort=None, timeout=None):
    """调用本机模型网关（cc-switch 代理）。"""
    import tomllib
    cfg = tomllib.loads((CODEX / "config.toml").read_text(encoding="utf-8"))
    prov = (cfg.get("model_providers") or {}).get("custom") or {}
    base = prov.get("base_url") or ""
    model = cfg.get("model") or "gpt-4o-mini"
    key = ""
    try:
        key = json.loads((CODEX / "auth.json").read_text(encoding="utf-8")).get("OPENAI_API_KEY") or ""
    except Exception:
        pass
    if not base:
        raise RuntimeError("没配模型网关")
    body = {"model": model, "input": prompt,
            "max_output_tokens": int(max_tokens or MAX_TOKENS),
            "reasoning": {"effort": effort or EFFORT}}
    req = urllib.request.Request(base.rstrip("/") + "/responses", data=json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    r = json.loads(urllib.request.urlopen(req, timeout=timeout or TIMEOUT).read())
    out = ""
    for item in (r.get("output") or []):
        if item.get("type") != "message":
            continue
        for c in (item.get("content") or []):
            if c.get("type") in ("output_text", "text"):
                out += c.get("text", "")
    return out


def _json_of(txt):
    m = re.search(r"\{[\s\S]*\}", txt or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _judge_json(prompt):
    """拿不到有效 JSON 就降推理强度重试一次（空正文 / 被截断 / 网关抖动）。"""
    for effort in (EFFORT, "none"):
        try:
            data = _json_of(_llm(prompt, effort=effort))
        except Exception:
            data = {}
        if data:
            return data
    return {}


# ---------------------------------------------------------------- 自己联网查证（D 轴默认动作）
_SEARCHY = ("官方", "规格", "标准", "版本", "文档", "接口", "要求", "参数", "分辨率", "帧率",
            "码率", "响度", "lufs", "协议", "教程", "怎么", "如何", "最新", "推荐",
            "规定", "公告", "政策", "支持", "兼容", "限制", "上限", "价格", "额度", "api")
_SEARCH_TOOLS = ("web_search", "search(", "lite.duckduckgo.com", "duckduckgo", "browser",
                 "fetch_text", "open_url", "fetch(", "curl ", "invoke-webrequest",
                 "requests.get", "research(")


def _looks_verifiable(text):
    """这一轮内容像不像"该联网核实的外部事实"（规格/官方/版本/数值）。"""
    t = str(text or "").lower()
    if any(w in t for w in _SEARCHY):
        return True
    return bool(re.search(r"\d+\s*(kbps|mbps|fps|lufs|px|hz|bit|ms|秒|帧)", t))


def _did_search(actions_text):
    """AI 自己这一轮有没有真去检索：既要看到检索工具，也要看到查询词/网址。"""
    a = str(actions_text or "").lower()
    if not any(w in a for w in _SEARCH_TOOLS):
        return False
    return bool(re.search(r"https?://|query|q=|搜索|检索|查证|duckduckgo", a))


# ---------------------------------------------------------------- 来源分级（T1 一手权威才算证据）
_DEFAULT_SOURCES = {
    "aliases": {},
    "t1_domains": ["douyin.com", "bytedance.com", "w3.org", "itu.int", "ebu.ch", "unicode.org",
                   "khronos.org", "ffmpeg.org", "python.org", "docs.python.org", "microsoft.com",
                   "learn.microsoft.com", "developer.mozilla.org", "apple.com",
                   "developers.google.com", "adobe.com", "nvidia.com", "iso.org", "iec.ch",
                   "openai.com", "platform.openai.com", "anthropic.com", "docs.anthropic.com"],
    "t2_domains": ["stackoverflow.com", "github.com", "medium.com", "infoq.cn", "oschina.net",
                   "developer.aliyun.com", "cloud.tencent.com", "segmentfault.com"],
    "t3_domains": ["baike.baidu.com", "wikipedia.org", "zhihu.com", "csdn.net", "juejin.cn",
                   "sohu.com", "sina.com.cn", "sina.cn", "toutiao.com", "bilibili.com",
                   "36kr.com", "toolbox365.cn", "myyinghe.com"],
}
_TIER_NAME = {1: "T1 一手权威", 2: "T2 二手专业", 3: "T3 百科/聚合", 4: "T4 未分级"}


def _sources_cfg():
    cfg = {k: list(v) for k, v in _DEFAULT_SOURCES.items()}
    try:
        d = json.loads((RUBRIC_DIR / "_sources.json").read_text(encoding="utf-8"))
        if isinstance(d, dict):
            for k in ("t1_domains", "t2_domains", "t3_domains"):
                if isinstance(d.get(k), list):
                    cfg[k] = [str(x).strip().lower() for x in d[k] if str(x).strip()]
            if isinstance(d.get("aliases"), dict):
                cfg["aliases"] = {str(k).lower(): str(v).strip().lower()
                                  for k, v in d["aliases"].items() if str(v).strip()}
    except Exception:
        pass
    return cfg


_SRC = _sources_cfg()


def reload_sources():
    """人改了 rubrics/_sources.json 之后，不重启也能生效。"""
    global _SRC
    _SRC = _sources_cfg()
    return _SRC


def _host_path(url):
    try:
        u = urlparse(str(url))
        return ((u.netloc or "").lower().split("@")[-1].split(":")[0].removeprefix("www."),
                (u.path or "").lower())
    except Exception:
        return "", ""


def tier_of(url):
    """来源分级：T1 一手权威 / T2 二手专业 / T3 百科·聚合 / T4 未分级。

    名单里带路径的（如 github.com/ffmpeg/ffmpeg）按 主机+路径 前缀匹配，用来认官方仓库。
    """
    h, p = _host_path(url)
    if not h:
        return {"tier": 4, "name": _TIER_NAME[4]}
    for d in _SRC["t1_domains"]:
        if "/" in d:
            if (h + p).startswith(d):
                return {"tier": 1, "name": _TIER_NAME[1]}
        elif h == d or h.endswith("." + d):
            return {"tier": 1, "name": _TIER_NAME[1]}
    for d in _SRC["t3_domains"]:
        if h == d or h.endswith("." + d):
            return {"tier": 3, "name": _TIER_NAME[3]}
    for d in _SRC["t2_domains"]:
        if h == d or h.endswith("." + d):
            return {"tier": 2, "name": _TIER_NAME[2]}
    return {"tier": 4, "name": _TIER_NAME[4]}


_STOP = set(("的 了 吗 呢 吧 是 要 我 你 他 她 它 们 这 那 和 与 及 或 者 把 被 给 对 在 为 从 到 "
             "一个 都 也 就 还 但 而 所以 如果 因为 可以 这个 那个 怎么 如何 请 问 一下 有 没有 "
             "不 用 好 能 需 需要 什么 哪个").split())


def _keywords(text, n=8):
    out, seen = [], set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9\.\-_]{2,}|[\u4e00-\u9fff]{2,6}|\d+(?:\.\d+)?", str(text or "")):
        lw = w.lower()
        if lw in _STOP or lw in seen or len(lw) < 2:
            continue
        seen.add(lw)
        out.append(w)
        if len(out) >= n:
            break
    return out


def extract_queries(ask, say, limit=2):
    qs = []
    a = re.sub(r"\s+", " ", str(ask or "")).strip()
    if a:
        qs.append(a[:100])
    kw = _keywords(say)
    if kw:
        qs.append(" ".join(kw))
    return qs[:limit]


def site_queries(text, extra=""):
    """问题里出现品牌别名时，额外打一次 site:<官方域名>，尽量拿到 T1 一手来源。"""
    low = str(text or "").lower()
    kw = " ".join(_keywords(str(text or "") + " " + str(extra or ""), 6))
    out = []
    for name, dom in (_SRC.get("aliases") or {}).items():
        if name and name in low and kw:
            q = "site:%s %s" % (dom, kw)
            if q not in out:
                out.append(q)
    return out[:2]


def must_verify(ask, say, sups=None):
    """该不该联网核实。

    H10「每轮必联网」（用户 2026-10-07 要求）：**每一轮都先查一遍**，
    哪怕结论是"本轮无需外部事实"，也要有这一次核实动作留痕。
    """
    return True


_ONLINE_CACHE = LOG_DIR / "online_cache.jsonl"
ONLINE_TTL = int(os.environ.get("SUP_ONLINE_TTL") or 6 * 3600)


def _cache_get(q):
    try:
        for line in reversed(_ONLINE_CACHE.read_text(encoding="utf-8").splitlines()[-300:]):
            r = json.loads(line)
            if r.get("query") == q and (time.time() - float(r.get("ts_epoch") or 0)) < ONLINE_TTL:
                return r.get("result")
    except Exception:
        pass
    return None


def _cache_put(q, result):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _ONLINE_CACHE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"query": q, "ts_epoch": time.time(), "result": result},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 四个必答（确定性检查）
_VAULT_HINTS = ("Obsidian Vault", "00_Inbox", "10_笔记", "20_附件", "30_模板", "90_归档",
                "obsidian", "双链", "frontmatter", "moc", "笔记", "知识库", "归档", "wiki", "vault")
_LAZY_CLAIMS = ("已完成", "已修复", "已核对", "已改好", "已经完成", "已更新", "已经修好", "搞定了", "完成度")
_ACT_HINTS = ("write", "edit", "patch", "command", "shell", "ffmpeg", "python ", "git ",
              "mkdir", "copy", "move", "apply", "set-content", "remove-item", "invoke-")


def check_vault(ask, actions, say):
    """查库了吗：这轮涉及库就要求动作里真碰过库文件。"""
    txt = ("%s %s" % (ask or "", say or "")).lower()
    needed = any(h.lower() in txt for h in _VAULT_HINTS)
    act = str(actions or "")
    did = bool(re.search(r"Obsidian Vault|00_Inbox|10_笔记|20_附件|30_模板|90_归档|vault_index|vault_checks", act, re.I))
    return {"needed": needed, "did": did,
            "verdict": ("查了库" if did else "没查库") if needed else "这轮不涉及库",
            "evidence": ("动作里碰过库文件" if did else ("这轮该查库，动作里没碰" if needed else ""))}


def check_lazy(ask, actions, say):
    """偷懒了吗：声称做完，但动作里没有任何落地操作。"""
    s_ = str(say or "")
    claimed = [w for w in _LAZY_CLAIMS if w in s_]
    act = str(actions or "").lower()
    acted = any(w in act for w in _ACT_HINTS)
    lazy = bool(claimed) and not acted
    return {"claimed": bool(claimed), "acted": acted, "lazy": lazy,
            "verdict": "说了没做" if lazy else ("有说也有做" if claimed else "没声称完成"),
            "evidence": ("说：" + "、".join(claimed[:3])) if claimed else ""}


def check_online(ask, actions, say, sups=None):
    """**监督者自己联网核一遍**，并把来源按 T1–T4 分级。

    AI 自己说"我查过"不算核实 —— 只有这里带回来的材料才算。
    """
    ai_searched = _did_search(actions)
    needed = must_verify(ask, say, sups)
    out = {"needed": bool(needed), "did": ai_searched, "ai_searched": ai_searched}
    if not needed:
        return out
    queries = [q for q in extract_queries(ask, say) if q]
    attempts = []
    for q in site_queries((ask or "") + " " + (say or "")) + queries:
        if q and q not in attempts:
            attempts.append(q)
    attempts = attempts[:3]
    out["queries"] = attempts
    res, used, sources, seen = None, "", [], set()
    for qq in attempts:
        r = _cache_get(qq)
        if r is None:
            try:
                import research as R
                r = R.research(qq, fetch_n=2)
            except Exception as e:
                r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            _cache_put(qq, r)
        got_t1 = False
        for x in ((r or {}).get("sources") or []):
            url = x.get("url") if isinstance(x, dict) else str(x)
            if not url or url in seen:
                continue
            seen.add(url)
            t = tier_of(url)
            sources.append({"id": (x.get("id") if isinstance(x, dict) else None),
                            "title": (x.get("title") if isinstance(x, dict) else ""),
                            "url": url, "tier": t["tier"], "tier_name": t["name"],
                            "query": qq})
            if t["tier"] == 1:
                got_t1 = True
        if r and r.get("ok") and not used:
            res, used = r, qq
        if got_t1:
            res, used = r, qq
            break
    res = res or {}
    out.update({"query": used, "ok": bool(res.get("ok")), "answer": res.get("answer") or "",
                "gaps": res.get("gaps") or [], "error": res.get("error")})
    counts = {}
    for s in sources:
        counts["T%d" % s["tier"]] = counts.get("T%d" % s["tier"], 0) + 1
    out["sources"] = sources
    out["tier_counts"] = counts
    out["first_party"] = counts.get("T1", 0) > 0
    if not sources:
        out["verdict"] = "没查到可用来源"
    elif out["first_party"]:
        out["verdict"] = "有一手权威来源（T1×%d）" % counts.get("T1", 0)
    else:
        out["verdict"] = "只有二手/百科（无 T1），未达证据线"
    return out


def _online_block(online):
    if not online or not online.get("needed"):
        return ""
    lines = ["", "============== 监督者自己联网核到的（**只有这份算核实过**）=============="]
    if online.get("queries"):
        lines.append("检索词：" + " ｜ ".join(str(x) for x in online["queries"][:2]))
    if online.get("ok"):
        lines.append("结论：%s" % (online.get("answer") or ""))
        for x in (online.get("sources") or [])[:5]:
            lines.append("[%s] %s — %s" % (x.get("tier_name"), (x.get("title") or "")[:60], x.get("url")))
        c = online.get("tier_counts") or {}
        lines.append("来源分级：T1 一手权威 %d 条 · T2 二手 %d · T3 百科/聚合 %d · T4 未分级 %d"
                     % (c.get("T1", 0), c.get("T2", 0), c.get("T3", 0), c.get("T4", 0)))
        lines.append("证据判定：%s" % (online.get("verdict") or ""))
        if online.get("gaps"):
            lines.append("材料没覆盖：" + "；".join(str(g) for g in online["gaps"][:4]))
    else:
        lines.append("（监督者去查了，但没查成：%s）" % (online.get("error") or "未知原因"))
    lines.append("AI 这一轮%s检索动作。" % ("自己也做过" if online.get("ai_searched") else "**没有**任何"))
    lines.append("D 轴判定规则（照这个判，不许凭记忆放行）：")
    lines.append("  ① 涉及平台规格/版本/API/标准/官方要求/具体数值，本轮没有检索 → 必报；")
    lines.append("  ② 关键数值**没有 T1 一手权威来源**（官方文档/标准组织/官方公告）→ 必报「未充分查证」；")
    lines.append("  ③ AI 的数值与上面材料冲突 → 必报，fix 用材料里的正确值 + 来源 URL；")
    lines.append("  ④ 材料没覆盖的点不许编，只能写「没查到一手来源」。")
    lines.append("============== 联网材料结束 ==============")
    return "\n".join(lines) + "\n"


def _lessons_block():
    """把人教过的（划掉/拒绝/采纳的教训）喂回判分器，避免重复冤枉。"""
    try:
        import learn as L
        rows = L.lessons_for_prompt(8)
    except Exception:
        return ""
    if not rows:
        return ""
    lines = ["", "============== 监督者自己学到的（别重犯）=============="]
    for r in rows:
        why = str(r.get("why") or "")[:120]
        lines.append("- [%s] %s%s" % (r.get("kind"), str(r.get("rule"))[:80], ("：" + why) if why else ""))
    lines.append("规矩：上面这些是「人被冤枉过 / 提案被拒过」的记录；同类情况不要重复报，除非有新证据。")
    lines.append("============== 教训结束 ==============")
    return "\n".join(lines) + "\n"


def _build_prompt(ask, actions, say, sups, online=None):
    if sups:
        head = (_SYS_DOMAIN + "\n\n"
                "================ 《判据》（人在 rubrics/ 里维护）================\n"
                + _rubric_text(sups) + "\n"
                + "================ 判据结束 ================\n\n")
    else:
        head = (_SYS_GENERIC + "\n\n"
                "【说明】本轮没命中监管者，所以没有判据清单，只做通用核对。\n\n")
    return (
        head
        + "【用户这一轮的要求】\n" + (ask or "")[:1500] + "\n\n"
        + "【这一轮实际做的事】\n" + (actions or "")[:6000] + "\n\n"
        + "【它最后对用户说的话】\n" + (say or "")[:4000] + "\n\n"
        + FM.render_for_prompt((ask or "") + "\n" + (actions or "") + "\n" + (say or ""), limit=8)
        + _online_block(online) + _lessons_block() + "\n"
        + '只输出 JSON：{"checklist":[{"item":"短标签","type":"done_claim|plan"}],'
          '"behavior":{"online":{"verdict":"是|否|不适用","evidence":"哪句话/哪个动作"},'
          '"vault":{"verdict":"是|否|不适用","evidence":"..."},'
          '"lazy":{"verdict":"是|否|不适用","evidence":"..."},'
          '"degrade":{"verdict":"是|否|不适用","evidence":"..."}},'
          '"violations":[{"axis":"A 合理规范|B 偷懒糊弄|C 舍近求远|D 联网学习",'
          '"rule":"判据编号+短标签(如 B1 全量检查)","evidence":"材料里哪句/哪个动作",'
          '"fix":"怎么改"}]}\n'
          'behavior 四问的语义：online=它这轮**自己**有没有真去联网查（不是我替它查）；'
          'vault=涉及笔记/资料时它有没有真去查库；lazy=有没有"说了没做/省工序"；'
          'degrade=有没有返工/绕圈/工具错误堆积等降智迹象。'
          'verdict 只能是 是/否/不适用；evidence 必须引用材料原文，指不出就写"不适用"。'
          '没有违规就 "violations":[]。'
    )


def _parse_behavior(data):
    out = {}
    b = (data or {}).get("behavior") or {}
    for k in ("online", "vault", "lazy", "degrade"):
        v = b.get(k) or {}
        if isinstance(v, str):
            v = {"verdict": v}
        out[k] = {"verdict": str(v.get("verdict") or "").strip()[:12],
                  "evidence": str(v.get("evidence") or "").strip()[:300],
                  "by": "model"}
    return out


def _parse(data):
    checklist = []
    for c in (data.get("checklist") or []):
        if isinstance(c, dict):
            item = str(c.get("item") or c.get("label") or "").strip()
            if item:
                ty = str(c.get("type") or "").strip().lower()
                checklist.append({"item": item[:40],
                                  "type": ty if ty in ("done_claim", "plan") else "plan"})

    out = []
    for v in (data.get("violations") or []):
        if not isinstance(v, dict):
            continue
        rule = str(v.get("rule") or "").strip()
        if not rule:
            continue
        axis = str(v.get("axis") or "").strip()
        tag = ("【%s】" % axis[:12]) if axis else ""   # 轴拼进 rule，界面不用改就能显示
        out.append({"rule": (tag + rule)[:160],
                    "axis": axis,
                    "evidence": str(v.get("evidence") or "")[:400],
                    "fix": str(v.get("fix") or "")[:400]})
    return out, checklist


# ---------------------------------------------------------------- 自动检查器
def _auto_violations(ask, actions, say):
    """把 checkers.py 的确定性检测结果转成违规项（规则标签带【自动】）。"""
    try:
        findings = CK.check_turn(ask, actions, say)
    except Exception:
        return []
    if not findings:
        return []
    fixmap = {}
    try:
        for m in FM.load_modes():
            fixmap[m.get("id")] = m.get("fix") or ""
    except Exception:
        pass
    out = []
    for f in findings:
        out.append({
            "rule": "[自动]" + str(f.get("title"))[:60],
            "axis": "自动检查",
            "evidence": ("；".join(f.get("items") or []))[:400],
            "fix": str(fixmap.get(f.get("id")) or "")[:400],
        })
    return out


def _merge_violations(primary, extra):
    """按 rule 去重合并，自动检查在前（确定性优先）。"""
    seen, out = set(), []
    for v in list(extra or []) + list(primary or []):
        key = str(v.get("rule") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def judge_turn_ex(ask, actions, say):
    """完整结果：{violations, checklist, supervisors}。supervisors 用于界面显示 + 通知。"""
    blob = " ".join([str(ask or ""), str(say or ""), str(actions or "")])
    sups = pick_supervisors(blob)
    online = check_online(ask, actions, say, sups)
    data = _judge_json(_build_prompt(ask, actions, say, sups, online))
    vs, ck = _parse(data)
    vs = _merge_violations(vs, _auto_violations(ask, actions, say))
    return {"violations": vs, "checklist": ck, "behavior": _parse_behavior(data),
            "supervisors": [{"id": s.get("id"), "name": s.get("name") or s.get("id")} for s in sups],
            "online": {"needed": bool(online.get("needed")),
                       "ai_searched": bool(online.get("ai_searched")),
                       "i_checked": bool(online.get("needed")),
                       "ok": bool(online.get("ok")),
                       "verdict": online.get("verdict") or "",
                       "first_party": bool(online.get("first_party")),
                       "queries": online.get("queries") or [],
                       "tier_counts": online.get("tier_counts") or {},
                       "gaps": online.get("gaps") or [],
                       "sources": [{"title": x.get("title"), "url": x.get("url"),
                                    "tier": x.get("tier"), "tier_name": x.get("tier_name")}
                                   for x in (online.get("sources") or [])]}}


def judge_turn_full(ask, actions, say):
    """返回 (violations, checklist)。保持旧签名。"""
    r = judge_turn_ex(ask, actions, say)
    return r["violations"], r["checklist"]


def judge_turn(ask, actions, say):
    """返回违规列表（可能为空）。保持旧签名，界面/注入端无需改动。"""
    try:
        return judge_turn_ex(ask, actions, say)["violations"]
    except Exception:
        return []