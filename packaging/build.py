# 管家 —— 冻结为独立可执行文件（公开发布用）
#
# 前置：pip install pyinstaller
# 用法：在 supervisor 目录下执行
#   python packaging\build.py
#
# 产物：packaging\dist\Supervisor\Supervisor.exe（含 PySide6，约 157 MB）

from __future__ import annotations
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent      # supervisor/
PKG = APP / "packaging"

HIDDEN = ["vault_ops", "vault_classify", "vault_priority", "vault_ops_batch",
          "learn", "brain", "pc_guard", "judge", "codex_trust", "research",
          "supervisor_ui", "paths"]


def main() -> int:
    args = [sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--windowed",
            "--name", "Supervisor",
            "--icon", str(APP / "home.ico"),
            "--add-data", "%s;." % (APP / "home.ico"),
            "--distpath", str(PKG / "dist"),
            "--workpath", str(PKG / "build"),
            "--specpath", str(PKG)]
    for h in HIDDEN:
        args += ["--hidden-import", h]
    args.append(str(APP / "supervisor_gui.py"))
    print(" ".join(args))
    return subprocess.call(args)


if __name__ == "__main__":
    sys.exit(main())