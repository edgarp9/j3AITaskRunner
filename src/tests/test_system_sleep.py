from __future__ import annotations

import unittest
from unittest import mock

from infra.system_sleep import SystemSleepPreventer


class SystemSleepPreventerTests(unittest.TestCase):
    def test_non_windows_tracks_active_without_calling_windows_api(self) -> None:
        preventer = SystemSleepPreventer()

        with (
            mock.patch("infra.system_sleep.sys.platform", "linux"),
            mock.patch("infra.system_sleep.ctypes.WinDLL", side_effect=AssertionError, create=True),
        ):
            preventer.set_active(True)
            self.assertTrue(preventer.active)

            preventer.release()
            self.assertFalse(preventer.active)

    def test_windows_uses_system_required_without_display_by_default(self) -> None:
        fake_kernel32 = _FakeKernel32()
        fake_factory = _FakeWinDllFactory(fake_kernel32)
        preventer = SystemSleepPreventer()

        with (
            mock.patch("infra.system_sleep.sys.platform", "win32"),
            mock.patch("infra.system_sleep.ctypes.WinDLL", fake_factory, create=True),
        ):
            preventer.set_active(True)
            preventer.release()

        self.assertEqual([0x80000001, 0x80000000], fake_kernel32.execution_state.calls)
        self.assertEqual([("kernel32", True), ("kernel32", True)], fake_factory.calls)

    def test_windows_can_keep_display_on_when_requested(self) -> None:
        fake_kernel32 = _FakeKernel32()
        preventer = SystemSleepPreventer(keep_display_on=True)

        with (
            mock.patch("infra.system_sleep.sys.platform", "win32"),
            mock.patch(
                "infra.system_sleep.ctypes.WinDLL",
                _FakeWinDllFactory(fake_kernel32),
                create=True,
            ),
        ):
            preventer.set_active(True)

        self.assertEqual([0x80000003], fake_kernel32.execution_state.calls)

    def test_windows_does_not_track_active_when_win_dll_is_unavailable(self) -> None:
        preventer = SystemSleepPreventer()

        with (
            mock.patch("infra.system_sleep.sys.platform", "win32"),
            mock.patch("infra.system_sleep.ctypes.WinDLL", None, create=True),
        ):
            preventer.set_active(True)

        self.assertFalse(preventer.active)

    def test_windows_does_not_track_active_when_kernel32_load_fails(self) -> None:
        preventer = SystemSleepPreventer()

        with (
            mock.patch("infra.system_sleep.sys.platform", "win32"),
            mock.patch(
                "infra.system_sleep.ctypes.WinDLL",
                _FailingWinDllFactory(),
                create=True,
            ),
        ):
            preventer.set_active(True)

        self.assertFalse(preventer.active)

    def test_windows_does_not_track_active_when_function_is_missing(self) -> None:
        preventer = SystemSleepPreventer()

        with (
            mock.patch("infra.system_sleep.sys.platform", "win32"),
            mock.patch(
                "infra.system_sleep.ctypes.WinDLL",
                _FakeWinDllFactory(_Kernel32WithoutSetExecutionState()),
                create=True,
            ),
        ):
            preventer.set_active(True)

        self.assertFalse(preventer.active)

    def test_windows_does_not_track_active_when_call_raises(self) -> None:
        fake_kernel32 = _FakeKernel32(exc=OSError("set state failed"))
        preventer = SystemSleepPreventer()

        with (
            mock.patch("infra.system_sleep.sys.platform", "win32"),
            mock.patch(
                "infra.system_sleep.ctypes.WinDLL",
                _FakeWinDllFactory(fake_kernel32),
                create=True,
            ),
        ):
            preventer.set_active(True)

        self.assertFalse(preventer.active)
        self.assertEqual([0x80000001], fake_kernel32.execution_state.calls)

    def test_windows_does_not_track_active_when_call_returns_zero(self) -> None:
        fake_kernel32 = _FakeKernel32(return_values=[0])
        preventer = SystemSleepPreventer()

        with (
            mock.patch("infra.system_sleep.sys.platform", "win32"),
            mock.patch(
                "infra.system_sleep.ctypes.WinDLL",
                _FakeWinDllFactory(fake_kernel32),
                create=True,
            ),
        ):
            preventer.set_active(True)

        self.assertFalse(preventer.active)
        self.assertEqual([0x80000001], fake_kernel32.execution_state.calls)

    def test_windows_keeps_active_when_release_fails(self) -> None:
        fake_kernel32 = _FakeKernel32(return_values=[1, 0])
        preventer = SystemSleepPreventer()

        with (
            mock.patch("infra.system_sleep.sys.platform", "win32"),
            mock.patch(
                "infra.system_sleep.ctypes.WinDLL",
                _FakeWinDllFactory(fake_kernel32),
                create=True,
            ),
        ):
            preventer.set_active(True)
            preventer.release()

        self.assertTrue(preventer.active)
        self.assertEqual([0x80000001, 0x80000000], fake_kernel32.execution_state.calls)


class _FakeWinDllFactory:
    def __init__(self, kernel32: object) -> None:
        self._kernel32 = kernel32
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, library_name: str, **kwargs: object) -> object:
        self.calls.append((library_name, bool(kwargs.get("use_last_error"))))
        if library_name != "kernel32":
            raise OSError(f"missing library: {library_name}")
        return self._kernel32


class _FailingWinDllFactory:
    def __call__(self, library_name: str, **kwargs: object) -> object:
        raise OSError(f"missing library: {library_name}")


class _FakeKernel32:
    def __init__(
        self,
        *,
        return_values: list[int] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.execution_state = _FakeSetExecutionState(return_values=return_values, exc=exc)
        self.SetThreadExecutionState = self.execution_state


class _Kernel32WithoutSetExecutionState:
    pass


class _FakeSetExecutionState:
    def __init__(
        self,
        *,
        return_values: list[int] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.calls: list[int] = []
        self._return_values = return_values or [1]
        self._exc = exc

    def __call__(self, flags: int) -> int:
        self.calls.append(flags)
        if self._exc is not None:
            raise self._exc
        if len(self._return_values) == 1:
            return self._return_values[0]
        return self._return_values.pop(0)


if __name__ == "__main__":
    unittest.main()
