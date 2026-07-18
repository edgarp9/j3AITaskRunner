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


from .process_runner import ProcessLaunchError, build_agent_cli_adapter
from .process_runner_process import RunningAgentProcess

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
        process_cwd = _process_runner_global("_resolve_workspace_cwd")(request.workspace_path)
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
                _process_runner_global("_try_append_text_file")(
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
                    "completed_at": _process_runner_global("_serialize_datetime")(
                        result.completed_at
                    ),
                    "result_status": result.status.value,
                    "failure_reason": result.failure_reason,
                    "start_error": str(exc),
                }
            )
            if request.operational_settings.file_logging_enabled:
                _process_runner_global("_try_write_json_file")(
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
                _process_runner_global("_write_json_file")(artifacts.launch_metadata_path, launch_metadata)

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
        safe_job_id = _process_runner_global("_JOB_ID_SANITIZER").sub("-", job_id).strip("-") or "job"
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
        _process_runner_global("_write_text_file")(artifacts.prompt_path, prompt)
        _process_runner_global("_write_json_file")(artifacts.launch_metadata_path, launch_metadata)

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
            _process_runner_global("_try_append_text_file")(
                artifacts.stderr_log_path,
                f"ARTIFACT_STORAGE_ERROR: {type(error).__name__}: {error}\n",
                context="record agent CLI artifact storage failure",
                job_id=request.job_id,
            )
        failed_metadata = dict(launch_metadata)
        failed_metadata.update(
            {
                "completed_at": _process_runner_global("_serialize_datetime")(
                    completed_at
                ),
                "result_status": result.status.value,
                "failure_reason": failure_reason,
                "artifact_error_type": type(error).__name__,
                "artifact_error": str(error),
            }
        )
        if file_logging_enabled:
            _process_runner_global("_try_write_json_file")(
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
            _process_runner_global("_terminate_process_tree")(process, force=False)
        except Exception:
            if process.poll() is None:
                LOGGER.exception(
                    "Failed to terminate agent CLI process during launch cleanup. job_id=%s pid=%s",
                    request.job_id,
                    process.pid,
                )

        try:
            process.wait(
                timeout=_process_runner_global("_DEFAULT_TERMINATE_TIMEOUT_SECONDS")
            )
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
                _process_runner_global("_terminate_process_tree")(process, force=True)
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
                process.wait(
                    timeout=_process_runner_global("_DEFAULT_TERMINATE_TIMEOUT_SECONDS")
                )
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
            if _process_runner_global("_should_stop_stdin_write")(should_stop):
                _process_runner_global("_append_stdin_diagnostic")(
                    stderr_log_path,
                    "STDIN_WRITE_CANCELED: cancellation requested before prompt write.\n",
                    file_logging_enabled=file_logging_enabled,
                )
                return

            if not prompt:
                stream.flush()
                return

            for offset in range(0, len(prompt), _process_runner_global("_STDIN_WRITE_CHUNK_SIZE")):
                if _process_runner_global("_should_stop_stdin_write")(should_stop):
                    _process_runner_global("_append_stdin_diagnostic")(
                        stderr_log_path,
                        "STDIN_WRITE_CANCELED: cancellation requested during prompt write.\n",
                        file_logging_enabled=file_logging_enabled,
                    )
                    return
                stream.write(prompt[offset : offset + _process_runner_global("_STDIN_WRITE_CHUNK_SIZE")])
            stream.flush()
        except (OSError, ValueError) as exc:
            if _process_runner_global("_should_stop_stdin_write")(should_stop):
                LOGGER.info(
                    "Stopped writing prompt to agent CLI stdin after cancellation.",
                    exc_info=exc,
                )
                _process_runner_global("_append_stdin_diagnostic")(
                    stderr_log_path,
                    f"STDIN_WRITE_CANCELED: {exc}\n",
                    file_logging_enabled=file_logging_enabled,
                )
                return
            LOGGER.warning("Failed to write prompt to agent CLI stdin.", exc_info=exc)
            _process_runner_global("_append_stdin_diagnostic")(
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
                    _process_runner_global("_append_stdin_diagnostic")(
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

