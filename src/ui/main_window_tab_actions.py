"""MainWindow role mixins split from ui.main_window."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
import logging
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, scrolledtext, ttk

try:
    from tkinterdnd2 import COPY as DND_COPY_ACTION
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_COPY_ACTION = "copy"
    DND_FILES = None
    TkinterDnD = None

from app import (
    AppRuntime,
    CompletedSessionUpdatedEvent,
    JobStatusChangedEvent,
    LogAppendedEvent,
    SessionIdConfirmedEvent,
)
from app.runtime import (
    AUTO_COMMIT_PROMPT,
    DEFAULT_PRESET_WORK_PRIORITY,
    MAX_JOB_PROGRESS_LOG_LINES,
    PersistenceIssueEvent,
    PresetAnalysisJobSubmittedEvent,
    PresetAnalysisJobSubmissionFailedEvent,
    PresetCandidateJobsRegisteredEvent,
    PresetPromptInstructionsLoadedEvent,
    PresetPromptLanguagesLoadedEvent,
    PRESET_WORK_PRIORITY_OPTIONS,
    QueueStartCompletedEvent,
    RuntimeActionFailedEvent,
    RuntimeActionWarningEvent,
    SettingsRetryCompletedEvent,
    WorkspaceOpenCompletedEvent,
)
from app.agent_cli_version import load_agent_cli_version_text
from app.agent_cli_options import (
    SelectOption,
    build_agent_provider_select_options,
    build_configured_agent_provider_select_options,
    build_model_select_options,
    build_reasoning_select_options,
    find_option_label,
)
from app.use_cases import extract_text_import_prompts
from app.version import APP_NAME, APP_VERSION
from domain import (
    AgentExecutionOptions,
    Job,
    JobStatus,
    QUEUE_MODE_SHARED,
    QueueStatus,
    SessionTabKind,
    canonicalize_workspace_path,
    workspace_folder_display_name,
)
from domain.localization import normalize_ui_language

from .dpi import DpiMetrics, UiScale
from .formatters import (
    completed_activity_text as _completed_activity_text,
    context_menu_prompt_label as _context_menu_prompt_label,
    finished_activity_text as _finished_activity_text,
    format_settings_summary as _format_settings_summary,
    job_status_label as _job_status_label,
    queue_stop_reason_label as _queue_stop_reason_label,
    running_activity_text as _running_activity_text,
    session_job_message_text as _session_job_message_text,
)
from .i18n import (
    localize_progress_line,
    localize_runtime_message,
)
from .main_window_shared import (
    _set_optional_label_text,
    _window_language,
    _tr_for,
    _localize_status_message,
    _split_dropped_workspace_paths,
    _session_kind_uses_prompt_editor,
    _notebook_insert_position,
    _queue_full_session_view_refresh,
    _is_pending_close_job,
    _format_scheduled_run_time,
    _safe_configure,
    _should_follow_text_end,
)
from .main_window_state import (
    DEFAULT_AUTO_COMMIT_ENABLED,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    EVENT_POLL_BACKGROUND_BATCH_SIZE,
    EVENT_POLL_BACKLOG_INTERVAL_MS,
    EVENT_POLL_IDLE_MAX_INTERVAL_MS,
    EVENT_POLL_INTERVAL_MS,
    EVENT_POLL_RUNTIME_BATCH_SIZE,
    ExecutionOptionControls,
    ExecutionOptionControlValues,
    MAIN_AREA_MIN_WIDTH,
    MESSAGE_LABEL_FOREGROUND,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    OUTPUT_FONT_FAMILY,
    OUTPUT_PANE_INITIAL_HEIGHT,
    PRESET_COMBOBOX_WIDTH,
    PROMPT_PANE_INITIAL_HEIGHT,
    RuntimeUiUpdateBatch,
    SCHEDULED_RUN_POLL_MAX_INTERVAL_MS,
    SESSION_EXECUTION_SUMMARY_WIDTH,
    SIDEBAR_COLLAPSED_WIDTH,
    SIDEBAR_INITIAL_WIDTH,
    TEXT_AUTOSCROLL_BOTTOM_THRESHOLD,
    WAIT_REASON_LABEL_FOREGROUND,
    WORKSPACE_SESSION_ACTION_BUTTONS,
    WORKSPACE_SESSIONS_INITIAL_WIDTH,
    WORKSPACE_TAB_ACTIVE_BORDER,
    WORKSPACE_TAB_ACTIVE_FILL,
    WORKSPACE_TASK_LIST_INITIAL_WIDTH,
    SessionInputWidgets,
    SessionOutputAppend,
    SessionWidgets,
    WorkspaceWidgets,
)
from .resources import app_icon_ico_path, app_icon_png_path
from .session_history import (
    HISTORY_TURN_SEPARATOR,
    SessionHistoryTurnRenderState,
    format_timestamp as _format_timestamp,
    join_session_history_blocks,
    render_session_history_turns,
    session_history_first_changed_index,
    session_history_prefix_length,
)
from .text_context_menu import bind_editable_text_context_menu
from .theme import (
    apply_dark_theme,
    configure_listbox,
    configure_text_widget,
)
from .windows_icon import (
    apply_windows_window_icon,
    destroy_windows_icon_handles,
)
from .workspace_tasks import (
    configure_workspace_task_tree_columns,
    resize_workspace_task_columns,
    sync_workspace_task_list,
    workspace_task_column_ids,
)

LOGGER = logging.getLogger("ui.main_window")


def _main_window_global(name: str):
    return getattr(sys.modules["ui.main_window"], name)


class MainWindowTabActionsMixin:
    def _session_tab_to_select_after_close(
        self, workspace_view: WorkspaceWidgets, closed_frame: tk.Misc
    ) -> str | None:
        closed_tab_id = str(closed_frame)
        try:
            tab_ids = tuple(
                str(tab_id) for tab_id in workspace_view.session_notebook.tabs()
            )
        except tk.TclError:
            return None

        try:
            closed_index = tab_ids.index(closed_tab_id)
        except ValueError:
            return None

        right_index = closed_index + 1
        if right_index < len(tab_ids):
            return tab_ids[right_index]
        if closed_index > 0:
            return tab_ids[closed_index - 1]
        return None

    def _remove_session_view(self, session_tab_id: str) -> None:
        session_tab = self._runtime.get_session_tab(session_tab_id)
        workspace_view = self._workspace_views.get(session_tab.workspace_tab_id)
        if workspace_view is None:
            return

        session_widgets = workspace_view.session_views.pop(session_tab_id, None)
        if session_widgets is None:
            return

        next_selected_tab_id = self._session_tab_to_select_after_close(
            workspace_view,
            session_widgets.frame,
        )
        self._preset_language_request_ids.pop(session_tab_id, None)
        self._preset_instruction_request_ids.pop(session_tab_id, None)
        self._preset_registration_pending_session_ids.discard(session_tab_id)
        self._immediate_run_pending_session_ids.discard(session_tab_id)
        self._session_frame_map.pop(str(session_widgets.frame), None)
        try:
            workspace_view.session_notebook.forget(session_widgets.frame)
        except tk.TclError:
            pass
        if next_selected_tab_id is not None:
            try:
                workspace_view.session_notebook.select(next_selected_tab_id)
            except tk.TclError:
                pass
        session_widgets.frame.destroy()

    def _remove_workspace_view(self, workspace_tab_id: str) -> None:
        workspace_view = self._workspace_views.pop(workspace_tab_id, None)
        if workspace_view is None:
            return

        for session_tab_id, session_widgets in tuple(
            workspace_view.session_views.items()
        ):
            self._session_frame_map.pop(str(session_widgets.frame), None)
            session_widgets.frame.destroy()
            workspace_view.session_views.pop(session_tab_id, None)

        self._workspace_frame_map.pop(str(workspace_view.frame), None)
        try:
            self._workspace_notebook.forget(workspace_view.frame)
        except tk.TclError:
            pass
        workspace_view.frame.destroy()
        self._refresh_empty_state()

    def _session_has_running_job(self, session_tab_id: str) -> bool:
        return any(
            job.status == JobStatus.RUNNING
            for job in self._runtime.list_jobs(session_tab_id=session_tab_id)
        )

    def _workspace_has_running_job(self, workspace_tab_id: str) -> bool:
        return any(
            job.status == JobStatus.RUNNING
            for job in self._runtime.list_workspace_jobs(workspace_tab_id)
        )

    def _session_pending_job_count(self, session_tab_id: str) -> int:
        return sum(
            1
            for job in self._runtime.list_jobs(session_tab_id=session_tab_id)
            if _is_pending_close_job(job)
        )

    def _workspace_pending_job_count(self, workspace_tab_id: str) -> int:
        return sum(
            1
            for job in self._runtime.list_workspace_jobs(workspace_tab_id)
            if _is_pending_close_job(job)
        )

    def _confirm_tab_close(
        self,
        *,
        title: str,
        has_running_job: bool,
        pending_job_count: int,
    ) -> bool:
        if not has_running_job and pending_job_count == 0:
            return True

        if has_running_job and pending_job_count > 0:
            message = _tr_for(
                self,
                "confirm_running_and_pending_close",
                count=pending_job_count,
            )
        elif has_running_job:
            message = _tr_for(self, "confirm_running_close")
        else:
            message = _tr_for(self, "confirm_pending_close", count=pending_job_count)

        return messagebox.askyesno(title, message, parent=self)

    def _has_session_view(self, session_tab_id: str) -> bool:
        try:
            session_tab = self._runtime.get_session_tab(session_tab_id)
        except KeyError:
            return False

        workspace_view = self._workspace_views.get(session_tab.workspace_tab_id)
        if workspace_view is None:
            return False
        return session_tab_id in workspace_view.session_views

    def _select_workspace_tab(self, workspace_tab_id: str) -> None:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            return
        self._workspace_notebook.select(workspace_view.frame)

    def _select_session_tab(self, workspace_tab_id: str, session_tab_id: str) -> None:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            return
        session_widgets = workspace_view.session_views.get(session_tab_id)
        if session_widgets is None:
            return
        workspace_view.session_notebook.select(session_widgets.frame)

    def _select_session_progress_log_tab(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        session_widgets.body_notebook.select(session_widgets.progress_log_tab_frame)

