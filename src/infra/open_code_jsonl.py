"""OpenCode-family CLI JSON event parsing utilities."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)

_SESSION_EVENT_TYPES = frozenset(
    {
        "thread.started",
        "session.started",
        "session.created",
        "session.updated",
        "step_start",
    }
)
_COMPLETION_EVENT_TYPES = frozenset(
    {
        "turn.completed",
        "message.completed",
        "assistant.message.completed",
        "response.completed",
    }
)
_FAILURE_EVENT_TYPES = frozenset(
    {
        "turn.failed",
        "message.failed",
        "session.failed",
        "response.failed",
    }
)
_ERROR_EVENT_TYPES = frozenset(
    {
        "error",
        "session.error",
        "message.error",
        "response.error",
    }
)
_TRACKED_EVENT_TYPES = (
    *_SESSION_EVENT_TYPES,
    *_COMPLETION_EVENT_TYPES,
    *_FAILURE_EVENT_TYPES,
    *_ERROR_EVENT_TYPES,
)


@dataclass(slots=True, frozen=True)
class OpenCodeJsonlEvent:
    """One parsed OpenCode-family CLI JSON stdout event."""

    line_number: int
    event_type: str
    payload: dict[str, Any]
    thread_id: str | None = None
    message: str | None = None
    raw_line: str | None = None


@dataclass(slots=True, frozen=True)
class OpenCodeJsonlParseSummary:
    """Aggregate result of parsing one OpenCode-family JSON event stream."""

    thread_id: str | None = None
    saw_turn_completed: bool = False
    turn_failed_events: tuple[OpenCodeJsonlEvent, ...] = ()
    error_events: tuple[OpenCodeJsonlEvent, ...] = ()
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
    def failure_events(self) -> tuple[OpenCodeJsonlEvent, ...]:
        """Return terminal failure events in observed order."""
        return self.turn_failed_events


class OpenCodeJsonlParser:
    """Incrementally parse OpenCode and Kilo raw JSON stdout events."""

    def __init__(self) -> None:
        self._line_count = 0
        self._thread_id: str | None = None
        self._saw_turn_completed = False
        self._turn_failed_events: list[OpenCodeJsonlEvent] = []
        self._error_events: list[OpenCodeJsonlEvent] = []
        self._malformed_lines: list[int] = []
        self._total_events = 0
        self._last_message: str | None = None

    def feed_line(self, raw_line: str) -> OpenCodeJsonlEvent | None:
        """Parse one JSON line and update aggregate state."""
        self._line_count += 1
        stripped = raw_line.strip()
        if not stripped:
            return None

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            LOGGER.warning(
                "Skipping malformed OpenCode-family CLI JSON line. line_number=%s",
                self._line_count,
                exc_info=exc,
            )
            self._malformed_lines.append(self._line_count)
            return None

        if not isinstance(payload, dict):
            LOGGER.warning(
                "Skipping OpenCode-family CLI JSON payload because it is not an object. "
                "line_number=%s",
                self._line_count,
            )
            self._malformed_lines.append(self._line_count)
            return None

        raw_event_type, event_payload = self._extract_event(payload)
        event_type = self._canonical_event_type(raw_event_type, event_payload)
        thread_id = self._extract_thread_id(event_type, event_payload)
        message = self._extract_message(event_payload)
        last_message = self._extract_last_message(event_type, event_payload)
        event = OpenCodeJsonlEvent(
            line_number=self._line_count,
            event_type=event_type,
            payload=payload,
            thread_id=thread_id,
            message=message,
            raw_line=raw_line.rstrip("\r\n"),
        )

        self._total_events += 1
        if event_type == "thread.started" and thread_id:
            self._thread_id = thread_id
        elif event_type == "turn.completed":
            self._saw_turn_completed = True
        elif event_type == "turn.failed":
            self._turn_failed_events.append(event)
        elif event_type == "error":
            self._error_events.append(event)

        if last_message:
            self._last_message = last_message

        return event

    def build_summary(self) -> OpenCodeJsonlParseSummary:
        """Return the current parse summary."""
        return OpenCodeJsonlParseSummary(
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
        for prefix in ("opencode.", "kilo."):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]

        normalized_symbol = normalized.replace("-", "_")
        if normalized_symbol == "step_start":
            return "thread.started"
        if normalized_symbol == "step_finish":
            reason = _extract_step_finish_reason(event_payload)
            if reason in {None, "stop", "done", "completed", "success"}:
                return "turn.completed"
            if reason in {"failed", "failure", "error"}:
                return "turn.failed"
            return normalized_symbol

        if normalized in _SESSION_EVENT_TYPES:
            return "thread.started"
        if normalized in _COMPLETION_EVENT_TYPES:
            return "turn.completed"
        if normalized in _FAILURE_EVENT_TYPES:
            return "turn.failed"
        if normalized in _ERROR_EVENT_TYPES:
            return "error"

        status = _compact_lower_text(event_payload.get("status"))
        if status in {"completed", "done", "success"} and _extract_response_text(event_payload):
            return "turn.completed"
        if status in {"failed", "failure"}:
            return "turn.failed"
        if status == "error":
            return "error"
        return normalized or "unknown"

    @staticmethod
    def _extract_thread_id(event_type: str, event_payload: dict[str, Any]) -> str | None:
        if event_type != "thread.started":
            return None

        for key in ("thread_id", "session_id", "sessionID", "sessionId"):
            value = _compact_optional_text(event_payload.get(key))
            if value:
                return value

        for container_key in ("thread", "session", "data", "part"):
            nested = event_payload.get(container_key)
            if isinstance(nested, dict):
                nested_id = OpenCodeJsonlParser._extract_thread_id(event_type, nested)
                if nested_id:
                    return nested_id

        return _compact_optional_text(event_payload.get("id"))

    @staticmethod
    def _extract_message(event_payload: dict[str, Any]) -> str | None:
        for key in ("message", "detail", "text", "summary"):
            value = event_payload.get(key)
            if isinstance(value, dict):
                nested_message = OpenCodeJsonlParser._extract_message(value)
                if nested_message:
                    return nested_message
            text = _compact_optional_text(value)
            if text:
                return text

        error_value = event_payload.get("error")
        if isinstance(error_value, dict):
            nested_message = OpenCodeJsonlParser._extract_message(error_value)
            if nested_message:
                return nested_message

        for container_key in ("data", "part"):
            nested = event_payload.get(container_key)
            if isinstance(nested, dict):
                nested_message = OpenCodeJsonlParser._extract_message(nested)
                if nested_message:
                    return nested_message
        return _compact_optional_text(error_value)

    @staticmethod
    def _extract_last_message(
        event_type: str,
        event_payload: dict[str, Any],
    ) -> str | None:
        if event_type in {"turn.failed", "error"}:
            return None
        if event_type == "text":
            return _extract_response_text(event_payload)
        if event_type == "step_finish":
            return None
        return _extract_response_text(event_payload)


def _extract_response_text(value: Any) -> str | None:
    if isinstance(value, str):
        return _compact_text(value)

    if isinstance(value, list):
        parts = [_extract_response_text(item) for item in value]
        joined = "\n".join(part for part in parts if part)
        return joined or None

    if not isinstance(value, dict):
        return None

    role = _compact_optional_text(value.get("role"))
    if role and role not in {"assistant", "agent"}:
        return None

    for key in ("content", "text", "response", "output"):
        text = _extract_response_text(value.get(key))
        if text:
            return text

    for key in ("message", "assistant", "data", "item", "part"):
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
    return _compact_text(value) or None


def _compact_lower_text(value: Any) -> str | None:
    text = _compact_optional_text(value)
    if text is None:
        return None
    return text.lower()


def _compact_text(value: str) -> str:
    return value.strip()


def _extract_step_finish_reason(event_payload: dict[str, Any]) -> str | None:
    reason = _compact_lower_text(event_payload.get("reason"))
    if reason:
        return reason

    part = event_payload.get("part")
    if isinstance(part, dict):
        return _compact_lower_text(part.get("reason"))
    return None
