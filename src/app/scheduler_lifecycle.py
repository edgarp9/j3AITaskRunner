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


class SchedulerLifecycleMixin:
    def register_job(
        self,
        session_tab_id: SessionTabId,
        prompt: str,
        *,
        when: datetime | None = None,
        force_fresh_session: bool = False,
        execution_options: AgentExecutionOptions | None = None,
        dispatch: bool = True,
    ) -> Job:
        """Register one queued job that belongs to exactly one session tab."""
        registered_jobs = self.register_jobs(
            ((session_tab_id, prompt),),
            when=when,
            force_fresh_session=force_fresh_session,
            execution_options=execution_options,
            dispatch=dispatch,
        )
        return registered_jobs[0]

    def register_jobs(
        self,
        job_requests: Sequence[tuple[SessionTabId, str]],
        *,
        when: datetime | None = None,
        force_fresh_session: bool = False,
        execution_options: AgentExecutionOptions | None = None,
        dispatch: bool = True,
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

            if not self._shared_queue_mode_locked():
                for workspace_tab_id in workspace_tab_ids:
                    self._rebalance_workspace_queue_order_locked(workspace_tab_id)

            should_dispatch = False
            if dispatch:
                if self._shared_queue_mode_locked():
                    should_dispatch = (
                        self._shared_queue_is_started_locked()
                        and self._get_shared_running_job_locked() is None
                        and self._request_dispatch_locked(
                            workspace_tab_ids=workspace_tab_ids
                        )
                    )
                else:
                    should_dispatch = any(
                        self._workspace_queue_is_started(workspace_tab_id)
                        and self._get_workspace_running_job_locked(workspace_tab_id) is None
                        for workspace_tab_id in workspace_tab_ids
                    ) and self._request_dispatch_locked(workspace_tab_ids=workspace_tab_ids)

        if should_dispatch:
            self._dispatch_next_job()

        with self._state_lock:
            return tuple(self._jobs[job_id] for job_id in registered_job_ids)

    def register_and_start_immediate_job(
        self,
        session_tab_id: SessionTabId,
        prompt: str,
        *,
        when: datetime | None = None,
        execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        """Register one job and start it without occupying the workspace queue slot."""
        job = self.register_job(
            session_tab_id,
            prompt,
            when=when,
            execution_options=execution_options,
            dispatch=False,
        )
        return self.start_job_immediately(job.job_id)

    def start_job_immediately(self, job_id: JobId) -> Job:
        """Start one queued job outside the per-workspace queue slot."""
        while True:
            with self._state_lock:
                next_job = self._get_immediate_startable_job_locked(job_id)
                request = self._build_execution_request(next_job)
                applied_execution_metadata = self._metadata_from_request(request)

            try:
                wait_reason = self._executor.validate(request)
            except Exception:
                LOGGER.exception("Immediate job validation failed. job_id=%s", job_id)
                waiting_job: Job | None = None
                with self._state_lock:
                    current_job = self._jobs.get(job_id)
                    if current_job is not None and current_job.status == JobStatus.QUEUED:
                        waiting_job = self._transition_job(
                            current_job,
                            JobStatus.WAITING_FOR_CONFIGURATION,
                            applied_execution_metadata=None,
                            completed_at=None,
                            configuration_wait_reason="실행 준비 오류",
                            user_message=build_internal_validation_failure_message(),
                        )
                        self._jobs[job_id] = waiting_job
                        should_dispatch = (
                            self._request_dispatch_after_immediate_block_locked(
                                waiting_job
                            )
                        )
                    else:
                        should_dispatch = False
                if should_dispatch and waiting_job is not None:
                    self._dispatch_next_job(previous_job=waiting_job)
                return self.get_job(job_id)

            waiting_job: Job | None = None
            with self._state_lock:
                next_job = self._get_immediate_startable_job_locked(job_id)
                if self._build_execution_request(next_job) != request:
                    continue

                if wait_reason is not None:
                    waiting_job = self._transition_job(
                        next_job,
                        JobStatus.WAITING_FOR_CONFIGURATION,
                        applied_execution_metadata=None,
                        configuration_wait_reason=wait_reason,
                        user_message=wait_reason,
                        completed_at=None,
                    )
                    self._jobs[job_id] = waiting_job
                    should_dispatch = (
                        self._request_dispatch_after_immediate_block_locked(
                            waiting_job
                        )
                    )
                else:
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
                    self._jobs[job_id] = running_job
                    should_dispatch = False
            break

        if should_dispatch and waiting_job is not None:
            self._dispatch_next_job(previous_job=waiting_job)
            return self.get_job(job_id)

        try:
            handle = self._executor.launch(request)
        except Exception:
            LOGGER.exception("Immediate job launch failed. job_id=%s", job_id)
            failed_job: Job | None = None
            with self._state_lock:
                current_running_job = self._jobs.get(job_id)
                if (
                    current_running_job is not None
                    and current_running_job.status == JobStatus.RUNNING
                ):
                    failed_job = self._transition_job(
                        current_running_job,
                        JobStatus.FAILED,
                        applied_execution_metadata=applied_execution_metadata,
                        completed_at=utc_now(),
                        configuration_wait_reason=None,
                        user_message=build_launch_failure_message(),
                    )
                    self._jobs[job_id] = failed_job
                    self._stop_workspace_queue_if_finished_locked(
                        current_running_job.workspace_tab_id
                    )
                    should_dispatch = self._request_dispatch_after_immediate_block_locked(
                        failed_job
                    )
                else:
                    should_dispatch = False
                self._pending_cancel_job_ids.discard(job_id)
            if should_dispatch and failed_job is not None:
                self._dispatch_next_job(previous_job=failed_job)
            return self.get_job(job_id)

        with self._state_lock:
            current_running_job = self._jobs.get(job_id)
            if current_running_job is None or current_running_job.status != JobStatus.RUNNING:
                cancel_after_launch = True
            else:
                self._running_handles[job_id] = handle
                cancel_after_launch = job_id in self._pending_cancel_job_ids
                self._pending_cancel_job_ids.discard(job_id)
                running_job = current_running_job

        if cancel_after_launch:
            self._cancel_handle(job_id, handle)
            return self.get_job(job_id)
        return running_job

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

            if self._shared_queue_mode_locked():
                return tuple(selected_jobs)

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
            if self._shared_queue_mode_locked():
                queue_state = self._shared_queue_state_for_workspace_locked(
                    resolved_workspace_tab_id
                )
                if (
                    queue_state.status != QueueStatus.STARTED
                    and not self._has_shared_runnable_job_locked()
                ):
                    return replace(queue_state, status=QueueStatus.STOPPED)

                self._shared_queue_state = replace(
                    self._shared_queue_state,
                    status=QueueStatus.STARTED,
                    last_stop_reason=None,
                )
                should_dispatch = self._request_dispatch_locked(
                    workspace_tab_ids=self._open_workspace_tab_ids_locked()
                )
            else:
                queue_state = self._get_or_create_queue_state(resolved_workspace_tab_id)
                if (
                    queue_state.status != QueueStatus.STARTED
                    and not self._workspace_has_runnable_job_locked(
                        resolved_workspace_tab_id
                    )
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
            if self._shared_queue_mode_locked():
                return self._shared_queue_state_for_workspace_locked(
                    resolved_workspace_tab_id
                )
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
            if self._shared_queue_mode_locked():
                running_job = self._get_shared_running_job_locked()
                self._shared_queue_state = replace(
                    self._shared_queue_state,
                    status=QueueStatus.STOPPED,
                    running_job_id=(
                        running_job.job_id if running_job is not None else None
                    ),
                    last_stop_reason=reason,
                )
                result = self._shared_queue_state_for_workspace_locked(
                    resolved_workspace_tab_id
                )
            else:
                running_job = self._get_running_job_locked(
                    workspace_tab_id=resolved_workspace_tab_id
                )
                queue_state = self._get_or_create_queue_state(resolved_workspace_tab_id)
                self._queue_states[resolved_workspace_tab_id] = replace(
                    queue_state,
                    status=QueueStatus.STOPPED,
                    running_job_id=running_job.job_id if running_job is not None else None,
                    last_stop_reason=reason,
                )
                result = self._queue_states[resolved_workspace_tab_id]

            if running_job is not None:
                handle_to_cancel = self._request_running_job_cancel_locked(running_job.job_id)

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
            if self._shared_queue_mode_locked():
                running_jobs = tuple(
                    job
                    for job in self._list_jobs_locked()
                    if job.status == JobStatus.RUNNING
                )
                self._shared_queue_state = replace(
                    self._shared_queue_state,
                    status=QueueStatus.STOPPED,
                    running_job_id=None,
                    last_stop_reason=reason,
                )
                for running_job in running_jobs:
                    handle = self._request_running_job_cancel_locked(
                        running_job.job_id
                    )
                    if handle is not None:
                        handles_to_cancel.append((running_job.job_id, handle))
                result = tuple(
                    self._shared_queue_state_for_workspace_locked(
                        workspace_tab.workspace_tab_id
                    )
                    for workspace_tab in self._workspace_manager.list_workspace_tabs(
                        include_closed=True
                    )
                )
                if not result:
                    result = (self._shared_queue_state,)
            else:
                workspace_tab_ids = set(self._queue_states)
                running_jobs_for_cancel = tuple(
                    job for job in self._list_jobs_locked() if job.status == JobStatus.RUNNING
                )
                workspace_tab_ids.update(job.workspace_tab_id for job in running_jobs_for_cancel)
                running_job_by_workspace = {
                    job.workspace_tab_id: job for job in running_jobs_for_cancel
                }

                for workspace_tab_id in workspace_tab_ids:
                    running_job = running_job_by_workspace.get(workspace_tab_id)
                    queue_state = self._get_or_create_queue_state(workspace_tab_id)
                    self._queue_states[workspace_tab_id] = replace(
                        queue_state,
                        status=QueueStatus.STOPPED,
                        running_job_id=running_job.job_id if running_job is not None else None,
                        last_stop_reason=reason,
                    )

                for running_job in running_jobs_for_cancel:
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
            if not self._shared_queue_mode_locked():
                self._rebalance_workspace_queue_order_locked(updated.workspace_tab_id)
            updated = self._jobs[job_id]
            if self._shared_queue_mode_locked():
                should_dispatch = (
                    self._shared_queue_is_started_locked()
                    and self._get_shared_running_job_locked() is None
                    and self._request_dispatch_locked(
                        workspace_tab_id=updated.workspace_tab_id
                    )
                )
            else:
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

