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


class MainWindowJobActionsMixin:
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
            dialog = _main_window_global("PromptViewerDialog")(
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
        resize_workspace_task_columns(jobs_tree, available_width)

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
        sync_workspace_task_list(
            workspace_view.workspace_jobs_tree,
            workspace_view.workspace_jobs_summary_var,
            jobs,
            language=_window_language(self),
            job_session_label=self._job_session_label,
            preferred_job_id=preferred_job_id,
        )

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

