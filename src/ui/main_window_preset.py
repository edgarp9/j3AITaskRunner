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
    Job,
    JobStatus,
    PresetCandidate,
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

from .main_window_preset_candidates import MainWindowPresetCandidateMixin

LOGGER = logging.getLogger("ui.main_window")


def _main_window_global(name: str):
    return getattr(sys.modules["ui.main_window"], name)


class MainWindowPresetMixin(MainWindowPresetCandidateMixin):
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
        self._select_session_progress_log_tab(session_tab_id)
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

    def _submit_immediate_job_for_session(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        if session_widgets.prompt_text is None:
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

        self._immediate_run_pending_session_ids.add(session_tab_id)
        self._refresh_immediate_run_button(session_tab_id)
        try:
            self._runtime.submit_immediate_job(
                session_tab_id,
                prompt,
                auto_commit_enabled=session_widgets.auto_commit_var.get(),
                execution_options=execution_options,
            )
        except Exception:
            self._immediate_run_pending_session_ids.discard(session_tab_id)
            self._refresh_immediate_run_button(session_tab_id)
            LOGGER.exception(
                "Failed to submit immediate job. session_tab_id=%s",
                session_tab_id,
            )
            messagebox.showerror(
                _tr_for(self, "dialog_job_error"),
                _tr_for(self, "dialog_job_register_failed"),
                parent=self,
            )
            return

        session_widgets.prompt_text.delete("1.0", tk.END)
        self._select_session_progress_log_tab(session_tab_id)
        self._set_status(_tr_for(self, "status_immediate_job_requested"))

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
        if work_priority == "manual" and self._queue_mode_is_shared():
            work_priority_var.set(DEFAULT_PRESET_WORK_PRIORITY)
            self._remember_preset_work_priority_for_session(session_tab_id)
            self._refresh_preset_work_priority_options(session_tab_id)
            messagebox.showerror(
                _tr_for(self, "dialog_input_error"),
                localize_runtime_message(
                    "manual 우선순위는 워크스페이스 개별큐에서만 사용할 수 있습니다.",
                    _window_language(self),
                ),
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
                self._select_session_progress_log_tab(session_tab_id)
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
            self._select_session_progress_log_tab(event.session_tab_id)
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


    def _refresh_preset_instruction_options(self, session_tab_id: str) -> None:
        self._request_preset_instruction_options(session_tab_id)

    def _refresh_preset_registration_controls(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        if session_widgets.preset_language_var is None:
            return
        self._refresh_preset_work_priority_options(session_tab_id)
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

