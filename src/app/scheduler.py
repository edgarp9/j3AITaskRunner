"""Global runtime job scheduling for j3AITaskRunner."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
import logging
import threading
from typing import Protocol

from domain.models import (
    AgentExecutionOptions,
    AppSettings,
    ExecutionMetadata,
    Job,
    JobId,
    JobStatus,
    ProcessMetadata,
    QueueStopReason,
    QueueStatus,
    SessionId,
    SessionTabId,
    TabOpenState,
    WorkspacePath,
    WorkspaceQueueState,
    WorkspaceTabId,
    execution_options_from_settings,
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
from .session_manager import SessionManager
from .workspace_manager import WorkspaceManager

LOGGER = logging.getLogger(__name__)

_UNSET = object()
_PENDING_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.WAITING_FOR_CONFIGURATION)
_FINISHED_JOB_STATUSES = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED)


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


@dataclass(slots=True)
class _DispatchSelectionState:
    """Reusable dispatch candidate state for one dispatch loop."""

    jobs_by_workspace: dict[WorkspaceTabId, list[Job]]
    next_jobs_by_workspace: dict[WorkspaceTabId, Job]


class JobExecutor(Protocol):
    """Execution contract for a future infra-backed subprocess runner."""

    def validate(self, request: JobExecutionRequest) -> str | None:
        """Return a configuration-wait reason when execution cannot start yet."""

    def launch(self, request: JobExecutionRequest) -> ExecutionHandle:
        """Start the external execution for a prepared request."""

    def cancel(self, handle: ExecutionHandle) -> None:
        """Cancel a previously launched execution."""


class Scheduler:
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

    def get_queue_state(
        self,
        workspace_tab_id: WorkspaceTabId | None = None,
    ) -> WorkspaceQueueState:
        """Return the queue state for one workspace."""
        with self._state_lock:
            resolved_workspace_tab_id = self._resolve_workspace_tab_id(workspace_tab_id)
            return self._get_or_create_queue_state(resolved_workspace_tab_id)

    def list_queue_states(self, *, include_closed: bool = False) -> tuple[WorkspaceQueueState, ...]:
        """Return known workspace queue states in stable order."""
        with self._state_lock:
            queue_states: list[WorkspaceQueueState] = []
            for workspace_tab_id in sorted(self._queue_states):
                if not include_closed and not self._workspace_tab_is_open(workspace_tab_id):
                    continue
                queue_states.append(self._queue_states[workspace_tab_id])
            return tuple(queue_states)

    def register_job(
        self,
        session_tab_id: SessionTabId,
        prompt: str,
        *,
        when: datetime | None = None,
        force_fresh_session: bool = False,
        execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        """Register one queued job that belongs to exactly one session tab."""
        registered_jobs = self.register_jobs(
            ((session_tab_id, prompt),),
            when=when,
            force_fresh_session=force_fresh_session,
            execution_options=execution_options,
        )
        return registered_jobs[0]

    def register_jobs(
        self,
        job_requests: Sequence[tuple[SessionTabId, str]],
        *,
        when: datetime | None = None,
        force_fresh_session: bool = False,
        execution_options: AgentExecutionOptions | None = None,
    ) -> tuple[Job, ...]:
        """Register queued jobs and rebalance each affected workspace once."""
        requests = tuple(job_requests)
        if not requests:
            return ()

        for _, prompt in requests:
            if not prompt.strip():
                raise ValueError("prompt must not be blank.")

        with self._state_lock:
            resolved_execution_options = (
                execution_options
                if execution_options is not None
                else execution_options_from_settings(self._settings_provider())
            )
            registered_job_ids: list[JobId] = []
            workspace_tab_ids: list[WorkspaceTabId] = []
            for session_tab_id, prompt in requests:
                session_tab = self._session_manager.get_session_tab(session_tab_id)
                timestamp = when or utc_now()
                job_id = self._next_job_id()
                job = Job(
                    job_id=job_id,
                    workspace_tab_id=session_tab.workspace_tab_id,
                    session_tab_id=session_tab_id,
                    prompt=prompt,
                    status=JobStatus.QUEUED,
                    user_message=build_job_status_message(JobStatus.QUEUED),
                    queue_order=self._issue_queue_order(),
                    process_metadata=None,
                    applied_execution_metadata=None,
                    created_at=timestamp,
                    started_at=None,
                    completed_at=None,
                    force_fresh_session=force_fresh_session,
                    execution_options=resolved_execution_options,
                )
                self._jobs[job_id] = job
                registered_job_ids.append(job_id)
                if session_tab.workspace_tab_id not in workspace_tab_ids:
                    workspace_tab_ids.append(session_tab.workspace_tab_id)

            for workspace_tab_id in workspace_tab_ids:
                self._rebalance_workspace_queue_order_locked(workspace_tab_id)

            should_dispatch = any(
                self._workspace_queue_is_started(workspace_tab_id)
                and self._get_workspace_running_job_locked(workspace_tab_id) is None
                for workspace_tab_id in workspace_tab_ids
            ) and self._request_dispatch_locked(workspace_tab_ids=workspace_tab_ids)

        if should_dispatch:
            self._dispatch_next_job()

        with self._state_lock:
            return tuple(self._jobs[job_id] for job_id in registered_job_ids)

    def prioritize_queued_jobs(self, job_ids: Sequence[JobId]) -> tuple[Job, ...]:
        """Move queued jobs before other pending jobs in each affected workspace."""
        ordered_job_ids = tuple(dict.fromkeys(job_ids))
        if not ordered_job_ids:
            return ()

        with self._state_lock:
            selected_jobs: list[Job] = []
            for job_id in ordered_job_ids:
                job = self._get_job_locked(job_id)
                if job.status != JobStatus.QUEUED:
                    raise ValueError(f"Can only prioritize queued jobs: {job_id}")
                selected_jobs.append(job)

            workspace_tab_ids = tuple(
                dict.fromkeys(job.workspace_tab_id for job in selected_jobs)
            )
            for workspace_tab_id in workspace_tab_ids:
                workspace_priority_job_ids = tuple(
                    job_id
                    for job_id in ordered_job_ids
                    if self._jobs[job_id].workspace_tab_id == workspace_tab_id
                )
                self._prioritize_workspace_pending_jobs_locked(
                    workspace_tab_id,
                    priority_job_ids=workspace_priority_job_ids,
                )
                # Explicit priority replaces any deferred follow-up dispatch hint.
                self._pending_dispatch_previous_job_ids.pop(workspace_tab_id, None)

            return tuple(self._jobs[job_id] for job_id in ordered_job_ids)

    def start_queue(
        self,
        workspace_tab_id: WorkspaceTabId | None = None,
    ) -> WorkspaceQueueState:
        """Start one workspace queue and launch the next runnable job when possible."""
        with self._state_lock:
            resolved_workspace_tab_id = self._resolve_workspace_tab_id(workspace_tab_id)
            queue_state = self._get_or_create_queue_state(resolved_workspace_tab_id)
            if (
                queue_state.status != QueueStatus.STARTED
                and not self._workspace_has_runnable_job_locked(resolved_workspace_tab_id)
            ):
                return replace(queue_state, status=QueueStatus.STOPPED)

            self._queue_states[resolved_workspace_tab_id] = replace(
                queue_state,
                status=QueueStatus.STARTED,
                last_stop_reason=None,
            )
            should_dispatch = self._request_dispatch_locked(
                workspace_tab_id=resolved_workspace_tab_id
            )

        if should_dispatch:
            self._dispatch_next_job()

        with self._state_lock:
            return self._queue_states[resolved_workspace_tab_id]

    def stop_queue(
        self,
        workspace_tab_id: WorkspaceTabId | None = None,
        *,
        reason: QueueStopReason | str = QueueStopReason.USER_STOPPED,
        when: datetime | None = None,
    ) -> WorkspaceQueueState:
        """Stop one workspace queue and request cancellation of its running job if present."""
        handle_to_cancel: ExecutionHandle | None = None
        with self._state_lock:
            resolved_workspace_tab_id = self._resolve_workspace_tab_id(workspace_tab_id)
            running_job = self._get_running_job_locked(workspace_tab_id=resolved_workspace_tab_id)
            queue_state = self._get_or_create_queue_state(resolved_workspace_tab_id)
            self._queue_states[resolved_workspace_tab_id] = replace(
                queue_state,
                status=QueueStatus.STOPPED,
                running_job_id=running_job.job_id if running_job is not None else None,
                last_stop_reason=reason,
            )

            if running_job is not None:
                handle_to_cancel = self._request_running_job_cancel_locked(running_job.job_id)

            result = self._queue_states[resolved_workspace_tab_id]

        if handle_to_cancel is not None:
            self._cancel_handle(running_job.job_id, handle_to_cancel)

        return result

    def stop_all_queues(
        self,
        *,
        reason: QueueStopReason | str = QueueStopReason.USER_STOPPED,
        when: datetime | None = None,
    ) -> tuple[WorkspaceQueueState, ...]:
        """Stop every workspace queue and request cancellation of running jobs."""
        handles_to_cancel: list[tuple[JobId, ExecutionHandle]] = []
        with self._state_lock:
            workspace_tab_ids = set(self._queue_states)
            running_jobs = tuple(
                job for job in self._list_jobs_locked() if job.status == JobStatus.RUNNING
            )
            workspace_tab_ids.update(job.workspace_tab_id for job in running_jobs)
            running_job_by_workspace = {job.workspace_tab_id: job for job in running_jobs}

            for workspace_tab_id in workspace_tab_ids:
                running_job = running_job_by_workspace.get(workspace_tab_id)
                queue_state = self._get_or_create_queue_state(workspace_tab_id)
                self._queue_states[workspace_tab_id] = replace(
                    queue_state,
                    status=QueueStatus.STOPPED,
                    running_job_id=running_job.job_id if running_job is not None else None,
                    last_stop_reason=reason,
                )

            for running_job in running_jobs:
                handle = self._request_running_job_cancel_locked(running_job.job_id)
                if handle is not None:
                    handles_to_cancel.append((running_job.job_id, handle))

            result = tuple(
                self._queue_states[workspace_tab_id]
                for workspace_tab_id in sorted(self._queue_states)
            )

        for job_id, handle in handles_to_cancel:
            self._cancel_handle(job_id, handle)

        return result

    def requeue_waiting_job(
        self,
        job_id: JobId,
    ) -> Job:
        """Move one waiting-for-configuration job back into the runnable queue."""
        with self._state_lock:
            job = self._get_job_locked(job_id)
            updated = self._transition_job(
                job,
                JobStatus.QUEUED,
                queue_order=self._issue_queue_order(),
                configuration_wait_reason=None,
                user_message=build_retry_queued_message(),
                completed_at=None,
            )
            self._jobs[job_id] = updated
            self._rebalance_workspace_queue_order_locked(updated.workspace_tab_id)
            updated = self._jobs[job_id]
            should_dispatch = (
                self._workspace_queue_is_started(updated.workspace_tab_id)
                and self._get_workspace_running_job_locked(updated.workspace_tab_id) is None
                and self._request_dispatch_locked(workspace_tab_id=updated.workspace_tab_id)
            )

        if should_dispatch:
            self._dispatch_next_job()

        with self._state_lock:
            return self._jobs[job_id]

    def complete_running_job(
        self,
        job_id: JobId,
        *,
        when: datetime | None = None,
        process_metadata: ProcessMetadata | None = None,
        user_message: str | None = None,
    ) -> Job:
        """Complete the current running job and schedule the next follow-up job."""
        return self._finish_running_job(
            job_id,
            final_status=JobStatus.COMPLETED,
            when=when,
            process_metadata=process_metadata,
            user_message=user_message,
        )

    def fail_running_job(
        self,
        job_id: JobId,
        *,
        when: datetime | None = None,
        process_metadata: ProcessMetadata | None = None,
        user_message: str | None = None,
    ) -> Job:
        """Fail the current running job and continue queue processing when started."""
        return self._finish_running_job(
            job_id,
            final_status=JobStatus.FAILED,
            when=when,
            process_metadata=process_metadata,
            user_message=user_message,
        )

    def cancel_running_job(
        self,
        job_id: JobId,
        *,
        when: datetime | None = None,
        process_metadata: ProcessMetadata | None = None,
        cancel_execution: bool = True,
        user_message: str | None = None,
    ) -> Job:
        """Request cancellation or finalize a canceled result for one running job."""
        handle_to_cancel: ExecutionHandle | None = None
        with self._state_lock:
            job = self._get_job_locked(job_id)
            if job.status != JobStatus.RUNNING:
                raise ValueError(f"Job is not running: {job_id}")

            if cancel_execution:
                handle_to_cancel = self._request_running_job_cancel_locked(job_id)

        if cancel_execution:
            if handle_to_cancel is not None:
                self._cancel_handle(job_id, handle_to_cancel)
            return job

        return self._finish_running_job(
            job_id,
            final_status=JobStatus.CANCELED,
            when=when,
            process_metadata=process_metadata,
            user_message=user_message,
        )

    def get_job(self, job_id: JobId) -> Job:
        """Return one job by id."""
        with self._state_lock:
            return self._get_job_locked(job_id)

    def _get_job_locked(self, job_id: JobId) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"Unknown job id: {job_id}") from exc

    def get_running_job(
        self,
        workspace_tab_id: WorkspaceTabId | None = None,
    ) -> Job | None:
        """Return the currently running job, optionally filtered by workspace."""
        with self._state_lock:
            return self._get_running_job_locked(workspace_tab_id=workspace_tab_id)

    def _get_running_job_locked(
        self,
        workspace_tab_id: WorkspaceTabId | None = None,
    ) -> Job | None:
        if workspace_tab_id is not None:
            return self._get_workspace_running_job_locked(workspace_tab_id)

        running_jobs = [
            job for job in self._list_jobs_locked() if job.status == JobStatus.RUNNING
        ]
        if not running_jobs:
            return None
        return running_jobs[0]

    def delete_job(self, job_id: JobId) -> Job:
        """Delete one non-running runtime job from the scheduler."""
        with self._state_lock:
            job = self._get_job_locked(job_id)
            if job.status == JobStatus.RUNNING:
                raise ValueError(f"Cannot delete a running job: {job_id}")

            deleted = self._remove_job_locked(job_id)
            self._rebalance_workspace_queue_order_locked(job.workspace_tab_id)
            return deleted

    def remove_queued_jobs_for_session(self, session_tab_id: SessionTabId) -> tuple[Job, ...]:
        """Remove pending jobs that belong to one session tab."""
        with self._state_lock:
            removed_jobs = tuple(
                job
                for job in self._list_jobs_locked()
                if job.session_tab_id == session_tab_id
                and job.status in _PENDING_JOB_STATUSES
            )
            for job in removed_jobs:
                self._remove_job_locked(job.job_id)
            for workspace_tab_id in {job.workspace_tab_id for job in removed_jobs}:
                self._rebalance_workspace_queue_order_locked(workspace_tab_id)
            return removed_jobs

    def remove_queued_jobs_for_workspace(self, workspace_tab_id: WorkspaceTabId) -> tuple[Job, ...]:
        """Remove pending jobs that belong to one workspace tab."""
        with self._state_lock:
            removed_jobs = tuple(
                job
                for job in self._list_jobs_locked()
                if job.workspace_tab_id == workspace_tab_id
                and job.status in _PENDING_JOB_STATUSES
            )
            for job in removed_jobs:
                self._remove_job_locked(job.job_id)
            self._rebalance_workspace_queue_order_locked(workspace_tab_id)
            return removed_jobs

    def list_jobs(self) -> tuple[Job, ...]:
        """Return all runtime jobs in queue order."""
        with self._state_lock:
            return self._list_jobs_locked()

    def snapshot_jobs_by_id(self) -> dict[JobId, Job]:
        """Return all runtime jobs keyed by id without queue-order sorting."""
        with self._state_lock:
            return dict(self._jobs)

    def has_running_job(self) -> bool:
        """Return whether any runtime job is running without queue-order sorting."""
        with self._state_lock:
            return any(job.status == JobStatus.RUNNING for job in self._jobs.values())

    def has_started_queue(self) -> bool:
        """Return whether any open workspace queue is started without sorting queues."""
        with self._state_lock:
            return any(
                queue_state.status == QueueStatus.STARTED
                and self._workspace_tab_is_open(workspace_tab_id)
                for workspace_tab_id, queue_state in self._queue_states.items()
            )

    def list_workspace_jobs(self, workspace_tab_id: WorkspaceTabId) -> tuple[Job, ...]:
        """Return runtime jobs for one workspace in queue order."""
        with self._state_lock:
            return self._list_jobs_locked(workspace_tab_id=workspace_tab_id)

    def list_jobs_by_workspace(
        self,
        workspace_tab_ids: Iterable[WorkspaceTabId],
    ) -> dict[WorkspaceTabId, tuple[Job, ...]]:
        """Return runtime jobs grouped by requested workspace in queue order."""
        requested_workspace_tab_ids = tuple(dict.fromkeys(workspace_tab_ids))
        if not requested_workspace_tab_ids:
            return {}

        with self._state_lock:
            return self._list_jobs_by_workspace_locked(requested_workspace_tab_ids)

    def summarize_workspace_jobs(
        self,
        workspace_tab_ids: Iterable[WorkspaceTabId],
    ) -> dict[WorkspaceTabId, WorkspaceJobSummary]:
        """Return lightweight job summaries for requested workspaces without sorting."""
        requested_workspace_tab_ids = tuple(dict.fromkeys(workspace_tab_ids))
        if not requested_workspace_tab_ids:
            return {}

        with self._state_lock:
            return self._summarize_workspace_jobs_locked(requested_workspace_tab_ids)

    def workspace_has_jobs(self, workspace_tab_id: WorkspaceTabId) -> bool:
        """Return whether one workspace has any jobs without sorting."""
        with self._state_lock:
            return any(
                job.workspace_tab_id == workspace_tab_id
                for job in self._jobs.values()
            )

    def workspace_has_runnable_jobs(self, workspace_tab_id: WorkspaceTabId) -> bool:
        """Return whether one workspace has queued jobs that can be dispatched."""
        with self._state_lock:
            return self._workspace_has_runnable_job_locked(workspace_tab_id)

    def list_session_jobs(self, session_tab_id: SessionTabId) -> tuple[Job, ...]:
        """Return runtime jobs for one session in queue order."""
        with self._state_lock:
            return self._list_jobs_locked(session_tab_id=session_tab_id)

    def _list_jobs_locked(
        self,
        *,
        workspace_tab_id: WorkspaceTabId | None = None,
        session_tab_id: SessionTabId | None = None,
    ) -> tuple[Job, ...]:
        jobs: Iterable[Job] = self._jobs.values()
        if workspace_tab_id is not None:
            jobs = (job for job in jobs if job.workspace_tab_id == workspace_tab_id)
        if session_tab_id is not None:
            jobs = (job for job in jobs if job.session_tab_id == session_tab_id)
        return tuple(sorted(jobs, key=_job_list_order_key))

    def _list_jobs_by_workspace_locked(
        self,
        workspace_tab_ids: Iterable[WorkspaceTabId],
    ) -> dict[WorkspaceTabId, tuple[Job, ...]]:
        grouped_jobs: dict[WorkspaceTabId, list[Job]] = {
            workspace_tab_id: [] for workspace_tab_id in workspace_tab_ids
        }
        if not grouped_jobs:
            return {}

        workspace_tab_id_set = set(grouped_jobs)
        for job in self._jobs.values():
            if job.workspace_tab_id in workspace_tab_id_set:
                grouped_jobs[job.workspace_tab_id].append(job)

        return {
            workspace_tab_id: tuple(sorted(workspace_jobs, key=_job_list_order_key))
            for workspace_tab_id, workspace_jobs in grouped_jobs.items()
        }

    def _summarize_workspace_jobs_locked(
        self,
        workspace_tab_ids: Iterable[WorkspaceTabId],
    ) -> dict[WorkspaceTabId, WorkspaceJobSummary]:
        requested_workspace_tab_ids = tuple(workspace_tab_ids)
        if not requested_workspace_tab_ids:
            return {}

        workspace_tab_id_set = set(requested_workspace_tab_ids)
        workspace_ids_with_jobs: set[WorkspaceTabId] = set()
        workspace_ids_with_runnable_jobs: set[WorkspaceTabId] = set()
        workspace_ids_with_running_jobs: set[WorkspaceTabId] = set()
        for job in self._jobs.values():
            workspace_tab_id = job.workspace_tab_id
            if workspace_tab_id not in workspace_tab_id_set:
                continue

            workspace_ids_with_jobs.add(workspace_tab_id)
            if job.status == JobStatus.QUEUED:
                workspace_ids_with_runnable_jobs.add(workspace_tab_id)
            if job.status == JobStatus.RUNNING:
                workspace_ids_with_running_jobs.add(workspace_tab_id)
            if (
                len(workspace_ids_with_jobs) == len(workspace_tab_id_set)
                and len(workspace_ids_with_runnable_jobs) == len(workspace_tab_id_set)
                and len(workspace_ids_with_running_jobs) == len(workspace_tab_id_set)
            ):
                break

        return {
            workspace_tab_id: WorkspaceJobSummary(
                has_jobs=workspace_tab_id in workspace_ids_with_jobs,
                has_runnable_jobs=workspace_tab_id in workspace_ids_with_runnable_jobs,
                has_running_job=workspace_tab_id in workspace_ids_with_running_jobs,
            )
            for workspace_tab_id in requested_workspace_tab_ids
        }

    def _rebalance_workspace_queue_order_locked(
        self,
        workspace_tab_id: WorkspaceTabId,
    ) -> None:
        workspace_jobs = self._list_jobs_locked(workspace_tab_id=workspace_tab_id)
        if not workspace_jobs:
            return

        ordered_pending_jobs = order_pending_jobs_by_queue_order(workspace_jobs)
        pending_job_ids = {job.job_id for job in ordered_pending_jobs}
        pending_job_iterator = iter(ordered_pending_jobs)
        ordered_jobs = tuple(
            next(pending_job_iterator)
            if job.job_id in pending_job_ids
            else job
            for job in workspace_jobs
        )

        for queue_order, job in enumerate(ordered_jobs, start=1):
            if job.queue_order == queue_order:
                continue
            self._jobs[job.job_id] = replace(job, queue_order=queue_order)

    def _prioritize_workspace_pending_jobs_locked(
        self,
        workspace_tab_id: WorkspaceTabId,
        *,
        priority_job_ids: Sequence[JobId],
    ) -> None:
        workspace_jobs = tuple(
            job
            for job in self._list_jobs_locked()
            if job.workspace_tab_id == workspace_tab_id
        )
        if not workspace_jobs:
            return

        priority_job_id_set = set(priority_job_ids)
        pending_jobs = [
            job for job in workspace_jobs if job.status in _PENDING_JOB_STATUSES
        ]
        prioritized_jobs = [self._jobs[job_id] for job_id in priority_job_ids]
        remaining_pending_jobs = [
            job for job in pending_jobs if job.job_id not in priority_job_id_set
        ]
        ordered_pending_jobs = tuple(prioritized_jobs + remaining_pending_jobs)
        ordered_pending_job_ids = {job.job_id for job in ordered_pending_jobs}
        pending_job_iterator = iter(ordered_pending_jobs)
        ordered_jobs = tuple(
            next(pending_job_iterator)
            if job.job_id in ordered_pending_job_ids
            else job
            for job in workspace_jobs
        )

        for queue_order, job in enumerate(ordered_jobs, start=1):
            if job.queue_order == queue_order:
                continue
            self._jobs[job.job_id] = replace(job, queue_order=queue_order)

    def _remove_job_locked(self, job_id: JobId) -> Job:
        deleted = self._jobs.pop(job_id)
        self._pending_cancel_job_ids.discard(job_id)
        for workspace_tab_id, previous_job_id in tuple(
            self._pending_dispatch_previous_job_ids.items()
        ):
            if previous_job_id == job_id:
                self._pending_dispatch_previous_job_ids.pop(workspace_tab_id, None)
        self._pending_dispatch_requested = bool(
            self._pending_dispatch_workspace_ids
            or self._pending_dispatch_previous_job_ids
        )
        return deleted

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

    def _dispatch_or_defer(self, previous_job: Job | None = None) -> Job | None:
        with self._state_lock:
            should_dispatch = self._request_dispatch_locked(previous_job=previous_job)

        if not should_dispatch:
            return None
        return self._dispatch_next_job(previous_job=previous_job)

    def _request_dispatch_locked(
        self,
        previous_job: Job | None = None,
        *,
        workspace_tab_id: WorkspaceTabId | None = None,
        workspace_tab_ids: Iterable[WorkspaceTabId] = (),
    ) -> bool:
        if self._dispatch_defer_depth > 0:
            self._pending_dispatch_requested = True
            if previous_job is not None:
                self._pending_dispatch_previous_job_ids[previous_job.workspace_tab_id] = (
                    previous_job.job_id
                )
                self._pending_dispatch_workspace_ids.add(previous_job.workspace_tab_id)
            if workspace_tab_id is not None:
                self._pending_dispatch_workspace_ids.add(workspace_tab_id)
            self._pending_dispatch_workspace_ids.update(workspace_tab_ids)
            return False
        return True

    def _take_pending_dispatch_previous_jobs_locked(
        self,
        *,
        excluded_workspace_tab_ids: set[WorkspaceTabId] | None = None,
    ) -> dict[WorkspaceTabId, Job]:
        excluded_workspace_ids = excluded_workspace_tab_ids or set()
        previous_job_ids = {
            workspace_tab_id: previous_job_id
            for workspace_tab_id, previous_job_id in self._pending_dispatch_previous_job_ids.items()
            if workspace_tab_id not in excluded_workspace_ids
        }
        self._pending_dispatch_previous_job_ids = {
            workspace_tab_id: previous_job_id
            for workspace_tab_id, previous_job_id in self._pending_dispatch_previous_job_ids.items()
            if workspace_tab_id in excluded_workspace_ids
        }
        self._pending_dispatch_workspace_ids = {
            workspace_tab_id
            for workspace_tab_id in self._pending_dispatch_workspace_ids
            if workspace_tab_id in excluded_workspace_ids
        }
        self._pending_dispatch_requested = bool(
            self._pending_dispatch_workspace_ids
            or self._pending_dispatch_previous_job_ids
        )

        previous_jobs: dict[WorkspaceTabId, Job] = {}
        for workspace_tab_id, previous_job_id in previous_job_ids.items():
            try:
                previous_jobs[workspace_tab_id] = self._get_job_locked(previous_job_id)
            except KeyError:
                continue
        return previous_jobs

    def _dispatch_next_job(
        self,
        previous_job: Job | None = None,
        previous_jobs: dict[WorkspaceTabId, Job] | None = None,
        excluded_workspace_tab_ids: Iterable[WorkspaceTabId] = (),
    ) -> Job | None:
        previous_jobs_by_workspace = dict(previous_jobs or {})
        if previous_job is not None:
            previous_jobs_by_workspace[previous_job.workspace_tab_id] = previous_job
        excluded_workspace_ids = set(excluded_workspace_tab_ids)

        first_started_job: Job | None = None
        dispatch_selection_state: _DispatchSelectionState | None = None
        while True:
            with self._state_lock:
                if self._dispatch_defer_depth > 0:
                    self._request_dispatch_locked(previous_job=previous_job)
                    return first_started_job
                if not self._has_started_queue():
                    return first_started_job

                if dispatch_selection_state is None:
                    dispatch_selection_state = self._build_dispatch_selection_state_locked(
                        previous_jobs_by_workspace,
                        excluded_workspace_tab_ids=excluded_workspace_ids,
                    )
                next_job = self._select_next_dispatchable_job_locked(
                    dispatch_selection_state
                )
                if next_job is None:
                    return first_started_job

                request = self._build_execution_request(next_job)
                applied_execution_metadata = self._metadata_from_request(request)

            try:
                wait_reason = self._executor.validate(request)
            except Exception:
                LOGGER.exception("Job validation failed. job_id=%s", next_job.job_id)
                with self._state_lock:
                    current_job = self._get_dispatchable_job(next_job.job_id)
                    if current_job is None:
                        dispatch_selection_state = None
                        continue
                    next_job = current_job
                    waiting_job = self._transition_job(
                        next_job,
                        JobStatus.WAITING_FOR_CONFIGURATION,
                        applied_execution_metadata=None,
                        completed_at=None,
                        configuration_wait_reason="실행 준비 오류",
                        user_message=build_internal_validation_failure_message(),
                    )
                    self._jobs[next_job.job_id] = waiting_job
                    if not self._replace_dispatch_selection_job_locked(
                        dispatch_selection_state,
                        waiting_job,
                        previous_jobs_by_workspace,
                    ):
                        dispatch_selection_state = None
                continue

            with self._state_lock:
                current_job = self._get_dispatchable_job(next_job.job_id)
                if current_job is None:
                    dispatch_selection_state = None
                    continue
                current_request = self._build_execution_request(current_job)
                if current_request != request:
                    dispatch_selection_state = None
                    continue
                next_job = current_job

                if wait_reason is not None:
                    waiting_job = self._transition_job(
                        next_job,
                        JobStatus.WAITING_FOR_CONFIGURATION,
                        applied_execution_metadata=None,
                        configuration_wait_reason=wait_reason,
                        user_message=wait_reason,
                        completed_at=None,
                    )
                    self._jobs[next_job.job_id] = waiting_job
                    if not self._replace_dispatch_selection_job_locked(
                        dispatch_selection_state,
                        waiting_job,
                        previous_jobs_by_workspace,
                    ):
                        dispatch_selection_state = None
                    continue

                started_at = utc_now()
                running_job = self._transition_job(
                    next_job,
                    JobStatus.RUNNING,
                    applied_execution_metadata=applied_execution_metadata,
                    configuration_wait_reason=None,
                    user_message=build_job_status_message(JobStatus.RUNNING),
                    started_at=started_at,
                    completed_at=None,
                )
                self._jobs[next_job.job_id] = running_job
                queue_state = self._get_or_create_queue_state(next_job.workspace_tab_id)
                self._queue_states[next_job.workspace_tab_id] = replace(
                    queue_state,
                    running_job_id=next_job.job_id,
                )
                self._discard_dispatch_selection_workspace(
                    dispatch_selection_state,
                    next_job.workspace_tab_id,
                )

            try:
                handle = self._executor.launch(request)
            except Exception:
                LOGGER.exception("Job launch failed. job_id=%s", next_job.job_id)
                with self._state_lock:
                    current_running_job = self._jobs.get(next_job.job_id)
                    if current_running_job is not None and current_running_job.status == JobStatus.RUNNING:
                        self._jobs[next_job.job_id] = self._transition_job(
                            current_running_job,
                            JobStatus.FAILED,
                            applied_execution_metadata=applied_execution_metadata,
                            completed_at=utc_now(),
                            configuration_wait_reason=None,
                            user_message=build_launch_failure_message(),
                        )
                    self._pending_cancel_job_ids.discard(next_job.job_id)
                    self._clear_workspace_running_job(next_job.workspace_tab_id, next_job.job_id)
                    self._stop_workspace_queue_if_finished_locked(next_job.workspace_tab_id)
                    dispatch_selection_state = None
                continue

            with self._state_lock:
                current_running_job = self._jobs.get(next_job.job_id)
                if (
                    current_running_job is None
                    or current_running_job.status != JobStatus.RUNNING
                    or not self._workspace_has_running_job_locked(
                        next_job.workspace_tab_id,
                        next_job.job_id,
                    )
                ):
                    cancel_after_launch = True
                    dispatch_selection_state = None
                else:
                    self._running_handles[next_job.job_id] = handle
                    cancel_after_launch = next_job.job_id in self._pending_cancel_job_ids
                    self._pending_cancel_job_ids.discard(next_job.job_id)

            if cancel_after_launch:
                self._cancel_handle(next_job.job_id, handle)
                continue
            if first_started_job is None:
                first_started_job = running_job

    def _select_next_dispatchable_job_locked(
        self,
        dispatch_selection_state: _DispatchSelectionState,
    ) -> Job | None:
        if not dispatch_selection_state.next_jobs_by_workspace:
            return None
        return min(
            dispatch_selection_state.next_jobs_by_workspace.values(),
            key=_job_dispatch_priority_key,
        )

    def _build_dispatch_selection_state_locked(
        self,
        previous_jobs_by_workspace: dict[WorkspaceTabId, Job],
        *,
        excluded_workspace_tab_ids: set[WorkspaceTabId] | None = None,
    ) -> _DispatchSelectionState:
        excluded_workspace_ids = excluded_workspace_tab_ids or set()
        workspace_tab_ids = [
            workspace_tab_id
            for workspace_tab_id in self._started_workspace_ids_with_open_slots_locked()
            if workspace_tab_id not in excluded_workspace_ids
        ]
        jobs_by_workspace: dict[WorkspaceTabId, list[Job]] = {
            workspace_tab_id: [] for workspace_tab_id in workspace_tab_ids
        }
        if workspace_tab_ids:
            workspace_tab_id_set = set(workspace_tab_ids)
            session_open_by_id: dict[SessionTabId, bool] = {}
            for job in self._jobs.values():
                if job.workspace_tab_id not in workspace_tab_id_set:
                    continue

                session_is_open = session_open_by_id.get(job.session_tab_id)
                if session_is_open is None:
                    session_is_open = self._session_tab_is_open(job.session_tab_id)
                    session_open_by_id[job.session_tab_id] = session_is_open
                if session_is_open:
                    jobs_by_workspace[job.workspace_tab_id].append(job)

        dispatch_selection_state = _DispatchSelectionState(
            jobs_by_workspace=jobs_by_workspace,
            next_jobs_by_workspace={},
        )
        for workspace_tab_id in workspace_tab_ids:
            self._refresh_dispatch_selection_candidate_locked(
                dispatch_selection_state,
                workspace_tab_id,
                previous_jobs_by_workspace,
            )
        return dispatch_selection_state

    def _refresh_dispatch_selection_candidate_locked(
        self,
        dispatch_selection_state: _DispatchSelectionState,
        workspace_tab_id: WorkspaceTabId,
        previous_jobs_by_workspace: dict[WorkspaceTabId, Job],
    ) -> None:
        workspace_jobs = dispatch_selection_state.jobs_by_workspace.get(workspace_tab_id)
        if workspace_jobs is None:
            dispatch_selection_state.next_jobs_by_workspace.pop(workspace_tab_id, None)
            return

        next_job = select_next_runnable_job(
            workspace_jobs,
            previous_job=previous_jobs_by_workspace.get(workspace_tab_id),
        )
        if next_job is None:
            dispatch_selection_state.next_jobs_by_workspace.pop(workspace_tab_id, None)
            return
        dispatch_selection_state.next_jobs_by_workspace[workspace_tab_id] = next_job

    def _replace_dispatch_selection_job_locked(
        self,
        dispatch_selection_state: _DispatchSelectionState | None,
        updated_job: Job,
        previous_jobs_by_workspace: dict[WorkspaceTabId, Job],
    ) -> bool:
        if dispatch_selection_state is None:
            return True

        workspace_jobs = dispatch_selection_state.jobs_by_workspace.get(
            updated_job.workspace_tab_id
        )
        if workspace_jobs is None:
            return False

        for index, job in enumerate(workspace_jobs):
            if job.job_id == updated_job.job_id:
                workspace_jobs[index] = updated_job
                self._refresh_dispatch_selection_candidate_locked(
                    dispatch_selection_state,
                    updated_job.workspace_tab_id,
                    previous_jobs_by_workspace,
                )
                return True
        return False

    @staticmethod
    def _discard_dispatch_selection_workspace(
        dispatch_selection_state: _DispatchSelectionState | None,
        workspace_tab_id: WorkspaceTabId,
    ) -> None:
        if dispatch_selection_state is None:
            return

        dispatch_selection_state.jobs_by_workspace.pop(workspace_tab_id, None)
        dispatch_selection_state.next_jobs_by_workspace.pop(workspace_tab_id, None)

    def _started_workspace_ids_with_open_slots_locked(self) -> tuple[WorkspaceTabId, ...]:
        return tuple(
            workspace_tab_id
            for workspace_tab_id in sorted(self._queue_states)
            if self._workspace_queue_is_started(workspace_tab_id)
            and self._get_workspace_running_job_locked(workspace_tab_id) is None
        )

    def _get_dispatchable_job(self, job_id: JobId) -> Job | None:
        try:
            job = self.get_job(job_id)
        except KeyError:
            return None
        if job.status != JobStatus.QUEUED:
            return None
        if not self._workspace_queue_is_started(job.workspace_tab_id):
            return None
        if not self._session_tab_is_open(job.session_tab_id):
            return None
        if self._get_workspace_running_job_locked(job.workspace_tab_id) is not None:
            return None
        return job

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
        return any(
            self._workspace_queue_is_started(workspace_tab_id)
            for workspace_tab_id in self._queue_states
        )

    def _workspace_queue_is_started(self, workspace_tab_id: WorkspaceTabId) -> bool:
        queue_state = self._queue_states.get(workspace_tab_id)
        if queue_state is None or queue_state.status != QueueStatus.STARTED:
            return False

        return self._workspace_tab_is_open(workspace_tab_id)

    def _workspace_has_runnable_job_locked(self, workspace_tab_id: WorkspaceTabId) -> bool:
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
        queue_state = self._queue_states.get(workspace_tab_id)
        if queue_state is None or queue_state.running_job_id != job_id:
            return
        self._queue_states[workspace_tab_id] = replace(queue_state, running_job_id=None)

    def _stop_workspace_queue_if_finished_locked(self, workspace_tab_id: WorkspaceTabId) -> None:
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


def _job_dispatch_priority_key(job: Job) -> tuple[int, datetime, JobId]:
    queue_order = job.queue_order if job.queue_order is not None else 2**31 - 1
    return (queue_order, job.created_at, job.job_id)


def _job_list_order_key(job: Job) -> tuple[float, datetime, JobId]:
    queue_order = job.queue_order if job.queue_order is not None else float("inf")
    return (queue_order, job.created_at, job.job_id)
