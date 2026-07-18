"""Claude Code stream-json event parsing utilities."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)

_TRACKED_EVENT_TYPES = (
    "system",
    "result",
    "assistant",
    "user",
    "stream_event",
    "error",
    "thread.started",
    "session.started",
    "turn.completed",
    "turn.failed",
)


@dataclass(slots=True, frozen=True)
class ClaudeCodeJsonlEvent:
    """One parsed Claude Code stream-json stdout event."""

    line_number: int
    event_type: str
    payload: dict[str, Any]
    thread_id: str | None = None
    message: str | None = None
    raw_line: str | None = None


@dataclass(slots=True, frozen=True)
class ClaudeCodeJsonlParseSummary:
    """Aggregate result of parsing one Claude Code stream-json event stream."""

    thread_id: str | None = None
    saw_turn_completed: bool = False
    turn_failed_events: tuple[ClaudeCodeJsonlEvent, ...] = ()
    error_events: tuple[ClaudeCodeJsonlEvent, ...] = ()
    malformed_lines: tuple[int, ...] = ()
    total_events: int = 0
    last_message: str | None = None

    @property
    def has_failure_event(self) -> bool:
        """Return whether any terminal failure event was observed."""
        return bool(self.turn_failed_events)

    @property
    def has_error_event(self) -> bool:
        """Return whether any non-terminal CLI error event was observed."""
        return bool(self.error_events)

    @property
    def failure_events(self) -> tuple[ClaudeCodeJsonlEvent, ...]:
        """Return terminal failure events in observed order."""
        return self.turn_failed_events


class ClaudeCodeJsonlParser:
    """Incrementally parse Claude Code ``--output-format stream-json`` stdout."""

    def __init__(self) -> None:
        self._line_count = 0
        self._thread_id: str | None = None
        self._saw_turn_completed = False
        self._turn_failed_events: list[ClaudeCodeJsonlEvent] = []
        self._error_events: list[ClaudeCodeJsonlEvent] = []
        self._malformed_lines: list[int] = []
        self._total_events = 0
        self._last_message: str | None = None

    def feed_line(self, raw_line: str) -> ClaudeCodeJsonlEvent | None:
        """Parse one JSON line and update aggregate state."""
        self._line_count += 1
        stripped = raw_line.strip()
        if not stripped:
            return None

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            LOGGER.warning(
                "Skipping malformed Claude Code stream-json line. line_number=%s",
                self._line_count,
                exc_info=exc,
            )
            self._malformed_lines.append(self._line_count)
            return None

        if not isinstance(payload, dict):
            LOGGER.warning(
                "Skipping Claude Code stream-json payload because it is not an object. "
                "line_number=%s",
                self._line_count,
            )
            self._malformed_lines.append(self._line_count)
            return None

        raw_event_type, event_payload = self._extract_event(payload)
        event_type = self._canonical_event_type(raw_event_type, event_payload)
        thread_id = self._extract_session_id(event_payload)
        message = self._extract_message(event_type, event_payload)
        last_message = self._extract_last_message(event_type, event_payload)
        event = ClaudeCodeJsonlEvent(
            line_number=self._line_count,
            event_type=event_type,
            payload=payload,
            thread_id=thread_id,
            message=message,
            raw_line=raw_line.rstrip("\r\n"),
        )

        self._total_events += 1
        if thread_id and (event_type == "thread.started" or self._thread_id is None):
            self._thread_id = thread_id
        if event_type == "turn.completed":
            self._saw_turn_completed = True
        elif event_type == "turn.failed":
            self._turn_failed_events.append(event)
        elif event_type == "error":
            self._error_events.append(event)

        if last_message:
            self._last_message = last_message

        return event

    def build_summary(self) -> ClaudeCodeJsonlParseSummary:
        """Return the current parse summary."""
        return ClaudeCodeJsonlParseSummary(
            thread_id=self._thread_id,
            saw_turn_completed=self._saw_turn_completed,
            turn_failed_events=tuple(self._turn_failed_events),
            error_events=tuple(self._error_events),
            malformed_lines=tuple(self._malformed_lines),
            total_events=self._total_events,
            last_message=self._last_message,
        )

    @staticmethod
    def _extract_event(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        for key in ("type", "event", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), payload

        for candidate in _TRACKED_EVENT_TYPES:
            nested_payload = payload.get(candidate)
            if isinstance(nested_payload, dict):
                return candidate, nested_payload
            if candidate in payload:
                return candidate, payload

        return "unknown", payload

    @staticmethod
    def _canonical_event_type(event_type: str, event_payload: dict[str, Any]) -> str:
        normalized = event_type.strip().lower()
        subtype = _compact_lower_text(event_payload.get("subtype"))
        status = _compact_lower_text(event_payload.get("status"))

        if normalized in {"thread.started", "session.started"}:
            return "thread.started"
        if normalized == "system" and subtype == "init":
            return "thread.started"
        if normalized in {"turn.completed", "response.completed"}:
            return "turn.completed"
        if normalized == "result":
            if subtype == "success" or status in {"success", "completed", "done"}:
                return "turn.completed"
            if _is_failure_subtype(subtype) or status in {"failed", "failure", "error"}:
                return "turn.failed"
            return "result"
        if normalized in {"turn.failed", "response.failed"}:
            return "turn.failed"
        if normalized in {"error", "response.error"}:
            return "error"
        if normalized == "system" and subtype:
            return f"system/{subtype}"
        return normalized or "unknown"

    @staticmethod
    def _extract_session_id(event_payload: dict[str, Any]) -> str | None:
        return _extract_nested_text(
            event_payload,
            keys=("session_id", "sessionId", "sessionID", "thread_id", "threadId"),
        )

    @staticmethod
    def _extract_message(event_type: str, event_payload: dict[str, Any]) -> str | None:
        if event_type == "stream_event":
            stream_text = _extract_nested_text(event_payload, keys=("text", "text_delta", "delta"))
            if stream_text:
                return stream_text

        error_value = event_payload.get("error")
        if isinstance(error_value, dict):
            nested_message = ClaudeCodeJsonlParser._extract_message(event_type, error_value)
            if nested_message:
                return nested_message
        error_text = _compact_optional_text(error_value)
        if error_text:
            return error_text

        for key in ("message", "detail", "summary", "status"):
            value = event_payload.get(key)
            if isinstance(value, dict):
                nested_message = ClaudeCodeJsonlParser._extract_message(event_type, value)
                if nested_message:
                    return nested_message
            text = _compact_optional_text(value)
            if text:
                return text

        subtype = _compact_optional_text(event_payload.get("subtype"))
        if event_type == "turn.failed" and subtype:
            return subtype
        return subtype

    @staticmethod
    def _extract_last_message(
        event_type: str,
        event_payload: dict[str, Any],
    ) -> str | None:
        if event_type == "turn.completed":
            for key in ("result", "response", "output", "message"):
                text = _extract_response_text(event_payload.get(key))
                if text:
                    return text
        if event_type == "assistant":
            return _extract_response_text(event_payload)
        return None


def _extract_nested_text(
    value: Any,
    *,
    keys: tuple[str, ...],
    depth: int = 0,
) -> str | None:
    if depth > 6 or not isinstance(value, dict):
        return None

    for key in keys:
        text = _compact_optional_text(value.get(key))
        if text:
            return text

    for nested_key in ("data", "message", "result", "session", "thread", "event", "delta"):
        nested = value.get(nested_key)
        text = _extract_nested_text(nested, keys=keys, depth=depth + 1)
        if text:
            return text

    return None


def _extract_response_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None

    if isinstance(value, list):
        parts = [_extract_response_text(item) for item in value]
        joined = "\n".join(part for part in parts if part)
        return joined or None

    if not isinstance(value, dict):
        return None

    role = _compact_lower_text(value.get("role"))
    if role and role not in {"assistant", "agent"}:
        return None

    block_type = _compact_lower_text(value.get("type"))
    if block_type in {"tool_use", "server_tool_use", "thinking"}:
        return None

    for key in ("result", "content", "text", "response", "output"):
        text = _extract_response_text(value.get(key))
        if text:
            return text

    for key in ("message", "assistant", "data", "item"):
        text = _extract_response_text(value.get(key))
        if text:
            return text

    parts = value.get("parts")
    if isinstance(parts, list):
        return _extract_response_text(parts)

    return None


def _compact_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _compact_lower_text(value: Any) -> str | None:
    text = _compact_optional_text(value)
    if text is None:
        return None
    return text.lower()


def _is_failure_subtype(subtype: str | None) -> bool:
    if subtype is None:
        return False
    return subtype.startswith("error") or subtype in {
        "failed",
        "failure",
        "aborted",
        "timeout",
    }
