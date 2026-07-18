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
    FileDropCommandRequestedEvent,
    FileDropIssueEvent,
    MAX_JOB_PROGRESS_LOG_LINES,
    PersistenceIssueEvent,
    PresetAnalysisJobSubmittedEvent,
    PresetAnalysisJobSubmissionFailedEvent,
    PresetCandidateJobsRegisteredEvent,
    PresetManualCandidateSelectionClearedEvent,
    PresetManualCandidateSelectionContinuedEvent,
    PresetManualCandidateSelectionRequiredEvent,
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
    FILE_DROP_COMMAND_START_REGISTERED_JOBS,
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


class MainWindowEventsMixin:
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
            pending_session_ids = getattr(
                self,
                "_immediate_run_pending_session_ids",
                None,
            )
            if pending_session_ids is not None:
                pending_session_ids.discard(event.session_tab_id)
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
            queue_mode_is_shared = getattr(self, "_queue_mode_is_shared", lambda: False)
            if queue_mode_is_shared():
                self._queue_start_pending_workspace_ids.clear()
            else:
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

        if isinstance(event, PresetManualCandidateSelectionRequiredEvent):
            self._apply_manual_candidate_selection_required(event, updates)
            return

        if isinstance(event, PresetManualCandidateSelectionContinuedEvent):
            self._apply_manual_candidate_selection_continued(event, updates)
            return

        if isinstance(event, PresetManualCandidateSelectionClearedEvent):
            self._apply_manual_candidate_selection_cleared(event, updates)
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

        if isinstance(event, FileDropCommandRequestedEvent):
            if event.command_type == FILE_DROP_COMMAND_START_REGISTERED_JOBS:
                self._start_file_drop_registered_jobs(event.request_id)
                return

            updates.status_message = _tr_for(
                self,
                "status_file_drop_request_failed",
                reason=event.command_type,
            )
            return

        if isinstance(event, FileDropIssueEvent):
            updates.status_message = self._file_drop_issue_status_message(event)
            return

        if isinstance(event, RuntimeActionFailedEvent):
            pending_session_ids = getattr(
                self,
                "_immediate_run_pending_session_ids",
                None,
            )
            if pending_session_ids is not None:
                pending_session_ids.clear()
            if (
                event.title == "큐 오류"
                and event.message == "큐를 시작할 수 없습니다."
                and event.workspace_tab_id is not None
            ):
                queue_mode_is_shared = getattr(self, "_queue_mode_is_shared", lambda: False)
                if queue_mode_is_shared():
                    self._queue_start_pending_workspace_ids.clear()
                else:
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

    def _file_drop_issue_status_message(self, event: FileDropIssueEvent) -> str:
        if event.code == "delete_failed":
            return _tr_for(self, "status_file_drop_delete_failed")

        reason = localize_runtime_message(event.message, _window_language(self))
        if event.code == "unknown_command_type" and event.detail:
            reason = _tr_for(
                self,
                "status_file_drop_unknown_command",
                command_type=event.detail,
            )
        return _tr_for(self, "status_file_drop_request_failed", reason=reason)

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

