"""UI resource path helpers."""

from __future__ import annotations

from pathlib import Path
import sys

APP_ICON_ICO_RELATIVE_PATH = Path("assets") / "app_icon.ico"
APP_ICON_PNG_RELATIVE_PATH = Path("assets") / "app_icon.png"


def resource_path(relative_path: Path) -> Path:
    """Return a resource path for source runs or PyInstaller bundles."""
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root) / relative_path
    return Path(__file__).resolve().parents[1] / relative_path


def app_icon_ico_path() -> Path:
    """Return the Windows icon resource path."""
    return resource_path(APP_ICON_ICO_RELATIVE_PATH)


def app_icon_png_path() -> Path:
    """Return the Tk-compatible icon resource path."""
    return resource_path(APP_ICON_PNG_RELATIVE_PATH)
