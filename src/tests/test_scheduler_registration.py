from __future__ import annotations

from tests._app_runtime_helpers import *

class SchedulerRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_manager = WorkspaceManager()
        self.session_manager = SessionManager(self.workspace_manager)
        self.executor = _FakeExecutor()
        self.scheduler = Scheduler(
            workspace_manager=self.workspace_manager,
            session_manager=self.session_manager,
            executor=self.executor,
            settings_provider=lambda: AppSettings(
                executable_path=r"C:\Tools\agent.exe",
            ),
        )
        self.workspace_tab = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Alpha",
            when=_dt(0),
        ).workspace_tab
        self.session_a = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(1),
        )
        self.session_b = self.session_manager.open_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(2),
        )

    def _use_shared_queue_mode(self) -> None:
        self.scheduler = Scheduler(
            workspace_manager=self.workspace_manager,
            session_manager=self.session_manager,
            executor=self.executor,
            settings_provider=lambda: AppSettings(
                executable_path=r"C:\Tools\agent.exe",
                queue_mode="shared",
            ),
        )

    def test_job_registration_keeps_session_without_confirmed_session_id(self) -> None:
        job = self.scheduler.register_job(self.session_a.session_tab_id, "first prompt", when=_dt(3))

        self.assertEqual(self.session_a.session_tab_id, job.session_tab_id)
        self.assertIsNone(self.session_manager.get_session_tab(self.session_a.session_tab_id).session_id)
        self.assertEqual(JobStatus.QUEUED, job.status)

    def test_force_fresh_session_job_starts_without_existing_session_id(self) -> None:
        self.session_manager.assign_session_id(
            self.session_a.session_tab_id,
            "thread-parent",
            when=_dt(3),
        )
        job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "internal isolated prompt",
            when=_dt(4),
            force_fresh_session=True,
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.assertTrue(self.scheduler.get_job(job.job_id).force_fresh_session)
        self.assertEqual(1, len(self.executor.launched_requests))
        self.assertIsNone(self.executor.launched_requests[0].session_id)

    def test_start_and_stop_queue_keeps_running_job_until_completion_event_arrives(self) -> None:
        job = self.scheduler.register_job(self.session_a.session_tab_id, "run me", when=_dt(3))

        started_state = self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.assertEqual(QueueStatus.STARTED, started_state.status)
        self.assertEqual(
            job.job_id,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(job.job_id).status)
        self.assertEqual(("run me",), tuple(request.prompt for request in self.executor.launched_requests))

        stopped_state = self.scheduler.stop_queue(
            self.workspace_tab.workspace_tab_id,
            reason=QueueStopReason.USER_STOPPED,
            when=_dt(4),
        )

        self.assertEqual(QueueStatus.STOPPED, stopped_state.status)
        self.assertEqual(QueueStopReason.USER_STOPPED, stopped_state.last_stop_reason)
        self.assertEqual(
            job.job_id,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(job.job_id).status)
        self.assertEqual(("job-1",), tuple(handle.handle_id for handle in self.executor.canceled_handles))

    def test_immediate_job_runs_parallel_without_workspace_queue_slot(self) -> None:
        queued_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "queue job",
            when=_dt(3),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        immediate_job = self.scheduler.register_and_start_immediate_job(
            self.session_b.session_tab_id,
            "run now",
            when=_dt(4),
        )

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(queued_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, immediate_job.status)
        self.assertEqual(
            queued_job.job_id,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )
        self.assertEqual(
            ("queue job", "run now"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_dispatch_skips_same_session_follow_up_while_immediate_job_runs(self) -> None:
        immediate_job = self.scheduler.register_and_start_immediate_job(
            self.session_a.session_tab_id,
            "run now",
            when=_dt(3),
        )
        follow_up_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "auto commit",
            when=_dt(4),
        )
        other_session_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "other session",
            when=_dt(5),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(immediate_job.job_id).status)
        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(follow_up_job.job_id).status)
        self.assertEqual(
            JobStatus.RUNNING,
            self.scheduler.get_job(other_session_job.job_id).status,
        )
        self.assertEqual(
            ("run now", "other session"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_new_execution_request_uses_latest_timeout_settings_without_mutating_running_request(
        self,
    ) -> None:
        current_settings = AppSettings(
            executable_path=r"C:\Tools\agent.exe",
            execution_timeout_minutes=120,
            inactivity_timeout_minutes=30,
            termination_grace_seconds=5,
        )
        workspace_manager = WorkspaceManager()
        session_manager = SessionManager(workspace_manager)
        executor = _FakeExecutor()
        scheduler = Scheduler(
            workspace_manager=workspace_manager,
            session_manager=session_manager,
            executor=executor,
            settings_provider=lambda: current_settings,
        )
        workspace_tab = workspace_manager.open_validated_workspace(
            r"C:\Repo\Timeouts",
            when=_dt(0),
        ).workspace_tab
        session_tab = session_manager.open_session(
            workspace_tab.workspace_tab_id,
            when=_dt(1),
        )

        first_job = scheduler.register_job(
            session_tab.session_tab_id,
            "first",
            when=_dt(2),
        )
        scheduler.start_queue(workspace_tab.workspace_tab_id)

        self.assertEqual(1, len(executor.launched_requests))
        first_request = executor.launched_requests[0]
        self.assertEqual(120, first_request.operational_settings.execution_timeout_minutes)
        self.assertEqual(30, first_request.operational_settings.inactivity_timeout_minutes)
        self.assertEqual(5, first_request.operational_settings.termination_grace_seconds)

        current_settings = AppSettings(
            executable_path=r"C:\Tools\agent.exe",
            execution_timeout_minutes=0,
            inactivity_timeout_minutes=45,
            termination_grace_seconds=9,
        )
        second_job = scheduler.register_job(
            session_tab.session_tab_id,
            "second",
            when=_dt(3),
        )

        self.assertEqual(1, len(executor.launched_requests))
        self.assertEqual(120, first_request.operational_settings.execution_timeout_minutes)
        self.assertEqual(30, first_request.operational_settings.inactivity_timeout_minutes)
        self.assertEqual(5, first_request.operational_settings.termination_grace_seconds)

        scheduler.complete_running_job(first_job.job_id, when=_dt(4))

        self.assertEqual(2, len(executor.launched_requests))
        second_request = executor.launched_requests[1]
        self.assertEqual(second_job.job_id, second_request.job_id)
        self.assertEqual(0, second_request.operational_settings.execution_timeout_minutes)
        self.assertEqual(45, second_request.operational_settings.inactivity_timeout_minutes)
        self.assertEqual(9, second_request.operational_settings.termination_grace_seconds)

    def test_execution_request_uses_registered_agent_options_with_latest_runtime_controls(
        self,
    ) -> None:
        current_settings = AppSettings(
            agent_provider="codex",
            executable_paths={
                "codex": r"C:\Tools\codex.exe",
                "pi": r"C:\Tools\pi.exe",
            },
            execution_timeout_minutes=120,
        )
        workspace_manager = WorkspaceManager()
        session_manager = SessionManager(workspace_manager)
        executor = _FakeExecutor()
        scheduler = Scheduler(
            workspace_manager=workspace_manager,
            session_manager=session_manager,
            executor=executor,
            settings_provider=lambda: current_settings,
        )
        workspace_tab = workspace_manager.open_validated_workspace(
            r"C:\Repo\Snapshot",
            when=_dt(0),
        ).workspace_tab
        session_tab = session_manager.open_session(
            workspace_tab.workspace_tab_id,
            when=_dt(1),
        )

        job = scheduler.register_job(
            session_tab.session_tab_id,
            "snapshot",
            when=_dt(2),
            execution_options=AgentExecutionOptions(
                agent_provider="codex",
                model="gpt-5.4",
                reasoning_effort="high",
            ),
        )
        current_settings = AppSettings(
            agent_provider="pi",
            executable_paths={
                "codex": r"C:\Tools\codex-updated.exe",
                "pi": r"C:\Tools\pi.exe",
            },
            execution_timeout_minutes=15,
        )

        scheduler.start_queue(workspace_tab.workspace_tab_id)

        self.assertEqual(1, len(executor.launched_requests))
        request = executor.launched_requests[0]
        self.assertEqual(job.job_id, request.job_id)
        self.assertEqual("codex", request.operational_settings.agent_provider)
        self.assertEqual("gpt-5.4", request.execution_options.model)
        self.assertEqual("high", request.execution_options.reasoning_effort)
        self.assertEqual(r"C:\Tools\codex-updated.exe", request.operational_settings.executable_path)
        self.assertEqual(15, request.operational_settings.execution_timeout_minutes)

    def test_waiting_for_configuration_job_is_preserved_and_skipped(self) -> None:
        self.executor.blocked_prompts.add("needs-config")
        waiting_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "needs-config",
            when=_dt(3),
        )
        running_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "ready-now",
            when=_dt(4),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.assertEqual(
            JobStatus.WAITING_FOR_CONFIGURATION,
            self.scheduler.get_job(waiting_job.job_id).status,
        )
        self.assertEqual(
            "설정 확인 필요",
            self.scheduler.get_job(waiting_job.job_id).configuration_wait_reason,
        )
        self.assertEqual(
            JobStatus.RUNNING,
            self.scheduler.get_job(running_job.job_id).status,
        )
        self.assertEqual(
            running_job.job_id,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )

    def test_registered_older_other_session_job_runs_before_same_session_follow_up(self) -> None:
        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a first",
            when=_dt(3),
        )
        other_session_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b first",
            when=_dt(4),
        )
        follow_up_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a second",
            when=_dt(5),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.complete_running_job(first_job.job_id, when=_dt(6))

        self.assertEqual(JobStatus.COMPLETED, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(follow_up_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(other_session_job.job_id).status)
        self.assertEqual(
            ("session-a first", "session-b first"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_queue_stops_when_workspace_task_list_is_all_completed(self) -> None:
        job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "single task",
            when=_dt(3),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        completed_job = self.scheduler.complete_running_job(job.job_id, when=_dt(4))
        queue_state = self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id)

        self.assertEqual(JobStatus.COMPLETED, completed_job.status)
        self.assertEqual(QueueStatus.STOPPED, queue_state.status)
        self.assertIsNone(queue_state.running_job_id)
        self.assertEqual(QueueStopReason.ALL_JOBS_COMPLETED, queue_state.last_stop_reason)

    def test_waiting_for_configuration_job_keeps_queue_started_after_other_jobs_complete(self) -> None:
        self.executor.blocked_prompts.add("needs-config")
        waiting_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "needs-config",
            when=_dt(3),
        )
        running_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "ready-now",
            when=_dt(4),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.scheduler.complete_running_job(running_job.job_id, when=_dt(5))
        queue_state = self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id)

        self.assertEqual(
            JobStatus.WAITING_FOR_CONFIGURATION,
            self.scheduler.get_job(waiting_job.job_id).status,
        )
        self.assertEqual(QueueStatus.STARTED, queue_state.status)
        self.assertIsNone(queue_state.running_job_id)

    def test_job_registration_preserves_pending_queue_order(self) -> None:
        self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a first",
            when=_dt(3),
        )
        self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b first",
            when=_dt(4),
        )
        self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b second",
            when=_dt(5),
        )
        self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a second",
            when=_dt(6),
        )

        jobs = self.scheduler.list_jobs()

        self.assertEqual(
            (
                "session-a first",
                "session-b first",
                "session-b second",
                "session-a second",
            ),
            tuple(job.prompt for job in jobs),
        )
        self.assertEqual((1, 2, 3, 4), tuple(job.queue_order for job in jobs))

    def test_snapshot_jobs_by_id_does_not_use_queue_order_sorting(self) -> None:
        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a first",
            when=_dt(3),
        )
        second_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b first",
            when=_dt(4),
        )

        with patch(
            "app.scheduler._job_list_order_key",
            side_effect=AssertionError("snapshot should not sort jobs"),
        ):
            snapshot = self.scheduler.snapshot_jobs_by_id()

        self.assertEqual(
            {first_job.job_id, second_job.job_id},
            set(snapshot),
        )
        self.assertIs(self.scheduler.get_job(first_job.job_id), snapshot[first_job.job_id])
        self.assertIs(self.scheduler.get_job(second_job.job_id), snapshot[second_job.job_id])

