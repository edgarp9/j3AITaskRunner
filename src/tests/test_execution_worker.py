from __future__ import annotations

import queue
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.execution_worker import (
    ExecutionCompletedEvent,
    ExecutionLogEvent,
    ExecutionSessionIdEvent,
    ExecutionWorker,
)
from app.scheduler import JobExecutionRequest
from domain import AppSettings
from infra.process_runner import (
    AgentRunResult,
    AgentRunStatus,
    AgentStreamEvent,
    ExecutionArtifactPaths,
)


def _wait_until(condition, *, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class ExecutionWorkerTests(unittest.TestCase):
    def test_launch_returns_before_blocking_runner_launch_finishes(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            event_queue: queue.Queue = queue.Queue()
            runner = _BlockingLaunchRunner(temp_dir)
            worker = ExecutionWorker(runner=runner, event_queue=event_queue)
            request = JobExecutionRequest(
                job_id="job-0",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                workspace_path=str(temp_dir),
                session_id=None,
                prompt="slow launch",
                operational_settings=AppSettings(executable_path=str(temp_dir / "agent.exe")),
            )

            started_at = time.perf_counter()
            handle = worker.launch(request)
            elapsed = time.perf_counter() - started_at

            self.assertLess(elapsed, 0.1)
            self.assertTrue(worker.has_pending_work())
            self.assertTrue(runner.launch_started.wait(timeout=0.2))

            worker.cancel(handle)
            runner.release_launch()

            completion_event = event_queue.get(timeout=1.0)
            self.assertIsInstance(completion_event, ExecutionCompletedEvent)
            self.assertEqual(AgentRunStatus.CANCELED, completion_event.result.status)
            self.assertFalse(worker.has_pending_work())

    def test_cancel_during_launch_after_handle_created_reaches_runner(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            event_queue: queue.Queue = queue.Queue()
            runner = _LaunchBlockedAfterHandleRunner(temp_dir)
            worker = ExecutionWorker(runner=runner, event_queue=event_queue)
            request = JobExecutionRequest(
                job_id="job-stdin",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                workspace_path=str(temp_dir),
                session_id=None,
                prompt="blocked stdin write",
                operational_settings=AppSettings(executable_path=str(temp_dir / "agent.exe")),
            )

            handle = worker.launch(request)
            self.assertTrue(runner.handle_created.wait(timeout=0.2))

            worker.cancel(handle)

            self.assertTrue(runner.cancel_called.wait(timeout=0.2))
            self.assertTrue(worker.has_pending_work())
            runner.release_launch()

            completion_event = event_queue.get(timeout=1.0)
            self.assertIsInstance(completion_event, ExecutionCompletedEvent)
            self.assertEqual(AgentRunStatus.CANCELED, completion_event.result.status)
            self.assertFalse(worker.has_pending_work())

    def test_cancel_returns_before_completion_event_is_enqueued(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            event_queue: queue.Queue = queue.Queue()
            runner = _AsyncCancelRunner(temp_dir)
            worker = ExecutionWorker(runner=runner, event_queue=event_queue)
            request = JobExecutionRequest(
                job_id="job-1",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                workspace_path=str(temp_dir),
                session_id=None,
                prompt="cancel me",
                operational_settings=AppSettings(executable_path=str(temp_dir / "agent.exe")),
            )

            handle = worker.launch(request)
            worker.cancel(handle)

            self.assertTrue(worker.has_pending_work())
            self.assertTrue(runner.handle_ready.wait(timeout=0.2))
            self.assertIsNotNone(runner.handle)
            self.assertTrue(runner.handle.wait_thread_blocked.wait(timeout=0.1))
            with self.assertRaises(queue.Empty):
                event_queue.get_nowait()

            completion_event = event_queue.get(timeout=1.0)
            self.assertIsInstance(completion_event, ExecutionCompletedEvent)
            self.assertEqual(AgentRunStatus.CANCELED, completion_event.result.status)
            self.assertFalse(runner.handle.wait_thread_blocked.is_set())
            self.assertFalse(worker.has_pending_work())

    def test_wait_exception_still_enqueues_failed_completion_event(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            event_queue: queue.Queue = queue.Queue()
            runner = _WaitFailureRunner(temp_dir)
            worker = ExecutionWorker(runner=runner, event_queue=event_queue)
            request = JobExecutionRequest(
                job_id="job-2",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                workspace_path=str(temp_dir),
                session_id=None,
                prompt="fail wait",
                operational_settings=AppSettings(executable_path=str(temp_dir / "agent.exe")),
            )

            worker.launch(request)

            self.assertTrue(
                _wait_until(lambda: not event_queue.empty()),
                "completion event was not enqueued",
            )
            self.assertTrue(worker.has_pending_work())

            completion_event = event_queue.get_nowait()
            self.assertIsInstance(completion_event, ExecutionCompletedEvent)
            self.assertEqual(AgentRunStatus.FAILED, completion_event.result.status)
            self.assertEqual(("fake-agent", "job-2"), completion_event.result.command)
            self.assertEqual(
                "완료 대기 중 오류가 발생했습니다.",
                completion_event.result.failure_reason,
            )
            self.assertFalse(worker.has_pending_work())

    def test_disabled_file_logging_keeps_progress_log_event_and_session_id(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            event_queue: queue.Queue = queue.Queue()
            runner = _JsonEventRunner(
                temp_dir,
                AgentStreamEvent(
                    line_number=1,
                    event_type="thread.started",
                    payload={"type": "thread.started", "thread_id": "thread-1"},
                    thread_id="thread-1",
                ),
            )
            worker = ExecutionWorker(runner=runner, event_queue=event_queue)
            request = JobExecutionRequest(
                job_id="job-log-off",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                workspace_path=str(temp_dir),
                session_id=None,
                prompt="no progress log",
                operational_settings=AppSettings(
                    executable_path=str(temp_dir / "agent.exe"),
                    file_logging_enabled=False,
                ),
            )

            worker.launch(request)

            self.assertTrue(
                _wait_until(lambda: event_queue.qsize() >= 3),
                "worker did not publish expected events",
            )
            events = []
            while not event_queue.empty():
                events.append(event_queue.get_nowait())
            self.assertTrue(
                _wait_until(lambda: not worker.has_pending_work()),
                "worker did not clear completed execution tracking",
            )

            self.assertTrue(any(isinstance(event, ExecutionLogEvent) for event in events))
            self.assertTrue(any(isinstance(event, ExecutionSessionIdEvent) for event in events))
            self.assertTrue(any(isinstance(event, ExecutionCompletedEvent) for event in events))


class _AsyncCancelRunner:
    def __init__(self, artifacts_root: Path) -> None:
        self._artifacts_root = artifacts_root
        self.handle: _AsyncCancelHandle | None = None
        self.handle_ready = threading.Event()

    def validate(self, request: JobExecutionRequest) -> str | None:
        return None

    def launch(
        self,
        request: JobExecutionRequest,
        *,
        on_stdout_line=None,
        on_stderr_line=None,
        on_json_event=None,
        on_handle_created=None,
    ) -> _AsyncCancelHandle:
        self.handle = _AsyncCancelHandle(
            job_id=request.job_id,
            artifacts_root=self._artifacts_root,
        )
        self.handle_ready.set()
        if on_handle_created is not None:
            on_handle_created(self.handle)
        return self.handle

    def cancel(self, handle: _AsyncCancelHandle) -> None:
        threading.Thread(
            target=self._resolve_later,
            args=(handle,),
            name=f"cancel-resolve-{handle.handle_id}",
            daemon=True,
        ).start()

    @staticmethod
    def _resolve_later(handle: _AsyncCancelHandle) -> None:
        time.sleep(0.15)
        handle.resolve_canceled()


class _JsonEventRunner:
    def __init__(self, artifacts_root: Path, event: AgentStreamEvent) -> None:
        self._artifacts_root = artifacts_root
        self._event = event

    def validate(self, request: JobExecutionRequest) -> str | None:
        return None

    def launch(
        self,
        request: JobExecutionRequest,
        *,
        on_stdout_line=None,
        on_stderr_line=None,
        on_json_event=None,
        on_handle_created=None,
    ) -> "_ImmediateCompletedHandle":
        handle = _ImmediateCompletedHandle(
            job_id=request.job_id,
            artifacts_root=self._artifacts_root,
        )
        if on_handle_created is not None:
            on_handle_created(handle)
        if on_json_event is not None:
            on_json_event(self._event)
        return handle

    def cancel(self, handle: "_ImmediateCompletedHandle") -> None:
        return None


class _ImmediateCompletedHandle:
    def __init__(self, *, job_id: str, artifacts_root: Path) -> None:
        self.handle_id = job_id
        self.command = ("fake-agent", job_id)
        self.artifacts = _create_artifacts(artifacts_root, job_id)

    def wait(self, timeout: float | None = None) -> AgentRunResult:
        return AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            command=self.command,
            artifacts=self.artifacts,
            exit_code=0,
        )


class _AsyncCancelHandle:
    def __init__(self, *, job_id: str, artifacts_root: Path) -> None:
        self.handle_id = job_id
        self._artifacts_root = artifacts_root
        self.command = ("fake-agent", job_id)
        self.artifacts = _create_artifacts(artifacts_root, job_id)
        self._ready = threading.Event()
        self.wait_thread_blocked = threading.Event()

    def wait(self, timeout: float | None = None) -> AgentRunResult:
        self.wait_thread_blocked.set()
        is_ready = self._ready.wait(timeout)
        self.wait_thread_blocked.clear()
        if not is_ready:
            raise TimeoutError(f"Result not ready for {self.handle_id}")
        return AgentRunResult(
            status=AgentRunStatus.CANCELED,
            command=self.command,
            artifacts=self.artifacts,
            exit_code=-15,
        )

    def resolve_canceled(self) -> None:
        self._ready.set()


class _WaitFailureRunner:
    def __init__(self, artifacts_root: Path) -> None:
        self._artifacts_root = artifacts_root

    def validate(self, request: JobExecutionRequest) -> str | None:
        return None

    def launch(
        self,
        request: JobExecutionRequest,
        *,
        on_stdout_line=None,
        on_stderr_line=None,
        on_json_event=None,
        on_handle_created=None,
    ) -> _WaitFailureHandle:
        handle = _WaitFailureHandle(
            job_id=request.job_id,
            artifacts_root=self._artifacts_root,
        )
        if on_handle_created is not None:
            on_handle_created(handle)
        return handle

    def cancel(self, handle: _WaitFailureHandle) -> None:
        return None


class _LaunchBlockedAfterHandleRunner:
    def __init__(self, artifacts_root: Path) -> None:
        self._artifacts_root = artifacts_root
        self._launch_released = threading.Event()
        self.handle_created = threading.Event()
        self.cancel_called = threading.Event()
        self.handle: _BlockingLaunchHandle | None = None

    def validate(self, request: JobExecutionRequest) -> str | None:
        return None

    def launch(
        self,
        request: JobExecutionRequest,
        *,
        on_stdout_line=None,
        on_stderr_line=None,
        on_json_event=None,
        on_handle_created=None,
    ) -> _BlockingLaunchHandle:
        self.handle = _BlockingLaunchHandle(
            job_id=request.job_id,
            artifacts_root=self._artifacts_root,
        )
        if on_handle_created is not None:
            on_handle_created(self.handle)
        self.handle_created.set()
        if not self._launch_released.wait(timeout=1.0):
            raise TimeoutError("Launch was not released in time.")
        return self.handle

    def cancel(self, handle: _BlockingLaunchHandle) -> None:
        self.cancel_called.set()
        handle.resolve_canceled()

    def release_launch(self) -> None:
        self._launch_released.set()


class _BlockingLaunchRunner:
    def __init__(self, artifacts_root: Path) -> None:
        self._artifacts_root = artifacts_root
        self._launch_released = threading.Event()
        self.launch_started = threading.Event()

    def validate(self, request: JobExecutionRequest) -> str | None:
        return None

    def launch(
        self,
        request: JobExecutionRequest,
        *,
        on_stdout_line=None,
        on_stderr_line=None,
        on_json_event=None,
        on_handle_created=None,
    ) -> _BlockingLaunchHandle:
        self.launch_started.set()
        if not self._launch_released.wait(timeout=1.0):
            raise TimeoutError("Launch was not released in time.")
        handle = _BlockingLaunchHandle(
            job_id=request.job_id,
            artifacts_root=self._artifacts_root,
        )
        if on_handle_created is not None:
            on_handle_created(handle)
        return handle

    def cancel(self, handle: _BlockingLaunchHandle) -> None:
        handle.resolve_canceled()

    def release_launch(self) -> None:
        self._launch_released.set()


class _BlockingLaunchHandle:
    def __init__(self, *, job_id: str, artifacts_root: Path) -> None:
        self.handle_id = job_id
        self.command = ("fake-agent", job_id)
        self.artifacts = _create_artifacts(artifacts_root, job_id)
        self._ready = threading.Event()

    def wait(self, timeout: float | None = None) -> AgentRunResult:
        if not self._ready.wait(timeout):
            raise TimeoutError(f"Result not ready for {self.handle_id}")
        return AgentRunResult(
            status=AgentRunStatus.CANCELED,
            command=self.command,
            artifacts=self.artifacts,
            exit_code=-15,
        )

    def resolve_canceled(self) -> None:
        self._ready.set()


class _WaitFailureHandle:
    def __init__(self, *, job_id: str, artifacts_root: Path) -> None:
        self.handle_id = job_id
        self.command = ("fake-agent", job_id)
        self.artifacts = _create_artifacts(artifacts_root, job_id)

    def wait(self, timeout: float | None = None) -> AgentRunResult:
        raise RuntimeError("simulated wait failure")


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


if __name__ == "__main__":
    unittest.main()

