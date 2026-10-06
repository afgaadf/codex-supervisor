#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py —— 管家「Codex 监督插件」安装器（幂等、失败不抛）。

做三件事：
  1. 把本目录的 SKILL.md 复制/链接到 ~/.codex/skills/codex-supervisor/。
  2. 打印需要追加到 ~/.codex/config.toml 的 MCP 注册片段（**不自动改**该文件）。
  3. 幂等：重复运行结果一致；已存在且内容相同则跳过。

用法：
  python install.py             # 真正执行
  python install.py --dry-run   # 只打印将要做什么，不落盘
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
SKILL_SRC = PLUGIN_DIR / "SKILL.md"
MCP_SERVER = PLUGIN_DIR / "mcp_server.py"

HOME = Path.home()
SKILLS_DIR = HOME / ".codex" / "skills"
SKILL_DST_DIR = SKILLS_DIR / "codex-supervisor"
SKILL_DST = SKILL_DST_DIR / "SKILL.md"
CONFIG_TOML = HOME / ".codex" / "config.toml"


def info(msg):
    print("[安装器] " + msg)


def copy_skill(dry_run):
    """把 SKILL.md 放到 skills 目录；失败不抛。"""
    try:
        if not SKILL_SRC.exists():
            info("找不到源文件：%s，跳过。" % SKILL_SRC)
            return False
        if dry_run:
            info("将把 %s 复制到 %s（预览模式，未执行）。" % (SKILL_SRC, SKILL_DST))
            return True
        SKILL_DST_DIR.mkdir(parents=True, exist_ok=True)
        if SKILL_DST.exists() and filecmp.cmp(str(SKILL_SRC), str(SKILL_DST), shallow=False):
            info("skill 已是最新，无需复制：%s" % SKILL_DST)
            return True
        shutil.copyfile(str(SKILL_SRC), str(SKILL_DST))
        info("已写入 skill：%s" % SKILL_DST)
        return True
    except Exception as exc:
        info("复制 skill 失败（已忽略，不影响其它步骤）：%r" % exc)
        return False


def config_snippet():
    """生成要追加到 config.toml 的 MCP 注册片段（绝对路径）。"""
    py = sys.executable or "python"
    # TOML 字符串里反斜杠要转义；统一转成双反斜杠形式最稳妥
    py_toml = py.replace("\\", "\\\\")
    mcp_toml = str(MCP_SERVER).replace("\\", "\\\\")
    return (
        "\n# ---- 管家 Codex 监督插件（手动追加到 config.toml）----\n"
        "[mcp_servers.codex-supervisor]\n"
        'command = "%s"\n'
        'args = ["%s"]\n' % (py_toml, mcp_toml)
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="管家 Codex 监督插件安装器")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要做的事，不实际写文件")
    args = parser.parse_args(argv)

    try:
        info("插件目录：%s" % PLUGIN_DIR)
        ok = copy_skill(args.dry_run)

        info("接下来请手动编辑：%s" % CONFIG_TOML)
        info("把下面这段【追加】到该文件末尾，然后重启 Codex：")
        print(config_snippet())

        if args.dry_run:
            info("预览模式结束：未改动任何文件。")
        else:
            info("完成。skill 安装%s；config.toml 需你手动粘贴上面片段。"
                 % ("成功" if ok else "未完成"))
        return 0
    except Exception as exc:
        # 失败不抛：只报告，返回非零码便于脚本判断，但不打断流程
        info("安装过程中出错（已捕获）：%r" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())