#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp_server.py —— 管家「Codex 监督插件」的 MCP 服务器（stdio，纯标准库）。

职责：让 Codex 在声称「完成 / 已修复 / 已验证」之前，能够主动查询：
  · supervisor_status —— 管家当前判定（分数 / 等级 / 是否维护模式）
  · failure_modes     —— 失败模式库（codex / obsidian），可按 area 筛选
  · check_text        —— 对一段文本做确定性失败模式自检

设计约束（大厂标准）：
  · 纯标准库，不引入第三方依赖，不联网。
  · stdout 只允许输出 JSON-RPC（MCP stdio 传输契约）；一切诊断走 stderr。
  · 任何异常都不得把栈打到 stdout；工具内部错误以 isError 结果返回。
  · 只读：本服务器不改管家的判定状态，仅在启动时写一份自检报告。
  · 优雅降级：管家目录 / 状态文件缺失时给中文说明，不抛异常。

用法：
  python mcp_server.py        # 直接作为 stdio MCP 服务器运行
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_NAME = "codex"
PLUGIN_VERSION = "0.1.0"
SERVER_NAME = "codex-supervisor"
DEFAULT_PROTOCOL = "2024-11-05"

HOME = Path.home()
SUPERVISOR_DIR = Path(r"C:\Users\taich\.codex\supervisor")
ANTI_DIR = Path(r"C:\Users\taich\.codex\anti-degradation")
FAILURE_MODES_FILE = SUPERVISOR_DIR / "rubrics" / "_failure_modes.json"
CHECKERS_FILE = SUPERVISOR_DIR / "checkers.py"
ANTI_SUPERVISOR_FILE = ANTI_DIR / "supervisor.py"
SESSION_FILE = ANTI_DIR / "state" / "session.json"
RULES_FILE = ANTI_DIR / "rules" / "rules.json"
MAINTENANCE_FILE = ANTI_DIR / "state" / "maintenance.json"
REPORT_DIR = HOME / ".codex" / "supervisor-plugin-reports"
REPORT_FILE = REPORT_DIR / "codex.json"


# ------------------------------------------------------------------ 基础设施
def log(msg):
    """诊断信息一律写 stderr，绝不污染 stdout 的 JSON-RPC 流。"""
    try:
        sys.stderr.write("[codex-supervisor] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


class _SilenceStdout:
    """导入管家模块期间临时把 stdout 挪到 stderr，避免第三方 print 污染 JSON-RPC 流。"""

    def __enter__(self):
        self._old = sys.stdout
        sys.stdout = sys.stderr
        return self

    def __exit__(self, *exc):
        sys.stdout = self._old
        return False


def _force_utf8():
    """stdin/stdout/stderr 一律按 UTF-8 处理，避免 Windows 默认 GBK 造成乱码。"""
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _now_iso_local():
    """本地时区 ISO8601，秒级（例如 2026-10-07T07:00:00+08:00）。"""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _read_json(path):
    """容错读取 JSON：文件不存在 / 解析失败都返回 None，不抛。"""
    try:
        p = Path(path)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        log("读取 JSON 失败 %s: %r" % (path, exc))
        return None


def _write_json(path, obj):
    """写 JSON：UTF-8 无 BOM、缩进 2、LF 换行。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")


# ------------------------------------------------------------------ 模块加载
_CACHE = {}


def load_failure_modes():
    """读取失败模式库；缺失则返回空列表。"""
    if "modes" in _CACHE:
        return _CACHE["modes"]
    data = _read_json(FAILURE_MODES_FILE) or {}
    modes = data.get("modes") if isinstance(data, dict) else None
    if not isinstance(modes, list):
        modes = []
    _CACHE["modes"] = modes
    return modes


def _load_checkers():
    """按绝对路径加载 supervisor/checkers.py；失败返回 None。"""
    if "checkers" in _CACHE:
        return _CACHE["checkers"]
    mod = None
    if CHECKERS_FILE.exists():
        if str(SUPERVISOR_DIR) not in sys.path:
            sys.path.insert(0, str(SUPERVISOR_DIR))
        try:
            with _SilenceStdout():
                import checkers as mod  # noqa: F401
        except Exception as exc:
            log("导入 checkers 失败: %r" % exc)
            mod = None
    else:
        log("未找到 checkers.py: %s" % CHECKERS_FILE)
    _CACHE["checkers"] = mod
    return mod


def _load_anti():
    """按绝对路径加载 anti-degradation/supervisor.py；失败返回 None。"""
    if "anti" in _CACHE:
        return _CACHE["anti"]
    mod = None
    if ANTI_SUPERVISOR_FILE.exists():
        try:
            with _SilenceStdout():
                spec = importlib.util.spec_from_file_location(
                    "supervisor_plugin_anti", str(ANTI_SUPERVISOR_FILE))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
        except Exception as exc:
            log("导入 anti-degradation/supervisor 失败: %r" % exc)
            mod = None
    else:
        log("未找到 anti-degradation/supervisor.py: %s" % ANTI_SUPERVISOR_FILE)
    _CACHE["anti"] = mod
    return mod


# ------------------------------------------------------------------ 工具实现
def _maintenance_summary():
    """从 maintenance.json 计算维护模式状态（active / 剩余分钟），永不抛。"""
    obj = _read_json(MAINTENANCE_FILE) or {}
    active = bool(obj.get("active"))
    until = obj.get("until")
    remaining = None
    if active and until:
        try:
            dt = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            remaining = round(max(0.0, (dt - datetime.now(timezone.utc)).total_seconds()) / 60.0, 1)
            active = remaining > 0
        except Exception as exc:
            log("解析维护到期时间失败: %r" % exc)
    elif active and not until:
        active = False
    if not active:
        return {"active": False}
    return {
        "active": True,
        "until": until,
        "by": obj.get("by") or "",
        "reason": obj.get("reason") or "",
        "remaining_min": remaining,
    }


def tool_supervisor_status(_args):
    """读取管家当前判定：分数 / 等级 / 是否维护模式。"""
    session = _read_json(SESSION_FILE) or {}
    rules = _read_json(RULES_FILE) or {}
    anti = _load_anti()

    score = None
    level = "UNKNOWN"
    reasons = []
    integrity = "未知"
    notes = []

    if anti is not None and session:
        r = rules or getattr(anti, "DEFAULT_RULES", {})
        try:
            with _SilenceStdout():
                score, level, reasons, ok, detail = anti.score_session(session, r)
            integrity = detail
        except Exception as exc:
            log("score_session 失败: %r" % exc)
            notes.append("调用管家评分失败，已降级为原始计数：%r" % exc)
    else:
        if not SESSION_FILE.exists():
            notes.append("未找到会话状态文件：%s" % SESSION_FILE)
        if not RULES_FILE.exists():
            notes.append("未找到规则文件：%s" % RULES_FILE)
        if anti is None:
            notes.append("未能加载 anti-degradation 评分模块，等级无法计算。")

    summary = "管家判定：等级=%s，分数=%s，维护模式=%s" % (
        level, score if score is not None else "未知",
        "开启" if _maintenance_summary().get("active") else "关闭")
    if notes:
        summary += "（部分数据缺失，已优雅降级）"

    return {
        "ok": bool(session),
        "summary": summary,
        "score": score,
        "level": level,
        "reasons": reasons,
        "rules_integrity": integrity,
        "maintenance": _maintenance_summary(),
        "session_id": session.get("session_id"),
        "codex_session_id": session.get("codex_session_id"),
        "turns": session.get("turns"),
        "sources": {
            "session": str(SESSION_FILE) if SESSION_FILE.exists() else None,
            "rules": str(RULES_FILE) if RULES_FILE.exists() else None,
        },
        "notes": notes,
    }


def tool_failure_modes(args):
    """返回失败模式库，可按 area（codex / obsidian）筛选。"""
    area = str((args or {}).get("area") or "").strip().lower()
    modes = load_failure_modes()
    if area:
        modes = [m for m in modes if str(m.get("area", "")).lower() == area]
    return {
        "ok": True,
        "area": area or "all",
        "count": len(modes),
        "modes": modes,
    }


def tool_check_text(args):
    """对一段文本做确定性失败模式自检（复用 checkers.check_turn）。"""
    text = str((args or {}).get("text") or "")
    if not text.strip():
        return {"ok": False, "error": "缺少 text 参数（要自检的文本为空）", "findings": []}
    checkers = _load_checkers()
    if checkers is None or not hasattr(checkers, "check_turn"):
        return {"ok": False, "error": "checkers.py 不可用，无法执行自检", "findings": []}
    try:
        with _SilenceStdout():
            findings = checkers.check_turn(text, text, text)
    except Exception as exc:
        log("check_turn 失败: %r" % exc)
        return {"ok": False, "error": "自检执行异常：%r" % exc, "findings": []}
    findings = list(findings or [])
    return {"ok": True, "count": len(findings), "findings": findings}


# ------------------------------------------------------------------ MCP 契约
TOOLS = [
    {
        "name": "supervisor_status",
        "description": "读取管家当前判定：分数、等级（NORMAL/WATCH/DEGRADED/BLOCKED）、是否处于外部维护模式。用于确认自己是否已被监督层判为降智或阻断。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "failure_modes",
        "description": "返回管家的失败模式库（codex / obsidian）。area 可选；缺省返回全部条目。写收尾结论前用它核对是否踩中已知缺点。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "enum": ["codex", "obsidian"],
                         "description": "只返回该领域的失败模式"}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "check_text",
        "description": "对一段文本（如本轮动作 + 收尾声明）做确定性失败模式自检，返回命中的失败模式与证据。声称完成/已修复/已验证前应先调用。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要自检的文本"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
]

_DISPATCH = {
    "supervisor_status": tool_supervisor_status,
    "failure_modes": tool_failure_modes,
    "check_text": tool_check_text,
}


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _handle_tools_call(msg_id, params):
    params = params or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    fn = _DISPATCH.get(name)
    if fn is None:
        return _error(msg_id, -32602, "未知工具: %s" % name)
    try:
        result = fn(args)
        is_err = isinstance(result, dict) and result.get("ok") is False
    except Exception as exc:
        log("工具 %s 执行失败: %r" % (name, exc))
        result = {"ok": False, "error": "工具执行异常：%r" % exc}
        is_err = True
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return _ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": bool(is_err)})


def dispatch(msg):
    """把一条 JSON-RPC 消息派发成响应；通知类返回 None。"""
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        params = msg.get("params") or {}
        proto = params.get("protocolVersion") or DEFAULT_PROTOCOL
        return _ok(msg_id, {
            "protocolVersion": proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": PLUGIN_VERSION},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        return _handle_tools_call(msg_id, msg.get("params") or {})

    # 其它 notification/* 一律静默忽略（协议要求不响应通知）
    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    if msg_id is None:
        return None
    return _error(msg_id, -32601, "Method not found: %s" % method)


# ------------------------------------------------------------------ 启动报告
def write_report():
    """每次成功启动写一份自检报告到 ~/.codex/supervisor-plugin-reports/codex.json。"""
    modes = load_failure_modes()
    modes_total = len(modes)
    auto_modes = sum(1 for m in modes if str(m.get("auto")) == "yes")
    codex_auto = [m for m in modes
                  if str(m.get("area")) == "codex" and str(m.get("auto")) == "yes"]
    checks = [
        {
            "id": m.get("id"),
            "status": "ok",
            "count": 0,
            "detail": "已加载：%s" % (m.get("title") or ""),
        }
        for m in codex_auto
    ]
    report = {
        "plugin": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "ts": _now_iso_local(),
        "healthy": modes_total > 0,
        "summary": ("插件就绪，%d 项 Codex 失败模式已加载" % len(codex_auto))
                   if modes_total > 0 else "插件已启动，但失败模式库未找到",
        "checks": checks,
        "metrics": {"modes_total": modes_total, "auto_modes": auto_modes},
    }
    try:
        _write_json(REPORT_FILE, report)
    except Exception as exc:
        log("写报告失败: %r" % exc)
    return report


def serve():
    _force_utf8()
    write_report()
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as exc:
            log("跳过无法解析的输入: %r" % exc)
            continue
        if not isinstance(msg, dict):
            continue
        try:
            resp = dispatch(msg)
        except Exception as exc:
            log("处理消息异常: %r" % exc)
            resp = _error(msg.get("id"), -32603, "内部错误: %r" % exc) \
                if msg.get("id") is not None else None
        if resp is None:
            continue
        try:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as exc:
            log("写响应失败: %r" % exc)


def main():
    try:
        return serve() or 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        log("服务器异常退出: %r" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())