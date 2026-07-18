from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from app.controller import (
    AppController,
    CompletedSessionUpdatedEvent,
    JobExecutionResultCapturedEvent,
    JobStatusChangedEvent,
    LogAppendedEvent,
    SessionIdConfirmedEvent,
)
from app.runtime import AppRuntime
from app.scheduler import JobExecutionRequest
from domain.models import AppSettings, JobStatus, QueueStatus, QueueStopReason, TabOpenState
from infra.process_runner import (
    AgentRunResult,
    AgentRunStatus,
    AgentStreamEvent,
    ExecutionArtifactPaths,
)

from tests._controller_helpers import *


class AppControllerQueueLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.workspace_path = self.root_path / "workspace"
        self.workspace_path.mkdir()
        self.executable_path = self.root_path / "agent.exe"
        self.executable_path.write_text("", encoding="utf-8")
        self.artifacts_root = self.root_path / "artifacts"
        self.settings = AppSettings(
            executable_path=str(self.executable_path),
        )
        self.runner = _FakeBackgroundRunner(self.artifacts_root)
        self.controller = AppController(
            runner=self.runner,
            settings_provider=lambda: self.settings,
        )
        self.workspace_tab = self.controller.open_workspace(str(self.workspace_path)).workspace_tab
        self.session_a = self.controller.open_session(self.workspace_tab.workspace_tab_id)
        self.session_b = self.controller.open_session(self.workspace_tab.workspace_tab_id)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_close_session_completed_result_after_cancel_is_recorded_as_canceled(self) -> None:
        self.runner.prepare(
            "close completed race",
            _Scenario(
                status=AgentRunStatus.COMPLETED,
                session_id="thread-closed-race",
                last_message="Completed after tab close",
                cancel_ignored=True,
            ),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "close completed race")
        self.controller.start_queue()
        self.controller.close_session(self.session_a.session_tab_id)
        self.controller.drain_ui_events()

        self.runner.resolve(job.job_id)
        self._process_background_until(job.job_id, JobStatus.CANCELED)
        events = self.controller.drain_ui_events()

        canceled_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.CANCELED, canceled_job.status)
        self.assertEqual("탭 닫기로 취소했습니다.", canceled_job.user_message)
        self.assertEqual(
            (),
            self.controller.session_manager.list_completed_sessions(str(self.workspace_path)),
        )
        self.assertFalse(any(isinstance(event, CompletedSessionUpdatedEvent) for event in events))

    def test_close_session_removes_waiting_for_configuration_job(self) -> None:
        self.settings = AppSettings(
            executable_path=str(self.root_path / "missing-agent.exe"),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "close waiting job")
        self.controller.start_queue(self.workspace_tab.workspace_tab_id)
        self.assertEqual(
            JobStatus.WAITING_FOR_CONFIGURATION,
            self.controller.scheduler.get_job(job.job_id).status,
        )

        result = self.controller.close_session(self.session_a.session_tab_id)

        self.assertEqual(TabOpenState.CLOSED, result.session_tab.open_state)
        self.assertEqual(1, result.removed_queued_job_count)
        self.assertTrue(result.queue_stopped)
        queue_state = self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id)
        self.assertEqual(QueueStatus.STOPPED, queue_state.status)
        self.assertEqual(QueueStopReason.USER_STOPPED, queue_state.last_stop_reason)
        with self.assertRaises(KeyError):
            self.controller.scheduler.get_job(job.job_id)

    def test_close_workspace_removes_pending_jobs_for_closed_sessions(self) -> None:
        other_workspace_path = self.root_path / "workspace-b"
        other_workspace_path.mkdir()
        other_workspace = self.controller.open_workspace(str(other_workspace_path)).workspace_tab
        other_session = self.controller.open_session(other_workspace.workspace_tab_id)

        self.settings = AppSettings(
            executable_path=str(self.root_path / "missing-agent.exe"),
        )
        first_job = self.controller.submit_job(self.session_a.session_tab_id, "workspace-a first")
        self.controller.start_queue(self.workspace_tab.workspace_tab_id)
        self.assertEqual(
            JobStatus.WAITING_FOR_CONFIGURATION,
            self.controller.scheduler.get_job(first_job.job_id).status,
        )
        self.controller.stop_queue(self.workspace_tab.workspace_tab_id)

        self.settings = AppSettings(
            executable_path=str(self.executable_path),
        )
        second_job = self.controller.submit_job(self.session_b.session_tab_id, "workspace-a second")
        other_workspace_job = self.controller.submit_job(
            other_session.session_tab_id,
            "workspace-b queued",
        )
        self.assertEqual(JobStatus.QUEUED, self.controller.scheduler.get_job(second_job.job_id).status)

        result = self.controller.close_workspace(self.workspace_tab.workspace_tab_id)

        self.assertEqual(TabOpenState.CLOSED, result.workspace_tab.open_state)
        self.assertEqual(
            (self.session_a.session_tab_id, self.session_b.session_tab_id),
            tuple(session.session_tab_id for session in result.closed_sessions),
        )
        self.assertEqual(2, result.removed_queued_job_count)
        self.assertEqual(
            {other_workspace_job.job_id},
            {job.job_id for job in self.controller.scheduler.list_jobs()},
        )
        self.assertEqual(
            JobStatus.QUEUED,
            self.controller.scheduler.get_job(other_workspace_job.job_id).status,
        )

    def test_retry_waiting_jobs_skips_closed_workspace_jobs(self) -> None:
        self.settings = AppSettings(
            executable_path=str(self.root_path / "missing-agent.exe"),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "retry me later")
        self.controller.start_queue(self.workspace_tab.workspace_tab_id)
        self.assertEqual(
            JobStatus.WAITING_FOR_CONFIGURATION,
            self.controller.scheduler.get_job(job.job_id).status,
        )

        runtime = AppRuntime.__new__(AppRuntime)
        runtime._controller = self.controller

        self.controller.workspace_manager.close_workspace(self.workspace_tab.workspace_tab_id)

        retried_job_ids = runtime.retry_waiting_jobs(sync_events=False)

        self.assertEqual((), retried_job_ids)
        self.assertEqual(
            JobStatus.WAITING_FOR_CONFIGURATION,
            self.controller.scheduler.get_job(job.job_id).status,
        )

    def test_close_workspace_reissues_cancel_for_running_job_after_queue_already_stopped(self) -> None:
        self.runner.prepare("workspace-stop-race", _Scenario(status=AgentRunStatus.COMPLETED))
        job = self.controller.submit_job(self.session_a.session_tab_id, "workspace-stop-race")
        self.controller.start_queue()
        self.controller.drain_ui_events()

        stopped_state = self.controller.stop_queue(self.workspace_tab.workspace_tab_id)

        self.assertEqual(QueueStatus.STOPPED, stopped_state.status)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(job.job_id).status)

        result = self.controller.close_workspace(self.workspace_tab.workspace_tab_id)

        self.assertTrue(result.queue_stopped)
        self.assertEqual(job.job_id, result.canceled_job.job_id if result.canceled_job else None)
        self.assertEqual(TabOpenState.CLOSED, result.workspace_tab.open_state)
        self.assertEqual(
            QueueStopReason.RUNNING_TAB_CLOSED,
            self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).last_stop_reason,
        )

        self._process_background_until(job.job_id, JobStatus.CANCELED)

        canceled_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.CANCELED, canceled_job.status)
        self.assertEqual("탭 닫기로 취소했습니다.", canceled_job.user_message)

    def test_stopping_one_workspace_queue_keeps_other_workspace_running(self) -> None:
        other_workspace_path = self.root_path / "workspace-b"
        other_workspace_path.mkdir()
        other_workspace = self.controller.open_workspace(str(other_workspace_path)).workspace_tab
        other_session = self.controller.open_session(other_workspace.workspace_tab_id)

        self.runner.prepare("workspace-a", _Scenario(status=AgentRunStatus.COMPLETED))
        self.runner.prepare("workspace-b", _Scenario(status=AgentRunStatus.COMPLETED))

        first_job = self.controller.submit_job(self.session_a.session_tab_id, "workspace-a")
        second_job = self.controller.submit_job(other_session.session_tab_id, "workspace-b")

        self.controller.start_queue(self.workspace_tab.workspace_tab_id)
        self.controller.start_queue(other_workspace.workspace_tab_id)
        self.runner.wait_until_launched(second_job.job_id)
        self.controller.stop_queue(self.workspace_tab.workspace_tab_id)

        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            QueueStatus.STOPPED,
            self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).status,
        )
        self.assertEqual(
            QueueStatus.STARTED,
            self.controller.scheduler.get_queue_state(other_workspace.workspace_tab_id).status,
        )

        self._process_background_until(first_job.job_id, JobStatus.CANCELED)

        self.assertEqual(JobStatus.CANCELED, self.controller.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            ("workspace-a", "workspace-b"),
            tuple(request.prompt for request in self.runner.launched_requests[:2]),
        )

    def test_global_oldest_runs_before_same_session_follow_up_after_completion(self) -> None:
        self.runner.prepare(
            "session-a first",
            _Scenario(
                status=AgentRunStatus.COMPLETED,
                session_id="thread-a",
                last_message="done",
            ),
        )
        self.runner.prepare("session-b waiting", _Scenario(status=AgentRunStatus.COMPLETED))
        self.runner.prepare("session-a second", _Scenario(status=AgentRunStatus.COMPLETED))

        first_job = self.controller.submit_job(self.session_a.session_tab_id, "session-a first")
        other_session_job = self.controller.submit_job(self.session_b.session_tab_id, "session-b waiting")
        follow_up_job = self.controller.submit_job(self.session_a.session_tab_id, "session-a second")
        self.controller.start_queue()
        self.controller.drain_ui_events()

        self.runner.resolve(first_job.job_id)
        self._process_background_until(other_session_job.job_id, JobStatus.RUNNING)
        self.runner.wait_until_launched(other_session_job.job_id)

        self.assertEqual(JobStatus.COMPLETED, self.controller.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.QUEUED, self.controller.scheduler.get_job(follow_up_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(other_session_job.job_id).status)
        self.assertEqual(
            ("session-a first", "session-b waiting"),
            tuple(request.prompt for request in self.runner.launched_requests[:2]),
        )

    def test_immediate_job_runs_while_workspace_queue_job_is_running(self) -> None:
        self.runner.prepare("queue job", _Scenario(status=AgentRunStatus.COMPLETED))
        self.runner.prepare("run now", _Scenario(status=AgentRunStatus.COMPLETED))

        queue_job = self.controller.submit_job(self.session_a.session_tab_id, "queue job")
        self.controller.start_queue()
        self.controller.drain_ui_events()
        self.runner.wait_until_launched(queue_job.job_id)

        immediate_job = self.controller.submit_immediate_job(
            self.session_b.session_tab_id,
            "run now",
        )
        self.controller.drain_ui_events()

        self.runner.wait_until_launched(immediate_job.job_id)
        self.assertEqual(
            JobStatus.RUNNING,
            self.controller.scheduler.get_job(queue_job.job_id).status,
        )
        self.assertEqual(
            JobStatus.RUNNING,
            self.controller.scheduler.get_job(immediate_job.job_id).status,
        )
        self.assertEqual(
            queue_job.job_id,
            self.controller.scheduler.get_queue_state(
                self.workspace_tab.workspace_tab_id
            ).running_job_id,
        )
        self.assertEqual(
            ("queue job", "run now"),
            tuple(request.prompt for request in self.runner.launched_requests[:2]),
        )

    def test_immediate_job_rejects_session_with_pending_job(self) -> None:
        self.controller.submit_job(self.session_a.session_tab_id, "queued job")

        with self.assertRaisesRegex(ValueError, "queued, waiting, or running"):
            self.controller.submit_immediate_job(
                self.session_a.session_tab_id,
                "run now",
            )

    def test_process_background_events_max_items_keeps_worker_event_order(self) -> None:
        self.runner.prepare(
            "bounded",
            _Scenario(
                status=AgentRunStatus.COMPLETED,
                session_id="thread-bounded",
                last_message="done",
            ),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "bounded")
        self.controller.start_queue()
        self.controller.drain_ui_events()

        self.runner.resolve(job.job_id)

        self.assertEqual(1, self.controller.process_background_events(max_items=1))
        first_events = self.controller.drain_ui_events()
        self.assertEqual(1, len(first_events))
        self.assertIsInstance(first_events[0], LogAppendedEvent)
        self.assertIn("thread.started", first_events[0].line)
        self.assertIn("thread-bounded", first_events[0].line)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(job.job_id).status)

        self.assertEqual(1, self.controller.process_background_events(max_items=1))
        second_events = self.controller.drain_ui_events()
        self.assertEqual(1, len(second_events))
        self.assertIsInstance(second_events[0], SessionIdConfirmedEvent)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(job.job_id).status)

        processed = 0
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and processed == 0:
            processed = self.controller.process_background_events(max_items=1)
            if processed == 0:
                time.sleep(0.01)
        self.assertEqual(1, processed)
        completion_events = self.controller.drain_ui_events()
        self.assertTrue(
            any(
                isinstance(event, JobStatusChangedEvent)
                and event.current_status == JobStatus.COMPLETED
                for event in completion_events
            )
        )
        self.assertEqual(JobStatus.COMPLETED, self.controller.scheduler.get_job(job.job_id).status)

    def _process_background_until(self, job_id: str, status: JobStatus) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self.controller.process_background_events()
            if self.controller.scheduler.get_job(job_id).status == status:
                return
            time.sleep(0.01)
        self.fail(f"Timed out waiting for job {job_id} to reach status {status}.")

