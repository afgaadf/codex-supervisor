# -*- coding: utf-8 -*-
r"""pc_guard.py —— 监督者 · 电脑管家（本机体检 + 受控动作）

依据（T1，2026-10-06/07 联网核对）：
  · 启动项注册表位置：Microsoft Learn「运行和 RunOnce 注册表项」
    HKLM/HKCU\Software\Microsoft\Windows\CurrentVersion\Run[Once]
  · 性能数据接口：Microsoft Learn「关于性能计数器」（CPU/内存/磁盘的一致接口）
  · 能力对照：psutil 官方文档（本机未安装 → 改用系统自带 CIM/PowerShell，不引依赖）
本机实测补充（非标准，标为"本机实测"）：启动文件夹两个路径、Win32_StartupCommand 类。
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LOG_DIR = APP_DIR / "logs"
PC_STATE = APP_DIR / "pc_state.json"          # 最近一次体检结果
PC_AUDIT = APP_DIR / "pc_audit.jsonl"         # 管家动作审计
PC_HISTORY = LOG_DIR / "pc_history.jsonl"     # 采样曲线（CPU/内存/磁盘）
PC_ALERTS = LOG_DIR / "pc_alerts.jsonl"       # 越过阈值的告警历史
STARTUP_BK = APP_DIR / "startup_backups.json" # 启动项备份（禁用前先存原值）
FOLDER_BK = APP_DIR / "startup_folder_backup" # 启动文件夹项备份（移动，不删）

PWSH = shutil.which("powershell") or shutil.which("pwsh") or "powershell"

# ---------------------------------------------------------------- 采集
PS_COLLECT = r'''
$ErrorActionPreference = "SilentlyContinue"
$out = [ordered]@{}
$os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, LastBootUpTime, TotalVisibleMemorySize, FreePhysicalMemory
$out.os = $os
$out.cpu = Get-CimInstance Win32_Processor | Select-Object Name, LoadPercentage, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed
$out.disk = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID, VolumeName, Size, FreeSpace, FileSystem
$out.top = Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 15 Id, ProcessName, WorkingSet64, CPU, Path
$out.services = Get-CimInstance Win32_Service | Where-Object { $_.StartMode -eq "Auto" -and $_.State -ne "Running" } | Select-Object Name, DisplayName, State, StartMode
$out.startup = @()
$keys = @(
  "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run",
  "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce",
  "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run",
  "HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce",
  "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
)
foreach ($k in $keys) {
  if (Test-Path $k) {
    $p = Get-ItemProperty -Path $k
    foreach ($n in $p.PSObject.Properties) {
      if ($n.Name -notlike "PS*") {
        $out.startup += [ordered]@{ name = $n.Name; command = [string]$n.Value; where = $k; source = "registry" }
      }
    }
  }
}
$folders = @(
  (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"),
  (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\StartUp")
)
foreach ($f in $folders) {
  if (Test-Path $f) {
    Get-ChildItem -LiteralPath $f -File | ForEach-Object {
      $out.startup += [ordered]@{ name = $_.Name; command = $_.FullName; where = $f; source = "startup_folder" }
    }
  }
}
$out.tasks = Get-ScheduledTask | Where-Object { $_.State -ne "Disabled" -and $_.Triggers.CimClass.CimClassName -contains "MSFT_TaskBootTrigger" } | Select-Object -First 20 TaskName, TaskPath, State
$out.hotfix = Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1 HotFixID, InstalledOn
$out.net = Get-NetAdapter -Physical | Select-Object Name, Status, LinkSpeed, MacAddress
$out.uptime_sec = [int]((Get-Date) - $os.LastBootUpTime).TotalSeconds
$out | ConvertTo-Json -Depth 5 | Out-File -FilePath "__OUT__" -Encoding utf8
'''

def collect(timeout=90):
    """跑一次系统采集。返回 dict（失败时 ok=False）。"""
    tmp = Path(tempfile.gettempdir()) / ("sb_pc_%d.json" % os.getpid())
    script = PS_COLLECT.replace("__OUT__", str(tmp))
    t0 = time.time()
    try:
        p = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                           capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        raw = tmp.read_text(encoding="utf-8-sig", errors="replace") if tmp.exists() else ""
        try:
            tmp.unlink()
        except Exception:
            pass
        if not raw.strip():
            return {"ok": False, "error": "采集无输出", "stderr": (p.stderr or "")[-400:]}
        data = json.loads(raw)
        data["ok"] = True
        data["ts"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        data["elapsed_ms"] = int((time.time() - t0) * 1000)
        return data
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 规则（体检）
def _gb(n):
    try:
        return float(n) / 1024 ** 3
    except Exception:
        return 0.0


def scan(data=None):
    """体检：按规则打分 + 列问题。等级口径和监管者一致（NORMAL/WATCH/DEGRADED/BLOCKED）。"""
    d = data or collect()
    if not d.get("ok"):
        return {"ok": False, "error": d.get("error") or "采集失败", "level": "WATCH", "score": 0, "items": []}
    items = []
    os_ = d.get("os") or {}
    cpu = d.get("cpu") or {}
    if isinstance(cpu, list):
        cpu = cpu[0] if cpu else {}
    total_mb = float(os_.get("TotalVisibleMemorySize") or 0) / 1024
    free_mb = float(os_.get("FreePhysicalMemory") or 0) / 1024
    used_pct = (1 - free_mb / total_mb) * 100 if total_mb else 0
    used_mb = max(0.0, total_mb - free_mb)
    cpu_pct = float(cpu.get("LoadPercentage") or 0)

    disks = d.get("disk") or []
    if isinstance(disks, dict):
        disks = [disks]
    for dk in disks:
        total = float(dk.get("Size") or 0)
        free = float(dk.get("FreeSpace") or 0)
        if total <= 0:
            continue
        pct = free / total * 100
        if pct < 10:
            items.append({"kind": "disk_low", "sev": "要紧", "title": "%s 盘只剩 %.1f%%" % (dk.get("DeviceID"), pct),
                          "evidence": "剩余 %.1f GB / 共 %.1f GB" % (_gb(free), _gb(total)),
                          "advice": "清理大文件或临时文件（管家可先出清单再清）"})
        elif pct < 20:
            items.append({"kind": "disk_low", "sev": "注意", "title": "%s 盘剩余 %.1f%%" % (dk.get("DeviceID"), pct),
                          "evidence": "剩余 %.1f GB" % _gb(free), "advice": "留意，别让它掉到 10% 以下"})

    if used_pct >= 88:
        items.append({"kind": "mem_high", "sev": "要紧", "title": "内存占用 %.0f%%" % used_pct,
                      "evidence": "已用 %.1f GB / 共 %.1f GB，空闲 %.1f GB" % (
                          _gb(used_mb * 1024 * 1024), _gb(total_mb * 1024 * 1024), _gb(free_mb * 1024 * 1024)),
                      "advice": "看 Top 进程，重的可以结束；持续高就加内存"})
    elif used_pct >= 75:
        items.append({"kind": "mem_high", "sev": "注意", "title": "内存占用 %.0f%%" % used_pct,
                      "evidence": "空闲 %.1f GB" % _gb(free_mb * 1024 * 1024), "advice": "盯着点，别再开重活"})

    if cpu_pct >= 85:
        items.append({"kind": "cpu_high", "sev": "注意", "title": "CPU 瞬时 %.0f%%" % cpu_pct,
                      "evidence": "CIM 的 LoadPercentage 是瞬时值", "advice": "看 Top 进程确认是不是正常任务"})

    startup = d.get("startup") or []
    if isinstance(startup, dict):
        startup = [startup]
    if len(startup) > 15:
        items.append({"kind": "startup_many", "sev": "注意", "title": "开机自启 %d 项" % len(startup),
                      "evidence": "、".join(str(s.get("name")) for s in startup[:6]) + "…",
                      "advice": "不常用的可以关（关之前管家会先备份注册表值）"})

    top = d.get("top") or []
    if isinstance(top, dict):
        top = [top]
    heavy = [t for t in top if float(t.get("WorkingSet64") or 0) > 1024 ** 3]
    if heavy:
        items.append({"kind": "proc_heavy", "sev": "注意",
                      "title": "%d 个进程占用超 1GB" % len(heavy),
                      "evidence": "、".join("%s %.1fGB" % (t.get("ProcessName"), float(t.get("WorkingSet64")) / 1024 ** 3) for t in heavy[:4]),
                      "advice": "确认是不是需要常驻；不需要可以结束"})

    hf = d.get("hotfix") or {}
    if isinstance(hf, list):
        hf = hf[0] if hf else {}
    try:
        inst = hf.get("InstalledOn")
        if isinstance(inst, dict):
            inst = inst.get("DateTime") or inst.get("value") or ""
        installed = str(inst or "")
        if installed.startswith("/Date("):
            import datetime as _dt
            try:
                ms = int(installed.replace("/Date(", "").split(")")[0].split("+")[0].split("-")[0])
                installed = _dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
            except Exception:
                installed = ""
        if installed:
            import re as _re
            m = _re.search(r"(\d{4})", installed)
            year = int(m.group(1)) if m else 0
            if year and year < datetime.now().year - 0 and year < datetime.now().year:
                items.append({"kind": "patch_old", "sev": "注意", "title": "最近一次补丁是 %s" % installed,
                              "evidence": "HotFixID=%s" % hf.get("HotFixID"), "advice": "检查 Windows Update"})
    except Exception:
        pass

    weight = {"要紧": 22, "注意": 8, "还好": 0}
    penalty = sum(weight.get(i.get("sev"), 0) for i in items)
    score = max(0, 100 - penalty)
    level = "NORMAL" if score >= 88 else "WATCH" if score >= 70 else "DEGRADED" if score >= 40 else "BLOCKED"
    res = {"ok": True, "ts": d.get("ts"), "level": level, "score": score,
           "summary": {"cpu_pct": round(cpu_pct, 1), "mem_pct": round(used_pct, 1),
                       "mem_total_gb": round(_gb(total_mb * 1024 * 1024), 1),
                       "mem_used_gb": round(_gb(used_mb * 1024 * 1024), 1),
                       "startup": len(startup), "procs_top": len(top),
                       "disks": [{"id": x.get("DeviceID"), "free_gb": round(_gb(x.get("FreeSpace")), 1),
                                  "total_gb": round(_gb(x.get("Size")), 1)} for x in disks],
                       "uptime_h": round(float(d.get("uptime_sec") or 0) / 3600, 1),
                       "host": os_.get("Caption"), "last_boot": os_.get("LastBootUpTime"),
                       "hotfix": hf.get("HotFixID")},
           "items": items, "startup": startup, "top": top,
           "services_stopped": d.get("services") or [], "net": d.get("net") or []}
    try:
        PC_STATE.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return res


# ---------------------------------------------------------------- 采样曲线 + 告警
def _jsonl_tail(path, n):
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


def sample():
    """采一次曲线点（CPU/内存/C盘剩余）。每个点只查一次，开销小。"""
    ps = ("$ErrorActionPreference='SilentlyContinue';"
          "$os=Get-CimInstance Win32_OperatingSystem;"
          "$cpu=Get-CimInstance Win32_Processor|Select-Object -First 1;"
          "$d=Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='C:'\";"
          "$o=[ordered]@{cpu=[double]$cpu.LoadPercentage;"
          "mem=[math]::Round((1-($os.FreePhysicalMemory/$os.TotalVisibleMemorySize))*100,1);"
          "c_free=[math]::Round($d.FreeSpace/1GB,1);c_total=[math]::Round($d.Size/1GB,1)};"
          "$o|ConvertTo-Json -Compress")
    try:
        r = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, text=True, timeout=45, encoding="utf-8", errors="replace")
        d = json.loads((r.stdout or "").strip() or "{}")
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    d["ts"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    d["ok"] = True
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with PC_HISTORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        if PC_HISTORY.stat().st_size > 4 * 1024 * 1024:
            lines = PC_HISTORY.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
            PC_HISTORY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass
    return d


def history(n=120):
    return _jsonl_tail(PC_HISTORY, n)


def alerts(n=50):
    return _jsonl_tail(PC_ALERTS, n)


def check_alerts():
    """把这次的曲线点跟阈值比，越线就记一条告警（同一种连续越线只记一次）。"""
    pts = _jsonl_tail(PC_HISTORY, 2)
    if not pts:
        return None
    cur = pts[-1]
    prev = pts[-2] if len(pts) > 1 else {}
    rules = [("cpu", 90, "要紧", "CPU 打到 %.0f%%"),
             ("mem", 88, "要紧", "内存占用 %.0f%%"),
             ("mem", 75, "注意", "内存占用 %.0f%%")]
    made = []
    for key, limit, sev, tpl in rules:
        try:
            v = float(cur.get(key) or 0)
            pv = float(prev.get(key) or 0)
        except Exception:
            continue
        if v >= limit and pv < limit:          # 只在"刚越线"时记一次
            rec = {"ts": cur.get("ts"), "kind": "pc_" + key, "sev": sev,
                   "detail": tpl % v, "value": v, "limit": limit}
            try:
                with PC_ALERTS.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass
            made.append(rec)
    try:
        cf, ct = float(cur.get("c_free") or 0), float(cur.get("c_total") or 0)
        pf = float(prev.get("c_free") or 0)
        if ct and cf / ct < 0.10 and (not pf or pf / ct >= 0.10):
            rec = {"ts": cur.get("ts"), "kind": "pc_disk", "sev": "要紧",
                   "detail": "C 盘剩余 %.1f GB（%.1f%%）" % (cf, cf / ct * 100), "value": cf}
            with PC_ALERTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            made.append(rec)
    except Exception:
        pass
    return made


# ---------------------------------------------------------------- 启动项：禁用 / 恢复（先备份原值）
def _bk_load():
    try:
        return json.loads(STARTUP_BK.read_text(encoding="utf-8"))
    except Exception:
        return []


def _bk_save(rows):
    STARTUP_BK.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def startup_backups():
    return _bk_load()


def startup_action(where, name, command="", enable=False, dry_run=True):
    """禁用 = 先把原值存进 startup_backups.json，再从 Run 键删除；恢复 = 按备份写回。

    依据：Microsoft Learn《运行和 RunOnce 注册表项》（Run 键里的**值**就是开机要跑的命令）。
    备份-再删 是本项目设计（非权威），为的是随时能一键恢复。
    """
    where = str(where or "")
    name = str(name or "")
    if where.startswith("HKLM") and not enable:
        pass  # 允许尝试，但会明确报"需要管理员"
    rec = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
           "action": "startup_disable" if not enable else "startup_restore",
           "where": where, "name": name, "dry_run": bool(dry_run)}
    if not name or not where.startswith("HK"):
        return {"ok": False, "error": "只支持注册表启动项（where 必须以 HK 开头）"}
    try:
        if not enable:
            ps = ("$p='%s';$n='%s';"
                  "$v=(Get-ItemProperty -Path $p -Name $n).$n;"
                  "Write-Output ('VALUE=' + [string]$v)") % (where, name)
            if dry_run:
                r = subprocess.run([PWSH, "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30,
                                   encoding="utf-8", errors="replace")
                val = (r.stdout or "").replace("VALUE=", "").strip()
                out = {"ok": True, "dry_run": True, "would": "备份原值后，从 %s 删除启动项 %s" % (where, name),
                       "current_value": val[:200]}
            else:
                r0 = subprocess.run([PWSH, "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30,
                                    encoding="utf-8", errors="replace")
                val = (r0.stdout or "").replace("VALUE=", "").strip()
                if not val:
                    return {"ok": False, "error": "读不到原值，没动手（可能没有管理员权限，或该项已不存在）"}
                rows = _bk_load()
                rows.append({"ts": rec["ts"], "where": where, "name": name, "command": val})
                _bk_save(rows)
                ps2 = ("Remove-ItemProperty -Path '%s' -Name '%s' -ErrorAction Stop" % (where, name))
                r = subprocess.run([PWSH, "-NoProfile", "-Command", ps2], capture_output=True, text=True, timeout=30,
                                   encoding="utf-8", errors="replace")
                out = {"ok": r.returncode == 0, "disabled": name, "backed_up": val[:200],
                       "error": ((r.stderr or "").strip()[-200:] or None) if r.returncode else None,
                       "hint": "HKLM 需要管理员；失败就是没权限" if r.returncode else None}
        else:
            rows = _bk_load()
            hit = next((x for x in reversed(rows) if x.get("name") == name and x.get("where") == where), None)
            if not hit:
                return {"ok": False, "error": "没有这个启动项的备份，不猜"}
            if dry_run:
                out = {"ok": True, "dry_run": True, "would": "把 %s 写回 %s" % (name, where),
                       "command": str(hit.get("command"))[:200]}
            else:
                ps = "Set-ItemProperty -Path '%s' -Name '%s' -Value '%s' -ErrorAction Stop" % (
                    where, name, str(hit.get("command")).replace("'", "''"))
                r = subprocess.run([PWSH, "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30,
                                   encoding="utf-8", errors="replace")
                out = {"ok": r.returncode == 0, "restored": name,
                       "error": ((r.stderr or "").strip()[-200:] or None) if r.returncode else None}
    except Exception as e:
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    rec["result"] = out
    _audit(rec)
    out["audit_ts"] = rec["ts"]
    return out


# ---------------------------------------------------------------- 垃圾/大文件扫描（只报，不乱动）
def _du(path, older_than_days=None, limit_files=20000):
    """算目录占用 + 文件数（可选只算 N 天前的）。返回 (bytes, files, skipped)。"""
    total, n, skipped = 0, 0, 0
    cutoff = time.time() - older_than_days * 86400 if older_than_days else None
    try:
        for root, dirs, names in os.walk(path, onerror=lambda e: None):
            for nm in names:
                fp = os.path.join(root, nm)
                try:
                    st = os.stat(fp)
                except Exception:
                    skipped += 1
                    continue
                if cutoff and st.st_mtime >= cutoff:
                    continue
                total += st.st_size
                n += 1
                if n >= limit_files:
                    return total, n, skipped
    except Exception:
        pass
    return total, n, skipped


def junk_scan():
    """扫可清理项。**只报告**，清理要人确认。路径来源：TEMP 用系统变量；下载/浏览器缓存为本机实测。"""
    home = os.path.expanduser("~")
    cats = []
    tmp = tempfile.gettempdir()
    b, n, sk = _du(tmp, older_than_days=7)
    cats.append({"key": "user_temp", "name": "用户临时文件（7 天前的）", "path": tmp,
                 "bytes": b, "files": n, "skipped": sk, "cleanable": True,
                 "note": "TEMP 目录，系统变量指向"})
    rb = 0
    try:
        ps = ("$s=New-Object -ComObject Shell.Application;$rb=$s.Namespace(10);"
              "$sum=0;foreach($i in $rb.Items()){$sum+=$i.Size};Write-Output $sum")
        r = subprocess.run([PWSH, "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           timeout=45, encoding="utf-8", errors="replace")
        rb = int((r.stdout or "0").strip() or 0)
    except Exception:
        rb = 0
    cats.append({"key": "recycle_bin", "name": "回收站", "path": "（当前用户）", "bytes": rb, "files": None,
                 "cleanable": False,
                 "note": "本机实测：官方 Clear-RecycleBin 报「The system cannot find the path specified」，"
                         "所以管家只显示大小、不提供清理按钮（不假装能用）"})
    caches = []
    for nm, rel in (("Edge 缓存", r"AppData\Local\Microsoft\Edge\User Data\Default\Cache"),
                    ("Chrome 缓存", r"AppData\Local\Google\Chrome\User Data\Default\Cache")):
        cpath = os.path.join(home, rel)
        if os.path.isdir(cpath):
            cb, cn, _ = _du(cpath, older_than_days=7)
            caches.append({"key": "browser_cache", "name": nm, "path": cpath, "bytes": cb, "files": cn,
                           "cleanable": True, "note": "本机实测路径；只删 7 天前的缓存文件"})
    cats.extend(caches)
    dl = os.path.join(home, "Downloads")
    if os.path.isdir(dl):
        tops = []
        for root, dirs, names in os.walk(dl):
            for nm in names:
                fp = os.path.join(root, nm)
                try:
                    st = os.stat(fp)
                except Exception:
                    continue
                tops.append((st.st_size, fp, st.st_mtime))
        tops.sort(reverse=True)
        big = [t for t in tops if t[0] > 200 * 1024 * 1024]
        cats.append({"key": "downloads_big", "name": "下载目录大文件（>200MB）", "path": dl,
                     "bytes": sum(t[0] for t in big), "files": len(big), "cleanable": False,
                     "note": "这是你的文件，管家只列出来，不动",
                     "sample": [{"path": t[1], "mb": round(t[0] / 1024 ** 2, 1)} for t in big[:8]]})
    wu = r"C:\Windows\SoftwareDistribution\Download"
    if os.path.isdir(wu):
        wb, wn, wsk = _du(wu)
        cats.append({"key": "win_update_cache", "name": "Windows 更新缓存", "path": wu,
                     "bytes": wb, "files": wn, "skipped": wsk, "cleanable": False,
                     "note": "需要管理员；管家的清理白名单里没有它"})
    total = sum(c.get("bytes") or 0 for c in cats if c.get("cleanable"))
    return {"ok": True, "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "categories": cats, "cleanable_bytes": total}


def junk_clean(target, dry_run=True):
    """清理白名单：user_temp / recycle_bin / browser_cache。"""
    home = os.path.expanduser("~")
    rec = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
           "action": "junk_clean", "target": target, "dry_run": bool(dry_run)}
    try:
        if target == "user_temp":
            out = act("clear_temp", dry_run=dry_run)
        elif target == "recycle_bin":
            out = {"ok": False, "unsupported": True,
                   "error": "本机实测 Clear-RecycleBin 不可用（报找不到路径），管家不提供该清理；"
                            "若要清空回收站请用资源管理器手动清空"}
            rec["result"] = out
            _audit(rec)
            out["audit_ts"] = rec["ts"]
            return out
        elif target == "recycle_bin_legacy":
            if dry_run:
                ps = "Clear-RecycleBin -Force -WhatIf"
                r = subprocess.run([PWSH, "-NoProfile", "-Command", ps], capture_output=True, text=True,
                                   timeout=45, encoding="utf-8", errors="replace")
                out = {"ok": r.returncode == 0, "dry_run": True,
                       "would": "清空当前用户回收站（官方 Clear-RecycleBin -Force）",
                       "ps_output": ((r.stdout or "") + (r.stderr or ""))[:300]}
            else:
                r = subprocess.run([PWSH, "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction Stop"],
                                   capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
                out = {"ok": r.returncode == 0, "cleared": "recycle_bin",
                       "error": ((r.stderr or "").strip()[-200:] or None) if r.returncode else None}
        elif target == "browser_cache":
            removed = errs = 0
            freed = 0
            cutoff = time.time() - 7 * 86400
            for rel in (r"AppData\Local\Microsoft\Edge\User Data\Default\Cache",
                        r"AppData\Local\Google\Chrome\User Data\Default\Cache"):
                cpath = os.path.join(home, rel)
                if not os.path.isdir(cpath):
                    continue
                for root, dirs, names in os.walk(cpath):
                    for nm in names:
                        fp = os.path.join(root, nm)
                        try:
                            st = os.stat(fp)
                            if st.st_mtime >= cutoff:
                                continue
                            if dry_run:
                                freed += st.st_size
                                removed += 1
                            else:
                                sz = st.st_size
                                os.unlink(fp)
                                freed += sz
                                removed += 1
                        except Exception:
                            errs += 1
            out = {"ok": True, "dry_run": bool(dry_run), "files": removed, "mb": round(freed / 1024 ** 2, 1),
                   "errors": errs,
                   "would" if dry_run else "removed": "浏览器缓存里 7 天前的文件"}
        else:
            return {"ok": False, "error": "不在清理白名单：user_temp / recycle_bin / browser_cache"}
    except Exception as e:
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    rec["result"] = {k: v for k, v in out.items() if k != "sample"}
    _audit(rec)
    out["audit_ts"] = rec["ts"]
    return out


# ---------------------------------------------------------------- 启动文件夹项：移动备份（不删）
def startup_folder_action(path, enable=False, dry_run=True):
    path = str(path or "")
    name = os.path.basename(path)
    rec = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
           "action": "startup_folder_restore" if enable else "startup_folder_disable",
           "where": "startup_folder", "name": name, "dry_run": bool(dry_run)}
    if not os.path.isfile(path):
        return {"ok": False, "error": "找不到文件：%s" % path}
    FOLDER_BK.mkdir(parents=True, exist_ok=True)
    dst = FOLDER_BK / name
    try:
        if not enable:
            if dry_run:
                out = {"ok": True, "dry_run": True, "would": "把 %s 移到备份目录（可恢复）" % path}
            else:
                if dst.exists():
                    return {"ok": False, "error": "备份里已经有同名文件，先手工确认：%s" % dst}
                shutil.move(path, str(dst))
                out = {"ok": True, "moved": str(dst), "restore_with": name}
        else:
            src = FOLDER_BK / name
            if not src.exists():
                return {"ok": False, "error": "备份里没有这个文件：%s" % src}
            if dry_run:
                out = {"ok": True, "dry_run": True, "would": "把 %s 移回启动文件夹" % src}
            else:
                if os.path.exists(path):
                    return {"ok": False, "error": "启动文件夹里已有同名文件，不覆盖"}
                shutil.move(str(src), path)
                out = {"ok": True, "restored": path}
    except Exception as e:
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    rec["result"] = out
    _audit(rec)
    out["audit_ts"] = rec["ts"]
    return out


def startup_folder_backups():
    try:
        return [{"name": f.name, "path": str(f), "mb": round(f.stat().st_size / 1024, 1)}
                for f in FOLDER_BK.iterdir() if f.is_file()]
    except Exception:
        return []


# ---------------------------------------------------------------- 服务：只读清单 + 启动（要管理员）
def services_overview():
    d = collect()
    if not d.get("ok"):
        return {"ok": False, "error": d.get("error")}
    rows = d.get("services") or []
    if isinstance(rows, dict):
        rows = [rows]
    return {"ok": True, "ts": d.get("ts"), "count": len(rows), "auto_stopped": rows}


def service_start(name, dry_run=True):
    """启动服务：sc.exe start（T1：Microsoft Learn「Sc.exe query」同族命令）。需要管理员。"""
    name = str(name or "")
    if not name:
        return {"ok": False, "error": "要给服务名"}
    rec = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
           "action": "service_start", "name": name, "dry_run": bool(dry_run)}
    if dry_run:
        out = {"ok": True, "dry_run": True, "would": "启动服务 %s（需要管理员）" % name}
    else:
        r = subprocess.run(["sc.exe", "start", name], capture_output=True, text=True, timeout=45,
                           encoding="utf-8", errors="replace")
        txt = ((r.stdout or "") + (r.stderr or "")).strip()
        out = {"ok": r.returncode == 0 and "FAILED" not in txt.upper(), "service": name,
               "detail": txt[-260:],
               "hint": "Access is denied / 拒绝访问 = 需要管理员" if "denied" in txt.lower() or "拒绝" in txt else None}
    rec["result"] = out
    _audit(rec)
    out["audit_ts"] = rec["ts"]
    return out


# ---------------------------------------------------------------- 温度（本机实测，可能不支持）
def temps():
    ps = ("$ErrorActionPreference='SilentlyContinue';"
          "$a=Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature;"
          "$b=Get-CimInstance Win32_TemperatureProbe;"
          "[ordered]@{acpi=@($a|ForEach-Object{[math]::Round(($_.CurrentTemperature/10)-273.15,1)});"
          "probe=@($b|ForEach-Object{$_.CurrentReading})}|ConvertTo-Json -Compress")
    try:
        r = subprocess.run([PWSH, "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           timeout=45, encoding="utf-8", errors="replace")
        d = json.loads((r.stdout or "{}").strip() or "{}")
        return {"ok": True, "acpi_c": d.get("acpi") or [], "probe": d.get("probe") or [],
                "note": "MSAcpi_ThermalZoneTemperature 官方无文档、且是主板区域温度（非 CPU）—— 只作参考（本机实测）"}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 动作（白名单 + 审计，默认先预演）
ACTIONS = ("kill_process", "clear_temp", "open_task_manager", "startup_disable", "startup_restore",
           "startup_folder_disable", "startup_folder_restore", "service_start",
           "junk_clean", "recycle_bin")


def _audit(rec):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with PC_AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def act(action, dry_run=True, **kw):
    """只做白名单里的动作；dry_run=True 时只报"会做什么"，不真动手。"""
    if action == "junk_clean":
        return junk_clean(str(kw.get("target") or ""), dry_run=dry_run)
    if action == "recycle_bin":
        return junk_clean("recycle_bin", dry_run=dry_run)
    if action in ("startup_folder_disable", "startup_folder_restore"):
        return startup_folder_action(kw.get("path"), enable=(action == "startup_folder_restore"), dry_run=dry_run)
    if action == "service_start":
        return service_start(kw.get("name"), dry_run=dry_run)
    if action in ("startup_disable", "startup_restore"):
        return startup_action(kw.get("where"), kw.get("name"), kw.get("command") or "",
                              enable=(action == "startup_restore"), dry_run=dry_run)
    if action not in ACTIONS:
        return {"ok": False, "error": "动作不在白名单：%s（%s）" % (action, "、".join(ACTIONS))}
    rec = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
           "action": action, "dry_run": bool(dry_run), "args": kw}
    try:
        if action == "kill_process":
            pid = int(kw.get("pid") or 0)
            name = str(kw.get("name") or "")
            if not pid:
                return {"ok": False, "error": "要给 pid"}
            cur = scan()
            allowed = {int(t.get("Id") or 0) for t in cur.get("top") or []}
            if pid not in allowed:
                return {"ok": False, "error": "只允许结束当前 Top 列表里的进程（防误杀）"}
            if dry_run:
                out = {"ok": True, "dry_run": True, "would": "结束进程 %s (pid=%d)" % (name, pid)}
            else:
                p = subprocess.run([PWSH, "-NoProfile", "-Command", "Stop-Process -Id %d -Force" % pid],
                                   capture_output=True, text=True, timeout=30)
                out = {"ok": p.returncode == 0, "killed": pid, "name": name,
                       "stderr": (p.stderr or "")[-200:]}
        elif action == "clear_temp":
            tmp = Path(tempfile.gettempdir())
            cutoff = time.time() - 7 * 86400
            old = []
            for root, dirs, files in os.walk(tmp):
                for n in files:
                    fp = Path(root) / n
                    try:
                        if fp.stat().st_mtime < cutoff:
                            old.append(fp)
                    except Exception:
                        pass
                if len(old) > 5000:
                    break
            total = sum(f.stat().st_size for f in old if f.exists())
            if dry_run:
                out = {"ok": True, "dry_run": True, "files": len(old), "mb": round(total / 1024 ** 2, 1),
                       "sample": [str(f) for f in old[:10]],
                       "would": "删除 %d 个 7 天前的临时文件（约 %.1f MB）" % (len(old), total / 1024 ** 2)}
            else:
                # 真正删除前再拦一道：只删 temp 目录下的、且仍在 7 天前的
                base = str(tmp).lower()
                removed = 0
                errs = 0
                for f in old:
                    try:
                        if str(f).lower().startswith(base) and f.stat().st_mtime < cutoff:
                            f.unlink()
                            removed += 1
                    except Exception:
                        errs += 1
                out = {"ok": True, "removed": removed, "errors": errs, "mb": round(total / 1024 ** 2, 1)}
        else:  # open_task_manager
            if dry_run:
                out = {"ok": True, "dry_run": True, "would": "打开任务管理器"}
            else:
                subprocess.Popen(["taskmgr.exe"], close_fds=True)
                out = {"ok": True, "opened": "taskmgr"}
    except Exception as e:
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    rec["result"] = {k: v for k, v in out.items() if k != "sample"}
    _audit(rec)
    out["audit_ts"] = rec["ts"]
    return out


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "scan").lower()
    if cmd == "scan":
        r = scan()
        print(json.dumps({"level": r.get("level"), "score": r.get("score"), "summary": r.get("summary"),
                          "items": r.get("items")}, ensure_ascii=False, indent=1))
    elif cmd == "sample":
        d = sample()
        made = check_alerts()
        print(json.dumps({"sample": d, "alerts": made}, ensure_ascii=False, indent=1))
    elif cmd == "history":
        print(json.dumps({"points": len(history()), "last": history(3), "alerts": alerts(6)},
                         ensure_ascii=False, indent=1))
    elif cmd == "junk":
        print(json.dumps(junk_scan(), ensure_ascii=False, indent=1))
    elif cmd == "services":
        print(json.dumps(services_overview(), ensure_ascii=False, indent=1))
    elif cmd == "temps":
        print(json.dumps(temps(), ensure_ascii=False, indent=1))
    elif cmd == "raw":
        print(json.dumps(collect(), ensure_ascii=False, indent=1)[:2000])
    elif cmd == "act":
        args = {}
        for a in sys.argv[2:]:
            if "=" in a:
                k, v = a.split("=", 1)
                args[k] = v
        print(json.dumps(act(sys.argv[2] if len(sys.argv) > 2 else "", **args), ensure_ascii=False, indent=1))
    else:
        print("用法: python pc_guard.py scan|raw|act <动作> [k=v] ...")


if __name__ == "__main__":
    main()
