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
from .text_context_menu import bind_readonly_text_context_menu
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


class MainWindowSessionViewsMixin:
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
            on_ai_settings_requested=self._open_session_ai_settings_dialog,
            on_agent_provider_selected=self._handle_session_agent_provider_selected,
            on_model_selected=self._handle_session_model_selected,
            on_reasoning_selected=self._handle_session_reasoning_selected,
            trailing_combobox_pad=True,
            summary_width=None,
        )
        exit_hook_button = ttk.Button(
            execution_option_frame,
            text=_tr_for(self, "button_session_exit_hook"),
            command=lambda target_id=session_tab_id: self._open_session_exit_hook_dialog(
                target_id
            ),
        )
        exit_hook_button.grid(
            row=0,
            column=2,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        immediate_run_button = ttk.Button(
            execution_option_frame,
            text=_tr_for(self, "button_run_now"),
            command=lambda target_id=session_tab_id: self._submit_immediate_job_for_session(
                target_id
            ),
        )
        immediate_run_button.grid(
            row=0,
            column=3,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        ttk.Button(
            execution_option_frame,
            text=_tr_for(self, "button_close_session"),
            command=lambda target_id=session_tab_id: self._close_session(target_id),
        ).grid(
            row=0,
            column=4,
            sticky="w",
        )

        body_notebook = ttk.Notebook(frame)
        body_notebook.grid(
            row=1, column=0, sticky="nsew", pady=self._ui_scale.padding(12, 0)
        )

        prompt_tab_frame = ttk.Frame(body_notebook)
        prompt_tab_frame.columnconfigure(0, weight=1)
        prompt_tab_frame.rowconfigure(0, weight=1)

        auto_commit_var = tk.BooleanVar(value=DEFAULT_AUTO_COMMIT_ENABLED)
        input_widgets = self._build_session_input_widgets(
            prompt_tab_frame,
            workspace_tab_id=session_tab.workspace_tab_id,
            session_tab_id=session_tab_id,
            kind=session_tab.kind,
            auto_commit_var=auto_commit_var,
        )
        input_widgets.frame.grid(row=0, column=0, sticky="nsew")

        progress_log_tab_frame = ttk.Frame(body_notebook)
        progress_log_tab_frame.columnconfigure(0, weight=1)
        progress_log_tab_frame.rowconfigure(0, weight=1)
        history_tab_frame = ttk.Frame(body_notebook)
        history_tab_frame.columnconfigure(0, weight=1)
        history_tab_frame.rowconfigure(0, weight=1)
        candidates_tab_frame: ttk.Frame | None = None
        candidates_status_var: tk.StringVar | None = None
        candidates_status_label: ttk.Label | None = None
        candidates_list_frame: ttk.Frame | None = None
        candidates_continue_button: ttk.Button | None = None

        if session_tab.kind == SessionTabKind.PRESET:
            candidates_tab_frame = ttk.Frame(body_notebook)
            candidates_tab_frame.columnconfigure(0, weight=1)
            candidates_tab_frame.rowconfigure(1, weight=1)
            candidates_status_var = tk.StringVar(
                value=_tr_for(self, "manual_candidates_empty")
            )
            candidates_status_label = ttk.Label(
                candidates_tab_frame,
                textvariable=candidates_status_var,
                foreground=MESSAGE_LABEL_FOREGROUND,
            )
            candidates_status_label.grid(
                row=0,
                column=0,
                sticky="w",
                pady=self._ui_scale.padding(0, 8),
            )
            candidates_container = ttk.Frame(candidates_tab_frame)
            candidates_container.grid(row=1, column=0, sticky="nsew")
            candidates_container.columnconfigure(0, weight=1)
            candidates_container.rowconfigure(0, weight=1)
            candidates_canvas = tk.Canvas(
                candidates_container,
                borderwidth=0,
                highlightthickness=0,
            )
            candidates_scrollbar = ttk.Scrollbar(
                candidates_container,
                orient="vertical",
                command=candidates_canvas.yview,
            )
            candidates_canvas.configure(yscrollcommand=candidates_scrollbar.set)
            candidates_canvas.grid(row=0, column=0, sticky="nsew")
            candidates_scrollbar.grid(row=0, column=1, sticky="ns")
            candidates_list_frame = ttk.Frame(candidates_canvas)
            candidates_window_id = candidates_canvas.create_window(
                (0, 0),
                window=candidates_list_frame,
                anchor="nw",
            )
            candidates_list_frame.bind(
                "<Configure>",
                lambda _event, canvas=candidates_canvas: canvas.configure(
                    scrollregion=canvas.bbox("all")
                ),
            )
            candidates_canvas.bind(
                "<Configure>",
                lambda event,
                canvas=candidates_canvas,
                window_id=candidates_window_id: canvas.itemconfigure(
                    window_id,
                    width=event.width,
                ),
            )
            candidates_continue_button = ttk.Button(
                candidates_tab_frame,
                text=_tr_for(self, "button_continue"),
                command=lambda target_id=session_tab_id: (
                    self._continue_preset_manual_candidates(target_id)
                ),
                state="disabled",
            )
            candidates_continue_button.grid(
                row=2,
                column=0,
                sticky="e",
                pady=self._ui_scale.padding(8, 0),
            )

        log_text = scrolledtext.ScrolledText(
            progress_log_tab_frame, wrap="word", state="disabled"
        )
        history_text = scrolledtext.ScrolledText(
            history_tab_frame, wrap="word", state="disabled"
        )
        configure_text_widget(log_text, scale=self._ui_scale)
        configure_text_widget(history_text, scale=self._ui_scale)
        bind_readonly_text_context_menu(
            log_text,
            menu_parent=self,
            language=lambda: _window_language(self),
        )
        bind_readonly_text_context_menu(
            history_text,
            menu_parent=self,
            language=lambda: _window_language(self),
        )
        log_text.grid(row=0, column=0, sticky="nsew")
        history_text.grid(row=0, column=0, sticky="nsew")

        body_notebook.add(prompt_tab_frame, text=_tr_for(self, "tab_prompt"))
        body_notebook.add(
            progress_log_tab_frame,
            text=_tr_for(self, "tab_progress_log"),
        )
        body_notebook.add(history_tab_frame, text=_tr_for(self, "tab_history"))
        if candidates_tab_frame is not None:
            body_notebook.add(
                candidates_tab_frame,
                text=_tr_for(self, "tab_candidates"),
            )

        session_widgets = SessionWidgets(
            frame=frame,
            body_notebook=body_notebook,
            prompt_tab_frame=prompt_tab_frame,
            progress_log_tab_frame=progress_log_tab_frame,
            history_tab_frame=history_tab_frame,
            candidates_tab_frame=candidates_tab_frame,
            prompt_frame=input_widgets.frame,
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
            register_button=input_widgets.register_button,
            exit_hook_button=exit_hook_button,
            immediate_run_button=immediate_run_button,
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
            preset_candidates_status_var=candidates_status_var,
            preset_candidates_status_label=candidates_status_label,
            preset_candidates_list_frame=candidates_list_frame,
            preset_candidates_continue_button=candidates_continue_button,
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

