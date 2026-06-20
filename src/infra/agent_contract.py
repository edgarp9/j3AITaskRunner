"""Common external agent CLI execution contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging
from pathlib import Path
from typing import IO, Any, Protocol

from domain import AgentExecutionOptions, AppSettings

LOGGER = logging.getLogger(__name__)


class SupportsAgentExecutionRequest(Protocol):
    """Minimal request contract needed by the subprocess runner."""

    job_id: str
    workspace_path: str
    session_id: str | None
    prompt: str
    operational_settings: AppSettings
    execution_options: AgentExecutionOptions


class PopenLike(Protocol):
    """Subset of subprocess.Popen used by the runner."""

    pid: int | None
    stdin: IO[str] | None
    stdout: IO[str] | None
    stderr: IO[str] | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@dataclass(slots=True, frozen=True)
class AgentRunRequest:
    """Concrete request model for one external agent CLI execution."""

    job_id: str
    workspace_path: str
    prompt: str
    operational_settings: AppSettings
    session_id: str | None = None
    execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )


@dataclass(slots=True, frozen=True)
class ExecutionArtifactPaths:
    """Filesystem paths for one execution attempt's diagnostic artifacts."""

    root_dir: Path
    prompt_path: Path
    stdout_jsonl_path: Path
    stderr_log_path: Path
    last_message_path: Path
    launch_metadata_path: Path
    ephemeral_cleanup: Callable[[], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def cleanup_ephemeral(self) -> None:
        """Remove temporary execution artifacts when no file logs should remain."""
        if self.ephemeral_cleanup is None:
            return
        try:
            self.ephemeral_cleanup()
        except OSError:
            LOGGER.warning(
                "Failed to clean temporary agent CLI artifacts. root_dir=%s",
                self.root_dir,
                exc_info=True,
            )


class AgentRunStatus(str, Enum):
    """Final outcome of one external agent CLI execution."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(slots=True, frozen=True)
class AgentStreamEvent:
    """One parsed provider stdout event suitable for app-level progress logs."""

    line_number: int
    event_type: str
    payload: dict[str, Any]
    thread_id: str | None = None
    message: str | None = None
    raw_line: str | None = None


@dataclass(slots=True, frozen=True)
class AgentParseSummary:
    """Aggregate provider stdout parse result for one execution."""

    thread_id: str | None = None
    saw_turn_completed: bool = False
    turn_failed_events: tuple[AgentStreamEvent, ...] = ()
    error_events: tuple[AgentStreamEvent, ...] = ()
    malformed_lines: tuple[int, ...] = ()
    total_events: int = 0

    @property
    def has_failure_event(self) -> bool:
        """Return whether any terminal failure event was observed."""
        return bool(self.turn_failed_events)

    @property
    def has_error_event(self) -> bool:
        """Return whether any non-terminal CLI error event was observed."""
        return bool(self.error_events)

    @property
    def failure_events(self) -> tuple[AgentStreamEvent, ...]:
        """Return terminal failure events in observed order."""
        return self.turn_failed_events


@dataclass(slots=True, frozen=True)
class AgentRunResult:
    """Resolved result of one external agent CLI execution."""

    status: AgentRunStatus
    command: tuple[str, ...]
    artifacts: ExecutionArtifactPaths
    parser_summary: AgentParseSummary = field(default_factory=AgentParseSummary)
    exit_code: int | None = None
    session_id: str | None = None
    last_message: str | None = None
    failure_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def success(self) -> bool:
        """Return whether the execution completed successfully."""
        return self.status == AgentRunStatus.COMPLETED


class AgentCliAdapter(Protocol):
    """Provider-specific CLI contract used by the common subprocess runner."""

    provider_id: str
    display_name: str

    def create_run_adapter(self) -> "AgentCliAdapter":
        """Return a fresh adapter instance for one execution stream."""

    def validate(self, request: SupportsAgentExecutionRequest) -> str | None:
        """Return a user-facing configuration issue when execution cannot start."""

    def build_command(
        self,
        request: SupportsAgentExecutionRequest,
        *,
        last_message_path: str | Path,
    ) -> tuple[str, ...]:
        """Build the provider CLI command for one request."""

    def build_popen_kwargs(
        self,
        process_cwd: str,
        *,
        os_name: str | None = None,
    ) -> dict[str, Any]:
        """Build subprocess.Popen keyword arguments for the provider CLI."""

    def build_stdin_payload(self, request: SupportsAgentExecutionRequest) -> str | None:
        """Return the text to write to stdin, or None when stdin is unused."""

    def feed_stdout_line(self, raw_line: str) -> AgentStreamEvent | None:
        """Parse one stdout line and update provider-specific stream state."""

    def build_parse_summary(self) -> AgentParseSummary:
        """Return the provider-specific stdout parse summary."""

    def extract_session_id(self, parser_summary: AgentParseSummary) -> str | None:
        """Return the confirmed session id from a parse summary."""

    def read_last_message(self, artifacts: ExecutionArtifactPaths) -> str | None:
        """Read the provider's final response artifact, when available."""

    def resolve_outcome(
        self,
        *,
        parser_summary: AgentParseSummary,
        exit_code: int | None,
        last_message: str | None,
        cancel_requested: bool,
        timeout_failure_reason: str | None,
    ) -> tuple[AgentRunStatus, str | None]:
        """Resolve final status and failure reason from provider signals."""

    def build_launch_metadata(
        self,
        *,
        request: SupportsAgentExecutionRequest,
        command: tuple[str, ...],
        artifacts: ExecutionArtifactPaths,
        started_at: datetime,
        process_cwd: str,
    ) -> dict[str, Any]:
        """Build launch metadata saved with execution artifacts."""

    def build_version_command(self, executable_reference: str | None) -> tuple[str, ...]:
        """Return the provider's default version query command."""
