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


class SchedulerQueryMixin:
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
            if not self._shared_queue_mode_locked():
                self._rebalance_workspace_queue_order_locked(job.workspace_tab_id)
            return deleted

    def clear_all_jobs(self) -> int:
        """Remove every non-running runtime job and reset queue control state."""
        with self._state_lock:
            if any(job.status == JobStatus.RUNNING for job in self._jobs.values()):
                raise ValueError("Cannot clear jobs while a job is running.")

            cleared_count = len(self._jobs)
            self._jobs.clear()
            self._running_handles.clear()
            self._pending_cancel_job_ids.clear()
            self._pending_dispatch_workspace_ids.clear()
            self._pending_dispatch_previous_job_ids.clear()
            self._pending_dispatch_requested = False
            self._queue_states = {
                workspace_tab_id: replace(
                    queue_state,
                    status=QueueStatus.STOPPED,
                    running_job_id=None,
                    last_stop_reason=None,
                )
                for workspace_tab_id, queue_state in self._queue_states.items()
            }
            self._shared_queue_state = replace(
                self._shared_queue_state,
                status=QueueStatus.STOPPED,
                running_job_id=None,
                last_stop_reason=None,
            )
            self._next_queue_order = 1
            return cleared_count

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
            if not self._shared_queue_mode_locked():
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
            if not self._shared_queue_mode_locked():
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
            if self._shared_queue_mode_locked():
                return self._has_shared_runnable_job_locked()
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
        shared_has_runnable_jobs = (
            self._has_shared_runnable_job_locked()
            if self._shared_queue_mode_locked()
            else False
        )
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
                has_runnable_jobs=(
                    shared_has_runnable_jobs
                    if self._shared_queue_mode_locked()
                    else workspace_tab_id in workspace_ids_with_runnable_jobs
                ),
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

