from __future__ import annotations

from tests._preset_flow_helpers import *

class PresetRuntimeFlowCandidateTests(unittest.TestCase):
    def test_work_generation_prompt_count_mismatch_stops_workspace(self) -> None:
        runtime = _build_runtime_for_preset_flow()
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
        self.assertIn("Preset flow stopped while parsing generated work prompts", log_text)
        self.assertIn("prompts 개수", log_text)
        self.assertIn("Preset flow stopped workspace queue", log_text)
        self.assertEqual(
            [("workspace-1", QueueStopReason.PRESET_FLOW_FAILED)],
            runtime._controller.stopped_queues,
        )
        self.assertEqual({}, runtime._preset_work_generation_job_contexts)
        self.assertEqual(1, len(runtime._event_queue.events))
        failed_event = runtime._event_queue.events[0]
        self.assertIsInstance(failed_event, RuntimeActionFailedEvent)
        self.assertIn("prompts 개수", failed_event.message)

    def test_work_generation_completion_inherits_auto_commit_to_candidate_sessions(self) -> None:
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

        self.assertEqual(["preset-parent", "preset-parent"], runtime._controller.opened_parent_ids)
        self.assertEqual(
            [
                ("candidate-1", "/goal one"),
                ("candidate-1", AUTO_COMMIT_PROMPT),
                ("candidate-2", "/goal two"),
                ("candidate-2", AUTO_COMMIT_PROMPT),
            ],
            runtime._controller.submitted_jobs,
        )
        self.assertEqual(
            ("job-1", "job-2", "job-3", "job-4"),
            runtime._controller.prioritized_job_ids,
        )
        registered_event = runtime._event_queue.events[0]
        self.assertIsInstance(registered_event, PresetCandidateJobsRegisteredEvent)
        self.assertEqual(("candidate-1", "candidate-2"), registered_event.candidate_session_tab_ids)
        self.assertEqual(("job-1", "job-2", "job-3", "job-4"), registered_event.registered_job_ids)
        self.assertTrue(registered_event.auto_commit_enabled)

    def test_p3_execution_options_reach_candidates_auto_commit_and_runner_settings(
        self,
    ) -> None:
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
            parent_execution_options = AgentExecutionOptions(
                agent_provider="codex",
                model="gpt-5.4-mini",
                reasoning_effort="low",
            )
            candidate_execution_options = AgentExecutionOptions(
                agent_provider="codex",
                model="gpt-5",
                reasoning_effort="high",
            )
            try:
                workspace = runtime.open_workspace(str(workspace_path)).open_result.workspace_tab
                session_one = runtime.open_session(workspace.workspace_tab_id)
                session_two = runtime.open_session(workspace.workspace_tab_id)
                parent = runtime.open_preset_session(workspace.workspace_tab_id)

                self.assertEqual(
                    ("S1", "S2", "P3"),
                    tuple(
                        tab.display_name
                        for tab in runtime.list_session_tabs(workspace.workspace_tab_id)
                    ),
                )

                runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    auto_commit_enabled=True,
                    execution_options=parent_execution_options,
                    candidate_execution_options=candidate_execution_options,
                )
                runtime.submit_job(session_one.session_tab_id, "existing queued")
                runtime.start_queue(workspace.workspace_tab_id)

                self.assertTrue(
                    _drain_until(
                        runtime,
                        lambda: len(runner.launched_prompts) >= 7,
                    ),
                    "P3 프리셋 후보 작업 실행 순서가 시간 안에 확인되지 않았습니다.",
                )

                session_tabs = runtime.list_session_tabs(workspace.workspace_tab_id)
                self.assertEqual(
                    ("S1", "S2", "P3", "P3-1", "P3-2"),
                    tuple(tab.display_name for tab in session_tabs),
                )
                self.assertEqual(
                    (session_one.session_tab_id, session_two.session_tab_id),
                    tuple(tab.session_tab_id for tab in session_tabs[:2]),
                )

                candidate_tabs = tuple(
                    tab
                    for tab in session_tabs
                    if tab.parent_session_tab_id == parent.session_tab_id
                )
                self.assertEqual(
                    (SessionTabKind.PRESET_CANDIDATE, SessionTabKind.PRESET_CANDIDATE),
                    tuple(tab.kind for tab in candidate_tabs),
                )

                parent_jobs = runtime.list_jobs(session_tab_id=parent.session_tab_id)
                self.assertEqual(2, len(parent_jobs))
                self.assertNotIn(AUTO_COMMIT_PROMPT, tuple(job.prompt for job in parent_jobs))
                self.assertTrue(all(job.status == JobStatus.COMPLETED for job in parent_jobs))
                self.assertEqual(
                    (parent_execution_options, parent_execution_options),
                    tuple(job.execution_options for job in parent_jobs),
                )
                locked_parent = runtime.get_session_tab(parent.session_tab_id)
                self.assertTrue(locked_parent.execution_options_locked)
                self.assertEqual(parent_execution_options, locked_parent.execution_options)
                self.assertEqual(
                    (candidate_execution_options, candidate_execution_options),
                    tuple(tab.execution_options for tab in candidate_tabs),
                )
                self.assertTrue(
                    all(tab.execution_options_locked for tab in candidate_tabs)
                )

                candidate_job_prompts = tuple(
                    tuple(job.prompt for job in runtime.list_jobs(session_tab_id=tab.session_tab_id))
                    for tab in candidate_tabs
                )
                self.assertEqual(
                    (
                        ("/goal candidate one", AUTO_COMMIT_PROMPT),
                        ("/goal candidate two", AUTO_COMMIT_PROMPT),
                    ),
                    candidate_job_prompts,
                )
                candidate_job_execution_options = tuple(
                    tuple(
                        job.execution_options
                        for job in runtime.list_jobs(session_tab_id=tab.session_tab_id)
                    )
                    for tab in candidate_tabs
                )
                self.assertEqual(
                    (
                        (candidate_execution_options, candidate_execution_options),
                        (candidate_execution_options, candidate_execution_options),
                    ),
                    candidate_job_execution_options,
                )
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
                        AUTO_COMMIT_PROMPT,
                        "/goal candidate two",
                        AUTO_COMMIT_PROMPT,
                        "existing queued",
                    ),
                    tuple(runner.launched_prompts[:7]),
                )
                self.assertEqual(
                    (
                        ("codex", "gpt-5.4-mini", "low"),
                        ("codex", "gpt-5.4-mini", "low"),
                        ("codex", "gpt-5", "high"),
                        ("codex", "gpt-5", "high"),
                        ("codex", "gpt-5", "high"),
                        ("codex", "gpt-5", "high"),
                    ),
                    tuple(
                        (
                            options.agent_provider,
                            options.model,
                            options.reasoning_effort,
                        )
                        for options in runner.launched_execution_options[:6]
                    ),
                )
            finally:
                runtime.shutdown()

    def test_p2_completion_creates_candidate_sessions_and_runs_them_first(self) -> None:
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
                runtime.open_session(workspace.workspace_tab_id)
                parent = runtime.open_preset_session(workspace.workspace_tab_id)
                existing_session = runtime.open_session(workspace.workspace_tab_id)

                self.assertEqual("P2", parent.display_name)
                runtime.submit_preset_analysis_job(
                    parent.session_tab_id,
                    language="Python",
                    instruction="bug",
                    work_priority="medium",
                    analysis_prompt_prefix="custom analysis prefix",
                    auto_commit_enabled=True,
                )
                runtime.submit_job(existing_session.session_tab_id, "existing queued")
                runtime.start_queue(workspace.workspace_tab_id)

                self.assertTrue(
                    _drain_until(
                        runtime,
                        lambda: len(runner.launched_prompts) >= 7,
                    ),
                    "프리셋 후보 작업 실행 순서가 시간 안에 확인되지 않았습니다.",
                )

                session_tabs = runtime.list_session_tabs(workspace.workspace_tab_id)
                candidate_tabs = tuple(
                    tab
                    for tab in session_tabs
                    if tab.parent_session_tab_id == parent.session_tab_id
                )
                self.assertEqual(("P2-1", "P2-2"), tuple(tab.display_name for tab in candidate_tabs))
                self.assertEqual(
                    (SessionTabKind.PRESET_CANDIDATE, SessionTabKind.PRESET_CANDIDATE),
                    tuple(tab.kind for tab in candidate_tabs),
                )

                parent_jobs = runtime.list_jobs(session_tab_id=parent.session_tab_id)
                self.assertNotIn(
                    "커밋해 주세요.",
                    tuple(job.prompt for job in parent_jobs),
                )
                self.assertTrue(all(job.status == JobStatus.COMPLETED for job in parent_jobs))
                self.assertEqual(2, len(parent_jobs))

                candidate_job_prompts = tuple(
                    runtime.list_jobs(session_tab_id=tab.session_tab_id)[0].prompt
                    for tab in candidate_tabs
                )
                self.assertEqual(("/goal candidate one", "/goal candidate two"), candidate_job_prompts)
                candidate_job_counts = tuple(
                    len(runtime.list_jobs(session_tab_id=tab.session_tab_id))
                    for tab in candidate_tabs
                )
                self.assertEqual((2, 2), candidate_job_counts)
                self.assertEqual(
                    (
                        _build_preset_analysis_prompt(
                            "analysis prompt",
                            work_priority="medium",
                            analysis_prompt_prefix="custom analysis prefix",
                        ),
                        "work "
                        + build_candidates_payload(
                            [
                                _candidate("1", priority="high"),
                                _candidate("2", priority="medium"),
                            ]
                        ),
                        "/goal candidate one",
                        AUTO_COMMIT_PROMPT,
                        "/goal candidate two",
                        AUTO_COMMIT_PROMPT,
                        "existing queued",
                    ),
                    tuple(runner.launched_prompts[:7]),
                )
            finally:
                runtime.shutdown()

