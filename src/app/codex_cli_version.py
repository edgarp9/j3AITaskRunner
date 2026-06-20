"""Compatibility imports for the previous Codex-named CLI version module."""

from __future__ import annotations

from .agent_cli_version import (
    CLI_VERSION_ISSUE_MESSAGES,
    load_agent_cli_version_text,
)


def load_codex_cli_version_text(
    executable_path: str | None,
    agent_provider: str | None = None,
) -> str:
    """Compatibility wrapper for previous Codex-named callers."""
    return load_agent_cli_version_text(
        executable_path,
        agent_provider=agent_provider,
    )

__all__ = [
    "CLI_VERSION_ISSUE_MESSAGES",
    "load_agent_cli_version_text",
    "load_codex_cli_version_text",
]
