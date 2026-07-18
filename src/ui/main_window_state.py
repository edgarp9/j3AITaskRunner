"""UI state containers and constants for the Tkinter main window."""

from __future__ import annotations

from dataclasses import dataclass, field
import tkinter as tk
from tkinter import scrolledtext, ttk

from app.agent_cli_options import SelectOption
from app.runtime import WorkspaceOpenCompletedEvent
from domain import AgentExecutionOptions

from .session_history import SessionHistoryTurnRenderState
from .theme import DARK_THEME

EVENT_POLL_INTERVAL_MS = 150
EVENT_POLL_BACKLOG_INTERVAL_MS = 15
EVENT_POLL_IDLE_MAX_INTERVAL_MS = 750
EVENT_POLL_BACKGROUND_BATCH_SIZE = 32
EVENT_POLL_RUNTIME_BATCH_SIZE = 32
SCHEDULED_RUN_POLL_MAX_INTERVAL_MS = 30_000
OUTPUT_FONT_FAMILY = "Consolas"
TEXT_AUTOSCROLL_BOTTOM_THRESHOLD = 0.98
DEFAULT_WINDOW_WIDTH = 1100
DEFAULT_WINDOW_HEIGHT = 800
MIN_WINDOW_WIDTH = 800
MIN_WINDOW_HEIGHT = 600
SIDEBAR_INITIAL_WIDTH = 180
SIDEBAR_COLLAPSED_WIDTH = 0
MAIN_AREA_MIN_WIDTH = 780
WORKSPACE_SESSIONS_INITIAL_WIDTH = 560
WORKSPACE_TASK_LIST_INITIAL_WIDTH = 180
PROMPT_PANE_INITIAL_HEIGHT = 170
OUTPUT_PANE_INITIAL_HEIGHT = 300
WORKSPACE_TAB_ACTIVE_FILL = DARK_THEME.success_fill
WORKSPACE_TAB_ACTIVE_BORDER = DARK_THEME.success_border
MESSAGE_LABEL_FOREGROUND = DARK_THEME.accent
WAIT_REASON_LABEL_FOREGROUND = DARK_THEME.warning
DEFAULT_AUTO_COMMIT_ENABLED = True
PRESET_COMBOBOX_WIDTH = 10
SESSION_EXECUTION_SUMMARY_WIDTH = 34


@dataclass(slots=True, frozen=True)
class WorkspaceActionButtonSpec:
    """Placement and command metadata for workspace action buttons."""

    text: str
    text_key: str
    command_name: str
    column: int


WORKSPACE_SESSION_ACTION_BUTTONS = (
    WorkspaceActionButtonSpec(
        text="New Session",
        text_key="button_new_session",
        command_name="_create_session_for_workspace",
        column=2,
    ),
    WorkspaceActionButtonSpec(
        text="New Preset",
        text_key="button_new_preset",
        command_name="_create_preset_session_for_workspace",
        column=3,
    ),
    WorkspaceActionButtonSpec(
        text="Import",
        text_key="button_import",
        command_name="_open_bulk_import_dialog_for_workspace",
        column=4,
    ),
)


@dataclass(slots=True, frozen=True)
class ExecutionOptionControlValues:
    """Resolved choices and selected values for one execution-option control row."""

    provider_options: tuple[SelectOption, ...]
    model_options: tuple[SelectOption, ...]
    reasoning_options: tuple[SelectOption, ...]
    provider_value: str
    model_value: str
    reasoning_value: str
    execution_options: AgentExecutionOptions


@dataclass(slots=True)
class ExecutionOptionControls:
    """Widget state for one provider/model/reasoning selector row."""

    agent_provider_var: tk.StringVar
    model_var: tk.StringVar
    reasoning_var: tk.StringVar
    ai_settings_button: ttk.Button
    summary_var: tk.StringVar
    summary_label: ttk.Label
    agent_provider_options: tuple[SelectOption, ...] = ()
    model_options: tuple[SelectOption, ...] = ()
    reasoning_options: tuple[SelectOption, ...] = ()
    execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )


@dataclass(slots=True)
class SessionWidgets:
    """Widget references for one session tab."""

    frame: ttk.Frame
    body_notebook: ttk.Notebook
    prompt_tab_frame: tk.Widget
    progress_log_tab_frame: tk.Widget
    history_tab_frame: tk.Widget
    prompt_frame: ttk.LabelFrame
    started_tab_image: tk.PhotoImage
    prompt_text: scrolledtext.ScrolledText | None
    log_text: scrolledtext.ScrolledText
    history_text: scrolledtext.ScrolledText
    auto_commit_var: tk.BooleanVar
    session_id_var: tk.StringVar
    activity_var: tk.StringVar
    message_var: tk.StringVar
    wait_reason_var: tk.StringVar
    message_label: ttk.Label
    wait_reason_label: ttk.Label
    execution_controls: ExecutionOptionControls
    candidates_tab_frame: tk.Widget | None = None
    register_button: ttk.Button | None = None
    exit_hook_button: ttk.Button | None = None
    immediate_run_button: ttk.Button | None = None
    content_pane: ttk.Panedwindow | None = None
    output_frame: ttk.LabelFrame | None = None
    output_notebook: ttk.Notebook | None = None
    preset_language_var: tk.StringVar | None = None
    preset_instruction_var: tk.StringVar | None = None
    preset_work_priority_var: tk.StringVar | None = None
    preset_language_combobox: ttk.Combobox | None = None
    preset_instruction_combobox: ttk.Combobox | None = None
    preset_work_priority_combobox: ttk.Combobox | None = None
    preset_prompt_prefix_text: scrolledtext.ScrolledText | None = None
    preset_auto_commit_checkbutton: ttk.Checkbutton | None = None
    preset_register_button: ttk.Button | None = None
    preset_action_execution_controls: ExecutionOptionControls | None = None
    preset_candidates_status_var: tk.StringVar | None = None
    preset_candidates_status_label: ttk.Label | None = None
    preset_candidates_list_frame: ttk.Frame | None = None
    preset_candidates_continue_button: ttk.Button | None = None
    preset_candidate_check_vars: dict[str, tk.BooleanVar] = field(default_factory=dict)
    preset_candidate_ids: tuple[str, ...] = ()
    preset_candidates_editable: bool = False
    selected_job_id: str | None = None
    rendered_log_job_id: str | None = None
    rendered_log_line_count: int = 0
    rendered_log_last_line: str | None = None
    rendered_log_language: str | None = None
    rendered_history_turns: tuple[SessionHistoryTurnRenderState, ...] = ()
    rendered_history_source_turns: object | None = None
    rendered_history_language: str | None = None

    @property
    def agent_provider_var(self) -> tk.StringVar:
        return self.execution_controls.agent_provider_var

    @property
    def model_var(self) -> tk.StringVar:
        return self.execution_controls.model_var

    @property
    def reasoning_var(self) -> tk.StringVar:
        return self.execution_controls.reasoning_var

    @property
    def ai_settings_button(self) -> ttk.Button:
        return self.execution_controls.ai_settings_button

    @property
    def execution_summary_var(self) -> tk.StringVar:
        return self.execution_controls.summary_var

    @property
    def execution_summary_label(self) -> ttk.Label:
        return self.execution_controls.summary_label

    @property
    def agent_provider_options(self) -> tuple[SelectOption, ...]:
        return self.execution_controls.agent_provider_options

    @property
    def model_options(self) -> tuple[SelectOption, ...]:
        return self.execution_controls.model_options

    @property
    def reasoning_options(self) -> tuple[SelectOption, ...]:
        return self.execution_controls.reasoning_options

    @property
    def preset_action_agent_provider_var(self) -> tk.StringVar | None:
        controls = self.preset_action_execution_controls
        return controls.agent_provider_var if controls is not None else None

    @property
    def preset_action_model_var(self) -> tk.StringVar | None:
        controls = self.preset_action_execution_controls
        return controls.model_var if controls is not None else None

    @property
    def preset_action_reasoning_var(self) -> tk.StringVar | None:
        controls = self.preset_action_execution_controls
        return controls.reasoning_var if controls is not None else None

    @property
    def preset_action_ai_settings_button(self) -> ttk.Button | None:
        controls = self.preset_action_execution_controls
        return controls.ai_settings_button if controls is not None else None

    @property
    def preset_action_execution_summary_var(self) -> tk.StringVar | None:
        controls = self.preset_action_execution_controls
        return controls.summary_var if controls is not None else None

    @property
    def preset_action_execution_summary_label(self) -> ttk.Label | None:
        controls = self.preset_action_execution_controls
        return controls.summary_label if controls is not None else None

    @property
    def preset_action_agent_provider_options(self) -> tuple[SelectOption, ...]:
        controls = self.preset_action_execution_controls
        return controls.agent_provider_options if controls is not None else ()

    @property
    def preset_action_model_options(self) -> tuple[SelectOption, ...]:
        controls = self.preset_action_execution_controls
        return controls.model_options if controls is not None else ()

    @property
    def preset_action_reasoning_options(self) -> tuple[SelectOption, ...]:
        controls = self.preset_action_execution_controls
        return controls.reasoning_options if controls is not None else ()

    @property
    def preset_action_execution_options(self) -> AgentExecutionOptions:
        controls = self.preset_action_execution_controls
        return (
            controls.execution_options
            if controls is not None
            else AgentExecutionOptions()
        )

    @preset_action_execution_options.setter
    def preset_action_execution_options(
        self,
        execution_options: AgentExecutionOptions,
    ) -> None:
        controls = self.preset_action_execution_controls
        if controls is not None:
            controls.execution_options = execution_options


@dataclass(slots=True)
class SessionOutputAppend:
    """Buffered incremental output update for one session."""

    job_id: str
    lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionInputWidgets:
    """Input-area widget references for a normal or preset session."""

    frame: ttk.LabelFrame
    prompt_tab_frame: tk.Widget | None = None
    prompt_text: scrolledtext.ScrolledText | None = None
    register_button: ttk.Button | None = None
    preset_language_var: tk.StringVar | None = None
    preset_instruction_var: tk.StringVar | None = None
    preset_work_priority_var: tk.StringVar | None = None
    preset_language_combobox: ttk.Combobox | None = None
    preset_instruction_combobox: ttk.Combobox | None = None
    preset_work_priority_combobox: ttk.Combobox | None = None
    preset_prompt_prefix_text: scrolledtext.ScrolledText | None = None
    preset_auto_commit_checkbutton: ttk.Checkbutton | None = None
    preset_register_button: ttk.Button | None = None
    preset_action_execution_controls: ExecutionOptionControls | None = None


@dataclass(slots=True)
class WorkspaceWidgets:
    """Widget references for one workspace tab."""

    frame: ttk.Frame
    content_pane: ttk.Panedwindow
    sessions_area: ttk.Frame
    workspace_jobs_area: ttk.Frame
    session_notebook: ttk.Notebook
    workspace_jobs_tree: ttk.Treeview
    workspace_jobs_summary_var: tk.StringVar
    path_var: tk.StringVar
    queue_var: tk.StringVar
    queue_toggle_var: tk.BooleanVar
    queue_toggle_button: ttk.Checkbutton
    started_tab_image: tk.PhotoImage
    session_action_buttons: dict[str, ttk.Button] = field(default_factory=dict)
    session_views: dict[str, SessionWidgets] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeUiUpdateBatch:
    """Coalesce runtime-driven UI refreshes for one polling tick."""

    refresh_queue_summaries: bool = False
    queue_summary_workspace_ids: set[str] = field(default_factory=set)
    status_message: str | None = None
    persistence_warnings: list[str] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    full_session_views: list[str] = field(default_factory=list)
    session_summaries: set[str] = field(default_factory=set)
    session_histories: set[str] = field(default_factory=set)
    session_outputs: dict[str, SessionOutputAppend | None] = field(default_factory=dict)
    workspace_task_lists: set[str] = field(default_factory=set)
    completed_workspace_paths: set[str] = field(default_factory=set)
    opened_workspaces: list[WorkspaceOpenCompletedEvent] = field(default_factory=list)
    candidate_auto_commit_states: dict[str, bool] = field(default_factory=dict)
