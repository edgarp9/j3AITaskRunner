"""Windows window icon helpers for Tk windows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ctypes
import logging
import sys
from typing import Any

LOGGER = logging.getLogger(__name__)

_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x00000010
_WM_SETICON = 0x0080
_ICON_SMALL = 0
_ICON_BIG = 1
_SM_CXICON = 11
_SM_CYICON = 12
_SM_CXSMICON = 49
_SM_CYSMICON = 50
_GA_ROOT = 2


@dataclass(slots=True, frozen=True)
class WindowsIconHandles:
    """Owned Windows icon handles assigned to a Tk window."""

    large_icon: int | None
    small_icon: int | None


def apply_windows_window_icon(hwnd: int, icon_path: Path) -> WindowsIconHandles | None:
    """Set a native Windows icon on an existing Tk window."""
    if sys.platform != "win32":
        return None

    win_dll_factory = getattr(ctypes, "WinDLL", None)
    if win_dll_factory is None:
        LOGGER.debug("ctypes.WinDLL is unavailable; skipping Windows icon setup.")
        return None

    try:
        user32 = win_dll_factory("user32", use_last_error=True)
    except Exception:
        LOGGER.debug("Failed to load user32.dll for window icon setup.", exc_info=True)
        return None

    try:
        _configure_user32_signatures(user32)
        large_icon = _load_icon(user32, icon_path, _SM_CXICON, _SM_CYICON)
        small_icon = _load_icon(user32, icon_path, _SM_CXSMICON, _SM_CYSMICON)
    except Exception:
        LOGGER.debug("Failed to load Windows icon from %s.", icon_path, exc_info=True)
        return None

    target_hwnd = int(user32.GetAncestor(hwnd, _GA_ROOT) or hwnd)
    for handle in {int(hwnd), target_hwnd}:
        if large_icon is not None:
            user32.SendMessageW(handle, _WM_SETICON, _ICON_BIG, large_icon)
        if small_icon is not None:
            user32.SendMessageW(handle, _WM_SETICON, _ICON_SMALL, small_icon)

    return WindowsIconHandles(large_icon=large_icon, small_icon=small_icon)


def destroy_windows_icon_handles(handles: WindowsIconHandles | None) -> None:
    """Release icon handles loaded by apply_windows_window_icon."""
    if handles is None or sys.platform != "win32":
        return

    win_dll_factory = getattr(ctypes, "WinDLL", None)
    if win_dll_factory is None:
        return

    try:
        user32 = win_dll_factory("user32")
        destroy_icon = user32.DestroyIcon
        destroy_icon.argtypes = [ctypes.c_void_p]
        destroy_icon.restype = ctypes.c_bool
    except Exception:
        LOGGER.debug("Failed to prepare DestroyIcon.", exc_info=True)
        return

    for handle in (handles.large_icon, handles.small_icon):
        if handle is None:
            continue
        try:
            destroy_icon(ctypes.c_void_p(handle))
        except Exception:
            LOGGER.debug("Failed to destroy Windows icon handle.", exc_info=True)


def _configure_user32_signatures(user32: Any) -> None:
    try:
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
    except AttributeError:
        pass


def _load_icon(user32: Any, icon_path: Path, width_metric: int, height_metric: int) -> int | None:
    width = int(user32.GetSystemMetrics(width_metric))
    height = int(user32.GetSystemMetrics(height_metric))
    icon = user32.LoadImageW(
        None,
        str(icon_path),
        _IMAGE_ICON,
        width,
        height,
        _LR_LOADFROMFILE,
    )
    if not icon:
        return None
    return int(icon)
