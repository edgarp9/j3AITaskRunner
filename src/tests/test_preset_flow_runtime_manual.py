from __future__ import annotations

from tests._preset_flow_helpers import *


class PresetRuntimeManualFlowTests(unittest.TestCase):
    def test_manual_analysis_completion_waits_for_candidate_selection(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="manual",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text(
                [
                    _candidate_payload("1", priority="high"),
                    _candidate_payload("2", priority="medium"),
                    _candidate_payload("3", priority="low"),
                ]
            ),
        )

        runtime._handle_preset_execution_result(event)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual({}, runtime._preset_work_generation_job_contexts)
        self.assertTrue(runtime._has_pending_preset_followup())
        self.assertEqual(("preset-parent",), tuple(runtime._preset_manual_selection_contexts))
        self.assertEqual(1, len(runtime._event_queue.events))
        selection_event = runtime._event_queue.events[0]
        self.assertIsInstance(selection_event, PresetManualCandidateSelectionRequiredEvent)
        self.assertEqual(
            ("1", "2", "3"),
            tuple(candidate.id for candidate in selection_event.candidates),
        )

    def test_manual_continue_registers_turn2_with_selected_candidates_in_analysis_order(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="manual",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text(
                [
                    _candidate_payload("1", priority="high"),
                    _candidate_payload("2", priority="medium"),
                    _candidate_payload("3", priority="low"),
                ]
            ),
        )
        runtime._handle_preset_execution_result(event)
        runtime._event_queue.events.clear()

        runtime.continue_preset_manual_selection_in_background(
            "preset-parent",
            ("3", "1", "missing", "3"),
        )

        expected_candidates = [
            _candidate("1", priority="high"),
            _candidate("3", priority="low"),
        ]
        self.assertEqual(
            [("preset-parent", "work " + build_candidates_payload(expected_candidates))],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual(("job-1",), runtime._controller.prioritized_job_ids)
        self.assertEqual(["workspace-1"], runtime._controller.started_queue_ids)
        self.assertFalse(runtime._has_pending_preset_followup())
        self.assertEqual({}, runtime._preset_manual_selection_contexts)
        self.assertEqual(["job-1"], list(runtime._preset_work_generation_job_contexts))
        self.assertEqual(
            ("1", "3"),
            tuple(
                candidate.id
                for candidate in runtime._preset_work_generation_job_contexts["job-1"].candidates
            ),
        )
        continued_event = runtime._event_queue.events[0]
        self.assertIsInstance(continued_event, PresetManualCandidateSelectionContinuedEvent)
        self.assertEqual(("1", "3"), continued_event.selected_candidate_ids)

    def test_shared_queue_rejects_manual_preset_registration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            controller = AppController(
                runner=_ImmediatePresetRunner(root_path / "artifacts"),
                settings_provider=lambda: AppSettings(
                    executable_path=str(executable_path),
                    queue_mode="shared",
                ),
            )
            runtime = AppRuntime(
                controller=controller,
                repository=_RuntimeRepositoryStub(),
                prompt_store=_PresetPromptStoreStub(),
            )
            try:
                runtime.update_settings(
                    AppSettings(
                        executable_path=str(executable_path),
                        queue_mode="shared",
                    )
                )
                workspace = runtime.open_workspace(str(workspace_path)).open_result.workspace_tab
                parent = runtime.open_preset_session(workspace.workspace_tab_id)

                with self.assertRaisesRegex(ValueError, "워크스페이스 개별큐"):
                    runtime.submit_preset_analysis_job(
                        parent.session_tab_id,
                        language="Python",
                        instruction="bug",
                        work_priority="manual",
                    )

                self.assertEqual((), runtime.list_jobs(session_tab_id=parent.session_tab_id))
            finally:
                runtime.shutdown()

    def test_manual_pending_clears_on_queue_stop(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_manual_selection_contexts["preset-parent"] = (
            _PresetManualSelectionContext(
                workspace_tab_id="workspace-1",
                parent_session_tab_id="preset-parent",
                language="Python",
                instruction="bug",
                work_prompt_template="work {{candidates_payload}}",
                candidates=(_candidate("1"),),
                auto_commit_enabled=False,
                queue_control_generation=(0, 0),
            )
        )
        runtime._mark_preset_followup_pending("workspace-1")

        runtime.stop_queue("workspace-1")

        self.assertFalse(runtime._has_pending_preset_followup())
        self.assertEqual({}, runtime._preset_manual_selection_contexts)
        cleared_event = runtime._event_queue.events[0]
        self.assertIsInstance(cleared_event, PresetManualCandidateSelectionClearedEvent)
        self.assertEqual("preset-parent", cleared_event.parent_session_tab_id)

    def test_manual_pending_clears_on_session_close_and_queue_mode_change(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            controller = AppController(
                runner=_ImmediatePresetRunner(root_path / "artifacts"),
                settings_provider=lambda: AppSettings(
                    executable_path=str(executable_path),
                ),
            )
            runtime = AppRuntime(
                controller=controller,
                repository=_RuntimeRepositoryStub(),
                prompt_store=_PresetPromptStoreStub(),
            )
            try:
                workspace = runtime.open_workspace(str(workspace_path)).open_result.workspace_tab
                first_parent = runtime.open_preset_session(workspace.workspace_tab_id)
                runtime._preset_manual_selection_contexts[first_parent.session_tab_id] = (
                    _PresetManualSelectionContext(
                        workspace_tab_id=workspace.workspace_tab_id,
                        parent_session_tab_id=first_parent.session_tab_id,
                        language="Python",
                        instruction="bug",
                        work_prompt_template="work {{candidates_payload}}",
                        candidates=(_candidate("1"),),
                        auto_commit_enabled=False,
                        queue_control_generation=runtime._get_queue_control_generation(
                            workspace.workspace_tab_id
                        ),
                    )
                )
                runtime._mark_preset_followup_pending(workspace.workspace_tab_id)

                runtime.close_session(first_parent.session_tab_id)

                self.assertFalse(runtime._has_pending_preset_followup())
                self.assertEqual({}, runtime._preset_manual_selection_contexts)
                self.assertTrue(
                    any(
                        isinstance(event, PresetManualCandidateSelectionClearedEvent)
                        for event in runtime.drain_events()
                    )
                )

                second_parent = runtime.open_preset_session(workspace.workspace_tab_id)
                runtime._preset_manual_selection_contexts[second_parent.session_tab_id] = (
                    _PresetManualSelectionContext(
                        workspace_tab_id=workspace.workspace_tab_id,
                        parent_session_tab_id=second_parent.session_tab_id,
                        language="Python",
                        instruction="bug",
                        work_prompt_template="work {{candidates_payload}}",
                        candidates=(_candidate("2"),),
                        auto_commit_enabled=False,
                        queue_control_generation=runtime._get_queue_control_generation(
                            workspace.workspace_tab_id
                        ),
                    )
                )
                runtime._mark_preset_followup_pending(workspace.workspace_tab_id)

                runtime.update_settings(
                    AppSettings(
                        executable_path=str(executable_path),
                        queue_mode="shared",
                    )
                )

                self.assertFalse(runtime._has_pending_preset_followup())
                self.assertEqual({}, runtime._preset_manual_selection_contexts)
                self.assertTrue(
                    any(
                        isinstance(event, PresetManualCandidateSelectionClearedEvent)
                        for event in runtime.drain_events()
                    )
                )
            finally:
                runtime.shutdown()

    def test_preset_followup_pending_counts_same_workspace_actions(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = ("workspace-1",)
        runtime._dispatch_action_requested = True

        runtime._mark_preset_followup_pending("workspace-1")
        runtime._mark_preset_followup_pending("workspace-1")
        runtime._clear_preset_followup_pending("workspace-1")

        self.assertTrue(runtime._has_pending_preset_followup())

        runtime._dispatch_next_job_for_worker()

        self.assertEqual(0, runtime._controller.dispatch_next_job_calls)
        self.assertEqual(1, len(request_queue.requests))

        runtime._clear_preset_followup_pending("workspace-1")
        request_queue.requests[0].action()

        self.assertEqual(1, runtime._controller.dispatch_next_job_calls)
        self.assertFalse(runtime._has_pending_preset_followup())

    def test_preset_turn2_registration_pending_flag_clears_after_action(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        runtime._handle_preset_execution_result(event)

        self.assertTrue(runtime._has_pending_preset_followup())
        self.assertEqual(1, len(request_queue.requests))

        request_queue.requests[0].action()

        self.assertFalse(runtime._has_pending_preset_followup())
        self.assertEqual(
            [
                (
                    "preset-parent",
                    "work " + build_candidates_payload([_candidate("1", priority="high")]),
                )
            ],
            runtime._controller.submitted_jobs,
        )
