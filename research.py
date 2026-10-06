# -*- coding: utf-8 -*-
"""research.py —— 监督者的「联网思考」。

它解决一件事：**该查的时候真去查，而且结论要能指到来源。**

做法：DuckDuckGo lite 检索 → 抓取前几篇正文 → 交给模型归纳（**只许依据材料**）→
输出「结论 + 每条结论的来源 URL」；材料不足就明说材料不足，**不许编**。

用法
----
  python research.py "抖音 竖屏视频 上传规格 分辨率 帧率 码率"
  python research.py --json "..."          # 只输出 JSON（给别的程序用）

别的模块怎么用
--------------
  import research
  r = research.research("问题")
  r["answer"] / r["points"][i]["claim"] / r["points"][i]["sources"] / r["sources"]

设计约束
--------
· 只用 **DuckDuckGo lite**（实测本机可达且结果相关；Bing 在这台机器上返回的是别的内容，已弃用）。
· 抓正文时**限长**（默认 5000 字/页，最多 3 页），避免把上下文撑爆。
· 联网失败**不抛异常**给上层，返回 {"ok": False, "error": ...}，让调用方有机会降级。
"""
from __future__ import annotations
import html as _html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from paths import DATA_DIR, APP_DIR

LOG_DIR = DATA_DIR / "logs"
CACHE = LOG_DIR / "research.jsonl"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _get(url, timeout=18):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def _strip(s):
    s = re.sub(r"<(script|style|noscript)[\s\S]*?</\1>", " ", s or "", flags=re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def _ddg_real(href):
    """DDG 的跳转链接 → 真实 URL。"""
    try:
        m = re.search(r"[?&]uddg=([^&]+)", href or "")
        if m:
            return urllib.parse.unquote(m.group(1))
        if href.startswith("//"):
            return "https:" + href
    except Exception:
        pass
    return href


def search(q, n=6):
    """DuckDuckGo lite 检索。返回 [{title,url,snippet}]。"""
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(q)
    h = _get(url)
    out, seen = [], set()
    for m in re.finditer(
            r'<a[^>]+href="([^"]+)"[^>]*class=\'result-link\'[^>]*>([\s\S]*?)</a>', h):
        u = _ddg_real(m.group(1))
        t = _strip(m.group(2))[:160]
        if not u.startswith("http") or u in seen:
            continue
        seen.add(u)
        out.append({"title": t, "url": u, "snippet": ""})
    # 摘要按顺序跟在后面，逐个配
    snips = [_strip(x)[:400] for x in re.findall(r"<td class='result-snippet'>([\s\S]*?)</td>", h)]
    for i, s in enumerate(snips):
        if i < len(out):
            out[i]["snippet"] = s
    return out[:n]


def fetch_text(url, limit=5000):
    """抓正文并压成纯文本（限长）。"""
    try:
        h = _get(url, timeout=20)
    except Exception as e:
        return "（抓取失败：%s）" % e
    t = _strip(h)
    return t[:limit]


_SYS = (
    "你是研究助理。**只依据给定的【搜索结果】回答【问题】**。\n"
    "铁律：\n"
    "  1) 每条结论后面必须标来源编号，如 [1][3]；编号对应材料里的 [1][2]...\n"
    "  2) 材料里没有的，**不许补**；材料不足以回答，就在 answer 里明说『材料不足』。\n"
    "  3) 不要写套话，直接给能用的结论（数值、做法、名字）。\n"
    "只输出 JSON：{\"answer\":\"一段话回答\","
    "\"points\":[{\"claim\":\"一条结论\",\"sources\":[1,2]}],"
    "\"gaps\":[\"材料没覆盖到的点\"]}"
)


def research(q, fetch_n=3, timeout=90):
    """联网研究：检索 → 抓正文 → 归纳。返回 dict（ok=False 表示没成功）。"""
    try:
        hits = search(q, n=max(fetch_n + 2, 6))
    except Exception as e:
        return {"ok": False, "error": "检索失败：%s" % e, "question": q}
    if not hits:
        return {"ok": False, "error": "没搜到结果", "question": q}

    materials = []
    for i, hit in enumerate(hits[:fetch_n], 1):
        body = fetch_text(hit["url"], limit=5000)
        materials.append("[%d] %s\nURL: %s\n摘要: %s\n正文:\n%s"
                         % (i, hit["title"], hit["url"], hit.get("snippet") or "", body[:4000]))
    prompt = (_SYS + "\n\n【问题】\n" + str(q) + "\n\n【搜索结果】\n"
              + "\n\n".join(materials) + "\n\n只输出 JSON。")

    data = {}
    try:
        import judge as J
        for effort in ("low", "none"):
            try:
                raw = J._llm(prompt, max_tokens=2000, effort=effort, timeout=timeout)
                data = J._json_of(raw)
            except Exception:
                data = {}
            if data:
                break
    except Exception as e:
        data = {}
        if not data:
            return {"ok": False, "error": "归纳失败：%s" % e, "question": q,
                    "hits": hits[:fetch_n]}

    points = []
    for p in (data.get("points") or []):
        if isinstance(p, dict) and p.get("claim"):
            ids = [int(x) for x in (p.get("sources") or []) if str(x).isdigit()]
            points.append({"claim": str(p["claim"])[:400],
                           "sources": [{"id": i,
                                        "title": hits[i - 1]["title"] if i - 1 < len(hits) else "",
                                        "url": hits[i - 1]["url"] if i - 1 < len(hits) else ""}
                                       for i in ids]})
    res = {"ok": bool(data), "question": q,
           "answer": str(data.get("answer") or "")[:1500],
           "points": points,
           "gaps": [str(g)[:200] for g in (data.get("gaps") or [])],
           "sources": [{"id": i, "title": h["title"], "url": h["url"]}
                       for i, h in enumerate(hits[:fetch_n], 1)]}
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        with CACHE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "question": q, "ok": res["ok"],
                                "answer": res["answer"][:400],
                                "sources": [s["url"] for s in res["sources"]]},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass
    return res


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("用法: python research.py \"要查的问题\"")
        return 2
    q = " ".join(args)
    r = research(q)
    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    if not r.get("ok"):
        print("没查成：%s" % r.get("error"))
        for s in (r.get("sources") or []) + (r.get("hits") or []):
            print(" -", s.get("title"), s.get("url"))
        return 1
    print("问题：%s\n" % r["question"])
    print(r["answer"] or "（没答出来）")
    if r["points"]:
        print("\n结论与来源：")
        for p in r["points"]:
            src = " ".join("[%d]" % s["id"] for s in p["sources"]) or "（没标来源）"
            print("  · %s  %s" % (p["claim"], src))
    if r["gaps"]:
        print("\n材料没覆盖：")
        for g in r["gaps"]:
            print("  ·", g)
    print("\n来源：")
    for s in r["sources"]:
        print("  [%d] %s\n      %s" % (s["id"], s["title"][:70], s["url"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())