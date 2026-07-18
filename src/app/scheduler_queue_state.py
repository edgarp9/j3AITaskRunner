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


class SchedulerQueueStateMixin:
    @contextmanager
    def defer_dispatch(self) -> Iterator[None]:
        """Record follow-up dispatch requests without running validation immediately."""
        with self._state_lock:
            self._dispatch_defer_depth += 1
            try:
                yield
            finally:
                self._dispatch_defer_depth -= 1

    def has_pending_dispatch(self) -> bool:
        """Return whether a deferred dispatch should be run off the caller thread."""
        with self._state_lock:
            return self._pending_dispatch_requested

    def pending_dispatch_workspace_tab_ids(self) -> tuple[WorkspaceTabId, ...]:
        """Return workspaces that currently have deferred dispatch work."""
        with self._state_lock:
            return tuple(sorted(self._pending_dispatch_workspace_ids))

    def dispatch_next_job(
        self,
        *,
        excluded_workspace_tab_ids: Iterable[WorkspaceTabId] = (),
    ) -> Job | None:
        """Run a previously deferred dispatch request, or dispatch normally if possible."""
        excluded_workspace_ids = set(excluded_workspace_tab_ids)
        with self._state_lock:
            previous_jobs = self._take_pending_dispatch_previous_jobs_locked(
                excluded_workspace_tab_ids=excluded_workspace_ids,
            )
        return self._dispatch_next_job(
            previous_jobs=previous_jobs,
            excluded_workspace_tab_ids=excluded_workspace_ids,
        )

    def _finish_running_job(
        self,
        job_id: JobId,
        *,
        final_status: JobStatus,
        when: datetime | None = None,
        process_metadata: ProcessMetadata | None = None,
        user_message: str | None = None,
    ) -> Job:
        with self._state_lock:
            job = self._get_job_locked(job_id)
            if job.status != JobStatus.RUNNING:
                raise ValueError(f"Job is not running: {job_id}")

            timestamp = when or utc_now()
            updated = self._transition_job(
                job,
                final_status,
                process_metadata=process_metadata if process_metadata is not None else job.process_metadata,
                completed_at=timestamp,
                configuration_wait_reason=None,
                user_message=user_message or build_job_status_message(final_status),
            )
            self._jobs[job_id] = updated
            self._running_handles.pop(job_id, None)
            self._pending_cancel_job_ids.discard(job_id)

            self._clear_workspace_running_job(job.workspace_tab_id, job_id)
            if not self._shared_queue_mode_locked():
                self._rebalance_workspace_queue_order_locked(job.workspace_tab_id)
            updated = self._jobs[job_id]

            self._stop_workspace_queue_if_finished_locked(job.workspace_tab_id)
            should_dispatch = self._has_started_queue() and self._request_dispatch_locked(
                previous_job=updated
            )

        if should_dispatch:
            self._dispatch_next_job(previous_job=updated)
        return updated

    def _request_running_job_cancel(self, job_id: JobId) -> None:
        with self._state_lock:
            handle = self._request_running_job_cancel_locked(job_id)
        if handle is not None:
            self._cancel_handle(job_id, handle)

    def _request_running_job_cancel_locked(self, job_id: JobId) -> ExecutionHandle | None:
        handle = self._running_handles.get(job_id)
        if handle is None:
            job = self._jobs.get(job_id)
            if job is not None and job.status == JobStatus.RUNNING:
                self._pending_cancel_job_ids.add(job_id)
            return None
        return handle

    def _cancel_handle(self, job_id: JobId, handle: ExecutionHandle) -> None:
        try:
            self._executor.cancel(handle)
        except Exception:
            LOGGER.exception("Failed to cancel running job. job_id=%s", job_id)

    def _resolve_workspace_tab_id(
        self,
        workspace_tab_id: WorkspaceTabId | None,
    ) -> WorkspaceTabId:
        if workspace_tab_id is not None:
            self._workspace_manager.get_workspace_tab(workspace_tab_id)
            return workspace_tab_id

        active_workspace = self._workspace_manager.get_active_workspace_tab()
        if active_workspace is None:
            raise ValueError("No active workspace tab is available.")
        return active_workspace.workspace_tab_id

    def _get_or_create_queue_state(self, workspace_tab_id: WorkspaceTabId) -> WorkspaceQueueState:
        queue_state = self._queue_states.get(workspace_tab_id)
        if queue_state is None:
            queue_state = WorkspaceQueueState(workspace_tab_id=workspace_tab_id)
            self._queue_states[workspace_tab_id] = queue_state
        return queue_state

    def _has_started_queue(self) -> bool:
        if self._shared_queue_mode_locked():
            return self._shared_queue_is_started_locked()
        return any(
            self._workspace_queue_is_started(workspace_tab_id)
            for workspace_tab_id in self._queue_states
        )

    def _workspace_queue_is_started(self, workspace_tab_id: WorkspaceTabId) -> bool:
        if self._shared_queue_mode_locked():
            return (
                self._shared_queue_is_started_locked()
                and self._workspace_tab_is_open(workspace_tab_id)
            )

        queue_state = self._queue_states.get(workspace_tab_id)
        if queue_state is None or queue_state.status != QueueStatus.STARTED:
            return False

        return self._workspace_tab_is_open(workspace_tab_id)

    def _workspace_has_runnable_job_locked(self, workspace_tab_id: WorkspaceTabId) -> bool:
        if self._shared_queue_mode_locked():
            return self._has_shared_runnable_job_locked()
        return (
            select_next_runnable_job(
                job
                for job in self._jobs.values()
                if job.workspace_tab_id == workspace_tab_id
            )
            is not None
        )

    def _workspace_tab_is_open(self, workspace_tab_id: WorkspaceTabId) -> bool:
        try:
            workspace_tab = self._workspace_manager.get_workspace_tab(workspace_tab_id)
        except KeyError:
            return False
        return workspace_tab.open_state == TabOpenState.OPEN

    def _get_workspace_running_job_locked(
        self,
        workspace_tab_id: WorkspaceTabId,
    ) -> Job | None:
        if self._shared_queue_mode_locked():
            running_job = self._get_shared_running_job_locked()
            if running_job is not None and running_job.workspace_tab_id == workspace_tab_id:
                return running_job
            return None

        queue_state = self._queue_states.get(workspace_tab_id)
        if queue_state is None or queue_state.running_job_id is None:
            return None

        running_job = self._jobs.get(queue_state.running_job_id)
        if (
            running_job is not None
            and running_job.status == JobStatus.RUNNING
            and running_job.workspace_tab_id == workspace_tab_id
        ):
            return running_job

        self._queue_states[workspace_tab_id] = replace(queue_state, running_job_id=None)
        return None

    def _workspace_has_running_job_locked(
        self,
        workspace_tab_id: WorkspaceTabId,
        job_id: JobId,
    ) -> bool:
        running_job = self._get_workspace_running_job_locked(workspace_tab_id)
        return running_job is not None and running_job.job_id == job_id

    def _session_tab_is_open(self, session_tab_id: SessionTabId) -> bool:
        try:
            session_tab = self._session_manager.get_session_tab(session_tab_id)
        except KeyError:
            return False
        return session_tab.open_state == TabOpenState.OPEN

    def _clear_workspace_running_job(self, workspace_tab_id: WorkspaceTabId, job_id: JobId) -> None:
        if self._shared_queue_mode_locked():
            if self._shared_queue_state.running_job_id == job_id:
                self._shared_queue_state = replace(
                    self._shared_queue_state,
                    running_job_id=None,
                )
            return

        queue_state = self._queue_states.get(workspace_tab_id)
        if queue_state is None or queue_state.running_job_id != job_id:
            return
        self._queue_states[workspace_tab_id] = replace(queue_state, running_job_id=None)

    def _stop_workspace_queue_if_finished_locked(self, workspace_tab_id: WorkspaceTabId) -> None:
        if self._shared_queue_mode_locked():
            if self._shared_queue_state.status != QueueStatus.STARTED:
                return
            if not self._shared_all_jobs_finished_locked():
                return
            self._shared_queue_state = replace(
                self._shared_queue_state,
                status=QueueStatus.STOPPED,
                running_job_id=None,
                last_stop_reason=QueueStopReason.ALL_JOBS_COMPLETED,
            )
            return

        queue_state = self._get_or_create_queue_state(workspace_tab_id)
        if queue_state.status != QueueStatus.STARTED:
            return
        if not self._workspace_all_jobs_finished_locked(workspace_tab_id):
            return

        self._queue_states[workspace_tab_id] = replace(
            queue_state,
            status=QueueStatus.STOPPED,
            running_job_id=None,
            last_stop_reason=QueueStopReason.ALL_JOBS_COMPLETED,
        )

    def _workspace_all_jobs_finished_locked(self, workspace_tab_id: WorkspaceTabId) -> bool:
        workspace_jobs = tuple(
            job
            for job in self._list_jobs_locked()
            if job.workspace_tab_id == workspace_tab_id
        )
        if not workspace_jobs:
            return False
        return all(job.status in _FINISHED_JOB_STATUSES for job in workspace_jobs)

    def _queue_mode(self) -> str:
        return normalize_queue_mode(self._settings_provider().queue_mode)

    def _shared_queue_mode_locked(self) -> bool:
        return self._queue_mode() == QUEUE_MODE_SHARED

    def _shared_queue_state_for_workspace_locked(
        self,
        workspace_tab_id: WorkspaceTabId,
    ) -> WorkspaceQueueState:
        return replace(
            self._shared_queue_state,
            workspace_tab_id=workspace_tab_id,
        )

    def _shared_queue_is_started_locked(self) -> bool:
        if self._shared_queue_state.status != QueueStatus.STARTED:
            return False
        return bool(self._open_workspace_tab_ids_locked())

    def _open_workspace_tab_ids_locked(self) -> tuple[WorkspaceTabId, ...]:
        return tuple(
            workspace_tab.workspace_tab_id
            for workspace_tab in self._workspace_manager.list_workspace_tabs(
                include_closed=False
            )
        )

    def _get_shared_running_job_locked(self) -> Job | None:
        running_job_id = self._shared_queue_state.running_job_id
        if running_job_id is None:
            return None

        running_job = self._jobs.get(running_job_id)
        if running_job is not None and running_job.status == JobStatus.RUNNING:
            return running_job

        self._shared_queue_state = replace(
            self._shared_queue_state,
            running_job_id=None,
        )
        return None

    def _has_shared_runnable_job_locked(self) -> bool:
        open_workspace_tab_ids = set(self._open_workspace_tab_ids_locked())
        if not open_workspace_tab_ids:
            return False

        return (
            select_next_runnable_job(
                job
                for job in self._jobs.values()
                if job.workspace_tab_id in open_workspace_tab_ids
            )
            is not None
        )

    def _shared_all_jobs_finished_locked(self) -> bool:
        jobs = tuple(
            job
            for job in self._list_jobs_locked()
            if self._workspace_tab_is_open(job.workspace_tab_id)
        )
        if not jobs:
            return False
        return all(job.status in _FINISHED_JOB_STATUSES for job in jobs)

