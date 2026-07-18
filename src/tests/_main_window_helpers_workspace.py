from __future__ import annotations

from tests._main_window_helpers_core import *

class _SidebarCollapseWindowStub:
    _toggle_sidebar = MainWindow._toggle_sidebar
    _set_sidebar_collapsed = MainWindow._set_sidebar_collapsed
    _remember_sidebar_restore_width = MainWindow._remember_sidebar_restore_width
    _apply_sidebar_layout = MainWindow._apply_sidebar_layout
    _position_sidebar_sash = MainWindow._position_sidebar_sash
    _refresh_sidebar_restore_button = MainWindow._refresh_sidebar_restore_button
    _is_sidebar_sash_hidden = MainWindow._is_sidebar_sash_hidden
    _expanded_sidebar_width = MainWindow._expanded_sidebar_width

    def __init__(self, *, sash_position: int = SIDEBAR_INITIAL_WIDTH) -> None:
        self._ui_scale = _IdentityUiScaleStub()
        self._main_splitter = _PanedWindowStub(sash_position)
        self._sidebar = _ConfigurableWidgetStub()
        self._sidebar_content = _GridWidgetStub()
        self._sidebar_toggle_button = _ButtonConfigureStub()
        self._sidebar_restore_button = _ButtonConfigureStub()
        self._sidebar_collapsed = False
        self._sidebar_restore_width = SIDEBAR_INITIAL_WIDTH

class _SidebarRebuildWindowStub:
    _refresh_sidebar_restore_button = MainWindow._refresh_sidebar_restore_button
    _is_sidebar_sash_hidden = MainWindow._is_sidebar_sash_hidden
    _position_sidebar_sash = MainWindow._position_sidebar_sash
    _expanded_sidebar_width = MainWindow._expanded_sidebar_width

    def __init__(self) -> None:
        self._ui_scale = _IdentityUiScaleStub()
        self._workspace_views: dict[str, object] = {}
        self._workspace_frame_map: dict[str, str] = {}
        self._session_frame_map: dict[str, tuple[str, str]] = {}
        self._preset_language_request_ids: dict[str, int] = {}
        self._preset_instruction_request_ids: dict[str, int] = {}
        self._job_context_menu = object()
        self._main_splitter = _PanedWindowStub()
        self._sidebar = _ConfigurableWidgetStub()
        self._sidebar_content = _GridWidgetStub()
        self._sidebar_toggle_button = _ButtonConfigureStub()
        self._sidebar_restore_button = _ButtonConfigureStub()
        self._sidebar_collapsed = True
        self._sidebar_restore_width = 236
        self._main_area = object()
        self._status_bar = object()
        self._settings_summary_label = object()
        self._scheduled_run_button = object()
        self._scheduled_run_status_label = object()
        self._saved_workspace_paths = ["workspace"]
        self.build_widgets_calls = 0
        self.refresh_saved_workspace_list_calls = 0
        self.refresh_scheduled_run_display_calls = 0
        self.refresh_settings_summary_calls = 0
        self.rebuild_workspace_tabs_calls = 0

    def _build_widgets(self) -> None:
        self.build_widgets_calls += 1
        self._main_splitter = _PanedWindowStub()
        self._sidebar = _ConfigurableWidgetStub()
        self._sidebar_content = _GridWidgetStub()
        self._sidebar_toggle_button = _ButtonConfigureStub()
        self._sidebar_restore_button = _ButtonConfigureStub()
        MainWindow._apply_sidebar_layout(self)

    def _refresh_saved_workspace_list(self) -> None:
        self.refresh_saved_workspace_list_calls += 1

    def _refresh_scheduled_run_display(self) -> None:
        self.refresh_scheduled_run_display_calls += 1

    def _refresh_settings_summary(self) -> None:
        self.refresh_settings_summary_calls += 1

    def _rebuild_workspace_tabs(self) -> None:
        self.rebuild_workspace_tabs_calls += 1

@dataclass(slots=True)
class _WorkspaceQueueSummaryViewStub:
    queue_var: _StringVarStub
    queue_toggle_var: _BoolVarStub
    queue_toggle_button: _ButtonConfigureStub

class _WorkspaceQueueSummaryRuntimeStub:
    def __init__(
        self,
        jobs: tuple[Job, ...],
        *,
        queue_status: QueueStatus = QueueStatus.STARTED,
        last_stop_reason: QueueStopReason | str | None = None,
    ) -> None:
        self._jobs = jobs
        self._queue_status = queue_status
        self._last_stop_reason = last_stop_reason
        self.list_workspace_jobs_requests: list[str] = []
        self.list_jobs_by_workspace_requests: list[tuple[str, ...]] = []
        self.summarize_workspace_jobs_requests: list[tuple[str, ...]] = []

    def get_queue_state(self, workspace_tab_id: str) -> WorkspaceQueueState:
        return WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id,
            status=self._queue_status,
            last_stop_reason=self._last_stop_reason,
        )

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
                has_runnable_jobs=any(
                    job.workspace_tab_id == workspace_tab_id
                    and job.status == JobStatus.QUEUED
                    for job in self._jobs
                ),
                has_running_job=any(
                    job.workspace_tab_id == workspace_tab_id
                    and job.status == JobStatus.RUNNING
                    for job in self._jobs
                ),
            )
            for workspace_tab_id in workspace_tab_ids
        }

class _WorkspaceQueueSummaryWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _WorkspaceQueueSummaryRuntimeStub) -> None:
        self._runtime = runtime
        self._queue_start_pending_workspace_ids: set[str] = set()
        self.workspace_view = _WorkspaceQueueSummaryViewStub(
            queue_var=_StringVarStub(),
            queue_toggle_var=_BoolVarStub(False),
            queue_toggle_button=_ButtonConfigureStub(),
        )
        self._workspace_views = {"workspace-1": self.workspace_view}
        self.indicator_calls: list[tuple[str, bool]] = []

    def _format_queue_label(self, queue_state: WorkspaceQueueState) -> str:
        return MainWindow._format_queue_label(self, queue_state)

    def _queue_start_is_pending(self, workspace_tab_id: str) -> bool:
        return MainWindow._queue_start_is_pending(self, workspace_tab_id)

    def _set_queue_toggle_state(
        self,
        workspace_view: object,
        *,
        active: bool,
        enabled: bool = True,
    ) -> None:
        MainWindow._set_queue_toggle_state(self, workspace_view, active=active, enabled=enabled)

    def _workspace_has_running_job(self, workspace_tab_id: str) -> bool:
        return MainWindow._workspace_has_running_job(self, workspace_tab_id)

    def _refresh_workspace_tab_indicator(self, workspace_tab_id: str, *, running: bool) -> None:
        self.indicator_calls.append((workspace_tab_id, running))

@dataclass(slots=True)
class _TaskListWorkspaceViewStub:
    workspace_jobs_tree: _TaskListTreeStub
    workspace_jobs_summary_var: _StringVarStub

class _TaskListRuntimeStub:
    def __init__(self, jobs: tuple[Job, ...]) -> None:
        self._jobs = jobs

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        del workspace_tab_id
        return self._jobs

class _TaskListWindowStub(_KoreanUiLanguageStub):
    def __init__(self, jobs: tuple[Job, ...], tree: _TaskListTreeStub) -> None:
        self._runtime = _TaskListRuntimeStub(jobs)
        self._workspace_views = {
            "workspace-1": _TaskListWorkspaceViewStub(
                workspace_jobs_tree=tree,
                workspace_jobs_summary_var=_StringVarStub(),
            )
        }

    def _job_session_label(self, job: Job) -> str:
        del job
        return "S1"

class _PromptDialogWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _JobLookupRuntimeStub) -> None:
        self._runtime = runtime
        self.status_messages: list[str] = []

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

class _JobDeleteRuntimeStub:
    def __init__(self, job: Job) -> None:
        self._job = job
        self.deleted_job_ids: list[str] = []

    def get_job(self, job_id: str) -> Job:
        if job_id != self._job.job_id:
            raise KeyError(job_id)
        return self._job

    def delete_job(self, job_id: str) -> Job:
        if job_id != self._job.job_id:
            raise KeyError(job_id)
        self.deleted_job_ids.append(job_id)
        return self._job

class _JobDeleteWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _JobDeleteRuntimeStub) -> None:
        self._runtime = runtime
        self.drain_runtime_events_calls = 0
        self.refreshed_session_ids: list[str] = []
        self.refreshed_workspace_ids: list[str] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _has_session_view(self, session_tab_id: str) -> bool:
        del session_tab_id
        return True

    def _refresh_session_view(self, session_tab_id: str) -> None:
        self.refreshed_session_ids.append(session_tab_id)

    def _refresh_workspace_task_list(self, workspace_tab_id: str) -> None:
        self.refreshed_workspace_ids.append(workspace_tab_id)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

@dataclass(slots=True, frozen=True)
class _WorkspaceTabStub:
    display_name: str

@dataclass(slots=True, frozen=True)
class _CreatedSessionTabStub:
    session_tab_id: str
    display_name: str

class _CreatePresetSessionRuntimeStub:
    def __init__(self) -> None:
        self.open_preset_session_workspace_ids: list[str] = []

    def open_preset_session(self, workspace_tab_id: str) -> _CreatedSessionTabStub:
        self.open_preset_session_workspace_ids.append(workspace_tab_id)
        return _CreatedSessionTabStub(
            session_tab_id="session-preset-1",
            display_name="P1",
        )

class _CreatePresetSessionWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _CreatePresetSessionRuntimeStub) -> None:
        self._runtime = runtime
        self.ensured_session_ids: list[str] = []
        self.refreshed_session_ids: list[str] = []
        self.selected_workspace_ids: list[str] = []
        self.selected_session_ids: list[tuple[str, str]] = []
        self.status_messages: list[str] = []

    def _ensure_session_view(self, session_tab_id: str) -> None:
        self.ensured_session_ids.append(session_tab_id)

    def _refresh_session_view(self, session_tab_id: str) -> None:
        self.refreshed_session_ids.append(session_tab_id)

    def _select_workspace_tab(self, workspace_tab_id: str) -> None:
        self.selected_workspace_ids.append(workspace_tab_id)

    def _select_session_tab(self, workspace_tab_id: str, session_tab_id: str) -> None:
        self.selected_session_ids.append((workspace_tab_id, session_tab_id))

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

@dataclass(slots=True)
class _BulkImportSessionWidgetsStub:
    auto_commit_var: "_BoolVarStub"

class _BulkImportRuntimeStub:
    def __init__(self) -> None:
        self.import_calls: list[tuple[str, tuple[str, ...], bool, StepExecutionMode]] = []

    def import_prompt_sessions(
        self,
        workspace_tab_id: str,
        prompts: tuple[str, ...],
        *,
        auto_commit_enabled: bool,
        step_execution_mode: StepExecutionMode = StepExecutionMode.SINGLE_SESSION,
    ) -> ImportedPromptSessionsResult:
        self.import_calls.append(
            (
                workspace_tab_id,
                tuple(prompts),
                auto_commit_enabled,
                step_execution_mode,
            )
        )
        registrations: list[ImportedPromptSessionRegistration] = []
        next_job_number = 1
        if step_execution_mode == StepExecutionMode.SINGLE_SESSION:
            session_prompts = tuple(("session-1", "S1", prompt) for prompt in prompts)
        else:
            session_prompts = tuple(
                (f"session-{index}", f"S{index}", prompt)
                for index, prompt in enumerate(prompts, start=1)
            )

        for session_tab_id, display_name, prompt in session_prompts:
            prompt_job = Job(
                job_id=f"job-{next_job_number}",
                workspace_tab_id=workspace_tab_id,
                session_tab_id=session_tab_id,
                prompt=prompt,
                status=JobStatus.QUEUED,
            )
            next_job_number += 1
            auto_commit_job = None
            if auto_commit_enabled:
                auto_commit_job = Job(
                    job_id=f"job-{next_job_number}",
                    workspace_tab_id=workspace_tab_id,
                    session_tab_id=session_tab_id,
                    prompt=AUTO_COMMIT_PROMPT,
                    status=JobStatus.QUEUED,
                )
                next_job_number += 1
            registrations.append(
                ImportedPromptSessionRegistration(
                    session_tab=SessionTab(
                        session_tab_id=session_tab_id,
                        workspace_tab_id=workspace_tab_id,
                        display_name=display_name,
                    ),
                    prompt_job=prompt_job,
                    auto_commit_job=auto_commit_job,
                )
            )
        return ImportedPromptSessionsResult(registrations=tuple(registrations))

class _BulkImportWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _BulkImportRuntimeStub) -> None:
        self._runtime = runtime
        self.ensured_session_ids: list[str] = []
        self.auto_commit_states: list[tuple[str, bool]] = []
        self.drain_runtime_events_calls = 0
        self.refreshed_session_ids: list[tuple[str, str | None]] = []
        self.refreshed_workspace_ids: list[tuple[str, str | None]] = []
        self.selected_workspace_ids: list[str] = []
        self.selected_session_ids: list[tuple[str, str]] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _ensure_session_view(self, session_tab_id: str) -> _BulkImportSessionWidgetsStub:
        self.ensured_session_ids.append(session_tab_id)
        return _BulkImportSessionWidgetsStub(
            auto_commit_var=_BulkImportBoolVarStub(self, session_tab_id)
        )

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _refresh_session_view(
        self,
        session_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_session_ids.append((session_tab_id, preferred_job_id))

    def _select_workspace_tab(self, workspace_tab_id: str) -> None:
        self.selected_workspace_ids.append(workspace_tab_id)

    def _select_session_tab(self, workspace_tab_id: str, session_tab_id: str) -> None:
        self.selected_session_ids.append((workspace_tab_id, session_tab_id))

    def _refresh_workspace_task_list(
        self,
        workspace_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_workspace_ids.append((workspace_tab_id, preferred_job_id))

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

class _BulkImportBoolVarStub:
    def __init__(self, window: _BulkImportWindowStub, session_tab_id: str) -> None:
        self._window = window
        self._session_tab_id = session_tab_id

    def set(self, value: bool) -> None:
        self._window.auto_commit_states.append((self._session_tab_id, value))

class _QueueRuntimeStub:
    def __init__(
        self,
        *,
        jobs: tuple[Job, ...] | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        self.settings = settings or AppSettings(ui_language="ko")
        self._jobs = (
            jobs
            if jobs is not None
            else (
                Job(
                    job_id="job-1",
                    workspace_tab_id="workspace-1",
                    session_tab_id="session-1",
                    prompt="queued",
                ),
            )
        )
        self.background_starts: list[str] = []
        self.stopped_queue_ids: list[str] = []
        self.list_workspace_jobs_requests: list[str] = []
        self.workspace_has_jobs_requests: list[str] = []

    def has_pending_background_work(self) -> bool:
        return False

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        self.list_workspace_jobs_requests.append(workspace_tab_id)
        return tuple(job for job in self._jobs if job.workspace_tab_id == workspace_tab_id)

    def workspace_has_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_jobs_requests.append(workspace_tab_id)
        return any(job.workspace_tab_id == workspace_tab_id for job in self._jobs)

    def workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_jobs_requests.append(workspace_tab_id)
        return any(
            job.workspace_tab_id == workspace_tab_id and job.status == JobStatus.QUEUED
            for job in self._jobs
        )

    def start_queue_in_background(self, workspace_tab_id: str) -> None:
        self.background_starts.append(workspace_tab_id)

    def stop_queue(self, workspace_tab_id: str) -> WorkspaceQueueState:
        self.stopped_queue_ids.append(workspace_tab_id)
        return WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id,
            status=QueueStatus.STOPPED,
        )

    def get_workspace_tab(self, workspace_tab_id: str) -> _WorkspaceTabStub:
        return _WorkspaceTabStub(display_name="W1")

class _QueueWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _QueueRuntimeStub, *, toggle_value: bool = False) -> None:
        self._runtime = runtime
        self._workspace_views = {
            "workspace-1": _WorkspaceQueueSummaryViewStub(
                queue_var=_StringVarStub(),
                queue_toggle_var=_BoolVarStub(toggle_value),
                queue_toggle_button=_ButtonConfigureStub(),
            )
        }
        self._queue_start_pending_workspace_ids: set[str] = set()
        self.drain_runtime_events_calls = 0
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _start_queue(self, workspace_tab_id: str) -> bool:
        return MainWindow._start_queue(self, workspace_tab_id)

    def _stop_queue(self, workspace_tab_id: str) -> bool:
        return MainWindow._stop_queue(self, workspace_tab_id)

    def _workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        return MainWindow._workspace_has_runnable_jobs(self, workspace_tab_id)

    def _queue_mode_is_shared(self) -> bool:
        return MainWindow._queue_mode_is_shared(self)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

@dataclass(slots=True, frozen=True)
class _ScheduledRunWorkspaceTabStub:
    workspace_tab_id: str
    display_name: str

class _ScheduledRunRuntimeStub:
    def __init__(
        self,
        *,
        jobs: tuple[Job, ...],
        open_workspace_ids: tuple[str, ...],
        settings: AppSettings | None = None,
    ) -> None:
        self.settings = settings or AppSettings(ui_language="ko")
        self._jobs = jobs
        self._open_workspace_ids = open_workspace_ids
        self.background_starts: list[str] = []
        self.workspace_has_runnable_jobs_requests: list[str] = []

    def list_workspace_tabs(
        self,
        *,
        include_closed: bool = False,
    ) -> tuple[_ScheduledRunWorkspaceTabStub, ...]:
        del include_closed
        return tuple(
            _ScheduledRunWorkspaceTabStub(
                workspace_tab_id=workspace_tab_id,
                display_name=workspace_tab_id.replace("workspace-", "W"),
            )
            for workspace_tab_id in self._open_workspace_ids
        )

    def workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_runnable_jobs_requests.append(workspace_tab_id)
        return any(
            job.workspace_tab_id == workspace_tab_id and job.status == JobStatus.QUEUED
            for job in self._jobs
        )

    def start_queue_in_background(self, workspace_tab_id: str) -> None:
        self.background_starts.append(workspace_tab_id)

    def get_workspace_tab(self, workspace_tab_id: str) -> _ScheduledRunWorkspaceTabStub:
        return _ScheduledRunWorkspaceTabStub(
            workspace_tab_id=workspace_tab_id,
            display_name=workspace_tab_id.replace("workspace-", "W"),
        )

class _ScheduledRunWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _ScheduledRunRuntimeStub) -> None:
        self._runtime = runtime
        self._closed = False
        self._scheduled_run_at: datetime | None = None
        self._scheduled_run_after_id: str | None = None
        self._scheduled_run_var = _StringVarStub()
        self._scheduled_run_toggle_var = _BoolVarStub(False)
        self._scheduled_run_button = _ButtonConfigureStub()
        self._scheduled_run_status_label = None
        self._queue_start_pending_workspace_ids: set[str] = set()
        self.status_messages: list[str] = []
        self.canceled_after_ids: list[str] = []
        self.after_intervals: list[int] = []
        self.refresh_workspace_queue_summaries_calls = 0

    def _cancel_scheduled_run(self, *, update_status: bool = False) -> None:
        MainWindow._cancel_scheduled_run(self, update_status=update_status)

    def _cancel_scheduled_run_timer(self) -> None:
        MainWindow._cancel_scheduled_run_timer(self)

    def _schedule_scheduled_run_check(self) -> None:
        MainWindow._schedule_scheduled_run_check(self)

    def _start_scheduled_run_queues(self, scheduled_at: datetime) -> None:
        MainWindow._start_scheduled_run_queues(self, scheduled_at)

    def _start_file_drop_registered_jobs(self, request_id: str) -> None:
        MainWindow._start_file_drop_registered_jobs(self, request_id)

    def _start_registered_job_queues(
        self,
        *,
        started_status_key: str,
        no_jobs_status_key: str,
    ) -> int:
        return MainWindow._start_registered_job_queues(
            self,
            started_status_key=started_status_key,
            no_jobs_status_key=no_jobs_status_key,
        )

    def _refresh_scheduled_run_display(self) -> None:
        MainWindow._refresh_scheduled_run_display(self)

    def _workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        return MainWindow._workspace_has_runnable_jobs(self, workspace_tab_id)

    def _queue_mode_is_shared(self) -> bool:
        return MainWindow._queue_mode_is_shared(self)

    def _start_queue(self, workspace_tab_id: str) -> bool:
        return MainWindow._start_queue(self, workspace_tab_id)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

    def after(self, interval_ms: int, callback: object) -> str:
        del callback
        self.after_intervals.append(interval_ms)
        return f"after-{len(self.after_intervals)}"

    def after_cancel(self, after_id: str) -> None:
        self.canceled_after_ids.append(after_id)

__all__ = [name for name in globals() if not name.startswith("__")]
