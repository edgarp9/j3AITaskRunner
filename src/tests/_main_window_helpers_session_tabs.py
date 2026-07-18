from __future__ import annotations

from tests._main_window_helpers_core import *


class _WorkspaceOpenRuntimeStub:
    def __init__(self) -> None:
        self.background_open_paths: list[str] = []

    def open_workspace_in_background(self, workspace_path: str) -> None:
        self.background_open_paths.append(workspace_path)

class _WorkspaceOpenWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _WorkspaceOpenRuntimeStub) -> None:
        self._runtime = runtime
        self.status_messages: list[str] = []

    def _request_workspace_open(self, workspace_path: str) -> bool:
        return MainWindow._request_workspace_open(self, workspace_path)

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

class _StartupWorkspaceOpenWindowStub(_WorkspaceOpenWindowStub):
    def __init__(self, runtime: _WorkspaceOpenRuntimeStub) -> None:
        super().__init__(runtime)
        self.after_intervals: list[int] = []
        self.after_callbacks: list[object] = []

    def _open_workspace_path(self, workspace_path: str) -> None:
        MainWindow._open_workspace_path(self, workspace_path)

    def _open_startup_workspace_paths(self, workspace_paths: tuple[str, ...]) -> None:
        MainWindow._open_startup_workspace_paths(self, workspace_paths)

    def after(self, interval_ms: int, callback: object) -> str:
        self.after_intervals.append(interval_ms)
        self.after_callbacks.append(callback)
        return f"after-{len(self.after_intervals)}"

    def run_scheduled_callbacks(self) -> None:
        for callback in self.after_callbacks:
            if not callable(callback):
                raise AssertionError("scheduled callback is not callable")
            callback()

@dataclass(slots=True, frozen=True)
class _DropEvent:
    data: str

class _DropTkStub:
    def __init__(self, split_paths: tuple[str, ...]) -> None:
        self._split_paths = split_paths

    def splitlist(self, _data: str) -> tuple[str, ...]:
        return self._split_paths

class _WorkspaceDropWindowStub(_WorkspaceOpenWindowStub):
    def __init__(
        self,
        runtime: _WorkspaceOpenRuntimeStub,
        *,
        split_paths: tuple[str, ...],
    ) -> None:
        super().__init__(runtime)
        self.tk = _DropTkStub(split_paths)

@dataclass(slots=True, frozen=True)
class _SavedWorkspaceStub:
    path: str
    display_name: str

class _SavedWorkspaceDeleteRuntimeStub:
    def __init__(
        self,
        deleted_workspace: _SavedWorkspaceStub | None,
        *,
        running_workspace_paths: tuple[str, ...] = (),
    ) -> None:
        self._deleted_workspace = deleted_workspace
        self._running_workspace_paths = set(running_workspace_paths)
        self.running_checks: list[str] = []
        self.deleted_paths: list[str] = []

    def workspace_path_has_running_job(self, workspace_path: str) -> bool:
        self.running_checks.append(workspace_path)
        return workspace_path in self._running_workspace_paths

    def delete_saved_workspace(self, workspace_path: str) -> _SavedWorkspaceStub | None:
        self.deleted_paths.append(workspace_path)
        return self._deleted_workspace

class _SavedWorkspaceListboxStub:
    def __init__(self, selection: tuple[int, ...]) -> None:
        self._selection = selection
        self.selection_clear_calls: list[tuple[int, object]] = []
        self.selection_sets: list[int] = []
        self.activate_calls: list[int] = []
        self.see_calls: list[int] = []

    def curselection(self) -> tuple[int, ...]:
        return self._selection

    def selection_clear(self, first: int, last: object) -> None:
        self.selection_clear_calls.append((first, last))
        self._selection = ()

    def selection_set(self, index: int) -> None:
        self.selection_sets.append(index)
        self._selection = (index,)

    def activate(self, index: int) -> None:
        self.activate_calls.append(index)

    def see(self, index: int) -> None:
        self.see_calls.append(index)

class _SavedWorkspaceDeleteWindowStub(_KoreanUiLanguageStub):
    _select_saved_workspace_after_delete = (
        MainWindow._select_saved_workspace_after_delete
    )

    def __init__(
        self,
        runtime: _SavedWorkspaceDeleteRuntimeStub,
        *,
        saved_workspace_paths: list[str],
        selection: tuple[int, ...],
    ) -> None:
        self._runtime = runtime
        self._saved_workspace_paths = saved_workspace_paths
        self._saved_workspaces_listbox = _SavedWorkspaceListboxStub(selection)
        self.refresh_saved_workspace_list_calls = 0
        self.status_messages: list[str] = []

    def _refresh_saved_workspace_list(self) -> None:
        self.refresh_saved_workspace_list_calls += 1
        self._saved_workspace_paths = [
            saved_path
            for saved_path in self._saved_workspace_paths
            if saved_path not in self._runtime.deleted_paths
        ]

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

@dataclass(slots=True, frozen=True)
class _ClosedSessionTabStub:
    workspace_tab_id: str

@dataclass(slots=True, frozen=True)
class _CloseResultStub:
    session_tab: _ClosedSessionTabStub | None = None
    canceled_job: Job | None = None
    removed_queued_job_count: int = 0

class _TabCloseRuntimeStub:
    def __init__(self, jobs: tuple[Job, ...]) -> None:
        self._jobs = jobs
        self.closed_session_ids: list[str] = []
        self.closed_workspace_ids: list[str] = []

    def list_jobs(self, *, session_tab_id: str | None = None) -> tuple[Job, ...]:
        if session_tab_id is None:
            return self._jobs
        return tuple(job for job in self._jobs if job.session_tab_id == session_tab_id)

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        return tuple(job for job in self._jobs if job.workspace_tab_id == workspace_tab_id)

    def close_session(self, session_tab_id: str) -> _CloseResultStub:
        self.closed_session_ids.append(session_tab_id)
        removed_count = len(
            [
                job
                for job in self._jobs
                if job.session_tab_id == session_tab_id
                and job.status in (JobStatus.QUEUED, JobStatus.WAITING_FOR_CONFIGURATION)
            ]
        )
        return _CloseResultStub(
            session_tab=_ClosedSessionTabStub(workspace_tab_id="workspace-1"),
            removed_queued_job_count=removed_count,
        )

    def close_workspace(self, workspace_tab_id: str) -> _CloseResultStub:
        self.closed_workspace_ids.append(workspace_tab_id)
        removed_count = len(
            [
                job
                for job in self._jobs
                if job.workspace_tab_id == workspace_tab_id
                and job.status in (JobStatus.QUEUED, JobStatus.WAITING_FOR_CONFIGURATION)
            ]
        )
        return _CloseResultStub(removed_queued_job_count=removed_count)

class _TabCloseWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _TabCloseRuntimeStub) -> None:
        self._runtime = runtime
        self._queue_start_pending_workspace_ids: set[str] = set()
        self.removed_session_views: list[str] = []
        self.removed_workspace_views: list[str] = []
        self.refreshed_workspace_ids: list[str] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _remove_session_view(self, session_tab_id: str) -> None:
        self.removed_session_views.append(session_tab_id)

    def _remove_workspace_view(self, workspace_tab_id: str) -> None:
        self.removed_workspace_views.append(workspace_tab_id)

    def _session_has_running_job(self, session_tab_id: str) -> bool:
        return MainWindow._session_has_running_job(self, session_tab_id)

    def _workspace_has_running_job(self, workspace_tab_id: str) -> bool:
        return MainWindow._workspace_has_running_job(self, workspace_tab_id)

    def _session_pending_job_count(self, session_tab_id: str) -> int:
        return MainWindow._session_pending_job_count(self, session_tab_id)

    def _workspace_pending_job_count(self, workspace_tab_id: str) -> int:
        return MainWindow._workspace_pending_job_count(self, workspace_tab_id)

    def _confirm_tab_close(
        self,
        *,
        title: str,
        has_running_job: bool,
        pending_job_count: int,
    ) -> bool:
        return MainWindow._confirm_tab_close(
            self,
            title=title,
            has_running_job=has_running_job,
            pending_job_count=pending_job_count,
        )

    def _refresh_workspace_task_list(self, workspace_tab_id: str) -> None:
        self.refreshed_workspace_ids.append(workspace_tab_id)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

class _WorkspaceNotebookSelectStub:
    def __init__(self, selected_tab: str) -> None:
        self._selected_tab = selected_tab

    def select(self) -> str:
        return self._selected_tab

class _CloseActiveWorkspaceWindowStub(_KoreanUiLanguageStub):
    def __init__(self, *, selected_tab: str) -> None:
        self._workspace_notebook = _WorkspaceNotebookSelectStub(selected_tab)
        self._workspace_frame_map = {"frame-1": "workspace-1"}
        self.closed_workspace_ids: list[str] = []
        self.status_messages: list[str] = []

    def _close_workspace(self, workspace_tab_id: str) -> None:
        self.closed_workspace_ids.append(workspace_tab_id)

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

class _PollingWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _PollingRuntimeStub,
        *,
        drain_exception: Exception | None = None,
    ) -> None:
        self._runtime = runtime
        self._closed = False
        self._after_id: str | None = None
        self._event_poll_idle_interval_ms = EVENT_POLL_INTERVAL_MS
        self._drain_exception = drain_exception
        self.after_intervals: list[int] = []
        self.drain_runtime_events_calls = 0

    def _schedule_event_poll(self) -> None:
        raise AssertionError("scheduled callback should not run during the test")

    def _drain_runtime_events(self, *, max_items: int | None = None) -> int:
        del max_items
        self.drain_runtime_events_calls += 1
        if self._drain_exception is not None:
            raise self._drain_exception
        return 0

    def _next_event_poll_interval(
        self,
        *,
        processed: int,
        drained: int,
        poll_failed: bool = False,
    ) -> int:
        return MainWindow._next_event_poll_interval(
            self,
            processed=processed,
            drained=drained,
            poll_failed=poll_failed,
        )

    def after(self, interval_ms: int, callback: object) -> str:
        del callback
        self.after_intervals.append(interval_ms)
        return f"after-{len(self.after_intervals)}"


class _ShutdownWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _PollingRuntimeStub,
        *,
        drain_exception: Exception | None = None,
    ) -> None:
        self._runtime = runtime
        self._shutdown_after_id: str | None = None
        self._drain_exception = drain_exception
        self.after_intervals: list[int] = []
        self.finalize_close_calls = 0
        self.status_messages: list[str] = []

    def _continue_close(self) -> None:
        raise AssertionError("scheduled callback should not run during the test")

    def _drain_runtime_events(self, *, max_items: int | None = None) -> int:
        del max_items
        if self._drain_exception is not None:
            raise self._drain_exception
        return 0

    def _finalize_close(self) -> None:
        self.finalize_close_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

    def after(self, interval_ms: int, callback: object) -> str:
        del callback
        self.after_intervals.append(interval_ms)
        return f"after-{len(self.after_intervals)}"


__all__ = [name for name in globals() if not name.startswith("__")]

