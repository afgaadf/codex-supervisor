# -*- coding: utf-8 -*-
"""checkers.py —— 自动检查器：把失败模式库里 auto=yes 的条目变成**真能跑**的检测。

设计原则（大厂标准）：
  · 纯函数优先：核心检测只吃数据结构，不吃全局状态，便于单测与复现。
  · IO 收口在适配器：读 vault_index.json / vault_checks.json / 文件内容只发生在
    load_* 与 scan_* 里，检测函数本身不碰磁盘。
  · 只读不改：检查器**只报**，不删不改不移动（H9）。动手交给 vault_ops。
  · 可解释：每条 finding 带 items 证据行，人能看到"为什么被判"。

用法：
  python checkers.py                # 跑一遍，打印 JSON
  python checkers.py --human        # 打印中文摘要
  python checkers.py --area obsidian
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paths import DATA_DIR, VAULT_DIR

# 非知识笔记：不参与"孤立/缺 frontmatter"判定
SKIP_DIRS = {".obsidian", ".git", ".trash", "copilot", ".opencode",
             "20_附件", "30_模板", "90_归档"}
ATTACH_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf",
              ".mp3", ".wav", ".mp4", ".mov", ".zip", ".xlsx", ".docx", ".pptx"}
NOTE_EXT = {".md"}
PINNED = {"agents.md", "claude.md", "maintenance_prompt.md", ".gitignore"}
HARD_SKIP = {".obsidian", ".git", ".trash", "copilot", ".opencode"}


def _skip_segment(part):
    """目录名该不该跳过：点开头一律跳过（.obsidian/.agents/.claudian…），再加显式清单。"""
    p = str(part)
    return p.startswith(".") or p in HARD_SKIP
MD_LINK = re.compile(r"!?\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")
CERTAINTY = ("一定", "肯定", "必然", "显然", "毫无疑问", "绝对")
DONE_WORDS = ("完成", "已修复", "已核对", "搞定", "done", "已提交", "已解决")
TEST_RUN_MARKERS = ("unittest", "pytest", "test ", "tests", "npm test", "go test",
                    "cargo test", "运行测试", "跑了测试")
ERROR_MARKERS = ("traceback", "error:", "错误", "failed", "拒绝访问",
                 "不是内部或外部命令", "is not recognized", "未找到", "no such file")


# ------------------------------------------------------------------ 工具
def _norm(s: str) -> str:
    return str(s or "").replace("\\", "/").strip().lower()


def _stem(path: str) -> str:
    p = _norm(path)
    for ext in (".md", ".canvas", ".base"):
        if p.endswith(ext):
            return p[: -len(ext)]
    return p


def _ext(path: str) -> str:
    p = _norm(path)
    i = p.rfind(".")
    return p[i:] if i >= 0 else ""


def _is_note(path: str) -> bool:
    p = _norm(path)
    if _ext(p) != ".md":
        return False
    if Path(p).name in PINNED:
        return False
    return not any(p.startswith(d.lower() + "/") for d in SKIP_DIRS)


def _is_attachment(path: str) -> bool:
    return _ext(path) in ATTACH_EXT


def _under(path: str, prefix: str) -> bool:
    return _norm(path).startswith(_norm(prefix).rstrip("/") + "/")


def _finding(mid, area, severity, title, items):
    out = {"id": mid, "area": area, "severity": severity,
           "title": title, "count": len(items), "items": list(items)}
    h = FIX_HINTS.get(mid)
    if h:
        out["fix"] = h.get("hint")
        out["fix_auto"] = bool(h.get("auto"))
    return out


# ------------------------------------------------------------------ 链接解析
class _Index:
    """把笔记列表编成"能按路径/文件名/后缀解析链接"的索引。"""

    def __init__(self, files):
        self.files = list(files)
        self.by_stem = {}
        self.names = set()
        for f in self.files:
            p = _norm(f.get("path") or f.get("name"))
            self.by_stem.setdefault(_stem(p), p)
            self.names.add(_norm(Path(p).name).rsplit(".", 1)[0])

    def resolve(self, link: str) -> bool:
        key = _stem(link)
        if not key:
            return False
        if key in self.by_stem:
            return True
        if key in self.names:
            return True
        # 后缀匹配：链接只写文件名，实际文件在子目录
        for stem in self.by_stem:
            if stem.endswith("/" + key):
                return True
        return False

    def backlinks(self):
        """返回 {norm_path: 入链数}。"""
        counts = defaultdict(int)
        for f in self.files:
            for link in f.get("links") or []:
                for target in self._targets_for(link):
                    counts[target] += 1
        return counts

    def _targets_for(self, link):
        key = _stem(link)
        out = []
        if key in self.by_stem:
            out.append(self.by_stem[key])
        for stem, p in self.by_stem.items():
            if stem.endswith("/" + key):
                out.append(p)
        return out


# ------------------------------------------------------------------ Obsidian 检测
def check_vault(files, checks=None, max_items=15, now=None):
    """files: vault_index.json 的 files 列表；checks: vault_checks.json 的 summary。"""
    files = list(files or [])
    idx = _Index(files)
    notes = [f for f in files if _is_note(f.get("path") or f.get("name"))]
    backlinks = idx.backlinks()
    findings = []

    # 1. 断链 / 改名未修链接
    broken, rename = [], []
    for f in notes:
        for link in f.get("links") or []:
            lk = str(link).strip()
            if not lk or lk.startswith("#"):
                continue
            if idx.resolve(lk):
                continue
            broken.append("%s -> [[%s]]" % (f.get("path"), lk))
            near = _close_stem(lk, idx)
            if near:
                rename.append("%s -> [[%s]] 疑为 [[%s]]" % (f.get("path"), lk, near))
    if broken:
        findings.append(_finding("obsidian.broken_link", "obsidian", "high",
                                 "断链或未解析链接", broken[:max_items]))
    if rename:
        findings.append(_finding("obsidian.rename_without_link_repair", "obsidian", "high",
                                 "改名/移动后未修链接", rename[:max_items]))

    # 2. 缺 frontmatter
    miss = [f.get("path") for f in notes if not f.get("has_frontmatter")]
    if miss:
        findings.append(_finding("obsidian.missing_frontmatter", "obsidian", "high",
                                 "缺 frontmatter / 属性", miss[:max_items]))

    # 3. 孤立笔记（无出链且无入链）
    orphans = []
    for f in notes:
        p = _norm(f.get("path"))
        if not (f.get("links") or []) and backlinks.get(p, 0) == 0:
            orphans.append(f.get("path"))
    if orphans:
        findings.append(_finding("obsidian.orphan_note", "obsidian", "medium",
                                 "孤立笔记无入链也无出链", orphans[:max_items]))

    # 4. 收件箱未分流（无标签）
    triage = [f.get("path") for f in notes
              if _under(f.get("path"), "00_Inbox") and not (f.get("tags") or [])]
    if triage:
        findings.append(_finding("obsidian.inbox_no_triage", "obsidian", "medium",
                                 "收件箱条目无标签无法分流", triage[:max_items]))

    # 5. 收件箱堆积（超期）
    stale = _stale_inbox(notes, now=now)
    if stale:
        findings.append(_finding("obsidian.inbox_stale", "obsidian", "high",
                                 "收件箱长期堆积", stale[:max_items]))

    # 6. 孤立附件（无人引用）
    referenced = set()
    for f in files:
        for link in f.get("links") or []:
            referenced.add(_norm(Path(_stem(link)).name))
    orphan_att = []
    for f in files:
        p = f.get("path") or f.get("name")
        if not _is_attachment(p):
            continue
        if _norm(Path(_stem(p)).name) not in referenced:
            orphan_att.append(p)
    if orphan_att:
        findings.append(_finding("obsidian.orphan_attachment", "obsidian", "low",
                                 "附件无人引用", orphan_att[:max_items]))

    # 7. 单篇过长（桥同口径：>200KB）
    big = []
    for f in notes:
        try:
            size = int(f.get("size") or 0)
        except Exception:
            size = 0
        if size > 200 * 1024:
            big.append("%s（%d KB）" % (f.get("path"), size // 1024))
    if big:
        findings.append(_finding("obsidian.note_bloat", "obsidian", "low",
                                 "单篇过长未拆分", big[:max_items]))

    # 8. 链接方向：被引用很多却不指出去
    direction = []
    for f in notes:
        p_ = _norm(f.get("path"))
        if not (f.get("links") or []) and backlinks.get(p_, 0) >= 3:
            direction.append("%s（被引用 %d 次，但一条出链都没有）"
                             % (f.get("path"), backlinks.get(p_, 0)))
    if direction:
        findings.append(_finding("obsidian.link_direction", "obsidian", "low",
                                 "双链方向错误：只被引用不指出去", direction[:max_items]))

    # 9. 未提交（来自 checks 摘要）
    if checks:
        s = checks.get("summary") if isinstance(checks, dict) else None
        if isinstance(s, dict) and s.get("uncommitted"):
            findings.append(_finding("obsidian.uncommitted_vault", "obsidian", "medium",
                                     "知识库改完未提交 Git",
                                     ["未提交改动 %s 处" % s.get("uncommitted")]))

    return findings


def _stale_inbox(notes, days=14, now=None):
    now = now or datetime.now(timezone.utc)
    out = []
    for f in notes:
        if not _under(f.get("path"), "00_Inbox"):
            continue
        ts = f.get("mtime")
        if not ts:
            continue
        try:
            dt = datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc)
        except Exception:
            continue
        if now - dt > timedelta(days=days):
            out.append("%s（%d 天）" % (f.get("path"), (now - dt).days))
    return out


def _close_stem(link, idx, cutoff=0.82):
    import difflib
    key = _stem(link)
    if not key:
        return None
    best, score = None, 0.0
    for stem in idx.by_stem:
        r = difflib.SequenceMatcher(None, key, stem.rsplit("/", 1)[-1]).ratio()
        if r > score:
            best, score = stem, r
    return best if score >= cutoff else None


# ------------------------------------------------------------------ Codex 检测
def check_turn(ask="", actions="", say=""):
    """对一轮 Codex 行为做文本级自动检测（保守，宁缺勿滥）。"""
    act = str(actions or "")
    out = str(say or "")
    both = (str(ask or "") + "\n" + act + "\n" + out)
    findings = []

    # 声称完成但没有对应动作
    claims = [w for w in DONE_WORDS if w in out]
    if claims and len(act.strip()) < 40:
        findings.append(_finding("codex.claim_without_action", "codex", "high",
                                 "声称完成但没有对应动作",
                                 ["收尾出现完成词 %s，但本轮动作很少" % "/".join(claims)]))
    # 改了却不验证
    if re.search(r"(修改|修复|重构|patch|写入)", both) and not any(m in act.lower() for m in TEST_RUN_MARKERS):
        findings.append(_finding("codex.no_test_after_change", "codex", "high",
                                 "改了代码/文档却不验证",
                                 ["出现改动词，但动作里没有测试/复检标记"]))
    # 工具报错当成功继续
    errs = [m for m in ERROR_MARKERS if m in act.lower()]
    if errs and any(w in out for w in ("完成", "已修复", "通过", "成功", "搞定")):
        findings.append(_finding("codex.ignored_tool_error", "codex", "high",
                                 "工具或命令报错却当作成功继续",
                                 ["动作含错误标记 %s，收尾却称成功" % "/".join(errs)]))
    # 假装验证
    if any(w in out for w in ("已验证", "测试通过", "全部通过", "passed", "green")) \
            and not any(m in act.lower() for m in TEST_RUN_MARKERS):
        findings.append(_finding("codex.fake_verification", "codex", "high",
                                 "假装验证：称已验证但没有测试动作",
                                 ["收尾称已验证，动作里找不到测试运行"]))
    # 无证据的肯定断言
    hits = [w for w in CERTAINTY if w in out]
    if hits:
        findings.append(_finding("codex.unsupported_certainty", "codex", "medium",
                                 "无证据却用肯定语气断言",
                                 ["收尾出现确定词：%s" % "、".join(hits)]))
    # 重复绕圈：同一命令行重复 >= 3
    lines = [ln.strip() for ln in act.splitlines() if len(ln.strip()) > 12]
    dup = [ln for ln, c in _counts(lines).items() if c >= 3]
    if dup:
        findings.append(_finding("codex.repeat_loop", "codex", "medium",
                                 "重复命令/绕圈不收敛",
                                 ["重复 >=3 次：%s" % d[:120] for d in dup[:5]]))
    # 自证完成 / 自解除限制
    if re.search(r"(自行(解除|批准|放行)|给自己(发|开)|self[- ]?(approve|grant))", both, re.I):
        findings.append(_finding("codex.self_approval", "codex", "critical",
                                 "自证完成或自行解除限制",
                                 ["文本出现自行解除限制的表述"]))
    # 大文件全量读
    if re.search(r"(cat|type|get-content)\s+\S*(log|日志|\.jsonl|\.json|\.csv)", act, re.I) \
            and not re.search(r"(tail|select-string|head|尾|检索|-Tail)", act, re.I):
        findings.append(_finding("codex.token_bloat", "codex", "medium",
                                 "疑似全量读取大文件或大日志",
                                 ["动作像在整读日志/大文件，未用尾部或检索"]))
    # 同一失败无上限重试（重复 >=2 且带重试口吻）
    rep2 = [ln for ln, c in _counts(lines).items() if c >= 2]
    if rep2 and re.search(r"(重试|retry|再试|再来一次|again)", both, re.I):
        findings.append(_finding("codex.unbounded_retry", "codex", "medium",
                                 "同一失败无上限重试不换策略",
                                 ["同一命令重复且出现重试口吻：%s" % d[:120] for d in rep2[:5]]))
    # 可能把用户锁在外面，却没提恢复通道
    if re.search(r"(禁用|关闭|锁定|阻断|卸载|停止服务|不再允许)", both) and not re.search(
            r"(恢复|回滚|rollback|rescue|撤销|退回|卸载方法|退路)", both, re.I):
        findings.append(_finding("codex.irreversible_lockout", "codex", "critical",
                                 "改动可能把用户锁在外面且没有恢复通道",
                                 ["文本提到禁用/阻断类改动，但没提恢复或回滚办法"]))
    # 声称已核对但没有核对动作
    if re.search(r"(已核对|已确认|检查过了|确认无误|已验证)", out) and not re.search(
            r"(read|type |cat |get-content|rg |grep|select-string|diff|比较|比对|打开)", act, re.I):
        findings.append(_finding("codex.no_evidence_claim", "codex", "high",
                                 "声称已核对/已确认但没有核对动作",
                                 ["收尾称已核对，但动作里没有读取或比对"]))
    # 通配符删除/覆盖
    if re.search(r"(rm\s+-rf|del\s+\*|remove-item\s+\*|通配删除|清空目录|删除所有)", act, re.I):
        findings.append(_finding("codex.destructive_glob", "codex", "critical",
                                 "通配符删除或覆盖",
                                 ["动作里出现通配符删除/清空，范围不受控"]))
    # 同一环节连续报错仍按原路推进
    err_hits = sum(len(re.findall(p, act, re.I)) for p in
                   (r"error", r"failed", r"traceback", r"报错", r"失败", r"拒绝访问"))
    if err_hits >= 3:
        findings.append(_finding("codex.tool_error_pileup", "codex", "high",
                                 "工具错误堆积却仍按原路推进",
                                 ["动作里出现 %d 处错误标记，未停下换路子" % err_hits]))
    # 涉及既有资料却不查库
    mentions_lib = re.search(r"(之前(写|做)|我们的|已有|现有|既有|规范|知识库|vault|文档库|README|AGENTS)", both, re.I)
    reads_lib = re.search(r"(rg\s|grep|select-string|findstr|get-childitem|glob|search|检索|查找|读取|getfile|read)", act, re.I)
    if mentions_lib and not reads_lib:
        findings.append(_finding("codex.no_library_lookup", "codex", "high",
                                 "涉及既有资料却不查库",
                                 ["文本提到既有资料/规范，但动作里没有检索或读取"]))
    return findings


def _counts(seq):
    d = defaultdict(int)
    for x in seq:
        d[x] += 1
    return d


# ------------------------------------------------------------------ 内容级检测
def _parse_frontmatter(text):
    """极简 YAML 子集：只取 --- ... --- 之间的顶层 `key: value`；解析不了返回 None。"""
    if not text or not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    lines = text[3:end].splitlines()
    fm = {}
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in (" ", "\t", "-"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        if not k:
            continue
        v = v.strip()
        if v == "":
            # 值为空时看下一行：缩进的 `- ` 说明是块列表，避免误判成"空"类型
            for nxt in lines[i + 1:]:
                if not nxt.strip():
                    continue
                if nxt[0] in (" ", "\t"):
                    v = "[]" if nxt.lstrip().startswith("-") else "<多行>"
                break
        fm[k] = v
    return fm


def _infer_type(value):
    s = str(value or "").strip()
    if s == "":
        return "空"
    if s.startswith("[") or s.startswith("-"):
        return "列表"
    if s.lower() in ("true", "false", "yes", "no"):
        return "布尔"
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return "日期"
    if re.match(r"^-?\d+(\.\d+)?$", s):
        return "数字"
    return "文本"


# 同义字段组：组内出现 >=2 种写法 = 命名漂移
SYNONYM_GROUPS = (
    {"date", "日期", "created", "创建", "创建日期"},
    {"updated", "更新", "更新日期", "modified", "修改日期"},
    {"title", "标题", "name", "名称"},
    {"tags", "tag", "标签"},
    {"type", "类型", "类别"},
    {"aliases", "alias", "别名"},
)


def _resolve_ref(ref, stems):
    key = _stem(ref)
    if not key:
        return False
    if key in stems:
        return True
    names = {Path(x).name for x in stems}
    if key in names:
        return True
    return any(x.endswith("/" + key) for x in stems)


def _base_properties(text):
    """.base 文件里引用的属性名（保守：只收明显像属性名的）。"""
    props = set()
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, dict):
        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("property", "properties", "columns", "order") and isinstance(v, (list, str)):
                        items = v if isinstance(v, list) else [v]
                        for it in items:
                            if isinstance(it, str):
                                props.add(it)
                            elif isinstance(it, dict) and isinstance(it.get("property"), str):
                                props.add(it["property"])
                    walk(v)
            elif isinstance(node, list):
                for it in node:
                    walk(it)
        walk(data)
    if not props:
        for line in text.splitlines():
            m = re.match(r"^\s*-?\s*([A-Za-z_]\w*(?:\.\w+)*)\s*$", line)
            if m:
                props.add(m.group(1))
    return {x for x in props if x and x not in ("and", "or", "not", "true", "false")}


def check_vault_content(root=None, max_files=4000, max_items=15):
    """读知识库正文，补跑依赖文件内容的 auto=yes 检查（只读，不改库）。"""
    root = Path(root or VAULT_DIR)
    if not root.is_dir():
        return []
    stems, notes_fm, canvases, bases = {}, [], [], []
    n = 0
    for path in root.rglob("*"):
        if n >= max_files:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(_skip_segment(part) for part in rel.split("/")[:-1]):
            continue
        n += 1
        stems[_stem(rel)] = rel
        ext = _ext(rel)
        if ext not in (".md", ".canvas", ".base"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if ext == ".md":
            fm = _parse_frontmatter(text)
            if fm is not None:
                notes_fm.append((rel, fm))
        elif ext == ".canvas":
            canvases.append((rel, text))
        else:
            bases.append((rel, text))

    findings = []

    # 属性类型漂移
    types, examples = defaultdict(set), defaultdict(list)
    for rel, fm in notes_fm:
        for k, v in fm.items():
            types[k].add(_infer_type(v))
            if len(examples[k]) < 3:
                examples[k].append("%s=%s(%s)" % (Path(rel).name, str(v)[:24], _infer_type(v)))
    # 空值 = 未设置，不算类型漂移；只有 >=2 种"有值"类型才算
    drift = ["属性 %s 类型不一致：%s ｜ %s" % (k, "/".join(sorted(ts - {"空"})), "；".join(examples[k][:2]))
             for k, ts in types.items() if len(ts - {"空"}) >= 2]
    if drift:
        findings.append(_finding("obsidian.property_type_drift", "obsidian", "medium",
                                 "同名属性类型不一致", drift[:max_items]))

    # frontmatter 字段命名漂移
    present = set()
    for _, fm in notes_fm:
        present |= set(fm)
    schema = ["同义字段并存：%s" % " / ".join(sorted(present & grp))
              for grp in SYNONYM_GROUPS if len(present & grp) >= 2]
    if schema:
        findings.append(_finding("obsidian.frontmatter_schema_drift", "obsidian", "medium",
                                 "frontmatter 字段命名不统一", schema[:max_items]))

    # canvas 引用失效
    broken = []
    for rel, text in canvases:
        try:
            data = json.loads(text)
        except Exception:
            continue
        nodes = data.get("nodes") if isinstance(data, dict) else None
        for node in nodes or []:
            ref = str((node or {}).get("file") or "").strip()
            if ref and not _resolve_ref(ref, stems):
                broken.append("%s -> %s" % (rel, ref))
    if broken:
        findings.append(_finding("obsidian.canvas_drift", "obsidian", "medium",
                                 "canvas 引用的笔记或节点失效", broken[:max_items]))

    # Bases 属性缺失
    have = set()
    for _, fm in notes_fm:
        have |= set(fm)
    base_miss = []
    for rel, text in bases:
        miss = sorted(x for x in _base_properties(text) if x not in have)
        if miss:
            base_miss.append("%s 引用的属性无笔记提供：%s" % (rel, "、".join(miss)))
    if base_miss:
        findings.append(_finding("obsidian.bases_property_mismatch", "obsidian", "medium",
                                 "Bases 视图依赖的属性缺失或类型不符", base_miss[:max_items]))

    return findings


# ------------------------------------------------------------------ 文本与结构级检测
BULK_SKIP = ("20_附件/原始资料",)     # 真库里的巨型归档，别整个走一遍
REQUIRED_PROPS = ("type", "title", "updated")   # 库规要求的标准属性


def _walk_notes(root, max_files=4000):
    """走知识区，产出 (rel, text)。巨型归档与隐藏目录跳过。"""
    root = Path(root)
    n = 0
    for path in root.rglob("*.md"):
        if n >= max_files:
            return
        try:
            rel = path.relative_to(root).as_posix()
        except Exception:
            continue
        if any(_skip_segment(part) for part in rel.split("/")[:-1]):
            continue
        if any(rel.startswith(b + "/") for b in BULK_SKIP):
            continue
        n += 1
        try:
            yield rel, path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue


def check_vault_text(root=None, max_files=4000, max_items=15):
    """读正文做：缺库规属性 / 重复标题 / 缺一级标题 / 碎片标签 / 结构问题。"""
    root = Path(root or VAULT_DIR)
    if not root.is_dir():
        return []

    miss_props, dup_heads, no_h1 = [], [], []
    tag_count = defaultdict(int)
    for rel, text in _walk_notes(root, max_files=max_files):
        if not _is_note(rel):
            continue
        fm = _parse_frontmatter(text) or {}
        lack = [k for k in REQUIRED_PROPS if k not in fm]
        if lack:
            miss_props.append("%s 缺 %s" % (rel, "/".join(lack)))
        heads, seen, dup = [], set(), set()
        for line in text.splitlines():
            if line.startswith("#"):
                h = line.lstrip("#").strip()
                if not h:
                    continue
                heads.append(h)
                if h in seen:
                    dup.add(h)
                seen.add(h)
        if dup:
            dup_heads.append("%s 重复标题：%s" % (rel, "、".join(sorted(dup))))
        if not any(l.startswith("# ") for l in text.splitlines()):
            no_h1.append(rel)
        for t in re.findall(r"(?:^|\s)#([^\s#[\]()]+)", text):
            tag_count[t] += 1

    findings = []
    if miss_props:
        findings.append(_finding("obsidian.property_missing_required", "obsidian", "high",
                                 "库规要求的属性缺失", miss_props[:max_items]))
    if dup_heads:
        findings.append(_finding("obsidian.heading_duplicate", "obsidian", "low",
                                 "同一文件出现重复标题", dup_heads[:max_items]))
    if no_h1:
        findings.append(_finding("obsidian.note_no_h1", "obsidian", "low",
                                 "笔记正文缺一级标题", no_h1[:max_items]))
    singles = sorted(t for t, c in tag_count.items() if c == 1)
    if len(singles) >= 20:
        findings.append(_finding("obsidian.tag_singleton", "obsidian", "low",
                                 "只用过一次的碎片标签",
                                 ["共 %d 个只用一次的标签，例：%s" % (len(singles), "、".join(singles[:8]))]))

    # ---- 模板未套用：有模板，但没有一篇笔记用了模板的字段组合 ----
    tdir = root / "30_模板"
    if tdir.is_dir():
        tmpl_keys = []
        for tf in tdir.rglob("*.md"):
            try:
                ttext = tf.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            tfm = _parse_frontmatter(ttext) or {}
            ks = set(tfm)
            if ks:
                tmpl_keys.append((tf.name, ks))
        if tmpl_keys:
            note_keys = []
            for rel, text in _walk_notes(root, max_files=max_files):
                if not _is_note(rel):
                    continue
                note_keys.append(set(_parse_frontmatter(text) or {}))
            used = any(ks <= nk for _, ks in tmpl_keys for nk in note_keys)
            if note_keys and not used:
                findings.append(_finding("obsidian.template_unused", "obsidian", "low",
                                         "模板存在但没有笔记套用",
                                         ["%d 个模板的字段组合在 %d 篇笔记里一个都没出现（例：%s）"
                                          % (len(tmpl_keys), len(note_keys), tmpl_keys[0][0])]))

    # ---- 结构级 ----
    backup_dirs = [d.name for d in root.iterdir()
                   if d.is_dir() and re.search(r"(backup|备份|快照|bak)", d.name, re.I)]
    if backup_dirs:
        findings.append(_finding("obsidian.backup_dir_in_vault", "obsidian", "medium",
                                 "备份目录放在库内", ["库内备份目录：%s" % "、".join(backup_dirs[:8])]))
    if not (root / ".gitignore").exists():
        findings.append(_finding("obsidian.vault_no_gitignore", "obsidian", "medium",
                                 "缺 .gitignore 把缓存大文件纳入版本控制",
                                 ["库根没有 .gitignore"]))
    inbox = root / "00_Inbox"
    if inbox.is_dir():
        files = [f for f in inbox.rglob("*") if f.is_file() and _is_note(f.relative_to(root).as_posix())]
        if len(files) > 20:
            findings.append(_finding("obsidian.inbox_never_emptied", "obsidian", "high",
                                     "收件箱只进不出", ["00_Inbox 已积 %d 篇未分流" % len(files)]))
    return findings


# ------------------------------------------------------------------ 修复建议
# 每条失败模式对应的"安全修法"。auto=True 表示有现成工具能自动做（仍需人点一下）；
# auto=False 表示必须人来看。这里只给建议，**检查器自己绝不改库**（H9）。
FIX_HINTS = {
    "obsidian.broken_link": {
        "hint": "把链接改成现有笔记名，或删掉失效链接",
        "auto": True, "how": "python vault_ops_batch.py plan  # 先看计划再 run"},
    "obsidian.rename_without_link_repair": {
        "hint": "疑似改名未修：把旧链接改成新名字",
        "auto": True, "how": "python vault_ops_batch.py plan"},
    "obsidian.missing_frontmatter": {
        "hint": "补 frontmatter（至少 type/title/updated）",
        "auto": True, "how": "python vault_ops_batch.py plan  # 会先备份再补"},
    "obsidian.property_missing_required": {
        "hint": "补齐库规要求的 type / title / updated",
        "auto": False, "how": "按 30_模板 的模板补，或人工逐篇补"},
    "obsidian.property_type_drift": {
        "hint": "同名属性统一类型（日期别一会儿带引号一会儿不带）",
        "auto": False, "how": "人工统一后再跑一次 --human 复查"},
    "obsidian.frontmatter_schema_drift": {
        "hint": "同义字段二选一统一（如 created/date、name/title）",
        "auto": False, "how": "定一个主字段名，批量改完复查"},
    "obsidian.heading_duplicate": {
        "hint": "合并或改写重复标题（锚点会打架）",
        "auto": False, "how": "人工改标题"},
    "obsidian.note_no_h1": {
        "hint": "补一级标题",
        "auto": False, "how": "在正文首行加 # 标题"},
    "obsidian.orphan_note": {
        "hint": "挂进 MOC，或补一条指向相关笔记的链接",
        "auto": False, "how": "人工判断归属"},
    "obsidian.orphan_attachment": {
        "hint": "归档或删除无人引用的附件（先确认再删）",
        "auto": False, "how": "清单在 items 里，人确认后再动；本程序不删"},
    "obsidian.inbox_no_triage": {
        "hint": "给收件箱条目打标签或归类",
        "auto": False, "how": "人工分流"},
    "obsidian.inbox_stale": {
        "hint": "清收件箱：归位或登记放弃",
        "auto": False, "how": "人工分流"},
    "obsidian.inbox_never_emptied": {
        "hint": "收件箱只进不出，需要一次集中分流",
        "auto": False, "how": "人工集中处理一批"},
    "obsidian.tag_singleton": {
        "hint": "把只用一次的标签并到既有标签",
        "auto": False, "how": "人工合并，或改 30_模板 里的默认标签"},
    "obsidian.backup_dir_in_vault": {
        "hint": "把备份目录移到知识库外面",
        "auto": False, "how": "移动前先确认备份完整"},
    "obsidian.vault_no_gitignore": {
        "hint": "补 .gitignore，排除缓存与大目录",
        "auto": True, "how": "在库根建 .gitignore（内容按 items 里提示）"},
    "obsidian.uncommitted_vault": {
        "hint": "把库内改动提交 git",
        "auto": True, "how": "git -C <库路径> add -A && git commit"},
    "obsidian.plugin_disabled_unnoticed": {
        "hint": "对齐启用清单与实际安装目录",
        "auto": True, "how": "管家 → Obsidian 库 页可一键装插件；或手改 community-plugins.json"},
    "obsidian.canvas_drift": {
        "hint": "修 canvas 里失效的节点引用",
        "auto": False, "how": "在 Obsidian 里打开该 canvas 手动改"},
    "obsidian.bases_property_mismatch": {
        "hint": "补视图依赖的属性，或改视图",
        "auto": False, "how": "人工改 .base 或补属性"},
    "obsidian.note_bloat": {
        "hint": "单篇过长，按主题拆开并加索引",
        "auto": False, "how": "人工拆分"},
    "obsidian.template_unused": {
        "hint": "模板没人套用：要么用起来，要么删掉",
        "auto": False, "how": "检查 30_模板 是否还符合当前库规"},
    "obsidian.link_direction": {
        "hint": "被引用很多却不指出去的笔记，补几条出链",
        "auto": False, "how": "人工补链"},
    "codex.tool_error_pileup": {
        "hint": "同一环节连错多次要停下来换路子，别硬推",
        "auto": False, "how": "改行为；把错误如实报出来"},
    "codex.irreversible_lockout": {
        "hint": "先给恢复通道并验证它能用，再上限制",
        "auto": False, "how": "参考 supervisor.py rescue 的做法"},
}


def fix_hint_for(mode_id):
    return dict(FIX_HINTS.get(mode_id) or {})


def fix_plan(findings, limit=40):
    """把一次检查的结果转成可执行的修复计划（只给建议，不执行）。"""
    plan = []
    for f in findings or []:
        h = FIX_HINTS.get(f.get("id"))
        if not h:
            continue
        plan.append({
            "id": f.get("id"),
            "severity": f.get("severity"),
            "count": f.get("count"),
            "hint": h.get("hint"),
            "auto": bool(h.get("auto")),
            "how": h.get("how"),
            "samples": list(f.get("items") or [])[:3],
            "targets": list(f.get("items") or []),
        })
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    plan.sort(key=lambda x: (order.get(x.get("severity"), 9), not x.get("auto"), x.get("id") or ""))
    return plan[:max(0, int(limit))]


def render_fix_plan(plan):
    if not plan:
        return "没有需要修的项目。"
    lines = ["修复计划（共 %d 类；【可脚本】= 有现成工具，其它需人工）：" % len(plan)]
    for i, it in enumerate(plan, 1):
        tag = "可脚本" if it.get("auto") else "需人工"
        lines.append("%d. [%s|%s] %s（%s 条）" % (i, it.get("severity"), tag,
                                                  it.get("hint"), it.get("count")))
        if it.get("how"):
            lines.append("      做法：%s" % it.get("how"))
        for smp in (it.get("samples") or [])[:2]:
            lines.append("      例：%s" % str(smp)[:110])
    return "\n".join(lines)


# ------------------------------------------------------------------ 插件状态检测
def check_vault_plugins(vault_dir=None, max_items=15):
    """比对「启用清单」与「实际安装目录」。

    这是"以为在跑、其实没跑"的那类静默失效：
      · 清单里有、目录没有 -> 插件根本没装（启用了个空气）
      · 目录有、清单里没有 -> 插件被静默禁用
    """
    vault = Path(vault_dir or VAULT_DIR)
    obs = vault / ".obsidian"
    pdir = obs / "plugins"
    if not pdir.is_dir():
        return []
    try:
        enabled_raw = json.loads((obs / "community-plugins.json").read_text(encoding="utf-8"))
        enabled = [str(x) for x in enabled_raw] if isinstance(enabled_raw, list) else []
    except Exception:
        enabled = []
    installed = sorted(d.name for d in pdir.iterdir() if d.is_dir() and not d.name.startswith("."))

    ghost = [x for x in enabled if x not in installed]          # 启用了但没装
    disabled = [x for x in installed if x not in enabled]       # 装了但没启用

    findings = []
    if ghost:
        findings.append(_finding("obsidian.plugin_disabled_unnoticed", "obsidian", "high",
                                 "插件被静默禁用/缺失却以为在跑",
                                 ["启用清单里有、但目录里找不到：%s" % "、".join(ghost[:max_items])]))
    return findings


def plugin_status(vault_dir=None, max_items=20):
    """给界面用：两个清单的对照结果（只读）。"""
    vault = Path(vault_dir or VAULT_DIR)
    obs = vault / ".obsidian"
    pdir = obs / "plugins"
    try:
        enabled = json.loads((obs / "community-plugins.json").read_text(encoding="utf-8"))
        enabled = [str(x) for x in enabled] if isinstance(enabled, list) else []
    except Exception:
        enabled = []
    installed = sorted(d.name for d in pdir.iterdir() if d.is_dir() and not d.name.startswith(".")) \
        if pdir.is_dir() else []
    return {"enabled": enabled, "installed": installed[:max_items],
            "ghost": [x for x in enabled if x not in installed],
            "disabled": [x for x in installed if x not in enabled],
            "installed_total": len(installed)}


# ------------------------------------------------------------------ IO 适配器
def load_vault_index(path=None):
    p = Path(path) if path else (DATA_DIR / "vault_index.json")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else list(d.get("files") or [])
    except Exception:
        return []


def load_vault_checks(path=None):
    p = Path(path) if path else (DATA_DIR / "vault_checks.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def scan_vault(vault_dir=None, max_files=4000):
    """读真实知识库，跑全部 auto=yes 的 Obsidian 检查。"""
    root = Path(vault_dir or VAULT_DIR)
    files = load_vault_index()
    if not files:
        files = _index_from_dir(root, max_files=max_files)
    return (check_vault(files, load_vault_checks())
            + check_vault_content(root, max_files=max_files)
            + check_vault_text(root, max_files=max_files)
            + check_vault_plugins(root))


def _index_from_dir(root: Path, max_files=4000):
    out = []
    if not root or not Path(root).is_dir():
        return out
    for i, f in enumerate(Path(root).rglob("*")):
        if i >= max_files:
            break
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if any(_skip_segment(part) for part in rel.split("/")[:-1]):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        links = [m.group(1).strip() for m in MD_LINK.finditer(text)] if text else []
        out.append({"path": rel, "name": f.name, "links": links,
                    "tags": re.findall(r"#([^\s#\[\]]+)", text) if text else [],
                    "has_frontmatter": text.startswith("---"),
                    "mtime": f.stat().st_mtime * 1000})
    return out


def run(area=None):
    """跑一遍。area=None 时只看知识库；回合级 Codex 检测在 judge 里调 check_turn。"""
    modes = list(load_modes_safe())
    findings = []
    if area in (None, "obsidian"):
        findings += scan_vault()
    return {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "modes_total": len(modes),
            "auto_modes": sum(1 for m in modes if m.get("auto") == "yes"),
            "findings": findings}


def load_modes_safe():
    try:
        import failure_modes as FM
        return FM.load_modes()
    except Exception:
        return []


def _human(result):
    fs = result.get("findings") or []
    if not fs:
        return "自动检查：未发现 auto=yes 的 Obsidian 问题。"
    lines = ["自动检查发现 %d 类问题：" % len(fs)]
    for f in fs:
        lines.append("· [%s] %s（%d 条）" % (f["severity"], f["title"], f["count"]))
        if f.get("fix"):
            lines.append("    修法：%s%s" % (f["fix"], "（可脚本）" if f.get("fix_auto") else "（需人工）"))
        for it in f["items"][:5]:
            lines.append("    - %s" % it)
    return "\n".join(lines)


def main(argv):
    area = None
    if "--area" in argv:
        i = argv.index("--area")
        if i + 1 < len(argv):
            area = argv[i + 1]
    result = run(area=area)
    if "--fix-plan" in argv:
        print(render_fix_plan(fix_plan(result.get("findings") or [])))
    elif "--human" in argv:
        print(_human(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))