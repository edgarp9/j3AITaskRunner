from __future__ import annotations

from tests._main_window_helpers_core import *


@dataclass(slots=True)
class _SubmitPresetSessionWidgetsStub:
    preset_language_var: _StringVarStub
    preset_instruction_var: _StringVarStub
    preset_work_priority_var: _StringVarStub
    auto_commit_var: _BoolVarStub
    body_notebook: _BodyNotebookSelectStub = field(
        default_factory=_BodyNotebookSelectStub
    )
    progress_log_tab_frame: object = field(default_factory=object)
    preset_prompt_prefix_text: _SubmitPromptTextStub | None = None
    preset_language_combobox: _ComboboxConfigureStub | None = None
    preset_instruction_combobox: _ComboboxConfigureStub | None = None
    preset_work_priority_combobox: _ComboboxConfigureStub | None = None
    preset_auto_commit_checkbutton: _ButtonConfigureStub | None = None
    preset_register_button: _ButtonConfigureStub | None = None
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
        default_factory=lambda: AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4-mini",
            reasoning_effort="low",
        )
    )

class _SubmitPresetRuntimeStub:
    def __init__(
        self,
        *,
        submit_error: Exception | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        self._submit_error = submit_error
        self.settings = settings or AppSettings(ui_language="ko")
        self.submitted_preset_jobs: list[tuple[str, str, str, str, bool]] = []
        self.submitted_analysis_prompt_prefixes: list[str] = []
        self.submitted_execution_options: list[AgentExecutionOptions | None] = []
        self.submitted_candidate_execution_options: list[
            AgentExecutionOptions | None
        ] = []

    def submit_preset_analysis_job(
        self,
        session_tab_id: str,
        *,
        language: str,
        instruction: str,
        work_priority: str,
        analysis_prompt_prefix: str = "",
        auto_commit_enabled: bool = False,
        execution_options: AgentExecutionOptions | None = None,
        candidate_execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        self.submitted_preset_jobs.append(
            (session_tab_id, language, instruction, work_priority, auto_commit_enabled)
        )
        self.submitted_analysis_prompt_prefixes.append(analysis_prompt_prefix)
        self.submitted_execution_options.append(execution_options)
        self.submitted_candidate_execution_options.append(candidate_execution_options)
        if self._submit_error is not None:
            raise self._submit_error
        return Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id=session_tab_id,
            prompt="preset analysis",
            status=JobStatus.QUEUED,
        )

    def list_jobs(self, *, session_tab_id: str | None = None) -> tuple[Job, ...]:
        del session_tab_id
        return ()

class _SubmitPresetWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _SubmitPresetRuntimeStub,
        *,
        auto_commit: bool,
        language: str = "Python",
        instruction: str = "bug",
        work_priority: str = "medium",
        analysis_prompt_prefix: str = "",
        language_combobox: _ComboboxConfigureStub | None = None,
        instruction_combobox: _ComboboxConfigureStub | None = None,
        work_priority_combobox: _ComboboxConfigureStub | None = None,
        auto_commit_checkbutton: _ButtonConfigureStub | None = None,
        register_button: _ButtonConfigureStub | None = None,
    ) -> None:
        self._runtime = runtime
        self.session_widgets = _SubmitPresetSessionWidgetsStub(
            preset_language_var=_StringVarStub(language),
            preset_instruction_var=_StringVarStub(instruction),
            preset_work_priority_var=_StringVarStub(work_priority),
            auto_commit_var=_BoolVarStub(auto_commit),
            preset_prompt_prefix_text=_SubmitPromptTextStub(analysis_prompt_prefix),
            preset_language_combobox=language_combobox,
            preset_instruction_combobox=instruction_combobox,
            preset_work_priority_combobox=work_priority_combobox,
            preset_auto_commit_checkbutton=auto_commit_checkbutton,
            preset_register_button=register_button,
        )
        self.drain_runtime_events_calls = 0
        self.refreshed_session_ids: list[tuple[str, str | None]] = []
        self.refreshed_workspace_ids: list[tuple[str, str | None]] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []
        self.execution_option_controls_enabled: list[bool] = []
        self.remembered_prompt_prefixes: list[str] = []
        self.remembered_work_priorities: list[str] = []
        self._preset_registration_pending_session_ids: set[str] = set()

    def _get_session_widgets(self, session_tab_id: str) -> _SubmitPresetSessionWidgetsStub:
        del session_tab_id
        return self.session_widgets

    def _execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        del session_tab_id
        return AgentExecutionOptions(agent_provider="codex", model="gpt-5.4")

    def _preset_action_execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        del session_tab_id
        return AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4-mini",
            reasoning_effort="low",
        )

    def _preset_prompt_prefix_for_session(self, session_tab_id: str) -> str:
        return MainWindow._preset_prompt_prefix_for_session(self, session_tab_id)

    def _remember_preset_prompt_prefix_for_session(self, session_tab_id: str) -> None:
        self.remembered_prompt_prefixes.append(
            self._preset_prompt_prefix_for_session(session_tab_id)
        )

    def _remember_preset_work_priority_for_session(self, session_tab_id: str) -> None:
        del session_tab_id
        work_priority_var = self.session_widgets.preset_work_priority_var
        if work_priority_var is not None:
            self.remembered_work_priorities.append(work_priority_var.get().strip())

    def _remember_preset_prompt_prefix_for_workspace(
        self,
        workspace_tab_id: str,
        prompt_prefix: str,
    ) -> None:
        del workspace_tab_id
        self.remembered_prompt_prefixes.append(prompt_prefix)

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _set_preset_registration_controls_enabled(
        self,
        session_widgets: _SubmitPresetSessionWidgetsStub,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_registration_controls_enabled(
            self,
            session_widgets,
            enabled=enabled,
        )

    def _set_session_execution_option_controls_enabled(
        self,
        session_widgets: _SubmitPresetSessionWidgetsStub,
        *,
        enabled: bool,
    ) -> None:
        del session_widgets
        self.execution_option_controls_enabled.append(enabled)

    def _set_preset_combobox_enabled(
        self,
        combobox: _ComboboxConfigureStub | None,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_combobox_enabled(combobox, enabled=enabled)

    def _has_session_view(self, session_tab_id: str) -> bool:
        del session_tab_id
        return True

    def _queue_mode_is_shared(self) -> bool:
        return MainWindow._queue_mode_is_shared(self)

    def _preset_registration_is_locked(self, session_tab_id: str) -> bool:
        return MainWindow._preset_registration_is_locked(self, session_tab_id)

    def _preset_session_has_registered_job(self, session_tab_id: str) -> bool:
        return MainWindow._preset_session_has_registered_job(self, session_tab_id)

    def _preset_work_priority_options(self) -> tuple[str, ...]:
        return MainWindow._preset_work_priority_options(self)

    def _refresh_preset_work_priority_options(self, session_tab_id: str) -> None:
        MainWindow._refresh_preset_work_priority_options(self, session_tab_id)

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


__all__ = [name for name in globals() if not name.startswith("__")]

