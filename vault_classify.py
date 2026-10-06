# -*- coding: utf-8 -*-
"""vault_classify.py —— 知识库分类（电脑管家负责）

分类规则写在 CLASSIFY 里（路径 + 标题关键词 → 分类），结果：
  · vault_classes.json —— 机器用
  · 库里 10_笔记/知识库/分类索引.md —— 人看（自动生成，可随时重跑）
未分类的会单独列出来，并按库规给出"建议放哪"。
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from paths import VAULT_DIR as V, DATA_DIR, APP_DIR

IDX = DATA_DIR / "vault_index.json"
OUT_JSON = DATA_DIR / "vault_classes.json"

CLASSIFY = [
    ("规范标准", ["规范", "标准", "铁律", "清单", "体系", "指南", "SOP", "手册", "要求", "SOP"]),
    ("项目文档", []),
    ("参考资料", ["参考资料", "网页剪藏", "原始网页", "原始资料"]),
    ("复盘总结", ["复盘", "总结", "报告", "体检"]),
    ("库级文档", ["知识库首页", "使用说明", "使用手册", "AGENTS.md", "CLAUDE.md"]),
    ("学习笔记", ["10_笔记/AI学习", "10_笔记/ai学习", "AI学习"]),
    ("知识条目", ["知识库", "Wiki"]),
    ("产出与报告", ["AI产出"]),
    ("写作", ["写作"]),
    ("模板与技能", ["30_模板", "copilot/", "技能"]),
    ("归档", ["90_归档", "历史版本", "_合并"]),
]
SUGGEST = {
    "规范标准": "10_笔记/项目/<项目>/（并挂进对应 MOC）",
    "项目文档": "保持 10_笔记/项目/<项目>/",
    "参考资料": "20_附件 或 <项目>/参考资料/（保留出处）",
    "复盘总结": "10_笔记/项目/<项目>/（复盘）",
    "库级文档": "库根（工具依赖，别搬）",
    "学习笔记": "10_笔记/AI学习/（保持）",
    "知识条目": "10_笔记/知识库/Wiki/条目/",
    "产出与报告": "10_笔记/AI产出/",
    "写作": "10_笔记/写作/",
    "模板与技能": "30_模板/ 或 copilot/skills/（插件文件别动）",
    "归档": "90_归档/（保持）",
    "未分类": "按主题归到 10_笔记/项目 或 知识库；实在不确定 → 00_Inbox",
}


def classify_one(f):
    path = str(f.get("path") or "")
    # 库根下的单文件 = 库级文档（首页/说明/手册/AGENTS 之类，工具依赖，别搬）
    if "/" not in path:
        return "库级文档"
    name = str(f.get("name") or "")
    tags = " ".join(str(t) for t in (f.get("tags") or []))
    low = (path + " " + name + " " + tags).lower()
    if path.startswith("90_归档") or "历史版本" in path or "_合并" in path:
        return "归档"
    if path.startswith("30_模板") or path.startswith("copilot/"):
        return "模板与技能"
    for cat, kws in CLASSIFY:
        if any(k.lower() in low for k in kws):
            return cat
    if path.startswith("10_笔记/项目"):
        return "项目文档"
    if path.startswith("10_笔记/AI产出"):
        return "产出与报告"
    if path.startswith("00_Inbox/_"):
        return "产出与报告"
    if path.startswith("10_笔记/写作"):
        return "写作"
    if path.startswith("00_Inbox"):
        return "未分类"
    return "未分类"


def compute():
    try:
        idx = json.loads(IDX.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": "读不到索引：%s" % e}
    files = idx.get("files") or []
    buckets = {}
    for f in files:
        cat = classify_one(f)
        buckets.setdefault(cat, []).append(f)
    order = [c for c, _ in CLASSIFY] + ["未分类"]
    out = []
    for cat in order:
        rows = buckets.get(cat) or []
        if not rows and cat != "未分类":
            continue
        rows.sort(key=lambda x: str(x.get("mtime") or ""), reverse=True)
        out.append({"cat": cat, "count": len(rows), "suggest": SUGGEST.get(cat, ""),
                    "items": [{"path": r.get("path"), "name": r.get("name"),
                               "size_kb": round((r.get("size") or 0) / 1024, 1),
                               "fm": bool(r.get("has_frontmatter"))} for r in rows[:40]]})
    res = {"ok": True, "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
           "notes": len(files), "classes": out,
           "counts": {c["cat"]: c["count"] for c in out}}
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


def report_to_vault():
    r = compute()
    if not r.get("ok"):
        return r
    d = V / "10_笔记" / "知识库"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "分类索引.md"
    L = ["---", "type: 索引", "title: \"分类索引（自动生成）\"",
         "updated: %s" % datetime.now().strftime("%Y-%m-%d"), "tags:", "  - 知识库/索引", "---", "",
         "# 分类索引", "", "> 电脑管家自动分类：%s ｜ 共 %d 篇" % (r["ts"], r["notes"]), "",
         "| 分类 | 篇数 | 建议放哪 |", "|---|---|---|"]
    for c in r["classes"]:
        L.append("| %s | %d | %s |" % (c["cat"], c["count"], c["suggest"]))
    for c in r["classes"]:
        L += ["", "## %s（%d 篇）" % (c["cat"], c["count"]), ""]
        for it in c["items"]:
            L.append("- `%s`%s" % (it["path"], "" if it["fm"] else "  ⚠ 缺 frontmatter"))
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(p), "counts": r["counts"]}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "report":
        print(json.dumps(report_to_vault(), ensure_ascii=False, indent=1))
    else:
        r = compute()
        print(json.dumps({"ok": r.get("ok"), "notes": r.get("notes"), "counts": r.get("counts")},
                         ensure_ascii=False, indent=1))
