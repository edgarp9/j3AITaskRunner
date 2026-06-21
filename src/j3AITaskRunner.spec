# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

from app.version import APP_NAME
from build_release import (
    LICENSES_DESTINATION,
    STATIC_LICENSE_FILES,
    _format_version_info_file,
    prepare_release_license_files,
)

ROOT = Path(SPECPATH)
ICON_FILE = ROOT / "assets" / "app_icon.ico"
THIRD_PARTY_NOTICES_FILE = ROOT / "THIRD_PARTY_NOTICES.txt"
PROJECT_LICENSE_FILE = ROOT / "LICENSE"
ABOUT_FILE = ROOT / "about.txt"
VERSION_INFO_FILE = ROOT / "build" / "version_info.txt"
VERSION_INFO_FILE.parent.mkdir(parents=True, exist_ok=True)
VERSION_INFO_FILE.write_text(_format_version_info_file(), encoding="utf-8")
PROMPT_DATAS = [
    (str(path), path.parent.relative_to(ROOT).as_posix())
    for path in sorted((ROOT / "prompt").rglob("*.md"))
    if path.is_file()
]

LICENSE_DATAS = [
    (str(path), LICENSES_DESTINATION)
    for path in (*STATIC_LICENSE_FILES, *prepare_release_license_files(ROOT / "build"))
]


try:
    TKINTERDND2_DATAS, TKINTERDND2_BINARIES, TKINTERDND2_HIDDENIMPORTS = collect_all(
        "tkinterdnd2"
    )
except Exception:
    TKINTERDND2_DATAS = []
    TKINTERDND2_BINARIES = []
    TKINTERDND2_HIDDENIMPORTS = []

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=TKINTERDND2_BINARIES,
    datas=[
        (str(ROOT / "assets" / "app_icon.ico"), "assets"),
        (str(ROOT / "assets" / "app_icon.png"), "assets"),
        (str(PROJECT_LICENSE_FILE), "."),
        (str(THIRD_PARTY_NOTICES_FILE), "."),
        (str(ABOUT_FILE), "."),
        *LICENSE_DATAS,
        *PROMPT_DATAS,
        *TKINTERDND2_DATAS,
    ],
    hiddenimports=TKINTERDND2_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_FILE),
    version=str(VERSION_INFO_FILE),
)
