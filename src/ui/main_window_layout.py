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


class MainWindowLayoutMixin:
    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        main_splitter = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        self._main_splitter = main_splitter
        main_splitter.grid(row=0, column=0, sticky="nsew")
        main_splitter.bind(
            "<Configure>",
            lambda _event: self._refresh_sidebar_restore_button(),
            add="+",
        )
        main_splitter.bind(
            "<ButtonRelease-1>",
            lambda _event: self._refresh_sidebar_restore_button(),
            add="+",
        )

        sidebar = ttk.Frame(
            main_splitter,
            padding=self._ui_scale.padding(12, 12, 10, 12),
            width=self._ui_scale.px(SIDEBAR_INITIAL_WIDTH),
        )
        self._sidebar = sidebar
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(0, weight=1)

        sidebar_content = ttk.Frame(sidebar)
        self._sidebar_content = sidebar_content
        sidebar_content.grid(row=0, column=0, sticky="nsew")
        sidebar_content.columnconfigure(0, weight=1)
        sidebar_content.rowconfigure(0, weight=1)

        saved_list_frame = ttk.Frame(sidebar_content)
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

        workspace_button_frame = ttk.Frame(sidebar_content)
        workspace_button_frame.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=self._ui_scale.padding(0, 6),
        )
        workspace_button_frame.columnconfigure(0, weight=1)
        workspace_button_frame.columnconfigure(1, weight=1)

        self._workspace_register_button = ttk.Button(
            workspace_button_frame,
            text=_tr_for(self, "button_register"),
            command=self._open_workspace_from_dialog,
        )
        self._workspace_register_button.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=self._ui_scale.padding(0, 3),
            pady=self._ui_scale.padding(0, 6),
        )
        self._workspace_remove_button = ttk.Button(
            workspace_button_frame,
            text=_tr_for(self, "button_remove"),
            command=self._delete_selected_saved_workspace,
        )
        self._workspace_remove_button.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=self._ui_scale.padding(3, 0),
            pady=self._ui_scale.padding(0, 6),
        )
        self._workspace_open_button = ttk.Button(
            workspace_button_frame,
            text=_tr_for(self, "button_open"),
            command=self._open_selected_saved_workspace,
        )
        self._workspace_open_button.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=self._ui_scale.padding(0, 3),
        )
        self._workspace_close_button = ttk.Button(
            workspace_button_frame,
            text=_tr_for(self, "button_close"),
            command=self._close_active_workspace,
        )
        self._workspace_close_button.grid(
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

        settings_frame = ttk.Frame(sidebar_content)
        settings_frame.grid(row=3, column=0, sticky="ew")
        settings_frame.columnconfigure(0, weight=1)

        scheduled_run_frame = ttk.Frame(sidebar_content)
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
        settings_button_row = ttk.Frame(settings_header)
        settings_button_row.grid(
            row=0,
            column=0,
            sticky="e",
            pady=self._ui_scale.padding(0, 6),
        )
        self._about_button = ttk.Button(
            settings_button_row,
            text=_tr_for(self, "button_about"),
            command=self._open_about_dialog,
        )
        self._about_button.grid(row=0, column=0, padx=self._ui_scale.padding(0, 6))
        self._settings_button = ttk.Button(
            settings_button_row,
            text=_tr_for(self, "button_change"),
            command=self._open_settings_dialog,
        )
        self._settings_button.grid(row=0, column=1)

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

        status_bar_container = ttk.Frame(main_area)
        self._status_bar_container = status_bar_container
        status_bar_container.columnconfigure(1, weight=1)
        status_bar_container.grid(
            row=1, column=0, sticky="ew", pady=self._ui_scale.padding(12, 0)
        )

        self._sidebar_toggle_button = ttk.Button(
            status_bar_container,
            text="<",
            width=2,
            command=self._toggle_sidebar,
        )
        self._sidebar_toggle_button.grid(
            row=0,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 6),
        )

        status_bar = ttk.Label(
            status_bar_container,
            textvariable=self._status_message_var,
            relief="groove",
            anchor="w",
            style="Status.TLabel",
        )
        self._status_bar = status_bar
        status_bar.grid(row=0, column=1, sticky="ew")

        self._apply_sidebar_layout()

    def _toggle_sidebar(self) -> None:
        should_expand = self._sidebar_collapsed or self._is_sidebar_sash_hidden()
        self._set_sidebar_collapsed(not should_expand)

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        if collapsed and not self._sidebar_collapsed:
            self._remember_sidebar_restore_width()
        self._sidebar_collapsed = collapsed
        self._apply_sidebar_layout()

    def _remember_sidebar_restore_width(self) -> None:
        main_splitter = self._main_splitter
        if main_splitter is None:
            return

        try:
            sidebar_width = int(main_splitter.sashpos(0))
        except (tk.TclError, TypeError, ValueError):
            LOGGER.debug("Failed to read sidebar sash position.", exc_info=True)
            return

        if sidebar_width > self._ui_scale.px(SIDEBAR_COLLAPSED_WIDTH):
            self._sidebar_restore_width = sidebar_width

    def _apply_sidebar_layout(self) -> None:
        sidebar = self._sidebar
        sidebar_content = self._sidebar_content
        sidebar_toggle_button = self._sidebar_toggle_button
        if sidebar is None or sidebar_content is None or sidebar_toggle_button is None:
            return

        collapsed_width = self._ui_scale.px(SIDEBAR_COLLAPSED_WIDTH)
        if self._sidebar_collapsed:
            sidebar_width = collapsed_width
            sidebar_padding = self._ui_scale.padding(0)
            sidebar_content.grid_remove()
            toggle_text = ">"
        else:
            sidebar_width = self._expanded_sidebar_width(collapsed_width)
            sidebar_padding = self._ui_scale.padding(12, 12, 10, 12)
            sidebar_content.grid()
            toggle_text = "<"

        _safe_configure(sidebar, padding=sidebar_padding, width=sidebar_width)
        _safe_configure(sidebar_toggle_button, text=toggle_text, width=2)
        self._position_sidebar_sash(sidebar_width)

    def _position_sidebar_sash(self, sidebar_width: int) -> None:
        main_splitter = self._main_splitter
        if main_splitter is None:
            return

        try:
            splitter_width = main_splitter.winfo_width()
        except (AttributeError, tk.TclError):
            splitter_width = None

        if splitter_width is not None and splitter_width <= 1:
            try:
                self.after_idle(
                    lambda width=sidebar_width: self._position_sidebar_sash(width)
                )
            except tk.TclError:
                LOGGER.debug(
                    "Failed to schedule sidebar sash positioning.", exc_info=True
                )
            return

        try:
            main_splitter.sashpos(0, sidebar_width)
        except tk.TclError:
            LOGGER.debug("Failed to set sidebar sash position.", exc_info=True)
            return

        self._refresh_sidebar_restore_button()

    def _refresh_sidebar_restore_button(self) -> None:
        toggle_button = self._sidebar_toggle_button
        if toggle_button is not None:
            toggle_text = (
                ">"
                if self._sidebar_collapsed or self._is_sidebar_sash_hidden()
                else "<"
            )
            _safe_configure(toggle_button, text=toggle_text, width=2)

        restore_button = self._sidebar_restore_button
        if restore_button is not None:
            restore_button.grid_remove()

    def _is_sidebar_sash_hidden(self) -> bool:
        main_splitter = self._main_splitter
        if main_splitter is not None:
            try:
                hidden_threshold = max(
                    1,
                    self._ui_scale.px(SIDEBAR_COLLAPSED_WIDTH) // 2,
                )
                return int(main_splitter.sashpos(0)) <= hidden_threshold
            except (tk.TclError, TypeError, ValueError):
                LOGGER.debug("Failed to read sidebar sash position.", exc_info=True)
        return False

    def _expanded_sidebar_width(self, collapsed_width: int) -> int:
        if self._sidebar_restore_width > collapsed_width:
            return self._sidebar_restore_width
        return self._ui_scale.px(SIDEBAR_INITIAL_WIDTH)

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
        pending_session_ids = getattr(
            self,
            "_immediate_run_pending_session_ids",
            None,
        )
        if pending_session_ids is not None:
            pending_session_ids.clear()
        self._main_splitter = None
        self._sidebar = None
        self._sidebar_content = None
        self._sidebar_toggle_button = None
        self._sidebar_restore_button = None
        self._main_area = None
        self._status_bar_container = None
        self._status_bar = None
        self._settings_summary_label = None
        self._scheduled_run_button = None
        self._scheduled_run_status_label = None
        self._workspace_register_button = None
        self._workspace_remove_button = None
        self._workspace_open_button = None
        self._workspace_close_button = None
        self._about_button = None
        self._settings_button = None
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

