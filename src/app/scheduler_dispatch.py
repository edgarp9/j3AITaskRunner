"""Scheduler dispatch loop and dispatch candidate selection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
import logging

from domain.models import Job, JobId, JobStatus, SessionTabId, WorkspaceTabId, utc_now
from domain.policies import select_next_runnable_job

from .messages import (
    build_internal_validation_failure_message,
    build_job_status_message,
    build_launch_failure_message,
)
from .scheduler_ordering import job_dispatch_priority_key

LOGGER = logging.getLogger("app.scheduler")


@dataclass(slots=True)
class _DispatchSelectionState:
    """Reusable dispatch candidate state for one dispatch loop."""

    jobs_by_workspace: dict[WorkspaceTabId, list[Job]]
    next_jobs_by_workspace: dict[WorkspaceTabId, Job]


class SchedulerDispatchMixin:
    """Dispatch behavior for Scheduler."""

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
                if self._shared_queue_mode_locked():
                    self._shared_queue_state = replace(
                        self._shared_queue_state,
                        running_job_id=next_job.job_id,
                    )
                else:
                    queue_state = self._get_or_create_queue_state(
                        next_job.workspace_tab_id
                    )
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
                    if (
                        current_running_job is not None
                        and current_running_job.status == JobStatus.RUNNING
                    ):
                        self._jobs[next_job.job_id] = self._transition_job(
                            current_running_job,
                            JobStatus.FAILED,
                            applied_execution_metadata=applied_execution_metadata,
                            completed_at=utc_now(),
                            configuration_wait_reason=None,
                            user_message=build_launch_failure_message(),
                        )
                    self._pending_cancel_job_ids.discard(next_job.job_id)
                    self._clear_workspace_running_job(
                        next_job.workspace_tab_id, next_job.job_id
                    )
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
        if (
            self._shared_queue_mode_locked()
            and self._get_shared_running_job_locked() is not None
        ):
            return None
        if not dispatch_selection_state.next_jobs_by_workspace:
            return None
        return min(
            dispatch_selection_state.next_jobs_by_workspace.values(),
            key=job_dispatch_priority_key,
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
            running_session_ids: set[SessionTabId] = set()
            for job in self._jobs.values():
                if job.status == JobStatus.RUNNING:
                    running_session_ids.add(job.session_tab_id)
                if job.workspace_tab_id not in workspace_tab_id_set:
                    continue

                session_is_open = session_open_by_id.get(job.session_tab_id)
                if session_is_open is None:
                    session_is_open = self._session_tab_is_open(job.session_tab_id)
                    session_open_by_id[job.session_tab_id] = session_is_open
                if session_is_open:
                    jobs_by_workspace[job.workspace_tab_id].append(job)
            if running_session_ids:
                for workspace_tab_id, workspace_jobs in jobs_by_workspace.items():
                    jobs_by_workspace[workspace_tab_id] = [
                        job
                        for job in workspace_jobs
                        if (
                            job.status != JobStatus.QUEUED
                            or job.session_tab_id not in running_session_ids
                        )
                    ]

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
        if self._shared_queue_mode_locked():
            if not self._shared_queue_is_started_locked():
                return ()
            if self._get_shared_running_job_locked() is not None:
                return ()
            return self._open_workspace_tab_ids_locked()

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
        if self._shared_queue_mode_locked():
            if not self._shared_queue_is_started_locked():
                return None
            if not self._workspace_tab_is_open(job.workspace_tab_id):
                return None
            if not self._session_tab_is_open(job.session_tab_id):
                return None
            if self._get_shared_running_job_locked() is not None:
                return None
            return job
        if not self._workspace_queue_is_started(job.workspace_tab_id):
            return None
        if not self._session_tab_is_open(job.session_tab_id):
            return None
        if self._get_workspace_running_job_locked(job.workspace_tab_id) is not None:
            return None
        return job

    def _get_immediate_startable_job_locked(self, job_id: JobId) -> Job:
        job = self._get_job_locked(job_id)
        if job.status != JobStatus.QUEUED:
            raise ValueError(f"Immediate job is not queued: {job_id}")

        same_session_running_job = next(
            (
                candidate
                for candidate in self._jobs.values()
                if candidate.job_id != job_id
                and candidate.session_tab_id == job.session_tab_id
                and candidate.status == JobStatus.RUNNING
            ),
            None,
        )
        if same_session_running_job is not None:
            raise ValueError(
                "Immediate job cannot start while the same session is running: "
                f"{job.session_tab_id}"
            )
        return job

    def _request_dispatch_after_immediate_block_locked(self, updated_job: Job) -> bool:
        if not self._has_started_queue():
            return False
        return self._request_dispatch_locked(previous_job=updated_job)
