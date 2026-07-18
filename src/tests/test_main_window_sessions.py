from __future__ import annotations

from tests._main_window_helpers import *

class MainWindowSubmitJobTests(unittest.TestCase):
    def test_submit_job_adds_auto_commit_follow_up_when_checked(self) -> None:
        runtime = _SubmitJobRuntimeStub()
        window = _SubmitJobWindowStub(runtime, prompt="implement feature", auto_commit=True)

        MainWindow._submit_job_for_session(window, "session-1")

        self.assertEqual(
            [("session-1", "implement feature"), ("session-1", AUTO_COMMIT_PROMPT)],
            runtime.submitted_jobs,
        )
        self.assertEqual(
            [window.execution_options, window.execution_options],
            runtime.submitted_execution_options,
        )
        self.assertEqual(("1.0", "end"), window.session_widgets.prompt_text.deleted_ranges[0])
        self.assertEqual(1, window.drain_runtime_events_calls)
        self.assertEqual([("session-1", "job-1")], window.refreshed_session_ids)
        self.assertEqual([("workspace-1", "job-1")], window.refreshed_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            ["job-1, job-2 자동커밋 등록"],
            window.status_messages,
        )

    def test_submit_job_skips_auto_commit_follow_up_when_unchecked(self) -> None:
        runtime = _SubmitJobRuntimeStub()
        window = _SubmitJobWindowStub(runtime, prompt="implement feature", auto_commit=False)

        MainWindow._submit_job_for_session(window, "session-1")

        self.assertEqual([("session-1", "implement feature")], runtime.submitted_jobs)
        self.assertEqual([window.execution_options], runtime.submitted_execution_options)
        self.assertEqual(
            [window.session_widgets.progress_log_tab_frame],
            window.session_widgets.body_notebook.selected_tabs,
        )
        self.assertEqual(["job-1 등록"], window.status_messages)

    def test_submit_immediate_job_requests_runtime_run_now_with_auto_commit_flag(
        self,
    ) -> None:
        runtime = _SubmitJobRuntimeStub()
        window = _SubmitJobWindowStub(runtime, prompt="implement feature", auto_commit=True)

        MainWindow._submit_immediate_job_for_session(window, "session-1")

        self.assertEqual(
            [("session-1", "implement feature", True)],
            runtime.submitted_immediate_jobs,
        )
        self.assertEqual(
            [window.execution_options],
            runtime.submitted_immediate_execution_options,
        )
        self.assertEqual(
            ("1.0", "end"),
            window.session_widgets.prompt_text.deleted_ranges[0],
        )
        self.assertEqual(
            [window.session_widgets.progress_log_tab_frame],
            window.session_widgets.body_notebook.selected_tabs,
        )
        self.assertEqual(["session-1"], window.immediate_button_refreshes)
        self.assertEqual({"session-1"}, window._immediate_run_pending_session_ids)
        self.assertEqual(["바로실행 요청됨"], window.status_messages)

    def test_submit_job_stops_when_no_configured_agent_provider_is_selected(self) -> None:
        runtime = _SubmitJobRuntimeStub()
        window = _SubmitJobWindowStub(
            runtime,
            prompt="implement feature",
            auto_commit=False,
            execution_options=None,
        )

        MainWindow._submit_job_for_session(window, "session-1")

        self.assertEqual([], runtime.submitted_jobs)
        self.assertEqual([], window.status_messages)

class MainWindowPresetLanguagePreferenceTests(unittest.TestCase):
    def test_selected_preset_language_becomes_default_for_same_workspace_path(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
        )

        MainWindow._remember_preset_language_for_session(window, "session-preset-1")

        default_language = MainWindow._default_preset_language_for_workspace(
            window,
            "workspace-2",
            ("Python", "Rust", "Tauri"),
        )
        self.assertEqual("Rust", default_language)

    def test_selected_preset_language_is_scoped_to_workspace(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"D:\OtherRepo",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
        )

        MainWindow._remember_preset_language_for_session(window, "session-preset-1")

        default_language = MainWindow._default_preset_language_for_workspace(
            window,
            "workspace-2",
            ("Python", "Rust"),
        )
        self.assertEqual("Python", default_language)

    def test_unavailable_remembered_preset_language_falls_back_to_first_language(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
        )

        MainWindow._remember_preset_language_for_session(window, "session-preset-1")

        default_language = MainWindow._default_preset_language_for_workspace(
            window,
            "workspace-1",
            ("Python", "Tauri"),
        )
        self.assertEqual("Python", default_language)

class MainWindowPresetInstructionPreferenceTests(unittest.TestCase):
    def test_selected_preset_instruction_becomes_default_for_same_workspace_language(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_instruction="optimize",
        )

        MainWindow._remember_preset_instruction_for_session(window, "session-preset-1")

        default_instruction = MainWindow._default_preset_instruction_for_workspace(
            window,
            "workspace-2",
            "Rust",
            ("bug", "optimize", "refactor"),
        )
        self.assertEqual("optimize", default_instruction)

    def test_selected_preset_instruction_is_scoped_to_language(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Python",
            session_instruction="optimize",
        )

        MainWindow._remember_preset_instruction_for_session(window, "session-preset-1")

        default_instruction = MainWindow._default_preset_instruction_for_workspace(
            window,
            "workspace-1",
            "Rust",
            ("bug", "optimize"),
        )
        self.assertEqual("bug", default_instruction)

    def test_unavailable_remembered_preset_instruction_falls_back_to_first_instruction(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Python",
            session_instruction="optimize",
        )

        MainWindow._remember_preset_instruction_for_session(window, "session-preset-1")

        default_instruction = MainWindow._default_preset_instruction_for_workspace(
            window,
            "workspace-1",
            "Python",
            ("bug", "refactor"),
        )
        self.assertEqual("bug", default_instruction)

class MainWindowPresetWorkPriorityPreferenceTests(unittest.TestCase):
    def test_per_workspace_preset_work_priority_options_include_manual(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_work_priority="manual",
        )

        MainWindow._remember_preset_work_priority_for_session(
            window,
            "session-preset-1",
        )

        self.assertIn("manual", MainWindow._preset_work_priority_options(window))
        self.assertEqual(
            "manual",
            MainWindow._default_preset_work_priority_for_workspace(
                window,
                "workspace-1",
            ),
        )

    def test_shared_queue_preset_work_priority_options_hide_manual(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_work_priority="manual",
            queue_mode_shared=True,
        )

        MainWindow._remember_preset_work_priority_for_session(
            window,
            "session-preset-1",
        )

        self.assertNotIn("manual", MainWindow._preset_work_priority_options(window))
        self.assertEqual(
            "medium",
            MainWindow._default_preset_work_priority_for_workspace(
                window,
                "workspace-1",
            ),
        )

    def test_selected_preset_work_priority_becomes_default_for_same_workspace_path(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_work_priority="high",
        )

        MainWindow._remember_preset_work_priority_for_session(
            window,
            "session-preset-1",
        )

        default_priority = MainWindow._default_preset_work_priority_for_workspace(
            window,
            "workspace-2",
        )
        self.assertEqual("high", default_priority)

    def test_selected_preset_work_priority_is_scoped_to_workspace(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"D:\OtherRepo",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_work_priority="low",
        )

        MainWindow._remember_preset_work_priority_for_session(
            window,
            "session-preset-1",
        )

        default_priority = MainWindow._default_preset_work_priority_for_workspace(
            window,
            "workspace-2",
        )
        self.assertEqual("medium", default_priority)

    def test_unavailable_remembered_preset_work_priority_falls_back_to_default(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_work_priority="urgent",
        )

        MainWindow._remember_preset_work_priority_for_session(
            window,
            "session-preset-1",
        )

        default_priority = MainWindow._default_preset_work_priority_for_workspace(
            window,
            "workspace-1",
        )
        self.assertEqual("medium", default_priority)

class MainWindowPresetPromptPrefixPreferenceTests(unittest.TestCase):
    def test_submitted_preset_prompt_prefix_becomes_default_for_same_workspace_path(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_prompt_prefix="prefix line",
        )

        MainWindow._remember_preset_prompt_prefix_for_session(
            window,
            "session-preset-1",
        )

        default_prefix = MainWindow._default_preset_prompt_prefix_for_workspace(
            window,
            "workspace-2",
        )
        self.assertEqual("prefix line", default_prefix)

    def test_empty_preset_prompt_prefix_clears_remembered_workspace_default(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_prompt_prefix="",
        )
        window._workspace_preset_prompt_prefixes[
            MainWindow._workspace_preset_language_key(window, "workspace-1")
        ] = "old prefix"

        MainWindow._remember_preset_prompt_prefix_for_session(
            window,
            "session-preset-1",
        )

        default_prefix = MainWindow._default_preset_prompt_prefix_for_workspace(
            window,
            "workspace-1",
        )
        self.assertEqual("", default_prefix)

class MainWindowPresetActionExecutionOptionPreferenceTests(unittest.TestCase):
    def test_selected_preset_action_execution_options_become_default_for_same_workspace_path(
        self,
    ) -> None:
        selected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-pro",
            reasoning_effort="high",
        )
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_preset_action_execution_options=selected_execution_options,
        )

        MainWindow._remember_preset_action_execution_options_for_session(
            window,
            "session-preset-1",
        )

        default_options = (
            MainWindow._default_preset_action_execution_options_for_workspace(
                window,
                "workspace-2",
                fallback=AgentExecutionOptions(agent_provider="codex"),
            )
        )
        self.assertEqual(selected_execution_options, default_options)

    def test_selected_preset_action_execution_options_are_scoped_to_workspace(
        self,
    ) -> None:
        selected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-pro",
            reasoning_effort="high",
        )
        fallback_execution_options = AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4",
        )
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"D:\OtherRepo",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_preset_action_execution_options=selected_execution_options,
        )

        MainWindow._remember_preset_action_execution_options_for_session(
            window,
            "session-preset-1",
        )

        default_options = (
            MainWindow._default_preset_action_execution_options_for_workspace(
                window,
                "workspace-2",
                fallback=fallback_execution_options,
            )
        )
        self.assertEqual(fallback_execution_options, default_options)

class MainWindowPresetSubmitTests(unittest.TestCase):
    def test_preset_submit_uses_selected_inputs_without_prompt_editor_text(self) -> None:
        runtime = _SubmitPresetRuntimeStub()
        window = _SubmitPresetWindowStub(
            runtime,
            auto_commit=True,
            analysis_prompt_prefix="prefix text",
        )

        MainWindow._submit_preset_job_for_session(window, "session-preset-1")

        self.assertEqual(
            [("session-preset-1", "Python", "bug", "medium", True)],
            runtime.submitted_preset_jobs,
        )
        self.assertEqual(["prefix text"], runtime.submitted_analysis_prompt_prefixes)
        self.assertEqual(["prefix text"], window.remembered_prompt_prefixes)
        self.assertEqual(
            [AgentExecutionOptions(agent_provider="codex", model="gpt-5.4")],
            runtime.submitted_execution_options,
        )
        self.assertEqual(
            [
                AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4-mini",
                    reasoning_effort="low",
                )
            ],
            runtime.submitted_candidate_execution_options,
        )
        self.assertEqual(1, window.drain_runtime_events_calls)
        self.assertEqual([("session-preset-1", "job-1")], window.refreshed_session_ids)
        self.assertEqual(
            [window.session_widgets.progress_log_tab_frame],
            window.session_widgets.body_notebook.selected_tabs,
        )
        self.assertEqual([("workspace-1", "job-1")], window.refreshed_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            [
                "job-1 프리셋 분석 등록. 후보 작업은 분석 후 생성됩니다."
            ],
            window.status_messages,
        )

    def test_preset_submit_disables_inputs_and_buttons_after_success(self) -> None:
        runtime = _SubmitPresetRuntimeStub()
        language_combobox = _ComboboxConfigureStub(("Python",))
        instruction_combobox = _ComboboxConfigureStub(("bug",))
        work_priority_combobox = _ComboboxConfigureStub(("high", "medium", "low"))
        auto_commit_checkbutton = _ButtonConfigureStub()
        register_button = _ButtonConfigureStub()
        window = _SubmitPresetWindowStub(
            runtime,
            auto_commit=True,
            language_combobox=language_combobox,
            instruction_combobox=instruction_combobox,
            work_priority_combobox=work_priority_combobox,
            auto_commit_checkbutton=auto_commit_checkbutton,
            register_button=register_button,
        )

        MainWindow._submit_preset_job_for_session(window, "session-preset-1")

        self.assertEqual("disabled", language_combobox.state)
        self.assertEqual("disabled", instruction_combobox.state)
        self.assertEqual("disabled", work_priority_combobox.state)
        self.assertEqual("disabled", window.session_widgets.preset_prompt_prefix_text.state)
        self.assertEqual("disabled", auto_commit_checkbutton.state)
        self.assertEqual("disabled", register_button.state)

    def test_preset_submit_rejects_manual_priority_in_shared_queue(self) -> None:
        runtime = _SubmitPresetRuntimeStub(
            settings=AppSettings(queue_mode="shared", ui_language="ko")
        )
        work_priority_combobox = _ComboboxConfigureStub(("high", "medium", "low", "manual"))
        window = _SubmitPresetWindowStub(
            runtime,
            auto_commit=True,
            work_priority="manual",
            work_priority_combobox=work_priority_combobox,
        )

        with patch("ui.main_window.messagebox.showerror") as showerror:
            MainWindow._submit_preset_job_for_session(window, "session-preset-1")

        self.assertEqual([], runtime.submitted_preset_jobs)
        self.assertEqual("medium", window.session_widgets.preset_work_priority_var.get())
        self.assertNotIn("manual", work_priority_combobox.cget("values"))
        showerror.assert_called_once_with(
            "입력 오류",
            "manual 우선순위는 워크스페이스 개별큐에서만 사용할 수 있습니다.",
            parent=window,
        )

    def test_preset_submit_rejects_missing_required_values(self) -> None:
        runtime = _SubmitPresetRuntimeStub()
        window = _SubmitPresetWindowStub(
            runtime,
            auto_commit=True,
            language="",
            instruction="bug",
            work_priority="",
        )

        with patch("ui.main_window.messagebox.showerror") as showerror:
            MainWindow._submit_preset_job_for_session(window, "session-preset-1")

        self.assertEqual([], runtime.submitted_preset_jobs)
        self.assertEqual(0, window.drain_runtime_events_calls)
        self.assertEqual([], window.refreshed_session_ids)
        self.assertEqual([], window.refreshed_workspace_ids)
        showerror.assert_called_once_with(
            "입력 오류",
            "언어, 지시문, 우선순위를 선택하세요.",
            parent=window,
        )

    def test_preset_submit_reports_prompt_pair_error_without_registering_ui_updates(self) -> None:
        runtime = _SubmitPresetRuntimeStub(
            submit_error=ValueError("프리셋 prompt 파일 쌍을 찾거나 읽지 못했습니다.")
        )
        window = _SubmitPresetWindowStub(
            runtime,
            auto_commit=False,
            analysis_prompt_prefix="failed prefix",
        )

        with patch("ui.main_window.messagebox.showerror") as showerror:
            MainWindow._submit_preset_job_for_session(window, "session-preset-1")

        self.assertEqual(
            [("session-preset-1", "Python", "bug", "medium", False)],
            runtime.submitted_preset_jobs,
        )
        self.assertEqual([], window.remembered_prompt_prefixes)
        self.assertEqual(0, window.drain_runtime_events_calls)
        self.assertEqual([], window.refreshed_session_ids)
        self.assertEqual([], window.refreshed_workspace_ids)
        showerror.assert_called_once_with(
            "입력 오류",
            "프리셋 prompt 파일 쌍을 찾거나 읽지 못했습니다.",
            parent=window,
        )

    def test_preset_candidate_tab_insert_index_places_candidate_after_parent(self) -> None:
        window = _SessionOrderWindowStub(
            ordered_session_ids=("session-1", "preset-parent", "candidate-1", "session-2"),
            existing_session_ids=("session-1", "preset-parent", "session-2"),
        )

        insert_index = MainWindow._session_tab_insert_index(
            window,
            "workspace-1",
            "candidate-1",
        )

        self.assertEqual(2, insert_index)

    def test_close_session_selects_right_session_tab_after_removal(self) -> None:
        window = _SessionCloseSelectionWindowStub(
            ("session-1", "session-2", "session-3")
        )

        MainWindow._remove_session_view(window, "session-2")

        self.assertEqual(["frame-session-2"], window.session_notebook.forgotten_tab_ids)
        self.assertEqual(["frame-session-3"], window.session_notebook.selected_tab_ids)
        self.assertNotIn(
            "session-2",
            window._workspace_views["workspace-1"].session_views,
        )
        self.assertNotIn("frame-session-2", window._session_frame_map)
        self.assertEqual(1, window.frame_for_session("session-2").destroy_calls)

    def test_close_rightmost_session_selects_left_session_tab_after_removal(self) -> None:
        window = _SessionCloseSelectionWindowStub(
            ("session-1", "session-2", "session-3")
        )

        MainWindow._remove_session_view(window, "session-3")

        self.assertEqual(["frame-session-3"], window.session_notebook.forgotten_tab_ids)
        self.assertEqual(["frame-session-2"], window.session_notebook.selected_tab_ids)

    def test_preset_candidate_registration_event_reports_registered_job_count(self) -> None:
        window = _PresetCandidateRegistrationWindowStub()
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_runtime_event(
            window,
            PresetCandidateJobsRegisteredEvent(
                workspace_tab_id="workspace-1",
                parent_session_tab_id="preset-parent",
                candidate_session_tab_ids=("candidate-1", "candidate-2"),
                registered_job_ids=("job-1", "job-2", "job-3", "job-4"),
                auto_commit_enabled=True,
            ),
            updates,
        )
        MainWindow._apply_runtime_ui_updates(window, updates)

        self.assertEqual(["candidate-1", "candidate-2"], window.ensured_session_ids)
        self.assertEqual(
            {"candidate-1": True, "candidate-2": True},
            {
                session_tab_id: widgets.auto_commit_var.get()
                for session_tab_id, widgets in window.session_widgets.items()
            },
        )
        self.assertEqual(["candidate-1", "candidate-2"], window.refreshed_session_ids)
        self.assertEqual(["workspace-1"], window.refreshed_workspace_ids)
        self.assertEqual(["workspace-1"], window.synced_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            ["후보 세션 2개, 작업 4건 등록"],
            window.status_messages,
        )





