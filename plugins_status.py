# -*- coding: utf-8 -*-
"""plugins_status.py —— 插件状态：把 Codex 插件与 Obsidian 插件的安装/健康状态汇总给管家界面。

两个插件都把自己最近一次运行的结果写成报告：
    ~/.codex/supervisor-plugin-reports/codex.json
    ~/.codex/supervisor-plugin-reports/obsidian.json
本模块只读这些报告 + 检查安装位置，**不主动改用户环境**。
安装/复制是显式动作（install_obsidian_plugin），要人点了才做。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from paths import APP_DIR, CODEX_HOME, VAULT_DIR

# 随包发布的插件源码
PLUGIN_SRC = APP_DIR / "plugins"
CODEX_PLUGIN_SRC = PLUGIN_SRC / "codex"
OBSIDIAN_PLUGIN_SRC = PLUGIN_SRC / "obsidian"

# 报告目录（两个插件约定写这里）
REPORT_DIR = Path.home() / ".codex" / "supervisor-plugin-reports"

# Codex 插件装在哪
CODEX_SKILL_DIR = CODEX_HOME / "skills" / "codex-supervisor"

# Obsidian 插件装在哪（相对知识库）
OBSIDIAN_PLUGIN_ID = "codex-supervisor"
OBSIDIAN_PLUGIN_FILES = ("manifest.json", "main.js", "styles.css")

# 两个插件分别装在不同的软件里 —— 名字里就写清楚，别让人猜。
PLUGINS = {
    "codex": {
        "name": "Codex 侧插件",
        "area": "codex",
        "side": "codex",
        "host": "Codex",
        "installed_into": "Codex（技能目录 ~/.codex/skills + config.toml 里的 MCP 注册）",
        "what": "让 Codex 在声称完成/已验证前先找管家自查",
    },
    "obsidian": {
        "name": "Obsidian 侧插件",
        "area": "obsidian",
        "side": "obsidian",
        "host": "Obsidian",
        "installed_into": "Obsidian（知识库里的 .obsidian/plugins/codex-supervisor/）",
        "what": "把知识库接到管家（桥）+ 在 Obsidian 内做体检并写报告",
    },
}


def _now():
    return datetime.now().astimezone()


def read_report(plugin: str):
    """读某个插件的报告；不存在或坏了都返回 None（不抛）。"""
    try:
        p = REPORT_DIR / ("%s.json" % plugin)
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _report_age_seconds(report):
    ts = (report or {}).get("ts")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((_now() - dt).total_seconds()))
    except Exception:
        return None


def _human_age(seconds):
    if seconds is None:
        return "未知"
    if seconds < 90:
        return "%d 秒前" % seconds
    if seconds < 5400:
        return "%d 分钟前" % (seconds // 60)
    if seconds < 172800:
        return "%d 小时前" % (seconds // 3600)
    return "%d 天前" % (seconds // 86400)



def _disk_plugin_version(src_file):
    """从插件源码里读它自己的版本号（不看运行中的报告）。"""
    try:
        import re as _re
        text = Path(src_file).read_text(encoding="utf-8", errors="replace")
        m = _re.search(r'PLUGIN_VERSION\s*=\s*["\']([^"\']+)', text)
        return m.group(1) if m else ""
    except Exception:
        return ""

def codex_status():
    report = read_report("codex")
    skill_installed = (CODEX_SKILL_DIR / "SKILL.md").exists()
    server = CODEX_PLUGIN_SRC / "mcp_server.py"
    src_ok = server.exists()
    age = _report_age_seconds(report)
    healthy = bool(report.get("healthy")) if isinstance(report, dict) else False
    if report and healthy:
        state, note = "运行中", "最近一次报告健康"
    elif report:
        state, note = "有告警", "最近一次报告不健康"
    elif skill_installed or src_ok:
        state, note = "已就绪", "插件文件在，但还没有运行报告"
    else:
        state, note = "未安装", "找不到插件文件"
    # 运行中的 MCP 进程只在 Codex 启动时加载一次 —— 源码改了他也不会自动重载。
    # 所以要比对「磁盘版本」与「最近一次报告里的版本」，不一致就明说"要重启 Codex"。
    disk_ver = _disk_plugin_version(server)
    run_ver = (report or {}).get("version") or ""
    report_mtime = None
    try:
        rp = REPORT_DIR / "codex.json"
        if rp.exists():
            report_mtime = rp.stat().st_mtime
    except Exception:
        pass
    src_mtime = None
    try:
        if server.exists():
            src_mtime = server.stat().st_mtime
    except Exception:
        pass
    stale = False
    stale_reason = ""
    if run_ver and disk_ver and run_ver != disk_ver:
        stale, stale_reason = True, "运行版本 %s ≠ 磁盘版本 %s" % (run_ver, disk_ver)
    elif report_mtime and src_mtime and src_mtime > report_mtime + 1:
        stale, stale_reason = True, "插件源码比上次运行新"
    if stale:
        state, note = "需重启 Codex", "跑的是旧版（%s）；重启 Codex 才会加载新版" % stale_reason

    return {
        "plugin": "codex",
        "name": PLUGINS["codex"]["name"],
        "running_version": run_ver,
        "disk_version": disk_ver,
        "stale_running": stale,
        "stale_reason": stale_reason,
        "side": PLUGINS["codex"]["side"],
        "host": PLUGINS["codex"]["host"],
        "installed_into": PLUGINS["codex"]["installed_into"],
        "what": PLUGINS["codex"]["what"],
        "side": PLUGINS["codex"]["side"],
        "host": PLUGINS["codex"]["host"],
        "installed_into": PLUGINS["codex"]["installed_into"],
        "what": PLUGINS["codex"]["what"],
        "side": PLUGINS["codex"]["side"],
        "host": PLUGINS["codex"]["host"],
        "installed_into": PLUGINS["codex"]["installed_into"],
        "what": PLUGINS["codex"]["what"],
        "side": PLUGINS["codex"]["side"],
        "host": PLUGINS["codex"]["host"],
        "installed_into": PLUGINS["codex"]["installed_into"],
        "what": PLUGINS["codex"]["what"],
        "state": state,
        "note": note,
        "installed": bool(skill_installed or report),
        "skill_installed": bool(skill_installed),
        "source_present": bool(src_ok),
        "version": (report or {}).get("version") or "",
        "healthy": healthy,
        "last_run": _human_age(age),
        "summary": (report or {}).get("summary") or "",
        "checks": (report or {}).get("checks") or [],
        "metrics": (report or {}).get("metrics") or {},
    }


def obsidian_status(vault_dir=None):
    vault = Path(vault_dir or VAULT_DIR)
    report = read_report("obsidian")
    target = vault / ".obsidian" / "plugins" / OBSIDIAN_PLUGIN_ID
    installed = (target / "manifest.json").exists()
    src_ok = (OBSIDIAN_PLUGIN_SRC / "manifest.json").exists()
    age = _report_age_seconds(report)
    healthy = bool(report.get("healthy")) if isinstance(report, dict) else False
    if report and healthy:
        state, note = "运行中", "最近一次体检健康"
    elif report:
        state, note = "有告警", "最近一次体检不健康"
    elif installed:
        state, note = "已安装", "已装进知识库，但还没跑过"
    elif src_ok:
        state, note = "未安装", "插件源码在，尚未装进知识库"
    else:
        state, note = "未安装", "找不到插件文件"
    return {
        "plugin": "obsidian",
        "name": PLUGINS["obsidian"]["name"],
        "side": PLUGINS["obsidian"]["side"],
        "host": PLUGINS["obsidian"]["host"],
        "installed_into": PLUGINS["obsidian"]["installed_into"],
        "what": PLUGINS["obsidian"]["what"],
        "side": PLUGINS["obsidian"]["side"],
        "host": PLUGINS["obsidian"]["host"],
        "installed_into": PLUGINS["obsidian"]["installed_into"],
        "what": PLUGINS["obsidian"]["what"],
        "side": PLUGINS["obsidian"]["side"],
        "host": PLUGINS["obsidian"]["host"],
        "installed_into": PLUGINS["obsidian"]["installed_into"],
        "what": PLUGINS["obsidian"]["what"],
        "side": PLUGINS["obsidian"]["side"],
        "host": PLUGINS["obsidian"]["host"],
        "installed_into": PLUGINS["obsidian"]["installed_into"],
        "what": PLUGINS["obsidian"]["what"],
        "state": state,
        "note": note,
        "installed": bool(installed),
        "source_present": bool(src_ok),
        "vault": str(vault),
        "install_path": str(target),
        "version": (report or {}).get("version") or "",
        "healthy": healthy,
        "last_run": _human_age(age),
        "summary": (report or {}).get("summary") or "",
        "checks": (report or {}).get("checks") or [],
        "metrics": (report or {}).get("metrics") or {},
    }


def describe(vault_dir=None):
    return {
        "report_dir": str(REPORT_DIR),
        "codex": codex_status(),
        "obsidian": obsidian_status(vault_dir),
    }


def install_obsidian_plugin(vault_dir=None):
    """把 Obsidian 插件复制进知识库（显式调用）。返回 (ok, 说明)。"""
    vault = Path(vault_dir or VAULT_DIR)
    if not vault.is_dir():
        return False, "知识库目录不存在：%s" % vault
    if not (OBSIDIAN_PLUGIN_SRC / "manifest.json").exists():
        return False, "找不到插件源码：%s" % OBSIDIAN_PLUGIN_SRC
    target = vault / ".obsidian" / "plugins" / OBSIDIAN_PLUGIN_ID
    try:
        target.mkdir(parents=True, exist_ok=True)
        copied = []
        for name in OBSIDIAN_PLUGIN_FILES:
            src = OBSIDIAN_PLUGIN_SRC / name
            if src.exists():
                shutil.copy2(src, target / name)
                copied.append(name)
        return True, "已复制到 %s（%s）；请在 Obsidian 设置→第三方插件里启用" % (target, "、".join(copied))
    except Exception as exc:
        return False, "复制失败：%s" % exc


if __name__ == "__main__":
    print(json.dumps(describe(), ensure_ascii=False, indent=2))