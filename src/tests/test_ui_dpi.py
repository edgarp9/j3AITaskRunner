from __future__ import annotations

import unittest
from unittest import mock

from ui.dpi import (
    DpiMetrics,
    DpiSyncController,
    UiScale,
    configure_tk_dpi,
    sync_tk_dpi,
)


class UiScaleTests(unittest.TestCase):
    def test_scales_pixels_padding_and_geometry(self) -> None:
        scale = UiScale(1.5)

        self.assertEqual(2, scale.px(1))
        self.assertEqual((18, 0, 15, 18), scale.padding(12, 0, 10, 12))
        self.assertEqual("150x75", scale.geometry(100, 50))


class TkDpiConfigurationTests(unittest.TestCase):
    def test_configure_tk_dpi_reads_frame_hwnd_before_widget_id(self) -> None:
        root = _RootStub(frame_hwnd="0x64", widget_id=200)
        requested_hwnds: list[int] = []

        def fake_get_window_dpi(hwnd: int) -> int:
            requested_hwnds.append(hwnd)
            return 144

        with (
            mock.patch("ui.dpi.sys.platform", "linux"),
            mock.patch("ui.dpi.get_window_dpi", side_effect=fake_get_window_dpi),
            mock.patch("ui.dpi.get_system_dpi", return_value=120),
        ):
            metrics = configure_tk_dpi(root)

        self.assertEqual(DpiMetrics(current_dpi=144.0, dpi_scale=1.5), metrics)
        self.assertEqual([100], requested_hwnds)
        self.assertEqual([("tk", "scaling", 2.0)], root.tk.calls)

    def test_sync_tk_dpi_returns_none_when_dpi_is_unchanged(self) -> None:
        root = _RootStub(frame_hwnd=100, widget_id=200)
        setattr(root, "_j3_dpi_metrics", DpiMetrics.from_dpi(144))

        with mock.patch("ui.dpi.sys.platform", "linux"):
            result = sync_tk_dpi(root, current_dpi=144)

        self.assertIsNone(result)
        self.assertEqual([], root.tk.calls)

    def test_sync_tk_dpi_updates_scaling_when_dpi_changes(self) -> None:
        root = _RootStub(frame_hwnd=100, widget_id=200)
        setattr(root, "_j3_dpi_metrics", DpiMetrics.from_dpi(96))

        with mock.patch("ui.dpi.sys.platform", "linux"):
            result = sync_tk_dpi(root, current_dpi=192)

        self.assertEqual(DpiMetrics(current_dpi=192.0, dpi_scale=2.0), result)
        self.assertEqual([("tk", "scaling", 192 / 72.0)], root.tk.calls)


class DpiSyncControllerTests(unittest.TestCase):
    def test_debounces_root_configure_events(self) -> None:
        root = _ControllerRootStub()
        changed_metrics: list[DpiMetrics] = []
        controller = DpiSyncController(root, changed_metrics.append, debounce_ms=150)
        controller.bind()

        root.bound_callback(_Event(root))
        root.bound_callback(_Event(root))

        self.assertEqual(["after-1"], root.cancelled_after_ids)
        with mock.patch("ui.dpi.sync_tk_dpi", return_value=DpiMetrics.from_dpi(120)):
            root.run_after("after-2")

        self.assertEqual([DpiMetrics.from_dpi(120)], changed_metrics)

    def test_ignores_child_configure_and_cancels_on_close(self) -> None:
        root = _ControllerRootStub()
        child = object()
        controller = DpiSyncController(root, lambda _metrics: None, debounce_ms=150)
        controller.bind()

        root.bound_callback(_Event(child))
        self.assertEqual({}, root.after_callbacks)

        root.bound_callback(_Event(root))
        controller.close()

        self.assertEqual(["after-1"], root.cancelled_after_ids)
        self.assertEqual({}, root.after_callbacks)


class _TkCallStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    def call(self, namespace: str, command: str, value: float) -> None:
        self.calls.append((namespace, command, value))


class _RootStub:
    def __init__(self, *, frame_hwnd: object, widget_id: int) -> None:
        self._frame_hwnd = frame_hwnd
        self._widget_id = widget_id
        self.tk = _TkCallStub()

    def frame(self) -> object:
        return self._frame_hwnd

    def winfo_id(self) -> int:
        return self._widget_id

    def winfo_fpixels(self, value: str) -> float:
        del value
        return 96.0


class _ControllerRootStub:
    def __init__(self) -> None:
        self.bound_callback = None
        self.after_callbacks: dict[str, object] = {}
        self.cancelled_after_ids: list[str] = []

    def bind(self, event_name: str, callback: object, *, add: str) -> None:
        self.bound_event_name = event_name
        self.bound_add = add
        self.bound_callback = callback

    def after(self, _interval_ms: int, callback: object) -> str:
        after_id = f"after-{len(self.after_callbacks) + len(self.cancelled_after_ids) + 1}"
        self.after_callbacks[after_id] = callback
        return after_id

    def after_cancel(self, after_id: str) -> None:
        self.cancelled_after_ids.append(after_id)
        self.after_callbacks.pop(after_id, None)

    def run_after(self, after_id: str) -> None:
        callback = self.after_callbacks.pop(after_id)
        callback()

    def winfo_exists(self) -> bool:
        return True


class _Event:
    def __init__(self, widget: object) -> None:
        self.widget = widget


if __name__ == "__main__":
    unittest.main()
