from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tkinter as tk
from tkinter import scrolledtext, ttk
import unittest
from unittest.mock import patch

from app.agent_cli_version import load_agent_cli_version_text
from app.runtime import (
    ImportedPromptSessionRegistration,
    ImportedPromptSessionsResult,
    JobStatusChangedEvent,
    PersistenceIssueEvent,
    PresetAnalysisJobSubmittedEvent,
    PresetAnalysisJobSubmissionFailedEvent,
    PresetCandidateJobsRegisteredEvent,
    RuntimeActionWarningEvent,
    SettingsRetryCompletedEvent,
    SettingsUpdateResult,
)
from app.scheduler import WorkspaceJobSummary
from app.version import APP_NAME, APP_VERSION
from app.use_cases import UseCaseIssue
from domain import (
    AgentExecutionOptions,
    AppSettings,
    Job,
    JobStatus,
    QueueStatus,
    QueueStopReason,
    SessionTab,
    SessionTabKind,
    StepExecutionMode,
    WorkspaceQueueState,
)
from ui.main_window import (
    AUTO_COMMIT_PROMPT,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    EVENT_POLL_INTERVAL_MS,
    ExecutionOptionControls,
    MainWindow,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    PRESET_COMBOBOX_WIDTH,
    RuntimeUiUpdateBatch,
    SIDEBAR_COLLAPSED_WIDTH,
    SIDEBAR_INITIAL_WIDTH,
    SESSION_EXECUTION_SUMMARY_WIDTH,
    WORKSPACE_SESSIONS_INITIAL_WIDTH,
    WORKSPACE_SESSION_ACTION_BUTTONS,
    WORKSPACE_TASK_LIST_INITIAL_WIDTH,
    _completed_activity_text,
    _finished_activity_text,
    _format_settings_summary,
    _localize_status_message,
    _running_activity_text,
    _session_job_message_text,
    _set_optional_label_text,
    _session_kind_uses_prompt_editor,
)
from ui.formatters import (
    failed_activity_text as _failed_activity_text,
    format_workspace_task_summary as _format_workspace_task_summary,
    job_progress_text as _job_progress_text,
)
from ui.workspace_tasks import (
    calculate_workspace_task_column_widths as _calculate_workspace_task_column_widths,
)
from ui.session_history import (
    join_session_history_blocks,
    render_session_history_turns,
    session_history_prefix_length,
)
from ui.dialogs import (
    AboutDialog,
    ABOUT_SOURCE_URL,
    BULK_IMPORT_EXAMPLE_TEXT,
    SETTINGS_AUTHOR_URL,
    BulkPromptImportDialog,
    BulkPromptImportDialogResult,
    LicenseNoticesDialog,
    ScheduledRunValidationError,
    SettingsDialog,
    default_scheduled_run_time,
    parse_scheduled_run_datetime,
)
from ui.agent_settings_dialog import AgentSettingsDialog
from ui.i18n import text as ui_text
from main import build_runtime

def _write_prompt_pair(root: Path, *, language: str, instruction: str) -> None:
    prompt_dir = root / "prompt" / language
    prompt_dir.mkdir(parents=True)
    (prompt_dir / f"{instruction}.md").write_text("analysis prompt", encoding="utf-8")
    (prompt_dir / f"{instruction}_work.md").write_text(
        "work prompt {{candidates_payload}}",
        encoding="utf-8",
    )

def _walk_widgets(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from _walk_widgets(child)

def _widget_text(widget: tk.Misc) -> str:
    try:
        return str(widget.cget("text"))
    except tk.TclError:
        return ""

def _find_widgets_by_text(widget: tk.Misc, text: str) -> list[tk.Misc]:
    return [child for child in _walk_widgets(widget) if _widget_text(child) == text]

def _is_tk_display_unavailable(error: tk.TclError) -> bool:
    message = str(error).casefold()
    return (
        "no display" in message
        or "couldn't connect to display" in message
        or "cannot open display" in message
        or "can't find a usable init.tcl" in message
        or "can't find a usable tk.tcl" in message
        or "tcl wasn't installed properly" in message
        or "tk wasn't installed properly" in message
    )

def _create_tk_root_or_skip(test_case: unittest.TestCase) -> tk.Tk:
    try:
        root = tk.Tk()
    except tk.TclError as error:
        if _is_tk_display_unavailable(error):
            test_case.skipTest(f"Tk display is unavailable: {error}")
        raise
    root.withdraw()
    return root

def _destroy_dialog_and_root(
    dialog: tk.Toplevel | None,
    root: tk.Tk | None,
) -> None:
    if dialog is not None:
        try:
            if dialog.winfo_exists():
                dialog.destroy()
        except tk.TclError:
            pass
    if root is not None:
        try:
            root.destroy()
        except tk.TclError:
            pass

def _close_tk_window(window: MainWindow) -> None:
    try:
        window.close()
        for _ in range(100):
            try:
                window.update()
                if not window.winfo_exists():
                    return
            except tk.TclError:
                return
            time.sleep(0.01)
    finally:
        try:
            if window.winfo_exists():
                window.destroy()
        except tk.TclError:
            pass

def _shutdown_runtime(runtime: object) -> None:
    runtime.shutdown()
    for _ in range(100):
        runtime.process_background_events(max_items=32)
        if not runtime.has_pending_background_work():
            return
        time.sleep(0.01)

class _KoreanUiLanguageStub:
    _ui_language = "ko"

class _StringVarStub:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value

class _BoolVarStub:
    def __init__(self, value: bool) -> None:
        self._value = value

    def get(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = value

class _BodyNotebookSelectStub:
    def __init__(self) -> None:
        self.selected_tabs: list[object] = []

    def select(self, tab_id: object | None = None) -> str:
        if tab_id is None:
            return str(self.selected_tabs[-1]) if self.selected_tabs else ""
        self.selected_tabs.append(tab_id)
        return str(tab_id)

class _SubmitPromptTextStub:
    def __init__(self, content: str) -> None:
        self.content = content
        self.state = "normal"
        self.deleted_ranges: list[tuple[str, str]] = []

    def get(self, start: str, end: str) -> str:
        del start, end
        return self.content

    def cget(self, option: str) -> object:
        if option == "state":
            return self.state
        raise KeyError(option)

    def configure(self, **options: object) -> None:
        if "state" in options:
            self.state = str(options["state"])

    def grid(self) -> None:
        self.is_gridded = True
        self.grid_calls += 1

    def grid_remove(self) -> None:
        self.is_gridded = False
        self.grid_remove_calls += 1

    def lift(self) -> None:
        self.lift_calls += 1

    def delete(self, start: str, end: str) -> None:
        self.deleted_ranges.append((start, end))
        self.content = ""

class _IdentityUiScaleStub:
    def px(self, value: int | float) -> int:
        return int(value)

    def padding(self, *values: int | float) -> int | tuple[int, ...]:
        scaled = tuple(int(value) for value in values)
        if len(scaled) == 1:
            return scaled[0]
        return scaled

class _ConfigurableWidgetStub:
    def __init__(self) -> None:
        self.width: int | None = None
        self.configured_options: list[dict[str, object]] = []

    def configure(self, **options: object) -> None:
        self.configured_options.append(options)
        if "width" in options:
            self.width = int(options["width"])

class _GridWidgetStub(_ConfigurableWidgetStub):
    def __init__(self) -> None:
        super().__init__()
        self.grid_calls = 0
        self.grid_remove_calls = 0

    def grid(self) -> None:
        self.grid_calls += 1

    def grid_remove(self) -> None:
        self.grid_remove_calls += 1

class _PanedWindowStub:
    def __init__(self, sash_position: int = SIDEBAR_INITIAL_WIDTH) -> None:
        self.sash_position = sash_position
        self.destroy_calls = 0
        self.width = DEFAULT_WINDOW_WIDTH

    def sashpos(self, index: int, position: int | None = None) -> int | None:
        self.sashpos_index = index
        if position is None:
            return self.sash_position
        self.sash_position = position
        return None

    def winfo_width(self) -> int:
        return self.width

    def destroy(self) -> None:
        self.destroy_calls += 1

class _ButtonConfigureStub:
    def __init__(self) -> None:
        self.text = ""
        self.state = ""
        self.is_gridded = False
        self.grid_calls = 0
        self.grid_remove_calls = 0
        self.lift_calls = 0
        self.configured_options: list[dict[str, object]] = []

    def configure(self, **options: object) -> None:
        self.configured_options.append(options)
        if "text" in options:
            self.text = str(options["text"])
        if "state" in options:
            self.state = str(options["state"])

    def cget(self, option: str) -> object:
        if option == "text":
            return self.text
        if option == "state":
            return self.state
        raise KeyError(option)

    def grid(self) -> None:
        self.is_gridded = True
        self.grid_calls += 1

    def grid_remove(self) -> None:
        self.is_gridded = False
        self.grid_remove_calls += 1

    def lift(self) -> None:
        self.lift_calls += 1

class _ComboboxConfigureStub:
    def __init__(self, values: tuple[str, ...]) -> None:
        self._values = values
        self.state = ""
        self.configured_options: list[dict[str, object]] = []

    def cget(self, option: str) -> object:
        if option == "values":
            return self._values
        if option == "state":
            return self.state
        raise KeyError(option)

    def configure(self, **options: object) -> None:
        self.configured_options.append(options)
        if "values" in options:
            values = options["values"]
            self._values = tuple(values) if isinstance(values, (tuple, list)) else tuple()
        if "state" in options:
            self.state = str(options["state"])

class _LabelVisibilityStub:
    def __init__(self) -> None:
        self.grid_calls = 0
        self.grid_remove_calls = 0

    def grid(self) -> None:
        self.grid_calls += 1

    def grid_remove(self) -> None:
        self.grid_remove_calls += 1

class _SettingsRuntimeStub:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

class _StatusLocalizationWindowStub:
    def __init__(self, settings: AppSettings) -> None:
        self._runtime = _SettingsRuntimeStub(settings)
        self._status_message_var = _StringVarStub()

@dataclass(slots=True, frozen=True)
class _SessionIdCopySessionTabStub:
    session_id: str | None

class _SessionIdCopyRuntimeStub:
    def __init__(self, *, session_id: str | None) -> None:
        self._session_id = session_id

    def get_session_tab(self, session_tab_id: str) -> _SessionIdCopySessionTabStub:
        del session_tab_id
        return _SessionIdCopySessionTabStub(session_id=self._session_id)

class _SessionIdCopyWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _SessionIdCopyRuntimeStub) -> None:
        self._runtime = runtime
        self.clipboard_text = ""
        self.status_messages: list[str] = []

    def clipboard_clear(self) -> None:
        self.clipboard_text = ""

    def clipboard_append(self, value: str) -> None:
        self.clipboard_text += value

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

@dataclass(slots=True, frozen=True)
class _ContextMenuEvent:
    y: int
    x_root: int
    y_root: int

class _ContextMenuTreeStub:
    def __init__(self, *, row_id: str) -> None:
        self._row_id = row_id
        self.selection_sets: list[str] = []
        self.focus_sets: list[str] = []

    def identify_row(self, y: int) -> str:
        del y
        return self._row_id

    def selection_set(self, job_id: str) -> None:
        self.selection_sets.append(job_id)

    def focus(self, job_id: str) -> None:
        self.focus_sets.append(job_id)

class _FakeContextMenu:
    def __init__(self, parent: object, *, tearoff: bool) -> None:
        del parent, tearoff
        self.command_labels: list[str] = []
        self.commands: list[object] = []
        self.separator_calls = 0
        self.popup_position: tuple[int, int] | None = None
        self.grab_release_calls = 0

    def add_command(self, *, label: str, command: object) -> None:
        self.command_labels.append(label)
        self.commands.append(command)

    def add_separator(self) -> None:
        self.separator_calls += 1

    def tk_popup(self, x_root: int, y_root: int) -> None:
        self.popup_position = (x_root, y_root)

    def grab_release(self) -> None:
        self.grab_release_calls += 1

@dataclass(slots=True)
class _ContextMenuWorkspaceViewStub:
    workspace_jobs_tree: _ContextMenuTreeStub

class _JobLookupRuntimeStub:
    def __init__(self, job: Job) -> None:
        self._job = job

    def get_job(self, job_id: str) -> Job:
        if job_id != self._job.job_id:
            raise KeyError(job_id)
        return self._job

class _ContextMenuWindowStub(_KoreanUiLanguageStub):
    def __init__(self, tree: _ContextMenuTreeStub, job: Job) -> None:
        self._runtime = _JobLookupRuntimeStub(job)
        self._job_context_menu = None
        self._workspace_views = {"workspace-1": _ContextMenuWorkspaceViewStub(tree)}
        self.selected_jobs: list[tuple[str, str]] = []
        self.prompt_dialog_job_ids: list[str] = []
        self.deleted_job_ids: list[str] = []
        self.status_messages: list[str] = []

    def _select_workspace_job(self, workspace_tab_id: str, job_id: str) -> None:
        self.selected_jobs.append((workspace_tab_id, job_id))

    def _show_job_prompt_dialog(self, job_id: str) -> None:
        self.prompt_dialog_job_ids.append(job_id)

    def _delete_job(self, job_id: str) -> None:
        self.deleted_job_ids.append(job_id)

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

class _TaskListTreeStub:
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, ...]] = {}
        self.inserted_iids: list[str] = []
        self.deleted_iids: list[str] = []
        self.moves: list[tuple[str, str, int]] = []
        self.selection_sets: list[str] = []
        self.focus_sets: list[str] = []
        self.selection_remove_calls: list[tuple[str, ...]] = []
        self._selection: tuple[str, ...] = ()

    def get_children(self) -> tuple[str, ...]:
        return tuple(self.items)

    def selection(self) -> tuple[str, ...]:
        return self._selection

    def delete(self, iid: str) -> None:
        self.deleted_iids.append(iid)
        self.items.pop(iid, None)

    def exists(self, iid: str) -> bool:
        return iid in self.items

    def item(self, iid: str, *, values: tuple[str, ...]) -> None:
        self.items[iid] = tuple(values)

    def move(self, iid: str, parent: str, index: int) -> None:
        self.moves.append((iid, parent, index))

    def insert(self, parent: str, index: str, *, iid: str, values: tuple[str, ...]) -> None:
        del parent, index
        self.items[iid] = tuple(values)
        self.inserted_iids.append(iid)

    def selection_remove(self, selection: tuple[str, ...]) -> None:
        self.selection_remove_calls.append(selection)
        self._selection = ()

    def selection_set(self, iid: str) -> None:
        self.selection_sets.append(iid)
        self._selection = (iid,)

    def focus(self, iid: str) -> None:
        self.focus_sets.append(iid)

class _RuntimeStub:
    def __init__(self, *, settings: AppSettings, update_result: SettingsUpdateResult) -> None:
        self.settings = settings
        self._update_result = update_result
        self.updated_settings: list[AppSettings] = []

    def update_settings(self, settings: AppSettings) -> SettingsUpdateResult:
        self.updated_settings.append(settings)
        self.settings = settings
        return self._update_result

class _MainWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _RuntimeStub) -> None:
        self._runtime = runtime
        self.drain_runtime_events_calls = 0
        self.refresh_settings_summary_calls = 0
        self.refresh_workspace_queue_summaries_calls = 0
        self.apply_output_font_to_all_sessions_calls = 0
        self.refresh_all_session_execution_option_controls_calls = 0
        self.refresh_session_outputs_for_all_sessions_calls = 0
        self.rebuild_static_ui_calls = 0
        self.status_messages: list[str] = []

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _refresh_settings_summary(self) -> None:
        self.refresh_settings_summary_calls += 1

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _apply_output_font_to_all_sessions(self) -> None:
        self.apply_output_font_to_all_sessions_calls += 1

    def _refresh_all_session_execution_option_controls(self) -> None:
        self.refresh_all_session_execution_option_controls_calls += 1

    def _refresh_session_outputs_for_all_sessions(self) -> None:
        self.refresh_session_outputs_for_all_sessions_calls += 1

    def _rebuild_static_ui(self) -> None:
        self.rebuild_static_ui_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

class _RuntimeUiUpdateRuntimeStub:
    def __init__(self) -> None:
        self.list_jobs_by_workspace_requests: list[tuple[str, ...]] = []

    def list_jobs_by_workspace(
        self,
        workspace_tab_ids: tuple[str, ...],
    ) -> dict[str, tuple[Job, ...]]:
        self.list_jobs_by_workspace_requests.append(workspace_tab_ids)
        return {workspace_tab_id: () for workspace_tab_id in workspace_tab_ids}

class _RuntimeUiUpdateWindowStub(_KoreanUiLanguageStub):
    _queue_full_session_view_refresh = MainWindow._queue_full_session_view_refresh

    def __init__(self) -> None:
        self._runtime = _RuntimeUiUpdateRuntimeStub()
        self._workspace_views = {
            "workspace-1": object(),
            "workspace-2": object(),
        }
        self.synced_workspace_ids: list[str] = []
        self.refreshed_session_ids: list[str] = []
        self.refreshed_workspace_ids: list[str] = []
        self.refreshed_queue_summary_workspace_ids: list[tuple[str, ...] | None] = []

    def _sync_session_tab_order(self, workspace_tab_id: str) -> None:
        self.synced_workspace_ids.append(workspace_tab_id)

    def _has_session_view(self, session_tab_id: str) -> bool:
        del session_tab_id
        return True

    def _refresh_session_view(self, session_tab_id: str) -> None:
        self.refreshed_session_ids.append(session_tab_id)

    def _refresh_workspace_task_list(
        self,
        workspace_tab_id: str,
        *,
        jobs: tuple[Job, ...] = (),
    ) -> None:
        del jobs
        self.refreshed_workspace_ids.append(workspace_tab_id)

    def _refresh_workspace_queue_summaries(
        self,
        workspace_tab_ids: object = None,
    ) -> None:
        if workspace_tab_ids is None:
            self.refreshed_queue_summary_workspace_ids.append(None)
            return
        self.refreshed_queue_summary_workspace_ids.append(tuple(workspace_tab_ids))

@dataclass(slots=True)
class _HistoryTurnStub:
    started_at: datetime
    completed_at: datetime | None
    prompt_text: str
    response_text: str | None
    error_text: str | None = None

def _history_dt(minute: int) -> datetime:
    return datetime(2025, 1, 1, 0, minute, tzinfo=timezone.utc)

class _PollingRuntimeStub:
    def __init__(
        self,
        *,
        background_exception: Exception | None = None,
        pending_exception: Exception | None = None,
    ) -> None:
        self._background_exception = background_exception
        self._pending_exception = pending_exception
        self.process_background_events_calls = 0
        self.has_pending_background_work_calls = 0

    def process_background_events(self, *, max_items: int | None = None) -> int:
        del max_items
        self.process_background_events_calls += 1
        if self._background_exception is not None:
            raise self._background_exception
        return 0

    def has_pending_background_work(self) -> bool:
        self.has_pending_background_work_calls += 1
        if self._pending_exception is not None:
            raise self._pending_exception
        return False

__all__ = [name for name in globals() if not name.startswith("__")]
