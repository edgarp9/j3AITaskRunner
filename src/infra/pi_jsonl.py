"""Pi Coding Agent JSON Event Stream parsing utilities."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class PiJsonlEvent:
    """One parsed Pi JSON Event Stream stdout event."""

    line_number: int
    event_type: str
    payload: dict[str, Any]
    thread_id: str | None = None
    message: str | None = None
    raw_line: str | None = None


@dataclass(slots=True, frozen=True)
class PiJsonlParseSummary:
    """Aggregate result of parsing one Pi JSON Event Stream."""

    thread_id: str | None = None
    saw_turn_completed: bool = False
    turn_failed_events: tuple[PiJsonlEvent, ...] = ()
    error_events: tuple[PiJsonlEvent, ...] = ()
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
    def failure_events(self) -> tuple[PiJsonlEvent, ...]:
        """Return terminal failure events in observed order."""
        return self.turn_failed_events


class PiJsonlParser:
    """Incrementally parse Pi ``--mode json`` stdout events."""

    def __init__(self) -> None:
        self._line_count = 0
        self._thread_id: str | None = None
        self._saw_turn_completed = False
        self._turn_failed_events: list[PiJsonlEvent] = []
        self._error_events: list[PiJsonlEvent] = []
        self._malformed_lines: list[int] = []
        self._total_events = 0
        self._last_message: str | None = None

    def feed_line(self, raw_line: str) -> PiJsonlEvent | None:
        """Parse one JSON line and update aggregate state."""
        self._line_count += 1
        stripped = raw_line.strip()
        if not stripped:
            return None

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            LOGGER.warning(
                "Skipping malformed Pi JSON Event Stream line. line_number=%s",
                self._line_count,
                exc_info=exc,
            )
            self._malformed_lines.append(self._line_count)
            return None

        if not isinstance(payload, dict):
            LOGGER.warning(
                "Skipping Pi JSON Event Stream payload because it is not an object. "
                "line_number=%s",
                self._line_count,
            )
            self._malformed_lines.append(self._line_count)
            return None

        raw_event_type = _compact_lower_text(payload.get("type")) or "unknown"
        event_type = self._canonical_event_type(raw_event_type, payload)
        thread_id = self._extract_thread_id(event_type, payload)
        message = self._extract_message(event_type, payload)
        last_message = self._extract_last_message(raw_event_type, event_type, payload)
        event = PiJsonlEvent(
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

    def build_summary(self) -> PiJsonlParseSummary:
        """Return the current parse summary."""
        return PiJsonlParseSummary(
            thread_id=self._thread_id,
            saw_turn_completed=self._saw_turn_completed,
            turn_failed_events=tuple(self._turn_failed_events),
            error_events=tuple(self._error_events),
            malformed_lines=tuple(self._malformed_lines),
            total_events=self._total_events,
            last_message=self._last_message,
        )

    @staticmethod
    def _canonical_event_type(event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "session":
            return "thread.started"
        if event_type in {"turn_end", "agent_end"}:
            return "turn.completed"
        if event_type in {"turn_failed", "agent_failed"}:
            return "turn.failed"
        if event_type == "error":
            return "error"
        if event_type == "auto_retry_end" and payload.get("success") is False:
            return "error"
        if event_type == "tool_execution_end" and payload.get("isError") is True:
            return "error"
        return event_type or "unknown"

    @staticmethod
    def _extract_thread_id(event_type: str, payload: dict[str, Any]) -> str | None:
        if event_type != "thread.started":
            return None
        return _extract_nested_text(
            payload,
            keys=("id", "session_id", "sessionId", "sessionID"),
        )

    @staticmethod
    def _extract_message(event_type: str, payload: dict[str, Any]) -> str | None:
        if event_type == "error":
            return (
                _extract_nested_text(
                    payload,
                    keys=("finalError", "errorMessage", "message", "detail", "summary"),
                )
                or _compact_optional_text(payload.get("error"))
            )

        assistant_event = payload.get("assistantMessageEvent")
        if isinstance(assistant_event, dict):
            text = _extract_nested_text(assistant_event, keys=("delta", "text"))
            if text:
                return text

        message = payload.get("message")
        if isinstance(message, dict):
            text = _extract_message_text(message)
            if text:
                return text

        return _extract_nested_text(payload, keys=("message", "summary", "text"))

    @staticmethod
    def _extract_last_message(
        raw_event_type: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> str | None:
        if event_type in {"turn.failed", "error"}:
            return None

        if raw_event_type in {"turn_end", "message_end"}:
            message = payload.get("message")
            if isinstance(message, dict) and _message_is_assistant(message):
                return _extract_message_text(message)
            return None

        if raw_event_type == "agent_end":
            messages = payload.get("messages")
            if isinstance(messages, list):
                for message in reversed(messages):
                    if isinstance(message, dict) and _message_is_assistant(message):
                        text = _extract_message_text(message)
                        if text:
                            return text
            return None

        return None


def _message_is_assistant(message: dict[str, Any]) -> bool:
    role = _compact_lower_text(message.get("role"))
    return role in {"assistant", "agent"}


def _extract_message_text(message: dict[str, Any]) -> str | None:
    if not _message_is_assistant(message):
        return None
    return _extract_response_text(message)


def _extract_response_text(value: Any) -> str | None:
    if isinstance(value, str):
        return _compact_text(value)

    if isinstance(value, list):
        parts = [_extract_response_text(item) for item in value]
        joined = "\n".join(part for part in parts if part)
        return joined or None

    if not isinstance(value, dict):
        return None

    role = _compact_lower_text(value.get("role"))
    if role and role not in {"assistant", "agent"}:
        return None

    for key in ("text", "delta", "content", "message", "response", "output"):
        text = _extract_response_text(value.get(key))
        if text:
            return text

    for key in ("parts", "blocks", "items"):
        text = _extract_response_text(value.get(key))
        if text:
            return text

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

    for nested_key in ("data", "message", "session", "event", "error", "result"):
        text = _extract_nested_text(
            value.get(nested_key),
            keys=keys,
            depth=depth + 1,
        )
        if text:
            return text

    return None


def _compact_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _compact_text(value)


def _compact_lower_text(value: Any) -> str | None:
    text = _compact_optional_text(value)
    if text is None:
        return None
    return text.lower()


def _compact_text(value: str) -> str | None:
    normalized = value.strip()
    return normalized or None
