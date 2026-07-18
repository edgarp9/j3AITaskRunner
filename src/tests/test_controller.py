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
                and "thread.started" in event.line
                and "thread-success" in event.line
                for event in events
            )
        )
        self.assertTrue(
            any(
                isinstance(event, LogAppendedEvent)
                and event.stream_name == "progress"
                and "turn.completed" in event.line
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
        self.assertEqual("tool execution failed", turns[0].error_text)
        self.assertIsNotNone(turns[0].completed_at)

    def test_failed_execution_records_nested_error_event_in_session_history(self) -> None:
        error_message = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": (
                        "The following tools cannot be used with reasoning.effort "
                        "'minimal': web_search."
                    ),
                    "param": "tools",
                },
                "status": 400,
            },
            indent=2,
        )
        self.runner.prepare(
            "provider error",
            _Scenario(
                status=AgentRunStatus.FAILED,
                session_id="thread-error",
                failure_reason=error_message,
                stdout_lines=(
                    '{"type":"error","message":"request failed"}\n',
                    '{"type":"turn.failed","error":{"message":"request failed"}}\n',
                ),
                exit_code=1,
            ),
        )
        job = self.controller.submit_job(self.session_a.session_tab_id, "provider error")
        self.controller.start_queue()

        self.runner.resolve(job.job_id)
        self._process_background_until(job.job_id, JobStatus.FAILED)

        turns = self.controller.session_manager.list_session_tab_turns(
            self.session_a.session_tab_id
        )
        self.assertEqual(1, len(turns))
        self.assertIsNone(turns[0].response_text)
        self.assertEqual(error_message, turns[0].error_text)

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

    def _process_background_until(self, job_id: str, status: JobStatus) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self.controller.process_background_events()
            if self.controller.scheduler.get_job(job_id).status == status:
                return
            time.sleep(0.01)
        self.fail(f"Timed out waiting for job {job_id} to reach status {status}.")



















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

