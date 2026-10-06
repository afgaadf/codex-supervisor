# -*- mode: python ; coding: utf-8 -*-
# 同时构建 GUI 与后台管家，共用一个 _internal 目录，避免把 PySide6 复制两份。
from pathlib import Path

HERE = Path(SPECPATH).resolve()
APP = HERE.parent
HIDDEN = ["vault_ops", "vault_classify", "vault_priority", "vault_ops_batch",
          "learn", "brain", "pc_guard", "judge", "codex_trust", "research",
          "supervisor_ui", "paths"]
GUI_DATAS = [
    (str(APP / "home.ico"), "."),
    (str(APP / "config.example.json"), "."),
    (str(APP / "rules_hard.json"), "."),
    (str(APP / "rubrics"), "rubrics"),
]

a_gui = Analysis(
    [str(APP / "supervisor_gui.py")],
    pathex=[str(APP)],
    binaries=[],
    datas=GUI_DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
a_butler = Analysis(
    [str(APP / "pc_butler.py")],
    pathex=[str(APP)],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_gui = PYZ(a_gui.pure)
pyz_butler = PYZ(a_butler.pure)
exe_gui = EXE(
    pyz_gui, a_gui.scripts, [], exclude_binaries=True,
    name="Supervisor", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, icon=[str(APP / "home.ico")],
)
exe_butler = EXE(
    pyz_butler, a_butler.scripts, [], exclude_binaries=True,
    name="SupervisorButler", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, icon=[str(APP / "home.ico")],
)
coll = COLLECT(
    exe_gui, exe_butler, a_gui.binaries, a_gui.datas,
    a_butler.binaries, a_butler.datas, strip=False, upx=True,
    upx_exclude=[], name="Supervisor",
)