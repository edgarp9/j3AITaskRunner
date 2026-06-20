"""Compatibility imports for the previous Codex-named CLI version module."""

from __future__ import annotations

from domain import DEFAULT_AGENT_PROVIDER

from .agent_cli_version import (
    DEFAULT_AGENT_CLI_VERSION_TIMEOUT_SECONDS,
    AgentCliVersionQueryResult,
    query_agent_cli_version,
)

DEFAULT_CODEX_CLI_VERSION_TIMEOUT_SECONDS = DEFAULT_AGENT_CLI_VERSION_TIMEOUT_SECONDS
CodexCliVersionQueryResult = AgentCliVersionQueryResult


def query_codex_cli_version(
    executable_path: str | None,
    *,
    timeout: float = DEFAULT_CODEX_CLI_VERSION_TIMEOUT_SECONDS,
) -> CodexCliVersionQueryResult:
    """Run the default Codex provider version command."""
    return query_agent_cli_version(
        executable_path,
        agent_provider=DEFAULT_AGENT_PROVIDER,
        timeout=timeout,
    )

__all__ = [
    "DEFAULT_AGENT_CLI_VERSION_TIMEOUT_SECONDS",
    "DEFAULT_CODEX_CLI_VERSION_TIMEOUT_SECONDS",
    "AgentCliVersionQueryResult",
    "CodexCliVersionQueryResult",
    "query_agent_cli_version",
    "query_codex_cli_version",
]
