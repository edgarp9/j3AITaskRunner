"""Global runtime job scheduling for j3AITaskRunner."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
import logging
import threading

from domain.models import (
    AgentExecutionOptions,
    AppSettings,
    ExecutionMetadata,
    Job,
    JobId,
    JobStatus,
    ProcessMetadata,
    QUEUE_MODE_SHARED,
    QueueStopReason,
    QueueStatus,
    SessionTabId,
    TabOpenState,
    WorkspaceQueueState,
    WorkspaceTabId,
    execution_options_from_settings,
    normalize_queue_mode,
    utc_now,
)
from domain.policies import (
    is_valid_job_status_transition,
    order_pending_jobs_by_queue_order,
    select_next_runnable_job,
)

from .messages import (
    build_internal_validation_failure_message,
    build_job_status_message,
    build_launch_failure_message,
    build_retry_queued_message,
)
from .scheduler_dispatch import SchedulerDispatchMixin
from .scheduler_lifecycle import SchedulerLifecycleMixin
from .scheduler_queries import SchedulerQueryMixin
from .scheduler_queue_state import SchedulerQueueStateMixin
from .scheduler_ordering import job_list_order_key as _job_list_order_key
from .scheduler_types import (
    ExecutionHandle,
    JobExecutionRequest,
    JobExecutor,
    WorkspaceJobSummary,
)
from .session_manager import SessionManager
from .workspace_manager import WorkspaceManager

LOGGER = logging.getLogger(__name__)

_UNSET = object()
_SHARED_QUEUE_STATE_ID = "__shared_queue__"
_PENDING_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.WAITING_FOR_CONFIGURATION)
_FINISHED_JOB_STATUSES = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED)


class Scheduler(
    SchedulerLifecycleMixin,
    SchedulerQueryMixin,
    SchedulerQueueStateMixin,
    SchedulerDispatchMixin,
):
    """Manage independent per-workspace queues and execution slots."""

    def __init__(
        self,
        *,
        workspace_manager: WorkspaceManager,
        session_manager: SessionManager,
        executor: JobExecutor,
        settings_provider: Callable[[], AppSettings] | None = None,
    ) -> None:
        self._workspace_manager = workspace_manager
        self._session_manager = session_manager
        self._executor = executor
        self._settings_provider = settings_provider or AppSettings
        self._queue_states: dict[WorkspaceTabId, WorkspaceQueueState] = {}
        self._shared_queue_state = WorkspaceQueueState(
            workspace_tab_id=_SHARED_QUEUE_STATE_ID
        )
        self._jobs: dict[JobId, Job] = {}
        self._running_handles: dict[JobId, ExecutionHandle] = {}
        self._pending_cancel_job_ids: set[JobId] = set()
        self._next_job_sequence = 1
        self._next_queue_order = 1
        self._dispatch_defer_depth = 0
        self._pending_dispatch_requested = False
        self._pending_dispatch_workspace_ids: set[WorkspaceTabId] = set()
        self._pending_dispatch_previous_job_ids: dict[WorkspaceTabId, JobId] = {}
        self._state_lock = threading.RLock()

    @property
    def queue_state(self) -> WorkspaceQueueState:
        """Return the active workspace queue state."""
        return self.get_queue_state()

    @property
    def queue_mode(self) -> str:
        """Return the queue mode from current settings."""
        return self._queue_mode()

    def get_queue_state(
        self,
        workspace_tab_id: WorkspaceTabId | None = None,
    ) -> WorkspaceQueueState:
        """Return the queue state for one workspace."""
        with self._state_lock:
            resolved_workspace_tab_id = self._resolve_workspace_tab_id(workspace_tab_id)
            if self._shared_queue_mode_locked():
                return self._shared_queue_state_for_workspace_locked(
                    resolved_workspace_tab_id
                )
            return self._get_or_create_queue_state(resolved_workspace_tab_id)

    def list_queue_states(self, *, include_closed: bool = False) -> tuple[WorkspaceQueueState, ...]:
        """Return known workspace queue states in stable order."""
        with self._state_lock:
            if self._shared_queue_mode_locked():
                return tuple(
                    self._shared_queue_state_for_workspace_locked(
                        workspace_tab.workspace_tab_id
                    )
                    for workspace_tab in self._workspace_manager.list_workspace_tabs(
                        include_closed=include_closed
                    )
                )
            queue_states: list[WorkspaceQueueState] = []
            for workspace_tab_id in sorted(self._queue_states):
                if not include_closed and not self._workspace_tab_is_open(workspace_tab_id):
                    continue
                queue_states.append(self._queue_states[workspace_tab_id])
            return tuple(queue_states)

































































    def _build_execution_request(self, job: Job) -> JobExecutionRequest:
        workspace_tab = self._workspace_manager.get_workspace_tab(job.workspace_tab_id)
        session_tab = self._session_manager.get_session_tab(job.session_tab_id)
        if workspace_tab.open_state != TabOpenState.OPEN:
            raise ValueError(f"Workspace tab is closed: {workspace_tab.workspace_tab_id}")
        if session_tab.open_state != TabOpenState.OPEN:
            raise ValueError(f"Session tab is closed: {session_tab.session_tab_id}")
        operational_settings = self._operational_settings_for_job(job)
        return JobExecutionRequest(
            job_id=job.job_id,
            workspace_tab_id=workspace_tab.workspace_tab_id,
            session_tab_id=session_tab.session_tab_id,
            workspace_path=workspace_tab.workspace_path,
            session_id=None if job.force_fresh_session else session_tab.session_id,
            prompt=job.prompt,
            operational_settings=operational_settings,
            execution_options=job.execution_options,
        )

    def _operational_settings_for_job(self, job: Job) -> AppSettings:
        settings = self._settings_provider()
        options = job.execution_options
        executable_path = settings.executable_paths.get(options.agent_provider)
        return AppSettings(
            executable_path=executable_path,
            executable_paths=settings.executable_paths,
            output_font_size=settings.output_font_size,
            execution_timeout_minutes=settings.execution_timeout_minutes,
            inactivity_timeout_minutes=settings.inactivity_timeout_minutes,
            termination_grace_seconds=settings.termination_grace_seconds,
            file_logging_enabled=settings.file_logging_enabled,
            ui_language=settings.ui_language,
            agent_provider=options.agent_provider,
            queue_mode=settings.queue_mode,
        )

    def _transition_job(
        self,
        job: Job,
        next_status: JobStatus,
        *,
        queue_order: int | None | object = _UNSET,
        configuration_wait_reason: str | None | object = _UNSET,
        process_metadata: ProcessMetadata | None | object = _UNSET,
        applied_execution_metadata: ExecutionMetadata | None | object = _UNSET,
        user_message: str | None | object = _UNSET,
        started_at: datetime | None | object = _UNSET,
        completed_at: datetime | None | object = _UNSET,
    ) -> Job:
        if not is_valid_job_status_transition(job.status, next_status):
            raise ValueError(f"Invalid job status transition: {job.status} -> {next_status}")

        updates: dict[str, object] = {"status": next_status}
        if queue_order is not _UNSET:
            updates["queue_order"] = queue_order
        if configuration_wait_reason is not _UNSET:
            updates["configuration_wait_reason"] = configuration_wait_reason
        if process_metadata is not _UNSET:
            updates["process_metadata"] = process_metadata
        if applied_execution_metadata is not _UNSET:
            updates["applied_execution_metadata"] = applied_execution_metadata
        if user_message is not _UNSET:
            updates["user_message"] = user_message
        if started_at is not _UNSET:
            updates["started_at"] = started_at
        if completed_at is not _UNSET:
            updates["completed_at"] = completed_at
        return replace(job, **updates)

    def _next_job_id(self) -> JobId:
        job_id = f"job-{self._next_job_sequence}"
        self._next_job_sequence += 1
        return job_id

    def _issue_queue_order(self) -> int:
        queue_order = self._next_queue_order
        self._next_queue_order += 1
        return queue_order

    @staticmethod
    def _metadata_from_request(request: JobExecutionRequest) -> ExecutionMetadata:
        options = request.execution_options
        return ExecutionMetadata(
            model=options.model or None,
            reasoning_effort=options.reasoning_effort or None,
            agent_provider=options.agent_provider,
            agent_version=None,
        )
