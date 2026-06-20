"""Shared Tkinter dark theme helpers."""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from .dpi import UiScale


@dataclass(frozen=True, slots=True)
class DarkTheme:
    """Color tokens used by the Tkinter UI."""

    background: str = "#12161c"
    panel: str = "#191f27"
    elevated: str = "#202833"
    field: str = "#0f141a"
    text: str = "#e7edf4"
    muted_text: str = "#aeb8c5"
    disabled_text: str = "#657181"
    border: str = "#354150"
    accent: str = "#61a8ff"
    selected: str = "#245b89"
    button: str = "#27313d"
    button_active: str = "#334052"
    button_pressed: str = "#1e2630"
    success_fill: str = "#1f6f43"
    success_border: str = "#3ac47d"
    warning: str = "#ffb36b"


DARK_THEME = DarkTheme()
DEFAULT_UI_SCALE = UiScale()


def apply_dark_theme(
    widget: tk.Misc,
    *,
    theme: DarkTheme = DARK_THEME,
    scale: UiScale = DEFAULT_UI_SCALE,
) -> None:
    """Apply the app-wide dark theme to ttk styles and the given toplevel."""
    style = ttk.Style(widget)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    _configure_toplevel_options(widget, theme)
    _configure_ttk_styles(style, theme, scale)

    try:
        widget.configure(background=theme.background)
    except tk.TclError:
        pass


def configure_listbox(
    widget: tk.Listbox,
    *,
    theme: DarkTheme = DARK_THEME,
    scale: UiScale = DEFAULT_UI_SCALE,
) -> None:
    """Apply dark colors to a classic Tk listbox."""
    widget.configure(
        background=theme.field,
        foreground=theme.text,
        selectbackground=theme.selected,
        selectforeground=theme.text,
        highlightbackground=theme.border,
        highlightcolor=theme.accent,
        highlightthickness=scale.px(1),
        relief="flat",
        borderwidth=scale.px(1),
        activestyle="none",
    )


def configure_text_widget(
    widget: tk.Text,
    *,
    theme: DarkTheme = DARK_THEME,
    scale: UiScale = DEFAULT_UI_SCALE,
) -> None:
    """Apply dark colors to a classic Tk text widget."""
    widget.configure(
        background=theme.field,
        foreground=theme.text,
        insertbackground=theme.text,
        selectbackground=theme.selected,
        selectforeground=theme.text,
        highlightbackground=theme.border,
        highlightcolor=theme.accent,
        highlightthickness=scale.px(1),
        relief="flat",
        borderwidth=scale.px(1),
    )


def _configure_toplevel_options(widget: tk.Misc, theme: DarkTheme) -> None:
    widget.option_add("*Background", theme.background)
    widget.option_add("*Foreground", theme.text)
    widget.option_add("*selectBackground", theme.selected)
    widget.option_add("*selectForeground", theme.text)
    widget.option_add("*insertBackground", theme.text)
    widget.option_add("*TCombobox*Listbox.background", theme.field)
    widget.option_add("*TCombobox*Listbox.foreground", theme.text)
    widget.option_add("*TCombobox*Listbox.selectBackground", theme.selected)
    widget.option_add("*TCombobox*Listbox.selectForeground", theme.text)


def _configure_ttk_styles(style: ttk.Style, theme: DarkTheme, scale: UiScale) -> None:
    style.configure(
        ".",
        background=theme.background,
        foreground=theme.text,
        fieldbackground=theme.field,
        troughcolor=theme.field,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
        focuscolor=theme.accent,
        insertcolor=theme.text,
        font="TkDefaultFont",
    )
    style.map(
        ".",
        background=[("disabled", theme.background)],
        foreground=[("disabled", theme.disabled_text)],
    )

    style.configure("TFrame", background=theme.background)
    style.configure("TPanedwindow", background=theme.background)
    style.configure("Sash", background=theme.border, sashwidth=scale.px(6))

    style.configure("TLabel", background=theme.background, foreground=theme.text)
    style.configure(
        "Link.TLabel",
        background=theme.background,
        foreground=theme.accent,
    )
    style.configure(
        "Status.TLabel",
        background=theme.elevated,
        foreground=theme.muted_text,
        bordercolor=theme.border,
        relief="solid",
    )

    style.configure(
        "TLabelFrame",
        background=theme.background,
        foreground=theme.text,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
    )
    style.configure(
        "TLabelFrame.Label",
        background=theme.background,
        foreground=theme.muted_text,
    )

    style.configure(
        "TButton",
        background=theme.button,
        foreground=theme.text,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
        focusthickness=scale.px(1),
        focuscolor=theme.accent,
        padding=scale.padding(8, 4),
    )
    style.map(
        "TButton",
        background=[
            ("disabled", theme.elevated),
            ("pressed", theme.button_pressed),
            ("active", theme.button_active),
        ],
        foreground=[("disabled", theme.disabled_text)],
    )

    style.configure(
        "QueueToggle.Toolbutton",
        background=theme.button,
        foreground=theme.text,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
        focusthickness=scale.px(1),
        focuscolor=theme.accent,
        padding=scale.padding(8, 4),
    )
    style.map(
        "QueueToggle.Toolbutton",
        background=[
            ("disabled", theme.elevated),
            ("selected", theme.success_fill),
            ("pressed", theme.button_pressed),
            ("active", theme.button_active),
        ],
        foreground=[("disabled", theme.disabled_text)],
        bordercolor=[("selected", theme.success_border)],
    )

    style.configure(
        "ScheduledRun.Toolbutton",
        background=theme.button,
        foreground=theme.text,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
        focusthickness=scale.px(1),
        focuscolor=theme.accent,
        padding=scale.padding(8, 4),
    )
    style.map(
        "ScheduledRun.Toolbutton",
        background=[
            ("disabled", theme.elevated),
            ("selected", theme.success_fill),
            ("pressed", theme.button_pressed),
            ("active", theme.button_active),
        ],
        foreground=[("disabled", theme.disabled_text)],
        bordercolor=[("selected", theme.success_border)],
    )

    style.configure(
        "TEntry",
        fieldbackground=theme.field,
        foreground=theme.text,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
        insertcolor=theme.text,
        font="TkDefaultFont",
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", theme.background), ("readonly", theme.field)],
        foreground=[("disabled", theme.disabled_text), ("readonly", theme.text)],
    )

    style.configure(
        "TCombobox",
        background=theme.button,
        fieldbackground=theme.field,
        foreground=theme.text,
        arrowcolor=theme.text,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
        insertcolor=theme.text,
        font="TkDefaultFont",
    )
    style.map(
        "TCombobox",
        background=[("active", theme.button_active), ("disabled", theme.elevated)],
        fieldbackground=[("readonly", theme.field), ("disabled", theme.background)],
        foreground=[("readonly", theme.text), ("disabled", theme.disabled_text)],
        selectbackground=[("readonly", theme.selected)],
        selectforeground=[("readonly", theme.text)],
    )

    style.configure(
        "TCheckbutton",
        background=theme.background,
        foreground=theme.text,
        indicatorbackground=theme.field,
        indicatorforeground=theme.text,
        bordercolor=theme.border,
    )
    style.map(
        "TCheckbutton",
        background=[("active", theme.background), ("disabled", theme.background)],
        foreground=[("disabled", theme.disabled_text)],
    )

    style.configure("TNotebook", background=theme.background, bordercolor=theme.border)
    style.configure(
        "TNotebook.Tab",
        background=theme.elevated,
        foreground=theme.muted_text,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
        padding=scale.padding(10, 5),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", theme.panel), ("active", theme.button_active)],
        foreground=[("selected", theme.text), ("active", theme.text)],
    )

    style.configure(
        "Treeview",
        background=theme.field,
        fieldbackground=theme.field,
        foreground=theme.text,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
        rowheight=scale.px(24),
        font="TkDefaultFont",
    )
    style.map(
        "Treeview",
        background=[("selected", theme.selected)],
        foreground=[("selected", theme.text)],
    )
    style.configure(
        "Treeview.Heading",
        background=theme.elevated,
        foreground=theme.text,
        bordercolor=theme.border,
        lightcolor=theme.border,
        darkcolor=theme.border,
        relief="flat",
        font="TkHeadingFont",
    )
    style.map("Treeview.Heading", background=[("active", theme.button_active)])

    for scrollbar_style in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(
            scrollbar_style,
            background=theme.button,
            troughcolor=theme.field,
            arrowcolor=theme.text,
            bordercolor=theme.border,
            lightcolor=theme.border,
            darkcolor=theme.border,
        )
        style.map(
            scrollbar_style,
            background=[("pressed", theme.button_pressed), ("active", theme.button_active)],
            arrowcolor=[("disabled", theme.disabled_text)],
        )
