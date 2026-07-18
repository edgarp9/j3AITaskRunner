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


class MainWindowLifecycleMixin:
    def _handle_dpi_metrics_changed(self, metrics: DpiMetrics) -> None:
        previous_sidebar_default_width = self._ui_scale.px(SIDEBAR_INITIAL_WIDTH)
        self._dpi_metrics = metrics
        self._ui_scale = UiScale.from_metrics(metrics)
        if self._sidebar_restore_width == previous_sidebar_default_width:
            self._sidebar_restore_width = self._ui_scale.px(SIDEBAR_INITIAL_WIDTH)
        apply_dark_theme(self, scale=self._ui_scale)
        self._apply_scaled_options_after_dpi_change()

    def _apply_scaled_options_after_dpi_change(self) -> None:
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        if self._sidebar is not None:
            _safe_configure(
                self._sidebar,
                padding=self._ui_scale.padding(12, 12, 10, 12),
            )
            self._apply_sidebar_layout()
        if self._main_area is not None:
            _safe_configure(
                self._main_area,
                padding=self._ui_scale.padding(0, 12, 12, 12),
                width=self._ui_scale.px(MAIN_AREA_MIN_WIDTH),
            )
        if self._status_bar_container is not None:
            self._status_bar_container.grid_configure(
                pady=self._ui_scale.padding(12, 0)
            )
        if self._sidebar_toggle_button is not None:
            self._sidebar_toggle_button.grid_configure(
                padx=self._ui_scale.padding(0, 6)
            )
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

