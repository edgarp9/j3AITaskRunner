"""Window placement helpers for Tkinter UI."""

from __future__ import annotations

import logging
import tkinter as tk

LOGGER = logging.getLogger(__name__)


def present_centered_modal(parent: tk.Misc, window: tk.Toplevel) -> None:
    """Show a toplevel as a centered modal child of its parent."""
    center_toplevel(parent, window)
    window.deiconify()
    window.grab_set()


def center_toplevel(parent: tk.Misc, window: tk.Toplevel) -> None:
    """Place one toplevel window at the center of its parent."""
    parent_window = parent.winfo_toplevel()
    parent_window.update_idletasks()
    window.update_idletasks()

    parent_width = _actual_or_requested_width(parent_window)
    parent_height = _actual_or_requested_height(parent_window)
    width = _actual_or_requested_width(window)
    height = _actual_or_requested_height(window)

    x = parent_window.winfo_rootx() + (parent_width - width) // 2
    y = parent_window.winfo_rooty() + (parent_height - height) // 2
    geometry = _absolute_position_geometry(width, height, x, y)
    window.geometry(geometry)
    LOGGER.debug(
        "Centered toplevel over parent. geometry=%s parent=(%s,%s %sx%s)",
        geometry,
        parent_window.winfo_rootx(),
        parent_window.winfo_rooty(),
        parent_width,
        parent_height,
    )


def _actual_or_requested_width(window: tk.Misc) -> int:
    width = window.winfo_width()
    if width <= 1:
        width = window.winfo_reqwidth()
    return max(width, 1)


def _actual_or_requested_height(window: tk.Misc) -> int:
    height = window.winfo_height()
    if height <= 1:
        height = window.winfo_reqheight()
    return max(height, 1)


def _absolute_position_geometry(width: int, height: int, x: int, y: int) -> str:
    """Return a Tk geometry string preserving virtual-screen coordinates."""
    # Tk treats "300x200-10+20" as an offset from the screen edge. Prefixing
    # with "+" keeps negative coordinates absolute on virtual multi-monitor
    # desktops, e.g. "300x200+-10+20".
    return f"{max(width, 1)}x{max(height, 1)}+{x}+{y}"
