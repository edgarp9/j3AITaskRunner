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

from .main_window_execution_widgets import MainWindowExecutionWidgetsMixin

LOGGER = logging.getLogger("ui.main_window")


def _main_window_global(name: str):
    return getattr(sys.modules["ui.main_window"], name)


class MainWindowExecutionControlsMixin(MainWindowExecutionWidgetsMixin):
    def _refresh_all_session_execution_option_controls(self) -> None:
        for workspace_view in self._workspace_views.values():
            for session_tab_id in tuple(workspace_view.session_views):
                self._refresh_session_execution_option_controls(session_tab_id)

    def _refresh_session_execution_option_controls(self, session_tab_id: str) -> None:
        if not self._has_session_view(session_tab_id):
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        execution_options = session_tab.execution_options
        locked = session_tab.execution_options_locked or (
            session_tab_id in self._preset_registration_pending_session_ids
        )
        control_values = self._resolve_execution_option_control_values(
            execution_options,
            locked=locked,
        )
        if not locked and control_values.execution_options != execution_options:
            self._runtime.set_session_execution_options(
                session_tab_id,
                control_values.execution_options,
            )

        self._apply_execution_option_control_values(
            controls=session_widgets.execution_controls,
            control_values=control_values,
            locked=locked,
        )
        self._refresh_preset_action_execution_option_controls(session_tab_id)

    def _refresh_preset_action_execution_option_controls(
        self,
        session_tab_id: str,
    ) -> None:
        if not self._has_session_view(session_tab_id):
            return

        session_tab = self._runtime.get_session_tab(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return

        locked = session_tab.execution_options_locked or (
            session_tab_id in self._preset_registration_pending_session_ids
        )
        execution_options = controls.execution_options
        control_values = self._resolve_execution_option_control_values(
            execution_options,
            locked=locked,
        )
        if not locked:
            controls.execution_options = control_values.execution_options
            self._remember_preset_action_execution_options_for_session(session_tab_id)

        self._apply_execution_option_control_values(
            controls=controls,
            control_values=control_values,
            locked=locked,
        )

    def _resolve_execution_option_control_values(
        self,
        execution_options: AgentExecutionOptions,
        *,
        locked: bool,
    ) -> ExecutionOptionControlValues:
        settings = self._runtime.settings
        if locked:
            provider_options = (
                self._agent_provider_option_for_value(execution_options.agent_provider),
            )
        else:
            provider_options = build_configured_agent_provider_select_options(
                execution_options.agent_provider,
                settings,
            )

        provider_value = execution_options.agent_provider
        resolved_execution_options = execution_options
        provider_values = {option.value for option in provider_options}
        if provider_options and provider_value not in provider_values:
            provider_value = provider_options[0].value
            if not locked:
                resolved_execution_options = AgentExecutionOptions(
                    agent_provider=provider_value,
                    model="",
                    reasoning_effort="",
                )
        elif not provider_options:
            provider_value = ""

        if provider_value:
            model_options = build_model_select_options(
                resolved_execution_options.model,
                agent_provider=provider_value,
                auto_label=_tr_for(self, "settings_auto"),
                saved_value_suffix=_tr_for(self, "settings_saved_value_suffix"),
            )
            model_value = self._option_value_or_default(
                model_options,
                resolved_execution_options.model,
            )
            reasoning_options = build_reasoning_select_options(
                resolved_execution_options.reasoning_effort,
                agent_provider=provider_value,
                model=model_value,
                auto_label=_tr_for(self, "settings_auto"),
                saved_value_suffix=_tr_for(self, "settings_saved_value_suffix"),
            )
            reasoning_value = self._option_value_or_default(
                reasoning_options,
                resolved_execution_options.reasoning_effort,
            )
        else:
            model_options = ()
            reasoning_options = ()
            model_value = ""
            reasoning_value = ""

        return ExecutionOptionControlValues(
            provider_options=provider_options,
            model_options=model_options,
            reasoning_options=reasoning_options,
            provider_value=provider_value,
            model_value=model_value,
            reasoning_value=reasoning_value,
            execution_options=resolved_execution_options,
        )

    @staticmethod
    def _apply_execution_option_control_values(
        *,
        controls: ExecutionOptionControls,
        control_values: ExecutionOptionControlValues,
        locked: bool,
    ) -> None:
        controls.agent_provider_options = control_values.provider_options
        controls.model_options = control_values.model_options
        controls.reasoning_options = control_values.reasoning_options
        controls.agent_provider_var.set(
            find_option_label(
                control_values.provider_options,
                control_values.provider_value,
            )
            if control_values.provider_value
            else ""
        )
        controls.model_var.set(
            find_option_label(
                control_values.model_options,
                control_values.model_value,
            )
        )
        controls.reasoning_var.set(
            find_option_label(
                control_values.reasoning_options,
                control_values.reasoning_value,
            )
        )
        controls.execution_options = AgentExecutionOptions(
            agent_provider=control_values.provider_value,
            model=control_values.model_value,
            reasoning_effort=control_values.reasoning_value,
        )
        controls.summary_var.set(
            MainWindowExecutionControlsMixin._execution_option_summary_text(
                provider_label=controls.agent_provider_var.get(),
                model_label=controls.model_var.get(),
                reasoning_label=controls.reasoning_var.get(),
            )
        )
        controls.ai_settings_button.configure(
            state="normal" if control_values.provider_options and not locked else "disabled"
        )

    def _set_session_execution_option_controls_enabled(
        self,
        session_widgets: SessionWidgets,
        *,
        enabled: bool,
    ) -> None:
        self._set_execution_option_button_state(
            controls=session_widgets.execution_controls,
            enabled=enabled,
        )
        self._set_preset_action_execution_option_controls_enabled(
            session_widgets,
            enabled=enabled,
        )

    def _set_preset_action_execution_option_controls_enabled(
        self,
        session_widgets: SessionWidgets,
        *,
        enabled: bool,
    ) -> None:
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return
        self._set_execution_option_button_state(
            controls=controls,
            enabled=enabled,
        )

    @staticmethod
    def _set_execution_option_button_state(
        *,
        controls: ExecutionOptionControls,
        enabled: bool,
    ) -> None:
        controls.ai_settings_button.configure(
            state="normal" if controls.agent_provider_options and enabled else "disabled"
        )

    _set_execution_option_combobox_states = _set_execution_option_button_state

    def _open_session_ai_settings_dialog(self, session_tab_id: str) -> None:
        self._refresh_session_execution_option_controls(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.execution_controls
        result = self._open_ai_settings_dialog(controls)
        if result is None:
            return
        self._runtime.set_session_execution_options(session_tab_id, result)
        self._refresh_session_execution_option_controls(session_tab_id)

    def _open_preset_action_ai_settings_dialog(self, session_tab_id: str) -> None:
        self._refresh_preset_action_execution_option_controls(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return
        result = self._open_ai_settings_dialog(controls)
        if result is None:
            return
        controls.execution_options = result
        self._remember_preset_action_execution_options_for_session(session_tab_id)
        self._refresh_preset_action_execution_option_controls(session_tab_id)

    def _open_ai_settings_dialog(
        self,
        controls: ExecutionOptionControls,
    ) -> AgentExecutionOptions | None:
        if not controls.agent_provider_options:
            return None
        dialog = AgentSettingsDialog(
            self,
            execution_options=controls.execution_options,
            provider_options=controls.agent_provider_options,
            ui_language=_window_language(self),
        )
        return dialog.show_modal()

    def _handle_session_agent_provider_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.execution_controls
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=False,
            include_reasoning=False,
        )
        if execution_options is None:
            return
        self._runtime.set_session_execution_options(
            session_tab_id,
            AgentExecutionOptions(agent_provider=execution_options.agent_provider),
        )
        self._refresh_session_execution_option_controls(session_tab_id)

    def _handle_session_model_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.execution_controls
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=False,
        )
        if execution_options is None:
            return
        self._runtime.set_session_execution_options(
            session_tab_id,
            AgentExecutionOptions(
                agent_provider=execution_options.agent_provider,
                model=execution_options.model,
            ),
        )
        self._refresh_session_execution_option_controls(session_tab_id)

    def _handle_session_reasoning_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.execution_controls
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=True,
        )
        if execution_options is None:
            return
        self._runtime.set_session_execution_options(
            session_tab_id,
            execution_options,
        )

    def _handle_preset_action_agent_provider_selected(
        self,
        session_tab_id: str,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=False,
            include_reasoning=False,
        )
        if execution_options is None:
            return
        controls.execution_options = AgentExecutionOptions(
            agent_provider=execution_options.agent_provider
        )
        self._remember_preset_action_execution_options_for_session(session_tab_id)
        self._refresh_preset_action_execution_option_controls(session_tab_id)

    def _handle_preset_action_model_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=False,
        )
        if execution_options is None:
            return
        controls.execution_options = AgentExecutionOptions(
            agent_provider=execution_options.agent_provider,
            model=execution_options.model,
        )
        self._remember_preset_action_execution_options_for_session(session_tab_id)
        self._refresh_preset_action_execution_option_controls(session_tab_id)

    def _handle_preset_action_reasoning_selected(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=True,
        )
        if execution_options is None:
            return
        controls.execution_options = execution_options
        self._remember_preset_action_execution_options_for_session(session_tab_id)

    def _execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        self._refresh_session_execution_option_controls(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.execution_controls
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=True,
        )
        if execution_options is None:
            messagebox.showerror(
                _tr_for(self, "dialog_input_error"),
                _tr_for(self, "dialog_agent_provider_required"),
                parent=self,
            )
            return None
        self._runtime.set_session_execution_options(session_tab_id, execution_options)
        return execution_options

    def _preset_action_execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        self._refresh_preset_action_execution_option_controls(session_tab_id)
        session_widgets = self._get_session_widgets(session_tab_id)
        controls = session_widgets.preset_action_execution_controls
        if controls is None:
            return None
        execution_options = self._selected_execution_options_from_controls(
            controls,
            include_model=True,
            include_reasoning=True,
        )
        if execution_options is None:
            messagebox.showerror(
                _tr_for(self, "dialog_input_error"),
                _tr_for(self, "dialog_agent_provider_required"),
                parent=self,
            )
            return None
        controls.execution_options = execution_options
        self._remember_preset_action_execution_options_for_session(session_tab_id)
        return execution_options

    def _selected_execution_options_from_controls(
        self,
        controls: ExecutionOptionControls,
        *,
        include_model: bool,
        include_reasoning: bool,
    ) -> AgentExecutionOptions | None:
        return self._selected_execution_options(
            provider_options=controls.agent_provider_options,
            provider_label=controls.agent_provider_var.get(),
            model_options=controls.model_options if include_model else (),
            model_label=controls.model_var.get() if include_model else "",
            reasoning_options=controls.reasoning_options if include_reasoning else (),
            reasoning_label=(controls.reasoning_var.get() if include_reasoning else ""),
        )

    def _selected_execution_options(
        self,
        *,
        provider_options: tuple[SelectOption, ...],
        provider_label: str,
        model_options: tuple[SelectOption, ...],
        model_label: str,
        reasoning_options: tuple[SelectOption, ...],
        reasoning_label: str,
    ) -> AgentExecutionOptions | None:
        provider = self._selected_option_value(provider_options, provider_label)
        if not provider:
            return None
        return AgentExecutionOptions(
            agent_provider=provider,
            model=self._selected_option_value(model_options, model_label),
            reasoning_effort=self._selected_option_value(
                reasoning_options,
                reasoning_label,
            ),
        )

    @staticmethod
    def _selected_option_value(
        options: tuple[SelectOption, ...],
        selected_label: str,
    ) -> str:
        for option in options:
            if option.label == selected_label:
                return option.value
        normalized_label = selected_label.strip()
        if normalized_label in {option.value for option in options}:
            return normalized_label
        return ""

    @staticmethod
    def _option_value_or_default(
        options: tuple[SelectOption, ...],
        value: str,
    ) -> str:
        if value in {option.value for option in options}:
            return value
        return options[0].value if options else ""

    @staticmethod
    def _execution_option_summary_text(
        *,
        provider_label: str,
        model_label: str,
        reasoning_label: str,
    ) -> str:
        labels = (
            provider_label.strip(),
            model_label.strip(),
            reasoning_label.strip(),
        )
        return " / ".join(label for label in labels if label)

    @staticmethod
    def _agent_provider_option_for_value(provider_value: str) -> SelectOption:
        options = build_agent_provider_select_options(provider_value)
        label = find_option_label(options, provider_value) or provider_value
        return SelectOption(label=label, value=provider_value)





