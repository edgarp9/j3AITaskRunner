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


class AppControllerTests(unittest.TestCase):
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

    def test_invalid_executable_path_moves_job_to_waiting_until_retried(self) -> None:
        self.settings = AppSettings(
            executable_path=str(self.root_path / "missing-agent.exe"),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "retry me")

        self.controller.start_queue()

        waiting_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.WAITING_FOR_CONFIGURATION, waiting_job.status)
        self.assertEqual("실행기 경로를 확인하세요.", waiting_job.configuration_wait_reason)

        self.settings = AppSettings(
            executable_path=str(self.executable_path),
        )
        self.runner.prepare("retry me", _Scenario(status=AgentRunStatus.COMPLETED))

        self.controller.retry_waiting_job(job.job_id)

        retried_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.RUNNING, retried_job.status)

    def test_success_updates_completed_session_and_emits_bridge_events(self) -> None:
        self.runner.prepare(
            "success",
            _Scenario(
                status=AgentRunStatus.COMPLETED,
                session_id="thread-success",
                last_message="Final answer",
                stdout_lines=('{"type":"turn.completed"}\n',),
                stderr_lines=("diagnostic only\n",),
            ),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "success")
        self.controller.start_queue()
        self.controller.drain_ui_events()

        self.runner.resolve(job.job_id)
        self._process_background_until(job.job_id, JobStatus.COMPLETED)
        events = self.controller.drain_ui_events()

        completed_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.COMPLETED, completed_job.status)
        queue_state = self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id)
        self.assertEqual(QueueStatus.STOPPED, queue_state.status)
        self.assertEqual(QueueStopReason.ALL_JOBS_COMPLETED, queue_state.last_stop_reason)
        self.assertEqual(
            "thread-success",
            self.controller.session_manager.get_session_tab(self.session_a.session_tab_id).session_id,
        )
        completed_sessions = self.controller.session_manager.list_completed_sessions(
            str(self.workspace_path)
        )
        self.assertEqual(1, len(completed_sessions))
        self.assertEqual("thread-success", completed_sessions[0].session_id)
        self.assertEqual("Final answer", completed_sessions[0].turns[0].response_text)
        session_turns = self.controller.session_manager.list_session_tab_turns(
            self.session_a.session_tab_id
        )
        self.assertEqual(1, len(session_turns))
        self.assertEqual("success", session_turns[0].prompt_text)
        self.assertEqual("Final answer", session_turns[0].response_text)

        self.assertTrue(any(isinstance(event, SessionIdConfirmedEvent) for event in events))
        self.assertTrue(
            any(
                isinstance(event, LogAppendedEvent)
                and event.stream_name == "progress"
                and "세션 시작" in event.line
                for event in events
            )
        )
        self.assertTrue(
            any(
                isinstance(event, LogAppendedEvent)
                and event.stream_name == "progress"
                and "응답 완료" in event.line
                for event in events
            )
        )
        self.assertTrue(any(isinstance(event, CompletedSessionUpdatedEvent) for event in events))
        self.assertTrue(
            any(
                isinstance(event, JobStatusChangedEvent)
                and event.current_status == JobStatus.COMPLETED
                for event in events
            )
        )

    def test_started_job_records_prompt_in_session_history_before_completion(self) -> None:
        self.runner.prepare("start history", _Scenario(status=AgentRunStatus.COMPLETED))

        job = self.controller.submit_job(self.session_a.session_tab_id, "start history")
        self.controller.start_queue()

        turns = self.controller.session_manager.list_session_tab_turns(self.session_a.session_tab_id)
        self.assertEqual(1, len(turns))
        self.assertEqual(job.job_id, turns[0].job_id)
        self.assertEqual("start history", turns[0].prompt_text)
        self.assertIsNone(turns[0].response_text)
        self.assertIsNone(turns[0].completed_at)

    def test_conflicting_session_id_does_not_block_completion_or_queue(self) -> None:
        self.controller.session_manager.assign_session_id(
            self.session_b.session_tab_id,
            "thread-conflict",
        )
        self.runner.prepare(
            "conflicting session id",
            _Scenario(
                status=AgentRunStatus.COMPLETED,
                session_id="thread-conflict",
                last_message="done despite conflict",
            ),
        )
        self.runner.prepare("next job", _Scenario(status=AgentRunStatus.COMPLETED))

        first_job = self.controller.submit_job(
            self.session_a.session_tab_id,
            "conflicting session id",
        )
        second_job = self.controller.submit_job(self.session_b.session_tab_id, "next job")
        self.controller.start_queue()
        self.controller.drain_ui_events()

        with self.assertLogs("app.use_cases", level="WARNING") as captured_logs:
            self.runner.resolve(first_job.job_id)
            self._process_background_until(second_job.job_id, JobStatus.RUNNING)
        events = self.controller.drain_ui_events()

        completed_job = self.controller.scheduler.get_job(first_job.job_id)
        self.assertEqual(JobStatus.COMPLETED, completed_job.status)
        self.assertEqual(
            2,
            sum("Ignoring conflicting session id for job." in line for line in captured_logs.output),
        )
        self.assertIsNone(
            self.controller.session_manager.get_session_tab(self.session_a.session_tab_id).session_id
        )
        self.assertEqual(
            "thread-conflict",
            self.controller.session_manager.get_session_tab(self.session_b.session_tab_id).session_id,
        )
        self.assertEqual(
            JobStatus.RUNNING,
            self.controller.scheduler.get_job(second_job.job_id).status,
        )
        self.assertFalse(
            any(
                isinstance(event, SessionIdConfirmedEvent)
                and event.job_id == first_job.job_id
                for event in events
            )
        )
        self.assertTrue(
            any(
                isinstance(event, JobStatusChangedEvent)
                and event.job_id == first_job.job_id
                and event.current_status == JobStatus.COMPLETED
                for event in events
            )
        )

    def test_force_fresh_session_job_does_not_resume_or_replace_parent_session_id(self) -> None:
        self.controller.session_manager.assign_session_id(
            self.session_a.session_tab_id,
            "thread-parent",
        )
        self.runner.prepare(
            "fresh internal",
            _Scenario(
                status=AgentRunStatus.COMPLETED,
                session_id="thread-fresh",
                last_message="fresh response",
            ),
        )

        job = self.controller.submit_job(
            self.session_a.session_tab_id,
            "fresh internal",
            force_fresh_session=True,
        )
        self.controller.start_queue()
        self.controller.drain_ui_events()

        self.assertEqual(1, len(self.runner.launched_requests))
        self.assertIsNone(self.runner.launched_requests[0].session_id)

        self.runner.resolve(job.job_id)
        self._process_background_until(job.job_id, JobStatus.COMPLETED)
        events = self.controller.drain_ui_events()

        self.assertEqual(
            "thread-parent",
            self.controller.session_manager.get_session_tab(
                self.session_a.session_tab_id
            ).session_id,
        )
        self.assertFalse(
            any(
                isinstance(event, SessionIdConfirmedEvent)
                and event.job_id == job.job_id
                for event in events
            )
        )

    def test_stop_queue_preserves_completed_result_when_cancel_races_with_completion(self) -> None:
        self.runner.prepare(
            "stop-race",
            _Scenario(
                status=AgentRunStatus.COMPLETED,
                session_id="thread-stop-race",
                last_message="Completed despite stop",
                cancel_ignored=True,
            ),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "stop-race")
        started_state = self.controller.start_queue()
        self.assertEqual(QueueStatus.STARTED, started_state.status)
        self.controller.drain_ui_events()

        stopped_state = self.controller.stop_queue(self.workspace_tab.workspace_tab_id)

        self.assertEqual(QueueStatus.STOPPED, stopped_state.status)
        self.assertEqual(
            job.job_id,
            self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).running_job_id,
        )
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(job.job_id).status)

        self.runner.resolve(job.job_id)
        self._process_background_until(job.job_id, JobStatus.COMPLETED)
        events = self.controller.drain_ui_events()

        completed_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.COMPLETED, completed_job.status)
        queue_state = self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id)
        self.assertEqual(QueueStatus.STOPPED, queue_state.status)
        self.assertIsNone(queue_state.running_job_id)
        self.assertEqual(QueueStopReason.USER_STOPPED, queue_state.last_stop_reason)
        self.assertEqual(
            "thread-stop-race",
            self.controller.session_manager.get_session_tab(self.session_a.session_tab_id).session_id,
        )
        completed_sessions = self.controller.session_manager.list_completed_sessions(
            str(self.workspace_path)
        )
        self.assertEqual(1, len(completed_sessions))
        self.assertEqual("Completed despite stop", completed_sessions[0].turns[0].response_text)
        self.assertTrue(any(isinstance(event, CompletedSessionUpdatedEvent) for event in events))

    def test_failed_execution_is_recorded_as_failed(self) -> None:
        self.runner.prepare(
            "failure",
            _Scenario(
                status=AgentRunStatus.FAILED,
                session_id="thread-failure",
                failure_reason="tool execution failed",
                exit_code=3,
            ),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "failure")
        self.controller.start_queue()

        self.runner.resolve(job.job_id)
        self._process_background_until(job.job_id, JobStatus.FAILED)

        failed_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.FAILED, failed_job.status)
        self.assertNotEqual(JobStatus.CANCELED, failed_job.status)
        self.assertTrue(failed_job.user_message)
        turns = self.controller.session_manager.list_session_tab_turns(self.session_a.session_tab_id)
        self.assertEqual(1, len(turns))
        self.assertIsNone(turns[0].response_text)
        self.assertIsNotNone(turns[0].completed_at)

    def test_execution_timeout_fails_job_and_dispatches_next_queued_job(self) -> None:
        self.runner.prepare(
            "timeout",
            _Scenario(
                status=AgentRunStatus.FAILED,
                session_id="thread-timeout",
                last_message='{"candidates": []}',
                failure_reason="시간 제한 초과: 전체 실행 제한(1분)을 초과했습니다.",
                exit_code=-15,
            ),
        )
        self.runner.prepare("after timeout", _Scenario(status=AgentRunStatus.COMPLETED))
        timed_out_job = self.controller.submit_job(self.session_a.session_tab_id, "timeout")
        next_job = self.controller.submit_job(self.session_b.session_tab_id, "after timeout")
        self.controller.start_queue()
        self.controller.drain_ui_events()

        self.runner.resolve(timed_out_job.job_id)
        self._process_background_until(next_job.job_id, JobStatus.RUNNING)
        events = self.controller.drain_ui_events()

        failed_job = self.controller.scheduler.get_job(timed_out_job.job_id)
        queue_state = self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id)
        captured_events = [
            event
            for event in events
            if isinstance(event, JobExecutionResultCapturedEvent)
            and event.job_id == timed_out_job.job_id
        ]
        self.assertEqual(JobStatus.FAILED, failed_job.status)
        self.assertEqual("실행 시간이 초과되었습니다.", failed_job.user_message)
        self.assertNotEqual("작업을 취소했습니다.", failed_job.user_message)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(next_job.job_id).status)
        self.assertEqual(next_job.job_id, queue_state.running_job_id)
        self.assertEqual(("timeout", "after timeout"), tuple(request.prompt for request in self.runner.launched_requests))
        self.assertEqual(1, len(captured_events))
        self.assertEqual(AgentRunStatus.FAILED, captured_events[0].status)
        self.assertFalse(
            any(
                isinstance(event, CompletedSessionUpdatedEvent)
                and event.job_id == timed_out_job.job_id
                for event in events
            )
        )

    def test_execution_timeout_without_follow_up_clears_slot_and_stops_queue(self) -> None:
        self.runner.prepare(
            "timeout without follow-up",
            _Scenario(
                status=AgentRunStatus.FAILED,
                session_id="thread-timeout-alone",
                failure_reason="시간 제한 초과: 전체 실행 제한(1분)을 초과했습니다.",
                exit_code=-15,
            ),
        )
        timed_out_job = self.controller.submit_job(
            self.session_a.session_tab_id,
            "timeout without follow-up",
        )
        self.controller.start_queue()
        self.controller.drain_ui_events()

        self.runner.resolve(timed_out_job.job_id)
        self._process_background_until(timed_out_job.job_id, JobStatus.FAILED)

        failed_job = self.controller.scheduler.get_job(timed_out_job.job_id)
        queue_state = self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id)
        self.assertEqual(JobStatus.FAILED, failed_job.status)
        self.assertEqual("실행 시간이 초과되었습니다.", failed_job.user_message)
        self.assertIsNone(queue_state.running_job_id)
        self.assertEqual(QueueStatus.STOPPED, queue_state.status)
        self.assertEqual(QueueStopReason.ALL_JOBS_COMPLETED, queue_state.last_stop_reason)

    def test_inactivity_timeout_message_is_distinct_from_execution_timeout_and_cancel(self) -> None:
        self.runner.prepare(
            "inactivity timeout",
            _Scenario(
                status=AgentRunStatus.FAILED,
                failure_reason="시간 제한 초과: 출력 무활동 제한(30분)을 초과했습니다.",
                exit_code=-15,
            ),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "inactivity timeout")
        self.controller.start_queue()

        self.runner.resolve(job.job_id)
        self._process_background_until(job.job_id, JobStatus.FAILED)

        failed_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual("진행 로그가 없어 실행을 중단했습니다.", failed_job.user_message)
        self.assertNotEqual("실행 시간이 초과되었습니다.", failed_job.user_message)
        self.assertNotEqual("작업을 취소했습니다.", failed_job.user_message)

    def test_completed_timeout_result_is_captured_as_failed_for_follow_up_flows(self) -> None:
        self.runner.prepare(
            "completed timeout marker",
            _Scenario(
                status=AgentRunStatus.COMPLETED,
                last_message='{"candidates": [{"id": "1"}]}',
                failure_reason="시간 제한 초과: 전체 실행 제한(1분)을 초과했습니다.",
                exit_code=0,
            ),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "completed timeout marker")
        self.controller.start_queue()
        self.controller.drain_ui_events()

        self.runner.resolve(job.job_id)
        self._process_background_until(job.job_id, JobStatus.FAILED)
        events = self.controller.drain_ui_events()

        captured_events = [
            event
            for event in events
            if isinstance(event, JobExecutionResultCapturedEvent)
            and event.job_id == job.job_id
        ]
        failed_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.FAILED, failed_job.status)
        self.assertEqual("실행 시간이 초과되었습니다.", failed_job.user_message)
        self.assertEqual(1, len(captured_events))
        self.assertEqual(AgentRunStatus.FAILED, captured_events[0].status)

    def test_user_cancel_marks_job_canceled_not_failed(self) -> None:
        self.runner.prepare("cancel me", _Scenario(status=AgentRunStatus.COMPLETED))
        self.runner.prepare("after cancel", _Scenario(status=AgentRunStatus.COMPLETED))
        job = self.controller.submit_job(self.session_a.session_tab_id, "cancel me")
        next_job = self.controller.submit_job(self.session_b.session_tab_id, "after cancel")
        self.controller.start_queue()

        self.controller.cancel_running_job(job.job_id)

        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(job.job_id).status)
        self.assertEqual(JobStatus.QUEUED, self.controller.scheduler.get_job(next_job.job_id).status)
        self.assertEqual(("cancel me",), tuple(request.prompt for request in self.runner.launched_requests))

        self._process_background_until(job.job_id, JobStatus.CANCELED)

        canceled_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.CANCELED, canceled_job.status)
        self.assertEqual("작업을 취소했습니다.", canceled_job.user_message)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(next_job.job_id).status)

    def test_close_session_cancels_running_job_and_stops_queue(self) -> None:
        self.runner.prepare("close me", _Scenario(status=AgentRunStatus.COMPLETED))
        job = self.controller.submit_job(self.session_a.session_tab_id, "close me")
        self.controller.start_queue()

        result = self.controller.close_session(self.session_a.session_tab_id)

        canceled_job = self.controller.scheduler.get_job(job.job_id)
        self.assertTrue(result.queue_stopped)
        self.assertEqual(job.job_id, result.canceled_job.job_id if result.canceled_job else None)
        self.assertEqual(JobStatus.RUNNING, canceled_job.status)
        self.assertEqual(
            QueueStopReason.RUNNING_TAB_CLOSED,
            self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).last_stop_reason,
        )
        self.assertEqual(TabOpenState.CLOSED, result.session_tab.open_state)

        self._process_background_until(job.job_id, JobStatus.CANCELED)

        canceled_job = self.controller.scheduler.get_job(job.job_id)
        self.assertEqual(JobStatus.CANCELED, canceled_job.status)
        self.assertEqual("탭 닫기로 취소했습니다.", canceled_job.user_message)

    def test_close_session_cancels_selected_running_job_when_multiple_workspaces_run(self) -> None:
        other_workspace_path = self.root_path / "workspace-b"
        other_workspace_path.mkdir()
        other_workspace = self.controller.open_workspace(str(other_workspace_path)).workspace_tab
        other_session = self.controller.open_session(other_workspace.workspace_tab_id)

        self.runner.prepare("workspace-a running", _Scenario(status=AgentRunStatus.COMPLETED))
        self.runner.prepare("workspace-b running", _Scenario(status=AgentRunStatus.COMPLETED))

        first_job = self.controller.submit_job(
            self.session_a.session_tab_id,
            "workspace-a running",
        )
        second_job = self.controller.submit_job(
            other_session.session_tab_id,
            "workspace-b running",
        )
        self.controller.start_queue(self.workspace_tab.workspace_tab_id)
        self.controller.start_queue(other_workspace.workspace_tab_id)
        self.runner.wait_until_launched(first_job.job_id)
        self.runner.wait_until_launched(second_job.job_id)

        result = self.controller.close_session(other_session.session_tab_id)

        self.assertTrue(result.queue_stopped)
        self.assertEqual(second_job.job_id, result.canceled_job.job_id if result.canceled_job else None)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(second_job.job_id).status)
        self.assertEqual(
            QueueStatus.STARTED,
            self.controller.scheduler.get_queue_state(self.workspace_tab.workspace_tab_id).status,
        )
        self.assertEqual(
            QueueStatus.STOPPED,
            self.controller.scheduler.get_queue_state(other_workspace.workspace_tab_id).status,
        )

        self._process_background_until(second_job.job_id, JobStatus.CANCELED)

        self.assertEqual(JobStatus.RUNNING, self.controller.scheduler.get_job(first_job.job_id).status)
        self.assertEqual(JobStatus.CANCELED, self.controller.scheduler.get_job(second_job.job_id).status)
        self.controller.stop_queue(self.workspace_tab.workspace_tab_id)
        self._process_background_until(first_job.job_id, JobStatus.CANCELED)

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
        self.assertIn("세션 시작", first_events[0].line)
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


@dataclass(slots=True, frozen=True)
class _Scenario:
    status: AgentRunStatus
    session_id: str | None = None
    last_message: str | None = None
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    failure_reason: str | None = None
    exit_code: int | None = 0
    cancel_ignored: bool = False


class _FakeRunningHandle:
    def __init__(
        self,
        *,
        job_id: str,
        artifacts_root: Path,
        scenario: _Scenario,
        on_stdout_line,
        on_stderr_line,
        on_json_event,
    ) -> None:
        self.handle_id = job_id
        self._artifacts_root = artifacts_root
        self._scenario = scenario
        self._on_stdout_line = on_stdout_line
        self._on_stderr_line = on_stderr_line
        self._on_json_event = on_json_event
        self._result_ready = threading.Event()
        self._result: AgentRunResult | None = None

    def wait(self, timeout: float | None = None) -> AgentRunResult:
        if not self._result_ready.wait(timeout):
            raise TimeoutError(f"Result not ready for {self.handle_id}")
        assert self._result is not None
        return self._result

    def resolve(self) -> None:
        if self._result_ready.is_set():
            return

        if self._scenario.session_id and self._on_json_event is not None:
            self._on_json_event(
                AgentStreamEvent(
                    line_number=1,
                    event_type="thread.started",
                    payload={"type": "thread.started", "thread_id": self._scenario.session_id},
                    thread_id=self._scenario.session_id,
                )
            )
        if self._on_json_event is not None and self._scenario.stdout_lines:
            for line_number, line in enumerate(self._scenario.stdout_lines, start=2):
                event = _agent_stream_event_from_json_line(line, line_number=line_number)
                if event is not None:
                    self._on_json_event(event)
        if self._on_stderr_line is not None:
            for line in self._scenario.stderr_lines:
                self._on_stderr_line(line)

        artifacts = _create_artifacts(self._artifacts_root, self.handle_id)
        if self._scenario.last_message is not None:
            artifacts.last_message_path.write_text(self._scenario.last_message, encoding="utf-8")

        self._result = AgentRunResult(
            status=self._scenario.status,
            command=("fake-agent", self.handle_id),
            artifacts=artifacts,
            exit_code=self._scenario.exit_code,
            session_id=self._scenario.session_id,
            last_message=self._scenario.last_message,
            failure_reason=self._scenario.failure_reason,
        )
        self._result_ready.set()

    def cancel(self) -> None:
        if self._result_ready.is_set():
            return

        if self._scenario.cancel_ignored:
            return

        self._scenario = _Scenario(
            status=AgentRunStatus.CANCELED,
            session_id=self._scenario.session_id,
            exit_code=-15,
            cancel_ignored=self._scenario.cancel_ignored,
        )
        self.resolve()


class _FakeBackgroundRunner:
    def __init__(self, artifacts_root: Path) -> None:
        self._artifacts_root = artifacts_root
        self._prepared_scenarios: dict[str, _Scenario] = {}
        self._handles: dict[str, _FakeRunningHandle] = {}
        self._handle_ready: dict[str, threading.Event] = {}
        self.launched_requests: list[JobExecutionRequest] = []

    def prepare(self, prompt: str, scenario: _Scenario) -> None:
        self._prepared_scenarios[prompt] = scenario

    def validate(self, request: JobExecutionRequest) -> str | None:
        executable_path = Path(request.operational_settings.executable_path or "")
        if not request.operational_settings.executable_path or not executable_path.is_file():
            return "실행기 경로를 확인하세요."

        workspace_path = Path(request.workspace_path)
        if not workspace_path.is_dir():
            return "워크스페이스 경로를 확인하세요."

        return None

    def launch(
        self,
        request: JobExecutionRequest,
        *,
        on_stdout_line=None,
        on_stderr_line=None,
        on_json_event=None,
        on_handle_created=None,
    ) -> _FakeRunningHandle:
        scenario = self._prepared_scenarios.get(
            request.prompt,
            _Scenario(status=AgentRunStatus.COMPLETED),
        )
        handle = _FakeRunningHandle(
            job_id=request.job_id,
            artifacts_root=self._artifacts_root,
            scenario=scenario,
            on_stdout_line=on_stdout_line,
            on_stderr_line=on_stderr_line,
            on_json_event=on_json_event,
        )
        self._handles[request.job_id] = handle
        self._handle_ready.setdefault(request.job_id, threading.Event()).set()
        self.launched_requests.append(request)
        if on_handle_created is not None:
            on_handle_created(handle)
        return handle

    def cancel(self, handle: _FakeRunningHandle) -> None:
        handle.cancel()

    def resolve(self, job_id: str) -> None:
        self.wait_until_launched(job_id)
        self._handles[job_id].resolve()

    def wait_until_launched(self, job_id: str, timeout: float = 1.0) -> None:
        if not self._handle_ready.setdefault(job_id, threading.Event()).wait(timeout):
            raise AssertionError(f"Timed out waiting for job {job_id} launch.")


def _create_artifacts(root: Path, job_id: str) -> ExecutionArtifactPaths:
    artifact_dir = root / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = artifact_dir / "prompt.txt"
    stdout_jsonl_path = artifact_dir / "stdout.jsonl"
    stderr_log_path = artifact_dir / "stderr.log"
    last_message_path = artifact_dir / "last_message.txt"
    launch_metadata_path = artifact_dir / "launch.json"
    for path in (prompt_path, stdout_jsonl_path, stderr_log_path, launch_metadata_path):
        path.touch()
    return ExecutionArtifactPaths(
        root_dir=artifact_dir,
        prompt_path=prompt_path,
        stdout_jsonl_path=stdout_jsonl_path,
        stderr_log_path=stderr_log_path,
        last_message_path=last_message_path,
        launch_metadata_path=launch_metadata_path,
    )


def _agent_stream_event_from_json_line(
    raw_line: str,
    *,
    line_number: int,
) -> AgentStreamEvent | None:
    payload = json.loads(raw_line)
    event_type = str(payload.get("type") or "")
    if event_type not in {"thread.started", "turn.completed", "turn.failed", "error"}:
        return None
    return AgentStreamEvent(
        line_number=line_number,
        event_type=event_type,
        payload=payload,
        thread_id=payload.get("thread_id"),
        message=payload.get("message"),
        raw_line=raw_line,
    )


if __name__ == "__main__":
    unittest.main()

