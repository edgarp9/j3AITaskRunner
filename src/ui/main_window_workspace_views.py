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

from .main_window_session_views import MainWindowSessionViewsMixin

LOGGER = logging.getLogger("ui.main_window")


def _main_window_global(name: str):
    return getattr(sys.modules["ui.main_window"], name)


class MainWindowWorkspaceViewsMixin(MainWindowSessionViewsMixin):
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
        session_action_buttons: dict[str, ttk.Button] = {}
        for button_spec in WORKSPACE_SESSION_ACTION_BUTTONS:
            action_button = ttk.Button(
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
            )
            action_button.grid(
                row=0,
                column=button_spec.column,
                padx=self._ui_scale.padding(0, 8),
            )
            session_action_buttons[button_spec.text_key] = action_button

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

        workspace_jobs_tree = ttk.Treeview(
            workspace_jobs_frame,
            columns=workspace_task_column_ids(),
            show="headings",
            height=18,
        )
        workspace_jobs_tree.grid(row=0, column=0, sticky="nsew")
        configure_workspace_task_tree_columns(
            workspace_jobs_tree,
            language=_window_language(self),
            initial_width=self._ui_scale.px(WORKSPACE_TASK_LIST_INITIAL_WIDTH),
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
            session_action_buttons=session_action_buttons,
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




