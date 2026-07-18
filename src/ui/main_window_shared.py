"""Shared helpers for MainWindow mixins."""

from __future__ import annotations

from datetime import datetime
import logging
import tkinter as tk
from tkinter import scrolledtext, ttk

from domain import Job, JobStatus, SessionTabKind
from domain.localization import normalize_ui_language

from .i18n import localize_runtime_message, text as ui_text
from .main_window_state import RuntimeUiUpdateBatch, TEXT_AUTOSCROLL_BOTTOM_THRESHOLD

LOGGER = logging.getLogger("ui.main_window")

def _set_optional_label_text(
    label: tk.Widget,
    value_var: tk.StringVar,
    value: str,
) -> None:
    value_var.set(value)
    if value:
        label.grid()
    else:
        label.grid_remove()

def _window_language(window: object) -> str:
    runtime = getattr(window, "_runtime", None)
    settings = getattr(runtime, "settings", None)
    language = getattr(settings, "ui_language", None)
    if language is None:
        language = getattr(window, "_ui_language", None)
    return normalize_ui_language(language)

def _tr_for(window: object, key: str, **values: object) -> str:
    return ui_text(key, _window_language(window), **values)

def _localize_status_message(window: object, message: str) -> str:
    return localize_runtime_message(message, _window_language(window))

def _split_dropped_workspace_paths(widget: tk.Misc, data: str) -> tuple[str, ...]:
    if not data:
        return ()

    try:
        raw_paths = widget.tk.splitlist(data)
    except tk.TclError:
        raw_paths = (data,)

    return tuple(
        normalized_path
        for raw_path in raw_paths
        if (normalized_path := str(raw_path).strip())
    )

def _session_kind_uses_prompt_editor(kind: SessionTabKind) -> bool:
    return kind != SessionTabKind.PRESET

def _notebook_insert_position(
    notebook: ttk.Notebook, requested_index: int
) -> int | str:
    if requested_index >= len(notebook.tabs()):
        return tk.END
    return requested_index

def _queue_full_session_view_refresh(
    updates: RuntimeUiUpdateBatch,
    session_tab_id: str,
) -> None:
    if session_tab_id not in updates.full_session_views:
        updates.full_session_views.append(session_tab_id)
    updates.session_summaries.discard(session_tab_id)
    updates.session_histories.discard(session_tab_id)
    updates.session_outputs.pop(session_tab_id, None)

def _is_pending_close_job(job: Job) -> bool:
    return job.status in (JobStatus.QUEUED, JobStatus.WAITING_FOR_CONFIGURATION)

def _format_scheduled_run_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")

def _safe_configure(widget: tk.Misc, **options: object) -> None:
    try:
        widget.configure(**options)
    except tk.TclError:
        LOGGER.debug(
            "Failed to apply scaled widget options. widget=%s", widget, exc_info=True
        )

def _should_follow_text_end(widget: scrolledtext.ScrolledText) -> bool:
    existing_content = widget.get("1.0", "end-1c")
    if not existing_content.strip():
        return True

    _top_fraction, bottom_fraction = widget.yview()
    return bottom_fraction >= TEXT_AUTOSCROLL_BOTTOM_THRESHOLD

