"""Session history rendering helpers for the main window."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .i18n import text as ui_text

HISTORY_TURN_SEPARATOR = "\n\n" + "-" * 72 + "\n"


@dataclass(slots=True)
class SessionHistoryTurnRenderState:
    """Cached render state for one session history turn."""

    started_at: object
    completed_at: object
    prompt_text: str
    response_text: str | None
    error_text: str | None
    block_length: int
    content_end_index: int = 0


def render_session_history_turns(
    turns: tuple[object, ...],
    *,
    start_index: int,
    language: str,
    content_length: int,
) -> tuple[tuple[SessionHistoryTurnRenderState, str], ...]:
    rendered_turns: list[tuple[SessionHistoryTurnRenderState, str]] = []
    for index, turn in enumerate(turns, start=start_index):
        if index > 1:
            content_length += len(HISTORY_TURN_SEPARATOR)
        rendered_turn, block_text = render_session_history_turn(
            turn,
            index,
            language,
            content_length=content_length,
        )
        content_length = rendered_turn.content_end_index
        rendered_turns.append((rendered_turn, block_text))
    return tuple(rendered_turns)


def render_session_history_turn(
    turn: object,
    index: int,
    language: str,
    *,
    content_length: int,
) -> tuple[SessionHistoryTurnRenderState, str]:
    response_text = turn.response_text
    error_text = getattr(turn, "error_text", None)
    block_text = format_session_history_turn(turn, index, language)
    content_end_index = content_length + len(block_text)
    return (
        SessionHistoryTurnRenderState(
            started_at=turn.started_at,
            completed_at=turn.completed_at,
            prompt_text=turn.prompt_text,
            response_text=response_text,
            error_text=error_text,
            block_length=len(block_text),
            content_end_index=content_end_index,
        ),
        block_text,
    )


def format_session_history_turn(turn: object, index: int, language: str) -> str:
    timestamp = turn.completed_at or turn.started_at
    header = ui_text(
        "history_turn",
        language,
        index=index,
        timestamp=format_timestamp(timestamp),
    )
    if turn.completed_at is None:
        header = f"{header} / {ui_text('history_in_progress', language)}"

    chunks = [header, "Prompt:", turn.prompt_text]
    if turn.response_text is not None:
        chunks.extend(["", "Response:", turn.response_text])
    error_text = getattr(turn, "error_text", None)
    if error_text is not None:
        chunks.extend(["", "Error:", error_text])
    return "\n".join(chunks)


def session_history_first_changed_index(
    rendered_turns: tuple[SessionHistoryTurnRenderState, ...],
    turns: Sequence[object],
) -> int | None:
    compared_count = min(len(rendered_turns), len(turns))
    for index in range(compared_count):
        if not session_history_turn_matches(rendered_turns[index], turns[index]):
            return index
    if len(rendered_turns) == len(turns):
        return None
    return compared_count


def session_history_turn_matches(
    rendered_turn: SessionHistoryTurnRenderState, turn: object
) -> bool:
    if (
        rendered_turn.started_at != turn.started_at
        or rendered_turn.completed_at != turn.completed_at
    ):
        return False
    if not session_history_text_matches(
        rendered_turn.prompt_text,
        turn.prompt_text,
    ):
        return False
    return session_history_optional_text_matches(
        rendered_turn.response_text,
        turn.response_text,
    ) and session_history_optional_text_matches(
        rendered_turn.error_text,
        getattr(turn, "error_text", None),
    )


def session_history_text_matches(rendered_text: str, text: str) -> bool:
    if rendered_text is text:
        return True
    if len(rendered_text) != len(text):
        return False
    return rendered_text == text


def session_history_optional_text_matches(
    rendered_text: str | None,
    text: str | None,
) -> bool:
    if rendered_text is None or text is None:
        return rendered_text is text
    return session_history_text_matches(rendered_text, text)


def join_session_history_blocks(
    rendered_turns: tuple[tuple[SessionHistoryTurnRenderState, str], ...]
) -> str:
    return HISTORY_TURN_SEPARATOR.join(
        block_text for _rendered_turn, block_text in rendered_turns
    )


def session_history_prefix_length(
    rendered_turns: tuple[SessionHistoryTurnRenderState, ...],
    turn_count: int,
) -> int:
    if turn_count <= 0:
        return 0
    return rendered_turns[turn_count - 1].content_end_index


def format_timestamp(value) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")
