# -*- coding: utf-8 -*-
"""pc_butler.py —— 电脑管家后台进程（不依赖窗口，关掉窗口也继续跑）

常驻职责：
  · 监管循环：monitor_loop（看 Codex 会话）/ brain（自主学习）/ judge_loop（判分）
  · 本机 API：127.0.0.1:8765（界面、Obsidian 插件、脚本都靠它）
  · 知识库自动运维：每 30 分钟 auto_ops（体检/分类/修可确定断链/补 fm/日报/提交）
  · 本机采样：每 5 分钟记曲线 + 越线告警
  · 心跳：logs/butler.heartbeat（界面/看门狗据此判断它是否活着）
用法：pythonw pc_butler.py（无窗口）  |  python pc_butler.py --once（自检跑一轮就退）
"""
from __future__ import annotations
import json, os, socketserver, sys, threading, time
from datetime import datetime
from pathlib import Path

from paths import DATA_DIR, APP_DIR

sys.path.insert(0, str(APP_DIR))
LOG_DIR = DATA_DIR / "logs"
LOG = LOG_DIR / "butler.log"
HB = LOG_DIR / "butler.heartbeat"
PID = LOG_DIR / "butler.pid"


def log(msg: str):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().isoformat(timespec="seconds"), msg))
        if LOG.stat().st_size > 2 * 1024 * 1024:
            lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
            LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _guard(name, fn):
    def run():
        while True:
            try:
                fn()
            except Exception as e:
                log("%s 异常：%r" % (name, e))
                time.sleep(30)
    return run


def api_serve():
    import supervisor_ui as ui
    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.TCPServer((ui.HOST, ui.PORT), ui.H)
    except OSError as e:
        log("端口被占，API 不启动：%s" % e)
        return
    log("本机 API 已监听 http://%s:%s/" % (ui.HOST, ui.PORT))
    httpd.serve_forever()


def brain_loop():
    import brain as B
    B.run_once()
    B.loop(45)


def vault_loop():
    while True:
        try:
            import importlib
            import vault_ops as VO
            importlib.reload(VO)
            r = VO.auto_ops()
            log("自动运维：健康分=%s 做了=%s 需要人=%s" % (r.get("health"), r.get("did"), r.get("need_human")))
        except Exception as e:
            log("自动运维失败：%r" % e)
        time.sleep(30 * 60)


def pc_loop():
    while True:
        try:
            import pc_guard as P
            P.sample()
            P.check_alerts()
        except Exception as e:
            log("采样失败：%r" % e)
        time.sleep(5 * 60)


def heartbeat_loop():
    while True:
        try:
            HB.write_text(str(int(time.time())), encoding="utf-8")
        except Exception:
            pass
        time.sleep(30)


def alive():
    try:
        pid = int(PID.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def main():
    once = "--once" in sys.argv
    if alive() and not once:
        log("已有后台管家在跑，退出")
        return 0
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID.write_text(str(os.getpid()), encoding="utf-8")
    import supervisor_ui as ui
    try:
        ui.write_plugin_token()
    except Exception:
        pass
    log("后台管家启动 pid=%s once=%s" % (os.getpid(), once))
    if once:
        import vault_ops as VO
        r = VO.auto_ops()
        log("自检一轮：健康分=%s 做了=%s" % (r.get("health"), r.get("did")))
        return 0
    for name, fn in (("monitor", ui.monitor_loop), ("brain", brain_loop), ("judge", ui.judge_loop),
                     ("api", api_serve), ("vault", vault_loop), ("pc", pc_loop)):
        threading.Thread(target=_guard(name, fn), daemon=True).start()
    heartbeat_loop()


if __name__ == "__main__":
    sys.exit(main() or 0)
