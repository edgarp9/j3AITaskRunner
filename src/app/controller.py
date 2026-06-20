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
    QueueStatus,
    QueueStopReason,
    SessionTab,
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


ControllerEvent = (
    JobStatusChangedEvent
    | SessionIdConfirmedEvent
    | LogAppendedEvent
    | CompletedSessionUpdatedEvent
    | JobExecutionResultCapturedEvent
)


class AppController:
    """Coordinate scheduler actions and background execution events for the UI layer."""

    def __init__(
        self,
        *,
        runner: BackgroundExecutionRunner,
        settings_provider: Callable[[], AppSettings],
        workspace_manager: WorkspaceManager | None = None,
        session_manager: SessionManager | None = None,
        worker_event_queue: Queue[ExecutionWorkerEvent] | None = None,
        ui_event_queue: Queue[ControllerEvent] | None = None,
    ) -> None:
        self.workspace_manager = workspace_manager or WorkspaceManager()
        self.session_manager = session_manager or SessionManager(self.workspace_manager)
        self._worker_event_queue = worker_event_queue or Queue()
        self._ui_event_queue = ui_event_queue or Queue()
        self.execution_worker = ExecutionWorker(
            runner=runner,
            event_queue=self._worker_event_queue,
        )
        self.scheduler = Scheduler(
            workspace_manager=self.workspace_manager,
            session_manager=self.session_manager,
            executor=self.execution_worker,
            settings_provider=settings_provider,
        )

    @property
    def event_queue(self) -> Queue[ControllerEvent]:
        """Return the queue that UI code can consume on the main thread."""
        return self._ui_event_queue

    def open_workspace(self, workspace_path: str):
        """Open or activate a workspace tab."""
        return self.workspace_manager.open_workspace(workspace_path)

    def open_session(
        self,
        workspace_tab_id: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ):
        """Open a new session tab."""
        return self.session_manager.open_session(
            workspace_tab_id,
            execution_options=execution_options,
        )

    def open_preset_session(
        self,
        workspace_tab_id: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ):
        """Open a new preset parent session tab."""
        return self.session_manager.open_preset_session(
            workspace_tab_id,
            execution_options=execution_options,
        )

    def submit_job(
        self,
        session_tab_id: SessionTabId,
        prompt: str,
        *,
        dispatch_immediately: bool = True,
        force_fresh_session: bool = False,
        execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        """Register one queued job and emit observed status changes."""
        return self._run_scheduler_action(
            lambda: self.scheduler.register_job(
                session_tab_id,
                prompt,
                force_fresh_session=force_fresh_session,
                execution_options=execution_options,
            ),
            dispatch_immediately=dispatch_immediately,
        )

    def submit_jobs(
        self,
        job_requests: Sequence[tuple[SessionTabId, str]],
        *,
        dispatch_immediately: bool = True,
        execution_options: AgentExecutionOptions | None = None,
    ) -> tuple[Job, ...]:
        """Register queued jobs as one observed scheduler action."""
        requests = tuple(job_requests)
        return self._run_scheduler_action(
            lambda: self.scheduler.register_jobs(
                requests,
                execution_options=execution_options,
            ),
            dispatch_immediately=dispatch_immediately,
        )

    def prioritize_queued_jobs(self, job_ids: Sequence[str]) -> tuple[Job, ...]:
        """Move selected queued jobs before other pending jobs in their workspaces."""
        return self._run_scheduler_action(
            lambda: self.scheduler.prioritize_queued_jobs(job_ids)
        )

    def start_queue(self, workspace_tab_id: str | None = None):
        """Start one workspace queue and emit any immediate job transitions."""
        return self._run_scheduler_action(lambda: self.scheduler.start_queue(workspace_tab_id))

    def stop_queue(
        self,
        workspace_tab_id: str | None = None,
        *,
        reason: QueueStopReason | str = QueueStopReason.USER_STOPPED,
    ):
        """Stop one workspace queue and cancel its running job when present."""
        return self._run_scheduler_action(
            lambda: self.scheduler.stop_queue(workspace_tab_id, reason=reason)
        )

    def stop_all_queues(self) -> None:
        """Stop every workspace queue and cancel running jobs when present."""
        self._run_scheduler_action(self.scheduler.stop_all_queues)

    def retry_waiting_job(self, job_id: str) -> Job:
        """Move one waiting job back into the runnable queue."""
        return self._run_scheduler_action(lambda: self.scheduler.requeue_waiting_job(job_id))

    def delete_job(self, job_id: str) -> Job:
        """Delete one non-running runtime job."""
        return self._run_scheduler_action(lambda: self.scheduler.delete_job(job_id))

    def has_pending_dispatch(self) -> bool:
        """Return whether the scheduler deferred follow-up dispatch work."""
        return self.scheduler.has_pending_dispatch()

    def pending_dispatch_workspace_tab_ids(self) -> tuple[str, ...]:
        """Return workspaces that currently have deferred dispatch work."""
        return self.scheduler.pending_dispatch_workspace_tab_ids()

    def dispatch_next_job(
        self,
        *,
        excluded_workspace_tab_ids: Iterable[str] = (),
    ) -> Job | None:
        """Dispatch the next queued job and emit observed status changes."""
        return self._run_scheduler_action(
            lambda: self.scheduler.dispatch_next_job(
                excluded_workspace_tab_ids=excluded_workspace_tab_ids,
            )
        )

    def cancel_running_job(self, job_id: str | None = None) -> Job | None:
        """Cancel the selected running job and emit the resulting status changes."""
        target_job_id = job_id
        if target_job_id is None:
            running_job = self.scheduler.get_running_job()
            if running_job is None:
                return None
            target_job_id = running_job.job_id

        return self._run_scheduler_action(lambda: self.scheduler.cancel_running_job(target_job_id))

    def close_session(self, session_tab_id: str) -> SessionCloseResult:
        """Close one session tab, cancel its running job, and remove pending jobs."""
        before_jobs = self._snapshot_jobs()
        session_tab = self.session_manager.get_session_tab(session_tab_id)
        workspace_tab_id = session_tab.workspace_tab_id
        running_job = self._get_running_job_for_session(session_tab_id)
        canceled_job: Job | None = None
        queue_stopped = False
        queue_state = self.scheduler.get_queue_state(workspace_tab_id)

        if running_job is not None and running_job.session_tab_id == session_tab_id:
            self.scheduler.stop_queue(
                workspace_tab_id,
                reason=QueueStopReason.RUNNING_TAB_CLOSED,
            )
            canceled_job = self.scheduler.get_job(running_job.job_id)
            queue_stopped = True

        removed_jobs = self.scheduler.remove_queued_jobs_for_session(session_tab_id)
        if (
            not queue_stopped
            and removed_jobs
            and queue_state.status == QueueStatus.STARTED
            and not self._workspace_has_active_queue_jobs(workspace_tab_id)
        ):
            self.scheduler.stop_queue(
                workspace_tab_id,
                reason=QueueStopReason.USER_STOPPED,
            )
            queue_stopped = True

        closed_session = self.session_manager.close_session(session_tab_id)
        self._emit_job_status_changes(before_jobs, self._snapshot_jobs())
        return SessionCloseResult(
            session_tab=closed_session,
            canceled_job=canceled_job,
            removed_queued_job_count=len(removed_jobs),
            queue_stopped=queue_stopped,
        )

    def close_workspace(self, workspace_tab_id: str) -> WorkspaceCloseResult:
        """Close one workspace tab, its open session tabs, and pending jobs."""
        before_jobs = self._snapshot_jobs()
        running_job = self.scheduler.get_running_job(workspace_tab_id=workspace_tab_id)
        canceled_job: Job | None = None
        queue_stopped = False
        queue_state = self.scheduler.get_queue_state(workspace_tab_id)

        if queue_state.status == QueueStatus.STARTED or running_job is not None:
            self.scheduler.stop_queue(
                workspace_tab_id,
                reason=(
                    QueueStopReason.RUNNING_TAB_CLOSED
                    if running_job is not None
                    else QueueStopReason.USER_STOPPED
                ),
            )
            if running_job is not None:
                canceled_job = self.scheduler.get_job(running_job.job_id)
            queue_stopped = True

        removed_jobs = self.scheduler.remove_queued_jobs_for_workspace(workspace_tab_id)
        closed_sessions = self.session_manager.close_sessions_for_workspace(workspace_tab_id)
        closed_workspace = self.workspace_manager.close_workspace(workspace_tab_id)
        self._emit_job_status_changes(before_jobs, self._snapshot_jobs())
        return WorkspaceCloseResult(
            workspace_tab=closed_workspace,
            closed_sessions=closed_sessions,
            canceled_job=canceled_job,
            removed_queued_job_count=len(removed_jobs),
            queue_stopped=queue_stopped,
        )

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        """Drain worker events on the main thread and publish controller events."""
        if not dispatch_immediately:
            with self.scheduler.defer_dispatch():
                return self._process_background_events(max_items=max_items)

        return self._process_background_events(max_items=max_items)

    def _process_background_events(self, *, max_items: int | None = None) -> int:
        """Drain worker events and publish controller events."""
        processed = 0
        while max_items is None or processed < max_items:
            try:
                worker_event = self._worker_event_queue.get_nowait()
            except Empty:
                break

            self._handle_worker_event(worker_event)
            processed += 1
        return processed

    def drain_ui_events(self) -> tuple[ControllerEvent, ...]:
        """Collect UI events accumulated since the last drain."""
        events: list[ControllerEvent] = []
        while True:
            try:
                events.append(self._ui_event_queue.get_nowait())
            except Empty:
                return tuple(events)

    def has_pending_background_work(self) -> bool:
        """Return whether execution cleanup is still running off the UI thread."""
        return self.execution_worker.has_pending_work()

    def _handle_worker_event(self, worker_event: ExecutionWorkerEvent) -> None:
        if isinstance(worker_event, ExecutionLogEvent):
            self._handle_log_event(worker_event)
            return

        if isinstance(worker_event, ExecutionSessionIdEvent):
            self._handle_session_id_event(worker_event)
            return

        if isinstance(worker_event, ExecutionCompletedEvent):
            self._handle_completion_event(worker_event)
            return

        raise TypeError(f"Unsupported worker event: {type(worker_event)!r}")

    def _handle_log_event(self, worker_event: ExecutionLogEvent) -> None:
        job = self.scheduler.get_job(worker_event.job_id)
        self._publish_event(
            LogAppendedEvent(
                job_id=job.job_id,
                workspace_tab_id=job.workspace_tab_id,
                session_tab_id=job.session_tab_id,
                stream_name=worker_event.stream_name,
                line=worker_event.line,
            )
        )

    def _handle_session_id_event(self, worker_event: ExecutionSessionIdEvent) -> None:
        confirmation = confirm_session_id_for_job(
            scheduler=self.scheduler,
            session_manager=self.session_manager,
            job_id=worker_event.job_id,
            session_id=worker_event.session_id,
        )
        if confirmation.assigned_session_id is None:
            return

        self._publish_event(
            SessionIdConfirmedEvent(
                job_id=confirmation.job.job_id,
                session_tab_id=confirmation.job.session_tab_id,
                session_id=confirmation.assigned_session_id,
            )
        )

    def _handle_completion_event(self, worker_event: ExecutionCompletedEvent) -> None:
        before_jobs = self._snapshot_jobs()
        completion = apply_execution_result(
            scheduler=self.scheduler,
            session_manager=self.session_manager,
            workspace_manager=self.workspace_manager,
            job_id=worker_event.job_id,
            result=worker_event.result,
        )
        self._emit_job_status_changes(before_jobs, self._snapshot_jobs())

        if completion.assigned_session_id is not None:
            self._publish_event(
                SessionIdConfirmedEvent(
                    job_id=completion.job.job_id,
                    session_tab_id=completion.job.session_tab_id,
                    session_id=completion.assigned_session_id,
                )
            )

        if completion.ignored:
            return

        self._publish_event(
            JobExecutionResultCapturedEvent(
                job_id=completion.job.job_id,
                workspace_tab_id=completion.job.workspace_tab_id,
                session_tab_id=completion.job.session_tab_id,
                status=_agent_status_from_job_status(
                    completion.job.status,
                    fallback=worker_event.result.status,
                ),
                last_message=worker_event.result.last_message,
            )
        )

        if completion.completed_session is not None:
            self._publish_event(
                CompletedSessionUpdatedEvent(
                    job_id=completion.job.job_id,
                    summary=completion.completed_session,
                )
            )

    def _run_scheduler_action(
        self,
        action: Callable[[], object],
        *,
        dispatch_immediately: bool = True,
    ):
        before_jobs = self._snapshot_jobs()
        if dispatch_immediately:
            result = action()
        else:
            with self.scheduler.defer_dispatch():
                result = action()
        self._emit_job_status_changes(before_jobs, self._snapshot_jobs())
        return result

    def _snapshot_jobs(self) -> dict[str, Job]:
        return self.scheduler.snapshot_jobs_by_id()

    def _get_running_job_for_session(self, session_tab_id: str) -> Job | None:
        for job in self.scheduler.list_jobs():
            if job.session_tab_id == session_tab_id and job.status == JobStatus.RUNNING:
                return job
        return None

    def _workspace_has_active_queue_jobs(self, workspace_tab_id: str) -> bool:
        return any(
            job.status in _ACTIVE_QUEUE_JOB_STATUSES
            for job in self.scheduler.list_workspace_jobs(workspace_tab_id)
        )

    def _emit_job_status_changes(
        self,
        before_jobs: dict[str, Job],
        after_jobs: dict[str, Job],
    ) -> None:
        changed_jobs: list[Job] = []
        for job_id, current_job in after_jobs.items():
            previous_job = before_jobs.get(job_id)
            if previous_job is None:
                changed_jobs.append(current_job)
                continue

            if (
                previous_job.status != current_job.status
                or previous_job.configuration_wait_reason != current_job.configuration_wait_reason
                or previous_job.user_message != current_job.user_message
            ):
                changed_jobs.append(current_job)

        for job in sorted(changed_jobs, key=_job_sort_key):
            previous_status = before_jobs[job.job_id].status if job.job_id in before_jobs else None
            self._sync_session_turn_for_status_change(previous_status, job)
            self._publish_event(
                JobStatusChangedEvent(
                    job_id=job.job_id,
                    workspace_tab_id=job.workspace_tab_id,
                    session_tab_id=job.session_tab_id,
                    previous_status=previous_status,
                    current_status=job.status,
                    configuration_wait_reason=job.configuration_wait_reason,
                    user_message=job.user_message,
                )
            )

    def _publish_event(self, event: ControllerEvent) -> None:
        self._ui_event_queue.put(event)

    def _sync_session_turn_for_status_change(
        self,
        previous_status: JobStatus | None,
        job: Job,
    ) -> None:
        if job.status == JobStatus.RUNNING and previous_status != JobStatus.RUNNING:
            self._record_started_turn(job)
            return

        if (
            job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED)
            and previous_status == JobStatus.RUNNING
        ):
            self._finalize_session_turn(job)

    def _record_started_turn(self, job: Job) -> None:
        if job.started_at is None:
            LOGGER.warning("Running job is missing started_at. job_id=%s", job.job_id)
            return

        try:
            self.session_manager.record_started_turn(
                job.session_tab_id,
                job_id=job.job_id,
                prompt_text=job.prompt,
                started_at=job.started_at,
                last_activity_at=job.started_at,
            )
        except Exception:
            LOGGER.exception("Failed to record started session turn. job_id=%s", job.job_id)

    def _finalize_session_turn(self, job: Job) -> None:
        if job.completed_at is None:
            LOGGER.warning("Finished job is missing completed_at. job_id=%s", job.job_id)
            return

        try:
            self.session_manager.finalize_turn(
                job.session_tab_id,
                job_id=job.job_id,
                completed_at=job.completed_at,
                last_activity_at=job.completed_at,
            )
        except Exception:
            LOGGER.exception("Failed to finalize session turn. job_id=%s", job.job_id)


def _job_sort_key(job: Job) -> tuple[int, float, str]:
    queue_order = job.queue_order if job.queue_order is not None else 2**31 - 1
    return (queue_order, job.created_at.timestamp(), job.job_id)


def _agent_status_from_job_status(
    job_status: JobStatus,
    *,
    fallback: AgentRunStatus,
) -> AgentRunStatus:
    if job_status == JobStatus.COMPLETED:
        return AgentRunStatus.COMPLETED
    if job_status == JobStatus.CANCELED:
        return AgentRunStatus.CANCELED
    if job_status == JobStatus.FAILED:
        return AgentRunStatus.FAILED
    return fallback
