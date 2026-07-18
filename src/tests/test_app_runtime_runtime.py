from __future__ import annotations

from tests._app_runtime_helpers import *

class AppRuntimePollingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = AppRuntime(
            controller=_RuntimeControllerStub(),
            repository=_RuntimeRepositoryStub(),
        )

    def test_drain_events_respects_max_items_and_preserves_remaining_order(self) -> None:
        first = LogAppendedEvent(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            stream_name="progress",
            line="first",
        )
        second = LogAppendedEvent(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            stream_name="progress",
            line="second",
        )
        third = LogAppendedEvent(
            job_id="job-3",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            stream_name="progress",
            line="third",
        )
        for event in (first, second, third):
            self.runtime.event_queue.put(event)

        self.assertEqual((first, second), self.runtime.drain_events(max_items=2))
        self.assertEqual((third,), self.runtime.drain_events())

    def test_disabled_file_logging_keeps_ui_progress_log_events(self) -> None:
        log_event = LogAppendedEvent(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            stream_name="progress",
            line="hidden",
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeEventsControllerStub((log_event,))
        runtime._controller_state_lock = threading.RLock()
        runtime._event_queue = Queue()
        runtime._settings = AppSettings(file_logging_enabled=False)
        runtime._job_progress_logs = {"job-old": ["old"]}

        runtime._sync_controller_events()

        self.assertEqual((log_event,), runtime.drain_events())
        self.assertEqual(("hidden",), runtime.get_job_progress_logs("job-1"))
        self.assertEqual(("old",), runtime.get_job_progress_logs("job-old"))

class AppRuntimeWorkspaceJobListTests(unittest.TestCase):
    def test_list_workspace_jobs_filters_to_workspace_in_scheduler_order(self) -> None:
        first_workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
        )
        other_workspace_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-b",
            session_tab_id="session-b",
            prompt="other",
            queue_order=2,
        )
        second_workspace_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="second",
            queue_order=3,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub(
            (first_workspace_job, other_workspace_job, second_workspace_job)
        )

        jobs = runtime.list_workspace_jobs("workspace-a")

        self.assertEqual(("job-1", "job-3"), tuple(job.job_id for job in jobs))
        self.assertEqual(
            ["workspace-a"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )

    def test_list_jobs_by_workspace_uses_scheduler_grouped_lookup(self) -> None:
        first_workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
        )
        other_workspace_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-b",
            session_tab_id="session-b",
            prompt="other",
            queue_order=2,
        )
        second_workspace_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="second",
            queue_order=3,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub(
            (first_workspace_job, other_workspace_job, second_workspace_job)
        )

        jobs_by_workspace = runtime.list_jobs_by_workspace(("workspace-a", "workspace-b"))

        self.assertEqual(
            ("job-1", "job-3"),
            tuple(job.job_id for job in jobs_by_workspace["workspace-a"]),
        )
        self.assertEqual(
            ("job-2",),
            tuple(job.job_id for job in jobs_by_workspace["workspace-b"]),
        )
        self.assertEqual(
            ["workspace-a", "workspace-b"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )
        self.assertEqual(
            [("workspace-a", "workspace-b")],
            runtime._controller.scheduler.list_jobs_by_workspace_requests,
        )
        self.assertEqual(0, runtime._controller.scheduler.list_jobs_calls)

    def test_summarize_workspace_jobs_uses_scheduler_summary_lookup(self) -> None:
        first_workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
            status=JobStatus.RUNNING,
        )
        other_workspace_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-b",
            session_tab_id="session-b",
            prompt="other",
            queue_order=2,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub(
            (first_workspace_job, other_workspace_job)
        )

        summaries = runtime.summarize_workspace_jobs(("workspace-a", "workspace-b"))

        self.assertTrue(summaries["workspace-a"].has_jobs)
        self.assertTrue(summaries["workspace-a"].has_running_job)
        self.assertTrue(summaries["workspace-b"].has_jobs)
        self.assertFalse(summaries["workspace-b"].has_running_job)
        self.assertEqual(
            ["workspace-a", "workspace-b"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )
        self.assertEqual(
            [("workspace-a", "workspace-b")],
            runtime._controller.scheduler.summarize_workspace_jobs_requests,
        )
        self.assertEqual([], runtime._controller.scheduler.list_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_jobs_by_workspace_requests)
        self.assertEqual(0, runtime._controller.scheduler.list_jobs_calls)

    def test_workspace_has_jobs_uses_scheduler_presence_lookup(self) -> None:
        workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub((workspace_job,))

        self.assertTrue(runtime.workspace_has_jobs("workspace-a"))

        self.assertEqual(
            ["workspace-a"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )
        self.assertEqual(
            ["workspace-a"],
            runtime._controller.scheduler.workspace_has_jobs_requests,
        )
        self.assertEqual([], runtime._controller.scheduler.summarize_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_jobs_by_workspace_requests)
        self.assertEqual(0, runtime._controller.scheduler.list_jobs_calls)

    def test_workspace_has_runnable_jobs_uses_scheduler_runnable_lookup(self) -> None:
        workspace_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="first",
            queue_order=1,
            status=JobStatus.QUEUED,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspaceJobControllerStub((workspace_job,))

        self.assertTrue(runtime.workspace_has_runnable_jobs("workspace-a"))

        self.assertEqual(
            ["workspace-a"],
            runtime._controller.workspace_manager.requested_workspace_tab_ids,
        )
        self.assertEqual(
            ["workspace-a"],
            runtime._controller.scheduler.workspace_has_runnable_jobs_requests,
        )
        self.assertEqual([], runtime._controller.scheduler.workspace_has_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.summarize_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_workspace_jobs_requests)
        self.assertEqual([], runtime._controller.scheduler.list_jobs_by_workspace_requests)
        self.assertEqual(0, runtime._controller.scheduler.list_jobs_calls)

    def test_delete_job_clears_runtime_job_caches(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-a",
            session_tab_id="session-a",
            prompt="delete me",
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeDeleteControllerStub(job)
        runtime._job_progress_logs = {"job-1": ["log"], "job-2": ["keep"]}
        runtime._job_user_messages = {"job-1": "message", "job-2": "keep"}

        deleted_job = runtime.delete_job("job-1")

        self.assertEqual(job, deleted_job)
        self.assertEqual(["job-1"], runtime._controller.deleted_job_ids)
        self.assertNotIn("job-1", runtime._job_progress_logs)
        self.assertNotIn("job-1", runtime._job_user_messages)
        self.assertEqual(["keep"], runtime._job_progress_logs["job-2"])

    def test_workspace_path_has_running_job_matches_open_workspace_path(self) -> None:
        workspace_manager = WorkspaceManager()
        workspace_tab = workspace_manager.open_validated_workspace(
            r"C:\Repo\Alpha",
            when=_dt(1),
        ).workspace_tab
        running_job = Job(
            job_id="job-1",
            workspace_tab_id=workspace_tab.workspace_tab_id,
            session_tab_id="session-a",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspacePathRunningControllerStub(
            workspace_manager,
            (running_job,),
        )

        self.assertTrue(runtime.workspace_path_has_running_job(r"c:/repo/alpha/"))

    def test_workspace_path_has_running_job_ignores_non_running_jobs(self) -> None:
        workspace_manager = WorkspaceManager()
        workspace_tab = workspace_manager.open_validated_workspace(
            r"C:\Repo\Alpha",
            when=_dt(1),
        ).workspace_tab
        queued_job = Job(
            job_id="job-1",
            workspace_tab_id=workspace_tab.workspace_tab_id,
            session_tab_id="session-a",
            prompt="queued",
            status=JobStatus.QUEUED,
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = _RuntimeWorkspacePathRunningControllerStub(
            workspace_manager,
            (queued_job,),
        )

        self.assertFalse(runtime.workspace_path_has_running_job(r"C:\Repo\Alpha"))

class AppRuntimeDeferredDispatchTests(unittest.TestCase):
    def test_submit_job_runs_deferred_dispatch_on_runtime_worker(self) -> None:
        controller = _RuntimeDispatchControllerStub()
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())
        caller_thread_id = threading.get_ident()

        job = runtime.submit_job("session-1", "prompt")

        self.assertEqual("job-1", job.job_id)
        self.assertEqual([False], controller.submit_dispatch_immediately_values)
        self.assertTrue(
            _wait_until(lambda: bool(controller.dispatch_thread_ids)),
            "deferred submit dispatch did not run",
        )
        self.assertNotEqual(caller_thread_id, controller.dispatch_thread_ids[0])

    def test_submit_immediate_job_runs_on_runtime_worker_and_queues_auto_commit(self) -> None:
        controller = _RuntimeDispatchControllerStub()
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())
        caller_thread_id = threading.get_ident()

        runtime.submit_immediate_job(
            "session-1",
            "prompt",
            auto_commit_enabled=True,
        )

        self.assertTrue(
            _wait_until(lambda: bool(controller.immediate_thread_ids)),
            "immediate submit did not run",
        )
        self.assertEqual([("session-1", "prompt")], controller.immediate_calls)
        self.assertNotEqual(caller_thread_id, controller.immediate_thread_ids[0])
        self.assertEqual([False], controller.submit_dispatch_immediately_values)

    def test_deferred_dispatch_holds_controller_state_lock_while_dispatch_runs(
        self,
    ) -> None:
        controller = _RuntimeDispatchControllerStub()
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())
        controller.block_dispatch = True

        runtime.submit_job("session-1", "prompt")

        self.assertTrue(
            controller.dispatch_started.wait(timeout=1.0),
            "deferred dispatch did not run",
        )

        controller_lock = runtime._get_controller_state_lock()
        acquired = controller_lock.acquire(blocking=False)
        if acquired:
            controller_lock.release()

        controller.release_dispatch.set()
        self.assertTrue(
            _wait_until(lambda: not runtime.has_pending_background_work()),
            "deferred dispatch did not finish",
        )
        self.assertFalse(acquired, "deferred dispatch did not hold the controller lock")

    def test_completion_poll_runs_deferred_dispatch_on_runtime_worker(self) -> None:
        controller = _RuntimeDispatchControllerStub(background_events_to_process=1)
        runtime = AppRuntime(controller=controller, repository=_RuntimeRepositoryStub())
        caller_thread_id = threading.get_ident()

        processed = runtime.process_background_events()

        self.assertEqual(1, processed)
        self.assertEqual([False], controller.process_dispatch_immediately_values)
        self.assertTrue(
            _wait_until(lambda: bool(controller.dispatch_thread_ids)),
            "deferred completion dispatch did not run",
        )
        self.assertNotEqual(caller_thread_id, controller.dispatch_thread_ids[0])

class AppRuntimePromptImportTests(unittest.TestCase):
    def test_open_normal_and_preset_sessions_seed_default_ai_options(self) -> None:
        controller = _RuntimeSessionOpenControllerStub()
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = controller
        runtime._controller_state_lock = threading.RLock()
        runtime._settings = AppSettings(
            agent_provider="pi",
            executable_paths={"pi": r"C:\Tools\pi.exe"},
            default_model="pi-pro",
            default_reasoning_effort="high",
        )
        expected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-pro",
            reasoning_effort="high",
        )

        normal_session = runtime.open_session("workspace-1")
        preset_session = runtime.open_preset_session("workspace-1")

        self.assertEqual(
            [expected_execution_options],
            controller.open_session_execution_options,
        )
        self.assertEqual(
            [expected_execution_options],
            controller.open_preset_session_execution_options,
        )
        self.assertEqual(expected_execution_options, normal_session.execution_options)
        self.assertEqual(expected_execution_options, preset_session.execution_options)

    def test_open_sessions_reuse_last_top_execution_options_for_workspace_path(
        self,
    ) -> None:
        controller = _RuntimeSessionOpenControllerStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
                "workspace-3": r"D:\OtherRepo",
            },
        )
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = controller
        runtime._controller_state_lock = threading.RLock()
        runtime._settings = AppSettings(
            agent_provider="codex",
            executable_paths={"codex": "codex", "pi": "pi"},
        )
        selected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-pro",
            reasoning_effort="high",
        )

        first_session = runtime.open_session("workspace-1")
        runtime.set_session_execution_options(
            first_session.session_tab_id,
            selected_execution_options,
        )
        same_workspace_session = runtime.open_session("workspace-2")
        same_workspace_preset = runtime.open_preset_session("workspace-2")
        other_workspace_session = runtime.open_session("workspace-3")

        self.assertEqual(
            selected_execution_options,
            same_workspace_session.execution_options,
        )
        self.assertEqual(
            selected_execution_options,
            same_workspace_preset.execution_options,
        )
        self.assertEqual(
            AgentExecutionOptions(agent_provider="codex"),
            other_workspace_session.execution_options,
        )

    def test_import_prompt_sessions_creates_single_session_and_jobs_by_default(self) -> None:
        controller = _RuntimePromptImportControllerStub()
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = controller
        runtime._controller_state_lock = threading.RLock()
        runtime._event_queue = Queue()
        runtime._settings = AppSettings(
            agent_provider="pi",
            executable_paths={"pi": "pi"},
        )
        expected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
        )

        result = runtime.import_prompt_sessions(
            "workspace-1",
            ("first prompt", "second prompt"),
            auto_commit_enabled=True,
        )

        self.assertEqual(
            ["workspace-1"],
            controller.open_session_workspace_ids,
        )
        self.assertEqual(
            [expected_execution_options],
            controller.open_session_execution_options,
        )
        self.assertEqual(
            [
                ("session-1", "first prompt", False),
                ("session-1", AUTO_COMMIT_PROMPT, False),
                ("session-1", "second prompt", False),
                ("session-1", AUTO_COMMIT_PROMPT, False),
            ],
            controller.submitted_jobs,
        )
        self.assertEqual(
            [expected_execution_options] * 4,
            controller.submitted_execution_options,
        )
        self.assertEqual(
            [
                ("session-1", expected_execution_options),
            ],
            controller.session_manager.locked_execution_options,
        )
        self.assertEqual(
            ("session-1",),
            tuple(session.session_tab_id for session in result.session_tabs),
        )
        self.assertEqual(
            ("session-1", "session-1"),
            tuple(
                registration.session_tab.session_tab_id
                for registration in result.registrations
            ),
        )
        self.assertEqual(
            ("first prompt", AUTO_COMMIT_PROMPT, "second prompt", AUTO_COMMIT_PROMPT),
            tuple(job.prompt for job in result.registered_jobs),
        )

    def test_import_prompt_sessions_can_create_session_per_step(self) -> None:
        controller = _RuntimePromptImportControllerStub()
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = controller
        runtime._controller_state_lock = threading.RLock()
        runtime._event_queue = Queue()
        runtime._settings = AppSettings(
            agent_provider="pi",
            executable_paths={"pi": "pi"},
        )
        expected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
        )

        result = runtime.import_prompt_sessions(
            "workspace-1",
            ("first prompt", "second prompt"),
            auto_commit_enabled=True,
            step_execution_mode=StepExecutionMode.PER_STEP_SESSION,
        )

        self.assertEqual(
            ["workspace-1", "workspace-1"],
            controller.open_session_workspace_ids,
        )
        self.assertEqual(
            [expected_execution_options, expected_execution_options],
            controller.open_session_execution_options,
        )
        self.assertEqual(
            [
                ("session-1", "first prompt", False),
                ("session-1", AUTO_COMMIT_PROMPT, False),
                ("session-2", "second prompt", False),
                ("session-2", AUTO_COMMIT_PROMPT, False),
            ],
            controller.submitted_jobs,
        )
        self.assertEqual(
            [expected_execution_options] * 4,
            controller.submitted_execution_options,
        )
        self.assertEqual(
            [
                ("session-1", expected_execution_options),
                ("session-2", expected_execution_options),
            ],
            controller.session_manager.locked_execution_options,
        )
        self.assertEqual(
            ("session-1", "session-2"),
            tuple(session.session_tab_id for session in result.session_tabs),
        )
        self.assertEqual(
            ("first prompt", AUTO_COMMIT_PROMPT, "second prompt", AUTO_COMMIT_PROMPT),
            tuple(job.prompt for job in result.registered_jobs),
        )

    def test_import_prompt_sessions_uses_supplied_agent_execution_options(self) -> None:
        controller = _RuntimePromptImportControllerStub()
        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = controller
        runtime._controller_state_lock = threading.RLock()
        runtime._event_queue = Queue()
        runtime._settings = AppSettings(
            agent_provider="pi",
            executable_paths={"codex": "codex", "pi": "pi"},
        )
        selected_execution_options = AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4",
            reasoning_effort="high",
        )

        runtime.import_prompt_sessions(
            "workspace-1",
            ("first prompt", "second prompt"),
            auto_commit_enabled=True,
            execution_options=selected_execution_options,
        )

        self.assertEqual(
            [selected_execution_options],
            controller.open_session_execution_options,
        )
        self.assertEqual(
            [selected_execution_options] * 4,
            controller.submitted_execution_options,
        )
        self.assertEqual(
            [
                ("session-1", selected_execution_options),
            ],
            controller.session_manager.locked_execution_options,
        )

        per_step_controller = _RuntimePromptImportControllerStub()
        per_step_runtime = AppRuntime.__new__(AppRuntime)
        per_step_runtime._controller = per_step_controller
        per_step_runtime._controller_state_lock = threading.RLock()
        per_step_runtime._event_queue = Queue()
        per_step_runtime._settings = runtime._settings

        per_step_runtime.import_prompt_sessions(
            "workspace-1",
            ("first prompt", "second prompt"),
            auto_commit_enabled=True,
            execution_options=selected_execution_options,
            step_execution_mode=StepExecutionMode.PER_STEP_SESSION,
        )

        self.assertEqual(
            [selected_execution_options, selected_execution_options],
            per_step_controller.open_session_execution_options,
        )
        self.assertEqual(
            [selected_execution_options] * 4,
            per_step_controller.submitted_execution_options,
        )
        self.assertEqual(
            [
                ("session-1", selected_execution_options),
                ("session-2", selected_execution_options),
            ],
            per_step_controller.session_manager.locked_execution_options,
        )




