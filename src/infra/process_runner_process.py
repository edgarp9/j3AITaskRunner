"""Process runner implementation split from infra.process_runner."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
from typing import IO, Any
import sys

from domain import (
    DEFAULT_AGENT_PROVIDER,
    AppSettings,
    is_execution_control_limit_enabled,
    normalize_agent_provider,
)
from domain.models import (
    EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
    TERMINATION_GRACE_SECONDS_MAX,
    utc_now,
)

from .agent_contract import (
    AgentCliAdapter,
    AgentParseSummary,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    AgentStreamEvent,
    ExecutionArtifactPaths,
    PopenLike,
    SupportsAgentExecutionRequest,
)
from .codex_adapter import (
    CodexCliAdapter,
    build_codex_command,
    build_codex_environment,
    build_codex_popen_kwargs,
)
from .claude_code_adapter import ClaudeCodeCliAdapter, build_claude_code_command
from .open_code_adapter import (
    KiloCodeCliAdapter,
    OpenCodeCliAdapter,
    build_kilo_code_command,
    build_opencode_command,
)
from .pi_adapter import PiCliAdapter, build_pi_command
from .subprocess_options import WINDOWS_CREATE_NO_WINDOW, hidden_console_creationflags

LOGGER = logging.getLogger("infra.process_runner")


def _process_runner_global(name: str):
    return getattr(sys.modules["infra.process_runner"], name)


class RunningAgentProcess:
    """Control handle for one live external agent CLI subprocess."""

    def __init__(
        self,
        *,
        request: SupportsAgentExecutionRequest,
        command: tuple[str, ...],
        process: PopenLike,
        artifacts: ExecutionArtifactPaths,
        started_at: datetime,
        launch_metadata: dict[str, Any],
        adapter: AgentCliAdapter | None = None,
        parser: object | None = None,
        on_complete: Callable[[str], None] | None = None,
        on_stdout_line: Callable[[str], None] | None = None,
        on_stderr_line: Callable[[str], None] | None = None,
        on_json_event: Callable[[AgentStreamEvent], None] | None = None,
    ) -> None:
        self._request = request
        self._command = command
        self._process = process
        self._artifacts = artifacts
        self._adapter = adapter or CodexCliAdapter(parser=parser)
        self._started_at = started_at
        self._launch_metadata = launch_metadata
        self._on_complete = on_complete
        self._on_stdout_line = on_stdout_line
        self._on_stderr_line = on_stderr_line
        self._on_json_event = on_json_event
        self._file_logging_enabled = request.operational_settings.file_logging_enabled
        self._started_monotonic = time.monotonic()
        self._last_activity_monotonic = self._started_monotonic
        self._execution_timeout_seconds = _process_runner_global("_minutes_to_seconds")(
            request.operational_settings.execution_timeout_minutes
        )
        self._inactivity_timeout_seconds = _process_runner_global("_minutes_to_seconds")(
            request.operational_settings.inactivity_timeout_minutes
        )
        self._result: AgentRunResult | None = None
        self._timeout_failure_reason: str | None = None
        self._streams_started = False
        self._timeout_monitor_started = False
        self._completion_notified = False
        self._cancel_requested = False
        self._terminate_monitor_started = False
        self._stream_activity_count = 0
        self._lock = threading.Lock()
        self._stdout_thread = threading.Thread(
            target=self._consume_stdout,
            name=f"agent-stdout-{request.job_id}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._consume_stderr,
            name=f"agent-stderr-{request.job_id}",
            daemon=True,
        )
        self._timeout_thread = threading.Thread(
            target=self._monitor_timeouts,
            name=f"agent-timeout-{request.job_id}",
            daemon=True,
        )

    @property
    def handle_id(self) -> str:
        """Return the stable handle id for queue integration."""
        return self._request.job_id

    @property
    def pid(self) -> int | None:
        """Return the process id when available."""
        return self._process.pid

    @property
    def command(self) -> tuple[str, ...]:
        """Return the launched command."""
        return self._command

    @property
    def artifacts(self) -> ExecutionArtifactPaths:
        """Return artifact file paths."""
        return self._artifacts

    @property
    def cancel_requested(self) -> bool:
        """Return whether cancellation has been requested for this process."""
        with self._lock:
            return self._cancel_requested

    def start(self) -> None:
        """Start background stdout/stderr consumers exactly once."""
        with self._lock:
            if self._streams_started:
                return
            self._streams_started = True
            self._stdout_thread.start()
            self._stderr_thread.start()
            if (
                self._execution_timeout_seconds is not None
                or self._inactivity_timeout_seconds is not None
            ):
                self._timeout_monitor_started = True
                self._timeout_thread.start()

    def terminate(self, timeout: float | None = None) -> None:
        """Terminate the running process and mark the result as canceled."""
        if timeout is None:
            timeout = _process_runner_global("_termination_timeout_from_settings")(self._request.operational_settings)
        self._request_termination(timeout=timeout, mark_canceled=True)

    def _request_termination(
        self,
        *,
        timeout: float | None,
        mark_canceled: bool,
    ) -> None:
        with self._lock:
            if self._process.poll() is not None:
                return
            if mark_canceled:
                self._cancel_requested = True
            if self._terminate_monitor_started:
                return
            self._terminate_monitor_started = True

        try:
            _process_runner_global("_terminate_process_tree")(self._process, force=False)
        except Exception:
            if self._process.poll() is None:
                LOGGER.exception(
                    "Failed to terminate agent CLI process. Trying kill fallback. handle_id=%s pid=%s",
                    self.handle_id,
                    self.pid,
                )
                self._kill_after_timeout()
            return

        if timeout is None:
            return

        monitor = threading.Thread(
            target=self._enforce_termination_timeout,
            args=(timeout,),
            name=f"agent-terminate-{self.handle_id}",
            daemon=True,
        )
        monitor.start()

    def wait(self, timeout: float | None = None) -> AgentRunResult:
        """Wait for the process to finish and resolve the final result."""
        with self._lock:
            if self._result is not None:
                return self._result

        self.start()
        exit_code = self._wait_for_process_exit(timeout=timeout)
        self._join_stream_readers()
        completed_at = utc_now()
        parser_summary = self._adapter.build_parse_summary()
        last_message = self._read_last_message()
        status, failure_reason = self._resolve_outcome(
            parser_summary=parser_summary,
            exit_code=exit_code,
            last_message=last_message,
        )
        result = AgentRunResult(
            status=status,
            command=self._command,
            artifacts=self._artifacts,
            parser_summary=parser_summary,
            exit_code=exit_code,
            session_id=self._adapter.extract_session_id(parser_summary),
            last_message=last_message,
            failure_reason=failure_reason,
            started_at=self._started_at,
            completed_at=completed_at,
        )

        with self._lock:
            if self._result is None:
                self._result = result
                self._persist_completion_metadata(result)
                self._artifacts.cleanup_ephemeral()
                self._notify_complete()
            return self._result

    def _wait_for_process_exit(self, *, timeout: float | None) -> int | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        timeout_exit_deadline: float | None = None

        while True:
            exit_code = self._process.poll()
            if exit_code is not None:
                return exit_code

            timeout_reason = self._resolve_timeout_failure_reason()
            if timeout_reason is not None:
                self._terminate_for_timeout(timeout_reason)
                if timeout_exit_deadline is None:
                    timeout_exit_deadline = self._build_timeout_exit_deadline()
                if (
                    timeout_exit_deadline is not None
                    and time.monotonic() >= timeout_exit_deadline
                ):
                    self._kill_after_timeout()
                    exit_code = self._process.poll()
                    if exit_code is not None:
                        return exit_code
                    LOGGER.warning(
                        "agent CLI process remained alive after timeout termination budget. "
                        "Resolving failed result without waiting forever. handle_id=%s pid=%s",
                        self.handle_id,
                        self.pid,
                    )
                    return None

            wait_timeout = _process_runner_global("_WAIT_POLL_INTERVAL_SECONDS")
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(self._command, timeout)
                wait_timeout = min(wait_timeout, remaining)

            try:
                return self._process.wait(timeout=wait_timeout)
            except subprocess.TimeoutExpired:
                continue

    def _join_stream_readers(self) -> None:
        alive_readers = [
            (stream_name, thread)
            for stream_name, thread in (
                ("stdout", self._stdout_thread),
                ("stderr", self._stderr_thread),
            )
            if thread.is_alive()
        ]
        if not alive_readers:
            return

        with self._lock:
            last_activity_count = self._stream_activity_count
        idle_deadline = time.monotonic() + _process_runner_global("_STREAM_READER_JOIN_TIMEOUT_SECONDS")

        while alive_readers:
            now = time.monotonic()
            if now >= idle_deadline:
                break

            for _, thread in alive_readers:
                remaining = idle_deadline - time.monotonic()
                if remaining <= 0:
                    break
                thread.join(
                    timeout=min(_process_runner_global("_STREAM_READER_JOIN_POLL_INTERVAL_SECONDS"), remaining)
                )

            alive_readers = [
                (stream_name, thread)
                for stream_name, thread in alive_readers
                if thread.is_alive()
            ]
            if not alive_readers:
                return

            with self._lock:
                activity_count = self._stream_activity_count
            if activity_count != last_activity_count:
                last_activity_count = activity_count
                idle_deadline = time.monotonic() + _process_runner_global("_STREAM_READER_JOIN_TIMEOUT_SECONDS")

        for stream_name, thread in (
            ("stdout", self._stdout_thread),
            ("stderr", self._stderr_thread),
        ):
            if thread.is_alive():
                LOGGER.warning(
                    "agent CLI stream reader did not finish in time. handle_id=%s stream=%s",
                    self.handle_id,
                    stream_name,
                )

    def _consume_stdout(self) -> None:
        self._consume_stream(
            stream=self._process.stdout,
            output_path=self._artifacts.stdout_jsonl_path,
            on_line=self._adapter.feed_stdout_line,
            on_stream_line=self._on_stdout_line,
            on_parsed_event=self._on_json_event,
            save_to_file=self._file_logging_enabled,
        )

    def _enforce_termination_timeout(self, timeout: float) -> None:
        if timeout <= 0:
            self._kill_after_timeout()
            return

        deadline = time.monotonic() + timeout
        while self._process.poll() is None and time.monotonic() < deadline:
            time.sleep(_process_runner_global("_TERMINATE_POLL_INTERVAL_SECONDS"))

        self._kill_after_timeout()

    def _monitor_timeouts(self) -> None:
        while self._process.poll() is None:
            timeout_reason = self._resolve_timeout_failure_reason()
            if timeout_reason is not None:
                self._terminate_for_timeout(timeout_reason)
                return
            time.sleep(_process_runner_global("_TIMEOUT_MONITOR_POLL_INTERVAL_SECONDS"))

    def _resolve_timeout_failure_reason(self) -> str | None:
        now = time.monotonic()
        with self._lock:
            execution_elapsed = now - self._started_monotonic
            inactivity_elapsed = now - self._last_activity_monotonic

        if (
            self._execution_timeout_seconds is not None
            and execution_elapsed >= self._execution_timeout_seconds
        ):
            return (
                "시간 제한 초과: 전체 실행 제한"
                f"({self._request.operational_settings.execution_timeout_minutes}분)을 초과했습니다."
            )

        if (
            self._inactivity_timeout_seconds is not None
            and inactivity_elapsed >= self._inactivity_timeout_seconds
        ):
            return (
                "시간 제한 초과: 출력 무활동 제한"
                f"({self._request.operational_settings.inactivity_timeout_minutes}분)을 초과했습니다."
            )

        return None

    def _terminate_for_timeout(self, failure_reason: str) -> None:
        with self._lock:
            if self._cancel_requested:
                return
            if self._timeout_failure_reason is not None:
                return
            self._timeout_failure_reason = failure_reason
            if self._process.poll() is not None:
                return

        LOGGER.warning(
            "agent CLI process exceeded execution control limit. handle_id=%s pid=%s reason=%s",
            self.handle_id,
            self.pid,
            failure_reason,
        )
        self._request_termination(
            timeout=_process_runner_global("_termination_timeout_from_settings")(self._request.operational_settings),
            mark_canceled=False,
        )

    def _build_timeout_exit_deadline(self) -> float | None:
        timeout = _process_runner_global("_termination_timeout_from_settings")(self._request.operational_settings)
        if timeout is None:
            return None
        return time.monotonic() + timeout + _process_runner_global("_TIMEOUT_EXIT_FALLBACK_SECONDS")

    def _kill_after_timeout(self) -> None:
        if self._process.poll() is not None:
            return

        LOGGER.warning(
            "agent CLI process did not terminate in time. handle_id=%s pid=%s",
            self.handle_id,
            self.pid,
        )
        try:
            _process_runner_global("_terminate_process_tree")(self._process, force=True)
        except Exception:
            if self._process.poll() is None:
                LOGGER.exception(
                    "Failed to kill unresponsive agent CLI process. handle_id=%s pid=%s",
                    self.handle_id,
                    self.pid,
                )

    def _consume_stderr(self) -> None:
        self._consume_stream(
            stream=self._process.stderr,
            output_path=self._artifacts.stderr_log_path,
            on_line=None,
            on_stream_line=self._on_stderr_line,
            save_to_file=self._file_logging_enabled,
        )

    def _consume_stream(
        self,
        *,
        stream: IO[str] | None,
        output_path: Path,
        on_line: Callable[[str], object] | None,
        on_stream_line: Callable[[str], None] | None = None,
        on_parsed_event: Callable[[AgentStreamEvent], None] | None = None,
        save_to_file: bool = True,
    ) -> None:
        if stream is None:
            return

        artifact_file = self._open_stream_artifact(output_path) if save_to_file else None
        artifact_lines_since_flush = 0
        try:
            for line in stream:
                self._mark_activity()
                if artifact_file is not None:
                    artifact_file, artifact_lines_since_flush = self._write_stream_artifact_line(
                        artifact_file=artifact_file,
                        output_path=output_path,
                        line=line,
                        lines_since_flush=artifact_lines_since_flush,
                    )
                if on_stream_line is not None:
                    self._notify_callback(
                        callback=on_stream_line,
                        payload=line,
                        callback_name="stream line callback",
                    )

                if on_line is not None:
                    try:
                        parsed_event = on_line(line)
                    except Exception:
                        LOGGER.exception(
                            "Failed to parse agent CLI stream line. handle_id=%s path=%s",
                            self.handle_id,
                            output_path,
                        )
                        continue
                    if on_parsed_event is not None and isinstance(parsed_event, AgentStreamEvent):
                        self._notify_callback(
                            callback=on_parsed_event,
                            payload=parsed_event,
                            callback_name="json event callback",
                        )
        except Exception:
            LOGGER.exception(
                "Failed while consuming agent CLI stream. handle_id=%s path=%s",
                self.handle_id,
                output_path,
            )
        finally:
            if artifact_file is not None:
                self._close_stream_artifact(artifact_file, output_path)
            close_method = getattr(stream, "close", None)
            if callable(close_method):
                close_method()

    def _open_stream_artifact(self, output_path: Path) -> IO[str] | None:
        try:
            return output_path.open("a", encoding="utf-8")
        except Exception:
            LOGGER.exception(
                "Failed to open agent CLI artifact file. "
                "Continuing without file logging for this stream. handle_id=%s path=%s",
                self.handle_id,
                output_path,
            )
            return None

    def _write_stream_artifact_line(
        self,
        *,
        artifact_file: IO[str],
        output_path: Path,
        line: str,
        lines_since_flush: int,
    ) -> tuple[IO[str] | None, int]:
        try:
            artifact_file.write(line)
            lines_since_flush += 1
            if lines_since_flush >= _process_runner_global("_ARTIFACT_FILE_FLUSH_LINE_INTERVAL"):
                artifact_file.flush()
                lines_since_flush = 0
            return artifact_file, lines_since_flush
        except Exception:
            LOGGER.exception(
                "Failed to write agent CLI artifact file. "
                "Continuing without file logging for this stream. handle_id=%s path=%s",
                self.handle_id,
                output_path,
            )
            self._close_stream_artifact(artifact_file, output_path)
            return None, 0

    def _close_stream_artifact(self, artifact_file: IO[str], output_path: Path) -> None:
        try:
            artifact_file.close()
        except Exception:
            LOGGER.warning(
                "Failed to close agent CLI artifact file. handle_id=%s path=%s",
                self.handle_id,
                output_path,
                exc_info=True,
            )

    def _notify_callback(
        self,
        *,
        callback: Callable[[Any], None],
        payload: Any,
        callback_name: str,
    ) -> None:
        try:
            callback(payload)
        except Exception:
            LOGGER.exception(
                "Execution callback failed. handle_id=%s callback=%s",
                self.handle_id,
                callback_name,
            )

    def _mark_activity(self) -> None:
        with self._lock:
            self._last_activity_monotonic = time.monotonic()
            self._stream_activity_count += 1

    def _read_last_message(self) -> str | None:
        return self._adapter.read_last_message(self._artifacts)

    def _resolve_outcome(
        self,
        *,
        parser_summary: AgentParseSummary,
        exit_code: int | None,
        last_message: str | None,
    ) -> tuple[AgentRunStatus, str | None]:
        with self._lock:
            timeout_failure_reason = self._timeout_failure_reason
            cancel_requested = self._cancel_requested

        return self._adapter.resolve_outcome(
            parser_summary=parser_summary,
            exit_code=exit_code,
            last_message=last_message,
            cancel_requested=cancel_requested,
            timeout_failure_reason=timeout_failure_reason,
        )

    def _persist_completion_metadata(self, result: AgentRunResult) -> None:
        if not self._file_logging_enabled:
            return

        completion_metadata = dict(self._launch_metadata)
        completion_metadata.update(
            {
                "completed_at": _process_runner_global("_serialize_datetime")(result.completed_at),
                "result_status": result.status.value,
                "exit_code": result.exit_code,
                "resolved_session_id": result.session_id,
                "failure_reason": result.failure_reason,
                "turn_completed": result.parser_summary.saw_turn_completed,
                "has_failure_event": result.parser_summary.has_failure_event,
                "has_error_event": result.parser_summary.has_error_event,
                "error_event_count": len(result.parser_summary.error_events),
                "turn_failed_event_count": len(result.parser_summary.turn_failed_events),
            }
        )
        try:
            _process_runner_global("_write_json_file")(self._artifacts.launch_metadata_path, completion_metadata)
        except OSError:
            LOGGER.exception(
                "Failed to update agent CLI launch metadata. handle_id=%s path=%s",
                self.handle_id,
                self._artifacts.launch_metadata_path,
            )

    def _notify_complete(self) -> None:
        if self._completion_notified:
            return
        self._completion_notified = True
        if self._on_complete is not None:
            self._on_complete(self.handle_id)

