"""Controller layer that binds queue execution orchestration to UI-safe events."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import logging
from queue import Empty, Queue

from domain import (
    AgentExecutionOptions,
    AppSettings,
    Job,
    JobStatus,
    QUEUE_MODE_SHARED,
    QueueStatus,
    QueueStopReason,
    SessionTab,
    SessionTabKind,
    SessionTabId,
    WorkspaceTab,
)
from domain.models import utc_now
from infra.process_runner import AgentRunStatus

from .execution_worker import (
    BackgroundExecutionRunner,
    ExecutionCompletedEvent,
    ExecutionLogEvent,
    ExecutionSessionIdEvent,
    ExecutionWorker,
    ExecutionWorkerEvent,
)
from .scheduler import Scheduler
from .session_manager import CompletedSessionSummary, SessionManager
from .use_cases import apply_execution_result, confirm_session_id_for_job
from .workspace_manager import WorkspaceManager

LOGGER = logging.getLogger(__name__)
_ACTIVE_QUEUE_JOB_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.WAITING_FOR_CONFIGURATION,
    JobStatus.RUNNING,
)
_IMMEDIATE_RUN_BLOCKING_JOB_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.WAITING_FOR_CONFIGURATION,
    JobStatus.RUNNING,
)


@dataclass(slots=True, frozen=True)
class JobStatusChangedEvent:
    """UI-safe event describing one job status transition."""

    job_id: str
    workspace_tab_id: str
    session_tab_id: str
    previous_status: JobStatus | None
    current_status: JobStatus
    configuration_wait_reason: str | None
    user_message: str | None
    occurred_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True, frozen=True)
class SessionIdConfirmedEvent:
    """UI-safe event describing a session id that became available."""

    job_id: str
    session_tab_id: str
    session_id: str
    occurred_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True, frozen=True)
class LogAppendedEvent:
    """UI-safe event describing one log line that can be appended later."""

    job_id: str
    workspace_tab_id: str
    session_tab_id: str
    stream_name: str
    line: str
    occurred_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True, frozen=True)
class CompletedSessionUpdatedEvent:
    """UI-safe event describing runtime completed-session data changes."""

    job_id: str
    summary: CompletedSessionSummary
    occurred_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True, frozen=True)
class JobExecutionResultCapturedEvent:
    """UI-safe event with the final execution payload for app-level follow-ups."""

    job_id: str
    workspace_tab_id: str
    session_tab_id: str
    status: AgentRunStatus
    last_message: str | None
    occurred_at: datetime = field(default_factory=utc_now)


ControllerEvent = (
    JobStatusChangedEvent
    | SessionIdConfirmedEvent
    | LogAppendedEvent
    | CompletedSessionUpdatedEvent
    | JobExecutionResultCapturedEvent
)


@dataclass(slots=True, frozen=True)
class SessionCloseResult:
    """Outcome of closing one session tab."""

    session_tab: SessionTab
    canceled_job: Job | None = None
    removed_queued_job_count: int = 0
    queue_stopped: bool = False

@dataclass(slots=True, frozen=True)
class WorkspaceCloseResult:
    """Outcome of closing one workspace tab."""

    workspace_tab: WorkspaceTab
    closed_sessions: tuple[SessionTab, ...]
    canceled_job: Job | None = None
    removed_queued_job_count: int = 0
    queue_stopped: bool = False

