"""Shared subprocess option helpers for agent CLI providers."""

from __future__ import annotations

import os
import subprocess

WINDOWS_CREATE_NO_WINDOW = 0x08000000


def hidden_console_creationflags(*, os_name: str | None = None) -> int:
    """Return Windows creation flags that keep CLI child windows hidden."""
    if (os_name or os.name) != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", WINDOWS_CREATE_NO_WINDOW)
