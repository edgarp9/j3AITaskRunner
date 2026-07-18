"""Runtime session tab and completed-session management."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from domain.models import (
    AgentExecutionOptions,
    JobId,
    SessionId,
    SessionTab,
    SessionTabId,
    SessionTabKind,
    SessionTurnHistory,
    TabNameState,
    TabOpenState,
    WorkspacePath,
    WorkspaceTabId,
    utc_now,
)
from domain.policies import (
    canonicalize_workspace_path,
    issue_preset_candidate_session_tab_name,
    issue_session_tab_name,
)

from .workspace_manager import WorkspaceManager




from .session_summary import CompletedSessionSummary
class SessionManagerHistoryMixin:
    def record_started_turn(
        self,
        session_tab_id: SessionTabId,
        *,
        job_id: JobId,
        prompt_text: str,
        started_at: datetime,
        last_activity_at: datetime | None = None,
    ) -> SessionTurnHistory:
        """Append a turn as soon as a job starts so the prompt is visible immediately."""
        session_tab = self.get_session_tab(session_tab_id)
        existing_turn = self._find_session_tab_turn(session_tab_id, job_id)
        if existing_turn is not None:
            return existing_turn

        workspace_tab = self._workspace_manager.get_workspace_tab(session_tab.workspace_tab_id)
        turn_history = SessionTurnHistory(
            workspace_path=workspace_tab.workspace_path,
            session_tab_id=session_tab.session_tab_id,
            session_id=session_tab.session_id,
            prompt_text=prompt_text,
            response_text=None,
            started_at=started_at,
            completed_at=None,
            last_activity_at=last_activity_at or started_at,
            job_id=job_id,
            error_text=None,
        )
        self._insert_session_tab_turn(session_tab_id, turn_history)
        return turn_history

    def record_completed_turn(
        self,
        session_tab_id: SessionTabId,
        *,
        job_id: JobId | None = None,
        prompt_text: str,
        response_text: str,
        started_at: datetime,
        completed_at: datetime,
        last_activity_at: datetime | None = None,
    ) -> SessionTurnHistory:
        """Append one completed turn to runtime-only session history."""
        session_tab = self.get_session_tab(session_tab_id)
        if not session_tab.session_id:
            raise ValueError("Cannot record turn history without a confirmed session id.")

        workspace_tab = self._workspace_manager.get_workspace_tab(session_tab.workspace_tab_id)
        turn_history = SessionTurnHistory(
            workspace_path=workspace_tab.workspace_path,
            session_tab_id=session_tab.session_tab_id,
            session_id=session_tab.session_id,
            prompt_text=prompt_text,
            response_text=response_text,
            started_at=started_at,
            completed_at=completed_at,
            last_activity_at=last_activity_at or completed_at,
            job_id=job_id,
            error_text=None,
        )
        existing_turn = (
            self._find_session_tab_turn(session_tab_id, job_id) if job_id is not None else None
        )
        if existing_turn is not None:
            turn_history = replace(
                existing_turn,
                workspace_path=workspace_tab.workspace_path,
                session_id=session_tab.session_id,
                prompt_text=prompt_text,
                response_text=response_text,
                started_at=started_at,
                completed_at=completed_at,
                last_activity_at=last_activity_at or completed_at,
                error_text=None,
            )
            self._replace_session_tab_turn(session_tab_id, turn_history)
        else:
            self._insert_session_tab_turn(session_tab_id, turn_history)

        self._turn_history.setdefault(
            self._completed_session_key(workspace_tab.workspace_path, session_tab.session_id),
            [],
        )
        self._upsert_completed_turn(
            workspace_tab.workspace_path,
            session_tab.session_id,
            turn_history,
        )
        return turn_history

    def finalize_turn(
        self,
        session_tab_id: SessionTabId,
        *,
        job_id: JobId,
        completed_at: datetime,
        last_activity_at: datetime | None = None,
        error_text: str | None = None,
    ) -> SessionTurnHistory | None:
        """Mark a started turn as no longer running when no response is recorded."""
        existing_turn = self._find_session_tab_turn(session_tab_id, job_id)
        if existing_turn is None:
            return None
        normalized_error_text = _normalize_optional_history_text(error_text)
        if existing_turn.completed_at is not None:
            if (
                normalized_error_text is not None
                and existing_turn.error_text != normalized_error_text
            ):
                updated_turn = replace(
                    existing_turn,
                    error_text=normalized_error_text,
                    last_activity_at=last_activity_at or completed_at,
                )
                self._replace_session_tab_turn(session_tab_id, updated_turn)
                return updated_turn
            return existing_turn

        finalized_turn = replace(
            existing_turn,
            completed_at=completed_at,
            last_activity_at=last_activity_at or completed_at,
            error_text=normalized_error_text,
        )
        self._replace_session_tab_turn(session_tab_id, finalized_turn)
        return finalized_turn

    def list_completed_sessions(
        self,
        workspace_path: WorkspacePath,
    ) -> tuple[CompletedSessionSummary, ...]:
        """Return completed sessions sorted by latest activity."""
        canonical_workspace_path = canonicalize_workspace_path(workspace_path)
        summaries: list[CompletedSessionSummary] = []

        for (candidate_workspace_path, session_id), turns in self._turn_history.items():
            if candidate_workspace_path != canonical_workspace_path:
                continue

            summary = self._build_completed_session_summary(session_id, turns)
            if summary is None:
                continue
            summaries.append(summary)

        return tuple(
            sorted(
                summaries,
                key=lambda summary: (
                    -summary.last_activity_at.timestamp(),
                    summary.session_id,
                ),
            )
        )

    def get_completed_session_summary(
        self,
        workspace_path: WorkspacePath,
        session_id: SessionId,
    ) -> CompletedSessionSummary | None:
        """Return one completed-session summary without scanning other sessions."""
        normalized_session_id = self._normalize_session_id(session_id, allow_blank=False)
        turns = self._turn_history.get(
            self._completed_session_key(workspace_path, normalized_session_id),
            [],
        )
        return self._build_completed_session_summary(normalized_session_id, turns)

    def list_session_turns(
        self,
        workspace_path: WorkspacePath,
        session_id: SessionId,
    ) -> tuple[SessionTurnHistory, ...]:
        """Return runtime turn history for one completed session."""
        normalized_session_id = self._normalize_session_id(session_id, allow_blank=False)
        turns = self._turn_history.get(
            self._completed_session_key(workspace_path, normalized_session_id),
            [],
        )
        return tuple(
            sorted(
                turns,
                key=lambda turn: (
                    turn.started_at,
                    turn.completed_at or turn.started_at,
                    turn.session_tab_id,
                ),
            )
        )

    def list_session_tab_turns(
        self,
        session_tab_id: SessionTabId,
    ) -> tuple[SessionTurnHistory, ...]:
        """Return all runtime turn history for one session tab, including running turns."""
        self.get_session_tab(session_tab_id)
        turns = self._session_tab_turn_history.get(session_tab_id, [])
        snapshot = self._session_tab_turn_snapshots.get(session_tab_id)
        if snapshot is None:
            snapshot = tuple(turns)
            self._session_tab_turn_snapshots[session_tab_id] = snapshot
        return snapshot

    def _completed_session_key(
        self,
        workspace_path: WorkspacePath,
        session_id: SessionId,
    ) -> tuple[str, SessionId]:
        return (canonicalize_workspace_path(workspace_path), session_id)

    def _build_completed_session_summary(
        self,
        session_id: SessionId,
        turns: list[SessionTurnHistory],
    ) -> CompletedSessionSummary | None:
        ordered_turns = tuple(
            sorted(
                (turn for turn in turns if turn.completed_at is not None),
                key=lambda turn: (
                    turn.started_at,
                    turn.completed_at,
                    turn.session_tab_id,
                ),
            )
        )
        if not ordered_turns:
            return None

        latest_turn = max(
            ordered_turns,
            key=lambda turn: (
                turn.last_activity_at,
                turn.completed_at,
                turn.session_tab_id,
            ),
        )
        return CompletedSessionSummary(
            workspace_path=latest_turn.workspace_path,
            session_id=session_id,
            session_tab_id=latest_turn.session_tab_id,
            turn_count=len(ordered_turns),
            last_activity_at=latest_turn.last_activity_at,
            turns=ordered_turns,
        )

    def _refresh_session_tab_history_session_id(
        self,
        session_tab_id: SessionTabId,
        session_id: SessionId,
    ) -> None:
        turns = self._session_tab_turn_history.get(session_tab_id)
        if not turns:
            return

        self._session_tab_turn_history[session_tab_id] = [
            replace(turn, session_id=session_id) if turn.session_id is None else turn
            for turn in turns
        ]
        self._invalidate_session_tab_turn_snapshot(session_tab_id)

    def _find_session_tab_turn(
        self,
        session_tab_id: SessionTabId,
        job_id: JobId,
    ) -> SessionTurnHistory | None:
        turn_index = self._find_session_tab_turn_index(session_tab_id, job_id)
        if turn_index is None:
            return None
        return self._session_tab_turn_history[session_tab_id][turn_index]

    def _replace_session_tab_turn(
        self,
        session_tab_id: SessionTabId,
        turn_history: SessionTurnHistory,
    ) -> None:
        turn_index = (
            self._find_session_tab_turn_index(session_tab_id, turn_history.job_id)
            if turn_history.job_id is not None
            else None
        )
        if turn_index is None:
            self._insert_session_tab_turn(session_tab_id, turn_history)
            return
        turns = self._session_tab_turn_history[session_tab_id]
        del turns[turn_index]
        self._insert_session_tab_turn(session_tab_id, turn_history)

    def _find_session_tab_turn_index(
        self,
        session_tab_id: SessionTabId,
        job_id: JobId | None,
    ) -> int | None:
        if job_id is None:
            return None
        for index, turn_history in enumerate(
            self._session_tab_turn_history.get(session_tab_id, [])
        ):
            if turn_history.job_id == job_id:
                return index
        return None

    def _upsert_completed_turn(
        self,
        workspace_path: WorkspacePath,
        session_id: SessionId,
        turn_history: SessionTurnHistory,
    ) -> None:
        completed_turns = self._turn_history[
            self._completed_session_key(workspace_path, session_id)
        ]
        if turn_history.job_id is not None:
            for index, existing_turn in enumerate(completed_turns):
                if existing_turn.job_id == turn_history.job_id:
                    completed_turns[index] = turn_history
                    return
        completed_turns.append(turn_history)

    def _insert_session_tab_turn(
        self,
        session_tab_id: SessionTabId,
        turn_history: SessionTurnHistory,
    ) -> None:
        turns = self._session_tab_turn_history.setdefault(session_tab_id, [])
        turn_key = self._session_tab_turn_sort_key(turn_history)
        if not turns or self._session_tab_turn_sort_key(turns[-1]) <= turn_key:
            turns.append(turn_history)
            self._invalidate_session_tab_turn_snapshot(session_tab_id)
            return

        for index, existing_turn in enumerate(turns):
            if turn_key < self._session_tab_turn_sort_key(existing_turn):
                turns.insert(index, turn_history)
                self._invalidate_session_tab_turn_snapshot(session_tab_id)
                return

        turns.append(turn_history)
        self._invalidate_session_tab_turn_snapshot(session_tab_id)

    def _session_tab_turn_sort_key(
        self, turn_history: SessionTurnHistory
    ) -> tuple[datetime, datetime, str]:
        return (
            turn_history.started_at,
            turn_history.completed_at or turn_history.started_at,
            turn_history.job_id or "",
        )

    def _invalidate_session_tab_turn_snapshot(
        self,
        session_tab_id: SessionTabId,
    ) -> None:
        self._session_tab_turn_snapshots.pop(session_tab_id, None)


def _normalize_optional_history_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None

