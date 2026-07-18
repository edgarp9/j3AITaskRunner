from __future__ import annotations

from tests._main_window_helpers_core import *

from tests._main_window_helpers_execution_preset import *

@dataclass(slots=True)
class _ExecutionOptionSessionWidgetsStub:
    body_notebook: _BodyNotebookSelectStub = field(
        default_factory=_BodyNotebookSelectStub
    )
    progress_log_tab_frame: object = field(default_factory=object)
    agent_provider_var: _StringVarStub = field(default_factory=_StringVarStub)
    model_var: _StringVarStub = field(default_factory=_StringVarStub)
    reasoning_var: _StringVarStub = field(default_factory=_StringVarStub)
    ai_settings_button: _ButtonConfigureStub = field(
        default_factory=_ButtonConfigureStub
    )
    execution_summary_var: _StringVarStub = field(default_factory=_StringVarStub)
    execution_summary_label: _ButtonConfigureStub = field(
        default_factory=_ButtonConfigureStub
    )
    preset_action_ai_settings_button: _ButtonConfigureStub | None = None
    preset_action_execution_summary_var: _StringVarStub | None = field(
        default_factory=_StringVarStub
    )
    preset_action_execution_summary_label: _ButtonConfigureStub | None = field(
        default_factory=_ButtonConfigureStub
    )
    preset_language_combobox: _ComboboxConfigureStub | None = None
    preset_instruction_combobox: _ComboboxConfigureStub | None = None
    preset_work_priority_combobox: _ComboboxConfigureStub | None = None
    preset_prompt_prefix_text: _SubmitPromptTextStub | None = None
    preset_auto_commit_checkbutton: _ButtonConfigureStub | None = None
    preset_register_button: _ButtonConfigureStub | None = None
    agent_provider_options: tuple[object, ...] = ()
    model_options: tuple[object, ...] = ()
    reasoning_options: tuple[object, ...] = ()
    preset_action_agent_provider_var: _StringVarStub | None = field(
        default_factory=_StringVarStub
    )
    preset_action_model_var: _StringVarStub | None = field(default_factory=_StringVarStub)
    preset_action_reasoning_var: _StringVarStub | None = field(
        default_factory=_StringVarStub
    )
    preset_action_agent_provider_options: tuple[object, ...] = ()
    preset_action_model_options: tuple[object, ...] = ()
    preset_action_reasoning_options: tuple[object, ...] = ()
    preset_action_execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )
    execution_controls: ExecutionOptionControls = field(init=False)
    preset_action_execution_controls: ExecutionOptionControls | None = field(
        init=False,
    )

    def __post_init__(self) -> None:
        self.execution_controls = ExecutionOptionControls(
            agent_provider_var=self.agent_provider_var,
            model_var=self.model_var,
            reasoning_var=self.reasoning_var,
            ai_settings_button=self.ai_settings_button,
            summary_var=self.execution_summary_var,
            summary_label=self.execution_summary_label,
        )
        if (
            self.preset_action_ai_settings_button is None
            or self.preset_action_execution_summary_var is None
            or self.preset_action_execution_summary_label is None
            or self.preset_action_agent_provider_var is None
            or self.preset_action_model_var is None
            or self.preset_action_reasoning_var is None
        ):
            self.preset_action_execution_controls = None
            return
        self.preset_action_execution_controls = ExecutionOptionControls(
            agent_provider_var=self.preset_action_agent_provider_var,
            model_var=self.preset_action_model_var,
            reasoning_var=self.preset_action_reasoning_var,
            ai_settings_button=self.preset_action_ai_settings_button,
            summary_var=self.preset_action_execution_summary_var,
            summary_label=self.preset_action_execution_summary_label,
            execution_options=self.preset_action_execution_options,
        )

class _ExecutionOptionRuntimeStub:
    def __init__(self, *, settings: AppSettings, session_tab: SessionTab) -> None:
        self.settings = settings
        self.session_tab = session_tab
        self.updated_execution_options: list[AgentExecutionOptions] = []

    def get_session_tab(self, session_tab_id: str) -> SessionTab:
        if session_tab_id != self.session_tab.session_tab_id:
            raise KeyError(session_tab_id)
        return self.session_tab

    def set_session_execution_options(
        self,
        session_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> SessionTab:
        if session_tab_id != self.session_tab.session_tab_id:
            raise KeyError(session_tab_id)
        self.updated_execution_options.append(execution_options)
        if not self.session_tab.execution_options_locked:
            self.session_tab = replace(
                self.session_tab,
                execution_options=execution_options,
            )
        return self.session_tab

class _ExecutionOptionWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _ExecutionOptionRuntimeStub,
        widgets: _ExecutionOptionSessionWidgetsStub,
        *,
        pending_registration_session_ids: set[str] | None = None,
    ) -> None:
        self._runtime = runtime
        self._widgets = widgets
        self._preset_registration_pending_session_ids = (
            pending_registration_session_ids or set()
        )

    def _has_session_view(self, session_tab_id: str) -> bool:
        del session_tab_id
        return True

    def _get_session_widgets(
        self,
        session_tab_id: str,
    ) -> _ExecutionOptionSessionWidgetsStub:
        del session_tab_id
        return self._widgets

    def _refresh_session_execution_option_controls(self, session_tab_id: str) -> None:
        MainWindow._refresh_session_execution_option_controls(self, session_tab_id)

    def _refresh_preset_action_execution_option_controls(
        self,
        session_tab_id: str,
    ) -> None:
        MainWindow._refresh_preset_action_execution_option_controls(
            self,
            session_tab_id,
        )

    def _remember_preset_action_execution_options_for_session(
        self,
        session_tab_id: str,
    ) -> None:
        MainWindow._remember_preset_action_execution_options_for_session(
            self,
            session_tab_id,
        )

    def _resolve_execution_option_control_values(
        self,
        execution_options: AgentExecutionOptions,
        *,
        locked: bool,
    ):
        return MainWindow._resolve_execution_option_control_values(
            self,
            execution_options,
            locked=locked,
        )

    def _apply_execution_option_control_values(self, **kwargs: object) -> None:
        MainWindow._apply_execution_option_control_values(**kwargs)

    def _set_execution_option_combobox_states(self, **kwargs: object) -> None:
        MainWindow._set_execution_option_combobox_states(**kwargs)

    def _selected_execution_options(self, **kwargs: object):
        return MainWindow._selected_execution_options(self, **kwargs)

    def _selected_execution_options_from_controls(
        self,
        controls: ExecutionOptionControls,
        *,
        include_model: bool,
        include_reasoning: bool,
    ):
        return MainWindow._selected_execution_options_from_controls(
            self,
            controls,
            include_model=include_model,
            include_reasoning=include_reasoning,
        )

    def _selected_option_value(
        self,
        options: tuple[object, ...],
        selected_label: str,
    ) -> str:
        return MainWindow._selected_option_value(options, selected_label)

    def _open_ai_settings_dialog(self, controls: ExecutionOptionControls):
        return MainWindow._open_ai_settings_dialog(self, controls)

    def _option_value_or_default(
        self,
        options: tuple[object, ...],
        value: str,
    ) -> str:
        return MainWindow._option_value_or_default(options, value)

    def _set_preset_action_execution_option_controls_enabled(
        self,
        session_widgets: _ExecutionOptionSessionWidgetsStub,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_action_execution_option_controls_enabled(
            self,
            session_widgets,
            enabled=enabled,
        )

    def _agent_provider_option_for_value(self, provider_value: str):
        return MainWindow._agent_provider_option_for_value(provider_value)

    def _select_session_progress_log_tab(self, session_tab_id: str) -> None:
        MainWindow._select_session_progress_log_tab(self, session_tab_id)

class _PresetSubmissionEventWindowStub(_ExecutionOptionWindowStub):
    def __init__(
        self,
        runtime: _ExecutionOptionRuntimeStub,
        widgets: _ExecutionOptionSessionWidgetsStub,
        *,
        pending_registration_session_ids: set[str] | None = None,
    ) -> None:
        super().__init__(
            runtime,
            widgets,
            pending_registration_session_ids=pending_registration_session_ids,
        )
        self.preset_registration_refreshes: list[str] = []
        self.remembered_prompt_prefixes: list[tuple[str, str]] = []

    def _refresh_preset_registration_controls(self, session_tab_id: str) -> None:
        self.preset_registration_refreshes.append(session_tab_id)

    def _set_preset_registration_controls_enabled(
        self,
        session_widgets: _ExecutionOptionSessionWidgetsStub,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_registration_controls_enabled(
            self,
            session_widgets,
            enabled=enabled,
        )

    def _set_preset_combobox_enabled(
        self,
        combobox: _ComboboxConfigureStub | None,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_combobox_enabled(combobox, enabled=enabled)

    def _remember_preset_prompt_prefix_for_workspace(
        self,
        workspace_tab_id: str,
        prompt_prefix: str,
    ) -> None:
        self.remembered_prompt_prefixes.append((workspace_tab_id, prompt_prefix))

@dataclass(slots=True)
class _SubmitSessionWidgetsStub:
    prompt_text: _SubmitPromptTextStub
    auto_commit_var: _BoolVarStub
    body_notebook: _BodyNotebookSelectStub = field(
        default_factory=_BodyNotebookSelectStub
    )
    progress_log_tab_frame: object = field(default_factory=object)
    immediate_run_button: _ButtonConfigureStub = field(
        default_factory=_ButtonConfigureStub
    )

class _SubmitJobRuntimeStub:
    def __init__(self) -> None:
        self.submitted_jobs: list[tuple[str, str]] = []
        self.submitted_execution_options: list[AgentExecutionOptions | None] = []
        self.submitted_immediate_jobs: list[tuple[str, str, bool]] = []
        self.submitted_immediate_execution_options: list[
            AgentExecutionOptions | None
        ] = []

    def submit_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        self.submitted_jobs.append((session_tab_id, prompt))
        self.submitted_execution_options.append(execution_options)
        job_number = len(self.submitted_jobs)
        return Job(
            job_id=f"job-{job_number}",
            workspace_tab_id="workspace-1",
            session_tab_id=session_tab_id,
            prompt=prompt,
            status=JobStatus.QUEUED,
        )

    def submit_immediate_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        auto_commit_enabled: bool,
        execution_options: AgentExecutionOptions | None = None,
    ) -> None:
        self.submitted_immediate_jobs.append(
            (session_tab_id, prompt, auto_commit_enabled)
        )
        self.submitted_immediate_execution_options.append(execution_options)

class _SubmitJobWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _SubmitJobRuntimeStub,
        *,
        prompt: str,
        auto_commit: bool,
        execution_options: AgentExecutionOptions | None = AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4",
        ),
    ) -> None:
        self._runtime = runtime
        self.execution_options = execution_options
        self.session_widgets = _SubmitSessionWidgetsStub(
            prompt_text=_SubmitPromptTextStub(prompt),
            auto_commit_var=_BoolVarStub(auto_commit),
        )
        self.drain_runtime_events_calls = 0
        self.refreshed_session_ids: list[tuple[str, str | None]] = []
        self.refreshed_workspace_ids: list[tuple[str, str | None]] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []
        self.execution_option_refreshes: list[str] = []
        self.immediate_button_refreshes: list[str] = []
        self._immediate_run_pending_session_ids: set[str] = set()

    def _get_session_widgets(self, session_tab_id: str) -> _SubmitSessionWidgetsStub:
        del session_tab_id
        return self.session_widgets

    def _execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        del session_tab_id
        return self.execution_options

    def _refresh_session_execution_option_controls(self, session_tab_id: str) -> None:
        self.execution_option_refreshes.append(session_tab_id)

    def _refresh_immediate_run_button(self, session_tab_id: str) -> None:
        self.immediate_button_refreshes.append(session_tab_id)

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _refresh_session_view(
        self,
        session_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_session_ids.append((session_tab_id, preferred_job_id))

    def _select_session_progress_log_tab(self, session_tab_id: str) -> None:
        MainWindow._select_session_progress_log_tab(self, session_tab_id)

    def _refresh_workspace_task_list(
        self,
        workspace_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_workspace_ids.append((workspace_tab_id, preferred_job_id))

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

@dataclass(slots=True)
class _PresetLanguageSessionWidgetsStub:
    preset_language_var: _StringVarStub | None
    preset_instruction_var: _StringVarStub | None
    preset_work_priority_var: _StringVarStub | None = None
    preset_prompt_prefix_text: _SubmitPromptTextStub | None = None
    preset_action_execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )

@dataclass(slots=True)
class _PresetLanguageSessionTabStub:
    workspace_tab_id: str

@dataclass(slots=True)
class _PresetLanguageWorkspaceTabStub:
    workspace_path: str

class _PresetLanguageRuntimeStub:
    def __init__(
        self,
        *,
        workspace_paths: dict[str, str],
        session_workspace_ids: dict[str, str],
    ) -> None:
        self._workspace_paths = workspace_paths
        self._session_workspace_ids = session_workspace_ids

    def get_session_tab(self, session_tab_id: str) -> _PresetLanguageSessionTabStub:
        return _PresetLanguageSessionTabStub(
            workspace_tab_id=self._session_workspace_ids[session_tab_id]
        )

    def get_workspace_tab(self, workspace_tab_id: str) -> _PresetLanguageWorkspaceTabStub:
        return _PresetLanguageWorkspaceTabStub(
            workspace_path=self._workspace_paths[workspace_tab_id]
        )

class _PresetLanguagePreferenceWindowStub:
    def __init__(
        self,
        *,
        workspace_paths: dict[str, str],
        session_workspace_ids: dict[str, str],
        session_language: str,
        session_instruction: str = "bug",
        session_work_priority: str = "medium",
        session_prompt_prefix: str = "",
        session_preset_action_execution_options: AgentExecutionOptions | None = None,
        queue_mode_shared: bool = False,
    ) -> None:
        self._runtime = _PresetLanguageRuntimeStub(
            workspace_paths=workspace_paths,
            session_workspace_ids=session_workspace_ids,
        )
        self._workspace_preset_languages: dict[str, str] = {}
        self._workspace_preset_instructions: dict[tuple[str, str], str] = {}
        self._workspace_preset_work_priorities: dict[str, str] = {}
        self._workspace_preset_prompt_prefixes: dict[str, str] = {}
        self._workspace_preset_action_execution_options: dict[
            str,
            AgentExecutionOptions,
        ] = {}
        self._queue_mode_shared = queue_mode_shared
        self._session_widgets = _PresetLanguageSessionWidgetsStub(
            preset_language_var=_StringVarStub(session_language),
            preset_instruction_var=_StringVarStub(session_instruction),
            preset_work_priority_var=_StringVarStub(session_work_priority),
            preset_prompt_prefix_text=_SubmitPromptTextStub(session_prompt_prefix),
            preset_action_execution_options=(
                session_preset_action_execution_options or AgentExecutionOptions()
            ),
        )

    def _get_session_widgets(
        self,
        session_tab_id: str,
    ) -> _PresetLanguageSessionWidgetsStub:
        del session_tab_id
        return self._session_widgets

    def _queue_mode_is_shared(self) -> bool:
        return self._queue_mode_shared

    def _preset_work_priority_options(self) -> tuple[str, ...]:
        return MainWindow._preset_work_priority_options(self)

    def _workspace_preset_language_key(self, workspace_tab_id: str) -> str:
        return MainWindow._workspace_preset_language_key(self, workspace_tab_id)

    def _workspace_preset_instruction_key(
        self,
        workspace_tab_id: str,
        language: str,
    ) -> tuple[str, str]:
        return MainWindow._workspace_preset_instruction_key(
            self,
            workspace_tab_id,
            language,
        )

    def _preset_prompt_prefix_for_session(self, session_tab_id: str) -> str:
        return MainWindow._preset_prompt_prefix_for_session(self, session_tab_id)

    def _remember_preset_prompt_prefix_for_workspace(
        self,
        workspace_tab_id: str,
        prompt_prefix: str,
    ) -> None:
        MainWindow._remember_preset_prompt_prefix_for_workspace(
            self,
            workspace_tab_id,
            prompt_prefix,
        )




__all__ = [name for name in globals() if not name.startswith("__")]
