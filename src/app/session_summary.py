"""Session summary value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models import SessionId, SessionTabId, SessionTurnHistory, WorkspacePath

@dataclass(slots=True, frozen=True)
class CompletedSessionSummary:
    """Aggregated completed-session view kept only in runtime memory."""

    workspace_path: WorkspacePath
    session_id: SessionId
    session_tab_id: SessionTabId
    turn_count: int
    last_activity_at: datetime
    turns: tuple[SessionTurnHistory, ...]

