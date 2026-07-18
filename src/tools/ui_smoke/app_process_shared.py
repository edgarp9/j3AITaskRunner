"""Shared helpers for the in-process UI smoke scenario."""

from __future__ import annotations

import re
import traceback
import tkinter as tk

SMOKE_TEXT = "j3AITaskRunner UI smoke prompt"
BULK_IMPORT_TEXT = (
    "```text\nui smoke imported step 1\n```\n\n"
    "```text\nui smoke imported step 2\n```\n"
)
POLL_INTERVAL_MS = 50


class UiSmokeFailure(RuntimeError):
    """Raised when the smoke scenario reaches a failed state."""


def _format_traceback(error: object) -> str:
    if isinstance(error, BaseException):
        return "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    return traceback.format_exc()


def _walk_widgets(root: tk.Misc):
    yield root
    try:
        children = root.winfo_children()
    except tk.TclError:
        return
    for child in children:
        yield from _walk_widgets(child)


def _widget_exists(widget: object | None) -> bool:
    if widget is None:
        return False
    try:
        return bool(widget.winfo_exists())
    except (AttributeError, tk.TclError):
        return False


def _stringify_statuses(statuses: dict[str, object]) -> str:
    return ", ".join(
        f"{prompt}={getattr(status, 'value', str(status))}"
        for prompt, status in statuses.items()
    )


def has_fatal_output(output: str) -> bool:
    """Return whether process output contains crash-like diagnostics."""
    return re.search(r"crash|panic|fatal|traceback|unhandled", output, re.IGNORECASE) is not None
