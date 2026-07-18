"""Fire-and-forget external hook launcher for completed sessions."""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
import subprocess
from typing import Protocol

from domain import SessionExitHookConfig

from .subprocess_options import hidden_console_creationflags

LOGGER = logging.getLogger(__name__)


class SessionExitHookRunner(Protocol):
    """Callable contract used by AppRuntime to launch one session exit hook."""

    def __call__(
        self,
        config: SessionExitHookConfig,
        workspace_path: str,
    ) -> bool:
        """Launch the hook and return whether process creation was requested."""


PopenFactory = Callable[..., subprocess.Popen]


def launch_session_exit_hook(
    config: SessionExitHookConfig,
    workspace_path: str,
    *,
    popen_factory: PopenFactory = subprocess.Popen,
    os_name: str | None = None,
) -> bool:
    """Launch a configured session exit hook without waiting for completion."""
    if not config.is_runnable:
        return False

    command = (config.executable_path, *config.arguments)
    platform_name = os_name or os.name
    kwargs: dict[str, object] = {
        "cwd": workspace_path,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
    }
    creationflags = hidden_console_creationflags(os_name=platform_name)
    if creationflags:
        kwargs["creationflags"] = creationflags

    try:
        popen_factory(command, **kwargs)
    except Exception:
        LOGGER.exception(
            "Failed to launch session exit hook. executable_path=%s cwd=%s",
            config.executable_path,
            workspace_path,
        )
        return False

    LOGGER.info(
        "Session exit hook launched. executable_path=%s argument_count=%s cwd=%s",
        config.executable_path,
        len(config.arguments),
        workspace_path,
    )
    return True
