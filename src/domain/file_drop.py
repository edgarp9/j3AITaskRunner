"""File-drop request contract for external queue triggers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

FILE_DROP_SCHEMA = "j3aitaskrunner.file-drop.v1"
FILE_DROP_COMMAND_START_REGISTERED_JOBS = "start_registered_jobs"
SUPPORTED_FILE_DROP_COMMAND_TYPES = (FILE_DROP_COMMAND_START_REGISTERED_JOBS,)

_REQUEST_ID_PATTERN = re.compile(r"^\d{10}$")


class FileDropRequestError(ValueError):
    """Raised when a file-drop request violates the supported contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass(slots=True, frozen=True)
class FileDropCommand:
    """One validated command from a file-drop request."""

    type: str


@dataclass(slots=True, frozen=True)
class FileDropRequest:
    """Validated file-drop request payload."""

    schema: str
    request_id: str
    commands: tuple[FileDropCommand, ...]


def parse_file_drop_request_text(payload_text: str) -> FileDropRequest:
    """Parse and validate one UTF-8 JSON file-drop request."""
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise FileDropRequestError(
            "invalid_json",
            "파일 드롭 요청 JSON을 읽을 수 없습니다.",
            detail=str(exc),
        ) from exc
    return parse_file_drop_request_payload(payload)


def parse_file_drop_request_payload(payload: Any) -> FileDropRequest:
    """Validate a decoded file-drop request payload."""
    if not isinstance(payload, dict):
        raise FileDropRequestError(
            "invalid_root",
            "파일 드롭 요청은 JSON 객체여야 합니다.",
        )

    schema = payload.get("schema")
    if schema != FILE_DROP_SCHEMA:
        raise FileDropRequestError(
            "invalid_schema",
            "지원하지 않는 파일 드롭 schema입니다.",
            detail=str(schema),
        )

    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or _REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        raise FileDropRequestError(
            "invalid_request_id",
            "파일 드롭 request_id는 숫자 10자리 문자열이어야 합니다.",
            detail=str(request_id),
        )

    raw_commands = payload.get("commands")
    if not isinstance(raw_commands, list):
        raise FileDropRequestError(
            "invalid_commands",
            "파일 드롭 commands는 배열이어야 합니다.",
        )

    return FileDropRequest(
        schema=schema,
        request_id=request_id,
        commands=tuple(_parse_command(command) for command in raw_commands),
    )


def _parse_command(command: Any) -> FileDropCommand:
    if not isinstance(command, dict):
        raise FileDropRequestError(
            "invalid_command",
            "파일 드롭 command는 JSON 객체여야 합니다.",
        )

    command_type = command.get("type")
    if not isinstance(command_type, str) or not command_type:
        raise FileDropRequestError(
            "invalid_command_type",
            "파일 드롭 command type은 문자열이어야 합니다.",
            detail=str(command_type),
        )
    if command_type not in SUPPORTED_FILE_DROP_COMMAND_TYPES:
        raise FileDropRequestError(
            "unknown_command_type",
            "지원하지 않는 파일 드롭 command type입니다.",
            detail=command_type,
        )

    return FileDropCommand(type=command_type)
