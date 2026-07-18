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
from .agent_settings_dialog import AgentSettingsDialog
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


class MainWindowExecutionWidgetsMixin:
    def _build_execution_option_controls(
        self,
        parent: tk.Widget,
        *,
        session_tab_id: str,
        start_column: int,
        on_ai_settings_requested: Callable[[str], None],
        on_agent_provider_selected: Callable[[str], None],
        on_model_selected: Callable[[str], None],
        on_reasoning_selected: Callable[[str], None],
        trailing_combobox_pad: bool = False,
        summary_width: int | None = SESSION_EXECUTION_SUMMARY_WIDTH,
    ) -> ExecutionOptionControls:
        del on_agent_provider_selected, on_model_selected, on_reasoning_selected
        agent_provider_var = tk.StringVar()
        model_var = tk.StringVar()
        reasoning_var = tk.StringVar()
        summary_var = tk.StringVar()

        ai_settings_button = ttk.Button(
            parent,
            text=_tr_for(self, "button_ai_settings"),
            command=lambda target_id=session_tab_id: on_ai_settings_requested(target_id),
        )
        ai_settings_button.grid(
            row=0,
            column=start_column,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        summary_options: dict[str, object] = {"textvariable": summary_var}
        if summary_width is not None:
            summary_options["width"] = summary_width
        summary_label = ttk.Label(parent, **summary_options)
        summary_label.grid(
            row=0,
            column=start_column + 1,
            sticky="w",
            padx=self._ui_scale.padding(0, 8) if trailing_combobox_pad else 0,
        )

        return ExecutionOptionControls(
            agent_provider_var=agent_provider_var,
            model_var=model_var,
            reasoning_var=reasoning_var,
            ai_settings_button=ai_settings_button,
            summary_var=summary_var,
            summary_label=summary_label,
        )

    def _build_session_input_widgets(
        self,
        parent: tk.Widget,
        *,
        workspace_tab_id: str,
        session_tab_id: str,
        kind: SessionTabKind,
        auto_commit_var: tk.BooleanVar,
    ) -> SessionInputWidgets:
        if _session_kind_uses_prompt_editor(kind):
            return self._build_prompt_editor_widgets(
                parent,
                session_tab_id=session_tab_id,
                auto_commit_var=auto_commit_var,
            )
        return self._build_preset_input_widgets(
            parent,
            workspace_tab_id=workspace_tab_id,
            session_tab_id=session_tab_id,
            auto_commit_var=auto_commit_var,
        )

    def _build_prompt_editor_widgets(
        self,
        parent: tk.Widget,
        *,
        session_tab_id: str,
        auto_commit_var: tk.BooleanVar,
    ) -> SessionInputWidgets:
        prompt_frame = ttk.LabelFrame(
            parent,
            text=_tr_for(self, "section_prompt"),
        )
        prompt_frame.columnconfigure(0, weight=1)
        prompt_frame.rowconfigure(0, weight=1)
        prompt_text = scrolledtext.ScrolledText(prompt_frame, height=6, wrap="word")
        configure_text_widget(prompt_text, scale=self._ui_scale)
        bind_editable_text_context_menu(
            prompt_text,
            menu_parent=self,
            language=lambda: _window_language(self),
        )
        prompt_text.grid(row=0, column=0, sticky="nsew")

        prompt_action_frame = ttk.Frame(prompt_frame)
        prompt_action_frame.grid(
            row=1, column=0, sticky="e", pady=self._ui_scale.padding(8, 0)
        )
        ttk.Checkbutton(
            prompt_action_frame,
            text=_tr_for(self, "checkbox_auto_commit"),
            variable=auto_commit_var,
        ).grid(row=0, column=0, sticky="e", padx=self._ui_scale.padding(0, 8))
        register_button = ttk.Button(
            prompt_action_frame,
            text=_tr_for(self, "button_register"),
            command=lambda target_id=session_tab_id: self._submit_job_for_session(
                target_id
            ),
        )
        register_button.grid(row=0, column=1, sticky="e")
        return SessionInputWidgets(
            frame=prompt_frame,
            prompt_tab_frame=prompt_frame,
            prompt_text=prompt_text,
            register_button=register_button,
        )

    def _build_preset_input_widgets(
        self,
        parent: tk.Widget,
        *,
        workspace_tab_id: str,
        session_tab_id: str,
        auto_commit_var: tk.BooleanVar,
    ) -> SessionInputWidgets:
        preset_frame = ttk.LabelFrame(
            parent,
            text=_tr_for(self, "section_preset"),
        )
        preset_frame.columnconfigure(0, weight=1)
        preset_frame.rowconfigure(2, weight=1)

        languages: tuple[str, ...] = ()
        instructions: tuple[str, ...] = ()
        selected_language = ""
        selected_instruction = ""

        language_var = tk.StringVar(value=selected_language)
        instruction_var = tk.StringVar(value=selected_instruction)
        work_priority_var = tk.StringVar(
            value=self._default_preset_work_priority_for_workspace(workspace_tab_id)
        )
        work_priority_options = self._preset_work_priority_options()
        selector_frame = ttk.Frame(preset_frame)
        selector_frame.grid(row=0, column=0, sticky="w")

        ttk.Label(selector_frame, text=_tr_for(self, "preset_language")).grid(
            row=0,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        language_combobox = ttk.Combobox(
            selector_frame,
            textvariable=language_var,
            values=languages,
            state="readonly" if languages else "disabled",
            width=PRESET_COMBOBOX_WIDTH,
        )
        language_combobox.grid(
            row=0,
            column=1,
            sticky="w",
            padx=self._ui_scale.padding(0, 14),
        )
        language_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event, target_id=session_tab_id: self._handle_preset_language_selected(
                target_id
            ),
        )

        ttk.Label(selector_frame, text=_tr_for(self, "preset_instruction")).grid(
            row=0,
            column=2,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        instruction_combobox = ttk.Combobox(
            selector_frame,
            textvariable=instruction_var,
            values=instructions,
            state="readonly" if instructions else "disabled",
            width=PRESET_COMBOBOX_WIDTH,
        )
        instruction_combobox.grid(
            row=0,
            column=3,
            sticky="w",
            padx=self._ui_scale.padding(0, 14),
        )
        instruction_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event, target_id=session_tab_id: self._handle_preset_instruction_selected(
                target_id
            ),
        )

        ttk.Label(selector_frame, text=_tr_for(self, "preset_priority")).grid(
            row=0,
            column=4,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        work_priority_combobox = ttk.Combobox(
            selector_frame,
            textvariable=work_priority_var,
            values=work_priority_options,
            state="readonly",
            width=PRESET_COMBOBOX_WIDTH,
        )
        work_priority_combobox.grid(row=0, column=5, sticky="w")
        work_priority_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event, target_id=session_tab_id: self._handle_preset_work_priority_selected(
                target_id
            ),
        )

        ttk.Label(preset_frame, text=_tr_for(self, "preset_prompt_prefix")).grid(
            row=1,
            column=0,
            sticky="w",
            pady=self._ui_scale.padding(8, 4),
        )
        prompt_prefix_text = scrolledtext.ScrolledText(
            preset_frame,
            height=4,
            wrap="word",
        )
        configure_text_widget(prompt_prefix_text, scale=self._ui_scale)
        bind_editable_text_context_menu(
            prompt_prefix_text,
            menu_parent=self,
            language=lambda: _window_language(self),
        )
        default_prompt_prefix = self._default_preset_prompt_prefix_for_workspace(
            workspace_tab_id
        )
        if default_prompt_prefix:
            prompt_prefix_text.insert("1.0", default_prompt_prefix)
        prompt_prefix_text.grid(row=2, column=0, sticky="nsew")

        preset_action_frame = ttk.Frame(preset_frame)
        preset_action_frame.grid(
            row=3,
            column=0,
            sticky="e",
            pady=self._ui_scale.padding(8, 0),
        )
        auto_commit_checkbutton = ttk.Checkbutton(
            preset_action_frame,
            text=_tr_for(self, "checkbox_auto_commit"),
            variable=auto_commit_var,
        )
        auto_commit_checkbutton.grid(
            row=0,
            column=0,
            sticky="e",
            padx=self._ui_scale.padding(0, 8),
        )
        preset_action_execution_controls = self._build_execution_option_controls(
            preset_action_frame,
            session_tab_id=session_tab_id,
            start_column=1,
            on_ai_settings_requested=self._open_preset_action_ai_settings_dialog,
            on_agent_provider_selected=(
                self._handle_preset_action_agent_provider_selected
            ),
            on_model_selected=self._handle_preset_action_model_selected,
            on_reasoning_selected=self._handle_preset_action_reasoning_selected,
            trailing_combobox_pad=True,
        )
        register_button = ttk.Button(
            preset_action_frame,
            text=_tr_for(self, "button_register"),
            command=lambda target_id=session_tab_id: self._submit_preset_job_for_session(
                target_id
            ),
        )
        register_button.grid(row=0, column=3, sticky="e")

        return SessionInputWidgets(
            frame=preset_frame,
            prompt_tab_frame=preset_frame,
            preset_language_var=language_var,
            preset_instruction_var=instruction_var,
            preset_work_priority_var=work_priority_var,
            preset_language_combobox=language_combobox,
            preset_instruction_combobox=instruction_combobox,
            preset_work_priority_combobox=work_priority_combobox,
            preset_prompt_prefix_text=prompt_prefix_text,
            preset_auto_commit_checkbutton=auto_commit_checkbutton,
            preset_register_button=register_button,
            preset_action_execution_controls=preset_action_execution_controls,
        )

