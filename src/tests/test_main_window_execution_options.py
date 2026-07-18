from __future__ import annotations

from tests._main_window_helpers import *


class MainWindowExecutionOptionControlTests(unittest.TestCase):
    def test_locked_session_keeps_registered_options_when_settings_candidates_change(
        self,
    ) -> None:
        execution_options = AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4",
            reasoning_effort="high",
        )
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="pi",
                executable_paths={"pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=execution_options,
                execution_options_locked=True,
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)

        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        self.assertEqual("Codex CLI", widgets.agent_provider_var.get())
        self.assertEqual("gpt-5.4", widgets.model_var.get())
        self.assertEqual("high", widgets.reasoning_var.get())
        self.assertEqual("Codex CLI / gpt-5.4 / high", widgets.execution_summary_var.get())
        self.assertEqual("disabled", widgets.ai_settings_button.cget("state"))
        self.assertEqual([], runtime.updated_execution_options)

    def test_unlocked_session_moves_to_first_configured_provider_after_settings_change(
        self,
    ) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="pi",
                executable_paths={"pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)

        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        self.assertEqual(
            [AgentExecutionOptions(agent_provider="pi")],
            runtime.updated_execution_options,
        )
        self.assertEqual("Pi Coding Agent", widgets.agent_provider_var.get())
        self.assertEqual("Pi Coding Agent / Auto / Auto", widgets.execution_summary_var.get())
        self.assertEqual("normal", widgets.ai_settings_button.cget("state"))

    def test_session_ai_settings_dialog_save_updates_runtime_and_summary(self) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex", "pi": "pi"},
                ui_language="ko",
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=AgentExecutionOptions(agent_provider="codex"),
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)

        with patch("ui.main_window_execution_controls.AgentSettingsDialog") as dialog:
            dialog.return_value.show_modal.return_value = AgentExecutionOptions(
                agent_provider="pi",
                model="pi-pro",
                reasoning_effort="high",
            )
            MainWindow._open_session_ai_settings_dialog(window, "session-1")

        self.assertEqual(
            AgentExecutionOptions(
                agent_provider="pi",
                model="pi-pro",
                reasoning_effort="high",
            ),
            runtime.updated_execution_options[-1],
        )
        self.assertEqual(
            "Pi Coding Agent / pi-pro (저장값) / high",
            widgets.execution_summary_var.get(),
        )

    def test_session_ai_settings_dialog_cancel_keeps_current_options(self) -> None:
        initial_options = AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4",
            reasoning_effort="high",
        )
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex"},
                ui_language="ko",
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=initial_options,
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)
        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        with patch("ui.main_window_execution_controls.AgentSettingsDialog") as dialog:
            dialog.return_value.show_modal.return_value = None
            MainWindow._open_session_ai_settings_dialog(window, "session-1")

        self.assertEqual([], runtime.updated_execution_options)
        self.assertEqual(
            "Codex CLI / gpt-5.4 / high",
            widgets.execution_summary_var.get(),
        )

    def test_pending_preset_registration_keeps_execution_options_disabled(self) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="pi",
                executable_paths={"pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        registration_row_button = _ButtonConfigureStub()
        widgets = _ExecutionOptionSessionWidgetsStub(
            preset_action_ai_settings_button=registration_row_button,
        )
        window = _ExecutionOptionWindowStub(
            runtime,
            widgets,
            pending_registration_session_ids={"session-1"},
        )

        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        self.assertEqual("Codex CLI", widgets.agent_provider_var.get())
        self.assertEqual("disabled", widgets.ai_settings_button.cget("state"))
        self.assertEqual("disabled", registration_row_button.cget("state"))
        self.assertEqual([], runtime.updated_execution_options)

    def test_preset_analysis_submission_failure_restores_execution_option_controls(
        self,
    ) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex"},
                ui_language="ko",
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        registration_row_button = _ButtonConfigureStub()
        widgets = _ExecutionOptionSessionWidgetsStub(
            preset_action_ai_settings_button=registration_row_button,
        )
        window = _PresetSubmissionEventWindowStub(
            runtime,
            widgets,
            pending_registration_session_ids={"session-1"},
        )
        MainWindow._refresh_session_execution_option_controls(window, "session-1")
        self.assertEqual("disabled", widgets.ai_settings_button.cget("state"))
        self.assertEqual("disabled", registration_row_button.cget("state"))

        updates = RuntimeUiUpdateBatch()
        MainWindow._apply_preset_analysis_job_submission_failed(
            window,
            PresetAnalysisJobSubmissionFailedEvent(
                session_tab_id="session-1",
                title="프리셋 작업 오류",
                message="등록 실패",
            ),
            updates,
        )

        self.assertEqual(set(), window._preset_registration_pending_session_ids)
        self.assertEqual(["session-1"], window.preset_registration_refreshes)
        self.assertEqual("normal", widgets.ai_settings_button.cget("state"))
        self.assertEqual("normal", registration_row_button.cget("state"))
        self.assertEqual([("프리셋 작업 오류", "등록 실패")], updates.errors)
        self.assertEqual("등록 실패", updates.status_message)

    def test_preset_analysis_submission_failure_localizes_popup_for_english(self) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex"},
                ui_language="en",
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(agent_provider="codex"),
            ),
        )
        window = _PresetSubmissionEventWindowStub(
            runtime,
            _ExecutionOptionSessionWidgetsStub(),
            pending_registration_session_ids={"session-1"},
        )
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_preset_analysis_job_submission_failed(
            window,
            PresetAnalysisJobSubmissionFailedEvent(
                session_tab_id="session-1",
                title="프리셋 작업 오류",
                message="프리셋 분석 작업을 등록할 수 없습니다.",
            ),
            updates,
        )

        self.assertEqual(
            [("Preset Job Error", "Could not register the preset analysis job.")],
            updates.errors,
        )
        self.assertEqual(
            "Could not register the preset analysis job.",
            updates.status_message,
        )

    def test_preset_analysis_submission_success_remembers_prefix_after_registration(
        self,
    ) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex"},
                ui_language="ko",
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(agent_provider="codex"),
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub(
            preset_language_combobox=_ComboboxConfigureStub(("Python",)),
            preset_instruction_combobox=_ComboboxConfigureStub(("bug",)),
            preset_work_priority_combobox=_ComboboxConfigureStub(("medium",)),
            preset_prompt_prefix_text=_SubmitPromptTextStub("prefix"),
            preset_auto_commit_checkbutton=_ButtonConfigureStub(),
            preset_register_button=_ButtonConfigureStub(),
        )
        window = _PresetSubmissionEventWindowStub(
            runtime,
            widgets,
            pending_registration_session_ids={"session-1"},
        )
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_preset_analysis_job_submitted(
            window,
            PresetAnalysisJobSubmittedEvent(
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                job_id="job-1",
                analysis_prompt_prefix="prefix",
            ),
            updates,
        )

        self.assertEqual(set(), window._preset_registration_pending_session_ids)
        self.assertEqual([("workspace-1", "prefix")], window.remembered_prompt_prefixes)
        self.assertEqual("disabled", widgets.preset_prompt_prefix_text.state)
        self.assertEqual(
            [widgets.progress_log_tab_frame],
            widgets.body_notebook.selected_tabs,
        )
        self.assertEqual({"workspace-1"}, updates.workspace_task_lists)
        self.assertEqual(
            "job-1 프리셋 분석 등록. 후보 작업은 분석 후 생성됩니다.",
            updates.status_message,
        )

    def test_agent_provider_selection_resets_dependent_options(self) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex", "pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)
        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        widgets.agent_provider_var.set("Pi Coding Agent")
        MainWindow._handle_session_agent_provider_selected(window, "session-1")

        self.assertEqual(
            AgentExecutionOptions(agent_provider="pi"),
            runtime.updated_execution_options[-1],
        )
        self.assertEqual("Pi Coding Agent", widgets.agent_provider_var.get())
        self.assertEqual("Auto", widgets.model_var.get())
        self.assertEqual("Auto", widgets.reasoning_var.get())

    def test_preset_registration_row_execution_option_selection_updates_candidate_options(
        self,
    ) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex", "pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(agent_provider="codex"),
            ),
        )
        registration_row_button = _ButtonConfigureStub()
        widgets = _ExecutionOptionSessionWidgetsStub(
            preset_action_ai_settings_button=registration_row_button,
        )
        window = _ExecutionOptionWindowStub(runtime, widgets)
        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        with patch("ui.main_window_execution_controls.AgentSettingsDialog") as dialog:
            dialog.return_value.show_modal.return_value = AgentExecutionOptions(
                agent_provider="pi",
            )
            MainWindow._open_preset_action_ai_settings_dialog(window, "session-1")

        self.assertEqual(
            [],
            runtime.updated_execution_options,
        )
        self.assertEqual("Codex CLI", widgets.agent_provider_var.get())
        self.assertEqual("Pi Coding Agent", widgets.preset_action_agent_provider_var.get())
        assert widgets.preset_action_model_var is not None
        assert widgets.preset_action_reasoning_var is not None
        self.assertEqual("Auto", widgets.preset_action_model_var.get())
        self.assertEqual("Auto", widgets.preset_action_reasoning_var.get())
        self.assertEqual(
            AgentExecutionOptions(agent_provider="pi"),
            widgets.preset_action_execution_controls.execution_options
            if widgets.preset_action_execution_controls is not None
            else None,
        )
        self.assertEqual(
            "Pi Coding Agent / Auto / Auto",
            widgets.preset_action_execution_summary_var.get()
            if widgets.preset_action_execution_summary_var is not None
            else "",
        )

    def test_model_selection_resets_reasoning_to_auto(self) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)
        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        widgets.model_var.set("gpt-5.4-mini")
        MainWindow._handle_session_model_selected(window, "session-1")

        self.assertEqual(
            AgentExecutionOptions(agent_provider="codex", model="gpt-5.4-mini"),
            runtime.updated_execution_options[-1],
        )
        self.assertEqual("gpt-5.4-mini", widgets.model_var.get())
        self.assertEqual("Auto", widgets.reasoning_var.get())

