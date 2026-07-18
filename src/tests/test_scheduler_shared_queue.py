from __future__ import annotations

from tests._app_runtime_helpers import *

class SchedulerSharedQueueTests(unittest.TestCase):
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

    def test_started_workspace_queues_run_independently(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(3),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(4),
        )

        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a",
            when=_dt(5),
        )
        second_job = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b",
            when=_dt(6),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.start_queue(other_workspace.workspace_tab_id)

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            second_job.job_id,
            self.scheduler.get_queue_state(other_workspace.workspace_tab_id).running_job_id,
        )

        stopped_state = self.scheduler.stop_queue(
            self.workspace_tab.workspace_tab_id,
            reason=QueueStopReason.USER_STOPPED,
            when=_dt(7),
        )

        self.assertEqual(QueueStatus.STOPPED, stopped_state.status)
        self.assertEqual(QueueStopReason.USER_STOPPED, stopped_state.last_stop_reason)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            first_job.job_id,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )
        self.assertEqual(
            second_job.job_id,
            self.scheduler.get_queue_state(other_workspace.workspace_tab_id).running_job_id,
        )
        self.assertEqual(
            ("workspace-a", "workspace-b"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_shared_queue_runs_one_global_fifo_job_at_a_time(self) -> None:
        self._use_shared_queue_mode()
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(3),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(4),
        )

        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a",
            when=_dt(5),
        )
        second_job = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b",
            when=_dt(6),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.start_queue(other_workspace.workspace_tab_id)

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            first_job.job_id,
            self.scheduler.get_queue_state(other_workspace.workspace_tab_id).running_job_id,
        )
        self.assertEqual(
            ("workspace-a",),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

        self.scheduler.complete_running_job(first_job.job_id, when=_dt(7))

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            ("workspace-a", "workspace-b"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_shared_queue_stop_is_global(self) -> None:
        self._use_shared_queue_mode()
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(3),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(4),
        )

        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a",
            when=_dt(5),
        )
        self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b",
            when=_dt(6),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        stopped_state = self.scheduler.stop_queue(other_workspace.workspace_tab_id)

        self.assertEqual(QueueStatus.STOPPED, stopped_state.status)
        self.assertEqual(
            QueueStatus.STOPPED,
            self.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).status,
        )
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(
            (first_job.job_id,),
            tuple(handle.handle_id for handle in self.executor.canceled_handles),
        )

    def test_shared_queue_priority_request_does_not_break_fifo_order(self) -> None:
        self._use_shared_queue_mode()
        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "first",
            when=_dt(3),
        )
        second_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "second",
            when=_dt(4),
        )

        self.scheduler.prioritize_queued_jobs((second_job.job_id,))
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            ("first", "second"),
            tuple(job.prompt for job in self.scheduler.list_jobs()),
        )

    def test_dispatch_can_exclude_workspace_with_pending_follow_up(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(3),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(4),
        )

        first_a = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a first",
            when=_dt(5),
        )
        second_a = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a second",
            when=_dt(6),
        )
        first_b = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b first",
            when=_dt(7),
        )
        second_b = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b second",
            when=_dt(8),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.start_queue(other_workspace.workspace_tab_id)
        with self.scheduler.defer_dispatch():
            self.scheduler.complete_running_job(first_a.job_id, when=_dt(9))
            self.scheduler.complete_running_job(first_b.job_id, when=_dt(10))

        self.scheduler.dispatch_next_job(
            excluded_workspace_tab_ids=(self.workspace_tab.workspace_tab_id,)
        )

        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(second_a.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_b.job_id).status)
        self.assertTrue(self.scheduler.has_pending_dispatch())
        self.assertEqual(
            (self.workspace_tab.workspace_tab_id,),
            self.scheduler.pending_dispatch_workspace_tab_ids(),
        )

        self.scheduler.dispatch_next_job()

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_a.job_id).status)
        self.assertFalse(self.scheduler.has_pending_dispatch())
        self.assertEqual(
            (
                "workspace-a first",
                "workspace-b first",
                "workspace-b second",
                "workspace-a second",
            ),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_dispatch_reuses_job_scan_when_filling_multiple_workspace_slots(self) -> None:
        workspace_tabs = [self.workspace_tab]
        session_tabs = [self.session_a]
        for offset, workspace_name in enumerate(("Beta", "Gamma", "Delta"), start=1):
            workspace_tab = self.workspace_manager.open_validated_workspace(
                rf"C:\Repo\{workspace_name}",
                when=_dt(3 + offset),
            ).workspace_tab
            workspace_tabs.append(workspace_tab)
            session_tabs.append(
                self.session_manager.open_session(
                    workspace_tab.workspace_tab_id,
                    when=_dt(7 + offset),
                )
            )

        first_jobs = []
        for workspace_index, session_tab in enumerate(session_tabs):
            first_job = self.scheduler.register_job(
                session_tab.session_tab_id,
                f"workspace-{workspace_index}-first",
                when=_dt(20 + workspace_index),
            )
            first_jobs.append(first_job)
            for job_index in range(1, 6):
                self.scheduler.register_job(
                    session_tab.session_tab_id,
                    f"workspace-{workspace_index}-{job_index}",
                    when=_dt(30 + workspace_index + job_index),
                )

        with self.scheduler.defer_dispatch():
            for workspace_tab in workspace_tabs:
                self.scheduler.start_queue(workspace_tab.workspace_tab_id)

        counting_jobs = _CountingJobDict(self.scheduler._jobs)
        self.scheduler._jobs = counting_jobs

        self.scheduler.dispatch_next_job()

        self.assertEqual(1, counting_jobs.values_calls)
        self.assertEqual(
            tuple(job.job_id for job in first_jobs),
            tuple(request.job_id for request in self.executor.launched_requests),
        )
        self.assertEqual(
            tuple(job.job_id for job in first_jobs),
            tuple(
                self.scheduler.get_queue_state(workspace_tab.workspace_tab_id).running_job_id
                for workspace_tab in workspace_tabs
            ),
        )
        self.assertTrue(
            all(
                self.scheduler.get_job(job.job_id).status == JobStatus.RUNNING
                for job in first_jobs
            )
        )

    def test_stop_all_queues_cancels_running_jobs_for_all_workspaces(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(3),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(4),
        )

        first_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a",
            when=_dt(5),
        )
        second_job = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b",
            when=_dt(6),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.start_queue(other_workspace.workspace_tab_id)

        states = self.scheduler.stop_all_queues(reason=QueueStopReason.USER_STOPPED)

        self.assertEqual(
            {QueueStatus.STOPPED},
            {state.status for state in states},
        )
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            ("job-1", "job-2"),
            tuple(handle.handle_id for handle in self.executor.canceled_handles),
        )

    def test_delete_job_removes_non_running_job(self) -> None:
        running_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "running",
            when=_dt(3),
        )
        queued_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "queued",
            when=_dt(4),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        deleted_job = self.scheduler.delete_job(queued_job.job_id)

        self.assertEqual(queued_job.job_id, deleted_job.job_id)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(running_job.job_id).status)
        with self.assertRaises(KeyError):
            self.scheduler.get_job(queued_job.job_id)

    def test_delete_job_rejects_running_job(self) -> None:
        job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "running",
            when=_dt(3),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        with self.assertRaisesRegex(ValueError, "Cannot delete a running job"):
            self.scheduler.delete_job(job.job_id)

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(job.job_id).status)

