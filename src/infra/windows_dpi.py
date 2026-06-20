"""Windows process and window DPI helpers."""

from __future__ import annotations

from enum import Enum
import ctypes
import logging
import sys
from typing import Any

LOGGER = logging.getLogger(__name__)

BASE_DPI = 96
_DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = -2
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_PROCESS_SYSTEM_DPI_AWARE = 1
_PROCESS_PER_MONITOR_DPI_AWARE = 2
_ERROR_ACCESS_DENIED = 5
_E_ACCESSDENIED = 0x80070005
_E_ACCESSDENIED_SIGNED = -2147024891
_S_OK = 0


class DpiAwarenessResult(str, Enum):
    """Outcome of a Windows DPI awareness setup attempt."""

    NOT_WINDOWS = "not_windows"
    SYSTEM_CONTEXT = "system_context"
    PER_MONITOR_CONTEXT = "per_monitor_context"
    PER_MONITOR_V2 = "per_monitor_v2"
    PER_MONITOR = "per_monitor"
    SYSTEM = "system"
    ALREADY_SET = "already_set"
    FAILED = "failed"


class DpiAwarenessPolicy(str, Enum):
    """Preferred process DPI policy for the Tk application."""

    SYSTEM_AWARE = "system_aware"
    PER_MONITOR_AWARE = "per_monitor_aware"


def configure_windows_dpi_awareness(
    policy: DpiAwarenessPolicy = DpiAwarenessPolicy.SYSTEM_AWARE,
) -> DpiAwarenessResult:
    """Set process DPI awareness before Tk creates the first root window."""
    if sys.platform != "win32":
        return DpiAwarenessResult.NOT_WINDOWS

    win_dll_factory = getattr(ctypes, "WinDLL", None)
    if win_dll_factory is None:
        LOGGER.debug("ctypes.WinDLL is unavailable; skipping Windows DPI awareness setup.")
        return DpiAwarenessResult.FAILED

    for attempt in _build_awareness_attempts(policy):
        result = attempt(win_dll_factory)
        if result is not None:
            return result

    LOGGER.warning("Unable to configure Windows DPI awareness.")
    return DpiAwarenessResult.FAILED


configure_process_dpi_awareness = configure_windows_dpi_awareness


def get_window_dpi(hwnd: int) -> int | None:
    """Return the DPI for one Windows HWND when the API is available."""
    if sys.platform != "win32" or hwnd <= 0:
        return None

    win_dll_factory = getattr(ctypes, "WinDLL", None)
    if win_dll_factory is None:
        return None

    try:
        user32 = win_dll_factory("user32")
    except Exception:
        LOGGER.debug("Failed to load user32.dll for GetDpiForWindow.", exc_info=True)
        return None

    get_dpi_for_window = getattr(user32, "GetDpiForWindow", None)
    if get_dpi_for_window is None:
        return None

    try:
        get_dpi_for_window.argtypes = [ctypes.c_void_p]
        get_dpi_for_window.restype = ctypes.c_uint
    except AttributeError:
        pass

    try:
        dpi = int(get_dpi_for_window(ctypes.c_void_p(hwnd)))
    except Exception:
        LOGGER.debug("GetDpiForWindow failed. hwnd=%s", hwnd, exc_info=True)
        return None
    if dpi <= 0:
        return None
    return dpi


def get_system_dpi() -> int | None:
    """Return the current Windows system DPI when the API is available."""
    if sys.platform != "win32":
        return None

    win_dll_factory = getattr(ctypes, "WinDLL", None)
    if win_dll_factory is None:
        return None

    try:
        user32 = win_dll_factory("user32")
    except Exception:
        LOGGER.debug("Failed to load user32.dll for GetDpiForSystem.", exc_info=True)
        return None

    get_dpi_for_system = getattr(user32, "GetDpiForSystem", None)
    if get_dpi_for_system is None:
        return None

    try:
        get_dpi_for_system.restype = ctypes.c_uint
    except AttributeError:
        pass

    try:
        dpi = int(get_dpi_for_system())
    except Exception:
        LOGGER.debug("GetDpiForSystem failed.", exc_info=True)
        return None
    if dpi <= 0:
        return None
    return dpi


def _build_awareness_attempts(policy: DpiAwarenessPolicy) -> tuple[Any, ...]:
    if policy == DpiAwarenessPolicy.PER_MONITOR_AWARE:
        return (
            lambda win_dll_factory: _try_set_context(
                win_dll_factory,
                _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE,
                DpiAwarenessResult.PER_MONITOR_CONTEXT,
            ),
            lambda win_dll_factory: _try_set_context(
                win_dll_factory,
                _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
                DpiAwarenessResult.PER_MONITOR_V2,
            ),
            lambda win_dll_factory: _try_set_context(
                win_dll_factory,
                _DPI_AWARENESS_CONTEXT_SYSTEM_AWARE,
                DpiAwarenessResult.SYSTEM_CONTEXT,
            ),
            lambda win_dll_factory: _try_set_shcore_awareness(
                win_dll_factory,
                _PROCESS_PER_MONITOR_DPI_AWARE,
                DpiAwarenessResult.PER_MONITOR,
            ),
            lambda win_dll_factory: _try_set_shcore_awareness(
                win_dll_factory,
                _PROCESS_SYSTEM_DPI_AWARE,
                DpiAwarenessResult.SYSTEM,
            ),
            _try_set_legacy_system_aware,
        )
    return (
        lambda win_dll_factory: _try_set_context(
            win_dll_factory,
            _DPI_AWARENESS_CONTEXT_SYSTEM_AWARE,
            DpiAwarenessResult.SYSTEM_CONTEXT,
        ),
        lambda win_dll_factory: _try_set_context(
            win_dll_factory,
            _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE,
            DpiAwarenessResult.PER_MONITOR_CONTEXT,
        ),
        lambda win_dll_factory: _try_set_context(
            win_dll_factory,
            _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
            DpiAwarenessResult.PER_MONITOR_V2,
        ),
        lambda win_dll_factory: _try_set_shcore_awareness(
            win_dll_factory,
            _PROCESS_SYSTEM_DPI_AWARE,
            DpiAwarenessResult.SYSTEM,
        ),
        lambda win_dll_factory: _try_set_shcore_awareness(
            win_dll_factory,
            _PROCESS_PER_MONITOR_DPI_AWARE,
            DpiAwarenessResult.PER_MONITOR,
        ),
        _try_set_legacy_system_aware,
    )


def _try_set_context(
    win_dll_factory: Any,
    context: int,
    success_result: DpiAwarenessResult,
) -> DpiAwarenessResult | None:
    try:
        user32 = win_dll_factory("user32", use_last_error=True)
    except Exception:
        LOGGER.debug("Failed to load user32.dll for DPI awareness context.", exc_info=True)
        return None

    set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if set_context is None:
        return None

    try:
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
    except AttributeError:
        pass

    try:
        if set_context(ctypes.c_void_p(context)):
            return success_result
        last_error = _get_last_error()
    except Exception:
        LOGGER.debug("SetProcessDpiAwarenessContext failed.", exc_info=True)
        return None

    if last_error == _ERROR_ACCESS_DENIED:
        LOGGER.debug("Windows DPI awareness is already configured.")
        return DpiAwarenessResult.ALREADY_SET
    LOGGER.debug("SetProcessDpiAwarenessContext returned false. last_error=%s", last_error)
    return None


def _get_last_error() -> int:
    get_last_error = getattr(ctypes, "get_last_error", None)
    if get_last_error is None:
        return 0
    try:
        return int(get_last_error())
    except Exception:
        LOGGER.debug("ctypes.get_last_error failed.", exc_info=True)
        return 0


def _try_set_shcore_awareness(
    win_dll_factory: Any,
    awareness: int,
    success_result: DpiAwarenessResult,
) -> DpiAwarenessResult | None:
    try:
        shcore = win_dll_factory("shcore")
    except Exception:
        LOGGER.debug("Failed to load shcore.dll for DPI awareness.", exc_info=True)
        return None

    set_awareness = getattr(shcore, "SetProcessDpiAwareness", None)
    if set_awareness is None:
        return None

    try:
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
    except AttributeError:
        pass

    try:
        result = int(set_awareness(awareness))
    except Exception:
        LOGGER.debug("SetProcessDpiAwareness failed.", exc_info=True)
        return None

    if result == _S_OK:
        return success_result
    if _is_access_denied_hresult(result):
        LOGGER.debug("Windows DPI awareness is already configured.")
        return DpiAwarenessResult.ALREADY_SET
    LOGGER.debug("SetProcessDpiAwareness returned HRESULT=%s", result)
    return None


def _try_set_legacy_system_aware(win_dll_factory: Any) -> DpiAwarenessResult | None:
    try:
        user32 = win_dll_factory("user32")
    except Exception:
        LOGGER.debug("Failed to load user32.dll for system DPI awareness.", exc_info=True)
        return None

    set_system_aware = getattr(user32, "SetProcessDPIAware", None)
    if set_system_aware is None:
        return None

    try:
        set_system_aware.restype = ctypes.c_bool
    except AttributeError:
        pass

    try:
        if set_system_aware():
            return DpiAwarenessResult.SYSTEM
    except Exception:
        LOGGER.debug("SetProcessDPIAware failed.", exc_info=True)
        return None

    LOGGER.debug("SetProcessDPIAware returned false.")
    return None


def _is_access_denied_hresult(value: int) -> bool:
    return value in {_E_ACCESSDENIED, _E_ACCESSDENIED_SIGNED}
