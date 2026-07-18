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
    ImportedPromptSessionRegistration,
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

from .main_window_workspace_settings import MainWindowWorkspaceSettingsMixin

LOGGER = logging.getLogger("ui.main_window")


def _main_window_global(name: str):
    return getattr(sys.modules["ui.main_window"], name)


class MainWindowWorkspaceActionsMixin(MainWindowWorkspaceSettingsMixin):
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
        dialog = _main_window_global("BulkPromptImportDialog")(
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
                step_execution_mode=dialog_result.step_execution_mode,
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

        for session_tab in import_result.session_tabs:
            session_widgets = self._ensure_session_view(
                session_tab.session_tab_id
            )
            session_widgets.auto_commit_var.set(dialog_result.auto_commit_enabled)

        self._drain_runtime_events()
        first_registrations_by_session: list[ImportedPromptSessionRegistration] = []
        seen_session_tab_ids: set[str] = set()
        for registration in import_result.registrations:
            session_tab_id = registration.session_tab.session_tab_id
            if session_tab_id in seen_session_tab_ids:
                continue
            seen_session_tab_ids.add(session_tab_id)
            first_registrations_by_session.append(registration)

        for registration in first_registrations_by_session:
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
        session_count = len(import_result.session_tabs)
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

    def _open_session_exit_hook_dialog(self, session_tab_id: str) -> None:
        try:
            session_tab = self._runtime.get_session_tab(session_tab_id)
            dialog = _main_window_global("SessionExitHookDialog")(
                self,
                config=session_tab.exit_hook,
                ui_language=_window_language(self),
            )
            result = dialog.show_modal()
        except Exception:
            LOGGER.exception(
                "Failed to open session exit hook dialog. session_tab_id=%s",
                session_tab_id,
            )
            messagebox.showerror(
                _tr_for(self, "dialog_session_exit_hook_error"),
                _tr_for(self, "dialog_session_exit_hook_open_failed"),
                parent=self,
            )
            return

        if result is None:
            return

        try:
            self._runtime.set_session_exit_hook_config(session_tab_id, result)
        except Exception:
            LOGGER.exception(
                "Failed to save session exit hook settings. session_tab_id=%s",
                session_tab_id,
            )
            messagebox.showerror(
                _tr_for(self, "dialog_session_exit_hook_error"),
                _tr_for(self, "dialog_session_exit_hook_save_failed"),
                parent=self,
            )
            return

        self._set_status(_tr_for(self, "status_session_exit_hook_saved"))

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

        self._select_saved_workspace_after_delete(selection_index)
        self._set_status(
            _tr_for(
                self,
                "status_workspace_removed",
                display_name=deleted_workspace.display_name,
            )
        )

    def _select_saved_workspace_after_delete(self, deleted_index: int) -> None:
        if not self._saved_workspace_paths:
            return

        selection_index = min(deleted_index, len(self._saved_workspace_paths) - 1)
        self._saved_workspaces_listbox.selection_clear(0, tk.END)
        self._saved_workspaces_listbox.selection_set(selection_index)
        self._saved_workspaces_listbox.activate(selection_index)
        self._saved_workspaces_listbox.see(selection_index)

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
