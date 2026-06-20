"""System sleep prevention helpers."""

from __future__ import annotations

import ctypes
import logging
import sys

LOGGER = logging.getLogger(__name__)

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


class SystemSleepPreventer:
    """Prevent idle system sleep while long-running queue work is active."""

    def __init__(self, *, keep_display_on: bool = False) -> None:
        self._keep_display_on = keep_display_on
        self._active = False

    @property
    def active(self) -> bool:
        """Return the sleep prevention state tracked by this process."""
        return self._active

    def set_active(self, active: bool) -> None:
        """Enable or disable idle sleep prevention."""
        if self._active == active:
            return

        flags = self._active_flags() if active else _ES_CONTINUOUS
        if not self._set_windows_execution_state(flags):
            return
        self._active = active

    def release(self) -> None:
        """Disable idle sleep prevention."""
        self.set_active(False)

    def _active_flags(self) -> int:
        flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        if self._keep_display_on:
            flags |= _ES_DISPLAY_REQUIRED
        return flags

    def _set_windows_execution_state(self, flags: int) -> bool:
        if sys.platform != "win32":
            return True

        win_dll_factory = getattr(ctypes, "WinDLL", None)
        if win_dll_factory is None:
            LOGGER.warning("ctypes.WinDLL is unavailable; skipping sleep prevention update.")
            return False

        try:
            kernel32 = win_dll_factory("kernel32", use_last_error=True)
        except Exception:
            LOGGER.warning("Failed to load kernel32.dll for sleep prevention.", exc_info=True)
            return False

        set_execution_state = getattr(kernel32, "SetThreadExecutionState", None)
        if set_execution_state is None:
            LOGGER.warning("SetThreadExecutionState is unavailable; skipping sleep prevention update.")
            return False

        try:
            set_execution_state.argtypes = [ctypes.c_uint]
            set_execution_state.restype = ctypes.c_uint
        except AttributeError:
            pass

        try:
            previous_state = int(set_execution_state(flags))
        except Exception:
            LOGGER.warning("SetThreadExecutionState failed.", exc_info=True)
            return False

        if previous_state == 0:
            get_last_error = getattr(ctypes, "get_last_error", lambda: None)
            LOGGER.warning(
                "SetThreadExecutionState returned 0. last_error=%s",
                get_last_error(),
            )
            return False

        return True
