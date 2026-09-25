"""Builds the QuantOS desktop download for the current platform.

    uv sync --locked --no-dev --group desktop --no-editable
    uv run --no-sync python packaging/desktop/build.py

Steps:
1. vendor the pinned Perspective assets (npm integrity verified);
2. PyInstaller build;
3. run the built app's ``--smoke-test`` from a clean home;
4. package it:
   - Windows: ``QuantOS-Windows-x64.zip`` (portable folder);
   - macOS: ``QuantOS-macOS-arm64.dmg`` (``QuantOS.app``);
   - Linux: ``QuantOS-Linux-x86_64.AppImage``;
5. write a ``.sha256`` file next to the artifact.

Output goes to ``build/desktop/out``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "packaging" / "desktop"
WORK = ROOT / "build" / "desktop"
APPIMAGETOOL_URL = "https://github.com/AppImage/appimagetool/releases/download/1.9.0/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256 = "46fdd785094c7f6e545b61afcfb0f3d98d8eab243f644b4b17698c01d06083d1"


def run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-smoke-test", action="store_true")
    args = parser.parse_args()

    from quantos import __version__ as version

    system = platform.system()
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "out").mkdir(parents=True)

    # 1. offline terminal assets
    vendor = WORK / "vendor"
    env = {**os.environ, "QUANTOS_HOME": str(WORK / "vendor-home"), "QUANTOS_VENDOR_DIR": str(vendor)}
    run([sys.executable, "-m", "quantos.cli", "terminal", "vendor"], env=env)

    # 2. PyInstaller
    if system == "Darwin":
        make_icns(HERE / "icon.png", HERE / "icon.icns")
    env = {**os.environ, "FC_VENDOR_DIR": str(vendor), "FC_VERSION": version}
    run([sys.executable, "-m", "PyInstaller", HERE / "quantos.spec", "--noconfirm", "--log-level", "WARN",
         "--distpath", WORK / "dist", "--workpath", WORK / "pyi"], env=env, cwd=WORK)

    if system == "Windows":
        app_dir = WORK / "dist" / "QuantOS"
        executable = app_dir / "QuantOS.exe"
    elif system == "Darwin":
        app_dir = WORK / "dist" / "QuantOS.app"
        executable = app_dir / "Contents" / "MacOS" / "QuantOS"
    else:
        app_dir = WORK / "dist" / "QuantOS"
        executable = app_dir / "QuantOS"

    # 3. the built app must pass its own self-test from a clean profile
    if not args.skip_smoke_test:
        with tempfile.TemporaryDirectory() as home:
            smoke_env = {**os.environ, "HOME": home, "LOCALAPPDATA": home, "XDG_DATA_HOME": home, "USERPROFILE": home}
            for name in ("QUANTOS_HOME", "QUANTOS_VENDOR_DIR"):
                smoke_env.pop(name, None)
            run([executable, "--version"], env=smoke_env)
            run([executable, "--smoke-test"], env=smoke_env)

    # 4. package
    if system == "Windows":
        artifact = WORK / "out" / "QuantOS-Windows-x64.zip"
        package_zip(app_dir, artifact, version)
    elif system == "Darwin":
        artifact = WORK / "out" / "QuantOS-macOS-arm64.dmg"
        package_dmg(app_dir, artifact)
    else:
        artifact = WORK / "out" / "QuantOS-Linux-x86_64.AppImage"
        package_appimage(app_dir, artifact, version)

    # 5. checksum
    (artifact.parent / f"{artifact.name}.sha256").write_text(f"{sha256(artifact)}  {artifact.name}\n")
    print(f"Built {artifact} ({artifact.stat().st_size / 1e6:.0f} MB)")
    return 0


def package_zip(app_dir: Path, artifact: Path, version: str) -> None:
    readme = (
        f"QuantOS {version} for Windows\n\n"
        "1. Extract this whole folder (right-click the zip, Extract All).\n"
        "2. Open the QuantOS folder and double-click QuantOS.exe.\n"
        "3. Your browser opens the control center. Keep the small black window open while you work;\n"
        "   close it (or press Quit) to stop.\n\n"
        "Windows SmartScreen may warn the first time because the app is not code-signed yet:\n"
        "choose More info, then Run anyway.\n\n"
        "Portable use (e.g. a USB stick): create a folder named QuantOS-data next to\n"
        "QuantOS.exe and your data will be kept there.\n\n"
        "Free, open-source software (Apache-2.0). It never trades; nothing it produces is investment advice.\n"
    )
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("QuantOS/README.txt", readme.replace("\n", "\r\n"))
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path("QuantOS") / path.relative_to(app_dir))


def make_icns(png: Path, icns: Path) -> None:
    iconset = WORK / "icon.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        run(["sips", "-z", size, size, png, "--out", iconset / f"icon_{size}x{size}.png"])
        run(["sips", "-z", size * 2, size * 2, png, "--out", iconset / f"icon_{size}x{size}@2x.png"])
    run(["iconutil", "-c", "icns", iconset, "-o", icns])


def package_dmg(app_dir: Path, artifact: Path) -> None:
    staging = WORK / "dmg"
    staging.mkdir()
    shutil.copytree(app_dir, staging / app_dir.name, symlinks=True)
    os.symlink("/Applications", staging / "Applications")
    run(["hdiutil", "create", "-volname", "QuantOS", "-srcfolder", staging, "-ov", "-format", "UDZO", artifact])


def package_appimage(app_dir: Path, artifact: Path, version: str) -> None:
    appdir = WORK / "QuantOS.AppDir"
    shutil.copytree(app_dir, appdir / "usr" / "lib" / "QuantOS", symlinks=True)
    apprun = appdir / "AppRun"
    apprun.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
                      'exec "$HERE/usr/lib/QuantOS/QuantOS" "$@"\n')
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (appdir / "quantos.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=QuantOS\n"
        "Comment=Free, open-source point-in-time investment research\n"
        "Exec=QuantOS\nIcon=quantos\nTerminal=true\nCategories=Office;Finance;\n"
        f"X-AppImage-Version={version}\n"
    )
    shutil.copy(HERE / "icon.png", appdir / "quantos.png")
    tool = WORK / "appimagetool"
    urllib.request.urlretrieve(APPIMAGETOOL_URL, tool)
    if sha256(tool) != APPIMAGETOOL_SHA256:
        raise SystemExit("appimagetool checksum mismatch: refusing to use it")
    tool.chmod(0o755)
    run([tool, "--no-appstream", appdir, artifact],
        env={**os.environ, "ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"})


if __name__ == "__main__":
    raise SystemExit(main())
