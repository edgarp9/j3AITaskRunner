"""OpenCode-family CLI provider adapters."""

from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import subprocess
from typing import Any

from domain import normalize_agent_provider

from .agent_contract import (
    AgentParseSummary,
    AgentRunStatus,
    AgentStreamEvent,
    ExecutionArtifactPaths,
    SupportsAgentExecutionRequest,
)
from .executable import executable_command_for_launch, resolve_executable_reference
from .open_code_jsonl import OpenCodeJsonlEvent, OpenCodeJsonlParser
from .subprocess_options import hidden_console_creationflags

LOGGER = logging.getLogger(__name__)

_ARGUMENT_PROMPT_WARNING_THRESHOLD = 24_000


class OpenCodeLikeCliAdapter:
    """Shared adapter for CLIs that follow the OpenCode ``run`` contract."""

    provider_id: str = ""
    display_name: str = ""

    def __init__(self, *, parser: OpenCodeJsonlParser | None = None) -> None:
        self._parser = parser or OpenCodeJsonlParser()

    def create_run_adapter(self) -> "OpenCodeLikeCliAdapter":
        """Return a fresh adapter instance for one stdout stream."""
        return type(self)()

    def validate(self, request: SupportsAgentExecutionRequest) -> str | None:
        """Return a user-facing configuration issue when the CLI cannot start."""
        if normalize_agent_provider(request.operational_settings.agent_provider) != self.provider_id:
            return "선택한 실행기는 아직 자동 실행을 지원하지 않습니다."

        executable_path = _normalize_optional_text(request.operational_settings.executable_path)
        if executable_path is None:
            return "실행기 경로를 설정하세요."

        if resolve_executable_reference(
            executable_path,
            agent_provider=self.provider_id,
        ) is None:
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
        """Build the OpenCode-family non-interactive run command."""
        executable_path = executable_command_for_launch(
            _require_text(request.operational_settings.executable_path, field_name="executable_path"),
            agent_provider=self.provider_id,
        )
        workspace_path = _require_text(request.workspace_path, field_name="workspace_path")
        session_id = _normalize_optional_text(request.session_id)
        model = _normalize_optional_text(request.execution_options.model)
        variant = _normalize_optional_text(request.execution_options.reasoning_effort)
        prompt = _require_text(request.prompt, field_name="prompt")

        self._log_argument_prompt_delivery(prompt)

        command: list[str] = [
            executable_path,
            "run",
            "--format",
            "json",
            "--dir",
            workspace_path,
        ]
        if session_id is not None:
            command.extend(["--session", session_id])
        if model is not None:
            command.extend(["--model", model])
        if variant is not None:
            command.extend(["--variant", variant])
        command.append(prompt)
        return tuple(command)

    def build_popen_kwargs(
        self,
        process_cwd: str,
        *,
        os_name: str | None = None,
    ) -> dict[str, Any]:
        """Build subprocess options for OpenCode-family CLI execution."""
        platform_name = os_name or os.name
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
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

    @staticmethod
    def build_environment() -> dict[str, str]:
        """Return environment values for non-interactive execution."""
        env = dict(os.environ)
        env["CI"] = "1"
        env["NO_COLOR"] = "1"
        return env

    def build_stdin_payload(self, request: SupportsAgentExecutionRequest) -> str | None:
        """Return no stdin payload because prompt delivery uses a CLI argument."""
        return None

    def feed_stdout_line(self, raw_line: str) -> AgentStreamEvent | None:
        """Parse one raw JSON event line."""
        event = self._parser.feed_line(raw_line)
        if event is None:
            return None
        return _agent_event_from_open_code_event(event)

    def build_parse_summary(self) -> AgentParseSummary:
        """Return the current OpenCode-family JSON parse summary."""
        summary = self._parser.build_summary()
        return AgentParseSummary(
            thread_id=summary.thread_id,
            saw_turn_completed=summary.saw_turn_completed,
            turn_failed_events=tuple(
                _agent_event_from_open_code_event(event)
                for event in summary.turn_failed_events
            ),
            error_events=tuple(
                _agent_event_from_open_code_event(event) for event in summary.error_events
            ),
            malformed_lines=summary.malformed_lines,
            total_events=summary.total_events,
        )

    def extract_session_id(self, parser_summary: AgentParseSummary) -> str | None:
        """Return the OpenCode-family session id as the app session id."""
        return parser_summary.thread_id

    def read_last_message(self, artifacts: ExecutionArtifactPaths) -> str | None:
        """Read the final response extracted from JSON events."""
        last_message = self._parser.build_summary().last_message
        if last_message is not None:
            try:
                artifacts.last_message_path.parent.mkdir(parents=True, exist_ok=True)
                artifacts.last_message_path.write_text(last_message, encoding="utf-8")
            except OSError:
                LOGGER.exception(
                    "Failed to write OpenCode-family last message artifact. path=%s",
                    artifacts.last_message_path,
                )
            return last_message

        try:
            return artifacts.last_message_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            LOGGER.exception(
                "Failed to read OpenCode-family last message artifact. path=%s",
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
        """Resolve execution status from JSON events, exit code, and response text."""
        if timeout_failure_reason is not None:
            return AgentRunStatus.FAILED, timeout_failure_reason

        if cancel_requested:
            return AgentRunStatus.CANCELED, "사용자가 실행을 취소했습니다."

        if parser_summary.turn_failed_events:
            first_failed_turn = parser_summary.turn_failed_events[0]
            return (
                AgentRunStatus.FAILED,
                first_failed_turn.message
                or f"{self.display_name} turn.failed 이벤트를 확인했습니다.",
            )

        if exit_code != 0:
            return AgentRunStatus.FAILED, (
                f"{self.display_name}가 비정상 종료했습니다. exit_code={exit_code}"
            )

        if not parser_summary.saw_turn_completed:
            if parser_summary.error_events:
                first_error = parser_summary.error_events[0]
                return (
                    AgentRunStatus.FAILED,
                    first_error.message or f"{self.display_name} error 이벤트를 확인했습니다.",
                )
            return AgentRunStatus.FAILED, "turn.completed 이벤트를 확인하지 못했습니다."

        if last_message is None:
            return AgentRunStatus.FAILED, "마지막 응답 JSON 이벤트를 확인하지 못했습니다."

        if parser_summary.error_events:
            first_error = parser_summary.error_events[0]
            LOGGER.info(
                "%s emitted error events but completed the turn; treating them as diagnostic. "
                "error_event_count=%s first_error=%s",
                self.display_name,
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
        """Build OpenCode-family launch metadata."""
        session_id = _normalize_optional_text(request.session_id)
        model = _normalize_optional_text(request.execution_options.model)
        variant = _normalize_optional_text(request.execution_options.reasoning_effort)
        return {
            "job_id": request.job_id,
            "provider_id": self.provider_id,
            "provider_name": self.display_name,
            "workspace_path": request.workspace_path,
            "process_cwd": process_cwd,
            "session_id": session_id,
            "mode": "resume" if session_id is not None else "run",
            "started_at": _serialize_datetime(started_at),
            "command": list(command),
            "artifacts": {
                "prompt_path": str(artifacts.prompt_path),
                "stdout_jsonl_path": str(artifacts.stdout_jsonl_path),
                "stderr_log_path": str(artifacts.stderr_log_path),
                "last_message_path": str(artifacts.last_message_path),
                "launch_metadata_path": str(artifacts.launch_metadata_path),
            },
            "prompt_delivery": {
                "method": "argument",
                "stdin_supported_by_docs": False,
                "note": (
                    "Official CLI docs document run message positionals; stdin prompt "
                    "delivery is not relied on. subprocess argv avoids shell quoting, "
                    "but Windows command line length can still limit very long prompts."
                ),
            },
            "applied_settings": {
                "model": model,
                "variant": variant,
                "dangerous_permission_flags_enabled": False,
                "execution_timeout_minutes": request.operational_settings.execution_timeout_minutes,
                "inactivity_timeout_minutes": request.operational_settings.inactivity_timeout_minutes,
                "termination_grace_seconds": request.operational_settings.termination_grace_seconds,
                "file_logging_enabled": request.operational_settings.file_logging_enabled,
            },
        }

    def build_version_command(self, executable_reference: str | None) -> tuple[str, ...]:
        """Return the provider's default version query command."""
        executable_path = executable_command_for_launch(
            _require_text(executable_reference, field_name="executable_path"),
            agent_provider=self.provider_id,
        )
        return (executable_path, "--version")

    def _log_argument_prompt_delivery(self, prompt: str) -> None:
        if len(prompt) > _ARGUMENT_PROMPT_WARNING_THRESHOLD:
            LOGGER.warning(
                "%s prompt is being passed as a command argument because documented stdin "
                "prompt delivery is unavailable. prompt_length=%s risk=windows_command_line_limit",
                self.display_name,
                len(prompt),
            )
            return

        LOGGER.info(
            "%s prompt is being passed as a command argument because documented stdin "
            "prompt delivery is unavailable. prompt_length=%s",
            self.display_name,
            len(prompt),
        )


class OpenCodeCliAdapter(OpenCodeLikeCliAdapter):
    """Provider adapter for the OpenCode CLI."""

    provider_id = "opencode"
    display_name = "OpenCode"


class KiloCodeCliAdapter(OpenCodeLikeCliAdapter):
    """Provider adapter for the Kilo Code CLI."""

    provider_id = "kilo_code"
    display_name = "Kilo Code"


def build_opencode_command(
    request: SupportsAgentExecutionRequest,
    *,
    last_message_path: str | Path,
) -> tuple[str, ...]:
    """Build the OpenCode CLI command for one request."""
    return OpenCodeCliAdapter().build_command(
        request,
        last_message_path=last_message_path,
    )


def build_kilo_code_command(
    request: SupportsAgentExecutionRequest,
    *,
    last_message_path: str | Path,
) -> tuple[str, ...]:
    """Build the Kilo Code CLI command for one request."""
    return KiloCodeCliAdapter().build_command(
        request,
        last_message_path=last_message_path,
    )


def _agent_event_from_open_code_event(event: OpenCodeJsonlEvent) -> AgentStreamEvent:
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
