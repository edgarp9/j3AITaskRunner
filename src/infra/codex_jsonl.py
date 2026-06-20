"""Codex CLI JSONL event parsing utilities."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)

_TRACKED_EVENT_TYPES = frozenset(
    {
        "thread.started",
        "codex.thread.started",
        "turn.completed",
        "codex.turn.completed",
        "turn.failed",
        "codex.turn.failed",
        "error",
        "codex.error",
    }
)
_EVENT_TYPE_ALIASES = {
    "codex.thread.started": "thread.started",
    "codex.turn.completed": "turn.completed",
    "codex.turn.failed": "turn.failed",
    "codex.error": "error",
}


@dataclass(slots=True, frozen=True)
class CodexJsonlEvent:
    """One parsed Codex CLI JSONL event."""

    line_number: int
    event_type: str
    payload: dict[str, Any]
    thread_id: str | None = None
    message: str | None = None
    raw_line: str | None = None


@dataclass(slots=True, frozen=True)
class CodexJsonlParseSummary:
    """Aggregate result of parsing one JSONL event stream."""

    thread_id: str | None = None
    saw_turn_completed: bool = False
    turn_failed_events: tuple[CodexJsonlEvent, ...] = ()
    error_events: tuple[CodexJsonlEvent, ...] = ()
    malformed_lines: tuple[int, ...] = ()
    total_events: int = 0

    @property
    def has_failure_event(self) -> bool:
        """Return whether any terminal failure event was observed."""
        return bool(self.turn_failed_events)

    @property
    def has_error_event(self) -> bool:
        """Return whether any non-terminal CLI error event was observed."""
        return bool(self.error_events)

    @property
    def failure_events(self) -> tuple[CodexJsonlEvent, ...]:
        """Return terminal failure events in observed order."""
        return self.turn_failed_events


class CodexJsonlParser:
    """Incrementally parse the Codex CLI JSONL stdout stream."""

    def __init__(self) -> None:
        self._line_count = 0
        self._thread_id: str | None = None
        self._saw_turn_completed = False
        self._turn_failed_events: list[CodexJsonlEvent] = []
        self._error_events: list[CodexJsonlEvent] = []
        self._malformed_lines: list[int] = []
        self._total_events = 0

    def feed_line(self, raw_line: str) -> CodexJsonlEvent | None:
        """Parse one JSONL line and update aggregate state."""
        self._line_count += 1
        stripped = raw_line.strip()
        if not stripped:
            return None

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            LOGGER.warning(
                "Skipping malformed Codex CLI JSONL line. line_number=%s",
                self._line_count,
                exc_info=exc,
            )
            self._malformed_lines.append(self._line_count)
            return None

        if not isinstance(payload, dict):
            LOGGER.warning(
                "Skipping Codex CLI JSONL payload because it is not an object. line_number=%s",
                self._line_count,
            )
            self._malformed_lines.append(self._line_count)
            return None

        raw_event_type, event_payload = self._extract_event(payload)
        event_type = _canonical_event_type(raw_event_type)
        thread_id = self._extract_thread_id(event_type, event_payload)
        message = self._extract_message(event_payload)
        event = CodexJsonlEvent(
            line_number=self._line_count,
            event_type=event_type,
            payload=payload,
            thread_id=thread_id,
            message=message,
            raw_line=stripped,
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

        return event

    def build_summary(self) -> CodexJsonlParseSummary:
        """Return the current parse summary."""
        return CodexJsonlParseSummary(
            thread_id=self._thread_id,
            saw_turn_completed=self._saw_turn_completed,
            turn_failed_events=tuple(self._turn_failed_events),
            error_events=tuple(self._error_events),
            malformed_lines=tuple(self._malformed_lines),
            total_events=self._total_events,
        )

    @staticmethod
    def _extract_event(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        type_value = payload.get("type")
        if isinstance(type_value, str) and type_value.strip():
            return type_value.strip(), payload

        event_value = payload.get("event")
        if isinstance(event_value, str) and event_value.strip():
            return event_value.strip(), payload

        for candidate in _TRACKED_EVENT_TYPES:
            nested_payload = payload.get(candidate)
            if isinstance(nested_payload, dict):
                return candidate, nested_payload
            if candidate in payload:
                return candidate, payload

        return "unknown", payload

    @staticmethod
    def _extract_thread_id(event_type: str, event_payload: dict[str, Any]) -> str | None:
        if event_type != "thread.started":
            return None

        direct_thread_id = event_payload.get("thread_id")
        if isinstance(direct_thread_id, str) and direct_thread_id.strip():
            return direct_thread_id.strip()

        thread = event_payload.get("thread")
        if isinstance(thread, dict):
            for key in ("thread_id", "id"):
                value = thread.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        data = event_payload.get("data")
        if isinstance(data, dict):
            return CodexJsonlParser._extract_thread_id(event_type, data)

        return None

    @staticmethod
    def _extract_message(event_payload: dict[str, Any]) -> str | None:
        direct_message = event_payload.get("message")
        if isinstance(direct_message, str) and direct_message.strip():
            return direct_message.strip()

        detail = event_payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()

        error_value = event_payload.get("error")
        if isinstance(error_value, str) and error_value.strip():
            return error_value.strip()
        if isinstance(error_value, dict):
            nested_message = error_value.get("message")
            if isinstance(nested_message, str) and nested_message.strip():
                return nested_message.strip()

        return None


def _canonical_event_type(event_type: str) -> str:
    return _EVENT_TYPE_ALIASES.get(event_type, event_type)
