"""Common subprocess execution for external agent CLI providers."""

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
from .subprocess_options import (
    WINDOWS_CREATE_NO_WINDOW,
    hidden_console_creationflags,
)

LOGGER = logging.getLogger(__name__)

_DEFAULT_TERMINATE_TIMEOUT_SECONDS = 5.0
_TERMINATE_POLL_INTERVAL_SECONDS = 0.1
_TIMEOUT_MONITOR_POLL_INTERVAL_SECONDS = 0.5
_WAIT_POLL_INTERVAL_SECONDS = 0.1
_TIMEOUT_EXIT_FALLBACK_SECONDS = 0.5
_STREAM_READER_JOIN_TIMEOUT_SECONDS = 1.0
_STREAM_READER_JOIN_POLL_INTERVAL_SECONDS = 0.05
_STDIN_WRITE_CHUNK_SIZE = 64 * 1024
_ARTIFACT_FILE_FLUSH_LINE_INTERVAL = 64
_JOB_ID_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")
_WINDOWS_CREATE_NO_WINDOW = WINDOWS_CREATE_NO_WINDOW
_WINDOWS_TASKKILL_TIMEOUT_SECONDS = 5.0


SupportsCodexExecutionRequest = SupportsAgentExecutionRequest
CodexRunRequest = AgentRunRequest
CodexRunStatus = AgentRunStatus
CodexRunResult = AgentRunResult
ClaudeCodeRunRequest = AgentRunRequest
OpenCodeRunRequest = AgentRunRequest
KiloCodeRunRequest = AgentRunRequest
PiRunRequest = AgentRunRequest

_AGENT_ADAPTER_FACTORIES: dict[str, Callable[[], AgentCliAdapter]] = {
    DEFAULT_AGENT_PROVIDER: CodexCliAdapter,
    "claude_code": ClaudeCodeCliAdapter,
    "opencode": OpenCodeCliAdapter,
    "kilo_code": KiloCodeCliAdapter,
    "pi": PiCliAdapter,
}


def build_agent_cli_adapter(provider_id: str | None) -> AgentCliAdapter | None:
    """Return a supported provider adapter, leaving unsupported providers unimplemented."""
    normalized_provider = normalize_agent_provider(provider_id)
    factory = _AGENT_ADAPTER_FACTORIES.get(normalized_provider)
    if factory is None:
        return None
    return factory()


class ProcessLaunchError(RuntimeError):
    """Raised when an agent CLI process cannot be started."""

    def __init__(self, message: str, *, result: AgentRunResult) -> None:
        super().__init__(message)
        self.result = result


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
        self._execution_timeout_seconds = _minutes_to_seconds(
            request.operational_settings.execution_timeout_minutes
        )
        self._inactivity_timeout_seconds = _minutes_to_seconds(
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
            timeout = _termination_timeout_from_settings(self._request.operational_settings)
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
            _terminate_process_tree(self._process, force=False)
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

            wait_timeout = _WAIT_POLL_INTERVAL_SECONDS
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
        idle_deadline = time.monotonic() + _STREAM_READER_JOIN_TIMEOUT_SECONDS

        while alive_readers:
            now = time.monotonic()
            if now >= idle_deadline:
                break

            for _, thread in alive_readers:
                remaining = idle_deadline - time.monotonic()
                if remaining <= 0:
                    break
                thread.join(
                    timeout=min(_STREAM_READER_JOIN_POLL_INTERVAL_SECONDS, remaining)
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
                idle_deadline = time.monotonic() + _STREAM_READER_JOIN_TIMEOUT_SECONDS

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
            time.sleep(_TERMINATE_POLL_INTERVAL_SECONDS)

        self._kill_after_timeout()

    def _monitor_timeouts(self) -> None:
        while self._process.poll() is None:
            timeout_reason = self._resolve_timeout_failure_reason()
            if timeout_reason is not None:
                self._terminate_for_timeout(timeout_reason)
                return
            time.sleep(_TIMEOUT_MONITOR_POLL_INTERVAL_SECONDS)

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
            timeout=_termination_timeout_from_settings(self._request.operational_settings),
            mark_canceled=False,
        )

    def _build_timeout_exit_deadline(self) -> float | None:
        timeout = _termination_timeout_from_settings(self._request.operational_settings)
        if timeout is None:
            return None
        return time.monotonic() + timeout + _TIMEOUT_EXIT_FALLBACK_SECONDS

    def _kill_after_timeout(self) -> None:
        if self._process.poll() is not None:
            return

        LOGGER.warning(
            "agent CLI process did not terminate in time. handle_id=%s pid=%s",
            self.handle_id,
            self.pid,
        )
        try:
            _terminate_process_tree(self._process, force=True)
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
            if lines_since_flush >= _ARTIFACT_FILE_FLUSH_LINE_INTERVAL:
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
                "completed_at": _serialize_datetime(result.completed_at),
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
            _write_json_file(self._artifacts.launch_metadata_path, completion_metadata)
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


RunningCodexProcess = RunningAgentProcess


class AgentCliProcessRunner:
    """Prepare, launch, monitor, and cancel provider-backed CLI executions."""

    def __init__(
        self,
        artifacts_root: str | Path,
        *,
        adapter: AgentCliAdapter,
        popen_factory: Callable[..., PopenLike] | None = None,
    ) -> None:
        self._artifacts_root = Path(artifacts_root).resolve()
        self._adapter = adapter
        self._popen_factory = popen_factory or subprocess.Popen
        self._active_handles: dict[str, RunningAgentProcess] = {}

    def validate(self, request: SupportsAgentExecutionRequest) -> str | None:
        """Return a user-facing configuration issue when execution cannot start."""
        adapter = self._select_adapter(request)
        if adapter is None:
            return "선택한 실행기는 아직 자동 실행을 지원하지 않습니다."
        return adapter.validate(request)

    def launch(
        self,
        request: SupportsAgentExecutionRequest,
        *,
        on_stdout_line: Callable[[str], None] | None = None,
        on_stderr_line: Callable[[str], None] | None = None,
        on_json_event: Callable[[AgentStreamEvent], None] | None = None,
        on_handle_created: Callable[[RunningAgentProcess], None] | None = None,
    ) -> RunningAgentProcess:
        """Launch one agent CLI subprocess and return its control handle."""
        adapter = self._select_adapter(request)
        if adapter is None:
            raise ValueError("선택한 실행기는 아직 자동 실행을 지원하지 않습니다.")

        validation_issue = adapter.validate(request)
        if validation_issue is not None:
            raise ValueError(validation_issue)

        started_at = utc_now()
        artifacts = self._build_artifact_paths(
            request.job_id,
            started_at=started_at,
            file_logging_enabled=request.operational_settings.file_logging_enabled,
        )
        command = adapter.build_command(
            request,
            last_message_path=artifacts.last_message_path,
        )
        process_cwd = _resolve_workspace_cwd(request.workspace_path)
        launch_metadata = self._build_launch_metadata(
            adapter=adapter,
            request=request,
            command=command,
            artifacts=artifacts,
            started_at=started_at,
            process_cwd=process_cwd,
        )
        try:
            self._prepare_launch_artifacts(
                artifacts=artifacts,
                prompt=request.prompt,
                launch_metadata=launch_metadata,
                file_logging_enabled=request.operational_settings.file_logging_enabled,
            )
        except OSError as exc:
            raise self._build_artifact_storage_launch_error(
                adapter=adapter,
                request=request,
                command=command,
                artifacts=artifacts,
                launch_metadata=launch_metadata,
                started_at=started_at,
                error=exc,
                file_logging_enabled=request.operational_settings.file_logging_enabled,
            ) from exc

        try:
            process = self._popen_factory(
                command,
                **adapter.build_popen_kwargs(process_cwd),
            )
        except OSError as exc:
            LOGGER.exception(
                "Failed to start agent CLI process. provider=%s job_id=%s",
                adapter.provider_id,
                request.job_id,
            )
            if request.operational_settings.file_logging_enabled:
                _try_append_text_file(
                    artifacts.stderr_log_path,
                    f"PROCESS_START_ERROR: {exc}\n",
                    context="record agent CLI process start error",
                    job_id=request.job_id,
                )
            result = AgentRunResult(
                status=AgentRunStatus.FAILED,
                command=command,
                artifacts=artifacts,
                failure_reason=f"{adapter.display_name} 프로세스를 시작하지 못했습니다: {exc}",
                started_at=started_at,
                completed_at=utc_now(),
            )
            failed_metadata = dict(launch_metadata)
            failed_metadata.update(
                {
                    "completed_at": _serialize_datetime(result.completed_at),
                    "result_status": result.status.value,
                    "failure_reason": result.failure_reason,
                    "start_error": str(exc),
                }
            )
            if request.operational_settings.file_logging_enabled:
                _try_write_json_file(
                    artifacts.launch_metadata_path,
                    failed_metadata,
                    context="record agent CLI process start failure metadata",
                    job_id=request.job_id,
                )
            artifacts.cleanup_ephemeral()
            raise ProcessLaunchError("Failed to start agent CLI process.", result=result) from exc

        handle: RunningAgentProcess | None = None
        handle_registered = False
        try:
            launch_metadata["pid"] = process.pid
            if request.operational_settings.file_logging_enabled:
                _write_json_file(artifacts.launch_metadata_path, launch_metadata)

            handle = RunningAgentProcess(
                request=request,
                command=command,
                process=process,
                artifacts=artifacts,
                started_at=started_at,
                launch_metadata=launch_metadata,
                adapter=adapter.create_run_adapter(),
                on_complete=lambda handle_id: self._active_handles.pop(handle_id, None),
                on_stdout_line=on_stdout_line,
                on_stderr_line=on_stderr_line,
                on_json_event=on_json_event,
            )
            self._active_handles[handle.handle_id] = handle
            handle_registered = True
            handle.start()
            if on_handle_created is not None:
                on_handle_created(handle)
            stdin_payload = adapter.build_stdin_payload(request)
            if stdin_payload is not None:
                self._write_prompt_to_stdin(
                    process.stdin,
                    stdin_payload,
                    artifacts.stderr_log_path,
                    should_stop=lambda: handle.cancel_requested,
                    file_logging_enabled=request.operational_settings.file_logging_enabled,
                )
            return handle
        except Exception:
            self._cleanup_failed_launch(
                request=request,
                process=process,
                handle=handle,
                handle_registered=handle_registered,
            )
            artifacts.cleanup_ephemeral()
            raise

    def cancel(self, handle: RunningAgentProcess) -> None:
        """Cancel one launched agent CLI execution."""
        handle.terminate()

    def run(self, request: SupportsAgentExecutionRequest) -> AgentRunResult:
        """Launch one execution and wait until its final result is available."""
        try:
            handle = self.launch(request)
        except ProcessLaunchError as exc:
            return exc.result
        return handle.wait()

    def _create_artifact_paths(
        self,
        job_id: str,
        *,
        started_at: datetime,
        file_logging_enabled: bool = True,
    ) -> ExecutionArtifactPaths:
        artifacts = self._build_artifact_paths(
            job_id,
            started_at=started_at,
            file_logging_enabled=file_logging_enabled,
        )
        artifacts.root_dir.mkdir(parents=True, exist_ok=True)
        if file_logging_enabled:
            artifacts.stdout_jsonl_path.touch()
            artifacts.stderr_log_path.touch()
        return artifacts

    def _build_artifact_paths(
        self,
        job_id: str,
        *,
        started_at: datetime,
        file_logging_enabled: bool,
    ) -> ExecutionArtifactPaths:
        safe_job_id = _JOB_ID_SANITIZER.sub("-", job_id).strip("-") or "job"
        timestamp = started_at.strftime("%Y%m%dT%H%M%S%fZ")
        ephemeral_cleanup: Callable[[], None] | None = None
        if file_logging_enabled:
            root_dir = self._artifacts_root / safe_job_id / timestamp
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="j3aitaskrunner-artifacts-")
            root_dir = Path(temp_dir.name) / safe_job_id / timestamp
            ephemeral_cleanup = temp_dir.cleanup

        prompt_path = root_dir / "prompt.txt"
        stdout_jsonl_path = root_dir / "stdout.jsonl"
        stderr_log_path = root_dir / "stderr.log"
        last_message_path = root_dir / "last_message.txt"
        launch_metadata_path = root_dir / "launch.json"

        return ExecutionArtifactPaths(
            root_dir=root_dir,
            prompt_path=prompt_path,
            stdout_jsonl_path=stdout_jsonl_path,
            stderr_log_path=stderr_log_path,
            last_message_path=last_message_path,
            launch_metadata_path=launch_metadata_path,
            ephemeral_cleanup=ephemeral_cleanup,
        )

    def _prepare_launch_artifacts(
        self,
        *,
        artifacts: ExecutionArtifactPaths,
        prompt: str,
        launch_metadata: dict[str, Any],
        file_logging_enabled: bool,
    ) -> None:
        artifacts.root_dir.mkdir(parents=True, exist_ok=True)
        if not file_logging_enabled:
            return

        artifacts.stdout_jsonl_path.touch()
        artifacts.stderr_log_path.touch()
        _write_text_file(artifacts.prompt_path, prompt)
        _write_json_file(artifacts.launch_metadata_path, launch_metadata)

    def _build_artifact_storage_launch_error(
        self,
        *,
        adapter: AgentCliAdapter,
        request: SupportsAgentExecutionRequest,
        command: tuple[str, ...],
        artifacts: ExecutionArtifactPaths,
        launch_metadata: dict[str, Any],
        started_at: datetime,
        error: OSError,
        file_logging_enabled: bool,
    ) -> ProcessLaunchError:
        LOGGER.exception(
            "Failed to prepare agent CLI launch artifacts. provider=%s job_id=%s root_dir=%s",
            adapter.provider_id,
            request.job_id,
            artifacts.root_dir,
        )
        storage_label = "실행 로그" if file_logging_enabled else "실행 임시 파일"
        failure_reason = (
            f"{storage_label}을 준비하지 못했습니다. "
            f"권한, 용량, 경로를 확인하세요. 원인: {error}"
        )
        completed_at = utc_now()
        result = AgentRunResult(
            status=AgentRunStatus.FAILED,
            command=command,
            artifacts=artifacts,
            failure_reason=failure_reason,
            started_at=started_at,
            completed_at=completed_at,
        )
        if file_logging_enabled:
            _try_append_text_file(
                artifacts.stderr_log_path,
                f"ARTIFACT_STORAGE_ERROR: {type(error).__name__}: {error}\n",
                context="record agent CLI artifact storage failure",
                job_id=request.job_id,
            )
        failed_metadata = dict(launch_metadata)
        failed_metadata.update(
            {
                "completed_at": _serialize_datetime(completed_at),
                "result_status": result.status.value,
                "failure_reason": failure_reason,
                "artifact_error_type": type(error).__name__,
                "artifact_error": str(error),
            }
        )
        if file_logging_enabled:
            _try_write_json_file(
                artifacts.launch_metadata_path,
                failed_metadata,
                context="record agent CLI artifact storage failure metadata",
                job_id=request.job_id,
            )
        artifacts.cleanup_ephemeral()
        return ProcessLaunchError("Failed to prepare agent CLI launch artifacts.", result=result)

    def _build_launch_metadata(
        self,
        *,
        adapter: AgentCliAdapter,
        request: SupportsAgentExecutionRequest,
        command: tuple[str, ...],
        artifacts: ExecutionArtifactPaths,
        started_at: datetime,
        process_cwd: str,
    ) -> dict[str, Any]:
        return adapter.build_launch_metadata(
            request=request,
            command=command,
            artifacts=artifacts,
            started_at=started_at,
            process_cwd=process_cwd,
        )

    def _select_adapter(
        self,
        request: SupportsAgentExecutionRequest,
    ) -> AgentCliAdapter | None:
        return self._adapter

    def _cleanup_failed_launch(
        self,
        *,
        request: SupportsAgentExecutionRequest,
        process: PopenLike,
        handle: RunningAgentProcess | None,
        handle_registered: bool,
    ) -> None:
        if handle_registered and handle is not None:
            self._active_handles.pop(handle.handle_id, None)

        close_method = getattr(process.stdin, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception:
                LOGGER.exception(
                    "Failed to close agent CLI stdin during launch cleanup. job_id=%s pid=%s",
                    request.job_id,
                    process.pid,
                )

        if process.poll() is not None:
            return

        try:
            _terminate_process_tree(process, force=False)
        except Exception:
            if process.poll() is None:
                LOGGER.exception(
                    "Failed to terminate agent CLI process during launch cleanup. job_id=%s pid=%s",
                    request.job_id,
                    process.pid,
                )

        try:
            process.wait(timeout=_DEFAULT_TERMINATE_TIMEOUT_SECONDS)
            return
        except subprocess.TimeoutExpired:
            LOGGER.warning(
                "agent CLI process did not terminate during launch cleanup. job_id=%s pid=%s",
                request.job_id,
                process.pid,
            )
        except Exception:
            if process.poll() is not None:
                return
            LOGGER.exception(
                "Failed while waiting for agent CLI process during launch cleanup. job_id=%s pid=%s",
                request.job_id,
                process.pid,
            )

        if process.poll() is None:
            try:
                _terminate_process_tree(process, force=True)
            except Exception:
                if process.poll() is None:
                    LOGGER.exception(
                        "Failed to kill agent CLI process during launch cleanup. job_id=%s pid=%s",
                        request.job_id,
                        process.pid,
                    )
                    return

        if process.poll() is None:
            try:
                process.wait(timeout=_DEFAULT_TERMINATE_TIMEOUT_SECONDS)
            except Exception:
                if process.poll() is None:
                    LOGGER.exception(
                        "Failed while waiting after killing agent CLI process during launch cleanup. job_id=%s pid=%s",
                        request.job_id,
                        process.pid,
                    )

    @staticmethod
    def _write_prompt_to_stdin(
        stream: IO[str] | None,
        prompt: str,
        stderr_log_path: Path,
        *,
        should_stop: Callable[[], bool] | None = None,
        file_logging_enabled: bool = True,
    ) -> None:
        if stream is None:
            return

        try:
            if _should_stop_stdin_write(should_stop):
                _append_stdin_diagnostic(
                    stderr_log_path,
                    "STDIN_WRITE_CANCELED: cancellation requested before prompt write.\n",
                    file_logging_enabled=file_logging_enabled,
                )
                return

            if not prompt:
                stream.flush()
                return

            for offset in range(0, len(prompt), _STDIN_WRITE_CHUNK_SIZE):
                if _should_stop_stdin_write(should_stop):
                    _append_stdin_diagnostic(
                        stderr_log_path,
                        "STDIN_WRITE_CANCELED: cancellation requested during prompt write.\n",
                        file_logging_enabled=file_logging_enabled,
                    )
                    return
                stream.write(prompt[offset : offset + _STDIN_WRITE_CHUNK_SIZE])
            stream.flush()
        except (OSError, ValueError) as exc:
            if _should_stop_stdin_write(should_stop):
                LOGGER.info(
                    "Stopped writing prompt to agent CLI stdin after cancellation.",
                    exc_info=exc,
                )
                _append_stdin_diagnostic(
                    stderr_log_path,
                    f"STDIN_WRITE_CANCELED: {exc}\n",
                    file_logging_enabled=file_logging_enabled,
                )
                return
            LOGGER.warning("Failed to write prompt to agent CLI stdin.", exc_info=exc)
            _append_stdin_diagnostic(
                stderr_log_path,
                f"STDIN_WRITE_ERROR: {exc}\n",
                file_logging_enabled=file_logging_enabled,
            )
        finally:
            close_method = getattr(stream, "close", None)
            if callable(close_method):
                try:
                    close_method()
                except Exception as exc:
                    LOGGER.warning("Failed to close agent CLI stdin.", exc_info=exc)
                    _append_stdin_diagnostic(
                        stderr_log_path,
                        f"STDIN_CLOSE_ERROR: {exc}\n",
                        file_logging_enabled=file_logging_enabled,
                    )


class ProviderAgentCliProcessRunner(AgentCliProcessRunner):
    """Runner that selects the provider adapter from each request's settings."""

    def __init__(
        self,
        artifacts_root: str | Path,
        *,
        popen_factory: Callable[..., PopenLike] | None = None,
    ) -> None:
        super().__init__(
            artifacts_root,
            adapter=CodexCliAdapter(),
            popen_factory=popen_factory,
        )

    def _select_adapter(
        self,
        request: SupportsAgentExecutionRequest,
    ) -> AgentCliAdapter | None:
        return build_agent_cli_adapter(request.operational_settings.agent_provider)


class ClaudeCodeCliProcessRunner(AgentCliProcessRunner):
    """Runner wired to the Claude Code CLI provider adapter."""

    def __init__(
        self,
        artifacts_root: str | Path,
        *,
        popen_factory: Callable[..., PopenLike] | None = None,
    ) -> None:
        super().__init__(
            artifacts_root,
            adapter=ClaudeCodeCliAdapter(),
            popen_factory=popen_factory,
        )


class OpenCodeCliProcessRunner(AgentCliProcessRunner):
    """Runner wired to the OpenCode CLI provider adapter."""

    def __init__(
        self,
        artifacts_root: str | Path,
        *,
        popen_factory: Callable[..., PopenLike] | None = None,
    ) -> None:
        super().__init__(
            artifacts_root,
            adapter=OpenCodeCliAdapter(),
            popen_factory=popen_factory,
        )


class KiloCodeCliProcessRunner(AgentCliProcessRunner):
    """Runner wired to the Kilo Code CLI provider adapter."""

    def __init__(
        self,
        artifacts_root: str | Path,
        *,
        popen_factory: Callable[..., PopenLike] | None = None,
    ) -> None:
        super().__init__(
            artifacts_root,
            adapter=KiloCodeCliAdapter(),
            popen_factory=popen_factory,
        )


class PiCliProcessRunner(AgentCliProcessRunner):
    """Runner wired to the Pi Coding Agent CLI provider adapter."""

    def __init__(
        self,
        artifacts_root: str | Path,
        *,
        popen_factory: Callable[..., PopenLike] | None = None,
    ) -> None:
        super().__init__(
            artifacts_root,
            adapter=PiCliAdapter(),
            popen_factory=popen_factory,
        )


class CodexCliProcessRunner(AgentCliProcessRunner):
    """Compatibility runner wired to the Codex CLI provider adapter."""

    def __init__(
        self,
        artifacts_root: str | Path,
        *,
        popen_factory: Callable[..., PopenLike] | None = None,
    ) -> None:
        super().__init__(
            artifacts_root,
            adapter=CodexCliAdapter(),
            popen_factory=popen_factory,
        )


def _append_stdin_diagnostic(
    stderr_log_path: Path,
    message: str,
    *,
    file_logging_enabled: bool,
) -> None:
    if file_logging_enabled:
        _append_text_file(stderr_log_path, message)


def _terminate_process_tree(
    process: PopenLike,
    *,
    force: bool,
    os_name: str | None = None,
) -> None:
    """Terminate the process tree when the platform exposes a safe primitive."""
    platform_name = os_name or os.name
    pid = process.pid

    if platform_name == "nt":
        if pid is not None:
            descendant_pids = _collect_windows_descendant_pids(pid)
            if _kill_windows_process_tree(pid, force=force):
                return
            _kill_windows_descendant_processes(descendant_pids, force=True)
        _terminate_single_process(process, force=force)
        return

    if pid is not None and _signal_posix_process_tree(pid, force=force):
        return

    _terminate_single_process(process, force=force)


def _terminate_single_process(process: PopenLike, *, force: bool) -> None:
    if force:
        process.kill()
        return
    process.terminate()


def _kill_windows_process_tree(
    pid: int,
    *,
    force: bool,
    run: Callable[..., subprocess.CompletedProcess[Any]] | None = None,
) -> bool:
    runner = run or subprocess.run
    command = ("taskkill", "/PID", str(pid), "/T")
    if force:
        command = (*command, "/F")
    try:
        completed = runner(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_WINDOWS_TASKKILL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        LOGGER.warning(
            "Failed to run Windows process tree termination helper. pid=%s force=%s",
            pid,
            force,
            exc_info=True,
        )
        return False

    if completed.returncode == 0:
        return True

    LOGGER.warning(
        "Windows process tree termination helper returned non-zero exit code. pid=%s force=%s exit_code=%s",
        pid,
        force,
        completed.returncode,
    )
    return False


def _kill_windows_descendant_processes(descendant_pids: tuple[int, ...], *, force: bool) -> None:
    for descendant_pid in reversed(descendant_pids):
        _kill_windows_process_tree(descendant_pid, force=force)


def _collect_windows_descendant_pids(pid: int) -> tuple[int, ...]:
    if os.name != "nt":
        return ()

    try:
        process_parents = _snapshot_windows_process_parents()
    except Exception:
        LOGGER.warning(
            "Failed to snapshot Windows process tree. pid=%s",
            pid,
            exc_info=True,
        )
        return ()

    descendants: list[int] = []
    pending_parent_pids = [pid]
    while pending_parent_pids:
        parent_pid = pending_parent_pids.pop()
        child_pids = sorted(
            child_pid
            for child_pid, child_parent_pid in process_parents.items()
            if child_parent_pid == parent_pid and child_pid not in descendants
        )
        descendants.extend(child_pids)
        pending_parent_pids.extend(child_pids)
    return tuple(descendants)


def _snapshot_windows_process_parents() -> dict[int, int]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")

    parents: dict[int, int] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        has_entry = process_first(snapshot, ctypes.byref(entry))
        while has_entry:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            has_entry = process_next(snapshot, ctypes.byref(entry))
    finally:
        close_handle(snapshot)
    return parents


def _signal_posix_process_tree(pid: int, *, force: bool) -> bool:
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        process_group_id = os.getpgid(pid)
        if process_group_id == os.getpgrp():
            LOGGER.warning(
                "Refusing to signal current POSIX process group. pid=%s signal=%s",
                pid,
                sig,
            )
            return False
        os.killpg(process_group_id, sig)
        return True
    except ProcessLookupError:
        return True
    except (AttributeError, OSError):
        LOGGER.warning(
            "Failed to signal POSIX process group. pid=%s signal=%s",
            pid,
            sig,
            exc_info=True,
        )
        return False


def _build_codex_popen_kwargs(process_cwd: str, *, os_name: str | None = None) -> dict[str, Any]:
    return build_codex_popen_kwargs(process_cwd, os_name=os_name)


def _build_codex_environment() -> dict[str, str]:
    return build_codex_environment()


def _hidden_console_creationflags(*, os_name: str | None = None) -> int:
    return hidden_console_creationflags(os_name=os_name)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _minutes_to_seconds(value: int) -> float | None:
    if not is_execution_control_limit_enabled(value):
        return None
    bounded_value = _bounded_execution_control_value(
        value,
        max_value=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
        field_name="timeout_minutes",
    )
    return float(bounded_value * 60)


def _termination_timeout_from_settings(settings: AppSettings) -> float | None:
    if settings.termination_grace_seconds < 0:
        return None
    bounded_value = _bounded_execution_control_value(
        settings.termination_grace_seconds,
        max_value=TERMINATION_GRACE_SECONDS_MAX,
        field_name="termination_grace_seconds",
    )
    return float(bounded_value)


def _bounded_execution_control_value(
    value: int,
    *,
    max_value: int,
    field_name: str,
) -> int:
    if value <= max_value:
        return value
    LOGGER.warning(
        "Execution control setting exceeded the maximum; using maximum. field=%s value=%s max=%s",
        field_name,
        value,
        max_value,
    )
    return max_value


def _write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)


def _try_append_text_file(path: Path, content: str, *, context: str, job_id: str) -> None:
    try:
        _append_text_file(path, content)
    except OSError:
        LOGGER.exception(
            "Failed to %s. job_id=%s path=%s",
            context,
            job_id,
            path,
        )


def _should_stop_stdin_write(should_stop: Callable[[], bool] | None) -> bool:
    if should_stop is None:
        return False
    try:
        return should_stop()
    except Exception:
        LOGGER.exception("Failed to check agent CLI stdin cancellation state.")
        return False


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _try_write_json_file(path: Path, payload: dict[str, Any], *, context: str, job_id: str) -> None:
    try:
        _write_json_file(path, payload)
    except OSError:
        LOGGER.exception(
            "Failed to %s. job_id=%s path=%s",
            context,
            job_id,
            path,
        )


def _resolve_workspace_cwd(workspace_path: str | None) -> str:
    normalized_path = _require_text(workspace_path, field_name="workspace_path")
    return str(Path(normalized_path).resolve())


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized:
        return normalized
    return None


def _require_text(value: str | None, *, field_name: str) -> str:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        raise ValueError(f"{field_name} must not be blank.")
    return normalized

