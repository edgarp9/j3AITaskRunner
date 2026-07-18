from __future__ import annotations

from collections.abc import ValuesView
from dataclasses import dataclass, replace
import threading
import time
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from app.controller import LogAppendedEvent
from app.runtime import (
    AppRuntime,
    AUTO_COMMIT_PROMPT,
    PersistenceIssueEvent,
    QueueStartCompletedEvent,
    SettingsRetryCompletedEvent,
    WorkspaceOpenCompletedEvent,
    _RuntimeActionCompletion,
)
from app.scheduler import (
    ExecutionHandle,
    JobExecutionRequest,
    Scheduler,
    WorkspaceJobSummary,
)
from app.session_manager import SessionManager
from app.workspace_manager import WorkspaceManager
from domain.models import (
    AgentExecutionOptions,
    AppSettings,
    Job,
    JobStatus,
    QueueStatus,
    QueueStopReason,
    SavedWorkspace,
    SessionTab,
    SessionTabKind,
    StepExecutionMode,
    TabOpenState,
    WorkspaceQueueState,
)
from infra.repository import PersistenceSaveError

from tests._app_runtime_settings_helpers import *


def _dt(minutes: int) -> datetime:
    base = datetime(2026, 4, 22, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)

class _FakeExecutor:
    def __init__(self) -> None:
        self.blocked_prompts: set[str] = set()
        self.launched_requests: list[JobExecutionRequest] = []
        self.canceled_handles: list[ExecutionHandle] = []

    def validate(self, request: JobExecutionRequest) -> str | None:
        if request.prompt in self.blocked_prompts:
            return "설정 확인 필요"
        return None

    def launch(self, request: JobExecutionRequest) -> ExecutionHandle:
        self.launched_requests.append(request)
        return ExecutionHandle(handle_id=request.job_id)

    def cancel(self, handle: ExecutionHandle) -> None:
        self.canceled_handles.append(handle)

class _CountingJobDict(dict[str, Job]):
    def __init__(self, jobs: dict[str, Job]) -> None:
        super().__init__(jobs)
        self.values_calls = 0

    def values(self) -> ValuesView[Job]:
        self.values_calls += 1
        return super().values()

class _RuntimeControllerStub:
    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        return 0

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

    def has_pending_background_work(self) -> bool:
        return False

class _RuntimeEventsControllerStub:
    def __init__(self, events: tuple[object, ...]) -> None:
        self._events = events

    def drain_ui_events(self) -> tuple[object, ...]:
        events = self._events
        self._events = ()
        return events

class _RuntimeDispatchControllerStub:
    def __init__(self, *, background_events_to_process: int = 0) -> None:
        self.session_manager = _RuntimeSubmitSessionManagerStub()
        self._background_events_to_process = background_events_to_process
        self._pending_dispatch = False
        self.submit_dispatch_immediately_values: list[bool] = []
        self.process_dispatch_immediately_values: list[bool] = []
        self.dispatch_thread_ids: list[int] = []
        self.immediate_thread_ids: list[int] = []
        self.immediate_calls: list[tuple[str, str]] = []
        self.block_dispatch = False
        self.dispatch_started = threading.Event()
        self.release_dispatch = threading.Event()

    def submit_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        dispatch_immediately: bool = True,
        execution_options: AgentExecutionOptions | None = None,
    ) -> _RuntimeJobStub:
        del session_tab_id, prompt, execution_options
        self.submit_dispatch_immediately_values.append(dispatch_immediately)
        self._pending_dispatch = True
        return _RuntimeJobStub(job_id="job-1", status=JobStatus.QUEUED)

    def submit_immediate_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> _RuntimeJobStub:
        del execution_options
        self.immediate_calls.append((session_tab_id, prompt))
        self.immediate_thread_ids.append(threading.get_ident())
        return _RuntimeJobStub(job_id="job-immediate", status=JobStatus.RUNNING)

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        del max_items
        self.process_dispatch_immediately_values.append(dispatch_immediately)
        if self._background_events_to_process <= 0:
            return 0

        self._background_events_to_process -= 1
        self._pending_dispatch = True
        return 1

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

    def has_pending_dispatch(self) -> bool:
        return self._pending_dispatch

    def dispatch_next_job(self, *, excluded_workspace_tab_ids=()) -> None:
        del excluded_workspace_tab_ids
        self.dispatch_thread_ids.append(threading.get_ident())
        self.dispatch_started.set()
        if self.block_dispatch:
            self.release_dispatch.wait(timeout=1.0)
        self._pending_dispatch = False

    def has_pending_background_work(self) -> bool:
        return False

    def stop_all_queues(self) -> None:
        return None

class _RuntimeSubmitSessionManagerStub:
    def lock_session_execution_options(
        self,
        session_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> SessionTab:
        return SessionTab(
            session_tab_id=session_tab_id,
            workspace_tab_id="workspace-1",
            display_name="S1",
            execution_options=execution_options,
            execution_options_locked=True,
        )

@dataclass(slots=True, frozen=True)
class _RuntimeSessionOpenWorkspaceTabStub:
    workspace_path: str

class _RuntimeSessionOpenWorkspaceManagerStub:
    def __init__(self, workspace_paths: dict[str, str] | None = None) -> None:
        self._workspace_paths = workspace_paths or {}

    def get_workspace_tab(self, workspace_tab_id: str) -> _RuntimeSessionOpenWorkspaceTabStub:
        return _RuntimeSessionOpenWorkspaceTabStub(
            workspace_path=self._workspace_paths.get(workspace_tab_id, workspace_tab_id)
        )

class _RuntimeSessionOpenControllerStub:
    def __init__(self, workspace_paths: dict[str, str] | None = None) -> None:
        self.workspace_manager = _RuntimeSessionOpenWorkspaceManagerStub(
            workspace_paths
        )
        self.session_manager = self
        self.open_session_execution_options: list[AgentExecutionOptions | None] = []
        self.open_preset_session_execution_options: list[
            AgentExecutionOptions | None
        ] = []
        self._session_tabs: dict[str, SessionTab] = {}
        self._next_session_number = 1

    def open_session(
        self,
        workspace_tab_id: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> SessionTab:
        self.open_session_execution_options.append(execution_options)
        session_tab = self._new_session_tab(
            workspace_tab_id,
            kind=SessionTabKind.NORMAL,
            execution_options=execution_options,
        )
        self._session_tabs[session_tab.session_tab_id] = session_tab
        return session_tab

    def open_preset_session(
        self,
        workspace_tab_id: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> SessionTab:
        self.open_preset_session_execution_options.append(execution_options)
        session_tab = self._new_session_tab(
            workspace_tab_id,
            kind=SessionTabKind.PRESET,
            execution_options=execution_options,
        )
        self._session_tabs[session_tab.session_tab_id] = session_tab
        return session_tab

    def get_session_tab(self, session_tab_id: str) -> SessionTab:
        return self._session_tabs[session_tab_id]

    def set_session_execution_options(
        self,
        session_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> SessionTab:
        session_tab = self._session_tabs[session_tab_id]
        updated_session_tab = replace(
            session_tab,
            execution_options=execution_options,
        )
        self._session_tabs[session_tab_id] = updated_session_tab
        return updated_session_tab

    def _new_session_tab(
        self,
        workspace_tab_id: str,
        *,
        kind: SessionTabKind,
        execution_options: AgentExecutionOptions | None,
    ) -> SessionTab:
        session_number = self._next_session_number
        self._next_session_number += 1
        return SessionTab(
            session_tab_id=f"session-{session_number}",
            workspace_tab_id=workspace_tab_id,
            display_name=f"S{session_number}",
            kind=kind,
            execution_options=execution_options or AgentExecutionOptions(),
        )

class _RuntimePromptImportWorkspaceManagerStub:
    def get_workspace_tab(self, workspace_tab_id: str) -> object:
        if workspace_tab_id != "workspace-1":
            raise KeyError(workspace_tab_id)
        return object()

class _RuntimePromptImportSessionManagerStub:
    def __init__(self) -> None:
        self.locked_execution_options: list[tuple[str, AgentExecutionOptions]] = []

    def lock_session_execution_options(
        self,
        session_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> SessionTab:
        self.locked_execution_options.append((session_tab_id, execution_options))
        return SessionTab(
            session_tab_id=session_tab_id,
            workspace_tab_id="workspace-1",
            display_name=session_tab_id,
            execution_options=execution_options,
            execution_options_locked=True,
        )

class _RuntimePromptImportControllerStub:
    def __init__(self) -> None:
        self.workspace_manager = _RuntimePromptImportWorkspaceManagerStub()
        self.session_manager = _RuntimePromptImportSessionManagerStub()
        self.open_session_workspace_ids: list[str] = []
        self.open_session_execution_options: list[AgentExecutionOptions | None] = []
        self.submitted_jobs: list[tuple[str, str, bool]] = []
        self.submitted_execution_options: list[AgentExecutionOptions | None] = []
        self._next_session_number = 1
        self._next_job_number = 1

    def open_session(
        self,
        workspace_tab_id: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> SessionTab:
        self.open_session_workspace_ids.append(workspace_tab_id)
        self.open_session_execution_options.append(execution_options)
        session_tab = SessionTab(
            session_tab_id=f"session-{self._next_session_number}",
            workspace_tab_id=workspace_tab_id,
            display_name=f"S{self._next_session_number}",
            execution_options=execution_options or AgentExecutionOptions(),
        )
        self._next_session_number += 1
        return session_tab

    def submit_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        dispatch_immediately: bool = True,
        execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        self.submitted_jobs.append((session_tab_id, prompt, dispatch_immediately))
        self.submitted_execution_options.append(execution_options)
        job = Job(
            job_id=f"job-{self._next_job_number}",
            workspace_tab_id="workspace-1",
            session_tab_id=session_tab_id,
            prompt=prompt,
            status=JobStatus.QUEUED,
        )
        self._next_job_number += 1
        return job

    def submit_jobs(
        self,
        job_requests: tuple[tuple[str, str], ...] | list[tuple[str, str]],
        *,
        dispatch_immediately: bool = True,
        execution_options: AgentExecutionOptions | None = None,
    ) -> tuple[Job, ...]:
        return tuple(
            self.submit_job(
                session_tab_id,
                prompt,
                dispatch_immediately=dispatch_immediately,
                execution_options=execution_options,
            )
            for session_tab_id, prompt in job_requests
        )

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

class _BlockingRuntimeQueueControllerStub:
    def __init__(self, *, release_start: threading.Event) -> None:
        self.workspace_manager = _RuntimeQueueWorkspaceManagerStub()
        self.session_manager = _RuntimeQueueSessionManagerStub()
        self._release_start = release_start
        self.start_queue_started = threading.Event()
        self.started_queue_ids: list[str | None] = []
        self.stopped_queue_ids: list[str | None] = []
        self.closed_workspace_ids: list[str] = []
        self.closed_session_ids: list[str] = []
        self.stop_all_queue_calls = 0

    def start_queue(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        self.started_queue_ids.append(workspace_tab_id)
        self.start_queue_started.set()
        self._release_start.wait(timeout=1.0)
        return WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id or "workspace-1",
            status=QueueStatus.STARTED,
        )

    def stop_queue(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        self.stopped_queue_ids.append(workspace_tab_id)
        return WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id or "workspace-1",
            status=QueueStatus.STOPPED,
        )

    def stop_all_queues(self) -> None:
        self.stop_all_queue_calls += 1

    def close_workspace(self, workspace_tab_id: str) -> WorkspaceQueueState:
        self.closed_workspace_ids.append(workspace_tab_id)
        return self.stop_queue(workspace_tab_id)

    def close_session(self, session_tab_id: str) -> WorkspaceQueueState:
        self.closed_session_ids.append(session_tab_id)
        return self.stop_queue("workspace-1")

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        return 0

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

    def has_pending_background_work(self) -> bool:
        return False

class _RuntimeQueueWorkspaceManagerStub:
    def get_workspace_tab(self, workspace_tab_id: str) -> _RuntimeQueueWorkspaceTabStub:
        return _RuntimeQueueWorkspaceTabStub(workspace_tab_id=workspace_tab_id)

class _RuntimeQueueSessionManagerStub:
    def get_session_tab(self, session_tab_id: str) -> _RuntimeQueueSessionTabStub:
        return _RuntimeQueueSessionTabStub(
            session_tab_id=session_tab_id,
            workspace_tab_id="workspace-1",
        )

class _RuntimeSleepControllerStub:
    def __init__(
        self,
        *,
        jobs: tuple[_RuntimeJobStub, ...] = (),
        stop_on_next_poll: bool = False,
    ) -> None:
        self.scheduler = _RuntimeSleepSchedulerStub(jobs=jobs)
        self._stop_on_next_poll = stop_on_next_poll

    def start_queue(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        return self.scheduler.set_queue_state(
            workspace_tab_id or "workspace-1",
            QueueStatus.STARTED,
        )

    def stop_queue(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        return self.scheduler.set_queue_state(
            workspace_tab_id or "workspace-1",
            QueueStatus.STOPPED,
        )

    def stop_all_queues(self) -> None:
        self.scheduler.stop_all_queues()

    def close_workspace(self, workspace_tab_id: str) -> WorkspaceQueueState:
        return self.scheduler.set_queue_state(workspace_tab_id, QueueStatus.STOPPED)

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        del max_items, dispatch_immediately
        if not self._stop_on_next_poll:
            return 0
        self._stop_on_next_poll = False
        self.scheduler.set_queue_state("workspace-1", QueueStatus.STOPPED)
        return 1

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()

    def has_pending_background_work(self) -> bool:
        return False

class _RuntimeSleepSchedulerStub:
    def __init__(self, *, jobs: tuple[_RuntimeJobStub, ...]) -> None:
        self._jobs = jobs
        self._queue_states: dict[str, WorkspaceQueueState] = {}

    def set_queue_state(
        self,
        workspace_tab_id: str,
        status: QueueStatus,
    ) -> WorkspaceQueueState:
        queue_state = WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id,
            status=status,
        )
        self._queue_states[workspace_tab_id] = queue_state
        return queue_state

    def list_queue_states(self) -> tuple[WorkspaceQueueState, ...]:
        return tuple(self._queue_states[key] for key in sorted(self._queue_states))

    def list_jobs(self) -> tuple[_RuntimeJobStub, ...]:
        return self._jobs

    def stop_all_queues(self) -> None:
        for workspace_tab_id in tuple(self._queue_states):
            self.set_queue_state(workspace_tab_id, QueueStatus.STOPPED)

class _SleepPreventerStub:
    def __init__(self) -> None:
        self.active_values: list[bool] = []

    def set_active(self, active: bool) -> None:
        if self.active_values and self.active_values[-1] == active:
            return
        self.active_values.append(active)

    def release(self) -> None:
        self.set_active(False)

@dataclass(slots=True, frozen=True)
class _RuntimeQueueWorkspaceTabStub:
    workspace_tab_id: str
    display_name: str = "W1"
    open_state: TabOpenState = TabOpenState.OPEN

@dataclass(slots=True, frozen=True)
class _RuntimeQueueSessionTabStub:
    session_tab_id: str
    workspace_tab_id: str

@dataclass
class _RuntimeJobStub:
    job_id: str
    status: JobStatus
    session_tab_id: str = "session-1"
    workspace_tab_id: str = "workspace-1"

class _RuntimeSchedulerStub:
    def __init__(self, jobs: tuple[_RuntimeJobStub, ...]) -> None:
        self._jobs = jobs

    def list_jobs(self) -> tuple[_RuntimeJobStub, ...]:
        return self._jobs

    def has_running_job(self) -> bool:
        return any(job.status == JobStatus.RUNNING for job in self._jobs)

class _RuntimeWorkspaceJobControllerStub:
    def __init__(self, jobs: tuple[Job, ...]) -> None:
        self.workspace_manager = _RuntimeWorkspaceJobWorkspaceManagerStub()
        self.scheduler = _RuntimeWorkspaceJobSchedulerStub(jobs)

class _RuntimeWorkspaceJobWorkspaceManagerStub:
    def __init__(self) -> None:
        self.requested_workspace_tab_ids: list[str] = []

    def get_workspace_tab(self, workspace_tab_id: str) -> object:
        self.requested_workspace_tab_ids.append(workspace_tab_id)
        return object()

class _RuntimeWorkspaceJobSchedulerStub:
    def __init__(self, jobs: tuple[Job, ...]) -> None:
        self._jobs = jobs
        self.list_jobs_calls = 0
        self.list_workspace_jobs_requests: list[str] = []
        self.list_jobs_by_workspace_requests: list[tuple[str, ...]] = []
        self.summarize_workspace_jobs_requests: list[tuple[str, ...]] = []
        self.workspace_has_jobs_requests: list[str] = []
        self.workspace_has_runnable_jobs_requests: list[str] = []

    def list_jobs(self) -> tuple[Job, ...]:
        self.list_jobs_calls += 1
        return self._jobs

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        self.list_workspace_jobs_requests.append(workspace_tab_id)
        return tuple(job for job in self._jobs if job.workspace_tab_id == workspace_tab_id)

    def list_jobs_by_workspace(
        self,
        workspace_tab_ids: tuple[str, ...],
    ) -> dict[str, tuple[Job, ...]]:
        self.list_jobs_by_workspace_requests.append(workspace_tab_ids)
        return {
            workspace_tab_id: tuple(
                job for job in self._jobs if job.workspace_tab_id == workspace_tab_id
            )
            for workspace_tab_id in workspace_tab_ids
        }

    def summarize_workspace_jobs(
        self,
        workspace_tab_ids: tuple[str, ...],
    ) -> dict[str, WorkspaceJobSummary]:
        self.summarize_workspace_jobs_requests.append(workspace_tab_ids)
        return {
            workspace_tab_id: WorkspaceJobSummary(
                has_jobs=any(
                    job.workspace_tab_id == workspace_tab_id for job in self._jobs
                ),
                has_running_job=any(
                    job.workspace_tab_id == workspace_tab_id
                    and job.status == JobStatus.RUNNING
                    for job in self._jobs
                ),
            )
            for workspace_tab_id in workspace_tab_ids
        }

    def workspace_has_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_jobs_requests.append(workspace_tab_id)
        return any(job.workspace_tab_id == workspace_tab_id for job in self._jobs)

    def workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_runnable_jobs_requests.append(workspace_tab_id)
        return any(
            job.workspace_tab_id == workspace_tab_id and job.status == JobStatus.QUEUED
            for job in self._jobs
        )

class _RuntimeWorkspacePathRunningControllerStub:
    def __init__(self, workspace_manager: WorkspaceManager, jobs: tuple[Job, ...]) -> None:
        self.workspace_manager = workspace_manager
        self.scheduler = _RuntimeWorkspaceJobSchedulerStub(jobs)

class _RuntimeDeleteControllerStub:
    def __init__(self, deleted_job: Job) -> None:
        self._deleted_job = deleted_job
        self.deleted_job_ids: list[str] = []

    def delete_job(self, job_id: str) -> Job:
        self.deleted_job_ids.append(job_id)
        return self._deleted_job

    def drain_ui_events(self) -> tuple[object, ...]:
        return ()











def _wait_until(predicate, *, timeout: float = 1.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()

def _drain_runtime_until_retry_completed(
    runtime: AppRuntime,
    events: list[object],
) -> bool:
    runtime.process_background_events()
    events.extend(runtime.drain_events())
    return any(isinstance(event, SettingsRetryCompletedEvent) for event in events)

def _drain_runtime_until_queue_start_completed(
    runtime: AppRuntime,
    events: list[object],
) -> bool:
    runtime.process_background_events()
    events.extend(runtime.drain_events())
    return any(isinstance(event, QueueStartCompletedEvent) for event in events)

def _drain_runtime_until_settings_save_failure(
    runtime: AppRuntime,
    events: list[object],
) -> bool:
    runtime.process_background_events()
    events.extend(runtime.drain_events())
    return any(
        isinstance(event, PersistenceIssueEvent)
        and event.issue.operation == "save_settings"
        for event in events
    )

def _drain_runtime_until_workspace_open_completed(
    runtime: AppRuntime,
    events: list[object],
) -> bool:
    runtime.process_background_events()
    events.extend(runtime.drain_events())
    return any(isinstance(event, WorkspaceOpenCompletedEvent) for event in events)

__all__ = [name for name in globals() if not name.startswith("__")]
