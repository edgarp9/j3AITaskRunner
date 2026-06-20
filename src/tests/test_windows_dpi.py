from __future__ import annotations

import unittest
from unittest import mock

from infra.windows_dpi import (
    DpiAwarenessPolicy,
    DpiAwarenessResult,
    configure_windows_dpi_awareness,
)


class WindowsDpiAwarenessTests(unittest.TestCase):
    def test_returns_not_windows_without_calling_windows_api(self) -> None:
        with mock.patch("infra.windows_dpi.sys.platform", "linux"):
            result = configure_windows_dpi_awareness()

        self.assertEqual(DpiAwarenessResult.NOT_WINDOWS, result)

    def test_uses_system_aware_context_by_default(self) -> None:
        fake_user32 = _FakeUser32(set_context_results=(True,))
        fake_factory = _FakeWinDllFactory({"user32": fake_user32})

        with (
            mock.patch("infra.windows_dpi.sys.platform", "win32"),
            mock.patch("infra.windows_dpi.ctypes.WinDLL", fake_factory, create=True),
        ):
            result = configure_windows_dpi_awareness()

        self.assertEqual(DpiAwarenessResult.SYSTEM_CONTEXT, result)
        self.assertEqual([-2], fake_user32.contexts)
        self.assertEqual([("user32", True)], fake_factory.calls)

    def test_falls_back_to_per_monitor_context_for_system_policy(self) -> None:
        fake_user32 = _FakeUser32(set_context_results=(False, True))
        fake_factory = _FakeWinDllFactory({"user32": fake_user32})

        with (
            mock.patch("infra.windows_dpi.sys.platform", "win32"),
            mock.patch("infra.windows_dpi.ctypes.WinDLL", fake_factory, create=True),
            mock.patch("infra.windows_dpi.ctypes.get_last_error", return_value=87, create=True),
        ):
            result = configure_windows_dpi_awareness()

        self.assertEqual(DpiAwarenessResult.PER_MONITOR_CONTEXT, result)
        self.assertEqual([-2, -3], fake_user32.contexts)

    def test_per_monitor_policy_uses_v1_context_before_v2(self) -> None:
        fake_user32 = _FakeUser32(set_context_results=(True,))
        fake_factory = _FakeWinDllFactory({"user32": fake_user32})

        with (
            mock.patch("infra.windows_dpi.sys.platform", "win32"),
            mock.patch("infra.windows_dpi.ctypes.WinDLL", fake_factory, create=True),
        ):
            result = configure_windows_dpi_awareness(DpiAwarenessPolicy.PER_MONITOR_AWARE)

        self.assertEqual(DpiAwarenessResult.PER_MONITOR_CONTEXT, result)
        self.assertEqual([-3], fake_user32.contexts)

    def test_falls_back_to_shcore_system_awareness(self) -> None:
        fake_user32 = _FakeUser32(set_context_results=(False, False, False))
        fake_shcore = _FakeShcore(set_awareness_results=(0,))
        fake_factory = _FakeWinDllFactory({"user32": fake_user32, "shcore": fake_shcore})

        with (
            mock.patch("infra.windows_dpi.sys.platform", "win32"),
            mock.patch("infra.windows_dpi.ctypes.WinDLL", fake_factory, create=True),
            mock.patch("infra.windows_dpi.ctypes.get_last_error", return_value=87, create=True),
        ):
            result = configure_windows_dpi_awareness()

        self.assertEqual(DpiAwarenessResult.SYSTEM, result)
        self.assertEqual([1], fake_shcore.awareness_values)

    def test_reports_already_set_when_context_call_is_denied(self) -> None:
        fake_user32 = _FakeUser32(set_context_results=(False,))
        fake_factory = _FakeWinDllFactory({"user32": fake_user32})

        with (
            mock.patch("infra.windows_dpi.sys.platform", "win32"),
            mock.patch("infra.windows_dpi.ctypes.WinDLL", fake_factory, create=True),
            mock.patch("infra.windows_dpi.ctypes.get_last_error", return_value=5, create=True),
        ):
            result = configure_windows_dpi_awareness()

        self.assertEqual(DpiAwarenessResult.ALREADY_SET, result)


class _FakeWinDllFactory:
    def __init__(self, libraries: dict[str, object]) -> None:
        self._libraries = libraries
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, library_name: str, **kwargs: object) -> object:
        self.calls.append((library_name, bool(kwargs.get("use_last_error"))))
        try:
            return self._libraries[library_name]
        except KeyError as exc:
            raise OSError(f"missing library: {library_name}") from exc


class _FakeUser32:
    def __init__(self, *, set_context_results: tuple[bool, ...]) -> None:
        self.contexts: list[int] = []
        self._set_context_results = list(set_context_results)
        self.SetProcessDpiAwarenessContext = self._set_process_dpi_awareness_context

    def _set_process_dpi_awareness_context(self, context: object) -> bool:
        self.contexts.append(_signed_pointer_value(context))
        if not self._set_context_results:
            return False
        return self._set_context_results.pop(0)


class _FakeShcore:
    def __init__(self, *, set_awareness_results: tuple[int, ...]) -> None:
        self.awareness_values: list[int] = []
        self._set_awareness_results = list(set_awareness_results)
        self.SetProcessDpiAwareness = self._set_process_dpi_awareness

    def _set_process_dpi_awareness(self, awareness: int) -> int:
        self.awareness_values.append(awareness)
        if not self._set_awareness_results:
            return 1
        return self._set_awareness_results.pop(0)


def _signed_pointer_value(value: object) -> int:
    raw_value = int(getattr(value, "value", value))
    if raw_value > 2**63 - 1:
        return raw_value - 2**64
    return raw_value


if __name__ == "__main__":
    unittest.main()
