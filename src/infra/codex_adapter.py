"""Codex CLI provider adapter."""

from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import subprocess
from typing import Any

from domain import DEFAULT_AGENT_PROVIDER, normalize_agent_provider

from .agent_contract import (
    AgentParseSummary,
    AgentRunStatus,
    AgentStreamEvent,
    ExecutionArtifactPaths,
    SupportsAgentExecutionRequest,
)
from .codex_jsonl import CodexJsonlEvent, CodexJsonlParser
from .executable import executable_command_for_launch, resolve_executable_reference
from .subprocess_options import hidden_console_creationflags as _hidden_console_creationflags

LOGGER = logging.getLogger(__name__)


class CodexCliAdapter:
    """Provider adapter that preserves the current Codex CLI execution contract."""

    provider_id = DEFAULT_AGENT_PROVIDER
    display_name = "Codex CLI"

    def __init__(self, *, parser: CodexJsonlParser | None = None) -> None:
        self._parser = parser or CodexJsonlParser()

    def create_run_adapter(self) -> "CodexCliAdapter":
        """Return a fresh adapter instance for one Codex stdout stream."""
        return CodexCliAdapter()

    def validate(self, request: SupportsAgentExecutionRequest) -> str | None:
        """Return a user-facing configuration issue when Codex cannot start."""
        if normalize_agent_provider(request.operational_settings.agent_provider) != DEFAULT_AGENT_PROVIDER:
            return "선택한 실행기는 아직 자동 실행을 지원하지 않습니다."

        executable_path = _normalize_optional_text(request.operational_settings.executable_path)
        if executable_path is None:
            return "실행기 경로를 설정하세요."

        if resolve_executable_reference(executable_path) is None:
            return "실행기 경로를 확인하세요."

        workspace_path = _normalize_optional_text(request.workspace_path)
        if workspace_path is None:
            return "워크스페이스 경로를 확인하세요."

        workspace = Path(workspace_path)
        if not workspace.exists() or not workspace.is_dir():
            return "워크스페이스 경로를 확인하세요."

        return None

    def build_command(
        self,
        request: SupportsAgentExecutionRequest,
        *,
        last_message_path: str | Path,
    ) -> tuple[str, ...]:
        """Build the exact Codex CLI command for one request."""
        executable_path = executable_command_for_launch(
            _require_text(request.operational_settings.executable_path, field_name="executable_path")
        )
        workspace_path = _require_text(request.workspace_path, field_name="workspace_path")
        session_id = _normalize_optional_text(request.session_id)
        model = _normalize_optional_text(request.execution_options.model)
        reasoning_effort = _normalize_optional_text(
            request.execution_options.reasoning_effort
        )
        last_message_arg = str(Path(last_message_path))

        command: list[str] = [executable_path, "exec"]
        if session_id is None:
            command.extend(
                [
                    "--json",
                    "--skip-git-repo-check",
                    "-C",
                    workspace_path,
                ]
            )
        else:
            command.extend(
                [
                    "resume",
                    "--json",
                    "--skip-git-repo-check",
                    session_id,
                ]
            )

        if model is not None:
            command.extend(["-m", model])
        if reasoning_effort is not None:
            command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        command.extend(["-o", last_message_arg, "-"])
        return tuple(command)

    def build_popen_kwargs(
        self,
        process_cwd: str,
        *,
        os_name: str | None = None,
    ) -> dict[str, Any]:
        """Build subprocess options used for Codex CLI execution."""
        platform_name = os_name or os.name
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "cwd": process_cwd,
            "env": self.build_environment(),
        }
        if platform_name != "nt":
            kwargs["start_new_session"] = True
        creationflags = hidden_console_creationflags(os_name=platform_name)
        if creationflags:
            kwargs["creationflags"] = creationflags
        return kwargs

    def build_stdin_payload(self, request: SupportsAgentExecutionRequest) -> str | None:
        """Return the Codex prompt payload written to stdin."""
        return request.prompt

    @staticmethod
    def build_environment() -> dict[str, str]:
        """Return Codex CLI environment values for non-interactive execution."""
        env = dict(os.environ)
        env["CI"] = "1"
        env["NO_COLOR"] = "1"
        return env

    def feed_stdout_line(self, raw_line: str) -> AgentStreamEvent | None:
        """Parse one Codex JSONL stdout line."""
        event = self._parser.feed_line(raw_line)
        if event is None:
            return None
        return _agent_event_from_codex_event(event)

    def build_parse_summary(self) -> AgentParseSummary:
        """Return the current Codex JSONL parse summary."""
        summary = self._parser.build_summary()
        return AgentParseSummary(
            thread_id=summary.thread_id,
            saw_turn_completed=summary.saw_turn_completed,
            turn_failed_events=tuple(
                _agent_event_from_codex_event(event) for event in summary.turn_failed_events
            ),
            error_events=tuple(
                _agent_event_from_codex_event(event) for event in summary.error_events
            ),
            malformed_lines=summary.malformed_lines,
            total_events=summary.total_events,
        )

    def extract_session_id(self, parser_summary: AgentParseSummary) -> str | None:
        """Return Codex thread id as the app session id."""
        return parser_summary.thread_id

    def read_last_message(self, artifacts: ExecutionArtifactPaths) -> str | None:
        """Read Codex's final response file."""
        try:
            return artifacts.last_message_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            LOGGER.exception(
                "Failed to read Codex CLI last message artifact. path=%s",
                artifacts.last_message_path,
            )
            return None

    def resolve_outcome(
        self,
        *,
        parser_summary: AgentParseSummary,
        exit_code: int | None,
        last_message: str | None,
        cancel_requested: bool,
        timeout_failure_reason: str | None,
    ) -> tuple[AgentRunStatus, str | None]:
        """Resolve Codex execution status from JSONL, exit code, and response file."""
        if timeout_failure_reason is not None:
            return AgentRunStatus.FAILED, timeout_failure_reason

        if cancel_requested:
            return AgentRunStatus.CANCELED, "사용자가 실행을 취소했습니다."

        if parser_summary.turn_failed_events:
            first_failed_turn = parser_summary.turn_failed_events[0]
            return (
                AgentRunStatus.FAILED,
                first_failed_turn.message or "Codex CLI turn.failed 이벤트를 확인했습니다.",
            )

        if exit_code != 0:
            return AgentRunStatus.FAILED, f"Codex CLI가 비정상 종료했습니다. exit_code={exit_code}"

        if not parser_summary.saw_turn_completed:
            if parser_summary.error_events:
                first_error = parser_summary.error_events[0]
                return (
                    AgentRunStatus.FAILED,
                    first_error.message or "Codex CLI error 이벤트를 확인했습니다.",
                )
            return AgentRunStatus.FAILED, "turn.completed 이벤트를 확인하지 못했습니다."

        if last_message is None:
            return AgentRunStatus.FAILED, "마지막 응답 파일을 확인하지 못했습니다."

        if parser_summary.error_events:
            first_error = parser_summary.error_events[0]
            LOGGER.info(
                "Codex CLI emitted error events but completed the turn; treating them as diagnostic. "
                "error_event_count=%s first_error=%s",
                len(parser_summary.error_events),
                first_error.message,
            )

        return AgentRunStatus.COMPLETED, None

    def build_launch_metadata(
        self,
        *,
        request: SupportsAgentExecutionRequest,
        command: tuple[str, ...],
        artifacts: ExecutionArtifactPaths,
        started_at: datetime,
        process_cwd: str,
    ) -> dict[str, Any]:
        """Build Codex-specific launch metadata."""
        session_id = _normalize_optional_text(request.session_id)
        model = _normalize_optional_text(request.execution_options.model)
        reasoning_effort = _normalize_optional_text(
            request.execution_options.reasoning_effort
        )
        return {
            "job_id": request.job_id,
            "provider_id": self.provider_id,
            "provider_name": self.display_name,
            "workspace_path": request.workspace_path,
            "process_cwd": process_cwd,
            "session_id": session_id,
            "mode": "resume" if session_id is not None else "exec",
            "started_at": _serialize_datetime(started_at),
            "command": list(command),
            "artifacts": {
                "prompt_path": str(artifacts.prompt_path),
                "stdout_jsonl_path": str(artifacts.stdout_jsonl_path),
                "stderr_log_path": str(artifacts.stderr_log_path),
                "last_message_path": str(artifacts.last_message_path),
                "launch_metadata_path": str(artifacts.launch_metadata_path),
            },
            "applied_settings": {
                "model": model,
                "reasoning_effort": reasoning_effort,
                "execution_timeout_minutes": request.operational_settings.execution_timeout_minutes,
                "inactivity_timeout_minutes": request.operational_settings.inactivity_timeout_minutes,
                "termination_grace_seconds": request.operational_settings.termination_grace_seconds,
                "file_logging_enabled": request.operational_settings.file_logging_enabled,
            },
        }

    def build_version_command(self, executable_reference: str | None) -> tuple[str, ...]:
        """Return the default Codex CLI version query command."""
        executable_path = executable_command_for_launch(
            _require_text(executable_reference, field_name="executable_path")
        )
        return (executable_path, "--version")


def build_codex_command(
    request: SupportsAgentExecutionRequest,
    *,
    last_message_path: str | Path,
) -> tuple[str, ...]:
    """Build the exact Codex CLI command for one request."""
    return CodexCliAdapter().build_command(request, last_message_path=last_message_path)


def build_codex_popen_kwargs(
    process_cwd: str,
    *,
    os_name: str | None = None,
) -> dict[str, Any]:
    """Build subprocess options used for Codex CLI execution."""
    return CodexCliAdapter().build_popen_kwargs(process_cwd, os_name=os_name)


def build_codex_environment() -> dict[str, str]:
    """Return Codex CLI environment values for non-interactive execution."""
    return CodexCliAdapter.build_environment()


def hidden_console_creationflags(*, os_name: str | None = None) -> int:
    """Return Windows creation flags that keep CLI child windows hidden."""
    return _hidden_console_creationflags(os_name=os_name)


def _agent_event_from_codex_event(event: CodexJsonlEvent) -> AgentStreamEvent:
    return AgentStreamEvent(
        line_number=event.line_number,
        event_type=event.event_type,
        payload=event.payload,
        thread_id=event.thread_id,
        message=event.message,
        raw_line=event.raw_line,
    )


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


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
