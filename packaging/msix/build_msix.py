# 管家 —— MSIX 打包与签名辅助脚本
#
# 先构建 exe：
#   python packaging\build.py
# 再打包（未签名，适合先做结构验证）：
#   python packaging\msix\build_msix.py
# 再签名（本地测试证书示例）：
#   python packaging\msix\build_msix.py --sign-pfx <pfx路径> --sign-password <密码>

from __future__ import annotations
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PKG = ROOT / "packaging"
DIST = PKG / "dist" / "Supervisor"
STAGE = HERE / "staging"
OUT = HERE / "out"
TEMPLATE = HERE / "AppxManifest.xml.template"
ASSETS = HERE / "Assets"
DEFAULT_PUBLISHER = "CN=Codex Supervisor Dev"


def project_version() -> str:
    text = (ROOT / "version.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)', text)
    if not m:
        raise SystemExit("version.py 里找不到 __version__")
    return m.group(1)


def msix_version(version: str) -> str:
    nums = [int(x) for x in re.findall(r"\d+", str(version))]
    if len(nums) == 3:
        nums.append(0)
    if len(nums) != 4 or any(n < 0 or n > 65535 for n in nums):
        raise SystemExit("MSIX 版本必须是 4 段且每段 0..65535：%s" % version)
    return ".".join(str(x) for x in nums)


def _version_key(text: str):
    return tuple(int(x) for x in re.findall(r"\d+", text))


def find_sdk_tool(name: str) -> Path | None:
    env_key = name.upper() + "_PATH"
    if os.environ.get(env_key):
        p = Path(os.environ[env_key])
        if p.is_file():
            return p
    found = shutil.which(name)
    if found:
        return Path(found)
    roots = []
    for key in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(key)
        if base:
            roots.append(Path(base) / "Windows Kits" / "10" / "bin")
    candidates = []
    for base in roots:
        if base.is_dir():
            candidates.extend(base.glob("*/x64/%s.exe" % name))
    if not candidates:
        return None
    return max(candidates, key=lambda p: _version_key(p.parent.parent.name))


def render_manifest(publisher: str, version: str) -> str:
    data = TEMPLATE.read_text(encoding="utf-8")
    data = data.replace("{{PUBLISHER}}", escape(publisher))
    data = data.replace("{{VERSION}}", msix_version(version))
    if "{{" in data:
        raise SystemExit("清单模板还有未替换的占位符")
    ET.fromstring(data)
    return data


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def stage(publisher: str, version: str) -> Path:
    exe = DIST / "Supervisor.exe"
    butler = DIST / "SupervisorButler.exe"
    if not exe.is_file() or not butler.is_file():
        raise SystemExit("缺少 exe，请先运行：python packaging\\build.py")
    reset_dir(STAGE)
    shutil.copytree(DIST, STAGE / "Supervisor")
    if not ASSETS.is_dir():
        raise SystemExit("缺少 MSIX 图标：%s" % ASSETS)
    shutil.copytree(ASSETS, STAGE / "Assets")
    (STAGE / "AppxManifest.xml").write_text(render_manifest(publisher, version),
                                             encoding="utf-8", newline="\r\n")
    return STAGE


def run(cmd: list[str], secrets=()) -> None:
    shown = list(cmd)
    for i, arg in enumerate(shown):
        if arg and arg in secrets:
            shown[i] = "***"
    print(" ".join(shown))
    subprocess.run(cmd, check=True)


def pack(publisher: str, version: str) -> Path:
    makeappx = find_sdk_tool("makeappx")
    if not makeappx:
        raise SystemExit("找不到 makeappx.exe（Windows SDK）")
    stage(publisher, version)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / ("CodexSupervisor_%s_x64.msix" % msix_version(version))
    run([str(makeappx), "pack", "/o", "/d", str(STAGE), "/p", str(target)])
    return target


def sign(package: Path, pfx: Path, password: str, timestamp_url: str = "") -> None:
    signtool = find_sdk_tool("signtool")
    if not signtool:
        raise SystemExit("找不到 signtool.exe（Windows SDK）")
    cmd = [str(signtool), "sign", "/fd", "SHA256", "/f", str(pfx), "/p", password]
    if timestamp_url:
        cmd += ["/tr", timestamp_url, "/td", "SHA256"]
    cmd.append(str(package))
    run(cmd, secrets=(password,))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def main() -> int:
    ap = argparse.ArgumentParser(description="把 packaging/dist/Supervisor 打成 MSIX")
    ap.add_argument("--publisher", default=os.environ.get("MSIX_PUBLISHER", DEFAULT_PUBLISHER))
    ap.add_argument("--version", default=msix_version(project_version()))
    ap.add_argument("--sign-pfx", type=Path)
    ap.add_argument("--sign-password", default=os.environ.get("MSIX_PFX_PASSWORD", ""))
    ap.add_argument("--timestamp-url", default="")
    ap.add_argument("--install", action="store_true", help="打包/签名后尝试 Add-AppxPackage")
    args = ap.parse_args()

    pkg = pack(args.publisher, args.version)
    if args.sign_pfx:
        if not args.sign_pfx.is_file():
            raise SystemExit("找不到签名证书：%s" % args.sign_pfx)
        sign(pkg, args.sign_pfx, args.sign_password, args.timestamp_url)
    else:
        print("未签名包已生成（仅供结构检查）：%s" % pkg)
    print("SHA256=%s" % sha256(pkg))
    if args.install:
        ps = "Add-AppxPackage -Path '%s'" % str(pkg).replace("'", "''")
        run(["powershell", "-NoProfile", "-Command", ps])
    return 0


if __name__ == "__main__":
    sys.exit(main())