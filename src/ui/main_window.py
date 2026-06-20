"""Tkinter main window for j3AITaskRunner."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
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
    BulkPromptImportDialog,
    PromptViewerDialog,
    ScheduledRunDialog,
    SettingsDialog,
)
from .dpi import DpiMetrics, DpiSyncController, UiScale, configure_tk_dpi
from .formatters import (
    completed_activity_text as _completed_activity_text,
    context_menu_prompt_label as _context_menu_prompt_label,
    finished_activity_text as _finished_activity_text,
    format_settings_summary as _format_settings_summary,
    format_workspace_task_summary as _format_workspace_task_summary,
    job_progress_text as _job_progress_text,
    job_status_label as _job_status_label,
    queue_stop_reason_label as _queue_stop_reason_label,
    running_activity_text as _running_activity_text,
    session_job_message_text as _session_job_message_text,
    task_column_heading as _task_column_heading,
    truncate_prompt as _truncate_prompt,
)
from .i18n import (
    localize_progress_line,
    localize_runtime_message,
    text as ui_text,
)
from .resources import app_icon_ico_path, app_icon_png_path
from .text_context_menu import bind_editable_text_context_menu
from .theme import (
    DARK_THEME,
    apply_dark_theme,
    configure_listbox,
    configure_text_widget,
)
from .windows_icon import (
    WindowsIconHandles,
    apply_windows_window_icon,
    destroy_windows_icon_handles,
)

LOGGER = logging.getLogger(__name__)

EVENT_POLL_INTERVAL_MS = 150
EVENT_POLL_BACKLOG_INTERVAL_MS = 15
EVENT_POLL_IDLE_MAX_INTERVAL_MS = 750
EVENT_POLL_BACKGROUND_BATCH_SIZE = 32
EVENT_POLL_RUNTIME_BATCH_SIZE = 32
SCHEDULED_RUN_POLL_MAX_INTERVAL_MS = 30_000
OUTPUT_FONT_FAMILY = "Consolas"
TEXT_AUTOSCROLL_BOTTOM_THRESHOLD = 0.98
DEFAULT_WINDOW_WIDTH = 1100
DEFAULT_WINDOW_HEIGHT = 800
MIN_WINDOW_WIDTH = 800
MIN_WINDOW_HEIGHT = 600
SIDEBAR_INITIAL_WIDTH = 180
MAIN_AREA_MIN_WIDTH = 780
WORKSPACE_SESSIONS_INITIAL_WIDTH = 560
WORKSPACE_TASK_LIST_INITIAL_WIDTH = 180
PROMPT_PANE_INITIAL_HEIGHT = 170
OUTPUT_PANE_INITIAL_HEIGHT = 300
WORKSPACE_TASK_COLUMN_MIN_WIDTH = 1
WORKSPACE_TAB_ACTIVE_FILL = DARK_THEME.success_fill
WORKSPACE_TAB_ACTIVE_BORDER = DARK_THEME.success_border
MESSAGE_LABEL_FOREGROUND = DARK_THEME.accent
WAIT_REASON_LABEL_FOREGROUND = DARK_THEME.warning
DEFAULT_AUTO_COMMIT_ENABLED = True
PRESET_COMBOBOX_WIDTH = 10
SESSION_PROVIDER_COMBOBOX_WIDTH = 8
SESSION_MODEL_COMBOBOX_WIDTH = 8
SESSION_REASONING_COMBOBOX_WIDTH = 6
HISTORY_TURN_SEPARATOR = "\n\n" + "-" * 72 + "\n"

WORKSPACE_TASK_COLUMNS = (
    ("order", "Order", 74, "center"),
    ("session", "Session", 70, "center"),
    ("progress", "Status", 150, "w"),
    ("prompt", "Prompt", 300, "w"),
)


def _set_optional_label_text(
    label: tk.Widget,
    value_var: tk.StringVar,
    value: str,
) -> None:
    value_var.set(value)
    if value:
        label.grid()
    else:
        label.grid_remove()


def _window_language(window: object) -> str:
    runtime = getattr(window, "_runtime", None)
    settings = getattr(runtime, "settings", None)
    language = getattr(settings, "ui_language", None)
    if language is None:
        language = getattr(window, "_ui_language", None)
    return normalize_ui_language(language)


def _tr_for(window: object, key: str, **values: object) -> str:
    return ui_text(key, _window_language(window), **values)


def _localize_status_message(window: object, message: str) -> str:
    return localize_runtime_message(message, _window_language(window))


@dataclass(slots=True, frozen=True)
class WorkspaceActionButtonSpec:
    """Placement and command metadata for workspace action buttons."""

    text: str
    text_key: str
    command_name: str
    column: int


WORKSPACE_SESSION_ACTION_BUTTONS = (
    WorkspaceActionButtonSpec(
        text="New Session",
        text_key="button_new_session",
        command_name="_create_session_for_workspace",
        column=2,
    ),
    WorkspaceActionButtonSpec(
        text="New Preset",
        text_key="button_new_preset",
        command_name="_create_preset_session_for_workspace",
        column=3,
    ),
    WorkspaceActionButtonSpec(
        text="Import",
        text_key="button_import",
        command_name="_open_bulk_import_dialog_for_workspace",
        column=4,
    ),
)


@dataclass(slots=True)
class SessionHistoryTurnRenderState:
    """Cached render state for one session history turn."""

    started_at: object
    completed_at: object
    prompt_text: str
    response_text: str | None
    block_length: int
    content_end_index: int = 0


@dataclass(slots=True, frozen=True)
class ExecutionOptionControlValues:
    """Resolved choices and selected values for one execution-option control row."""

    provider_options: tuple[SelectOption, ...]
    model_options: tuple[SelectOption, ...]
    reasoning_options: tuple[SelectOption, ...]
    provider_value: str
    model_value: str
    reasoning_value: str
    execution_options: AgentExecutionOptions


@dataclass(slots=True)
class ExecutionOptionControls:
    """Widget state for one provider/model/reasoning selector row."""

    agent_provider_var: tk.StringVar
    model_var: tk.StringVar
    reasoning_var: tk.StringVar
    agent_provider_combobox: ttk.Combobox
    model_combobox: ttk.Combobox
    reasoning_combobox: ttk.Combobox
    agent_provider_options: tuple[SelectOption, ...] = ()
    model_options: tuple[SelectOption, ...] = ()
    reasoning_options: tuple[SelectOption, ...] = ()
    execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )


@dataclass(slots=True)
class SessionWidgets:
    """Widget references for one session tab."""

    frame: ttk.Frame
    content_pane: ttk.Panedwindow
    prompt_frame: ttk.LabelFrame
    output_frame: ttk.LabelFrame
    started_tab_image: tk.PhotoImage
    prompt_text: scrolledtext.ScrolledText | None
    log_text: scrolledtext.ScrolledText
    history_text: scrolledtext.ScrolledText
    auto_commit_var: tk.BooleanVar
    session_id_var: tk.StringVar
    activity_var: tk.StringVar
    message_var: tk.StringVar
    wait_reason_var: tk.StringVar
    message_label: ttk.Label
    wait_reason_label: ttk.Label
    execution_controls: ExecutionOptionControls
    preset_language_var: tk.StringVar | None = None
    preset_instruction_var: tk.StringVar | None = None
    preset_work_priority_var: tk.StringVar | None = None
    preset_language_combobox: ttk.Combobox | None = None
    preset_instruction_combobox: ttk.Combobox | None = None
    preset_work_priority_combobox: ttk.Combobox | None = None
    preset_prompt_prefix_text: scrolledtext.ScrolledText | None = None
    preset_auto_commit_checkbutton: ttk.Checkbutton | None = None
    preset_register_button: ttk.Button | None = None
    preset_action_execution_controls: ExecutionOptionControls | None = None
    selected_job_id: str | None = None
    rendered_log_job_id: str | None = None
    rendered_log_line_count: int = 0
    rendered_log_last_line: str | None = None
    rendered_log_language: str | None = None
    rendered_history_turns: tuple[SessionHistoryTurnRenderState, ...] = ()
    rendered_history_source_turns: object | None = None
    rendered_history_language: str | None = None

    @property
    def agent_provider_var(self) -> tk.StringVar:
        return self.execution_controls.agent_provider_var

    @property
    def model_var(self) -> tk.StringVar:
        return self.execution_controls.model_var

    @property
    def reasoning_var(self) -> tk.StringVar:
        return self.execution_controls.reasoning_var

    @property
    def agent_provider_combobox(self) -> ttk.Combobox:
        return self.execution_controls.agent_provider_combobox

    @property
    def model_combobox(self) -> ttk.Combobox:
        return self.execution_controls.model_combobox

    @property
    def reasoning_combobox(self) -> ttk.Combobox:
        return self.execution_controls.reasoning_combobox

    @property
    def agent_provider_options(self) -> tuple[SelectOption, ...]:
        return self.execution_controls.agent_provider_options

    @property
    def model_options(self) -> tuple[SelectOption, ...]:
        return self.execution_controls.model_options

    @property
    def reasoning_options(self) -> tuple[SelectOption, ...]:
        return self.execution_controls.reasoning_options

    @property
    def preset_action_agent_provider_var(self) -> tk.StringVar | None:
        controls = self.preset_action_execution_controls
        return controls.agent_provider_var if controls is not None else None

    @property
    def preset_action_model_var(self) -> tk.StringVar | None:
        controls = self.preset_action_execution_controls
        return controls.model_var if controls is not None else None

    @property
    def preset_action_reasoning_var(self) -> tk.StringVar | None:
        controls = self.preset_action_execution_controls
        return controls.reasoning_var if controls is not None else None

    @property
    def preset_action_agent_provider_combobox(self) -> ttk.Combobox | None:
        controls = self.preset_action_execution_controls
        return controls.agent_provider_combobox if controls is not None else None

    @property
    def preset_action_model_combobox(self) -> ttk.Combobox | None:
        controls = self.preset_action_execution_controls
        return controls.model_combobox if controls is not None else None

    @property
    def preset_action_reasoning_combobox(self) -> ttk.Combobox | None:
        controls = self.preset_action_execution_controls
        return controls.reasoning_combobox if controls is not None else None

    @property
    def preset_action_agent_provider_options(self) -> tuple[SelectOption, ...]:
        controls = self.preset_action_execution_controls
        return controls.agent_provider_options if controls is not None else ()

    @property
    def preset_action_model_options(self) -> tuple[SelectOption, ...]:
        controls = self.preset_action_execution_controls
        return controls.model_options if controls is not None else ()

    @property
    def preset_action_reasoning_options(self) -> tuple[SelectOption, ...]:
        controls = self.preset_action_execution_controls
        return controls.reasoning_options if controls is not None else ()

    @property
    def preset_action_execution_options(self) -> AgentExecutionOptions:
        controls = self.preset_action_execution_controls
        return (
            controls.execution_options
            if controls is not None
            else AgentExecutionOptions()
        )

    @preset_action_execution_options.setter
    def preset_action_execution_options(
        self,
        execution_options: AgentExecutionOptions,
    ) -> None:
        controls = self.preset_action_execution_controls
        if controls is not None:
            controls.execution_options = execution_options


@dataclass(slots=True)
class SessionOutputAppend:
    """Buffered incremental output update for one session."""

    job_id: str
    lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionInputWidgets:
    """Input-area widget references for a normal or preset session."""

    frame: ttk.LabelFrame
    prompt_text: scrolledtext.ScrolledText | None = None
    preset_language_var: tk.StringVar | None = None
    preset_instruction_var: tk.StringVar | None = None
    preset_work_priority_var: tk.StringVar | None = None
    preset_language_combobox: ttk.Combobox | None = None
    preset_instruction_combobox: ttk.Combobox | None = None
    preset_work_priority_combobox: ttk.Combobox | None = None
    preset_prompt_prefix_text: scrolledtext.ScrolledText | None = None
    preset_auto_commit_checkbutton: ttk.Checkbutton | None = None
    preset_register_button: ttk.Button | None = None
    preset_action_execution_controls: ExecutionOptionControls | None = None


@dataclass(slots=True)
class WorkspaceWidgets:
    """Widget references for one workspace tab."""

    frame: ttk.Frame
    content_pane: ttk.Panedwindow
    sessions_area: ttk.Frame
    workspace_jobs_area: ttk.Frame
    session_notebook: ttk.Notebook
    workspace_jobs_tree: ttk.Treeview
    workspace_jobs_summary_var: tk.StringVar
    path_var: tk.StringVar
    queue_var: tk.StringVar
    queue_toggle_var: tk.BooleanVar
    queue_toggle_button: ttk.Checkbutton
    started_tab_image: tk.PhotoImage
    session_views: dict[str, SessionWidgets] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeUiUpdateBatch:
    """Coalesce runtime-driven UI refreshes for one polling tick."""

    refresh_queue_summaries: bool = False
    queue_summary_workspace_ids: set[str] = field(default_factory=set)
    status_message: str | None = None
    persistence_warnings: list[str] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    full_session_views: list[str] = field(default_factory=list)
    session_summaries: set[str] = field(default_factory=set)
    session_histories: set[str] = field(default_factory=set)
    session_outputs: dict[str, SessionOutputAppend | None] = field(default_factory=dict)
    workspace_task_lists: set[str] = field(default_factory=set)
    completed_workspace_paths: set[str] = field(default_factory=set)
    opened_workspaces: list[WorkspaceOpenCompletedEvent] = field(default_factory=list)
    candidate_auto_commit_states: dict[str, bool] = field(default_factory=dict)


class MainWindow(tk.Tk):
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
        self._saved_workspace_paths: list[str] = []
        self._app_icon_image: tk.PhotoImage | None = None
        self._windows_icon_handles: WindowsIconHandles | None = None
        self._job_context_menu: tk.Menu | None = None
        self._main_splitter: ttk.Panedwindow | None = None
        self._sidebar: ttk.Frame | None = None
        self._main_area: ttk.Frame | None = None
        self._status_bar: ttk.Label | None = None
        self._settings_summary_label: ttk.Label | None = None
        self._scheduled_run_button: ttk.Checkbutton | None = None
        self._scheduled_run_status_label: ttk.Label | None = None
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

    def _handle_dpi_metrics_changed(self, metrics: DpiMetrics) -> None:
        self._dpi_metrics = metrics
        self._ui_scale = UiScale.from_metrics(metrics)
        apply_dark_theme(self, scale=self._ui_scale)
        self._apply_scaled_options_after_dpi_change()

    def _apply_scaled_options_after_dpi_change(self) -> None:
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        if self._sidebar is not None:
            _safe_configure(
                self._sidebar,
                padding=self._ui_scale.padding(12, 12, 10, 12),
                width=self._ui_scale.px(SIDEBAR_INITIAL_WIDTH),
            )
        if self._main_area is not None:
            _safe_configure(
                self._main_area,
                padding=self._ui_scale.padding(0, 12, 12, 12),
                width=self._ui_scale.px(MAIN_AREA_MIN_WIDTH),
            )
        if self._status_bar is not None:
            self._status_bar.grid_configure(pady=self._ui_scale.padding(12, 0))
        if self._settings_summary_label is not None:
            _safe_configure(
                self._settings_summary_label,
                wraplength=self._ui_scale.px(SIDEBAR_INITIAL_WIDTH - 28),
            )
        if self._scheduled_run_status_label is not None:
            _safe_configure(
                self._scheduled_run_status_label,
                wraplength=self._ui_scale.px(SIDEBAR_INITIAL_WIDTH - 28),
            )

        configure_listbox(self._saved_workspaces_listbox, scale=self._ui_scale)
        for workspace_tab_id, workspace_view in self._workspace_views.items():
            workspace_tab = self._runtime.get_workspace_tab(workspace_tab_id)
            _safe_configure(workspace_view.frame, padding=self._ui_scale.padding(12))
            _safe_configure(
                workspace_view.sessions_area,
                width=self._ui_scale.px(WORKSPACE_SESSIONS_INITIAL_WIDTH),
            )
            _safe_configure(
                workspace_view.workspace_jobs_area,
                width=self._ui_scale.px(WORKSPACE_TASK_LIST_INITIAL_WIDTH),
            )
            _safe_configure(workspace_view.content_pane, sashwidth=self._ui_scale.px(6))
            self._resize_workspace_task_columns(
                workspace_view.workspace_jobs_tree,
                max(workspace_view.workspace_jobs_tree.winfo_width(), 1),
            )
            workspace_view.started_tab_image = self._create_started_tab_image(
                workspace_tab.display_name
            )

            for session_tab_id, session_widgets in workspace_view.session_views.items():
                session_tab = self._runtime.get_session_tab(session_tab_id)
                _safe_configure(
                    session_widgets.frame, padding=self._ui_scale.padding(10)
                )
                _safe_configure(
                    session_widgets.content_pane,
                    sashwidth=self._ui_scale.px(6),
                )
                _safe_configure(
                    session_widgets.prompt_frame,
                    height=self._ui_scale.px(PROMPT_PANE_INITIAL_HEIGHT),
                )
                _safe_configure(
                    session_widgets.output_frame,
                    height=self._ui_scale.px(OUTPUT_PANE_INITIAL_HEIGHT),
                )
                if session_widgets.prompt_text is not None:
                    configure_text_widget(
                        session_widgets.prompt_text, scale=self._ui_scale
                    )
                if session_widgets.preset_prompt_prefix_text is not None:
                    configure_text_widget(
                        session_widgets.preset_prompt_prefix_text,
                        scale=self._ui_scale,
                    )
                configure_text_widget(session_widgets.log_text, scale=self._ui_scale)
                configure_text_widget(
                    session_widgets.history_text, scale=self._ui_scale
                )
                self._apply_output_font(session_widgets)
                session_widgets.started_tab_image = self._create_started_tab_image(
                    session_tab.display_name
                )
                self._refresh_session_tab_indicator(
                    session_tab_id,
                    started=self._session_has_running_job(session_tab_id),
                )

        self._refresh_workspace_queue_summaries()

    def _apply_app_icon(self) -> None:
        png_path = app_icon_png_path()
        if png_path.is_file():
            try:
                icon_image = tk.PhotoImage(master=self, file=str(png_path))
                self.iconphoto(False, icon_image)
                self.iconphoto(True, icon_image)
                self._app_icon_image = icon_image
            except tk.TclError:
                LOGGER.exception("Failed to apply app icon PNG from %s.", png_path)
        else:
            LOGGER.warning("App icon PNG was not found at %s.", png_path)

        ico_path = app_icon_ico_path()
        if not ico_path.is_file():
            LOGGER.warning("App icon ICO was not found at %s.", ico_path)
            return

        try:
            self.iconbitmap(str(ico_path))
            self.iconbitmap(default=str(ico_path))
        except tk.TclError:
            LOGGER.debug(
                "Failed to apply app icon ICO through Tk from %s.",
                ico_path,
                exc_info=True,
            )

        self._windows_icon_handles = apply_windows_window_icon(
            self.winfo_id(), ico_path
        )

    def close(self) -> None:
        """Cancel polling, stop queue execution, and close the window."""
        if self._closed:
            return

        self._closed = True
        self._dpi_sync_controller.close()
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                LOGGER.debug("Failed to cancel runtime poll callback.", exc_info=True)
            self._after_id = None
        if self._startup_issues_after_id is not None:
            try:
                self.after_cancel(self._startup_issues_after_id)
            except tk.TclError:
                LOGGER.debug(
                    "Failed to cancel startup issue warning callback.", exc_info=True
                )
            self._startup_issues_after_id = None
        self._cancel_scheduled_run_timer()

        self._set_status(_tr_for(self, "app_closing_status"))
        self._runtime.shutdown()
        self._continue_close()

    def _continue_close(self) -> None:
        processed = 0
        drained = 0
        has_pending_work = True
        try:
            processed = self._runtime.process_background_events(
                max_items=EVENT_POLL_BACKGROUND_BATCH_SIZE
            )
            drained = self._drain_runtime_events(
                max_items=EVENT_POLL_RUNTIME_BATCH_SIZE
            )
            has_pending_work = self._runtime.has_pending_background_work()
        except Exception:
            LOGGER.exception("Failed while waiting for runtime shutdown.")
            self._set_status(_tr_for(self, "app_shutdown_retry_status"))

        if (
            not has_pending_work
            and processed < EVENT_POLL_BACKGROUND_BATCH_SIZE
            and drained < EVENT_POLL_RUNTIME_BATCH_SIZE
        ):
            self._finalize_close()
            return

        interval_ms = (
            EVENT_POLL_BACKLOG_INTERVAL_MS
            if processed == EVENT_POLL_BACKGROUND_BATCH_SIZE
            or drained == EVENT_POLL_RUNTIME_BATCH_SIZE
            else EVENT_POLL_INTERVAL_MS
        )
        self._shutdown_after_id = self.after(interval_ms, self._continue_close)

    def _finalize_close(self) -> None:
        if self._shutdown_after_id is not None:
            try:
                self.after_cancel(self._shutdown_after_id)
            except tk.TclError:
                LOGGER.debug("Failed to cancel shutdown callback.", exc_info=True)
            self._shutdown_after_id = None
        windows_icon_handles = self._windows_icon_handles
        self._windows_icon_handles = None
        self.destroy()
        destroy_windows_icon_handles(windows_icon_handles)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        main_splitter = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        self._main_splitter = main_splitter
        main_splitter.grid(row=0, column=0, sticky="nsew")

        sidebar = ttk.Frame(
            main_splitter,
            padding=self._ui_scale.padding(12, 12, 10, 12),
            width=self._ui_scale.px(SIDEBAR_INITIAL_WIDTH),
        )
        self._sidebar = sidebar
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(0, weight=1)

        saved_list_frame = ttk.Frame(sidebar)
        saved_list_frame.grid(
            row=0, column=0, sticky="nsew", pady=self._ui_scale.padding(0, 12)
        )
        saved_list_frame.columnconfigure(0, weight=1)
        saved_list_frame.rowconfigure(0, weight=1)

        self._saved_workspaces_listbox = tk.Listbox(
            saved_list_frame, height=18, exportselection=False
        )
        configure_listbox(self._saved_workspaces_listbox, scale=self._ui_scale)
        self._saved_workspaces_listbox.grid(row=0, column=0, sticky="nsew")
        self._saved_workspaces_listbox.bind(
            "<Double-Button-1>", self._on_saved_workspace_double_click
        )
        saved_scrollbar = ttk.Scrollbar(
            saved_list_frame,
            orient="vertical",
            command=self._saved_workspaces_listbox.yview,
        )
        saved_scrollbar.grid(row=0, column=1, sticky="ns")
        self._saved_workspaces_listbox.configure(yscrollcommand=saved_scrollbar.set)

        workspace_button_frame = ttk.Frame(sidebar)
        workspace_button_frame.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=self._ui_scale.padding(0, 6),
        )
        workspace_button_frame.columnconfigure(0, weight=1)
        workspace_button_frame.columnconfigure(1, weight=1)

        ttk.Button(
            workspace_button_frame,
            text=_tr_for(self, "button_register"),
            command=self._open_workspace_from_dialog,
        ).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=self._ui_scale.padding(0, 3),
            pady=self._ui_scale.padding(0, 6),
        )
        ttk.Button(
            workspace_button_frame,
            text=_tr_for(self, "button_remove"),
            command=self._delete_selected_saved_workspace,
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=self._ui_scale.padding(3, 0),
            pady=self._ui_scale.padding(0, 6),
        )
        ttk.Button(
            workspace_button_frame,
            text=_tr_for(self, "button_open"),
            command=self._open_selected_saved_workspace,
        ).grid(
            row=1,
            column=0,
            sticky="ew",
            padx=self._ui_scale.padding(0, 3),
        )
        ttk.Button(
            workspace_button_frame,
            text=_tr_for(self, "button_close"),
            command=self._close_active_workspace,
        ).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=self._ui_scale.padding(3, 0),
        )
        self._configure_saved_workspace_drop_targets(
            sidebar,
            saved_list_frame,
            self._saved_workspaces_listbox,
            workspace_button_frame,
        )

        settings_frame = ttk.Frame(sidebar)
        settings_frame.grid(row=3, column=0, sticky="ew")
        settings_frame.columnconfigure(0, weight=1)

        scheduled_run_frame = ttk.Frame(sidebar)
        scheduled_run_frame.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=self._ui_scale.padding(0, 12),
        )
        scheduled_run_frame.columnconfigure(0, weight=1)
        self._scheduled_run_button = ttk.Checkbutton(
            scheduled_run_frame,
            text=_tr_for(self, "button_scheduled_run"),
            style="ScheduledRun.Toolbutton",
            variable=self._scheduled_run_toggle_var,
            command=self._open_scheduled_run_dialog,
        )
        self._scheduled_run_button.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=self._ui_scale.padding(0, 6),
        )
        self._scheduled_run_status_label = ttk.Label(
            scheduled_run_frame,
            textvariable=self._scheduled_run_var,
            wraplength=self._ui_scale.px(SIDEBAR_INITIAL_WIDTH - 28),
            justify="left",
        )
        self._scheduled_run_status_label.grid(row=1, column=0, sticky="w")

        settings_header = ttk.Frame(settings_frame)
        settings_header.grid(row=0, column=0, sticky="ew")
        settings_header.columnconfigure(0, weight=1)
        ttk.Button(
            settings_header,
            text=_tr_for(self, "button_change"),
            command=self._open_settings_dialog,
        ).grid(row=0, column=0, sticky="e", pady=self._ui_scale.padding(0, 6))

        self._settings_summary_label = ttk.Label(
            settings_frame,
            textvariable=self._settings_var,
            wraplength=self._ui_scale.px(SIDEBAR_INITIAL_WIDTH - 28),
            justify="left",
        )
        self._settings_summary_label.grid(
            row=1,
            column=0,
            sticky="w",
            pady=self._ui_scale.padding(2, 0),
        )

        main_area = ttk.Frame(
            main_splitter,
            padding=self._ui_scale.padding(0, 12, 12, 12),
            width=self._ui_scale.px(MAIN_AREA_MIN_WIDTH),
        )
        self._main_area = main_area
        main_area.columnconfigure(0, weight=1)
        main_area.rowconfigure(0, weight=1)

        main_splitter.add(
            sidebar,
            weight=0,
        )
        main_splitter.add(
            main_area,
            weight=1,
        )

        self._workspace_notebook = ttk.Notebook(main_area)
        self._workspace_notebook.grid(row=0, column=0, sticky="nsew")
        self._workspace_notebook.bind(
            "<<NotebookTabChanged>>", self._on_workspace_tab_changed
        )

        self._empty_state_label = ttk.Label(
            main_area,
            text=_tr_for(self, "empty_state"),
            anchor="center",
            justify="center",
        )
        self._empty_state_label.grid(row=0, column=0, sticky="nsew")

        status_bar = ttk.Label(
            main_area,
            textvariable=self._status_message_var,
            relief="groove",
            anchor="w",
            style="Status.TLabel",
        )
        self._status_bar = status_bar
        status_bar.grid(
            row=1, column=0, sticky="ew", pady=self._ui_scale.padding(12, 0)
        )

    def _rebuild_static_ui(self) -> None:
        """Recreate static widgets when the display language changes."""
        if self._main_splitter is not None:
            self._main_splitter.destroy()

        self._workspace_views.clear()
        self._workspace_frame_map.clear()
        self._session_frame_map.clear()
        self._preset_language_request_ids.clear()
        self._preset_instruction_request_ids.clear()
        self._job_context_menu = None
        self._main_splitter = None
        self._sidebar = None
        self._main_area = None
        self._status_bar = None
        self._settings_summary_label = None
        self._scheduled_run_button = None
        self._scheduled_run_status_label = None
        self._saved_workspace_paths = []

        self._build_widgets()
        self._refresh_saved_workspace_list()
        self._refresh_scheduled_run_display()
        self._refresh_settings_summary()
        self._rebuild_workspace_tabs()

    def _show_startup_issues(self) -> None:
        self._startup_issues_after_id = None
        if self._closed:
            return
        if not self._runtime.startup_issues:
            return

        issue_message = "\n".join(
            localize_runtime_message(issue.message, _window_language(self))
            for issue in self._runtime.startup_issues
        )
        self._set_status(issue_message)
        messagebox.showwarning(
            _tr_for(self, "dialog_startup_issue"), issue_message, parent=self
        )

    def _schedule_event_poll(self) -> None:
        if self._closed:
            return

        processed = 0
        drained = 0
        poll_failed = False
        try:
            processed = self._runtime.process_background_events(
                max_items=EVENT_POLL_BACKGROUND_BATCH_SIZE
            )
            drained = self._drain_runtime_events(
                max_items=EVENT_POLL_RUNTIME_BATCH_SIZE
            )
        except Exception:
            poll_failed = True
            LOGGER.exception("Failed while polling runtime events.")

        if self._closed:
            return

        interval_ms = self._next_event_poll_interval(
            processed=processed,
            drained=drained,
            poll_failed=poll_failed,
        )
        self._after_id = self.after(interval_ms, self._schedule_event_poll)

    def _next_event_poll_interval(
        self,
        *,
        processed: int,
        drained: int,
        poll_failed: bool = False,
    ) -> int:
        if poll_failed:
            self._event_poll_idle_interval_ms = EVENT_POLL_INTERVAL_MS
            return EVENT_POLL_INTERVAL_MS

        has_backlog = (
            processed == EVENT_POLL_BACKGROUND_BATCH_SIZE
            or drained == EVENT_POLL_RUNTIME_BATCH_SIZE
        )
        if has_backlog:
            self._event_poll_idle_interval_ms = EVENT_POLL_INTERVAL_MS
            return EVENT_POLL_BACKLOG_INTERVAL_MS

        if processed or drained:
            self._event_poll_idle_interval_ms = EVENT_POLL_INTERVAL_MS
            return EVENT_POLL_INTERVAL_MS

        interval_ms = self._event_poll_idle_interval_ms
        self._event_poll_idle_interval_ms = min(
            EVENT_POLL_IDLE_MAX_INTERVAL_MS,
            interval_ms * 2,
        )
        return interval_ms

    def _drain_runtime_events(self, *, max_items: int | None = None) -> int:
        events = self._runtime.drain_events(max_items=max_items)
        if not events:
            return 0

        updates = RuntimeUiUpdateBatch()
        for event in events:
            self._apply_runtime_event(event, updates)
        self._apply_runtime_ui_updates(updates)
        return len(events)

    def _apply_runtime_event(
        self, event: object, updates: RuntimeUiUpdateBatch
    ) -> None:
        if isinstance(event, JobStatusChangedEvent):
            self._queue_full_session_view_refresh(updates, event.session_tab_id)
            updates.workspace_task_lists.add(event.workspace_tab_id)
            updates.queue_summary_workspace_ids.add(event.workspace_tab_id)
            return

        if isinstance(event, SessionIdConfirmedEvent):
            self._queue_session_summary_refresh(updates, event.session_tab_id)
            self._queue_session_history_refresh(updates, event.session_tab_id)
            updates.status_message = _tr_for(
                self, "status_session_id_confirmed", session_id=event.session_id
            )
            return

        if isinstance(event, LogAppendedEvent):
            self._queue_session_output_refresh(
                updates,
                event.session_tab_id,
                appended_job_id=event.job_id,
                appended_line=event.line,
            )
            return

        if isinstance(event, CompletedSessionUpdatedEvent):
            self._queue_session_summary_refresh(updates, event.summary.session_tab_id)
            self._queue_session_history_refresh(updates, event.summary.session_tab_id)
            updates.completed_workspace_paths.add(event.summary.workspace_path)
            return

        if isinstance(event, PersistenceIssueEvent):
            issue_message = localize_runtime_message(
                event.issue.message, _window_language(self)
            )
            updates.persistence_warnings.append(issue_message)
            updates.status_message = issue_message
            return

        if isinstance(event, WorkspaceOpenCompletedEvent):
            updates.opened_workspaces.append(event)
            updates.status_message = _tr_for(
                self,
                "status_workspace_opened",
                display_name=workspace_folder_display_name(event.workspace_path),
            )
            return

        if isinstance(event, QueueStartCompletedEvent):
            self._queue_start_pending_workspace_ids.discard(event.workspace_tab_id)
            updates.refresh_queue_summaries = True
            updates.status_message = _tr_for(
                self, "status_queue_started", display_name=event.display_name
            )
            return

        if isinstance(event, SettingsRetryCompletedEvent):
            updates.refresh_queue_summaries = True
            if event.retried_job_ids:
                updates.status_message = _tr_for(
                    self,
                    "status_settings_retry",
                    count=len(event.retried_job_ids),
                )
            else:
                updates.status_message = _tr_for(self, "status_settings_saved")
            return

        if isinstance(event, PresetAnalysisJobSubmittedEvent):
            self._apply_preset_analysis_job_submitted(event, updates)
            return

        if isinstance(event, PresetAnalysisJobSubmissionFailedEvent):
            self._apply_preset_analysis_job_submission_failed(event, updates)
            return

        if isinstance(event, PresetCandidateJobsRegisteredEvent):
            updates.workspace_task_lists.add(event.workspace_tab_id)
            updates.refresh_queue_summaries = True
            for session_tab_id in event.candidate_session_tab_ids:
                _queue_full_session_view_refresh(updates, session_tab_id)
                updates.candidate_auto_commit_states[session_tab_id] = (
                    event.auto_commit_enabled
                )
            candidate_count = len(event.candidate_session_tab_ids)
            job_count = len(event.registered_job_ids)
            updates.status_message = _tr_for(
                self,
                "status_candidate_jobs_registered",
                session_count=candidate_count,
                job_count=job_count,
            )
            return

        if isinstance(event, PresetPromptLanguagesLoadedEvent):
            status_message = self._apply_preset_language_options_loaded(event)
            if status_message is not None:
                updates.status_message = status_message
            return

        if isinstance(event, PresetPromptInstructionsLoadedEvent):
            status_message = self._apply_preset_instruction_options_loaded(event)
            if status_message is not None:
                updates.status_message = status_message
            return

        if isinstance(event, RuntimeActionFailedEvent):
            if (
                event.title == "큐 오류"
                and event.message == "큐를 시작할 수 없습니다."
                and event.workspace_tab_id is not None
            ):
                self._queue_start_pending_workspace_ids.discard(event.workspace_tab_id)
                updates.refresh_queue_summaries = True
            error_title = localize_runtime_message(event.title, _window_language(self))
            error_message = localize_runtime_message(
                event.message, _window_language(self)
            )
            updates.errors.append((error_title, error_message))
            updates.status_message = error_message
            return

        if isinstance(event, RuntimeActionWarningEvent):
            warning_title = localize_runtime_message(
                event.title, _window_language(self)
            )
            warning_message = localize_runtime_message(
                event.message, _window_language(self)
            )
            updates.warnings.append((warning_title, warning_message))
            updates.status_message = warning_message

    def _apply_runtime_ui_updates(self, updates: RuntimeUiUpdateBatch) -> None:
        for (
            session_tab_id,
            auto_commit_enabled,
        ) in updates.candidate_auto_commit_states.items():
            session_widgets = self._ensure_session_view(session_tab_id)
            session_widgets.auto_commit_var.set(auto_commit_enabled)
        workspace_task_list_ids = tuple(updates.workspace_task_lists)
        for workspace_tab_id in workspace_task_list_ids:
            self._sync_session_tab_order(workspace_tab_id)

        for session_tab_id in updates.full_session_views:
            if self._has_session_view(session_tab_id):
                self._refresh_session_view(session_tab_id)

        workspace_views = getattr(self, "_workspace_views", None)
        if workspace_views is not None:
            workspace_task_list_ids = tuple(
                workspace_tab_id
                for workspace_tab_id in workspace_task_list_ids
                if workspace_tab_id in workspace_views
            )
        if workspace_task_list_ids:
            runtime = getattr(self, "_runtime", None)
            if runtime is None:
                for workspace_tab_id in workspace_task_list_ids:
                    self._refresh_workspace_task_list(workspace_tab_id)
            else:
                list_jobs_by_workspace = getattr(
                    runtime, "list_jobs_by_workspace", None
                )
                if callable(list_jobs_by_workspace):
                    jobs_by_workspace = list_jobs_by_workspace(workspace_task_list_ids)
                else:
                    jobs_by_workspace = {
                        workspace_tab_id: runtime.list_workspace_jobs(workspace_tab_id)
                        for workspace_tab_id in workspace_task_list_ids
                    }
                for workspace_tab_id in workspace_task_list_ids:
                    self._refresh_workspace_task_list(
                        workspace_tab_id,
                        jobs=jobs_by_workspace.get(workspace_tab_id, ()),
                    )

        for workspace_path in updates.completed_workspace_paths:
            self._refresh_workspace_task_lists_for_workspace_path(workspace_path)

        for event in updates.opened_workspaces:
            self._apply_workspace_open_completed(event)

        for session_tab_id in updates.session_summaries:
            if (
                session_tab_id in updates.full_session_views
                or not self._has_session_view(session_tab_id)
            ):
                continue
            self._refresh_session_summary(session_tab_id)

        for session_tab_id in updates.session_histories:
            if (
                session_tab_id in updates.full_session_views
                or not self._has_session_view(session_tab_id)
            ):
                continue
            self._refresh_session_history(session_tab_id)

        for session_tab_id, output_append in updates.session_outputs.items():
            if (
                session_tab_id in updates.full_session_views
                or not self._has_session_view(session_tab_id)
            ):
                continue
            self._refresh_session_output(session_tab_id, output_append=output_append)

        if updates.refresh_queue_summaries:
            self._refresh_workspace_queue_summaries()
        elif updates.queue_summary_workspace_ids:
            self._refresh_workspace_queue_summaries(updates.queue_summary_workspace_ids)

        for warning_message in updates.persistence_warnings:
            messagebox.showwarning(
                _tr_for(self, "dialog_save_warning"), warning_message, parent=self
            )

        for title, warning_message in updates.warnings:
            messagebox.showwarning(title, warning_message, parent=self)

        for title, error_message in updates.errors:
            messagebox.showerror(title, error_message, parent=self)

        if updates.status_message is not None:
            self._set_status(updates.status_message)

    def _apply_workspace_open_completed(
        self, event: WorkspaceOpenCompletedEvent
    ) -> None:
        workspace_tab = self._runtime.get_workspace_tab(event.workspace_tab_id)
        self._ensure_workspace_view(workspace_tab.workspace_tab_id)
        self._refresh_workspace_task_list(workspace_tab.workspace_tab_id)
        self._select_workspace_tab(workspace_tab.workspace_tab_id)

        if not self._runtime.list_session_tabs(
            workspace_tab.workspace_tab_id, include_closed=False
        ):
            session_tab = self._runtime.open_session(workspace_tab.workspace_tab_id)
            self._ensure_session_view(session_tab.session_tab_id)
            self._refresh_session_view(session_tab.session_tab_id)
            self._select_session_tab(
                workspace_tab.workspace_tab_id, session_tab.session_tab_id
            )
        elif workspace_tab.active_session_tab_id is not None:
            self._select_session_tab(
                workspace_tab.workspace_tab_id, workspace_tab.active_session_tab_id
            )

        self._refresh_workspace_queue_summaries()
        self._refresh_saved_workspace_list()

    def _queue_full_session_view_refresh(
        self,
        updates: RuntimeUiUpdateBatch,
        session_tab_id: str,
    ) -> None:
        _queue_full_session_view_refresh(updates, session_tab_id)

    def _queue_session_summary_refresh(
        self,
        updates: RuntimeUiUpdateBatch,
        session_tab_id: str,
    ) -> None:
        if session_tab_id not in updates.full_session_views:
            updates.session_summaries.add(session_tab_id)

    def _queue_session_history_refresh(
        self,
        updates: RuntimeUiUpdateBatch,
        session_tab_id: str,
    ) -> None:
        if session_tab_id not in updates.full_session_views:
            updates.session_histories.add(session_tab_id)

    def _queue_session_output_refresh(
        self,
        updates: RuntimeUiUpdateBatch,
        session_tab_id: str,
        *,
        appended_job_id: str | None,
        appended_line: str | None,
    ) -> None:
        if session_tab_id in updates.full_session_views:
            return

        if appended_job_id is None or appended_line is None:
            updates.session_outputs[session_tab_id] = None
            return

        previous_update = updates.session_outputs.get(session_tab_id)
        if session_tab_id not in updates.session_outputs:
            updates.session_outputs[session_tab_id] = SessionOutputAppend(
                job_id=appended_job_id,
                lines=[appended_line.rstrip()],
            )
            return

        if previous_update is None or previous_update.job_id != appended_job_id:
            updates.session_outputs[session_tab_id] = None
            return

        previous_update.lines.append(appended_line.rstrip())

    def _refresh_saved_workspace_list(self) -> None:
        self._saved_workspaces_listbox.delete(0, tk.END)
        self._saved_workspace_paths = []
        for saved_workspace in self._runtime.list_saved_workspaces():
            last_selected = (
                saved_workspace.last_selected_at.astimezone().strftime("%Y-%m-%d %H:%M")
                if saved_workspace.last_selected_at is not None
                else _tr_for(self, "saved_workspace_never_selected")
            )
            self._saved_workspace_paths.append(saved_workspace.path)
            self._saved_workspaces_listbox.insert(
                tk.END,
                f"{saved_workspace.display_name} [{last_selected}]",
            )

    def _refresh_settings_summary(self) -> None:
        settings = self._runtime.settings
        self._ui_language = normalize_ui_language(settings.ui_language)
        self._settings_var.set(_format_settings_summary(settings))
        self._refresh_scheduled_run_display()

    def _refresh_scheduled_run_display(self) -> None:
        scheduled_at = self._scheduled_run_at
        is_pending = scheduled_at is not None
        self._scheduled_run_toggle_var.set(is_pending)
        if scheduled_at is None:
            self._scheduled_run_var.set(_tr_for(self, "scheduled_run_none"))
        else:
            self._scheduled_run_var.set(
                _tr_for(
                    self,
                    "scheduled_run_pending",
                    scheduled_at=_format_scheduled_run_time(scheduled_at),
                )
            )
        if self._scheduled_run_button is not None:
            _safe_configure(
                self._scheduled_run_button,
                text=_tr_for(self, "button_scheduled_run"),
                state="normal",
            )

    def _refresh_workspace_queue_summaries(
        self,
        workspace_tab_ids: Iterable[str] | None = None,
    ) -> None:
        if workspace_tab_ids is None:
            target_workspace_tab_ids = tuple(self._workspace_views)
        else:
            target_workspace_tab_ids = tuple(
                workspace_tab_id
                for workspace_tab_id in dict.fromkeys(workspace_tab_ids)
                if workspace_tab_id in self._workspace_views
            )
        if not target_workspace_tab_ids:
            return

        summarize_workspace_jobs = getattr(
            self._runtime,
            "summarize_workspace_jobs",
            None,
        )
        if callable(summarize_workspace_jobs):
            workspace_job_summaries = summarize_workspace_jobs(target_workspace_tab_ids)
            jobs_by_workspace = None
        else:
            workspace_job_summaries = None
            list_jobs_by_workspace = getattr(
                self._runtime, "list_jobs_by_workspace", None
            )
            if callable(list_jobs_by_workspace):
                jobs_by_workspace = list_jobs_by_workspace(target_workspace_tab_ids)
            else:
                jobs_by_workspace = {
                    workspace_tab_id: self._runtime.list_workspace_jobs(
                        workspace_tab_id
                    )
                    for workspace_tab_id in target_workspace_tab_ids
                }
        for workspace_tab_id in target_workspace_tab_ids:
            if workspace_job_summaries is not None:
                workspace_job_summary = workspace_job_summaries.get(workspace_tab_id)
                has_runnable_jobs = bool(
                    workspace_job_summary
                    and getattr(workspace_job_summary, "has_runnable_jobs", False)
                )
                has_running_job = bool(
                    workspace_job_summary and workspace_job_summary.has_running_job
                )
            else:
                workspace_jobs = jobs_by_workspace.get(workspace_tab_id, ())
                has_runnable_jobs = any(
                    job.status == JobStatus.QUEUED for job in workspace_jobs
                )
                has_running_job = any(
                    job.status == JobStatus.RUNNING for job in workspace_jobs
                )

            workspace_view = self._workspace_views[workspace_tab_id]
            queue_state = self._runtime.get_queue_state(workspace_tab_id)
            is_started = queue_state.status == QueueStatus.STARTED
            if is_started:
                self._queue_start_pending_workspace_ids.discard(workspace_tab_id)
            start_pending = self._queue_start_is_pending(workspace_tab_id)
            queue_label = (
                _tr_for(self, "queue_starting")
                if start_pending and not is_started
                else self._format_queue_label(queue_state)
            )
            workspace_view.queue_var.set(
                f"{_tr_for(self, 'queue_prefix')}: {queue_label}"
            )
            self._set_queue_toggle_state(
                workspace_view,
                active=is_started or start_pending,
                enabled=is_started or start_pending or has_runnable_jobs,
            )
            self._refresh_workspace_tab_indicator(
                workspace_tab_id,
                running=has_running_job,
            )

    def _queue_start_is_pending(self, workspace_tab_id: str) -> bool:
        return workspace_tab_id in getattr(
            self, "_queue_start_pending_workspace_ids", set()
        )

    def _set_queue_toggle_state(
        self,
        workspace_view: object,
        *,
        active: bool,
        enabled: bool = True,
    ) -> None:
        toggle_var = getattr(workspace_view, "queue_toggle_var", None)
        if toggle_var is not None:
            toggle_var.set(active)

        toggle_button = getattr(workspace_view, "queue_toggle_button", None)
        if toggle_button is not None:
            _safe_configure(
                toggle_button,
                text=_tr_for(self, "button_stop" if active else "button_start"),
                state="normal" if enabled else "disabled",
            )

    def _format_queue_label(self, queue_state) -> str:
        queue_label = (
            _tr_for(self, "queue_started")
            if queue_state.status == QueueStatus.STARTED
            else _tr_for(self, "queue_stopped")
        )
        if queue_state.last_stop_reason:
            stop_reason = _queue_stop_reason_label(
                queue_state.last_stop_reason, _window_language(self)
            )
            return f"{queue_label} ({stop_reason})"
        return queue_label

    def _rebuild_workspace_tabs(self) -> None:
        for workspace_tab in self._runtime.list_workspace_tabs(include_closed=False):
            workspace_view = self._ensure_workspace_view(workspace_tab.workspace_tab_id)
            workspace_view.path_var.set(workspace_tab.workspace_path)
            self._refresh_workspace_task_list(workspace_tab.workspace_tab_id)
            for session_tab in self._runtime.list_session_tabs(
                workspace_tab.workspace_tab_id,
                include_closed=False,
            ):
                self._ensure_session_view(session_tab.session_tab_id)
                self._refresh_session_view(session_tab.session_tab_id)

        self._refresh_workspace_queue_summaries()
        self._refresh_empty_state()

    def _refresh_empty_state(self) -> None:
        has_tabs = bool(self._workspace_views)
        if has_tabs:
            self._empty_state_label.grid_remove()
            self._workspace_notebook.grid()
        else:
            self._workspace_notebook.grid_remove()
            self._empty_state_label.grid()

    def _ensure_workspace_view(self, workspace_tab_id: str) -> WorkspaceWidgets:
        existing = self._workspace_views.get(workspace_tab_id)
        if existing is not None:
            return existing

        workspace_tab = self._runtime.get_workspace_tab(workspace_tab_id)
        frame = ttk.Frame(self._workspace_notebook, padding=self._ui_scale.padding(12))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        path_var = tk.StringVar(value=workspace_tab.workspace_path)
        ttk.Label(frame, textvariable=path_var).grid(row=0, column=0, sticky="w")
        queue_var = tk.StringVar(
            value=f"{_tr_for(self, 'queue_prefix')}: {_tr_for(self, 'queue_stopped')}"
        )
        queue_toggle_var = tk.BooleanVar(value=False)
        started_tab_image = self._create_started_tab_image(workspace_tab.display_name)

        button_row = ttk.Frame(frame)
        button_row.grid(row=0, column=1, sticky="e")
        ttk.Label(button_row, textvariable=queue_var).grid(
            row=0,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 12),
        )
        queue_toggle_button = ttk.Checkbutton(
            button_row,
            text=_tr_for(self, "button_start"),
            style="QueueToggle.Toolbutton",
            variable=queue_toggle_var,
            command=lambda target_id=workspace_tab_id: self._toggle_queue(target_id),
        )
        queue_toggle_button.grid(row=0, column=1, padx=self._ui_scale.padding(0, 12))
        for button_spec in WORKSPACE_SESSION_ACTION_BUTTONS:
            ttk.Button(
                button_row,
                text=_tr_for(self, button_spec.text_key),
                command=(
                    lambda target_id=workspace_tab_id, command_name=button_spec.command_name: getattr(
                        self,
                        command_name,
                    )(
                        target_id
                    )
                ),
            ).grid(row=0, column=button_spec.column, padx=self._ui_scale.padding(0, 8))

        content_pane = ttk.Panedwindow(frame, orient=tk.HORIZONTAL)
        content_pane.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="nsew",
            pady=self._ui_scale.padding(12, 0),
        )

        sessions_area = ttk.Frame(
            content_pane,
            width=self._ui_scale.px(WORKSPACE_SESSIONS_INITIAL_WIDTH),
        )
        sessions_area.columnconfigure(0, weight=1)
        sessions_area.rowconfigure(0, weight=1)

        session_notebook = ttk.Notebook(sessions_area)
        session_notebook.grid(row=0, column=0, sticky="nsew")
        session_notebook.bind(
            "<<NotebookTabChanged>>",
            lambda event, target_id=workspace_tab_id: self._on_session_tab_changed(
                event, target_id
            ),
        )

        workspace_jobs_area = ttk.Frame(
            content_pane,
            width=self._ui_scale.px(WORKSPACE_TASK_LIST_INITIAL_WIDTH),
        )
        workspace_jobs_area.grid_propagate(False)
        workspace_jobs_area.columnconfigure(0, weight=1)
        workspace_jobs_area.rowconfigure(1, weight=1)
        workspace_jobs_summary_var = tk.StringVar(
            value=_tr_for(self, "workspace_task_summary_empty")
        )
        ttk.Label(
            workspace_jobs_area,
            textvariable=workspace_jobs_summary_var,
        ).grid(row=0, column=0, sticky="w", pady=self._ui_scale.padding(0, 6))

        workspace_jobs_frame = ttk.Frame(workspace_jobs_area)
        workspace_jobs_frame.grid(row=1, column=0, sticky="nsew")
        workspace_jobs_frame.columnconfigure(0, weight=1)
        workspace_jobs_frame.rowconfigure(0, weight=1)

        workspace_job_columns = tuple(
            column_id for column_id, _heading, _width, _anchor in WORKSPACE_TASK_COLUMNS
        )
        workspace_jobs_tree = ttk.Treeview(
            workspace_jobs_frame,
            columns=workspace_job_columns,
            show="headings",
            height=18,
        )
        workspace_jobs_tree.grid(row=0, column=0, sticky="nsew")
        initial_column_widths = _calculate_workspace_task_column_widths(
            self._ui_scale.px(WORKSPACE_TASK_LIST_INITIAL_WIDTH)
        )
        for (column_id, heading, _base_width, anchor), width in zip(
            WORKSPACE_TASK_COLUMNS,
            initial_column_widths,
        ):
            workspace_jobs_tree.heading(
                column_id,
                text=_task_column_heading(column_id, _window_language(self), heading),
            )
            workspace_jobs_tree.column(
                column_id,
                width=width,
                minwidth=WORKSPACE_TASK_COLUMN_MIN_WIDTH,
                anchor=anchor,
                stretch=False,
            )
        workspace_jobs_scrollbar = ttk.Scrollbar(
            workspace_jobs_frame,
            orient="vertical",
            command=workspace_jobs_tree.yview,
        )
        workspace_jobs_scrollbar.grid(row=0, column=1, sticky="ns")
        workspace_jobs_tree.configure(yscrollcommand=workspace_jobs_scrollbar.set)
        workspace_jobs_tree.bind(
            "<Configure>",
            lambda event, tree=workspace_jobs_tree: self._resize_workspace_task_columns(
                tree,
                event.width,
            ),
        )
        workspace_jobs_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event, target_id=workspace_tab_id: self._on_workspace_job_selected(
                target_id
            ),
        )
        workspace_jobs_tree.bind(
            "<Button-3>",
            lambda event, target_id=workspace_tab_id: self._show_job_context_menu(
                event, target_id
            ),
        )
        workspace_jobs_tree.bind(
            "<Control-Button-1>",
            lambda event, target_id=workspace_tab_id: self._show_job_context_menu(
                event, target_id
            ),
        )

        content_pane.add(
            sessions_area,
            weight=1,
        )
        content_pane.add(
            workspace_jobs_area,
            weight=0,
        )

        self._workspace_notebook.add(frame, text=workspace_tab.display_name)
        workspace_view = WorkspaceWidgets(
            frame=frame,
            content_pane=content_pane,
            sessions_area=sessions_area,
            workspace_jobs_area=workspace_jobs_area,
            session_notebook=session_notebook,
            workspace_jobs_tree=workspace_jobs_tree,
            workspace_jobs_summary_var=workspace_jobs_summary_var,
            path_var=path_var,
            queue_var=queue_var,
            queue_toggle_var=queue_toggle_var,
            queue_toggle_button=queue_toggle_button,
            started_tab_image=started_tab_image,
        )
        self._workspace_views[workspace_tab_id] = workspace_view
        self._workspace_frame_map[str(frame)] = workspace_tab_id
        self._refresh_workspace_task_list(workspace_tab_id)
        self._refresh_workspace_queue_summaries()
        self._refresh_empty_state()
        return workspace_view

    def _create_started_tab_image(self, label: str) -> tk.PhotoImage:
        font = tkfont.nametofont("TkDefaultFont")
        width = max(font.measure(label) + self._ui_scale.px(28), self._ui_scale.px(48))
        height = max(
            font.metrics("linespace") + self._ui_scale.px(10), self._ui_scale.px(24)
        )
        image = tk.PhotoImage(master=self, width=width, height=height)
        image.put(WORKSPACE_TAB_ACTIVE_FILL, to=(0, 0, width, height))
        image.put(WORKSPACE_TAB_ACTIVE_BORDER, to=(0, 0, width, 1))
        image.put(WORKSPACE_TAB_ACTIVE_BORDER, to=(0, height - 1, width, height))
        image.put(WORKSPACE_TAB_ACTIVE_BORDER, to=(0, 0, 1, height))
        image.put(WORKSPACE_TAB_ACTIVE_BORDER, to=(width - 1, 0, width, height))
        return image

    def _refresh_workspace_tab_indicator(
        self, workspace_tab_id: str, *, running: bool
    ) -> None:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            return

        workspace_tab = self._runtime.get_workspace_tab(workspace_tab_id)
        tab_options = {"text": workspace_tab.display_name}
        if running:
            tab_options["image"] = str(workspace_view.started_tab_image)
            tab_options["compound"] = "center"
        else:
            tab_options["image"] = ""
            tab_options["compound"] = "none"

        try:
            self._workspace_notebook.tab(workspace_view.frame, **tab_options)
        except tk.TclError:
            LOGGER.debug(
                "Failed to refresh workspace tab indicator. workspace_tab_id=%s",
                workspace_tab_id,
            )

    def _refresh_session_tab_indicator(
        self, session_tab_id: str, *, started: bool
    ) -> None:
        try:
            session_tab = self._runtime.get_session_tab(session_tab_id)
        except KeyError:
            return

        workspace_view = self._workspace_views.get(session_tab.workspace_tab_id)
        if workspace_view is None:
            return

        session_widgets = workspace_view.session_views.get(session_tab_id)
        if session_widgets is None:
            return

        tab_options = {"text": session_tab.display_name}
        if started:
            tab_options["image"] = str(session_widgets.started_tab_image)
            tab_options["compound"] = "center"
        else:
            tab_options["image"] = ""
            tab_options["compound"] = "none"

        try:
            workspace_view.session_notebook.tab(session_widgets.frame, **tab_options)
        except tk.TclError:
            LOGGER.debug(
                "Failed to refresh session tab indicator. session_tab_id=%s",
                session_tab_id,
            )

    def _ensure_session_view(self, session_tab_id: str) -> SessionWidgets:
        session_tab = self._runtime.get_session_tab(session_tab_id)
        workspace_view = self._ensure_workspace_view(session_tab.workspace_tab_id)
        existing = workspace_view.session_views.get(session_tab_id)
        if existing is not None:
            return existing

        frame = ttk.Frame(
            workspace_view.session_notebook, padding=self._ui_scale.padding(10)
        )
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        session_id_var = tk.StringVar()
        activity_var = tk.StringVar()
        message_var = tk.StringVar()
        wait_reason_var = tk.StringVar()
        started_tab_image = self._create_started_tab_image(session_tab.display_name)

        info_frame = ttk.Frame(frame)
        info_frame.grid(row=0, column=0, sticky="ew")
        info_frame.columnconfigure(0, weight=1)
        session_id_label = ttk.Label(
            info_frame,
            textvariable=session_id_var,
            cursor="hand2",
        )
        session_id_label.grid(row=0, column=0, sticky="w")
        session_id_label.bind(
            "<Button-1>",
            lambda _event, target_id=session_tab_id: self._copy_session_id(target_id),
        )
        ttk.Label(info_frame, textvariable=activity_var).grid(
            row=1,
            column=0,
            sticky="w",
            pady=self._ui_scale.padding(4, 0),
        )
        message_label = ttk.Label(
            info_frame, textvariable=message_var, foreground=MESSAGE_LABEL_FOREGROUND
        )
        message_label.grid(
            row=2,
            column=0,
            sticky="w",
            pady=self._ui_scale.padding(4, 0),
        )
        message_label.grid_remove()
        wait_reason_label = ttk.Label(
            info_frame,
            textvariable=wait_reason_var,
            foreground=WAIT_REASON_LABEL_FOREGROUND,
        )
        wait_reason_label.grid(
            row=3,
            column=0,
            sticky="w",
            pady=self._ui_scale.padding(4, 0),
        )
        wait_reason_label.grid_remove()

        execution_option_frame = ttk.Frame(info_frame)
        execution_option_frame.grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="ne",
            padx=self._ui_scale.padding(8, 0),
        )
        execution_controls = self._build_execution_option_controls(
            execution_option_frame,
            session_tab_id=session_tab_id,
            start_column=0,
            on_agent_provider_selected=self._handle_session_agent_provider_selected,
            on_model_selected=self._handle_session_model_selected,
            on_reasoning_selected=self._handle_session_reasoning_selected,
        )
        ttk.Button(
            info_frame,
            text=_tr_for(self, "button_close_session"),
            command=lambda target_id=session_tab_id: self._close_session(target_id),
        ).grid(
            row=0, column=2, rowspan=2, sticky="ne", padx=self._ui_scale.padding(8, 0)
        )

        content_pane = ttk.Panedwindow(frame, orient=tk.VERTICAL)
        content_pane.grid(
            row=1, column=0, sticky="nsew", pady=self._ui_scale.padding(12, 0)
        )

        auto_commit_var = tk.BooleanVar(value=DEFAULT_AUTO_COMMIT_ENABLED)
        input_widgets = self._build_session_input_widgets(
            content_pane,
            workspace_tab_id=session_tab.workspace_tab_id,
            session_tab_id=session_tab_id,
            kind=session_tab.kind,
            auto_commit_var=auto_commit_var,
        )

        output_frame = ttk.LabelFrame(
            content_pane,
            text=_tr_for(self, "section_output"),
            height=self._ui_scale.px(OUTPUT_PANE_INITIAL_HEIGHT),
        )
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        output_notebook = ttk.Notebook(output_frame)
        output_notebook.grid(row=0, column=0, sticky="nsew")

        log_text = scrolledtext.ScrolledText(
            output_notebook, wrap="word", state="disabled"
        )
        history_text = scrolledtext.ScrolledText(
            output_notebook, wrap="word", state="disabled"
        )
        configure_text_widget(log_text, scale=self._ui_scale)
        configure_text_widget(history_text, scale=self._ui_scale)
        output_notebook.add(log_text, text=_tr_for(self, "tab_progress_log"))
        output_notebook.add(history_text, text=_tr_for(self, "tab_history"))

        content_pane.add(
            input_widgets.frame,
            weight=0,
        )
        content_pane.add(
            output_frame,
            weight=1,
        )

        session_widgets = SessionWidgets(
            frame=frame,
            content_pane=content_pane,
            prompt_frame=input_widgets.frame,
            output_frame=output_frame,
            started_tab_image=started_tab_image,
            prompt_text=input_widgets.prompt_text,
            log_text=log_text,
            history_text=history_text,
            auto_commit_var=auto_commit_var,
            session_id_var=session_id_var,
            activity_var=activity_var,
            message_var=message_var,
            wait_reason_var=wait_reason_var,
            message_label=message_label,
            wait_reason_label=wait_reason_label,
            execution_controls=execution_controls,
            preset_language_var=input_widgets.preset_language_var,
            preset_instruction_var=input_widgets.preset_instruction_var,
            preset_work_priority_var=input_widgets.preset_work_priority_var,
            preset_language_combobox=input_widgets.preset_language_combobox,
            preset_instruction_combobox=input_widgets.preset_instruction_combobox,
            preset_work_priority_combobox=input_widgets.preset_work_priority_combobox,
            preset_prompt_prefix_text=input_widgets.preset_prompt_prefix_text,
            preset_auto_commit_checkbutton=input_widgets.preset_auto_commit_checkbutton,
            preset_register_button=input_widgets.preset_register_button,
            preset_action_execution_controls=(
                input_widgets.preset_action_execution_controls
            ),
        )
        if session_widgets.preset_action_execution_controls is not None:
            session_widgets.preset_action_execution_controls.execution_options = (
                self._default_preset_action_execution_options_for_workspace(
                    session_tab.workspace_tab_id,
                    fallback=session_tab.execution_options,
                )
            )
        self._apply_output_font(session_widgets)

        insert_index = self._session_tab_insert_index(
            session_tab.workspace_tab_id,
            session_tab.session_tab_id,
        )
        workspace_view.session_notebook.insert(
            insert_index,
            frame,
            text=session_tab.display_name,
        )
        workspace_view.session_views[session_tab_id] = session_widgets
        self._session_frame_map[str(frame)] = (
            session_tab.workspace_tab_id,
            session_tab_id,
        )
        if session_tab.kind == SessionTabKind.PRESET:
            self._request_preset_language_options(session_tab_id)
        self._refresh_session_execution_option_controls(session_tab_id)
        self._refresh_preset_registration_controls(session_tab_id)
        return session_widgets

    def _session_tab_insert_index(
        self, workspace_tab_id: str, session_tab_id: str
    ) -> int | str:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            return tk.END

        insert_index = 0
        for ordered_session in self._runtime.list_session_tabs(
            workspace_tab_id,
            include_closed=False,
        ):
            if ordered_session.session_tab_id == session_tab_id:
                return _notebook_insert_position(
                    workspace_view.session_notebook,
                    insert_index,
                )
            if ordered_session.session_tab_id in workspace_view.session_views:
                insert_index += 1
        return _notebook_insert_position(
            workspace_view.session_notebook,
            insert_index,
        )

    def _sync_session_tab_order(self, workspace_tab_id: str) -> None:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            return

        ordered_session_ids = [
            session_tab.session_tab_id
            for session_tab in self._runtime.list_session_tabs(
                workspace_tab_id,
                include_closed=False,
            )
            if session_tab.session_tab_id in workspace_view.session_views
        ]
        for index, session_tab_id in enumerate(ordered_session_ids):
            session_widgets = workspace_view.session_views[session_tab_id]
            try:
                workspace_view.session_notebook.insert(index, session_widgets.frame)
            except tk.TclError:
                LOGGER.debug(
                    "Failed to reorder session tab. workspace_tab_id=%s session_tab_id=%s",
                    workspace_tab_id,
                    session_tab_id,
                )

    def _refresh_all_session_execution_option_controls(self) -> None:
        for workspace_view in self._workspace_views.values():
            for session_tab_id in tuple(workspace_view.session_views):
                self._refresh_session_execution_option_controls(session_tab_id)

    def _refresh_session_execution_option_controls(self, session_tab_id: str) -> None:
        if not self._has_session_view(session_tab_id):
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        execution_options = session_tab.execution_options
        locked = session_tab.execution_options_locked or (
            session_tab_id in self._preset_registration_pending_session_ids
        )
        control_values = self._resolve_execution_option_control_values(
            execution_options,
            locked=locked,
        )
        if not locked and control_values.execution_options != execution_options:
            self._runtime.set_session_execution_options(
                session_tab_id,
                control_values.execution_options,
            )

        self._apply_execution_option_control_values(
            controls=session_widgets.execution_controls,
            control_values=control_values,
            locked=locked,
        )
        self._refresh_preset_action_execution_option_controls(session_tab_id)

    def _refresh_preset_action_execution_option_controls(
        self,
        session_tab_id: str,
    ) -> None:
        if not self._has_session_view(session_tab_id):
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return

        locked = session_tab.execution_options_locked or (
            session_tab_id in self._preset_registration_pending_session_ids
        )
        execution_options = controls.execution_options
        control_values = self._resolve_execution_option_control_values(
            execution_options,
            locked=locked,
        )
        if not locked:
            controls.execution_options = control_values.execution_options
            self._remember_preset_action_execution_options_for_session(session_tab_id)

        self._apply_execution_option_control_values(
            controls=controls,
            control_values=control_values,
            locked=locked,
        )

    def _resolve_execution_option_control_values(
        self,
        execution_options: AgentExecutionOptions,
        *,
        locked: bool,
    ) -> ExecutionOptionControlValues:
        settings = self._runtime.settings
        if locked:
            provider_options = (
                self._agent_provider_option_for_value(execution_options.agent_provider),
            )
        else:
            provider_options = build_configured_agent_provider_select_options(
                execution_options.agent_provider,
                settings,
            )

        provider_value = execution_options.agent_provider
        resolved_execution_options = execution_options
        provider_values = {option.value for option in provider_options}
        if provider_options and provider_value not in provider_values:
            provider_value = provider_options[0].value
            if not locked:
                resolved_execution_options = AgentExecutionOptions(
                    agent_provider=provider_value,
                    model="",
                    reasoning_effort="",
                )
        elif not provider_options:
            provider_value = ""

        if provider_value:
            model_options = build_model_select_options(
                resolved_execution_options.model,
                agent_provider=provider_value,
                auto_label=_tr_for(self, "settings_auto"),
                saved_value_suffix=_tr_for(self, "settings_saved_value_suffix"),
            )
            model_value = self._option_value_or_default(
                model_options,
                resolved_execution_options.model,
            )
            reasoning_options = build_reasoning_select_options(
                resolved_execution_options.reasoning_effort,
                agent_provider=provider_value,
                model=model_value,
                auto_label=_tr_for(self, "settings_auto"),
                saved_value_suffix=_tr_for(self, "settings_saved_value_suffix"),
            )
            reasoning_value = self._option_value_or_default(
                reasoning_options,
                resolved_execution_options.reasoning_effort,
            )
        else:
            model_options = ()
            reasoning_options = ()
            model_value = ""
            reasoning_value = ""

        return ExecutionOptionControlValues(
            provider_options=provider_options,
            model_options=model_options,
            reasoning_options=reasoning_options,
            provider_value=provider_value,
            model_value=model_value,
            reasoning_value=reasoning_value,
            execution_options=resolved_execution_options,
        )

    @staticmethod
    def _apply_execution_option_control_values(
        *,
        controls: ExecutionOptionControls,
        control_values: ExecutionOptionControlValues,
        locked: bool,
    ) -> None:
        controls.agent_provider_options = control_values.provider_options
        controls.model_options = control_values.model_options
        controls.reasoning_options = control_values.reasoning_options
        controls.agent_provider_combobox.configure(
            values=[option.label for option in control_values.provider_options],
            state=(
                "readonly"
                if control_values.provider_options and not locked
                else "disabled"
            ),
        )
        controls.model_combobox.configure(
            values=[option.label for option in control_values.model_options],
            state=(
                "readonly"
                if (
                    control_values.provider_options
                    and control_values.model_options
                    and not locked
                )
                else "disabled"
            ),
        )
        controls.reasoning_combobox.configure(
            values=[option.label for option in control_values.reasoning_options],
            state=(
                "readonly"
                if (
                    control_values.provider_options
                    and control_values.reasoning_options
                    and not locked
                )
                else "disabled"
            ),
        )
        controls.agent_provider_var.set(
            find_option_label(
                control_values.provider_options,
                control_values.provider_value,
            )
            if control_values.provider_value
            else ""
        )
        controls.model_var.set(
            find_option_label(
                control_values.model_options,
                control_values.model_value,
            )
        )
        controls.reasoning_var.set(
            find_option_label(
                control_values.reasoning_options,
                control_values.reasoning_value,
            )
        )

    def _set_session_execution_option_controls_enabled(
        self,
        session_widgets: SessionWidgets,
        *,
        enabled: bool,
    ) -> None:
        self._set_execution_option_combobox_states(
            controls=session_widgets.execution_controls,
            enabled=enabled,
        )
        self._set_preset_action_execution_option_controls_enabled(
            session_widgets,
            enabled=enabled,
        )

    def _set_preset_action_execution_option_controls_enabled(
        self,
        session_widgets: SessionWidgets,
        *,
        enabled: bool,
    ) -> None:
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return
        self._set_execution_option_combobox_states(
            controls=controls,
            enabled=enabled,
        )

    @staticmethod
    def _set_execution_option_combobox_states(
        *,
        controls: ExecutionOptionControls,
        enabled: bool,
    ) -> None:
        controls.agent_provider_combobox.configure(
            state=(
                "readonly"
                if controls.agent_provider_options and enabled
                else "disabled"
            )
        )
        controls.model_combobox.configure(
            state="readonly" if controls.model_options and enabled else "disabled"
        )
        controls.reasoning_combobox.configure(
            state="readonly" if controls.reasoning_options and enabled else "disabled"
        )

    def _handle_session_agent_provider_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.execution_controls
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=False,
            include_reasoning=False,
        )
        if execution_options is None:
            return
        self._runtime.set_session_execution_options(
            session_tab_id,
            AgentExecutionOptions(agent_provider=execution_options.agent_provider),
        )
        self._refresh_session_execution_option_controls(session_tab_id)

    def _handle_session_model_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.execution_controls
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=False,
        )
        if execution_options is None:
            return
        self._runtime.set_session_execution_options(
            session_tab_id,
            AgentExecutionOptions(
                agent_provider=execution_options.agent_provider,
                model=execution_options.model,
            ),
        )
        self._refresh_session_execution_option_controls(session_tab_id)

    def _handle_session_reasoning_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.execution_controls
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=True,
        )
        if execution_options is None:
            return
        self._runtime.set_session_execution_options(
            session_tab_id,
            execution_options,
        )

    def _handle_preset_action_agent_provider_selected(
        self,
        session_tab_id: str,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=False,
            include_reasoning=False,
        )
        if execution_options is None:
            return
        controls.execution_options = AgentExecutionOptions(
            agent_provider=execution_options.agent_provider
        )
        self._remember_preset_action_execution_options_for_session(session_tab_id)
        self._refresh_preset_action_execution_option_controls(session_tab_id)

    def _handle_preset_action_model_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=False,
        )
        if execution_options is None:
            return
        controls.execution_options = AgentExecutionOptions(
            agent_provider=execution_options.agent_provider,
            model=execution_options.model,
        )
        self._remember_preset_action_execution_options_for_session(session_tab_id)
        self._refresh_preset_action_execution_option_controls(session_tab_id)

    def _handle_preset_action_reasoning_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=True,
        )
        if execution_options is None:
            return
        controls.execution_options = execution_options
        self._remember_preset_action_execution_options_for_session(session_tab_id)

    def _execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        self._refresh_session_execution_option_controls(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.execution_controls
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=True,
        )
        if execution_options is None:
            messagebox.showerror(
                _tr_for(self, "dialog_input_error"),
                _tr_for(self, "dialog_agent_provider_required"),
                parent=self,
            )
            return None
        self._runtime.set_session_execution_options(session_tab_id, execution_options)
        return execution_options

    def _preset_action_execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        self._refresh_preset_action_execution_option_controls(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return None
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=True,
        )
        if execution_options is None:
            messagebox.showerror(
                _tr_for(self, "dialog_input_error"),
                _tr_for(self, "dialog_agent_provider_required"),
                parent=self,
            )
            return None
        controls.execution_options = execution_options
        self._remember_preset_action_execution_options_for_session(session_tab_id)
        return execution_options

    def _selected_execution_options_from_controls(
        self,
        controls: ExecutionOptionControls,
        *,
        include_model: bool,
        include_reasoning: bool,
    ) -> AgentExecutionOptions | None:
        return self._selected_execution_options(
            provider_options=controls.agent_provider_options,
            provider_label=controls.agent_provider_var.get(),
            model_options=controls.model_options if include_model else (),
            model_label=controls.model_var.get() if include_model else "",
            reasoning_options=controls.reasoning_options if include_reasoning else (),
            reasoning_label=(controls.reasoning_var.get() if include_reasoning else ""),
        )

    def _selected_execution_options(
        self,
        *,
        provider_options: tuple[SelectOption, ...],
        provider_label: str,
        model_options: tuple[SelectOption, ...],
        model_label: str,
        reasoning_options: tuple[SelectOption, ...],
        reasoning_label: str,
    ) -> AgentExecutionOptions | None:
        provider = self._selected_option_value(provider_options, provider_label)
        if not provider:
            return None
        return AgentExecutionOptions(
            agent_provider=provider,
            model=self._selected_option_value(model_options, model_label),
            reasoning_effort=self._selected_option_value(
                reasoning_options,
                reasoning_label,
            ),
        )

    @staticmethod
    def _selected_option_value(
        options: tuple[SelectOption, ...],
        selected_label: str,
    ) -> str:
        for option in options:
            if option.label == selected_label:
                return option.value
        normalized_label = selected_label.strip()
        if normalized_label in {option.value for option in options}:
            return normalized_label
        return ""

    @staticmethod
    def _option_value_or_default(
        options: tuple[SelectOption, ...],
        value: str,
    ) -> str:
        if value in {option.value for option in options}:
            return value
        return options[0].value if options else ""

    @staticmethod
    def _agent_provider_option_for_value(provider_value: str) -> SelectOption:
        options = build_agent_provider_select_options(provider_value)
        label = find_option_label(options, provider_value) or provider_value
        return SelectOption(label=label, value=provider_value)

    def _build_execution_option_controls(
        self,
        parent: tk.Widget,
        *,
        session_tab_id: str,
        start_column: int,
        on_agent_provider_selected: Callable[[str], None],
        on_model_selected: Callable[[str], None],
        on_reasoning_selected: Callable[[str], None],
        trailing_combobox_pad: bool = False,
    ) -> ExecutionOptionControls:
        agent_provider_var = tk.StringVar()
        model_var = tk.StringVar()
        reasoning_var = tk.StringVar()

        ttk.Label(parent, text=_tr_for(self, "session_agent_provider")).grid(
            row=0,
            column=start_column,
            sticky="w",
            padx=self._ui_scale.padding(0, 4),
        )
        agent_provider_combobox = ttk.Combobox(
            parent,
            textvariable=agent_provider_var,
            values=(),
            state="disabled",
            width=SESSION_PROVIDER_COMBOBOX_WIDTH,
        )
        agent_provider_combobox.grid(
            row=0,
            column=start_column + 1,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        agent_provider_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event, target_id=session_tab_id: on_agent_provider_selected(
                target_id
            ),
        )

        ttk.Label(parent, text=_tr_for(self, "session_model")).grid(
            row=0,
            column=start_column + 2,
            sticky="w",
            padx=self._ui_scale.padding(0, 4),
        )
        model_combobox = ttk.Combobox(
            parent,
            textvariable=model_var,
            values=(),
            state="disabled",
            width=SESSION_MODEL_COMBOBOX_WIDTH,
        )
        model_combobox.grid(
            row=0,
            column=start_column + 3,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        model_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event, target_id=session_tab_id: on_model_selected(target_id),
        )

        ttk.Label(parent, text=_tr_for(self, "session_reasoning")).grid(
            row=0,
            column=start_column + 4,
            sticky="w",
            padx=self._ui_scale.padding(0, 4),
        )
        reasoning_combobox = ttk.Combobox(
            parent,
            textvariable=reasoning_var,
            values=(),
            state="disabled",
            width=SESSION_REASONING_COMBOBOX_WIDTH,
        )
        reasoning_combobox.grid(
            row=0,
            column=start_column + 5,
            sticky="w",
            padx=self._ui_scale.padding(0, 8) if trailing_combobox_pad else 0,
        )
        reasoning_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event, target_id=session_tab_id: on_reasoning_selected(target_id),
        )

        return ExecutionOptionControls(
            agent_provider_var=agent_provider_var,
            model_var=model_var,
            reasoning_var=reasoning_var,
            agent_provider_combobox=agent_provider_combobox,
            model_combobox=model_combobox,
            reasoning_combobox=reasoning_combobox,
        )

    def _build_session_input_widgets(
        self,
        parent: ttk.Panedwindow,
        *,
        workspace_tab_id: str,
        session_tab_id: str,
        kind: SessionTabKind,
        auto_commit_var: tk.BooleanVar,
    ) -> SessionInputWidgets:
        if _session_kind_uses_prompt_editor(kind):
            return self._build_prompt_editor_widgets(
                parent,
                session_tab_id=session_tab_id,
                auto_commit_var=auto_commit_var,
            )
        return self._build_preset_input_widgets(
            parent,
            workspace_tab_id=workspace_tab_id,
            session_tab_id=session_tab_id,
            auto_commit_var=auto_commit_var,
        )

    def _build_prompt_editor_widgets(
        self,
        parent: ttk.Panedwindow,
        *,
        session_tab_id: str,
        auto_commit_var: tk.BooleanVar,
    ) -> SessionInputWidgets:
        prompt_frame = ttk.LabelFrame(
            parent,
            text=_tr_for(self, "section_prompt"),
            height=self._ui_scale.px(PROMPT_PANE_INITIAL_HEIGHT),
        )
        prompt_frame.columnconfigure(0, weight=1)
        prompt_frame.rowconfigure(0, weight=1)
        prompt_text = scrolledtext.ScrolledText(prompt_frame, height=6, wrap="word")
        configure_text_widget(prompt_text, scale=self._ui_scale)
        bind_editable_text_context_menu(
            prompt_text,
            menu_parent=self,
            language=lambda: _window_language(self),
        )
        prompt_text.grid(row=0, column=0, sticky="nsew")

        prompt_action_frame = ttk.Frame(prompt_frame)
        prompt_action_frame.grid(
            row=1, column=0, sticky="e", pady=self._ui_scale.padding(8, 0)
        )
        ttk.Checkbutton(
            prompt_action_frame,
            text=_tr_for(self, "checkbox_auto_commit"),
            variable=auto_commit_var,
        ).grid(row=0, column=0, sticky="e", padx=self._ui_scale.padding(0, 8))
        ttk.Button(
            prompt_action_frame,
            text=_tr_for(self, "button_register"),
            command=lambda target_id=session_tab_id: self._submit_job_for_session(
                target_id
            ),
        ).grid(row=0, column=1, sticky="e")
        return SessionInputWidgets(frame=prompt_frame, prompt_text=prompt_text)

    def _build_preset_input_widgets(
        self,
        parent: ttk.Panedwindow,
        *,
        workspace_tab_id: str,
        session_tab_id: str,
        auto_commit_var: tk.BooleanVar,
    ) -> SessionInputWidgets:
        preset_frame = ttk.LabelFrame(
            parent,
            text=_tr_for(self, "section_preset"),
            height=self._ui_scale.px(PROMPT_PANE_INITIAL_HEIGHT),
        )
        preset_frame.columnconfigure(0, weight=1)
        preset_frame.rowconfigure(2, weight=1)

        languages: tuple[str, ...] = ()
        instructions: tuple[str, ...] = ()
        selected_language = ""
        selected_instruction = ""

        language_var = tk.StringVar(value=selected_language)
        instruction_var = tk.StringVar(value=selected_instruction)
        work_priority_var = tk.StringVar(
            value=self._default_preset_work_priority_for_workspace(workspace_tab_id)
        )
        selector_frame = ttk.Frame(preset_frame)
        selector_frame.grid(row=0, column=0, sticky="w")

        ttk.Label(selector_frame, text=_tr_for(self, "preset_language")).grid(
            row=0,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        language_combobox = ttk.Combobox(
            selector_frame,
            textvariable=language_var,
            values=languages,
            state="readonly" if languages else "disabled",
            width=PRESET_COMBOBOX_WIDTH,
        )
        language_combobox.grid(
            row=0,
            column=1,
            sticky="w",
            padx=self._ui_scale.padding(0, 14),
        )
        language_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event, target_id=session_tab_id: self._handle_preset_language_selected(
                target_id
            ),
        )

        ttk.Label(selector_frame, text=_tr_for(self, "preset_instruction")).grid(
            row=0,
            column=2,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        instruction_combobox = ttk.Combobox(
            selector_frame,
            textvariable=instruction_var,
            values=instructions,
            state="readonly" if instructions else "disabled",
            width=PRESET_COMBOBOX_WIDTH,
        )
        instruction_combobox.grid(
            row=0,
            column=3,
            sticky="w",
            padx=self._ui_scale.padding(0, 14),
        )
        instruction_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event, target_id=session_tab_id: self._handle_preset_instruction_selected(
                target_id
            ),
        )

        ttk.Label(selector_frame, text=_tr_for(self, "preset_priority")).grid(
            row=0,
            column=4,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        work_priority_combobox = ttk.Combobox(
            selector_frame,
            textvariable=work_priority_var,
            values=PRESET_WORK_PRIORITY_OPTIONS,
            state="readonly",
            width=PRESET_COMBOBOX_WIDTH,
        )
        work_priority_combobox.grid(row=0, column=5, sticky="w")
        work_priority_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event, target_id=session_tab_id: self._handle_preset_work_priority_selected(
                target_id
            ),
        )

        ttk.Label(preset_frame, text=_tr_for(self, "preset_prompt_prefix")).grid(
            row=1,
            column=0,
            sticky="w",
            pady=self._ui_scale.padding(8, 4),
        )
        prompt_prefix_text = scrolledtext.ScrolledText(
            preset_frame,
            height=4,
            wrap="word",
        )
        configure_text_widget(prompt_prefix_text, scale=self._ui_scale)
        bind_editable_text_context_menu(
            prompt_prefix_text,
            menu_parent=self,
            language=lambda: _window_language(self),
        )
        default_prompt_prefix = self._default_preset_prompt_prefix_for_workspace(
            workspace_tab_id
        )
        if default_prompt_prefix:
            prompt_prefix_text.insert("1.0", default_prompt_prefix)
        prompt_prefix_text.grid(row=2, column=0, sticky="nsew")

        preset_action_frame = ttk.Frame(preset_frame)
        preset_action_frame.grid(
            row=3,
            column=0,
            sticky="e",
            pady=self._ui_scale.padding(8, 0),
        )
        auto_commit_checkbutton = ttk.Checkbutton(
            preset_action_frame,
            text=_tr_for(self, "checkbox_auto_commit"),
            variable=auto_commit_var,
        )
        auto_commit_checkbutton.grid(
            row=0,
            column=0,
            sticky="e",
            padx=self._ui_scale.padding(0, 8),
        )
        preset_action_execution_controls = self._build_execution_option_controls(
            preset_action_frame,
            session_tab_id=session_tab_id,
            start_column=1,
            on_agent_provider_selected=(
                self._handle_preset_action_agent_provider_selected
            ),
            on_model_selected=self._handle_preset_action_model_selected,
            on_reasoning_selected=self._handle_preset_action_reasoning_selected,
            trailing_combobox_pad=True,
        )
        register_button = ttk.Button(
            preset_action_frame,
            text=_tr_for(self, "button_register"),
            command=lambda target_id=session_tab_id: self._submit_preset_job_for_session(
                target_id
            ),
        )
        register_button.grid(row=0, column=7, sticky="e")

        return SessionInputWidgets(
            frame=preset_frame,
            preset_language_var=language_var,
            preset_instruction_var=instruction_var,
            preset_work_priority_var=work_priority_var,
            preset_language_combobox=language_combobox,
            preset_instruction_combobox=instruction_combobox,
            preset_work_priority_combobox=work_priority_combobox,
            preset_prompt_prefix_text=prompt_prefix_text,
            preset_auto_commit_checkbutton=auto_commit_checkbutton,
            preset_register_button=register_button,
            preset_action_execution_controls=preset_action_execution_controls,
        )

    def _create_session_for_workspace(self, workspace_tab_id: str) -> None:
        try:
            session_tab = self._runtime.open_session(workspace_tab_id)
        except Exception:
            LOGGER.exception(
                "Failed to open session. workspace_tab_id=%s", workspace_tab_id
            )
            messagebox.showerror(
                _tr_for(self, "dialog_session_error"),
                _tr_for(self, "dialog_session_create_failed"),
                parent=self,
            )
            return

        self._ensure_session_view(session_tab.session_tab_id)
        self._refresh_session_view(session_tab.session_tab_id)
        self._select_workspace_tab(workspace_tab_id)
        self._select_session_tab(workspace_tab_id, session_tab.session_tab_id)
        self._set_status(
            _tr_for(
                self, "status_session_created", display_name=session_tab.display_name
            )
        )

    def _create_preset_session_for_workspace(self, workspace_tab_id: str) -> None:
        try:
            session_tab = self._runtime.open_preset_session(workspace_tab_id)
        except Exception:
            LOGGER.exception(
                "Failed to open preset session. workspace_tab_id=%s", workspace_tab_id
            )
            messagebox.showerror(
                _tr_for(self, "dialog_session_error"),
                _tr_for(self, "dialog_preset_create_failed"),
                parent=self,
            )
            return

        self._ensure_session_view(session_tab.session_tab_id)
        self._refresh_session_view(session_tab.session_tab_id)
        self._select_workspace_tab(workspace_tab_id)
        self._select_session_tab(workspace_tab_id, session_tab.session_tab_id)
        self._set_status(
            _tr_for(
                self, "status_preset_created", display_name=session_tab.display_name
            )
        )

    def _open_bulk_import_dialog_for_workspace(self, workspace_tab_id: str) -> None:
        dialog = BulkPromptImportDialog(
            self,
            initial_auto_commit=DEFAULT_AUTO_COMMIT_ENABLED,
            ui_language=_window_language(self),
        )
        dialog_result = dialog.show_modal()
        if dialog_result is None:
            return

        try:
            prompts = extract_text_import_prompts(dialog_result.raw_text)
        except ValueError as error:
            messagebox.showerror(
                _tr_for(self, "dialog_import_error"),
                localize_runtime_message(str(error), _window_language(self)),
                parent=self,
            )
            return

        try:
            import_result = self._runtime.import_prompt_sessions(
                workspace_tab_id,
                prompts,
                auto_commit_enabled=dialog_result.auto_commit_enabled,
            )
        except Exception:
            LOGGER.exception(
                "Failed to import prompt sessions. workspace_tab_id=%s",
                workspace_tab_id,
            )
            messagebox.showerror(
                _tr_for(self, "dialog_import_error"),
                _tr_for(self, "dialog_import_failed"),
                parent=self,
            )
            return

        if not import_result.registrations:
            self._set_status(_tr_for(self, "status_import_empty"))
            return

        for registration in import_result.registrations:
            session_widgets = self._ensure_session_view(
                registration.session_tab.session_tab_id
            )
            session_widgets.auto_commit_var.set(dialog_result.auto_commit_enabled)

        self._drain_runtime_events()
        for registration in import_result.registrations:
            self._refresh_session_view(
                registration.session_tab.session_tab_id,
                preferred_job_id=registration.prompt_job.job_id,
            )

        first_registration = import_result.registrations[0]
        self._select_workspace_tab(workspace_tab_id)
        self._select_session_tab(
            workspace_tab_id,
            first_registration.session_tab.session_tab_id,
        )
        self._refresh_workspace_task_list(
            workspace_tab_id,
            preferred_job_id=first_registration.prompt_job.job_id,
        )
        self._refresh_workspace_queue_summaries()
        session_count = len(import_result.registrations)
        job_count = len(import_result.registered_jobs)
        self._set_status(
            _tr_for(
                self,
                "status_import_registered",
                session_count=session_count,
                job_count=job_count,
            )
        )

    def _close_session(self, session_tab_id: str) -> None:
        has_running_job = self._session_has_running_job(session_tab_id)
        pending_job_count = self._session_pending_job_count(session_tab_id)
        if not self._confirm_tab_close(
            title=_tr_for(self, "button_close_session"),
            has_running_job=has_running_job,
            pending_job_count=pending_job_count,
        ):
            return

        try:
            result = self._runtime.close_session(session_tab_id)
        except Exception:
            LOGGER.exception(
                "Failed to close session. session_tab_id=%s", session_tab_id
            )
            messagebox.showerror(
                _tr_for(self, "dialog_session_error"),
                _tr_for(self, "dialog_session_close_failed"),
                parent=self,
            )
            return

        workspace_tab_id = result.session_tab.workspace_tab_id
        self._queue_start_pending_workspace_ids.discard(workspace_tab_id)
        self._remove_session_view(session_tab_id)
        self._refresh_workspace_task_list(workspace_tab_id)
        self._refresh_workspace_queue_summaries()
        if result.canceled_job is not None and result.removed_queued_job_count > 0:
            self._set_status(
                _tr_for(
                    self,
                    "status_session_closed_canceled_and_removed",
                    count=result.removed_queued_job_count,
                )
            )
        elif result.canceled_job is not None:
            self._set_status(_tr_for(self, "status_session_closed_canceled"))
        elif result.removed_queued_job_count > 0:
            self._set_status(
                _tr_for(
                    self,
                    "status_session_closed_removed",
                    count=result.removed_queued_job_count,
                )
            )
        else:
            self._set_status(_tr_for(self, "status_session_closed"))

    def _close_active_workspace(self) -> None:
        selected = self._workspace_notebook.select()
        if not selected:
            self._set_status(_tr_for(self, "status_select_workspace_to_close"))
            return

        workspace_tab_id = self._workspace_frame_map.get(selected)
        if workspace_tab_id is None:
            self._set_status(_tr_for(self, "status_selected_workspace_missing"))
            return

        self._close_workspace(workspace_tab_id)

    def _close_workspace(self, workspace_tab_id: str) -> None:
        has_running_job = self._workspace_has_running_job(workspace_tab_id)
        pending_job_count = self._workspace_pending_job_count(workspace_tab_id)
        if not self._confirm_tab_close(
            title=_tr_for(self, "button_close_workspace"),
            has_running_job=has_running_job,
            pending_job_count=pending_job_count,
        ):
            return

        try:
            result = self._runtime.close_workspace(workspace_tab_id)
        except Exception:
            LOGGER.exception(
                "Failed to close workspace. workspace_tab_id=%s", workspace_tab_id
            )
            messagebox.showerror(
                _tr_for(self, "dialog_workspace_error"),
                _tr_for(self, "dialog_workspace_close_failed"),
                parent=self,
            )
            return

        self._queue_start_pending_workspace_ids.discard(workspace_tab_id)
        self._remove_workspace_view(workspace_tab_id)
        self._refresh_workspace_queue_summaries()
        if result.canceled_job is not None and result.removed_queued_job_count > 0:
            self._set_status(
                _tr_for(
                    self,
                    "status_workspace_closed_canceled_and_removed",
                    count=result.removed_queued_job_count,
                )
            )
        elif result.canceled_job is not None:
            self._set_status(_tr_for(self, "status_workspace_closed_canceled"))
        elif result.removed_queued_job_count > 0:
            self._set_status(
                _tr_for(
                    self,
                    "status_workspace_closed_removed",
                    count=result.removed_queued_job_count,
                )
            )
        else:
            self._set_status(_tr_for(self, "status_workspace_closed"))

    def _open_workspace_from_dialog(self) -> None:
        workspace_path = filedialog.askdirectory(
            parent=self, title=_tr_for(self, "dialog_workspace_select")
        )
        if not workspace_path:
            return
        self._open_workspace_path(workspace_path)

    def _open_selected_saved_workspace(self) -> None:
        selection = self._saved_workspaces_listbox.curselection()
        if not selection:
            self._set_status(_tr_for(self, "status_select_workspace_to_open"))
            return

        selection_index = selection[0]
        if selection_index >= len(self._saved_workspace_paths):
            self._refresh_saved_workspace_list()
            self._set_status(_tr_for(self, "status_selected_workspace_missing"))
            return

        self._open_workspace_path(self._saved_workspace_paths[selection_index])

    def _delete_selected_saved_workspace(self) -> None:
        selection = self._saved_workspaces_listbox.curselection()
        if not selection:
            self._set_status(_tr_for(self, "status_select_workspace_to_delete"))
            return

        selection_index = selection[0]
        if selection_index >= len(self._saved_workspace_paths):
            self._refresh_saved_workspace_list()
            self._set_status(_tr_for(self, "status_selected_workspace_missing"))
            return

        workspace_path = self._saved_workspace_paths[selection_index]
        display_name = workspace_folder_display_name(workspace_path)
        if self._runtime.workspace_path_has_running_job(workspace_path):
            if not messagebox.askyesno(
                _tr_for(self, "dialog_workspace_delete"),
                _tr_for(
                    self,
                    "dialog_workspace_delete_running",
                    display_name=display_name,
                ),
                parent=self,
            ):
                return

        try:
            deleted_workspace = self._runtime.delete_saved_workspace(workspace_path)
        except Exception:
            LOGGER.exception(
                "Failed to delete saved workspace. workspace_path=%s",
                workspace_path,
            )
            messagebox.showerror(
                _tr_for(self, "dialog_workspace_delete_error"),
                _tr_for(self, "dialog_workspace_delete_failed"),
                parent=self,
            )
            return

        self._refresh_saved_workspace_list()
        if deleted_workspace is None:
            self._set_status(_tr_for(self, "status_workspace_already_removed"))
            return

        self._set_status(
            _tr_for(
                self,
                "status_workspace_removed",
                display_name=deleted_workspace.display_name,
            )
        )

    def _on_saved_workspace_double_click(self, _event: tk.Event[tk.Misc]) -> None:
        self._open_selected_saved_workspace()

    def _open_workspace_path(self, workspace_path: str) -> None:
        if self._request_workspace_open(workspace_path):
            self._set_status(
                _tr_for(
                    self,
                    "status_workspace_opening",
                    display_name=workspace_folder_display_name(workspace_path),
                )
            )

    def _request_workspace_open(self, workspace_path: str) -> bool:
        try:
            self._runtime.open_workspace_in_background(workspace_path)
        except Exception:
            LOGGER.exception(
                "Failed to request workspace open. workspace_path=%s", workspace_path
            )
            messagebox.showerror(
                _tr_for(self, "dialog_workspace_error"),
                _tr_for(self, "dialog_workspace_open_failed"),
                parent=self,
            )
            return False
        return True

    def _configure_saved_workspace_drop_targets(self, *widgets: tk.Misc) -> None:
        if TkinterDnD is None or DND_FILES is None:
            LOGGER.info(
                "Workspace drag-and-drop is unavailable because tkinterdnd2 is not installed."
            )
            return

        try:
            TkinterDnD._require(self)
            for widget in widgets:
                drop_target_register = getattr(widget, "drop_target_register", None)
                dnd_bind = getattr(widget, "dnd_bind", None)
                if drop_target_register is None or dnd_bind is None:
                    continue
                drop_target_register(DND_FILES)
                dnd_bind("<<DropEnter>>", self._accept_saved_workspace_drop)
                dnd_bind("<<DropPosition>>", self._accept_saved_workspace_drop)
                dnd_bind("<<Drop>>", self._on_saved_workspace_drop)
        except Exception:
            LOGGER.info(
                "Workspace drag-and-drop target registration failed.", exc_info=True
            )

    def _accept_saved_workspace_drop(self, _event: object) -> str:
        return DND_COPY_ACTION

    def _on_saved_workspace_drop(self, event: object) -> str:
        workspace_paths = _split_dropped_workspace_paths(
            self, getattr(event, "data", "")
        )
        if not workspace_paths:
            self._set_status(_tr_for(self, "status_no_dropped_workspace"))
            return DND_COPY_ACTION

        requested_paths: list[str] = []
        for workspace_path in workspace_paths:
            if self._request_workspace_open(workspace_path):
                requested_paths.append(workspace_path)

        if len(requested_paths) == 1:
            self._set_status(
                _tr_for(
                    self,
                    "status_workspace_registering",
                    display_name=workspace_folder_display_name(requested_paths[0]),
                )
            )
        elif len(requested_paths) > 1:
            self._set_status(
                _tr_for(
                    self, "status_workspaces_registering", count=len(requested_paths)
                )
            )
        return DND_COPY_ACTION

    def _open_settings_dialog(self) -> None:
        previous_settings = self._runtime.settings
        previous_language = normalize_ui_language(previous_settings.ui_language)
        previous_output_font_size = previous_settings.output_font_size
        dialog = SettingsDialog(
            self,
            self._runtime.settings,
            app_name=APP_NAME,
            app_version=APP_VERSION,
            agent_cli_version_loader=load_agent_cli_version_text,
        )
        result = dialog.show_modal()
        if result is None:
            return

        try:
            self._runtime.update_settings(result)
        except Exception:
            LOGGER.exception("Failed to update settings.")
            messagebox.showerror(
                _tr_for(self, "dialog_settings_error"),
                _tr_for(self, "dialog_settings_save_failed"),
                parent=self,
            )
            return

        next_settings = self._runtime.settings
        next_language = normalize_ui_language(next_settings.ui_language)
        output_font_size_changed = (
            next_settings.output_font_size != previous_output_font_size
        )
        self._ui_language = next_language
        if next_language != previous_language:
            self._rebuild_static_ui()
        else:
            self._refresh_settings_summary()
            if output_font_size_changed:
                self._apply_output_font_to_all_sessions()
            self._refresh_all_session_execution_option_controls()
            self._refresh_workspace_queue_summaries()
        self._set_status(_tr_for(self, "status_settings_saved"))

    def _submit_job_for_session(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        if session_widgets.prompt_text is None:
            self._submit_preset_job_for_session(session_tab_id)
            return

        prompt = session_widgets.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showerror(
                _tr_for(self, "dialog_input_error"),
                _tr_for(self, "dialog_prompt_required"),
                parent=self,
            )
            return

        execution_options = self._execution_options_for_registration(session_tab_id)
        if execution_options is None:
            return

        try:
            job = self._runtime.submit_job(
                session_tab_id,
                prompt,
                execution_options=execution_options,
            )
        except Exception:
            LOGGER.exception("Failed to submit job. session_tab_id=%s", session_tab_id)
            messagebox.showerror(
                _tr_for(self, "dialog_job_error"),
                _tr_for(self, "dialog_job_register_failed"),
                parent=self,
            )
            return

        auto_commit_job: Job | None = None
        auto_commit_failed = False
        if session_widgets.auto_commit_var.get():
            try:
                auto_commit_job = self._runtime.submit_job(
                    session_tab_id,
                    AUTO_COMMIT_PROMPT,
                    execution_options=execution_options,
                )
            except Exception:
                auto_commit_failed = True
                LOGGER.exception(
                    "Failed to submit auto-commit job. session_tab_id=%s primary_job_id=%s",
                    session_tab_id,
                    job.job_id,
                )
                messagebox.showerror(
                    _tr_for(self, "dialog_job_error"),
                    _tr_for(self, "dialog_auto_commit_failed"),
                    parent=self,
                )

        session_widgets.prompt_text.delete("1.0", tk.END)
        self._drain_runtime_events()
        self._refresh_session_execution_option_controls(session_tab_id)
        self._refresh_session_view(session_tab_id, preferred_job_id=job.job_id)
        self._refresh_workspace_task_list(
            job.workspace_tab_id, preferred_job_id=job.job_id
        )
        self._refresh_workspace_queue_summaries()
        if auto_commit_job is not None:
            self._set_status(
                _tr_for(
                    self,
                    "status_job_auto_commit_registered",
                    job_id=job.job_id,
                    auto_commit_job_id=auto_commit_job.job_id,
                )
            )
        elif auto_commit_failed:
            self._set_status(
                _tr_for(
                    self,
                    "status_job_registered_auto_commit_failed",
                    job_id=job.job_id,
                )
            )
        else:
            self._set_status(_tr_for(self, "status_job_registered", job_id=job.job_id))

    def _submit_preset_job_for_session(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        language_var = session_widgets.preset_language_var
        instruction_var = session_widgets.preset_instruction_var
        work_priority_var = session_widgets.preset_work_priority_var
        if language_var is None or instruction_var is None or work_priority_var is None:
            messagebox.showerror(
                _tr_for(self, "dialog_input_error"),
                _tr_for(self, "dialog_preset_inputs_missing"),
                parent=self,
            )
            return

        language = language_var.get().strip()
        instruction = instruction_var.get().strip()
        work_priority = work_priority_var.get().strip()
        if not language or not instruction or not work_priority:
            messagebox.showerror(
                _tr_for(self, "dialog_input_error"),
                _tr_for(self, "dialog_preset_inputs_required"),
                parent=self,
            )
            return

        execution_options = self._execution_options_for_registration(session_tab_id)
        if execution_options is None:
            return
        candidate_execution_options = (
            self._preset_action_execution_options_for_registration(session_tab_id)
        )
        if candidate_execution_options is None:
            return

        analysis_prompt_prefix = self._preset_prompt_prefix_for_session(session_tab_id)
        auto_commit_enabled = session_widgets.auto_commit_var.get()
        try:
            submit_in_background = getattr(
                self._runtime,
                "submit_preset_analysis_job_in_background",
                None,
            )
            if callable(submit_in_background):
                submit_in_background(
                    session_tab_id,
                    language=language,
                    instruction=instruction,
                    work_priority=work_priority,
                    analysis_prompt_prefix=analysis_prompt_prefix,
                    auto_commit_enabled=auto_commit_enabled,
                    execution_options=execution_options,
                    candidate_execution_options=candidate_execution_options,
                )
                self._preset_registration_pending_session_ids.add(session_tab_id)
            else:
                job = self._runtime.submit_preset_analysis_job(
                    session_tab_id,
                    language=language,
                    instruction=instruction,
                    work_priority=work_priority,
                    analysis_prompt_prefix=analysis_prompt_prefix,
                    auto_commit_enabled=auto_commit_enabled,
                    execution_options=execution_options,
                    candidate_execution_options=candidate_execution_options,
                )
                self._remember_preset_prompt_prefix_for_workspace(
                    job.workspace_tab_id,
                    analysis_prompt_prefix,
                )
                self._drain_runtime_events()
                self._refresh_session_view(session_tab_id, preferred_job_id=job.job_id)
                self._refresh_workspace_task_list(
                    job.workspace_tab_id,
                    preferred_job_id=job.job_id,
                )
                self._refresh_workspace_queue_summaries()
                self._set_status(
                    _tr_for(self, "status_preset_registered", job_id=job.job_id)
                )
        except ValueError as exc:
            LOGGER.info(
                "Failed to submit preset analysis job. session_tab_id=%s language=%s instruction=%s",
                session_tab_id,
                language,
                instruction,
                exc_info=True,
            )
            messagebox.showerror(
                _tr_for(self, "dialog_input_error"),
                str(exc),
                parent=self,
            )
            return
        except Exception:
            LOGGER.exception(
                "Failed to submit preset analysis job. session_tab_id=%s language=%s instruction=%s",
                session_tab_id,
                language,
                instruction,
            )
            messagebox.showerror(
                _tr_for(self, "dialog_job_error"),
                _tr_for(self, "dialog_preset_register_failed"),
                parent=self,
            )
            return

        self._set_preset_registration_controls_enabled(session_widgets, enabled=False)
        self._set_session_execution_option_controls_enabled(
            session_widgets,
            enabled=False,
        )

    def _apply_preset_analysis_job_submitted(
        self,
        event: PresetAnalysisJobSubmittedEvent,
        updates: RuntimeUiUpdateBatch,
    ) -> None:
        self._preset_registration_pending_session_ids.discard(event.session_tab_id)
        self._remember_preset_prompt_prefix_for_workspace(
            event.workspace_tab_id,
            event.analysis_prompt_prefix,
        )
        if self._has_session_view(event.session_tab_id):
            session_widgets = self._get_session_widgets(event.session_tab_id)
            self._set_preset_registration_controls_enabled(
                session_widgets,
                enabled=False,
            )
            self._refresh_session_execution_option_controls(event.session_tab_id)
            _queue_full_session_view_refresh(updates, event.session_tab_id)
        updates.workspace_task_lists.add(event.workspace_tab_id)
        updates.refresh_queue_summaries = True
        updates.status_message = _tr_for(
            self,
            "status_preset_registered",
            job_id=event.job_id,
        )

    def _apply_preset_analysis_job_submission_failed(
        self,
        event: PresetAnalysisJobSubmissionFailedEvent,
        updates: RuntimeUiUpdateBatch,
    ) -> None:
        self._preset_registration_pending_session_ids.discard(event.session_tab_id)
        if self._has_session_view(event.session_tab_id):
            self._refresh_preset_registration_controls(event.session_tab_id)
            self._refresh_session_execution_option_controls(event.session_tab_id)
        error_title = localize_runtime_message(event.title, _window_language(self))
        error_message = localize_runtime_message(event.message, _window_language(self))
        updates.errors.append((error_title, error_message))
        updates.status_message = error_message

    def _next_preset_option_request_id(self) -> int:
        self._preset_option_request_sequence += 1
        return self._preset_option_request_sequence

    def _request_preset_language_options(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        if session_widgets.preset_language_var is None:
            return

        session_widgets.preset_language_var.set("")
        if session_widgets.preset_instruction_var is not None:
            session_widgets.preset_instruction_var.set("")
        if session_widgets.preset_language_combobox is not None:
            session_widgets.preset_language_combobox.configure(
                values=(),
                state="disabled",
            )
        if session_widgets.preset_instruction_combobox is not None:
            session_widgets.preset_instruction_combobox.configure(
                values=(),
                state="disabled",
            )

        session_tab = self._runtime.get_session_tab(session_tab_id)
        request_id = self._next_preset_option_request_id()
        self._preset_language_request_ids[session_tab_id] = request_id
        self._runtime.load_preset_languages_in_background(
            request_id=request_id,
            session_tab_id=session_tab_id,
            workspace_tab_id=session_tab.workspace_tab_id,
        )

    def _request_preset_instruction_options(
        self,
        session_tab_id: str,
        *,
        language: str | None = None,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        language_var = session_widgets.preset_language_var
        instruction_var = session_widgets.preset_instruction_var
        instruction_combobox = session_widgets.preset_instruction_combobox
        if (
            language_var is None
            or instruction_var is None
            or instruction_combobox is None
        ):
            return

        selected_language = (
            language if language is not None else language_var.get().strip()
        )
        instruction_var.set("")
        instruction_combobox.configure(values=(), state="disabled")
        self._preset_instruction_request_ids.pop(session_tab_id, None)
        if not selected_language:
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        request_id = self._next_preset_option_request_id()
        self._preset_instruction_request_ids[session_tab_id] = request_id
        self._runtime.load_preset_instructions_in_background(
            request_id=request_id,
            session_tab_id=session_tab_id,
            workspace_tab_id=session_tab.workspace_tab_id,
            language=selected_language,
        )

    def _apply_preset_language_options_loaded(
        self,
        event: PresetPromptLanguagesLoadedEvent,
    ) -> str | None:
        if (
            self._preset_language_request_ids.get(event.session_tab_id)
            != event.request_id
        ):
            return None
        self._preset_language_request_ids.pop(event.session_tab_id, None)
        if not self._has_session_view(event.session_tab_id):
            return None

        session_widgets = self._get_session_widgets(event.session_tab_id)
        language_var = session_widgets.preset_language_var
        language_combobox = session_widgets.preset_language_combobox
        instruction_var = session_widgets.preset_instruction_var
        instruction_combobox = session_widgets.preset_instruction_combobox
        if language_var is None or language_combobox is None:
            return None

        if event.error_message is not None:
            language_var.set("")
            language_combobox.configure(values=(), state="disabled")
            if instruction_var is not None:
                instruction_var.set("")
            if instruction_combobox is not None:
                instruction_combobox.configure(values=(), state="disabled")
            return _tr_for(self, "status_preset_languages_failed")

        languages = event.languages
        selected_language = self._default_preset_language_for_workspace(
            event.workspace_tab_id,
            languages,
        )
        language_var.set(selected_language)
        is_registered = self._preset_registration_is_locked(event.session_tab_id)
        language_combobox.configure(
            values=languages,
            state="readonly" if languages and not is_registered else "disabled",
        )
        if selected_language:
            self._request_preset_instruction_options(
                event.session_tab_id,
                language=selected_language,
            )
        elif instruction_var is not None and instruction_combobox is not None:
            instruction_var.set("")
            instruction_combobox.configure(values=(), state="disabled")
        return None

    def _apply_preset_instruction_options_loaded(
        self,
        event: PresetPromptInstructionsLoadedEvent,
    ) -> str | None:
        if (
            self._preset_instruction_request_ids.get(event.session_tab_id)
            != event.request_id
        ):
            return None
        self._preset_instruction_request_ids.pop(event.session_tab_id, None)
        if not self._has_session_view(event.session_tab_id):
            return None

        session_widgets = self._get_session_widgets(event.session_tab_id)
        language_var = session_widgets.preset_language_var
        instruction_var = session_widgets.preset_instruction_var
        instruction_combobox = session_widgets.preset_instruction_combobox
        if (
            language_var is None
            or instruction_var is None
            or instruction_combobox is None
        ):
            return None
        if language_var.get().strip() != event.language:
            return None

        if event.error_message is not None:
            instruction_var.set("")
            instruction_combobox.configure(values=(), state="disabled")
            return _tr_for(self, "status_preset_instructions_failed")

        instructions = event.instructions
        is_registered = self._preset_registration_is_locked(event.session_tab_id)
        instruction_combobox.configure(
            values=instructions,
            state="readonly" if instructions and not is_registered else "disabled",
        )
        instruction_var.set(
            self._default_preset_instruction_for_workspace(
                event.workspace_tab_id,
                event.language,
                instructions,
            )
        )
        return None

    def _default_preset_language_for_workspace(
        self,
        workspace_tab_id: str,
        languages: tuple[str, ...],
    ) -> str:
        if not languages:
            return ""

        key = self._workspace_preset_language_key(workspace_tab_id)
        remembered_language = self._workspace_preset_languages.get(key)
        if remembered_language in languages:
            return remembered_language
        return languages[0]

    def _default_preset_instruction_for_workspace(
        self,
        workspace_tab_id: str,
        language: str,
        instructions: tuple[str, ...],
    ) -> str:
        if not instructions:
            return ""

        key = self._workspace_preset_instruction_key(workspace_tab_id, language)
        remembered_instruction = self._workspace_preset_instructions.get(key)
        if remembered_instruction in instructions:
            return remembered_instruction
        return instructions[0]

    def _default_preset_work_priority_for_workspace(self, workspace_tab_id: str) -> str:
        key = self._workspace_preset_language_key(workspace_tab_id)
        remembered_priority = self._workspace_preset_work_priorities.get(key)
        if remembered_priority in PRESET_WORK_PRIORITY_OPTIONS:
            return remembered_priority
        return DEFAULT_PRESET_WORK_PRIORITY

    def _default_preset_prompt_prefix_for_workspace(self, workspace_tab_id: str) -> str:
        key = self._workspace_preset_language_key(workspace_tab_id)
        return self._workspace_preset_prompt_prefixes.get(key, "")

    def _default_preset_action_execution_options_for_workspace(
        self,
        workspace_tab_id: str,
        *,
        fallback: AgentExecutionOptions,
    ) -> AgentExecutionOptions:
        key = self._workspace_preset_language_key(workspace_tab_id)
        remembered_options = self._workspace_preset_action_execution_options.get(key)
        if remembered_options is not None:
            return remembered_options
        return fallback

    def _handle_preset_language_selected(self, session_tab_id: str) -> None:
        self._remember_preset_language_for_session(session_tab_id)
        self._refresh_preset_instruction_options(session_tab_id)

    def _handle_preset_instruction_selected(self, session_tab_id: str) -> None:
        self._remember_preset_instruction_for_session(session_tab_id)

    def _handle_preset_work_priority_selected(self, session_tab_id: str) -> None:
        self._remember_preset_work_priority_for_session(session_tab_id)

    def _remember_preset_language_for_session(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        language_var = session_widgets.preset_language_var
        if language_var is None:
            return

        language = language_var.get().strip()
        if not language:
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        key = self._workspace_preset_language_key(session_tab.workspace_tab_id)
        self._workspace_preset_languages[key] = language

    def _remember_preset_instruction_for_session(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        language_var = session_widgets.preset_language_var
        instruction_var = session_widgets.preset_instruction_var
        if language_var is None or instruction_var is None:
            return

        language = language_var.get().strip()
        instruction = instruction_var.get().strip()
        if not language or not instruction:
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        key = self._workspace_preset_instruction_key(
            session_tab.workspace_tab_id,
            language,
        )
        self._workspace_preset_instructions[key] = instruction

    def _remember_preset_work_priority_for_session(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        work_priority_var = session_widgets.preset_work_priority_var
        if work_priority_var is None:
            return

        work_priority = work_priority_var.get().strip()
        if work_priority not in PRESET_WORK_PRIORITY_OPTIONS:
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        key = self._workspace_preset_language_key(session_tab.workspace_tab_id)
        self._workspace_preset_work_priorities[key] = work_priority

    def _preset_prompt_prefix_for_session(self, session_tab_id: str) -> str:
        session_widgets = self._get_session_widgets(session_tab_id)
        prompt_prefix_text = session_widgets.preset_prompt_prefix_text
        if prompt_prefix_text is None:
            return ""
        return prompt_prefix_text.get("1.0", tk.END).strip()

    def _remember_preset_prompt_prefix_for_session(self, session_tab_id: str) -> None:
        prompt_prefix = self._preset_prompt_prefix_for_session(session_tab_id)
        session_tab = self._runtime.get_session_tab(session_tab_id)
        self._remember_preset_prompt_prefix_for_workspace(
            session_tab.workspace_tab_id,
            prompt_prefix,
        )

    def _remember_preset_prompt_prefix_for_workspace(
        self,
        workspace_tab_id: str,
        prompt_prefix: str,
    ) -> None:
        key = self._workspace_preset_language_key(workspace_tab_id)
        normalized_prompt_prefix = prompt_prefix.strip()
        if normalized_prompt_prefix:
            self._workspace_preset_prompt_prefixes[key] = normalized_prompt_prefix
        else:
            self._workspace_preset_prompt_prefixes.pop(key, None)

    def _remember_preset_action_execution_options_for_session(
        self,
        session_tab_id: str,
    ) -> None:
        remembered_options = getattr(
            self,
            "_workspace_preset_action_execution_options",
            None,
        )
        if remembered_options is None:
            return

        session_widgets = self._get_session_widgets(session_tab_id)
        execution_options = session_widgets.preset_action_execution_options
        session_tab = self._runtime.get_session_tab(session_tab_id)
        key = self._workspace_preset_language_key(session_tab.workspace_tab_id)
        remembered_options[key] = execution_options

    def _workspace_preset_language_key(self, workspace_tab_id: str) -> str:
        workspace_tab = self._runtime.get_workspace_tab(workspace_tab_id)
        return (
            canonicalize_workspace_path(workspace_tab.workspace_path)
            or workspace_tab.workspace_path
            or workspace_tab_id
        )

    def _workspace_preset_instruction_key(
        self,
        workspace_tab_id: str,
        language: str,
    ) -> tuple[str, str]:
        return (self._workspace_preset_language_key(workspace_tab_id), language)

    def _refresh_preset_instruction_options(self, session_tab_id: str) -> None:
        self._request_preset_instruction_options(session_tab_id)

    def _refresh_preset_registration_controls(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        if session_widgets.preset_language_var is None:
            return
        self._set_preset_registration_controls_enabled(
            session_widgets,
            enabled=not self._preset_registration_is_locked(session_tab_id),
        )

    def _preset_registration_is_locked(self, session_tab_id: str) -> bool:
        return (
            session_tab_id in self._preset_registration_pending_session_ids
            or self._preset_session_has_registered_job(session_tab_id)
        )

    def _preset_session_has_registered_job(self, session_tab_id: str) -> bool:
        return bool(self._runtime.list_jobs(session_tab_id=session_tab_id))

    def _set_preset_registration_controls_enabled(
        self,
        session_widgets: SessionWidgets,
        *,
        enabled: bool,
    ) -> None:
        self._set_preset_combobox_enabled(
            session_widgets.preset_language_combobox,
            enabled=enabled,
        )
        self._set_preset_combobox_enabled(
            session_widgets.preset_instruction_combobox,
            enabled=enabled,
        )
        self._set_preset_combobox_enabled(
            session_widgets.preset_work_priority_combobox,
            enabled=enabled,
        )
        if session_widgets.preset_prompt_prefix_text is not None:
            session_widgets.preset_prompt_prefix_text.configure(
                state="normal" if enabled else "disabled"
            )
        if session_widgets.preset_auto_commit_checkbutton is not None:
            session_widgets.preset_auto_commit_checkbutton.configure(
                state="normal" if enabled else "disabled"
            )
        if session_widgets.preset_register_button is not None:
            session_widgets.preset_register_button.configure(
                state="normal" if enabled else "disabled"
            )

    @staticmethod
    def _set_preset_combobox_enabled(
        combobox: ttk.Combobox | None,
        *,
        enabled: bool,
    ) -> None:
        if combobox is None:
            return
        values = combobox.cget("values")
        combobox.configure(state="readonly" if enabled and values else "disabled")

    def _toggle_queue(self, workspace_tab_id: str) -> None:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            try:
                queue_state = self._runtime.get_queue_state(workspace_tab_id)
            except Exception:
                LOGGER.exception(
                    "Failed to read queue state. workspace_tab_id=%s", workspace_tab_id
                )
                self._refresh_workspace_queue_summaries()
                return
            should_start = queue_state.status != QueueStatus.STARTED
        else:
            should_start = bool(workspace_view.queue_toggle_var.get())

        action_succeeded = (
            self._start_queue(workspace_tab_id)
            if should_start
            else self._stop_queue(workspace_tab_id)
        )
        if not action_succeeded:
            self._refresh_workspace_queue_summaries()

    def _start_queue(self, workspace_tab_id: str) -> bool:
        try:
            if not self._workspace_has_runnable_jobs(workspace_tab_id):
                self._queue_start_pending_workspace_ids.discard(workspace_tab_id)
                self._refresh_workspace_queue_summaries()
                workspace_tab = self._runtime.get_workspace_tab(workspace_tab_id)
                self._set_status(
                    _tr_for(
                        self,
                        "status_queue_empty",
                        display_name=workspace_tab.display_name,
                    )
                )
                return True

            self._runtime.start_queue_in_background(workspace_tab_id)
        except Exception:
            LOGGER.exception(
                "Failed to start queue. workspace_tab_id=%s", workspace_tab_id
            )
            messagebox.showerror(
                _tr_for(self, "dialog_queue_error"),
                _tr_for(self, "dialog_queue_start_failed"),
                parent=self,
            )
            self._queue_start_pending_workspace_ids.discard(workspace_tab_id)
            return False

        self._queue_start_pending_workspace_ids.add(workspace_tab_id)
        self._refresh_workspace_queue_summaries()
        workspace_tab = self._runtime.get_workspace_tab(workspace_tab_id)
        self._set_status(
            _tr_for(
                self, "status_queue_starting", display_name=workspace_tab.display_name
            )
        )
        return True

    def _workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        workspace_has_runnable_jobs = getattr(
            self._runtime,
            "workspace_has_runnable_jobs",
            None,
        )
        if callable(workspace_has_runnable_jobs):
            return bool(workspace_has_runnable_jobs(workspace_tab_id))

        return any(
            job.status == JobStatus.QUEUED
            for job in self._runtime.list_workspace_jobs(workspace_tab_id)
        )

    def _open_scheduled_run_dialog(self) -> None:
        try:
            dialog = ScheduledRunDialog(self, scheduled_at=self._scheduled_run_at)
            result = dialog.show_modal()
        except Exception:
            LOGGER.exception("Failed to open scheduled run dialog.")
            messagebox.showerror(
                _tr_for(self, "dialog_scheduled_run_error"),
                _tr_for(self, "dialog_scheduled_run_open_failed"),
                parent=self,
            )
            self._refresh_scheduled_run_display()
            return

        if result is None:
            self._refresh_scheduled_run_display()
            return
        if result.scheduled_at is None:
            self._cancel_scheduled_run(update_status=True)
            return
        self._set_scheduled_run(result.scheduled_at)

    def _set_scheduled_run(self, scheduled_at: datetime) -> None:
        self._cancel_scheduled_run_timer()
        self._scheduled_run_at = scheduled_at
        self._schedule_scheduled_run_check()
        self._refresh_scheduled_run_display()
        self._set_status(
            _tr_for(
                self,
                "status_scheduled_run_set",
                scheduled_at=_format_scheduled_run_time(scheduled_at),
            )
        )

    def _cancel_scheduled_run(self, *, update_status: bool = False) -> None:
        had_schedule = self._scheduled_run_at is not None
        self._cancel_scheduled_run_timer()
        self._scheduled_run_at = None
        self._refresh_scheduled_run_display()
        if update_status and had_schedule:
            self._set_status(_tr_for(self, "status_scheduled_run_canceled"))

    def _cancel_scheduled_run_timer(self) -> None:
        if self._scheduled_run_after_id is None:
            return
        try:
            self.after_cancel(self._scheduled_run_after_id)
        except tk.TclError:
            LOGGER.debug("Failed to cancel scheduled run callback.", exc_info=True)
        self._scheduled_run_after_id = None

    def _schedule_scheduled_run_check(self) -> None:
        if self._scheduled_run_at is None or self._closed:
            return

        remaining_ms = int(
            (self._scheduled_run_at - datetime.now()).total_seconds() * 1000
        )
        interval_ms = min(
            max(remaining_ms, 1),
            SCHEDULED_RUN_POLL_MAX_INTERVAL_MS,
        )
        self._scheduled_run_after_id = self.after(
            interval_ms,
            self._on_scheduled_run_timer,
        )

    def _on_scheduled_run_timer(self) -> None:
        self._scheduled_run_after_id = None
        scheduled_at = self._scheduled_run_at
        if scheduled_at is None or self._closed:
            return
        if datetime.now() < scheduled_at:
            self._schedule_scheduled_run_check()
            return

        self._cancel_scheduled_run(update_status=False)
        self._start_scheduled_run_queues(scheduled_at)

    def _start_scheduled_run_queues(self, scheduled_at: datetime) -> None:
        del scheduled_at

        started_count = 0
        for workspace_tab in self._runtime.list_workspace_tabs(include_closed=False):
            workspace_tab_id = workspace_tab.workspace_tab_id
            if not self._workspace_has_runnable_jobs(workspace_tab_id):
                continue
            if (
                self._start_queue(workspace_tab_id)
                and workspace_tab_id in self._queue_start_pending_workspace_ids
            ):
                started_count += 1

        if started_count == 0:
            self._set_status(_tr_for(self, "status_scheduled_run_no_jobs"))
            return

        self._set_status(
            _tr_for(self, "status_scheduled_run_started", count=started_count)
        )

    def _stop_queue(self, workspace_tab_id: str) -> bool:
        try:
            self._runtime.stop_queue(workspace_tab_id)
        except Exception:
            LOGGER.exception(
                "Failed to stop queue. workspace_tab_id=%s", workspace_tab_id
            )
            messagebox.showerror(
                _tr_for(self, "dialog_queue_error"),
                _tr_for(self, "dialog_queue_stop_failed"),
                parent=self,
            )
            return False

        self._queue_start_pending_workspace_ids.discard(workspace_tab_id)
        self._drain_runtime_events()
        self._refresh_workspace_queue_summaries()
        workspace_tab = self._runtime.get_workspace_tab(workspace_tab_id)
        self._set_status(
            _tr_for(
                self, "status_queue_stopped", display_name=workspace_tab.display_name
            )
        )
        return True

    def _refresh_session_view(
        self, session_tab_id: str, preferred_job_id: str | None = None
    ) -> None:
        jobs = self._runtime.list_jobs(session_tab_id=session_tab_id)
        self._refresh_session_job_selection(
            session_tab_id, preferred_job_id=preferred_job_id, jobs=jobs
        )
        self._refresh_session_summary(session_tab_id, jobs=jobs)
        self._refresh_session_output(session_tab_id)
        self._refresh_session_history(session_tab_id)
        self._refresh_preset_registration_controls(session_tab_id)

    def _refresh_session_job_selection(
        self,
        session_tab_id: str,
        preferred_job_id: str | None = None,
        *,
        jobs: tuple[Job, ...] | None = None,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        if jobs is None:
            jobs = self._runtime.list_jobs(session_tab_id=session_tab_id)

        desired_job_ids = [job.job_id for job in jobs]
        running_job_id = next(
            (job.job_id for job in jobs if job.status == JobStatus.RUNNING),
            None,
        )
        selected_job_id = running_job_id or preferred_job_id
        if (
            selected_job_id is None
            and session_widgets.selected_job_id in desired_job_ids
        ):
            selected_job_id = session_widgets.selected_job_id
        if selected_job_id is None and desired_job_ids:
            selected_job_id = desired_job_ids[-1]

        session_widgets.selected_job_id = selected_job_id

    def _refresh_session_summary(
        self,
        session_tab_id: str,
        *,
        jobs: tuple[Job, ...] | None = None,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        session_tab = self._runtime.get_session_tab(session_tab_id)
        if jobs is None:
            jobs = self._runtime.list_jobs(session_tab_id=session_tab_id)
        running_job = next(
            (job for job in jobs if job.status == JobStatus.RUNNING), None
        )
        turns = self._runtime.list_session_turns(session_tab_id)
        is_running = running_job is not None

        session_widgets.session_id_var.set(
            _tr_for(
                self,
                "session_id_label",
                session_id=session_tab.session_id
                or _tr_for(self, "session_id_pending"),
            )
        )
        self._refresh_session_tab_indicator(session_tab_id, started=is_running)

        focused_job = None
        if session_widgets.selected_job_id is not None:
            for job in jobs:
                if job.job_id == session_widgets.selected_job_id:
                    focused_job = job
                    break
        if focused_job is None and jobs:
            focused_job = jobs[-1]

        if is_running:
            session_widgets.activity_var.set(
                _running_activity_text(
                    running_job,
                    jobs,
                    language=_window_language(self),
                )
            )
        elif focused_job is not None:
            session_widgets.activity_var.set(
                _finished_activity_text(
                    focused_job,
                    jobs,
                    self._runtime.get_job_user_message(focused_job.job_id),
                    language=_window_language(self),
                )
            )
        elif jobs:
            session_widgets.activity_var.set(
                _completed_activity_text(
                    jobs,
                    language=_window_language(self),
                )
            )
        elif turns:
            latest_turn = turns[-1]
            session_widgets.activity_var.set(
                _tr_for(
                    self,
                    "activity_history",
                    count=len(turns),
                    timestamp=_format_timestamp(latest_turn.last_activity_at),
                )
            )
        else:
            session_widgets.activity_var.set(_tr_for(self, "activity_no_jobs"))

        if focused_job is not None:
            if not is_running:
                message = ""
            else:
                message = _session_job_message_text(
                    focused_job,
                    self._runtime.get_job_user_message(focused_job.job_id),
                    language=_window_language(self),
                )
        elif turns:
            message = _tr_for(self, "message_history_available")
        else:
            message = ""
        _set_optional_label_text(
            session_widgets.message_label,
            session_widgets.message_var,
            message,
        )

        waiting_jobs = [
            job for job in jobs if job.status == JobStatus.WAITING_FOR_CONFIGURATION
        ]
        if waiting_jobs:
            latest_waiting_job = waiting_jobs[-1]
            wait_reason = _tr_for(
                self,
                "wait_reason",
                reason=localize_runtime_message(
                    latest_waiting_job.configuration_wait_reason
                    or _job_status_label(
                        JobStatus.WAITING_FOR_CONFIGURATION,
                        _window_language(self),
                    ),
                    _window_language(self),
                ),
            )
        else:
            wait_reason = ""
        _set_optional_label_text(
            session_widgets.wait_reason_label,
            session_widgets.wait_reason_var,
            wait_reason,
        )

    def _copy_session_id(self, session_tab_id: str) -> None:
        session_tab = self._runtime.get_session_tab(session_tab_id)
        if not session_tab.session_id:
            return

        try:
            self.clipboard_clear()
            self.clipboard_append(session_tab.session_id)
        except Exception:
            LOGGER.exception(
                "Failed to copy session ID. session_tab_id=%s",
                session_tab_id,
            )
            messagebox.showerror(
                _tr_for(self, "dialog_session_error"),
                _tr_for(self, "dialog_session_id_copy_failed"),
                parent=self,
            )
            return

        self._set_status(_tr_for(self, "status_session_id_copied"))

    def _refresh_session_output(
        self,
        session_tab_id: str,
        *,
        output_append: SessionOutputAppend | None = None,
        appended_job_id: str | None = None,
    ) -> None:
        if output_append is None and appended_job_id is not None:
            output_append = SessionOutputAppend(job_id=appended_job_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        selected_job_id = session_widgets.selected_job_id
        if selected_job_id is None:
            if (
                session_widgets.rendered_log_job_id is None
                and session_widgets.rendered_log_line_count == 0
                and session_widgets.rendered_log_last_line is None
                and session_widgets.rendered_log_language is None
            ):
                return
            self._set_text_content(
                session_widgets.log_text,
                "",
                auto_scroll_to_end=True,
            )
            self._mark_session_output_rendered(
                session_widgets,
                job_id=None,
                line_count=0,
                last_line=None,
                language=None,
            )
            return

        appended_job_id = output_append.job_id if output_append is not None else None
        if appended_job_id is not None and appended_job_id != selected_job_id:
            selected_job_id = self._select_appended_running_job(
                session_widgets,
                selected_job_id=selected_job_id,
                appended_job_id=appended_job_id,
            )
            if selected_job_id != appended_job_id:
                return

        language = _window_language(self)
        if (
            output_append is not None
            and session_widgets.rendered_log_job_id == selected_job_id
            and session_widgets.rendered_log_language == language
        ):
            self._append_session_output_lines(
                session_widgets,
                output_append.lines,
                language=language,
            )
            return

        log_lines = self._runtime.get_job_progress_logs(selected_job_id)
        last_line = log_lines[-1].rstrip() if log_lines else None
        if (
            session_widgets.rendered_log_job_id == selected_job_id
            and session_widgets.rendered_log_line_count == len(log_lines)
            and session_widgets.rendered_log_last_line == last_line
            and session_widgets.rendered_log_language == language
        ):
            return

        log_content = "\n".join(
            localize_progress_line(line, language) for line in log_lines
        )
        self._set_text_content(
            session_widgets.log_text,
            log_content,
            auto_scroll_to_end=True,
        )
        self._mark_session_output_rendered(
            session_widgets,
            job_id=selected_job_id,
            line_count=len(log_lines),
            last_line=last_line,
            language=language,
        )

    def _append_session_output_lines(
        self,
        session_widgets: SessionWidgets,
        lines: list[str],
        *,
        language: str,
    ) -> None:
        if not lines:
            return

        log_content = "\n".join(
            localize_progress_line(line, language) for line in lines
        )
        self._append_text_content(
            session_widgets.log_text,
            log_content,
            prefix_separator=session_widgets.rendered_log_line_count > 0,
            auto_scroll_to_end=True,
        )
        session_widgets.rendered_log_line_count += len(lines)
        session_widgets.rendered_log_last_line = lines[-1].rstrip()
        self._trim_rendered_session_output_lines(session_widgets)

    def _trim_rendered_session_output_lines(
        self,
        session_widgets: SessionWidgets,
    ) -> None:
        excess_line_count = (
            session_widgets.rendered_log_line_count - MAX_JOB_PROGRESS_LOG_LINES
        )
        if excess_line_count <= 0:
            return

        widget = session_widgets.log_text
        should_scroll_to_end = _should_follow_text_end(widget)
        widget.configure(state="normal")
        widget.delete("1.0", f"{excess_line_count + 1}.0")
        if should_scroll_to_end:
            widget.see(tk.END)
        widget.configure(state="disabled")
        session_widgets.rendered_log_line_count = MAX_JOB_PROGRESS_LOG_LINES

    def _mark_session_output_rendered(
        self,
        session_widgets: SessionWidgets,
        *,
        job_id: str | None,
        line_count: int,
        last_line: str | None,
        language: str | None,
    ) -> None:
        session_widgets.rendered_log_job_id = job_id
        session_widgets.rendered_log_line_count = line_count
        session_widgets.rendered_log_last_line = last_line
        session_widgets.rendered_log_language = language

    def _select_appended_running_job(
        self,
        session_widgets: SessionWidgets,
        *,
        selected_job_id: str,
        appended_job_id: str,
    ) -> str:
        try:
            appended_job = self._runtime.get_job(appended_job_id)
        except KeyError:
            return selected_job_id

        if appended_job.status != JobStatus.RUNNING:
            return selected_job_id

        try:
            selected_job = self._runtime.get_job(selected_job_id)
        except KeyError:
            selected_job = None

        if selected_job is not None and selected_job.status == JobStatus.RUNNING:
            return selected_job_id

        session_widgets.selected_job_id = appended_job_id
        return appended_job_id

    def _refresh_session_history(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        turns = self._runtime.list_session_turns(session_tab_id)
        language = _window_language(self)

        if not turns:
            if (
                session_widgets.rendered_history_turns
                or session_widgets.rendered_history_language != language
            ):
                self._set_text_content(session_widgets.history_text, "")
            self._mark_session_history_rendered(
                session_widgets,
                rendered_turns=(),
                source_turns=turns,
                language=language,
            )
            return

        rendered_turns = session_widgets.rendered_history_turns
        if rendered_turns and session_widgets.rendered_history_language == language:
            if session_widgets.rendered_history_source_turns is turns:
                return

            changed_index = self._session_history_first_changed_index(
                rendered_turns, turns
            )
            if changed_index is None:
                session_widgets.rendered_history_source_turns = turns
                return

            replace_from = self._session_history_prefix_length(
                rendered_turns, changed_index
            )
            replacement_renders = self._render_session_history_turns(
                turns[changed_index:],
                start_index=changed_index + 1,
                language=language,
                content_length=replace_from,
            )
            replacement_turns = tuple(
                rendered_turn for rendered_turn, _block_text in replacement_renders
            )
            replacement_content = self._join_session_history_blocks(replacement_renders)
            if changed_index > 0 and replacement_content:
                replacement_content = HISTORY_TURN_SEPARATOR + replacement_content
            self._replace_text_tail(
                session_widgets.history_text,
                replace_from,
                replacement_content,
            )
            self._mark_session_history_rendered(
                session_widgets,
                rendered_turns=rendered_turns[:changed_index] + replacement_turns,
                source_turns=turns,
                language=language,
            )
            return

        rendered_history = self._render_session_history_turns(
            turns,
            start_index=1,
            language=language,
            content_length=0,
        )
        rendered_turns = tuple(
            rendered_turn for rendered_turn, _block_text in rendered_history
        )
        self._set_text_content(
            session_widgets.history_text,
            self._join_session_history_blocks(rendered_history),
        )
        self._mark_session_history_rendered(
            session_widgets,
            rendered_turns=rendered_turns,
            source_turns=turns,
            language=language,
        )

    def _render_session_history_turns(
        self,
        turns: tuple[object, ...],
        *,
        start_index: int,
        language: str,
        content_length: int,
    ) -> tuple[tuple[SessionHistoryTurnRenderState, str], ...]:
        rendered_turns: list[tuple[SessionHistoryTurnRenderState, str]] = []
        for index, turn in enumerate(turns, start=start_index):
            if index > 1:
                content_length += len(HISTORY_TURN_SEPARATOR)
            rendered_turn, block_text = self._render_session_history_turn(
                turn,
                index,
                language,
                content_length=content_length,
            )
            content_length = rendered_turn.content_end_index
            rendered_turns.append((rendered_turn, block_text))
        return tuple(rendered_turns)

    def _render_session_history_turn(
        self,
        turn: object,
        index: int,
        language: str,
        *,
        content_length: int,
    ) -> tuple[SessionHistoryTurnRenderState, str]:
        response_text = turn.response_text
        block_text = self._format_session_history_turn(turn, index, language)
        content_end_index = content_length + len(block_text)
        return (
            SessionHistoryTurnRenderState(
                started_at=turn.started_at,
                completed_at=turn.completed_at,
                prompt_text=turn.prompt_text,
                response_text=response_text,
                block_length=len(block_text),
                content_end_index=content_end_index,
            ),
            block_text,
        )

    def _format_session_history_turn(
        self, turn: object, index: int, language: str
    ) -> str:
        timestamp = turn.completed_at or turn.started_at
        header = ui_text(
            "history_turn",
            language,
            index=index,
            timestamp=_format_timestamp(timestamp),
        )
        if turn.completed_at is None:
            header = f"{header} / {ui_text('history_in_progress', language)}"

        chunks = [header, "Prompt:", turn.prompt_text]
        if turn.response_text is not None:
            chunks.extend(["", "Response:", turn.response_text])
        return "\n".join(chunks)

    def _session_history_first_changed_index(
        self,
        rendered_turns: tuple[SessionHistoryTurnRenderState, ...],
        turns: list[object],
    ) -> int | None:
        compared_count = min(len(rendered_turns), len(turns))
        for index in range(compared_count):
            if not self._session_history_turn_matches(
                rendered_turns[index], turns[index]
            ):
                return index
        if len(rendered_turns) == len(turns):
            return None
        return compared_count

    def _session_history_turn_matches(
        self, rendered_turn: SessionHistoryTurnRenderState, turn: object
    ) -> bool:
        if (
            rendered_turn.started_at != turn.started_at
            or rendered_turn.completed_at != turn.completed_at
        ):
            return False
        if not self._session_history_text_matches(
            rendered_turn.prompt_text,
            turn.prompt_text,
        ):
            return False
        return self._session_history_optional_text_matches(
            rendered_turn.response_text,
            turn.response_text,
        )

    def _session_history_text_matches(self, rendered_text: str, text: str) -> bool:
        if rendered_text is text:
            return True
        if len(rendered_text) != len(text):
            return False
        return rendered_text == text

    def _session_history_optional_text_matches(
        self,
        rendered_text: str | None,
        text: str | None,
    ) -> bool:
        if rendered_text is None or text is None:
            return rendered_text is text
        return self._session_history_text_matches(rendered_text, text)

    def _join_session_history_blocks(
        self, rendered_turns: tuple[tuple[SessionHistoryTurnRenderState, str], ...]
    ) -> str:
        return HISTORY_TURN_SEPARATOR.join(
            block_text for _rendered_turn, block_text in rendered_turns
        )

    def _session_history_prefix_length(
        self,
        rendered_turns: tuple[SessionHistoryTurnRenderState, ...],
        turn_count: int,
    ) -> int:
        if turn_count <= 0:
            return 0
        return rendered_turns[turn_count - 1].content_end_index

    def _mark_session_history_rendered(
        self,
        session_widgets: SessionWidgets,
        *,
        rendered_turns: tuple[SessionHistoryTurnRenderState, ...],
        source_turns: object,
        language: str,
    ) -> None:
        session_widgets.rendered_history_turns = rendered_turns
        session_widgets.rendered_history_source_turns = source_turns
        session_widgets.rendered_history_language = language

    def _on_workspace_tab_changed(self, _event: tk.Event[tk.Misc]) -> None:
        selected = self._workspace_notebook.select()
        if not selected:
            return

        workspace_tab_id = self._workspace_frame_map.get(selected)
        if workspace_tab_id is None:
            return
        try:
            self._runtime.activate_workspace(workspace_tab_id)
        except Exception:
            LOGGER.exception(
                "Failed to activate workspace. workspace_tab_id=%s", workspace_tab_id
            )

    def _on_session_tab_changed(
        self, _event: tk.Event[tk.Misc], workspace_tab_id: str
    ) -> None:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            return

        selected = workspace_view.session_notebook.select()
        if not selected:
            return

        session_mapping = self._session_frame_map.get(selected)
        if session_mapping is None:
            return

        _, session_tab_id = session_mapping
        try:
            self._runtime.activate_session(session_tab_id)
        except Exception:
            LOGGER.exception(
                "Failed to activate session. session_tab_id=%s", session_tab_id
            )

        self._refresh_session_view(session_tab_id)

    def _on_workspace_job_selected(self, workspace_tab_id: str) -> None:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            return

        selection = workspace_view.workspace_jobs_tree.selection()
        if not selection:
            return

        self._select_workspace_job(workspace_tab_id, selection[0])

    def _select_workspace_job(self, workspace_tab_id: str, job_id: str) -> None:
        try:
            job = self._runtime.get_job(job_id)
        except KeyError:
            return

        if job.workspace_tab_id != workspace_tab_id:
            return

        workspace_view = self._workspace_views.get(workspace_tab_id)
        if (
            workspace_view is None
            or job.session_tab_id not in workspace_view.session_views
        ):
            return

        session_widgets = self._get_session_widgets(job.session_tab_id)
        session_widgets.selected_job_id = job.job_id
        self._select_session_tab(workspace_tab_id, job.session_tab_id)
        self._refresh_session_summary(job.session_tab_id)
        self._refresh_session_output(job.session_tab_id)

    def _show_job_context_menu(
        self,
        event: tk.Event[tk.Misc],
        workspace_tab_id: str,
    ) -> str:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            return "break"

        tree = workspace_view.workspace_jobs_tree
        job_id = tree.identify_row(event.y)
        if not job_id:
            return "break"

        try:
            job = self._runtime.get_job(job_id)
        except KeyError:
            self._set_status(_tr_for(self, "status_job_already_deleted"))
            return "break"

        if job.workspace_tab_id != workspace_tab_id:
            return "break"

        tree.selection_set(job_id)
        tree.focus(job_id)
        self._select_workspace_job(workspace_tab_id, job_id)

        menu = tk.Menu(self, tearoff=False)
        menu.add_command(
            label=_context_menu_prompt_label(
                job.prompt, language=_window_language(self)
            ),
            command=lambda target_job_id=job_id: self._show_job_prompt_dialog(
                target_job_id
            ),
        )
        menu.add_separator()
        menu.add_command(
            label=_tr_for(self, "button_delete"),
            command=lambda target_job_id=job_id: self._delete_job(target_job_id),
        )
        self._job_context_menu = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _show_job_prompt_dialog(self, job_id: str) -> None:
        try:
            job = self._runtime.get_job(job_id)
        except KeyError:
            self._set_status(_tr_for(self, "status_job_already_deleted"))
            return

        try:
            dialog = PromptViewerDialog(
                self,
                job_id=job.job_id,
                prompt=job.prompt,
                ui_language=_window_language(self),
            )
            dialog.show_modal()
        except tk.TclError:
            LOGGER.exception("Failed to open job prompt dialog. job_id=%s", job_id)
            messagebox.showerror(
                _tr_for(self, "dialog_prompt_view_error"),
                _tr_for(self, "dialog_prompt_view_failed"),
                parent=self,
            )

    def _delete_job(self, job_id: str) -> None:
        try:
            job = self._runtime.get_job(job_id)
        except KeyError:
            self._set_status(_tr_for(self, "status_job_already_deleted"))
            return

        if job.status == JobStatus.RUNNING:
            messagebox.showinfo(
                _tr_for(self, "dialog_job_delete"),
                _tr_for(self, "dialog_job_delete_running"),
                parent=self,
            )
            return

        if not messagebox.askyesno(
            _tr_for(self, "dialog_job_delete"),
            _tr_for(self, "dialog_job_delete_confirm", job_id=job.job_id),
            parent=self,
        ):
            return

        try:
            deleted_job = self._runtime.delete_job(job_id)
        except ValueError:
            messagebox.showinfo(
                _tr_for(self, "dialog_job_delete"),
                _tr_for(self, "dialog_job_delete_running"),
                parent=self,
            )
            return
        except KeyError:
            self._set_status(_tr_for(self, "status_job_already_deleted"))
            return
        except Exception:
            LOGGER.exception("Failed to delete job. job_id=%s", job_id)
            messagebox.showerror(
                _tr_for(self, "dialog_job_delete_error"),
                _tr_for(self, "dialog_job_delete_failed"),
                parent=self,
            )
            return

        self._drain_runtime_events()
        if self._has_session_view(deleted_job.session_tab_id):
            self._refresh_session_view(deleted_job.session_tab_id)
        self._refresh_workspace_task_list(deleted_job.workspace_tab_id)
        self._refresh_workspace_queue_summaries()
        self._set_status(_tr_for(self, "status_job_deleted", job_id=deleted_job.job_id))

    def _resize_workspace_task_columns(
        self,
        jobs_tree: ttk.Treeview,
        available_width: int,
    ) -> None:
        if available_width <= 1:
            return

        widths = _calculate_workspace_task_column_widths(available_width)
        for (column_id, _heading, _base_width, _anchor), width in zip(
            WORKSPACE_TASK_COLUMNS,
            widths,
        ):
            jobs_tree.column(column_id, width=width)

    def _refresh_workspace_task_list(
        self,
        workspace_tab_id: str,
        *,
        preferred_job_id: str | None = None,
        jobs: tuple[Job, ...] | None = None,
    ) -> None:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if workspace_view is None:
            return

        if jobs is None:
            jobs = self._runtime.list_workspace_jobs(workspace_tab_id)
        jobs_tree = workspace_view.workspace_jobs_tree
        language = _window_language(self)
        summary = _format_workspace_task_summary(jobs, language=language)
        if workspace_view.workspace_jobs_summary_var.get() != summary:
            workspace_view.workspace_jobs_summary_var.set(summary)
        current_order = list(jobs_tree.get_children())
        existing_job_ids = set(current_order)
        desired_job_ids = tuple(job.job_id for job in jobs)
        desired_job_id_set = set(desired_job_ids)
        current_selection = jobs_tree.selection()
        selected_job_id = (
            preferred_job_id if preferred_job_id in desired_job_id_set else None
        )
        if (
            selected_job_id is None
            and current_selection
            and current_selection[0] in desired_job_id_set
        ):
            selected_job_id = current_selection[0]

        stale_job_ids = existing_job_ids - desired_job_id_set
        for stale_job_id in stale_job_ids:
            jobs_tree.delete(stale_job_id)
        if stale_job_ids:
            current_order = [
                job_id for job_id in current_order if job_id not in stale_job_ids
            ]

        for index, job in enumerate(jobs):
            values = (
                str(job.queue_order) if job.queue_order is not None else "-",
                self._job_session_label(job),
                _job_progress_text(job, language=language),
                _truncate_prompt(job.prompt, width=60),
            )
            if jobs_tree.exists(job.job_id):
                if tuple(jobs_tree.item(job.job_id, "values")) != values:
                    jobs_tree.item(job.job_id, values=values)
                if index >= len(current_order) or current_order[index] != job.job_id:
                    jobs_tree.move(job.job_id, "", index)
                    current_order.remove(job.job_id)
                    current_order.insert(index, job.job_id)
            else:
                jobs_tree.insert("", index, iid=job.job_id, values=values)
                current_order.insert(index, job.job_id)

        if selected_job_id is None:
            jobs_tree.selection_remove(jobs_tree.selection())
            return

        jobs_tree.selection_set(selected_job_id)
        jobs_tree.focus(selected_job_id)

    def _refresh_workspace_task_lists_for_workspace_path(
        self, workspace_path: str
    ) -> None:
        normalized_workspace_path = canonicalize_workspace_path(workspace_path)
        workspace_views_for_path: list[tuple[str, WorkspaceWidgets]] = []
        for workspace_tab_id, workspace_view in self._workspace_views.items():
            runtime_workspace = self._runtime.get_workspace_tab(workspace_tab_id)
            if (
                canonicalize_workspace_path(runtime_workspace.workspace_path)
                != normalized_workspace_path
            ):
                continue
            workspace_views_for_path.append((workspace_tab_id, workspace_view))

        jobs_by_workspace = self._runtime.list_jobs_by_workspace(
            workspace_tab_id
            for workspace_tab_id, _workspace_view in workspace_views_for_path
        )
        for workspace_tab_id, workspace_view in workspace_views_for_path:
            self._refresh_workspace_task_list(
                workspace_tab_id,
                jobs=jobs_by_workspace.get(workspace_tab_id, ()),
            )
            if workspace_view.session_views:
                for session_tab_id in tuple(workspace_view.session_views):
                    self._refresh_session_summary(session_tab_id)

    def _remove_session_view(self, session_tab_id: str) -> None:
        session_tab = self._runtime.get_session_tab(session_tab_id)
        workspace_view = self._workspace_views.get(session_tab.workspace_tab_id)
        if workspace_view is None:
            return

        session_widgets = workspace_view.session_views.pop(session_tab_id, None)
        if session_widgets is None:
            return

        self._preset_language_request_ids.pop(session_tab_id, None)
        self._preset_instruction_request_ids.pop(session_tab_id, None)
        self._preset_registration_pending_session_ids.discard(session_tab_id)
        self._session_frame_map.pop(str(session_widgets.frame), None)
        try:
            workspace_view.session_notebook.forget(session_widgets.frame)
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

    def _apply_output_font_to_all_sessions(self) -> None:
        for workspace_view in self._workspace_views.values():
            for session_widgets in workspace_view.session_views.values():
                self._apply_output_font(session_widgets)

    def _refresh_session_outputs_for_all_sessions(self) -> None:
        for workspace_view in self._workspace_views.values():
            for session_tab_id in tuple(workspace_view.session_views):
                self._refresh_session_output(session_tab_id)

    def _apply_output_font(self, session_widgets: SessionWidgets) -> None:
        output_font = (OUTPUT_FONT_FAMILY, self._runtime.settings.output_font_size)
        session_widgets.log_text.configure(font=output_font)
        session_widgets.history_text.configure(font=output_font)

    def _set_text_content(
        self,
        widget: scrolledtext.ScrolledText,
        content: str,
        *,
        auto_scroll_to_end: bool = False,
    ) -> None:
        should_scroll_to_end = auto_scroll_to_end and _should_follow_text_end(widget)
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        if content:
            widget.insert(tk.END, content)
        if should_scroll_to_end:
            widget.see(tk.END)
        widget.configure(state="disabled")

    def _replace_text_tail(
        self,
        widget: scrolledtext.ScrolledText,
        start_offset: int,
        content: str,
    ) -> None:
        start_index = f"1.0 + {start_offset} chars"
        widget.configure(state="normal")
        widget.delete(start_index, "end-1c")
        if content:
            widget.insert("end-1c", content)
        widget.configure(state="disabled")

    def _append_text_content(
        self,
        widget: scrolledtext.ScrolledText,
        content: str,
        *,
        prefix_separator: bool,
        auto_scroll_to_end: bool = False,
    ) -> None:
        should_scroll_to_end = auto_scroll_to_end and _should_follow_text_end(widget)
        widget.configure(state="normal")
        if prefix_separator:
            widget.insert(tk.END, "\n")
        if content:
            widget.insert(tk.END, content)
        if should_scroll_to_end:
            widget.see(tk.END)
        widget.configure(state="disabled")

    def _get_session_widgets(self, session_tab_id: str) -> SessionWidgets:
        session_tab = self._runtime.get_session_tab(session_tab_id)
        workspace_view = self._workspace_views[session_tab.workspace_tab_id]
        return workspace_view.session_views[session_tab_id]

    def _job_session_label(self, job: Job) -> str:
        try:
            session_tab = self._runtime.get_session_tab(job.session_tab_id)
        except KeyError:
            return job.session_tab_id
        return session_tab.display_name

    def _set_status(self, message: str) -> None:
        self._status_message_var.set(_localize_status_message(self, message))


def _split_dropped_workspace_paths(widget: tk.Misc, data: str) -> tuple[str, ...]:
    if not data:
        return ()

    try:
        raw_paths = widget.tk.splitlist(data)
    except tk.TclError:
        raw_paths = (data,)

    return tuple(
        normalized_path
        for raw_path in raw_paths
        if (normalized_path := str(raw_path).strip())
    )


def _session_kind_uses_prompt_editor(kind: SessionTabKind) -> bool:
    return kind != SessionTabKind.PRESET


def _notebook_insert_position(
    notebook: ttk.Notebook, requested_index: int
) -> int | str:
    if requested_index >= len(notebook.tabs()):
        return tk.END
    return requested_index


def _queue_full_session_view_refresh(
    updates: RuntimeUiUpdateBatch,
    session_tab_id: str,
) -> None:
    if session_tab_id not in updates.full_session_views:
        updates.full_session_views.append(session_tab_id)
    updates.session_summaries.discard(session_tab_id)
    updates.session_histories.discard(session_tab_id)
    updates.session_outputs.pop(session_tab_id, None)


def _calculate_workspace_task_column_widths(available_width: int) -> tuple[int, ...]:
    base_widths = tuple(
        width for _column_id, _heading, width, _anchor in WORKSPACE_TASK_COLUMNS
    )
    if available_width <= 0:
        return tuple(
            max(WORKSPACE_TASK_COLUMN_MIN_WIDTH, width) for width in base_widths
        )

    total_base_width = sum(base_widths)
    raw_widths = [width * available_width / total_base_width for width in base_widths]
    widths = [max(WORKSPACE_TASK_COLUMN_MIN_WIDTH, int(width)) for width in raw_widths]

    remaining_width = available_width - sum(widths)
    if remaining_width > 0:
        remainder_order = sorted(
            range(len(raw_widths)),
            key=lambda index: (
                raw_widths[index] - int(raw_widths[index]),
                base_widths[index],
            ),
            reverse=True,
        )
        for index in remainder_order:
            if remaining_width == 0:
                break
            widths[index] += 1
            remaining_width -= 1
    elif remaining_width < 0:
        shrink_order = sorted(
            range(len(widths)),
            key=lambda index: (widths[index], base_widths[index]),
            reverse=True,
        )
        while remaining_width < 0:
            changed = False
            for index in shrink_order:
                if remaining_width == 0:
                    break
                if widths[index] <= WORKSPACE_TASK_COLUMN_MIN_WIDTH:
                    continue
                widths[index] -= 1
                remaining_width += 1
                changed = True
            if not changed:
                break

    return tuple(widths)


def _is_pending_close_job(job: Job) -> bool:
    return job.status in (JobStatus.QUEUED, JobStatus.WAITING_FOR_CONFIGURATION)


def _format_scheduled_run_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _safe_configure(widget: tk.Misc, **options: object) -> None:
    try:
        widget.configure(**options)
    except tk.TclError:
        LOGGER.debug(
            "Failed to apply scaled widget options. widget=%s", widget, exc_info=True
        )


def _should_follow_text_end(widget: scrolledtext.ScrolledText) -> bool:
    existing_content = widget.get("1.0", "end-1c")
    if not existing_content.strip():
        return True

    _top_fraction, bottom_fraction = widget.yview()
    return bottom_fraction >= TEXT_AUTOSCROLL_BOTTOM_THRESHOLD


def _format_timestamp(value) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")
