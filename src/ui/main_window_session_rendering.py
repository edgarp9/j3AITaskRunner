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

LOGGER = logging.getLogger("ui.main_window")


def _main_window_global(name: str):
    return getattr(sys.modules["ui.main_window"], name)


class MainWindowSessionRenderingMixin:
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

            changed_index = session_history_first_changed_index(
                rendered_turns, turns
            )
            if changed_index is None:
                session_widgets.rendered_history_source_turns = turns
                return

            replace_from = session_history_prefix_length(
                rendered_turns, changed_index
            )
            replacement_renders = render_session_history_turns(
                turns[changed_index:],
                start_index=changed_index + 1,
                language=language,
                content_length=replace_from,
            )
            replacement_turns = tuple(
                rendered_turn for rendered_turn, _block_text in replacement_renders
            )
            replacement_content = join_session_history_blocks(replacement_renders)
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

        rendered_history = render_session_history_turns(
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
            join_session_history_blocks(rendered_history),
        )
        self._mark_session_history_rendered(
            session_widgets,
            rendered_turns=rendered_turns,
            source_turns=turns,
            language=language,
        )

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

