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


def _dt(minutes: int) -> datetime:
    base = datetime(2026, 4, 22, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)

def _dt(minutes: int) -> datetime:
    base = datetime(2026, 4, 22, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)


class _RuntimeSettingsControllerStub:
    def __init__(self, *, jobs: tuple[_RuntimeJobStub, ...] = ()) -> None:
        from tests._app_runtime_helpers import _RuntimeSchedulerStub

        self.scheduler = _RuntimeSchedulerStub(jobs)
        self.session_manager = _RuntimeSessionManagerStub()
        self.workspace_manager = _RuntimeWorkspaceManagerStub()
        self.retried_job_ids: list[str] = []
        self.cleared_job_count = 0
        self.drain_ui_events_calls = 0

    def process_background_events(
        self,
        *,
        max_items: int | None = None,
        dispatch_immediately: bool = True,
    ) -> int:
        return 0

    def drain_ui_events(self) -> tuple[object, ...]:
        self.drain_ui_events_calls += 1
        return ()

    def has_pending_background_work(self) -> bool:
        return False

    def stop_all_queues(self) -> None:
        return None

    def retry_waiting_job(self, job_id: str) -> None:
        self.retried_job_ids.append(job_id)

    def clear_all_jobs(self) -> int:
        cleared_job_count = len(self.scheduler._jobs)
        self.scheduler._jobs = ()
        self.cleared_job_count += cleared_job_count
        return cleared_job_count

@dataclass(slots=True, frozen=True)
class _RuntimeOpenTabStub:
    open_state: TabOpenState = TabOpenState.OPEN

class _RuntimeSessionManagerStub:
    def get_session_tab(self, session_tab_id: str) -> _RuntimeOpenTabStub:
        return _RuntimeOpenTabStub()

class _RuntimeWorkspaceManagerStub:
    def get_workspace_tab(self, workspace_tab_id: str) -> _RuntimeOpenTabStub:
        return _RuntimeOpenTabStub()

class _RuntimeWorkspaceControllerStub:
    def __init__(self) -> None:
        self.workspace_manager = WorkspaceManager()

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

    def open_workspace(self, workspace_path: str):
        return self.workspace_manager.open_workspace(workspace_path)

class _RuntimeRepositoryStub:
    def load_settings(self) -> AppSettings:
        return AppSettings()

    def save_settings(self, settings: AppSettings) -> None:
        return None

    def load_saved_workspaces(self) -> tuple[object, ...]:
        return ()

    def save_saved_workspaces(self, workspaces: tuple[object, ...]) -> None:
        return None

class _SaveResultStub:
    issue = None

class _RuntimeSettingsRepositoryStub:
    def __init__(
        self,
        *,
        initial_settings: AppSettings | None = None,
        save_error: Exception | None = None,
    ) -> None:
        self._initial_settings = initial_settings or AppSettings()
        self._save_error = save_error
        self.saved_settings: list[AppSettings] = []

    def load_settings(self) -> AppSettings:
        return self._initial_settings

    def save_settings(self, settings: AppSettings) -> None:
        self.saved_settings.append(settings)
        if self._save_error is not None:
            raise self._save_error

    def load_saved_workspaces(self) -> tuple[object, ...]:
        return ()

    def save_saved_workspaces(self, workspaces: tuple[object, ...]) -> None:
        return None

class _RuntimePersistenceRepositoryStub:
    def __init__(
        self,
        *,
        initial_settings: AppSettings | None = None,
        initial_saved_workspaces: tuple[object, ...] = (),
        settings_save_error: Exception | None = None,
        saved_workspaces_save_error: Exception | None = None,
    ) -> None:
        self._initial_settings = initial_settings or AppSettings()
        self._initial_saved_workspaces = initial_saved_workspaces
        self._settings_save_error = settings_save_error
        self._saved_workspaces_save_error = saved_workspaces_save_error
        self.saved_settings: list[AppSettings] = []
        self.saved_settings_thread_ids: list[int] = []
        self.saved_workspaces: list[tuple[object, ...]] = []
        self.saved_workspaces_thread_ids: list[int] = []

    def load_settings(self) -> AppSettings:
        return self._initial_settings

    def save_settings(self, settings: AppSettings) -> None:
        self.saved_settings.append(settings)
        self.saved_settings_thread_ids.append(threading.get_ident())
        if self._settings_save_error is not None:
            raise self._settings_save_error

    def load_saved_workspaces(self) -> tuple[object, ...]:
        return self._initial_saved_workspaces

    def save_saved_workspaces(self, workspaces: tuple[object, ...]) -> None:
        self.saved_workspaces.append(workspaces)
        self.saved_workspaces_thread_ids.append(threading.get_ident())
        if self._saved_workspaces_save_error is not None:
            raise self._saved_workspaces_save_error

class _BlockingRuntimePersistenceRepositoryStub(_RuntimePersistenceRepositoryStub):
    def __init__(self, *, release_settings_save: threading.Event, **kwargs) -> None:
        super().__init__(**kwargs)
        self.settings_save_started = threading.Event()
        self._release_settings_save = release_settings_save

    def save_settings(self, settings: AppSettings) -> None:
        self.settings_save_started.set()
        self._release_settings_save.wait(timeout=1.0)
        super().save_settings(settings)


__all__ = [name for name in globals() if not name.startswith("__")]

