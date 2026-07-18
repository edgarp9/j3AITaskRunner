from __future__ import annotations

from tests._app_runtime_helpers import *

class SchedulerOrderingTests(unittest.TestCase):
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

    def test_list_jobs_by_workspace_groups_requested_workspaces_in_queue_order(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(7),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(8),
        )
        workspace_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a first",
            when=_dt(9),
        )
        other_workspace_job = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b first",
            when=_dt(10),
        )
        second_workspace_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "workspace-a second",
            when=_dt(11),
        )
        second_other_workspace_job = self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b second",
            when=_dt(12),
        )

        jobs_by_workspace = self.scheduler.list_jobs_by_workspace(
            (other_workspace.workspace_tab_id, self.workspace_tab.workspace_tab_id)
        )

        self.assertEqual(
            (other_workspace.workspace_tab_id, self.workspace_tab.workspace_tab_id),
            tuple(jobs_by_workspace),
        )
        self.assertEqual(
            (other_workspace_job.job_id, second_other_workspace_job.job_id),
            tuple(job.job_id for job in jobs_by_workspace[other_workspace.workspace_tab_id]),
        )
        self.assertEqual(
            (workspace_job.job_id, second_workspace_job.job_id),
            tuple(job.job_id for job in jobs_by_workspace[self.workspace_tab.workspace_tab_id]),
        )

    def test_summarize_workspace_jobs_reports_presence_without_sorting(self) -> None:
        other_workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\Beta",
            when=_dt(7),
        ).workspace_tab
        other_session = self.session_manager.open_session(
            other_workspace.workspace_tab_id,
            when=_dt(8),
        )
        self.scheduler.register_job(
            self.session_a.session_tab_id,
            "workspace-a running",
            when=_dt(9),
        )
        self.scheduler.register_job(
            other_session.session_tab_id,
            "workspace-b queued",
            when=_dt(10),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)

        with patch(
            "app.scheduler._job_list_order_key",
            side_effect=AssertionError("workspace job summary should not sort jobs"),
        ):
            summaries = self.scheduler.summarize_workspace_jobs(
                (
                    other_workspace.workspace_tab_id,
                    self.workspace_tab.workspace_tab_id,
                    "workspace-empty",
                )
            )
            workspace_has_jobs = self.scheduler.workspace_has_jobs(
                self.workspace_tab.workspace_tab_id
            )
            empty_workspace_has_jobs = self.scheduler.workspace_has_jobs(
                "workspace-empty"
            )

        self.assertEqual(
            (
                other_workspace.workspace_tab_id,
                self.workspace_tab.workspace_tab_id,
                "workspace-empty",
            ),
            tuple(summaries),
        )
        self.assertTrue(summaries[self.workspace_tab.workspace_tab_id].has_jobs)
        self.assertTrue(
            summaries[self.workspace_tab.workspace_tab_id].has_running_job
        )
        self.assertTrue(summaries[other_workspace.workspace_tab_id].has_jobs)
        self.assertFalse(summaries[other_workspace.workspace_tab_id].has_running_job)
        self.assertFalse(summaries["workspace-empty"].has_jobs)
        self.assertFalse(summaries["workspace-empty"].has_running_job)
        self.assertTrue(workspace_has_jobs)
        self.assertFalse(empty_workspace_has_jobs)

    def test_s1_p2_s3_registration_keeps_queue_order(self) -> None:
        workspace = self.workspace_manager.open_validated_workspace(
            r"C:\Repo\QueueOrder",
            when=_dt(3),
        ).workspace_tab
        first_session = self.session_manager.open_session(
            workspace.workspace_tab_id,
            when=_dt(4),
        )
        preset_session = self.session_manager.open_preset_session(
            workspace.workspace_tab_id,
            when=_dt(5),
        )
        second_session = self.session_manager.open_session(
            workspace.workspace_tab_id,
            when=_dt(6),
        )

        self.assertEqual(("S1", "P2", "S3"), (
            first_session.display_name,
            preset_session.display_name,
            second_session.display_name,
        ))

        self.scheduler.register_job(first_session.session_tab_id, "first", when=_dt(7))
        self.scheduler.register_job(preset_session.session_tab_id, "preset", when=_dt(8))
        self.scheduler.register_job(second_session.session_tab_id, "second", when=_dt(9))

        workspace_jobs = tuple(
            job
            for job in self.scheduler.list_jobs()
            if job.workspace_tab_id == workspace.workspace_tab_id
        )
        session_names_by_id = {
            tab.session_tab_id: tab.display_name
            for tab in self.session_manager.list_session_tabs(
                workspace_tab_id=workspace.workspace_tab_id
            )
        }

        self.assertEqual(
            ("S1", "P2", "S3"),
            tuple(session_names_by_id[job.session_tab_id] for job in workspace_jobs),
        )
        self.assertEqual(("first", "preset", "second"), tuple(job.prompt for job in workspace_jobs))
        self.assertEqual((1, 2, 3), tuple(job.queue_order for job in workspace_jobs))

    def test_running_session_pending_jobs_keep_registration_order(self) -> None:
        running_job = self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a running",
            when=_dt(3),
        )
        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        self.scheduler.register_job(
            self.session_b.session_tab_id,
            "session-b waiting",
            when=_dt(4),
        )
        self.scheduler.register_job(
            self.session_a.session_tab_id,
            "session-a follow-up",
            when=_dt(5),
        )

        jobs = self.scheduler.list_jobs()

        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(running_job.job_id).status)
        self.assertEqual(
            ("session-a running", "session-b waiting", "session-a follow-up"),
            tuple(job.prompt for job in jobs),
        )
        self.assertEqual((1, 2, 3), tuple(job.queue_order for job in jobs))

    def test_deferred_completion_dispatch_preserves_registration_order(self) -> None:
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
        with self.scheduler.defer_dispatch():
            self.scheduler.complete_running_job(first_job.job_id, when=_dt(6))

        self.assertTrue(self.scheduler.has_pending_dispatch())
        self.assertEqual(JobStatus.COMPLETED, self.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(follow_up_job.job_id).status)

        self.scheduler.dispatch_next_job()

        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(follow_up_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.scheduler.get_job(other_session_job.job_id).status)
        self.assertEqual(
            ("session-a first", "session-b first"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_prioritized_queued_jobs_run_before_older_pending_jobs(self) -> None:
        preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(3),
        )
        generation_job = self.scheduler.register_job(
            preset.session_tab_id,
            "preset work generation",
            when=_dt(4),
        )
        existing_job = self.scheduler.register_job(
            self.session_b.session_tab_id,
            "existing queued",
            when=_dt(5),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        with self.scheduler.defer_dispatch():
            self.scheduler.complete_running_job(generation_job.job_id, when=_dt(6))
        first_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(7),
        )
        second_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(8),
        )
        with self.scheduler.defer_dispatch():
            first_candidate_job = self.scheduler.register_job(
                first_candidate.session_tab_id,
                "candidate one",
                when=_dt(9),
            )
            second_candidate_job = self.scheduler.register_job(
                second_candidate.session_tab_id,
                "candidate two",
                when=_dt(10),
            )
            self.scheduler.prioritize_queued_jobs(
                (first_candidate_job.job_id, second_candidate_job.job_id)
            )

        self.scheduler.dispatch_next_job()

        self.assertEqual(
            (
                "preset work generation",
                "candidate one",
                "candidate two",
                "existing queued",
            ),
            tuple(job.prompt for job in self.scheduler.list_jobs()),
        )
        self.assertEqual(
            JobStatus.RUNNING,
            self.scheduler.get_job(first_candidate_job.job_id).status,
        )
        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(existing_job.job_id).status)
        self.assertEqual(
            ("preset work generation", "candidate one"),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

    def test_prioritized_candidate_commit_jobs_override_deferred_parent_follow_up(self) -> None:
        preset = self.session_manager.open_preset_session(
            self.workspace_tab.workspace_tab_id,
            when=_dt(3),
        )
        generation_job = self.scheduler.register_job(
            preset.session_tab_id,
            "preset work generation",
            when=_dt(4),
        )
        parent_follow_up = self.scheduler.register_job(
            preset.session_tab_id,
            "unexpected parent follow-up",
            when=_dt(5),
        )

        self.scheduler.start_queue(self.workspace_tab.workspace_tab_id)
        with self.scheduler.defer_dispatch():
            self.scheduler.complete_running_job(generation_job.job_id, when=_dt(6))

        first_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(7),
        )
        second_candidate = self.session_manager.open_preset_candidate_session(
            preset.session_tab_id,
            when=_dt(8),
        )
        with self.scheduler.defer_dispatch():
            first_candidate_job = self.scheduler.register_job(
                first_candidate.session_tab_id,
                "candidate one",
                when=_dt(9),
            )
            first_candidate_commit = self.scheduler.register_job(
                first_candidate.session_tab_id,
                "commit candidate one",
                when=_dt(10),
            )
            second_candidate_job = self.scheduler.register_job(
                second_candidate.session_tab_id,
                "candidate two",
                when=_dt(11),
            )
            second_candidate_commit = self.scheduler.register_job(
                second_candidate.session_tab_id,
                "commit candidate two",
                when=_dt(12),
            )
            self.scheduler.prioritize_queued_jobs(
                (
                    first_candidate_job.job_id,
                    first_candidate_commit.job_id,
                    second_candidate_job.job_id,
                    second_candidate_commit.job_id,
                )
            )

        self.scheduler.dispatch_next_job()
        self.scheduler.complete_running_job(first_candidate_job.job_id, when=_dt(13))
        self.scheduler.complete_running_job(first_candidate_commit.job_id, when=_dt(14))
        self.scheduler.complete_running_job(second_candidate_job.job_id, when=_dt(15))

        self.assertEqual(JobStatus.QUEUED, self.scheduler.get_job(parent_follow_up.job_id).status)
        self.assertEqual(
            (
                "preset work generation",
                "candidate one",
                "commit candidate one",
                "candidate two",
                "commit candidate two",
            ),
            tuple(request.prompt for request in self.executor.launched_requests),
        )

