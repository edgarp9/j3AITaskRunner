"""Tk DPI scaling helpers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import sys
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable

from infra.windows_dpi import BASE_DPI, get_system_dpi, get_window_dpi

LOGGER = logging.getLogger(__name__)

_ROOT_DPI_METRICS_ATTR = "_j3_dpi_metrics"
_WINDOWS_UI_FONT_FAMILY = "Malgun Gothic"
_TK_SCALING_POINTS_PER_INCH = 72.0
_WINDOWS_UI_NAMED_FONTS = (
    "TkDefaultFont",
    "TkTextFont",
    "TkMenuFont",
    "TkHeadingFont",
    "TkCaptionFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)


@dataclass(frozen=True, slots=True)
class DpiMetrics:
    """Current DPI values for one Tk root window."""

    current_dpi: float = float(BASE_DPI)
    dpi_scale: float = 1.0

    @classmethod
    def from_dpi(cls, current_dpi: float) -> DpiMetrics:
        dpi = current_dpi if current_dpi > 0 else float(BASE_DPI)
        return cls(current_dpi=float(dpi), dpi_scale=float(dpi) / BASE_DPI)


@dataclass(frozen=True, slots=True)
class UiScale:
    """Scale pixel-based Tk UI values without touching character widths."""

    factor: float = 1.0

    @classmethod
    def from_metrics(cls, metrics: DpiMetrics) -> UiScale:
        return cls(metrics.dpi_scale)

    def px(self, value: int | float) -> int:
        return scale_pixels(value, self.factor)

    def padding(self, *values: int | float) -> int | tuple[int, ...]:
        scaled = scale_padding(values, self.factor)
        if len(values) == 1:
            return scaled[0]
        return scaled

    def geometry(self, width: int | float, height: int | float) -> str:
        scaled_width, scaled_height = self.size(width, height)
        return f"{scaled_width}x{scaled_height}"

    def size(self, width: int | float, height: int | float) -> tuple[int, int]:
        return self.px(width), self.px(height)


def scale_pixels(value: int | float, dpi_scale: float) -> int:
    """Scale one pixel value, preserving positive non-zero values."""
    scaled = int(round(float(value) * dpi_scale))
    if value > 0:
        return max(1, scaled)
    if value < 0:
        return min(-1, scaled)
    return 0


def scale_padding(
    padding: tuple[int | float, ...],
    dpi_scale: float,
) -> tuple[int, ...]:
    """Scale a Tk padding tuple."""
    return tuple(scale_pixels(value, dpi_scale) for value in padding)


def configure_tk_dpi(root: tk.Misc, logger: logging.Logger | None = None) -> DpiMetrics:
    """Read the current window DPI, apply Tk scaling, and cache metrics on the root."""
    resolved_logger = logger or LOGGER
    metrics = DpiMetrics.from_dpi(_read_current_dpi(root, resolved_logger))
    _apply_tk_scaling(root, metrics, resolved_logger)
    _configure_windows_ui_fonts(root, resolved_logger)
    _set_root_dpi_metrics(root, metrics)
    return metrics


def sync_tk_dpi(
    root: tk.Misc,
    logger: logging.Logger | None = None,
    current_dpi: float | None = None,
) -> DpiMetrics | None:
    """Synchronize Tk scaling only when the root DPI changed."""
    resolved_logger = logger or LOGGER
    old_metrics = get_root_dpi_metrics(root)
    new_dpi = current_dpi if current_dpi is not None else _read_current_dpi(root, resolved_logger)
    new_metrics = DpiMetrics.from_dpi(new_dpi)
    if round(new_metrics.current_dpi, 3) == round(old_metrics.current_dpi, 3):
        return None

    _apply_tk_scaling(root, new_metrics, resolved_logger)
    _configure_windows_ui_fonts(root, resolved_logger)
    _set_root_dpi_metrics(root, new_metrics)
    return new_metrics


def get_root_dpi_metrics(root: tk.Misc) -> DpiMetrics:
    """Return cached DPI metrics for a Tk root, or the 96-DPI default."""
    metrics = getattr(root, _ROOT_DPI_METRICS_ATTR, None)
    if isinstance(metrics, DpiMetrics):
        return metrics
    return DpiMetrics()


def get_widget_ui_scale(widget: tk.Misc) -> UiScale:
    """Return the UI scale associated with a widget's toplevel."""
    try:
        toplevel = widget.winfo_toplevel()
    except tk.TclError:
        toplevel = widget
    return UiScale.from_metrics(get_root_dpi_metrics(toplevel))


class DpiSyncController:
    """Debounce root Configure events and synchronize Tk DPI after movement settles."""

    def __init__(
        self,
        root: tk.Misc,
        on_metrics_changed: Callable[[DpiMetrics], None],
        *,
        logger: logging.Logger | None = None,
        debounce_ms: int = 150,
    ) -> None:
        self._root = root
        self._on_metrics_changed = on_metrics_changed
        self._logger = logger or LOGGER
        self._debounce_ms = debounce_ms
        self._after_id: str | None = None
        self._closed = False

    def bind(self) -> None:
        """Bind the root Configure event using additive Tk binding."""
        self._root.bind("<Configure>", self._on_root_configure, add="+")

    def close(self) -> None:
        """Cancel any pending DPI sync callback."""
        self._closed = True
        self._cancel_pending_sync()

    def _on_root_configure(self, event: tk.Event[tk.Misc]) -> None:
        if self._closed or event.widget is not self._root:
            return

        self._cancel_pending_sync()
        try:
            self._after_id = self._root.after(self._debounce_ms, self._run_deferred_sync)
        except tk.TclError:
            self._logger.debug("Failed to schedule DPI sync callback.", exc_info=True)
            self._after_id = None

    def _run_deferred_sync(self) -> None:
        self._after_id = None
        if self._closed or not _widget_exists(self._root):
            return

        try:
            metrics = sync_tk_dpi(self._root, logger=self._logger)
        except Exception:
            self._logger.exception("Failed to synchronize Tk DPI metrics.")
            return

        if metrics is not None:
            self._on_metrics_changed(metrics)

    def _cancel_pending_sync(self) -> None:
        if self._after_id is None:
            return
        after_id = self._after_id
        self._after_id = None
        try:
            self._root.after_cancel(after_id)
        except tk.TclError:
            self._logger.debug("Failed to cancel pending DPI sync callback.", exc_info=True)


def _set_root_dpi_metrics(root: tk.Misc, metrics: DpiMetrics) -> None:
    setattr(root, _ROOT_DPI_METRICS_ATTR, metrics)


def _read_current_dpi(root: tk.Misc, logger: logging.Logger) -> float:
    hwnd = _get_toplevel_frame_hwnd(root, logger)
    if hwnd is not None:
        window_dpi = get_window_dpi(hwnd)
        if window_dpi is not None:
            return float(window_dpi)

    system_dpi = get_system_dpi()
    if system_dpi is not None:
        return float(system_dpi)

    try:
        return float(root.winfo_fpixels("1i"))
    except tk.TclError:
        logger.debug("Failed to read Tk fpixels for DPI fallback.", exc_info=True)
        return float(BASE_DPI)


def _get_toplevel_frame_hwnd(root: tk.Misc, logger: logging.Logger) -> int | None:
    frame_method = getattr(root, "frame", None)
    if callable(frame_method):
        try:
            hwnd = _coerce_hwnd(frame_method())
        except (tk.TclError, TypeError, ValueError):
            logger.debug("Failed to read Tk toplevel frame HWND.", exc_info=True)
            hwnd = None
        if hwnd is not None:
            return hwnd

    try:
        return _coerce_hwnd(root.winfo_id())
    except (tk.TclError, TypeError, ValueError):
        logger.debug("Failed to read Tk widget HWND.", exc_info=True)
        return None


def _coerce_hwnd(value: object) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        hwnd = int(stripped, 0)
        return hwnd if hwnd > 0 else None
    return None


def _apply_tk_scaling(root: tk.Misc, metrics: DpiMetrics, logger: logging.Logger) -> None:
    try:
        root.tk.call("tk", "scaling", metrics.current_dpi / _TK_SCALING_POINTS_PER_INCH)
    except tk.TclError:
        logger.debug("Failed to apply Tk scaling.", exc_info=True)


def _configure_windows_ui_fonts(root: tk.Misc, logger: logging.Logger) -> None:
    if sys.platform != "win32":
        return

    for font_name in _WINDOWS_UI_NAMED_FONTS:
        try:
            named_font = tkfont.nametofont(font_name, root=root)
            named_font.configure(family=_WINDOWS_UI_FONT_FAMILY)
        except tk.TclError:
            logger.debug("Failed to configure Tk named font. font_name=%s", font_name, exc_info=True)


def _widget_exists(widget: tk.Misc) -> bool:
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False
