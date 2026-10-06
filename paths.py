# -*- coding: utf-8 -*-
"""paths.py —— 全局路径解析（公开分发版：**不写死任何用户路径**）。

优先级：**环境变量 > config.json > 默认值**。
- 环境变量：SUPERVISOR_CODEX_HOME / SUPERVISOR_VAULT_DIR
- 配置文件：与本文件同目录的 `config.json`（见 `config.example.json`）

用户装了以后，改 config.json 或设环境变量即可，**不用改代码**。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 打包（PyInstaller）后，__file__ 指向包内部；用户数据要放 exe 旁边。
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
EXAMPLE_FILE = APP_DIR / "config.example.json"        # 随包发布的只读示例

# 可写数据目录：
#   · 未打包（开发/绿色版）→ 就放程序旁边（保持老行为）
#   · 已打包（MSIX/exe，安装目录只读）→ 放 %LOCALAPPDATA%\CodexSupervisor
if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or (HOME / "AppData" / "Local")) / "CodexSupervisor"
else:
    DATA_DIR = APP_DIR

CONFIG_FILE = DATA_DIR / "config.json"


def _load_config() -> dict:
    try:
        d = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


CFG = _load_config()
HOME = Path.home()


def _resolve(key: str, env: str, default: Path) -> Path:
    raw = os.environ.get(env) or CFG.get(key)
    if raw:
        try:
            return Path(str(raw)).expanduser()
        except Exception:
            pass
    return default


# Codex 的家目录（默认 ~/.codex；每个用户自己的）
CODEX_HOME = _resolve("codex_home", "SUPERVISOR_CODEX_HOME", HOME / ".codex")

# Obsidian 库（默认 ~/Documents/Obsidian Vault；用户可在 config.json 里改）
VAULT_DIR = _resolve("vault_dir", "SUPERVISOR_VAULT_DIR", HOME / "Documents" / "Obsidian Vault")


def describe() -> dict:
    """给"关于/诊断"用：现在解析到的路径 + 来源。"""
    return {
        "app_dir": str(APP_DIR),
        "config_file": str(CONFIG_FILE),
        "config_exists": CONFIG_FILE.exists(),
        "codex_home": str(CODEX_HOME),
        "vault_dir": str(VAULT_DIR),
        "vault_exists": VAULT_DIR.exists(),
    }