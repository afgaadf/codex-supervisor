#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_mcp.py —— 对 mcp_server.py 做端到端自测。

启动 mcp_server.py 子进程，依次喂入 initialize / tools/list / tools/call，
校验回包结构。全部通过返回 0，否则返回 1。

用法：python selftest_mcp.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "mcp_server.py"


def _send(proc, obj):
    proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def _recv(proc):
    line = proc.stdout.readline()
    if not line:
        return None
    return json.loads(line)


def main():
    if not SERVER.exists():
        print("找不到 mcp_server.py：%s" % SERVER)
        return 1

    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
    )
    failures = []

    def check(name, resp, predicate):
        ok = bool(resp) and predicate(resp)
        print("[%s] %s" % ("PASS" if ok else "FAIL", name))
        print(json.dumps(resp, ensure_ascii=False)[:900])
        print("-" * 60)
        if not ok:
            failures.append(name)

    try:
        # 1. initialize
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05",
                                "capabilities": {}, "clientInfo": {"name": "selftest"}}})
        r = _recv(proc)
        check("initialize", r,
              lambda x: x.get("result", {}).get("serverInfo", {}).get("name") == "codex-supervisor")

        # 初始化完成通知（无 id，不应有回包）
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 2. tools/list
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        r = _recv(proc)
        names = []
        if r:
            names = [t.get("name") for t in r.get("result", {}).get("tools", [])]
        check("tools/list", r, lambda x: set(names) >= {"supervisor_status", "failure_modes", "check_text"})

        # 3. tools/call -> supervisor_status
        _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "supervisor_status", "arguments": {}}})
        r = _recv(proc)
        check("tools/call:supervisor_status", r,
              lambda x: "content" in x.get("result", {}))

        # 4. tools/call -> failure_modes (codex)
        _send(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "failure_modes", "arguments": {"area": "codex"}}})
        r = _recv(proc)
        check("tools/call:failure_modes", r,
              lambda x: "失败模式" in x.get("result", {}).get("content", [{}])[0].get("text", "")
                        or "\"modes\"" in x.get("result", {}).get("content", [{}])[0].get("text", ""))

        # 5. tools/call -> check_text
        _send(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                     "params": {"name": "check_text",
                                "arguments": {"text": "我已经完成了修复，测试全部通过，肯定没问题。"}}})
        r = _recv(proc)
        check("tools/call:check_text", r,
              lambda x: "content" in x.get("result", {}))

        # 6. 未知方法 -> -32601
        _send(proc, {"jsonrpc": "2.0", "id": 6, "method": "no/such/method", "params": {}})
        r = _recv(proc)
        check("unknown-method -> -32601", r,
              lambda x: x.get("error", {}).get("code") == -32601)

        # 7. ping
        _send(proc, {"jsonrpc": "2.0", "id": 7, "method": "ping"})
        r = _recv(proc)
        check("ping", r, lambda x: "result" in x)
    except Exception as exc:
        print("自测异常：%r" % exc)
        failures.append("exception")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        err = proc.stderr.read()
        if err.strip():
            print("--- stderr（诊断，不算失败）---")
            print(err.strip()[:1500])

    print("=" * 60)
    if failures:
        print("自测失败项：%s" % ", ".join(failures))
        return 1
    print("自测全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())