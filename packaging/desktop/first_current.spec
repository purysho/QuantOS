# PyInstaller spec for the First Current desktop app (Windows, macOS, Linux).
# Build: pyinstaller packaging/desktop/first_current.spec --noconfirm
# Env:   FC_VENDOR_DIR  offline Perspective assets (from `quantos terminal vendor`)
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
HERE = ROOT / "packaging" / "desktop"
vendor = os.environ.get("FC_VENDOR_DIR")

datas = []
datas += collect_data_files("quantos", includes=["terminal_static/*", "app_static/*"])
datas += collect_data_files("exchange_calendars")
datas += collect_data_files("tzdata")
datas += collect_data_files("pytz")
datas += collect_data_files("cvxpy")  # reads its own package files at import  # DuckDB imports pytz lazily for TIMESTAMPTZ values
datas += [(str(ROOT / name), ".") for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")]
if vendor:
    datas.append((vendor, "vendor"))

# The app imports most modules lazily; include every quantos module except
# the engine differentials that need NautilusTrader or ORE (not bundled).
hidden = [m for m in collect_submodules("quantos")
          if not m.startswith(("quantos.execution_nautilus", "quantos.ore_"))]
hidden += ["pytz"]  # imported by DuckDB at runtime, invisible to static analysis

a = Analysis(
    [str(HERE / "first_current_app.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hidden,
    excludes=["nautilus_trader", "ORE", "tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = str(HERE / ("icon.ico" if sys.platform == "win32" else "icon.icns" if sys.platform == "darwin" else "icon.png"))
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FirstCurrent",
    console=sys.platform != "darwin",  # Windows/Linux: a small status window; closing it quits
    icon=icon if Path(icon).exists() else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="FirstCurrent", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="First Current.app",
        icon=icon if Path(icon).exists() else None,
        bundle_identifier="io.github.purysho.firstcurrent",
        info_plist={
            "CFBundleShortVersionString": os.environ.get("FC_VERSION", "0.0.0"),
            "CFBundleVersion": os.environ.get("FC_VERSION", "0.0.0"),
            "NSHumanReadableCopyright": "Apache-2.0 · First Current Quant OS contributors",
            "LSMinimumSystemVersion": "12.0",
        },
    )
