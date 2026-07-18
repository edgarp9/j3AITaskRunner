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

from .main_window_job_actions import MainWindowJobActionsMixin
from .main_window_session_rendering import MainWindowSessionRenderingMixin
from .main_window_tab_actions import MainWindowTabActionsMixin

LOGGER = logging.getLogger("ui.main_window")


def _main_window_global(name: str):
    return getattr(sys.modules["ui.main_window"], name)


class MainWindowQueueSessionMixin(
    MainWindowJobActionsMixin,
    MainWindowSessionRenderingMixin,
    MainWindowTabActionsMixin,
):
    def _toggle_queue(self, workspace_tab_id: str) -> None:
        workspace_view = self._workspace_views.get(workspace_tab_id)
        if self._queue_mode_is_shared() or workspace_view is None:
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

        if self._queue_mode_is_shared():
            workspace_views = getattr(self, "_workspace_views", {})
            if workspace_views:
                self._queue_start_pending_workspace_ids.update(workspace_views)
            else:
                self._queue_start_pending_workspace_ids.add(workspace_tab_id)
        else:
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
            dialog = _main_window_global("ScheduledRunDialog")(self, scheduled_at=self._scheduled_run_at)
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

        self._start_registered_job_queues(
            started_status_key="status_scheduled_run_started",
            no_jobs_status_key="status_scheduled_run_no_jobs",
        )

    def _start_file_drop_registered_jobs(self, request_id: str) -> None:
        del request_id

        self._start_registered_job_queues(
            started_status_key="status_file_drop_started",
            no_jobs_status_key="status_file_drop_no_jobs",
        )

    def _start_registered_job_queues(
        self,
        *,
        started_status_key: str,
        no_jobs_status_key: str,
    ) -> int:
        if self._queue_mode_is_shared():
            target_workspace = next(
                (
                    workspace_tab
                    for workspace_tab in self._runtime.list_workspace_tabs(
                        include_closed=False
                    )
                    if self._workspace_has_runnable_jobs(
                        workspace_tab.workspace_tab_id
                    )
                ),
                None,
            )
            if target_workspace is None:
                self._set_status(_tr_for(self, no_jobs_status_key))
                return 0
            if self._start_queue(target_workspace.workspace_tab_id):
                self._set_status(_tr_for(self, started_status_key, count=1))
                return 1
            return 0

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
            self._set_status(_tr_for(self, no_jobs_status_key))
            return 0

        self._set_status(
            _tr_for(self, started_status_key, count=started_count)
        )
        return started_count

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

        if self._queue_mode_is_shared():
            self._queue_start_pending_workspace_ids.clear()
        else:
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

    def _queue_mode_is_shared(self) -> bool:
        settings = getattr(self._runtime, "settings", None)
        return getattr(settings, "queue_mode", None) == QUEUE_MODE_SHARED

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
        self._refresh_immediate_run_button(session_tab_id, jobs=jobs)

    def _refresh_immediate_run_button(
        self,
        session_tab_id: str,
        *,
        jobs: tuple[Job, ...] | None = None,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        button = getattr(session_widgets, "immediate_run_button", None)
        if button is None:
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        if jobs is None:
            jobs = self._runtime.list_jobs(session_tab_id=session_tab_id)

        has_blocking_job = any(
            job.status
            in (
                JobStatus.QUEUED,
                JobStatus.WAITING_FOR_CONFIGURATION,
                JobStatus.RUNNING,
            )
            for job in jobs
        )
        pending_session_ids = getattr(self, "_immediate_run_pending_session_ids", set())
        enabled = (
            session_tab.kind == SessionTabKind.NORMAL
            and session_tab_id not in pending_session_ids
            and not has_blocking_job
        )
        button.configure(state="normal" if enabled else "disabled")

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






























