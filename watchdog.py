#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监督者 · 看门狗（原生窗口版）。

界面从"浏览器网页"改成"原生窗口"之后，**没有端口可探**了 —— 所以改看一个
心跳文件：原生窗口每次刷新都会写 `logs/app.heartbeat`。

  · 心跳够新                → 窗口活着，什么都不做
  · 心跳过期（窗口没了/卡死）→ 重新拉起（stdout/stderr 落盘，方便下次查崩溃原因）
  · 你点过界面上的「退出」   → 留了 `logs/app.stopped`，看门狗尊重你的意思，不重启

用法：
  pythonw watchdog.py             # 常驻（开机自启指向它）
  python  watchdog.py --once      # 只探一次，退出码 0=没事 / 1=没起来（自检）
  python  watchdog.py --interval 20
"""
import argparse
import datetime
import subprocess
import sys
import time
from pathlib import Path

from paths import APP_DIR, DATA_DIR, APP_DIR

LOG_DIR = DATA_DIR / "logs"
APP = APP_DIR / "supervisor_gui.py"      # 2026-10-06 起：新界面（PySide6）；旧 Tk 版留在 supervisor_app.py 当后备
WD_LOG = LOG_DIR / "watchdog.log"
APP_OUT = LOG_DIR / "app.out.log"
APP_ERR = LOG_DIR / "app.err.log"
HEARTBEAT = LOG_DIR / "app.heartbeat"
STOPPED = LOG_DIR / "app.stopped"
STALE_SEC = 60


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with WD_LOG.open("a", encoding="utf-8") as f:
        f.write("%s %s\n" % (ts, msg))


def heartbeat_age():
    try:
        return time.time() - HEARTBEAT.stat().st_mtime
    except Exception:
        return None


def app_alive():
    age = heartbeat_age()
    return age is not None and age < STALE_SEC


def user_stopped():
    return STOPPED.exists()


def pythonw():
    """优先用与当前解释器同目录的 pythonw.exe（无控制台窗口）。"""
    try:
        cand = Path(sys.executable).with_name("pythonw.exe")
        if cand.is_file():
            return str(cand)
    except Exception:
        pass
    return sys.executable


def start_app():
    if not APP.is_file():
        log("ERROR 找不到窗口脚本: %s" % APP)
        return None
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = APP_OUT.open("ab")
    err = APP_ERR.open("ab")
    try:
        p = subprocess.Popen(
            [pythonw(), str(APP)],
            cwd=str(APP_DIR),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        log("start app pid=%s stderr->%s" % (p.pid, APP_ERR))
        return p.pid
    except Exception as e:
        log("ERROR 启动窗口失败: %r" % (e,))
        return None
    finally:
        out.close()
        err.close()


def ensure_up(wait_s=30):
    if app_alive():
        return True
    if user_stopped():
        return True                      # 你关的，不算故障
    log("心跳过期 -> 重新拉起原生窗口")
    if start_app() is None:
        return False
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(2)
        if app_alive():
            log("原生窗口已恢复（心跳恢复更新）")
            return True
    log("WARN 拉起后 %ss 心跳仍未更新（看 %s）" % (wait_s, APP_ERR))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=20, help="探测间隔秒数")
    ap.add_argument("--once", action="store_true", help="只探一次后退出（自检）")
    args = ap.parse_args()

    log("watchdog start interval=%ss once=%s" % (args.interval, args.once))
    if args.once:
        ok = ensure_up()
        log("once done ok=%s" % ok)
        return 0 if ok else 1

    last = None
    while True:
        try:
            ok = ensure_up()
            age = heartbeat_age()
            state = ("up" if ok else "down") + ("/stopped" if user_stopped() else "")
            if state != last:            # 只在状态变化时记，免得日志刷屏
                log("state=%s heartbeat_age=%s" % (state, None if age is None else round(age, 1)))
                last = state
        except Exception as e:
            log("ERROR 看门狗异常: %r" % (e,))
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    sys.exit(main())
