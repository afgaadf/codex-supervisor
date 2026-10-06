# -*- coding: utf-8 -*-
"""learn.py —— 监督者的学习内核（v1）

怎么学（全部有据可查，不猜）：
  1) 你每次「划掉责令改正」「拒绝提案」→ 记一条教训（谁、哪条规则、为什么）
  2) 教训在下一轮判分时随提示词一起喂回去（避免重复冤枉）
  3) 自己扫日志找模式（同一规则被划掉≥2次、同一文件反复改、同类工具错误反复出现）
     → 写成「候选教训」，**要人点头**才生效
  4) 出指标：判定轮数 / 违规数 / 被划掉数 → 误报率（人能看懂的那一个数）
"""
from __future__ import annotations
import json, time
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LESSONS = APP_DIR / "lessons.jsonl"
CANDS = APP_DIR / "learn_candidates.json"
JUDGE = APP_DIR / "judgments.jsonl"
CHANGES_LOG = APP_DIR / "changes" / "log.jsonl"
EVENTS = Path.home() / ".codex" / "anti-degradation" / "state" / "events.jsonl"


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _tail(path, n):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except Exception:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def add_lesson(kind, rule, why="", source="human", extra=None):
    """记一条教训。kind: dismiss（你划掉了）/ reject（你拒绝了）/ adopted（你采纳了候选）/ mined（自己扫出来的）"""
    rec = {"ts": _now(), "kind": str(kind), "rule": str(rule)[:160], "why": str(why)[:400],
           "source": str(source)}
    if extra:
        rec.update({k: v for k, v in extra.items() if k not in rec})
    try:
        with LESSONS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


def lessons(n=200):
    return _tail(LESSONS, n)


def lessons_for_prompt(n=8):
    """给判分器用：只挑"别重犯"的那几类，简短。"""
    rows = [x for x in lessons(80) if x.get("kind") in ("dismiss", "reject", "adopted", "mined")]
    return rows[-n:]


def load_candidates():
    try:
        d = json.loads(CANDS.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_candidates(rows):
    try:
        CANDS.write_text(json.dumps(rows[-200:], ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def learn_scan():
    """自己扫日志找模式 → 候选教训（不自动生效）。"""
    rows = _tail(JUDGE, 4000)
    from collections import Counter
    dismissed = [r for r in rows if r.get("action") == "dismiss"]
    judged = [r for r in rows if r.get("action") == "judged"]
    viol_by_rule = Counter()
    for r in judged:
        for v in (r.get("violations") or []):
            viol_by_rule[str(v.get("rule") or "")[:80]] += 1
    # 模式一：同一条规则被划掉 ≥2 次 → 建议收紧（可能误报）
    cands = []
    seen = {c.get("id") for c in load_candidates()}
    c1 = Counter(str(r.get("rule") or r.get("turn"))[:80] for r in dismissed)
    for rule, cnt in c1.items():
        if cnt >= 2 and rule:
            cid = "tighten:" + rule[:40]
            if cid in seen:
                continue
            cands.append({"id": cid, "kind": "tighten_rule", "title": "「%s」被划掉 %d 次" % (rule[:40], cnt),
                          "why": "同一类问题你多次划掉，说明这条判据太宽/太容易冤枉，建议收紧或加例外。",
                          "evidence": "judgments.jsonl 里 dismiss ×%d" % cnt,
                          "action": "把该规则的触发条件写细，或补一条例外"})
    # 模式二：同一文件反复改 → 返工
    edits = Counter()
    for r in _tail(CHANGES_LOG, 400):
        for f in (r.get("files") or []):
            edits[str(f)] += 1
    for f, cnt in edits.most_common(3):
        if cnt >= 4:
            cid = "rework:" + f[:40]
            if cid in seen:
                continue
            cands.append({"id": cid, "kind": "rework", "title": "「%s」改了 %d 次" % (Path(f).name, cnt),
                          "why": "同一文件反复改＝没先读清楚/没定标准。先读、先定，再动手。",
                          "evidence": "changes/log.jsonl 里出现 %d 次" % cnt,
                          "action": "下次动这个文件前先通读一遍，并写一句「要改什么、改成什么」"})
    # 模式三：工具错误反复
    errs = Counter()
    for e in _tail(EVENTS, 3000):
        if str(e.get("kind")) == "tool_error":
            errs[str(e.get("tool") or e.get("name") or "未知")[:40]] += 1
    for name, cnt in errs.most_common(2):
        if cnt >= 5:
            cid = "toolerr:" + name
            if cid in seen:
                continue
            cands.append({"id": cid, "kind": "tool_error", "title": "「%s」报错 %d 次" % (name, cnt),
                          "why": "同一工具反复报错＝用法不对或环境缺东西，先查文档再试。",
                          "evidence": "events.jsonl 里 tool_error ×%d" % cnt,
                          "action": "查一次官方用法（T1），把正确调用写进项目笔记"})
    if cands:
        save_candidates(load_candidates() + cands)
    return cands


def metrics():
    rows = _tail(JUDGE, 4000)
    judged = [r for r in rows if r.get("action") == "judged"]
    dismissed = [r for r in rows if r.get("action") == "dismiss"]
    viol = sum(len(r.get("violations") or []) for r in judged)
    ls = lessons(1000)
    adopted = [x for x in ls if x.get("kind") == "adopted"]
    return {"judged_turns": len(judged), "violations": viol, "dismissed": len(dismissed),
            "false_alarm_rate": round(len(dismissed) / viol * 100, 1) if viol else 0.0,
            "lessons": len(ls), "adopted": len(adopted),
            "candidates_pending": len(load_candidates())}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        print(json.dumps({"new_candidates": learn_scan(), "metrics": metrics()}, ensure_ascii=False, indent=1))
    elif cmd == "lessons":
        print(json.dumps(lessons(20), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(metrics(), ensure_ascii=False, indent=1))
