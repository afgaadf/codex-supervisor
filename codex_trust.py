# -*- coding: utf-8 -*-
"""Codex hook 信任引擎 —— 严格按 Codex 官方标准 1:1 复刻。

来源（openai/codex @ main，2026-10-06 在线核对）：
  · codex-rs/hooks/src/engine/discovery.rs
        hook_hash(): identity = NormalizedHookIdentity{ event_name, group(只留 1 个 handler) }
                     normalize_command_hook(): 超时规范化
                       - SessionEnd/Interrupt: 默认 1s，上限 3s
                       - 其它事件:            默认 600s，下限 1s
  · codex-rs/config/src/fingerprint.rs
        version_for_toml():  TOML -> JSON -> 递归按键排序 -> 紧凑序列化 -> sha256 -> "sha256:<hex>"

本机交叉验证：用本模块重算 ~/.codex/hooks.json 的 11 条 handler，
与 ~/.codex/config.toml 里已存的 trusted_hash **11/11 完全一致**。
"""
from __future__ import annotations
import hashlib, json, re, tomllib
from pathlib import Path

CODEX_HOME = Path.home() / ".codex"
HOOKS_JSON = CODEX_HOME / "hooks.json"
CONFIG_TOML = CODEX_HOME / "config.toml"

# hooks.json 的事件键 -> 官方 hook_event_key_label（snake_case）
def event_label(event_key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event_key).lower()

def _normalize_timeout(label: str, timeout_sec):
    if label in ("session_end", "interrupt"):
        v = 1 if timeout_sec is None else timeout_sec
        return max(1, min(int(v), 3))
    v = 600 if timeout_sec is None else timeout_sec
    return max(1, int(v))

def normalize_handler(label: str, h: dict) -> dict:
    """把一个 hooks.json handler 规范化成官方哈希输入里的形态。"""
    d = {"type": h["type"], "command": h["command"]}
    if h.get("commandWindows") is not None:
        d["commandWindows"] = h["commandWindows"]
    d["timeout"] = _normalize_timeout(label, h.get("timeout"))
    d["async"] = bool(h.get("async", False))
    if h.get("statusMessage") is not None:
        d["statusMessage"] = h["statusMessage"]
    if h.get("additionalContextLimit") is not None:
        d["additionalContextLimit"] = h["additionalContextLimit"]
    return d

def hook_hash(event_key: str, group: dict, handler_index: int = 0) -> str:
    label = event_label(event_key)
    ident = {"event_name": label}
    if group.get("matcher") is not None:
        ident["matcher"] = group["matcher"]
    ident["hooks"] = [normalize_handler(label, group["hooks"][handler_index])]
    blob = json.dumps(ident, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()

def handler_key(event_key: str, group_index: int, handler_index: int) -> str:
    return f"{HOOKS_JSON}:{event_label(event_key)}:{group_index}:{handler_index}"

def load_hooks() -> dict:
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))

def load_state() -> dict:
    try:
        cfg = tomllib.loads(CONFIG_TOML.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return ((cfg.get("hooks") or {}).get("state") or {})

def list_handlers() -> list[dict]:
    """列出所有 handler 及其信任状态（Trusted / Modified / New）。"""
    hooks = load_hooks().get("hooks", {})
    state = load_state()
    out = []
    for ev, groups in hooks.items():
        for gi, g in enumerate(groups):
            for hi in range(len(g.get("hooks", []) or [])):
                h = g["hooks"][hi]
                key = handler_key(ev, gi, hi)
                cur = hook_hash(ev, g, hi)
                rec = state.get(key) or {}
                trusted = rec.get("trusted_hash")
                if trusted is None:
                    status = "new"          # 从未被信任
                elif trusted == cur:
                    status = "trusted"      # 定义未变
                else:
                    status = "modified"     # 定义变了 -> 需要重新审阅
                out.append({
                    "key": key, "event": ev, "event_label": event_label(ev),
                    "group_index": gi, "handler_index": hi,
                    "matcher": g.get("matcher"),
                    "command": h.get("command"), "timeout": h.get("timeout"),
                    "status": status, "trusted_hash": trusted, "current_hash": cur,
                    "enabled": rec.get("enabled"),
                })
    return out
