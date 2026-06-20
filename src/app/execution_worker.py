"""Background execution worker that bridges subprocess activity into queues."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import logging
from pathlib import Path
from queue import Queue
import threading
from typing import Protocol

from domain.models import utc_now
from infra.process_runner import (
    AgentRunResult,
    AgentRunStatus,
    AgentStreamEvent,
    ExecutionArtifactPaths,
    ProcessLaunchError,
)

from .messages import format_progress_event
from .scheduler import ExecutionHandle, JobExecutionRequest, JobExecutor

LOGGER = logging.getLogger(__name__)


class SupportsBackgroundExecutionHandle(Protocol):
    """Minimal running-handle contract needed by the execution worker."""

    handle_id: str
    command: tuple[str, ...]
    artifacts: ExecutionArtifactPaths

    def wait(self, timeout: float | None = None) -> AgentRunResult: ...


class BackgroundExecutionRunner(Protocol):
    """Runner contract that supports validation, launch callbacks, and cancel."""

    def validate(self, request: JobExecutionRequest) -> str | None: ...

    def launch(
        self,
        request: JobExecutionRequest,
        *,
        on_stdout_line: Callable[[str], None] | None = None,
        on_stderr_line: Callable[[str], None] | None = None,
        on_json_event: Callable[[AgentStreamEvent], None] | None = None,
        on_handle_created: Callable[[SupportsBackgroundExecutionHandle], None] | None = None,
    ) -> SupportsBackgroundExecutionHandle: ...

    def cancel(self, handle: SupportsBackgroundExecutionHandle) -> None: ...


@dataclass(slots=True, frozen=True)
class ExecutionLogEvent:
    """One user-visible progress log line emitted from JSONL stdout."""

    job_id: str
    stream_name: str
    line: str
    occurred_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True, frozen=True)
class ExecutionSessionIdEvent:
    """A session id confirmed from the JSONL stdout stream."""

    job_id: str
    session_id: str
    occurred_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True, frozen=True)
class ExecutionCompletedEvent:
    """A final execution result resolved in the background."""

    job_id: str
    result: AgentRunResult
    occurred_at: datetime = field(default_factory=utc_now)


ExecutionWorkerEvent = ExecutionLogEvent | ExecutionSessionIdEvent | ExecutionCompletedEvent


class ExecutionWorker(JobExecutor):
    """Launch jobs in the background and push execution activity into a queue."""

    def __init__(
        self,
        *,
        runner: BackgroundExecutionRunner,
        event_queue: Queue[ExecutionWorkerEvent],
    ) -> None:
        self._runner = runner
        self._event_queue = event_queue
        self._launch_threads: dict[str, threading.Thread] = {}
        self._running_handles: dict[str, SupportsBackgroundExecutionHandle] = {}
        self._waiter_threads: dict[str, threading.Thread] = {}
        self._cancel_threads: dict[str, threading.Thread] = {}
        self._pending_cancel_job_ids: set[str] = set()
        self._handles_lock = threading.Lock()

    def validate(self, request: JobExecutionRequest) -> str | None:
        """Return a configuration-wait reason when the runner cannot start yet."""
        return self._runner.validate(request)

    def launch(self, request: JobExecutionRequest) -> ExecutionHandle:
        """Queue one job launch on a worker thread and return immediately."""
        launcher = threading.Thread(
            target=self._launch_in_background,
            args=(request,),
            name=f"execution-launch-{request.job_id}",
            daemon=True,
        )
        with self._handles_lock:
            self._launch_threads[request.job_id] = launcher
        try:
            launcher.start()
        except Exception:
            with self._handles_lock:
                self._launch_threads.pop(request.job_id, None)
                self._pending_cancel_job_ids.discard(request.job_id)
            raise
        return ExecutionHandle(handle_id=request.job_id)

    def cancel(self, handle: ExecutionHandle) -> None:
        """Request cancellation without waiting for process termination."""
        with self._handles_lock:
            running_handle = self._running_handles.get(handle.handle_id)
            if running_handle is None:
                if handle.handle_id in self._launch_threads:
                    self._pending_cancel_job_ids.add(handle.handle_id)
                return
        self._start_cancel_thread(handle.handle_id, running_handle)

    def has_pending_work(self) -> bool:
        """Return whether execution cleanup or worker event delivery is still pending."""
        with self._handles_lock:
            return bool(
                self._launch_threads
                or self._running_handles
                or self._waiter_threads
                or self._cancel_threads
                or not self._event_queue.empty()
            )

    def _launch_in_background(self, request: JobExecutionRequest) -> None:
        try:
            running_handle = self._runner.launch(
                request,
                on_json_event=lambda event: self._publish_json_event(request.job_id, event),
                on_handle_created=lambda handle: self._register_launching_handle(
                    request.job_id,
                    handle,
                ),
            )
        except ProcessLaunchError as exc:
            self._publish_completion_event_and_clear_tracking(
                job_id=request.job_id,
                result=exc.result,
                clear_launch=True,
                clear_running=True,
            )
            return
        except Exception:
            LOGGER.exception("Execution launch failed. job_id=%s", request.job_id)
            self._publish_completion_event_and_clear_tracking(
                job_id=request.job_id,
                result=self._build_launch_failure_result(request.job_id),
                clear_launch=True,
                clear_running=True,
            )
            return

        waiter = threading.Thread(
            target=self._wait_for_result,
            args=(request.job_id, running_handle),
            name=f"execution-wait-{request.job_id}",
            daemon=True,
        )
        with self._handles_lock:
            self._launch_threads.pop(request.job_id, None)
            self._running_handles[request.job_id] = running_handle
            self._waiter_threads[request.job_id] = waiter
            cancel_requested = request.job_id in self._pending_cancel_job_ids
            self._pending_cancel_job_ids.discard(request.job_id)

        try:
            waiter.start()
        except Exception:
            LOGGER.exception("Failed to start execution waiter. job_id=%s", request.job_id)
            try:
                self._runner.cancel(running_handle)
            except Exception:
                LOGGER.exception(
                    "Failed to cancel execution after waiter startup failure. job_id=%s",
                    request.job_id,
                )
            self._publish_completion_event_and_clear_tracking(
                job_id=request.job_id,
                result=self._build_launch_failure_result(request.job_id),
                clear_running=True,
                clear_waiter=True,
            )
            return

        if not cancel_requested:
            return
        try:
            self._start_cancel_thread(request.job_id, running_handle)
        except Exception:
            LOGGER.exception("Failed to cancel newly launched job. job_id=%s", request.job_id)

    def _register_launching_handle(
        self,
        job_id: str,
        running_handle: SupportsBackgroundExecutionHandle,
    ) -> None:
        """Track a process handle as soon as the runner creates it."""
        with self._handles_lock:
            self._running_handles[job_id] = running_handle
            cancel_requested = job_id in self._pending_cancel_job_ids
            self._pending_cancel_job_ids.discard(job_id)

        if not cancel_requested:
            return
        try:
            self._start_cancel_thread(job_id, running_handle)
        except Exception:
            LOGGER.exception("Failed to cancel launching job. job_id=%s", job_id)

    def _start_cancel_thread(
        self,
        job_id: str,
        running_handle: SupportsBackgroundExecutionHandle,
    ) -> None:
        canceler = threading.Thread(
            target=self._cancel_in_background,
            args=(job_id, running_handle),
            name=f"execution-cancel-{job_id}",
            daemon=True,
        )
        with self._handles_lock:
            if job_id in self._cancel_threads:
                return
            self._cancel_threads[job_id] = canceler
        try:
            canceler.start()
        except Exception:
            with self._handles_lock:
                self._cancel_threads.pop(job_id, None)
            raise

    def _cancel_in_background(
        self,
        job_id: str,
        running_handle: SupportsBackgroundExecutionHandle,
    ) -> None:
        try:
            self._runner.cancel(running_handle)
        except Exception:
            LOGGER.exception("Failed to cancel running job. job_id=%s", job_id)
        finally:
            with self._handles_lock:
                self._cancel_threads.pop(job_id, None)

    def _wait_for_result(
        self,
        job_id: str,
        running_handle: SupportsBackgroundExecutionHandle,
    ) -> None:
        try:
            result = running_handle.wait()
        except Exception:
            LOGGER.exception(
                "Execution wait failed. job_id=%s handle_id=%s",
                job_id,
                running_handle.handle_id,
            )
            result = self._build_wait_failure_result(job_id, running_handle)
        self._publish_completion_event_and_clear_tracking(
            job_id=job_id,
            result=result,
            clear_running=True,
            clear_waiter=True,
        )

    @staticmethod
    def _build_launch_failure_result(job_id: str) -> AgentRunResult:
        return AgentRunResult(
            status=AgentRunStatus.FAILED,
            command=("agent-cli", "run"),
            artifacts=_build_fallback_artifacts(job_id),
            failure_reason="실행 시작 중 오류가 발생했습니다.",
            completed_at=utc_now(),
        )

    def _publish_completion_event_and_clear_tracking(
        self,
        *,
        job_id: str,
        result: AgentRunResult,
        clear_launch: bool = False,
        clear_running: bool = False,
        clear_waiter: bool = False,
    ) -> None:
        """Publish a final event before clearing the state that keeps shutdown pending."""
        with self._handles_lock:
            self._event_queue.put(
                ExecutionCompletedEvent(
                    job_id=job_id,
                    result=result,
                )
            )
            if clear_launch:
                self._launch_threads.pop(job_id, None)
            if clear_running:
                self._running_handles.pop(job_id, None)
            if clear_waiter:
                self._waiter_threads.pop(job_id, None)
            self._pending_cancel_job_ids.discard(job_id)

    @staticmethod
    def _build_wait_failure_result(
        job_id: str,
        running_handle: SupportsBackgroundExecutionHandle,
    ) -> AgentRunResult:
        return AgentRunResult(
            status=AgentRunStatus.FAILED,
            command=getattr(running_handle, "command", ("agent-cli", "run")),
            artifacts=getattr(running_handle, "artifacts", _build_fallback_artifacts(job_id)),
            failure_reason="완료 대기 중 오류가 발생했습니다.",
            completed_at=utc_now(),
        )

    def _publish_json_event(self, job_id: str, event: AgentStreamEvent) -> None:
        self._event_queue.put(
            ExecutionLogEvent(
                job_id=job_id,
                stream_name="progress",
                line=format_progress_event(event),
            )
        )
        if event.event_type != "thread.started" or not event.thread_id:
            return

        self._event_queue.put(
            ExecutionSessionIdEvent(
                job_id=job_id,
                session_id=event.thread_id,
            )
        )


def _build_fallback_artifacts(job_id: str) -> ExecutionArtifactPaths:
    root_dir = Path("_execution_worker_fallback") / job_id
    return ExecutionArtifactPaths(
        root_dir=root_dir,
        prompt_path=root_dir / "prompt.txt",
        stdout_jsonl_path=root_dir / "stdout.jsonl",
        stderr_log_path=root_dir / "stderr.log",
        last_message_path=root_dir / "last_message.txt",
        launch_metadata_path=root_dir / "launch.json",
    )
