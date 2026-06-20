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


@dataclass(slots=True, frozen=True)
class CompletedSessionSummary:
    """Aggregated completed-session view kept only in runtime memory."""

    workspace_path: WorkspacePath
    session_id: SessionId
    session_tab_id: SessionTabId
    turn_count: int
    last_activity_at: datetime
    turns: tuple[SessionTurnHistory, ...]


class SessionManager:
    """Manage session tabs and completed-session history in runtime memory."""

    def __init__(self, workspace_manager: WorkspaceManager) -> None:
        self._workspace_manager = workspace_manager
        self._session_tabs: dict[SessionTabId, SessionTab] = {}
        self._tab_name_state = TabNameState()
        self._turn_history: dict[tuple[str, SessionId], list[SessionTurnHistory]] = {}
        self._session_tab_turn_history: dict[SessionTabId, list[SessionTurnHistory]] = {}
        self._session_tab_turn_snapshots: dict[
            SessionTabId, tuple[SessionTurnHistory, ...]
        ] = {}
        self._next_tab_sequence = 1

    def open_session(
        self,
        workspace_tab_id: WorkspaceTabId,
        *,
        execution_options: AgentExecutionOptions | None = None,
        when: datetime | None = None,
    ) -> SessionTab:
        """Open a normal session tab inside one workspace."""
        return self._open_numbered_session(
            workspace_tab_id,
            kind=SessionTabKind.NORMAL,
            execution_options=execution_options,
            when=when,
        )

    def open_preset_session(
        self,
        workspace_tab_id: WorkspaceTabId,
        *,
        execution_options: AgentExecutionOptions | None = None,
        when: datetime | None = None,
    ) -> SessionTab:
        """Open a preset parent session tab inside one workspace."""
        return self._open_numbered_session(
            workspace_tab_id,
            kind=SessionTabKind.PRESET,
            execution_options=execution_options,
            when=when,
        )

    def open_preset_candidate_session(
        self,
        parent_session_tab_id: SessionTabId,
        *,
        execution_options: AgentExecutionOptions | None = None,
        when: datetime | None = None,
    ) -> SessionTab:
        """Open the next candidate session tab for one preset parent tab."""
        return self.open_preset_candidate_sessions(
            parent_session_tab_id,
            count=1,
            execution_options=execution_options,
            when=when,
        )[0]

    def open_preset_candidate_sessions(
        self,
        parent_session_tab_id: SessionTabId,
        *,
        count: int,
        execution_options: AgentExecutionOptions | None = None,
        when: datetime | None = None,
    ) -> tuple[SessionTab, ...]:
        """Open candidate session tabs for one preset parent tab in one batch."""
        if count < 0:
            raise ValueError("count must not be negative.")
        if count == 0:
            return ()

        parent_session_tab = self.get_session_tab(parent_session_tab_id)
        if parent_session_tab.open_state != TabOpenState.OPEN:
            raise ValueError("Cannot open a preset candidate for a closed parent tab.")
        if parent_session_tab.kind != SessionTabKind.PRESET:
            raise ValueError("Preset candidate session tabs require a preset parent tab.")

        workspace_tab = self._workspace_manager.get_workspace_tab(
            parent_session_tab.workspace_tab_id
        )
        if workspace_tab.open_state != TabOpenState.OPEN:
            raise ValueError("Cannot open a session tab in a closed workspace tab.")

        timestamps = tuple((when or utc_now()) for _ in range(count))
        workspace_session_tabs = list(
            self.list_session_tabs(
                workspace_tab_id=parent_session_tab.workspace_tab_id,
                include_closed=True,
            )
        )
        sort_order = self._next_preset_candidate_sort_order_from_tabs(
            parent_session_tab,
            workspace_session_tabs,
        )
        self._shift_session_sort_orders(
            parent_session_tab.workspace_tab_id,
            start_order=sort_order,
            amount=count,
            when=timestamps[-1],
        )

        workspace_session_tabs = [
            self._session_tabs[session_tab.session_tab_id]
            for session_tab in workspace_session_tabs
        ]
        insertion_index = next(
            (
                index
                for index, session_tab in enumerate(workspace_session_tabs)
                if session_tab.sort_order >= sort_order
            ),
            len(workspace_session_tabs),
        )

        candidate_sessions: list[SessionTab] = []
        resolved_execution_options = (
            execution_options or parent_session_tab.execution_options
        )
        for offset in range(count):
            timestamp = timestamps[offset]
            display_name, candidate_index = issue_preset_candidate_session_tab_name(
                parent_session_tab,
                tuple(workspace_session_tabs),
            )
            session_tab_id = self._next_session_tab_id()
            session_tab = SessionTab(
                session_tab_id=session_tab_id,
                workspace_tab_id=parent_session_tab.workspace_tab_id,
                display_name=display_name,
                kind=SessionTabKind.PRESET_CANDIDATE,
                session_id=None,
                parent_session_tab_id=parent_session_tab.session_tab_id,
                candidate_index=candidate_index,
                execution_options=resolved_execution_options,
                execution_options_locked=True,
                open_state=TabOpenState.OPEN,
                sort_order=sort_order + offset,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._session_tabs[session_tab_id] = session_tab
            candidate_sessions.append(session_tab)
            workspace_session_tabs.insert(insertion_index + offset, session_tab)

        self._workspace_manager.set_active_session_tab(
            parent_session_tab.workspace_tab_id,
            candidate_sessions[-1].session_tab_id,
            when=timestamps[-1],
        )
        return tuple(candidate_sessions)

    def _open_numbered_session(
        self,
        workspace_tab_id: WorkspaceTabId,
        *,
        kind: SessionTabKind,
        execution_options: AgentExecutionOptions | None = None,
        when: datetime | None = None,
    ) -> SessionTab:
        """Open a numbered normal or preset session tab inside one workspace."""
        workspace_tab = self._workspace_manager.get_workspace_tab(workspace_tab_id)
        if workspace_tab.open_state != TabOpenState.OPEN:
            raise ValueError("Cannot open a session tab in a closed workspace tab.")

        timestamp = when or utc_now()
        display_name, self._tab_name_state = issue_session_tab_name(
            self._tab_name_state,
            workspace_tab_id=workspace_tab_id,
            session_tabs=self.list_session_tabs(workspace_tab_id=workspace_tab_id),
            kind=kind,
        )
        session_tab_id = self._next_session_tab_id()
        session_tab = SessionTab(
            session_tab_id=session_tab_id,
            workspace_tab_id=workspace_tab_id,
            display_name=display_name,
            kind=kind,
            session_id=None,
            execution_options=execution_options or AgentExecutionOptions(),
            open_state=TabOpenState.OPEN,
            sort_order=self._next_session_sort_order(workspace_tab_id),
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._session_tabs[session_tab_id] = session_tab
        self._workspace_manager.set_active_session_tab(
            workspace_tab_id,
            session_tab_id,
            when=timestamp,
        )
        return session_tab

    def set_session_execution_options(
        self,
        session_tab_id: SessionTabId,
        execution_options: AgentExecutionOptions,
        *,
        locked: bool | None = None,
        when: datetime | None = None,
    ) -> SessionTab:
        """Store the selected execution options for one session tab."""
        session_tab = self.get_session_tab(session_tab_id)
        timestamp = when or utc_now()
        updates: dict[str, object] = {
            "execution_options": execution_options,
            "updated_at": timestamp,
        }
        if locked is not None:
            updates["execution_options_locked"] = locked
        updated_session_tab = replace(session_tab, **updates)
        self._session_tabs[session_tab_id] = updated_session_tab
        return updated_session_tab

    def lock_session_execution_options(
        self,
        session_tab_id: SessionTabId,
        execution_options: AgentExecutionOptions,
        *,
        when: datetime | None = None,
    ) -> SessionTab:
        """Persist and lock the execution options chosen at registration."""
        return self.set_session_execution_options(
            session_tab_id,
            execution_options,
            locked=True,
            when=when,
        )

    def activate_session(
        self,
        session_tab_id: SessionTabId,
        *,
        when: datetime | None = None,
    ) -> SessionTab:
        """Mark one open session tab as active within its workspace."""
        session_tab = self.get_session_tab(session_tab_id)
        if session_tab.open_state != TabOpenState.OPEN:
            raise ValueError("Cannot activate a closed session tab.")

        timestamp = when or utc_now()
        updated = replace(session_tab, updated_at=timestamp)
        self._session_tabs[session_tab_id] = updated
        self._workspace_manager.set_active_session_tab(
            updated.workspace_tab_id,
            session_tab_id,
            when=timestamp,
        )
        return updated

    def close_session(
        self,
        session_tab_id: SessionTabId,
        *,
        when: datetime | None = None,
    ) -> SessionTab:
        """Close one session tab without deleting jobs or turn history."""
        session_tab = self.get_session_tab(session_tab_id)
        if session_tab.open_state == TabOpenState.CLOSED:
            return session_tab

        timestamp = when or utc_now()
        updated = replace(session_tab, open_state=TabOpenState.CLOSED, updated_at=timestamp)
        self._session_tabs[session_tab_id] = updated

        workspace_tab = self._workspace_manager.get_workspace_tab(updated.workspace_tab_id)
        if workspace_tab.open_state == TabOpenState.OPEN:
            next_active_session_id = self._select_fallback_active_session(
                workspace_tab_id=updated.workspace_tab_id,
                exclude_session_tab_id=updated.session_tab_id,
            )
            self._workspace_manager.set_active_session_tab(
                updated.workspace_tab_id,
                next_active_session_id,
                when=timestamp,
            )
        return updated

    def close_sessions_for_workspace(
        self,
        workspace_tab_id: WorkspaceTabId,
        *,
        when: datetime | None = None,
    ) -> tuple[SessionTab, ...]:
        """Close every open session tab that belongs to one workspace tab."""
        closed_sessions: list[SessionTab] = []
        for session_tab in self.list_session_tabs(
            workspace_tab_id=workspace_tab_id,
            include_closed=False,
        ):
            closed_sessions.append(self.close_session(session_tab.session_tab_id, when=when))
        return tuple(closed_sessions)

    def assign_session_id(
        self,
        session_tab_id: SessionTabId,
        session_id: SessionId,
        *,
        when: datetime | None = None,
    ) -> SessionTab:
        """Assign or update the session id for one session tab."""
        normalized_session_id = self._normalize_session_id(session_id, allow_blank=False)
        session_tab = self.get_session_tab(session_tab_id)

        for existing_session_tab in self.list_session_tabs(include_closed=False):
            if (
                existing_session_tab.session_tab_id != session_tab_id
                and existing_session_tab.session_id == normalized_session_id
            ):
                raise ValueError(
                    f"Session id is already assigned to another open session tab: {normalized_session_id}"
                )

        timestamp = when or utc_now()
        updated = replace(session_tab, session_id=normalized_session_id, updated_at=timestamp)
        self._session_tabs[session_tab_id] = updated
        self._refresh_session_tab_history_session_id(
            session_tab_id,
            normalized_session_id,
        )
        return updated

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
    ) -> SessionTurnHistory | None:
        """Mark a started turn as no longer running when no response is recorded."""
        existing_turn = self._find_session_tab_turn(session_tab_id, job_id)
        if existing_turn is None:
            return None
        if existing_turn.completed_at is not None:
            return existing_turn

        finalized_turn = replace(
            existing_turn,
            completed_at=completed_at,
            last_activity_at=last_activity_at or completed_at,
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

    def get_session_tab(self, session_tab_id: SessionTabId) -> SessionTab:
        """Return one session tab by id."""
        try:
            return self._session_tabs[session_tab_id]
        except KeyError as exc:
            raise KeyError(f"Unknown session tab id: {session_tab_id}") from exc

    def list_session_tabs(
        self,
        *,
        workspace_tab_id: WorkspaceTabId | None = None,
        include_closed: bool = True,
    ) -> tuple[SessionTab, ...]:
        """Return session tabs ordered within each workspace tab."""
        session_tabs = self._session_tabs.values()
        if workspace_tab_id is not None:
            session_tabs = (
                session_tab
                for session_tab in session_tabs
                if session_tab.workspace_tab_id == workspace_tab_id
            )
        if not include_closed:
            session_tabs = (
                session_tab for session_tab in session_tabs if session_tab.open_state == TabOpenState.OPEN
            )
        return tuple(
            sorted(
                session_tabs,
                key=lambda session_tab: (
                    session_tab.workspace_tab_id,
                    session_tab.sort_order,
                    session_tab.created_at,
                    session_tab.session_tab_id,
                ),
            )
        )

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

    def _next_session_tab_id(self) -> SessionTabId:
        session_tab_id = f"session-tab-{self._next_tab_sequence}"
        self._next_tab_sequence += 1
        return session_tab_id

    def _next_session_sort_order(self, workspace_tab_id: WorkspaceTabId) -> int:
        workspace_session_tabs = self.list_session_tabs(
            workspace_tab_id=workspace_tab_id,
            include_closed=False,
        )
        if not workspace_session_tabs:
            return 0
        return max(session_tab.sort_order for session_tab in workspace_session_tabs) + 1

    def _next_preset_candidate_sort_order(self, parent_session_tab: SessionTab) -> int:
        return self._next_preset_candidate_sort_order_from_tabs(
            parent_session_tab,
            self.list_session_tabs(
                workspace_tab_id=parent_session_tab.workspace_tab_id,
                include_closed=True,
            ),
        )

    def _next_preset_candidate_sort_order_from_tabs(
        self,
        parent_session_tab: SessionTab,
        session_tabs: Iterable[SessionTab],
    ) -> int:
        candidate_tabs = [
            session_tab
            for session_tab in session_tabs
            if session_tab.parent_session_tab_id == parent_session_tab.session_tab_id
        ]
        if not candidate_tabs:
            return parent_session_tab.sort_order + 1
        return max(session_tab.sort_order for session_tab in candidate_tabs) + 1

    def _shift_session_sort_orders(
        self,
        workspace_tab_id: WorkspaceTabId,
        *,
        start_order: int,
        when: datetime,
        amount: int = 1,
    ) -> None:
        if amount <= 0:
            return

        for session_tab_id, session_tab in tuple(self._session_tabs.items()):
            if (
                session_tab.workspace_tab_id == workspace_tab_id
                and session_tab.sort_order >= start_order
            ):
                self._session_tabs[session_tab_id] = replace(
                    session_tab,
                    sort_order=session_tab.sort_order + amount,
                    updated_at=when,
                )

    def _select_fallback_active_session(
        self,
        *,
        workspace_tab_id: WorkspaceTabId,
        exclude_session_tab_id: SessionTabId,
    ) -> SessionTabId | None:
        open_session_tabs = [
            session_tab
            for session_tab in self.list_session_tabs(
                workspace_tab_id=workspace_tab_id,
                include_closed=False,
            )
            if session_tab.session_tab_id != exclude_session_tab_id
        ]
        if not open_session_tabs:
            return None
        return open_session_tabs[-1].session_tab_id

    @staticmethod
    def _normalize_session_id(
        session_id: SessionId | None,
        *,
        allow_blank: bool = True,
    ) -> SessionId | None:
        if session_id is None:
            if allow_blank:
                return None
            raise ValueError("session_id must not be blank.")

        normalized_session_id = session_id.strip()
        if normalized_session_id:
            return normalized_session_id
        if allow_blank:
            return None
        raise ValueError("session_id must not be blank.")
