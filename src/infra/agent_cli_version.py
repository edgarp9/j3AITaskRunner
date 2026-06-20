"""Agent CLI version probing helpers."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import sys

from .executable import normalize_executable_reference, resolve_executable_reference
from .process_runner import build_agent_cli_adapter
from .subprocess_options import hidden_console_creationflags

DEFAULT_AGENT_CLI_VERSION_TIMEOUT_SECONDS = 3.0


@dataclass(slots=True, frozen=True)
class AgentCliVersionQueryResult:
    """Result of probing a configured agent CLI executable."""

    version_text: str | None = None
    issue_code: str | None = None
    detail: str | None = None

    @property
    def success(self) -> bool:
        return self.issue_code is None and self.version_text is not None


def query_agent_cli_version(
    executable_path: str | None,
    *,
    agent_provider: str | None = None,
    timeout: float = DEFAULT_AGENT_CLI_VERSION_TIMEOUT_SECONDS,
) -> AgentCliVersionQueryResult:
    """Run an agent CLI ``--version`` command and return the first non-empty line."""
    normalized_path = normalize_executable_reference(executable_path)
    if normalized_path is None:
        return AgentCliVersionQueryResult(issue_code="missing_executable_path")

    executable = resolve_executable_reference(
        normalized_path,
        agent_provider=agent_provider,
    )
    if executable is None:
        return AgentCliVersionQueryResult(issue_code="invalid_executable_path")

    kwargs: dict[str, object] = {
        "capture_output": True,
        "check": False,
        "encoding": "utf-8",
        "errors": "replace",
        "text": True,
        "timeout": timeout,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = hidden_console_creationflags(os_name="nt")

    adapter = build_agent_cli_adapter(agent_provider)
    command = (
        adapter.build_version_command(str(executable))
        if adapter is not None
        else (str(executable), "--version")
    )

    try:
        completed = subprocess.run(command, **kwargs)
    except subprocess.TimeoutExpired:
        return AgentCliVersionQueryResult(issue_code="timeout")
    except OSError as exc:
        return AgentCliVersionQueryResult(
            issue_code="launch_failed",
            detail=f"{type(exc).__name__}: {exc}",
        )

    output_line = _first_non_empty_line(completed.stdout) or _first_non_empty_line(
        completed.stderr
    )
    if completed.returncode != 0:
        return AgentCliVersionQueryResult(
            issue_code="nonzero_exit",
            detail=f"exit_code={completed.returncode}"
            + (f", output={output_line}" if output_line else ""),
        )
    if output_line is None:
        return AgentCliVersionQueryResult(issue_code="empty_output")
    return AgentCliVersionQueryResult(version_text=output_line)


def _first_non_empty_line(value: str | None) -> str | None:
    if not value:
        return None
    for line in value.splitlines():
        normalized_line = line.strip()
        if normalized_line:
            return normalized_line
    return None
