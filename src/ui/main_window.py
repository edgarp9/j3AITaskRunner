"""Tkinter main window for j3AITaskRunner."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
import logging
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
    QueueStatus,
    SessionTabKind,
    canonicalize_workspace_path,
    workspace_folder_display_name,
)
from domain.localization import normalize_ui_language

from .dialogs import (
    AboutDialog,
    BulkPromptImportDialog,
    PromptViewerDialog,
    ScheduledRunDialog,
    SessionExitHookDialog,
    SettingsDialog,
)
from .dpi import DpiMetrics, DpiSyncController, UiScale, configure_tk_dpi
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
    text as ui_text,
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
    WindowsIconHandles,
    apply_windows_window_icon,
    destroy_windows_icon_handles,
)
from .workspace_tasks import (
    configure_workspace_task_tree_columns,
    resize_workspace_task_columns,
    sync_workspace_task_list,
    workspace_task_column_ids,
)

LOGGER = logging.getLogger(__name__)

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
from .main_window_lifecycle import MainWindowLifecycleMixin
from .main_window_layout import MainWindowLayoutMixin
from .main_window_events import MainWindowEventsMixin
from .main_window_workspace_views import MainWindowWorkspaceViewsMixin
from .main_window_execution_controls import MainWindowExecutionControlsMixin
from .main_window_workspace_actions import MainWindowWorkspaceActionsMixin
from .main_window_preset import MainWindowPresetMixin
from .main_window_queue_session import MainWindowQueueSessionMixin


class MainWindow(
    MainWindowLifecycleMixin,
    MainWindowLayoutMixin,
    MainWindowEventsMixin,
    MainWindowWorkspaceViewsMixin,
    MainWindowExecutionControlsMixin,
    MainWindowWorkspaceActionsMixin,
    MainWindowPresetMixin,
    MainWindowQueueSessionMixin,
    tk.Tk,
):
    """Top-level Tkinter window that bridges user input to the app runtime."""

    def __init__(self, runtime: AppRuntime) -> None:
        super().__init__()
        self._dpi_metrics = configure_tk_dpi(self, logger=LOGGER)
        self._ui_scale = UiScale.from_metrics(self._dpi_metrics)
        self._dpi_sync_controller = DpiSyncController(
            self,
            self._handle_dpi_metrics_changed,
            logger=LOGGER,
        )
        self._runtime = runtime
        self._after_id: str | None = None
        self._shutdown_after_id: str | None = None
        self._startup_issues_after_id: str | None = None
        self._scheduled_run_after_id: str | None = None
        self._event_poll_idle_interval_ms = EVENT_POLL_INTERVAL_MS
        self._closed = False
        self._workspace_views: dict[str, WorkspaceWidgets] = {}
        self._workspace_frame_map: dict[str, str] = {}
        self._session_frame_map: dict[str, tuple[str, str]] = {}
        self._queue_start_pending_workspace_ids: set[str] = set()
        self._workspace_preset_languages: dict[str, str] = {}
        self._workspace_preset_instructions: dict[tuple[str, str], str] = {}
        self._workspace_preset_work_priorities: dict[str, str] = {}
        self._workspace_preset_prompt_prefixes: dict[str, str] = {}
        self._workspace_preset_action_execution_options: dict[
            str,
            AgentExecutionOptions,
        ] = {}
        self._preset_option_request_sequence = 0
        self._preset_language_request_ids: dict[str, int] = {}
        self._preset_instruction_request_ids: dict[str, int] = {}
        self._preset_registration_pending_session_ids: set[str] = set()
        self._immediate_run_pending_session_ids: set[str] = set()
        self._saved_workspace_paths: list[str] = []
        self._app_icon_image: tk.PhotoImage | None = None
        self._windows_icon_handles: WindowsIconHandles | None = None
        self._job_context_menu: tk.Menu | None = None
        self._main_splitter: ttk.Panedwindow | None = None
        self._sidebar: ttk.Frame | None = None
        self._sidebar_content: ttk.Frame | None = None
        self._sidebar_toggle_button: ttk.Button | None = None
        self._sidebar_restore_button: ttk.Button | None = None
        self._sidebar_collapsed = False
        self._sidebar_restore_width = self._ui_scale.px(SIDEBAR_INITIAL_WIDTH)
        self._main_area: ttk.Frame | None = None
        self._status_bar_container: ttk.Frame | None = None
        self._status_bar: ttk.Label | None = None
        self._settings_summary_label: ttk.Label | None = None
        self._scheduled_run_button: ttk.Checkbutton | None = None
        self._scheduled_run_status_label: ttk.Label | None = None
        self._workspace_register_button: ttk.Button | None = None
        self._workspace_remove_button: ttk.Button | None = None
        self._workspace_open_button: ttk.Button | None = None
        self._workspace_close_button: ttk.Button | None = None
        self._about_button: ttk.Button | None = None
        self._settings_button: ttk.Button | None = None
        self._scheduled_run_at: datetime | None = None
        self._ui_language = normalize_ui_language(self._runtime.settings.ui_language)

        self._settings_var = tk.StringVar()
        self._scheduled_run_var = tk.StringVar()
        self._scheduled_run_toggle_var = tk.BooleanVar(value=False)
        self._status_message_var = tk.StringVar(
            value=ui_text("app_initial_status", self._ui_language)
        )

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self._apply_app_icon()
        self.geometry(f"{DEFAULT_WINDOW_WIDTH}x{DEFAULT_WINDOW_HEIGHT}")
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.protocol("WM_DELETE_WINDOW", self.close)

        apply_dark_theme(self, scale=self._ui_scale)
        self._build_widgets()
        self._refresh_saved_workspace_list()
        self._refresh_settings_summary()
        self._rebuild_workspace_tabs()
        self._dpi_sync_controller.bind()
        self._startup_issues_after_id = self.after(50, self._show_startup_issues)

    def run(self) -> None:
        """Start the Tkinter main loop."""
        self._schedule_event_poll()
        self.mainloop()

    def open_startup_workspaces(self, workspace_paths: Sequence[str]) -> None:
        """Request startup workspace opens after Tk has processed pending setup."""
        requested_paths = tuple(workspace_paths)
        if not requested_paths:
            return
        self.after(
            0,
            lambda paths=requested_paths: self._open_startup_workspace_paths(paths),
        )

    def _open_startup_workspace_paths(self, workspace_paths: Sequence[str]) -> None:
        for workspace_path in workspace_paths:
            self._open_workspace_path(workspace_path)

