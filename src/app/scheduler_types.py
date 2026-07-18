"""Scheduler execution contracts and lightweight state types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from domain.models import (
    AgentExecutionOptions,
    AppSettings,
    JobId,
    SessionId,
    SessionTabId,
    WorkspacePath,
    WorkspaceTabId,
)


@dataclass(slots=True, frozen=True)
class ExecutionHandle:
    """Opaque runtime handle for a running external execution."""

    handle_id: str


@dataclass(slots=True, frozen=True)
class JobExecutionRequest:
    """Execution request composed at job start time from current runtime state."""

    job_id: JobId
    workspace_tab_id: WorkspaceTabId
    session_tab_id: SessionTabId
    workspace_path: WorkspacePath
    session_id: SessionId | None
    prompt: str
    operational_settings: AppSettings
    execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )


@dataclass(slots=True, frozen=True)
class WorkspaceJobSummary:
    """Lightweight job presence state for one workspace."""

    has_jobs: bool = False
    has_runnable_jobs: bool = False
    has_running_job: bool = False


class JobExecutor(Protocol):
    """Execution contract for a future infra-backed subprocess runner."""

    def validate(self, request: JobExecutionRequest) -> str | None:
        """Return a configuration-wait reason when execution cannot start yet."""

    def launch(self, request: JobExecutionRequest) -> ExecutionHandle:
        """Start the external execution for a prepared request."""

    def cancel(self, handle: ExecutionHandle) -> None:
        """Cancel a previously launched execution."""
