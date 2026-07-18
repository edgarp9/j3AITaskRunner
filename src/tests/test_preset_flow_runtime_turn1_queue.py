from __future__ import annotations

from tests._preset_flow_helpers import *

class PresetRuntimeFlowQueueTests(unittest.TestCase):
    def test_preset_analysis_continues_after_queue_stop_before_first_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = root_path / "agent.exe"
            executable_path.write_text("", encoding="utf-8")
            runner = _ImmediatePresetRunner(root_path / "artifacts")
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

                runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    auto_commit_enabled=False,
                )
                runtime.stop_queue(workspace.workspace_tab_id)
                runtime.start_queue(workspace.workspace_tab_id)

                self.assertTrue(
                    _drain_until(
                        runtime,
                        lambda: len(runner.launched_prompts) >= 4,
                    ),
                    "큐 재시작 뒤 프리셋 턴2와 후보 작업이 실행되지 않았습니다.",
                )

                parent_jobs = runtime.list_jobs(session_tab_id=parent.session_tab_id)
                self.assertEqual(2, len(parent_jobs))
                self.assertTrue(all(job.status == JobStatus.COMPLETED for job in parent_jobs))
                self.assertEqual(
                    (
                        _build_preset_analysis_prompt(
                            "analysis prompt",
                            work_priority="medium",
                        ),
                        "work "
                        + build_candidates_payload(
                            [
                                _candidate("1", priority="high"),
                                _candidate("2", priority="medium"),
                            ]
                        ),
                        "/goal candidate one",
                        "/goal candidate two",
                    ),
                    tuple(runner.launched_prompts[:4]),
                )
            finally:
                runtime.shutdown()

    def test_work_generation_completion_registers_candidate_jobs_in_input_order(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-model",
            reasoning_effort="high",
        )
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=False,
                execution_options=execution_options,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual(["preset-parent", "preset-parent"], runtime._controller.opened_parent_ids)
        self.assertEqual(
            [("candidate-1", "/goal one"), ("candidate-2", "/goal two")],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual(
            [execution_options, execution_options],
            runtime._controller.session_manager.candidate_session_execution_options,
        )
        self.assertEqual(
            [execution_options, execution_options],
            runtime._controller.submitted_execution_options,
        )
        self.assertIn("Preset turn2 result captured", log_text)
        self.assertIn("Preset turn2 completed; parsing generated prompts", log_text)
        self.assertIn("Preset turn2 parsed generated prompts", log_text)
        self.assertIn("Preset candidate jobs registered", log_text)
        self.assertEqual(("job-1", "job-2"), runtime._controller.prioritized_job_ids)
        self.assertEqual(["workspace-1"], runtime._controller.started_queue_ids)
        self.assertEqual(1, len(runtime._event_queue.events))
        registered_event = runtime._event_queue.events[0]
        self.assertIsInstance(registered_event, PresetCandidateJobsRegisteredEvent)
        self.assertEqual(("job-1", "job-2"), registered_event.registered_job_ids)
        self.assertFalse(registered_event.auto_commit_enabled)

    def test_work_generation_completion_enqueues_runtime_action_before_registration(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=False,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        runtime._handle_preset_execution_result(event)

        self.assertEqual([], runtime._controller.opened_parent_ids)
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual([], runtime._event_queue.events)
        self.assertEqual(1, len(request_queue.requests))

        registered_event = request_queue.requests[0].action()

        self.assertIsInstance(registered_event, PresetCandidateJobsRegisteredEvent)
        self.assertEqual(
            [("candidate-1", "/goal one"), ("candidate-2", "/goal two")],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual(("job-1", "job-2"), registered_event.registered_job_ids)

    def test_work_generation_completion_skips_candidates_when_parent_session_is_closed(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._controller.session_manager.sessions["preset-parent"] = _RuntimeSessionStub(
            "preset-parent",
            open_state=TabOpenState.CLOSED,
        )
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"),),
                auto_commit_enabled=True,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        with self.assertLogs("app.runtime", level="INFO") as captured_logs:
            runtime._handle_preset_execution_result(event)

        self.assertEqual([], runtime._controller.opened_parent_ids)
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertEqual([], runtime._controller.stopped_queues)
        self.assertEqual([], runtime._event_queue.events)
        self.assertIn(
            "Preset candidate job registration skipped because parent preset session is closed",
            "\n".join(captured_logs.output),
        )

    def test_candidate_registration_event_survives_queue_stop_after_action(
        self,
    ) -> None:
        runtime = _build_runtime_for_preset_flow()
        request_queue = _RuntimeActionRequestQueueStub()
        runtime._runtime_action_request_queue = request_queue
        runtime._runtime_action_completion_queue = Queue()
        runtime._persistence_shutdown_requested = False
        runtime._persistence_shutdown_sentinel_enqueued = False
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=False,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.COMPLETED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        runtime._handle_preset_execution_result(event)
        registered_event = request_queue.requests[0].action()
        runtime.stop_queue("workspace-1")
        runtime._runtime_action_completion_queue.put(
            _RuntimeActionCompletion(
                event=registered_event,
                queue_control_workspace_tab_id="workspace-1",
                queue_control_generation=(0, 0),
                drop_when_stale=request_queue.requests[0].drop_completion_when_stale,
            )
        )

        self.assertFalse(
            runtime._queue_start_is_current("workspace-1", (0, 0))
        )
        self.assertEqual(1, runtime._process_runtime_action_completions())
        self.assertEqual([registered_event], runtime._event_queue.events)

    def test_work_generation_timeout_failure_stops_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
        runtime._preset_work_generation_job_contexts["generation-job"] = (
            _PresetWorkGenerationJobContext(
                parent_session_tab_id="preset-parent",
                candidates=(_candidate("1"), _candidate("2")),
                auto_commit_enabled=True,
                queue_control_generation=(0, 0),
            )
        )
        event = JobExecutionResultCapturedEvent(
            job_id="generation-job",
            workspace_tab_id="workspace-1",
            session_tab_id="preset-parent",
            status=AgentRunStatus.FAILED,
            last_message=json.dumps(
                {
                    "prompts": [
                        {"candidate_id": "1", "title": "one", "prompt": "/goal one"},
                        {"candidate_id": "2", "title": "two", "prompt": "/goal two"},
                    ]
                },
                ensure_ascii=False,
            ),
        )

        with self.assertLogs("app.runtime", level="WARNING") as captured_logs:
            runtime._handle_preset_execution_result(event)
        log_text = "\n".join(captured_logs.output)

        self.assertEqual([], runtime._controller.opened_parent_ids)
        self.assertEqual([], runtime._controller.submitted_jobs)
        self.assertEqual((), runtime._controller.prioritized_job_ids)
        self.assertEqual([], runtime._controller.started_queue_ids)
        self.assertIn("Preset work-generation turn did not complete", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertEqual({}, runtime._preset_work_generation_job_contexts)
        self.assertEqual(1, len(runtime._event_queue.events))
        self.assertIsInstance(runtime._event_queue.events[0], RuntimeActionFailedEvent)

