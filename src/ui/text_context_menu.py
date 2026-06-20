"""Context menu helpers for editable Tk text widgets."""

from __future__ import annotations

from collections.abc import Callable
import tkinter as tk

from .i18n import text as ui_text

LanguageResolver = str | None | Callable[[], str | None]


def bind_editable_text_context_menu(
    widget: tk.Text,
    *,
    menu_parent: tk.Misc | None = None,
    language: LanguageResolver = None,
) -> None:
    """Bind the standard edit context menu to an editable text widget."""

    def show_menu(event: tk.Event[tk.Misc]) -> str:
        return _show_editable_text_context_menu(
            event,
            widget,
            menu_parent=menu_parent,
            language=language,
        )

    widget.bind("<Button-3>", show_menu, add="+")
    widget.bind("<Control-Button-1>", show_menu, add="+")


def _show_editable_text_context_menu(
    event: tk.Event[tk.Misc],
    widget: tk.Text,
    *,
    menu_parent: tk.Misc | None = None,
    language: LanguageResolver = None,
) -> str:
    _place_cursor_for_context_menu(widget, event)

    parent = menu_parent or widget
    menu = tk.Menu(parent, tearoff=False)
    active_language = _resolve_language(language)
    menu.add_command(
        label=ui_text("context_cut", active_language),
        command=lambda target=widget: target.event_generate("<<Cut>>"),
    )
    menu.add_command(
        label=ui_text("context_copy", active_language),
        command=lambda target=widget: target.event_generate("<<Copy>>"),
    )
    menu.add_command(
        label=ui_text("context_paste", active_language),
        command=lambda target=widget: target.event_generate("<<Paste>>"),
    )
    menu.add_separator()
    menu.add_command(
        label=ui_text("context_select_all", active_language),
        command=lambda target=widget: _select_all_text(target),
    )
    setattr(widget, "_editable_text_context_menu", menu)
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
    return "break"


def _place_cursor_for_context_menu(
    widget: tk.Text,
    event: tk.Event[tk.Misc],
) -> None:
    widget.focus_set()
    index = widget.index(f"@{event.x},{event.y}")
    if _text_index_is_in_selection(widget, index):
        return
    widget.tag_remove(tk.SEL, "1.0", tk.END)
    widget.mark_set(tk.INSERT, index)


def _text_index_is_in_selection(widget: tk.Text, index: str) -> bool:
    ranges = widget.tag_ranges(tk.SEL)
    range_pairs = zip(ranges[0::2], ranges[1::2])
    for start, end in range_pairs:
        if widget.compare(start, "<=", index) and widget.compare(index, "<", end):
            return True
    return False


def _select_all_text(widget: tk.Text) -> None:
    widget.tag_add(tk.SEL, "1.0", tk.END)
    widget.mark_set(tk.INSERT, "1.0")
    widget.see(tk.INSERT)


def _resolve_language(language: LanguageResolver) -> str | None:
    if callable(language):
        return language()
    return language
