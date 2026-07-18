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


__all__ = [name for name in globals() if not name.startswith("__")]

