# 管家 —— 冻结为独立可执行文件（公开发布用）
#
# 前置：pip install pyinstaller
# 用法：在 supervisor 目录下执行
#   python packaging\build.py
#
# 产物：packaging\dist\Supervisor\
#        Supervisor.exe        窗口/看板
#        SupervisorButler.exe  后台管家

from __future__ import annotations
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent


def main() -> int:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", str(PKG / "dist"),
        "--workpath", str(PKG / "build"),
        str(PKG / "Supervisor.spec"),
    ]
    print(" ".join(args))
    return subprocess.call(args)


if __name__ == "__main__":
    sys.exit(main())