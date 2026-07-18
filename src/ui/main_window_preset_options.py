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


class MainWindowPresetOptionsMixin:
    def _next_preset_option_request_id(self) -> int:
        self._preset_option_request_sequence += 1
        return self._preset_option_request_sequence

    def _request_preset_language_options(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        if session_widgets.preset_language_var is None:
            return

        session_widgets.preset_language_var.set("")
        if session_widgets.preset_instruction_var is not None:
            session_widgets.preset_instruction_var.set("")
        if session_widgets.preset_language_combobox is not None:
            session_widgets.preset_language_combobox.configure(
                values=(),
                state="disabled",
            )
        if session_widgets.preset_instruction_combobox is not None:
            session_widgets.preset_instruction_combobox.configure(
                values=(),
                state="disabled",
            )

        session_tab = self._runtime.get_session_tab(session_tab_id)
        request_id = self._next_preset_option_request_id()
        self._preset_language_request_ids[session_tab_id] = request_id
        self._runtime.load_preset_languages_in_background(
            request_id=request_id,
            session_tab_id=session_tab_id,
            workspace_tab_id=session_tab.workspace_tab_id,
        )

    def _request_preset_instruction_options(
        self,
        session_tab_id: str,
        *,
        language: str | None = None,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        language_var = session_widgets.preset_language_var
        instruction_var = session_widgets.preset_instruction_var
        instruction_combobox = session_widgets.preset_instruction_combobox
        if (
            language_var is None
            or instruction_var is None
            or instruction_combobox is None
        ):
            return

        selected_language = (
            language if language is not None else language_var.get().strip()
        )
        instruction_var.set("")
        instruction_combobox.configure(values=(), state="disabled")
        self._preset_instruction_request_ids.pop(session_tab_id, None)
        if not selected_language:
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        request_id = self._next_preset_option_request_id()
        self._preset_instruction_request_ids[session_tab_id] = request_id
        self._runtime.load_preset_instructions_in_background(
            request_id=request_id,
            session_tab_id=session_tab_id,
            workspace_tab_id=session_tab.workspace_tab_id,
            language=selected_language,
        )

    def _apply_preset_language_options_loaded(
        self,
        event: PresetPromptLanguagesLoadedEvent,
    ) -> str | None:
        if (
            self._preset_language_request_ids.get(event.session_tab_id)
            != event.request_id
        ):
            return None
        self._preset_language_request_ids.pop(event.session_tab_id, None)
        if not self._has_session_view(event.session_tab_id):
            return None

        session_widgets = self._get_session_widgets(event.session_tab_id)
        language_var = session_widgets.preset_language_var
        language_combobox = session_widgets.preset_language_combobox
        instruction_var = session_widgets.preset_instruction_var
        instruction_combobox = session_widgets.preset_instruction_combobox
        if language_var is None or language_combobox is None:
            return None

        if event.error_message is not None:
            language_var.set("")
            language_combobox.configure(values=(), state="disabled")
            if instruction_var is not None:
                instruction_var.set("")
            if instruction_combobox is not None:
                instruction_combobox.configure(values=(), state="disabled")
            return _tr_for(self, "status_preset_languages_failed")

        languages = event.languages
        selected_language = self._default_preset_language_for_workspace(
            event.workspace_tab_id,
            languages,
        )
        language_var.set(selected_language)
        is_registered = self._preset_registration_is_locked(event.session_tab_id)
        language_combobox.configure(
            values=languages,
            state="readonly" if languages and not is_registered else "disabled",
        )
        if selected_language:
            self._request_preset_instruction_options(
                event.session_tab_id,
                language=selected_language,
            )
        elif instruction_var is not None and instruction_combobox is not None:
            instruction_var.set("")
            instruction_combobox.configure(values=(), state="disabled")
        return None

    def _apply_preset_instruction_options_loaded(
        self,
        event: PresetPromptInstructionsLoadedEvent,
    ) -> str | None:
        if (
            self._preset_instruction_request_ids.get(event.session_tab_id)
            != event.request_id
        ):
            return None
        self._preset_instruction_request_ids.pop(event.session_tab_id, None)
        if not self._has_session_view(event.session_tab_id):
            return None

        session_widgets = self._get_session_widgets(event.session_tab_id)
        language_var = session_widgets.preset_language_var
        instruction_var = session_widgets.preset_instruction_var
        instruction_combobox = session_widgets.preset_instruction_combobox
        if (
            language_var is None
            or instruction_var is None
            or instruction_combobox is None
        ):
            return None
        if language_var.get().strip() != event.language:
            return None

        if event.error_message is not None:
            instruction_var.set("")
            instruction_combobox.configure(values=(), state="disabled")
            return _tr_for(self, "status_preset_instructions_failed")

        instructions = event.instructions
        is_registered = self._preset_registration_is_locked(event.session_tab_id)
        instruction_combobox.configure(
            values=instructions,
            state="readonly" if instructions and not is_registered else "disabled",
        )
        instruction_var.set(
            self._default_preset_instruction_for_workspace(
                event.workspace_tab_id,
                event.language,
                instructions,
            )
        )
        return None

    def _default_preset_language_for_workspace(
        self,
        workspace_tab_id: str,
        languages: tuple[str, ...],
    ) -> str:
        if not languages:
            return ""

        key = self._workspace_preset_language_key(workspace_tab_id)
        remembered_language = self._workspace_preset_languages.get(key)
        if remembered_language in languages:
            return remembered_language
        return languages[0]

    def _default_preset_instruction_for_workspace(
        self,
        workspace_tab_id: str,
        language: str,
        instructions: tuple[str, ...],
    ) -> str:
        if not instructions:
            return ""

        key = self._workspace_preset_instruction_key(workspace_tab_id, language)
        remembered_instruction = self._workspace_preset_instructions.get(key)
        if remembered_instruction in instructions:
            return remembered_instruction
        return instructions[0]

    def _default_preset_work_priority_for_workspace(self, workspace_tab_id: str) -> str:
        key = self._workspace_preset_language_key(workspace_tab_id)
        remembered_priority = self._workspace_preset_work_priorities.get(key)
        available_priorities = self._preset_work_priority_options()
        if remembered_priority in available_priorities:
            return remembered_priority
        return DEFAULT_PRESET_WORK_PRIORITY

    def _default_preset_prompt_prefix_for_workspace(self, workspace_tab_id: str) -> str:
        key = self._workspace_preset_language_key(workspace_tab_id)
        return self._workspace_preset_prompt_prefixes.get(key, "")

    def _default_preset_action_execution_options_for_workspace(
        self,
        workspace_tab_id: str,
        *,
        fallback: AgentExecutionOptions,
    ) -> AgentExecutionOptions:
        key = self._workspace_preset_language_key(workspace_tab_id)
        remembered_options = self._workspace_preset_action_execution_options.get(key)
        if remembered_options is not None:
            return remembered_options
        return fallback

    def _handle_preset_language_selected(self, session_tab_id: str) -> None:
        self._remember_preset_language_for_session(session_tab_id)
        self._refresh_preset_instruction_options(session_tab_id)

    def _handle_preset_instruction_selected(self, session_tab_id: str) -> None:
        self._remember_preset_instruction_for_session(session_tab_id)

    def _handle_preset_work_priority_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        work_priority_var = session_widgets.preset_work_priority_var
        if (
            work_priority_var is not None
            and work_priority_var.get().strip() not in self._preset_work_priority_options()
        ):
            work_priority_var.set(DEFAULT_PRESET_WORK_PRIORITY)
            self._refresh_preset_work_priority_options(session_tab_id)
        self._remember_preset_work_priority_for_session(session_tab_id)

    def _remember_preset_language_for_session(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        language_var = session_widgets.preset_language_var
        if language_var is None:
            return

        language = language_var.get().strip()
        if not language:
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        key = self._workspace_preset_language_key(session_tab.workspace_tab_id)
        self._workspace_preset_languages[key] = language

    def _remember_preset_instruction_for_session(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        language_var = session_widgets.preset_language_var
        instruction_var = session_widgets.preset_instruction_var
        if language_var is None or instruction_var is None:
            return

        language = language_var.get().strip()
        instruction = instruction_var.get().strip()
        if not language or not instruction:
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        key = self._workspace_preset_instruction_key(
            session_tab.workspace_tab_id,
            language,
        )
        self._workspace_preset_instructions[key] = instruction

    def _remember_preset_work_priority_for_session(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        work_priority_var = session_widgets.preset_work_priority_var
        if work_priority_var is None:
            return

        work_priority = work_priority_var.get().strip()
        if work_priority not in self._preset_work_priority_options():
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        key = self._workspace_preset_language_key(session_tab.workspace_tab_id)
        self._workspace_preset_work_priorities[key] = work_priority

    def _preset_work_priority_options(self) -> tuple[str, ...]:
        queue_mode_is_shared = getattr(self, "_queue_mode_is_shared", lambda: False)
        if queue_mode_is_shared():
            return tuple(
                priority
                for priority in PRESET_WORK_PRIORITY_OPTIONS
                if priority != "manual"
            )
        return PRESET_WORK_PRIORITY_OPTIONS

    def _refresh_preset_work_priority_options(self, session_tab_id: str) -> None:
        if not self._has_session_view(session_tab_id):
            return

        session_widgets = self._get_session_widgets(session_tab_id)
        work_priority_var = session_widgets.preset_work_priority_var
        work_priority_combobox = session_widgets.preset_work_priority_combobox
        if work_priority_var is None or work_priority_combobox is None:
            return

        priorities = self._preset_work_priority_options()
        if work_priority_var.get().strip() not in priorities:
            work_priority_var.set(DEFAULT_PRESET_WORK_PRIORITY)
        work_priority_combobox.configure(values=priorities)
        if self._preset_registration_is_locked(session_tab_id):
            work_priority_combobox.configure(state="disabled")
        else:
            work_priority_combobox.configure(
                state="readonly" if priorities else "disabled"
            )

    def _preset_prompt_prefix_for_session(self, session_tab_id: str) -> str:
        session_widgets = self._get_session_widgets(session_tab_id)
        prompt_prefix_text = session_widgets.preset_prompt_prefix_text
        if prompt_prefix_text is None:
            return ""
        return prompt_prefix_text.get("1.0", tk.END).strip()

    def _remember_preset_prompt_prefix_for_session(self, session_tab_id: str) -> None:
        prompt_prefix = self._preset_prompt_prefix_for_session(session_tab_id)
        session_tab = self._runtime.get_session_tab(session_tab_id)
        self._remember_preset_prompt_prefix_for_workspace(
            session_tab.workspace_tab_id,
            prompt_prefix,
        )

    def _remember_preset_prompt_prefix_for_workspace(
        self,
        workspace_tab_id: str,
        prompt_prefix: str,
    ) -> None:
        key = self._workspace_preset_language_key(workspace_tab_id)
        normalized_prompt_prefix = prompt_prefix.strip()
        if normalized_prompt_prefix:
            self._workspace_preset_prompt_prefixes[key] = normalized_prompt_prefix
        else:
            self._workspace_preset_prompt_prefixes.pop(key, None)

    def _remember_preset_action_execution_options_for_session(
        self,
        session_tab_id: str,
    ) -> None:
        remembered_options = getattr(
            self,
            "_workspace_preset_action_execution_options",
            None,
        )
        if remembered_options is None:
            return

        session_widgets = self._get_session_widgets(session_tab_id)
        execution_options = session_widgets.preset_action_execution_options
        session_tab = self._runtime.get_session_tab(session_tab_id)
        key = self._workspace_preset_language_key(session_tab.workspace_tab_id)
        remembered_options[key] = execution_options

    def _workspace_preset_language_key(self, workspace_tab_id: str) -> str:
        workspace_tab = self._runtime.get_workspace_tab(workspace_tab_id)
        return (
            canonicalize_workspace_path(workspace_tab.workspace_path)
            or workspace_tab.workspace_path
            or workspace_tab_id
        )

    def _workspace_preset_instruction_key(
        self,
        workspace_tab_id: str,
        language: str,
    ) -> tuple[str, str]:
        return (self._workspace_preset_language_key(workspace_tab_id), language)

