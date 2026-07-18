from __future__ import annotations

from tests._preset_flow_helpers import *

class PresetRuntimeFlowTests(unittest.TestCase):
    def test_preset_analysis_job_is_registered_once_per_parent_session(self) -> None:
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
                parent = runtime.open_preset_session(workspace.workspace_tab_id)

                first_job = runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    auto_commit_enabled=True,
                )

                with self.assertRaisesRegex(ValueError, "이미 등록"):
                    runtime.submit_preset_analysis_job(
                        parent.session_tab_id,
                        language="Python",
                        instruction="bug",
                        work_priority="medium",
                        auto_commit_enabled=True,
                    )

                self.assertEqual(
                    (first_job.job_id,),
                    tuple(
                        job.job_id
                        for job in runtime.list_jobs(
                            session_tab_id=parent.session_tab_id
                        )
                    ),
                )
            finally:
                runtime.shutdown()

    def test_closed_preset_session_rejects_delayed_analysis_registration(self) -> None:
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
                parent = runtime.open_preset_session(workspace.workspace_tab_id)
                runtime.close_session(parent.session_tab_id)

                with self.assertRaisesRegex(ValueError, "닫힌 프리셋 세션"):
                    runtime.submit_preset_analysis_job(
                        parent.session_tab_id,
                        language="Python",
                        instruction="bug",
                        work_priority="medium",
                        auto_commit_enabled=True,
                    )

                self.assertEqual((), runtime.list_jobs(session_tab_id=parent.session_tab_id))
            finally:
                runtime.shutdown()

    def test_analysis_completion_registers_work_generation_job(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-model",
            reasoning_effort="high",
        )
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            execution_options=execution_options,
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
                    _candidate_payload("2", priority="low"),
                ]
            ),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual(
            [("preset-parent", "work " + build_candidates_payload([_candidate("1", priority="high")]))],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual([True], runtime._controller.submitted_force_fresh_sessions)
        self.assertEqual(
            [execution_options],
            runtime._controller.submitted_execution_options,
        )
        self.assertIn("Preset turn1 result captured", log_text)
        self.assertIn("Preset turn1 completed; preparing turn2", log_text)
        self.assertIn("Preset turn2 registered", log_text)
        self.assertEqual(("job-1",), runtime._controller.prioritized_job_ids)
        self.assertEqual(["workspace-1"], runtime._controller.started_queue_ids)
        self.assertEqual(["job-1"], list(runtime._preset_work_generation_job_contexts))
        self.assertTrue(
            runtime._preset_work_generation_job_contexts["job-1"].auto_commit_enabled
        )
        self.assertEqual(
            execution_options,
            runtime._preset_work_generation_job_contexts["job-1"].execution_options,
        )
        self.assertEqual(
            ("1",),
            tuple(
                candidate.id
                for candidate in runtime._preset_work_generation_job_contexts["job-1"].candidates
            ),
        )


    def test_analysis_empty_candidates_skips_turn2_without_stopping_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message='{"candidates": []}',
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual([], runtime._controller.stopped_queues)
        self.assertEqual([], runtime._event_queue.events)
        self.assertEqual({}, runtime._preset_analysis_job_contexts)
        self.assertEqual({}, runtime._preset_work_generation_job_contexts)
        self.assertIn("Preset turn2 skipped because no candidates matched work priority", log_text)

    def test_analysis_empty_response_stops_workspace_before_turn2(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message="",
        )

        with self.assertLogs("app.runtime", level="WARNING") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertIn("Preset turn2 not started because turn1 response was empty", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(1, len(runtime._event_queue.events))
        self.assertIsInstance(runtime._event_queue.events[0], RuntimeActionFailedEvent)

    def test_analysis_completion_skips_turn2_when_parent_session_is_closed(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._controller.session_manager.sessions["preset-parent"] = _RuntimeSessionStub(
            "preset-parent",
            open_state=TabOpenState.CLOSED,
        )
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual([], runtime._controller.stopped_queues)
        self.assertEqual([], runtime._event_queue.events)
        self.assertIn(
            "Preset turn2 skipped because parent preset session is closed",
            "\n".join(captured_logs.output),
        )

    def test_analysis_invalid_response_stops_workspace_before_turn2(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([{"id": "1"}]),
        )

        with self.assertLogs("app.runtime", level="WARNING") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertIn("Preset turn2 not started because turn1 response could not be used", log_text)
        self.assertIn("필수 필드", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(1, len(runtime._event_queue.events))
        failed_event = runtime._event_queue.events[0]
        self.assertIsInstance(failed_event, RuntimeActionFailedEvent)
        self.assertIn("필수 필드", failed_event.message)

    def test_active_workspace_generation_change_keeps_other_workspace_preset_current(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )
        runtime._advance_queue_control_generation(None)
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        runtime._handle_preset_execution_result(event)

        self.assertFalse(runtime._queue_start_is_current(None, (0, 0)))
        self.assertTrue(runtime._queue_start_is_current("workspace-1", (0, 0)))
        self.assertEqual(
            [
                (
                    "preset-parent",
                    "work " + build_candidates_payload([_candidate("1", priority="high")]),
                )
            ],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual(["workspace-1"], runtime._controller.started_queue_ids)

    def test_active_workspace_stop_invalidates_same_workspace_pending_turn2(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._controller.workspace_manager = _ActiveWorkspaceManagerStub("workspace-1")
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )

        runtime.stop_queue(None)
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        runtime._handle_preset_execution_result(event)

        self.assertFalse(runtime._queue_start_is_current("workspace-1", (0, 0)))
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual(
            [("workspace-1", QueueStopReason.USER_STOPPED)],
            runtime._controller.stopped_queues,
        )
        self.assertEqual({}, runtime._preset_analysis_job_contexts)

    def test_stale_enqueued_turn1_followup_clears_pending_and_rechecks_dispatch(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = ("workspace-1",)
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
        runtime._advance_queue_control_generation("workspace-1")

        self.assertTrue(runtime._has_pending_preset_followup())
        self.assertEqual(1, len(request_queue.requests))
        self.assertTrue(
            runtime._runtime_action_request_is_stale(request_queue.requests[0])
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            AppRuntime._discard_runtime_action_request(request_queue.requests[0])

        self.assertFalse(runtime._has_pending_preset_followup())
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual(2, len(request_queue.requests))
        self.assertIn(
            "Preset turn1 follow-up discarded because queue generation is stale",
            "\n".join(captured_logs.output),
        )

        request_queue.requests[1].action()

        self.assertEqual(1, runtime._controller.dispatch_next_job_calls)

    def test_dispatch_waits_while_preset_turn2_registration_is_pending(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = ("workspace-1",)
        runtime._dispatch_action_requested = True
        runtime._mark_preset_followup_pending("workspace-1")

        runtime._dispatch_next_job_for_worker()

        self.assertEqual(0, runtime._controller.dispatch_next_job_calls)
        self.assertEqual(1, len(request_queue.requests))
        self.assertTrue(runtime._dispatch_action_requested)

        runtime._clear_preset_followup_pending("workspace-1")
        request_queue.requests[0].action()

        self.assertEqual(1, runtime._controller.dispatch_next_job_calls)
        self.assertFalse(runtime._controller.pending_dispatch)

    def test_dispatch_rechecks_preset_followup_pending_after_state_lock(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = ("workspace-1",)
        runtime._dispatch_action_requested = True
        runtime._controller_state_lock = _MarkPresetFollowupPendingOnEnterLock(
            lambda: runtime._mark_preset_followup_pending("workspace-1")
        )

        runtime._dispatch_next_job_for_worker()

        self.assertEqual(0, runtime._controller.dispatch_next_job_calls)
        self.assertTrue(runtime._has_pending_preset_followup())
        self.assertEqual(1, len(request_queue.requests))
        self.assertTrue(runtime._dispatch_action_requested)

    def test_dispatch_excludes_pending_preset_followup_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._controller.pending_dispatch = True
        runtime._controller.pending_dispatch_workspace_tab_ids_value = (
            "workspace-1",
            "workspace-2",
        )
        runtime._dispatch_action_requested = True
        runtime._mark_preset_followup_pending("workspace-1")

        runtime._dispatch_next_job_for_worker()

        self.assertEqual(1, runtime._controller.dispatch_next_job_calls)
        self.assertEqual(
            [("workspace-1",)],
            runtime._controller.dispatch_excluded_workspace_tab_ids,
        )
        self.assertFalse(runtime._controller.pending_dispatch)

    def test_running_generation_is_captured_before_user_stop_invalidates_turn2(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=False,
            queue_control_generation=(0, 0),
        )
        runtime._controller.pending_dispatch = True
        runtime._controller.running_status_events.append(
            JobStatusChangedEvent(
                job_id="analysis-job",
                workspace_tab_id="workspace-1",
                session_tab_id="preset-parent",
                previous_status=JobStatus.QUEUED,
                current_status=JobStatus.RUNNING,
                configuration_wait_reason=None,
                user_message="실행 중",
            )
        )

        runtime._dispatch_next_job_for_worker()
        runtime.stop_queue("workspace-1")
        self.assertEqual(
            (0, 0),
            runtime._preset_analysis_job_contexts[
                "analysis-job"
            ].queue_control_generation,
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=_analysis_text([_candidate_payload("1", priority="high")]),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertIn(
            "Preset turn1 result captured",
            "\n".join(captured_logs.output),
        )

    def test_closing_unrelated_idle_session_does_not_block_turn2_registration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            runner = _DeferredFirstPresetRunner(root_path / "artifacts")
            controller = AppController(
                runner=runner,
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
                parent = runtime.open_preset_session(workspace.workspace_tab_id)
                unrelated_session = runtime.open_session(workspace.workspace_tab_id)
                analysis_job = runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    auto_commit_enabled=False,
                )
                runtime.start_queue(workspace.workspace_tab_id)

                self.assertTrue(
                    _drain_until(runtime, lambda: bool(runner.launched_prompts)),
                    "프리셋 턴1이 시간 안에 시작되지 않았습니다.",
                )

                runtime.close_session(unrelated_session.session_tab_id)
                runner.resolve(analysis_job.job_id)

                expected_turn2_prompt = "work " + build_candidates_payload(
                    [
                        _candidate("1", priority="high"),
                        _candidate("2", priority="medium"),
                    ]
                )
                self.assertTrue(
                    _drain_until(
                        runtime,
                        lambda: len(runner.launched_prompts) >= 2,
                    ),
                    "무관한 유휴 세션을 닫은 뒤 프리셋 턴2가 시작되지 않았습니다.",
                )
                self.assertEqual(expected_turn2_prompt, runner.launched_prompts[1])
            finally:
                runtime.shutdown()


    def test_analysis_timeout_failure_with_candidates_stops_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_analysis_job_contexts["analysis-job"] = _PresetAnalysisJobContext(
            language="Python",
            instruction="bug",
            work_prompt_template="work {{candidates_payload}}",
            work_priority="medium",
            auto_commit_enabled=True,
            queue_control_generation=(0, 0),
        )
        event = JobExecutionResultCapturedEvent(
            job_id="analysis-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.FAILED,
            last_message=_analysis_text(
                [
                    _candidate_payload("1", priority="high"),
                    _candidate_payload("2", priority="medium"),
                ]
            ),
        )

        with self.assertLogs("app.runtime", level="WARNING") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertIn("Preset turn2 skipped because turn1 did not complete", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertEqual({}, runtime._preset_analysis_job_contexts)
        self.assertEqual({}, runtime._preset_work_generation_job_contexts)
        self.assertEqual(1, len(runtime._event_queue.events))
        self.assertIsInstance(runtime._event_queue.events[0], RuntimeActionFailedEvent)


