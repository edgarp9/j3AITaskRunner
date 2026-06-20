"""Runtime workspace tab management for j3AITaskRunner."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from domain.models import (
    SessionTabId,
    TabOpenState,
    WorkspacePath,
    WorkspaceTab,
    WorkspaceTabId,
    utc_now,
)
from domain.policies import canonicalize_workspace_path, workspace_folder_display_name


@dataclass(slots=True, frozen=True)
class WorkspaceOpenResult:
    """Result of opening or activating a workspace tab."""

    workspace_tab: WorkspaceTab
    created: bool


def validate_workspace_path(workspace_path: WorkspacePath) -> None:
    """Validate that a workspace path points to an existing directory."""
    normalized_path = canonicalize_workspace_path(workspace_path)
    if not normalized_path:
        raise ValueError("워크스페이스 경로를 입력하세요.")

    workspace = Path(workspace_path.strip())
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError("존재하는 워크스페이스 폴더 경로를 선택하세요.")


class WorkspaceManager:
    """Manage workspace tab runtime state without any UI dependencies."""

    def __init__(self) -> None:
        self._tabs: dict[WorkspaceTabId, WorkspaceTab] = {}
        self._active_workspace_tab_id: WorkspaceTabId | None = None
        self._next_tab_sequence = 1

    @property
    def active_workspace_tab_id(self) -> WorkspaceTabId | None:
        """Return the currently active workspace tab id."""
        return self._active_workspace_tab_id

    def open_workspace(
        self,
        workspace_path: WorkspacePath,
        *,
        when: datetime | None = None,
    ) -> WorkspaceOpenResult:
        """Open a workspace tab or activate the existing open tab for the same path."""
        validate_workspace_path(workspace_path)
        return self.open_validated_workspace(workspace_path, when=when)

    def open_validated_workspace(
        self,
        workspace_path: WorkspacePath,
        *,
        when: datetime | None = None,
    ) -> WorkspaceOpenResult:
        """Open a workspace tab after filesystem validation has already completed."""
        normalized_path = canonicalize_workspace_path(workspace_path)
        if not normalized_path:
            raise ValueError("워크스페이스 경로를 입력하세요.")

        timestamp = when or utc_now()
        existing = self.find_open_tab_by_path(workspace_path)
        if existing is not None:
            updated = replace(existing, updated_at=timestamp)
            self._tabs[existing.workspace_tab_id] = updated
            self._active_workspace_tab_id = existing.workspace_tab_id
            return WorkspaceOpenResult(workspace_tab=updated, created=False)

        workspace_tab_id = self._next_workspace_tab_id()
        workspace_tab = WorkspaceTab(
            workspace_tab_id=workspace_tab_id,
            workspace_path=workspace_path,
            display_name=workspace_folder_display_name(workspace_path),
            open_state=TabOpenState.OPEN,
            sort_order=self._next_workspace_sort_order(),
            active_session_tab_id=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._tabs[workspace_tab_id] = workspace_tab
        self._active_workspace_tab_id = workspace_tab_id
        return WorkspaceOpenResult(workspace_tab=workspace_tab, created=True)

    def activate_workspace(
        self,
        workspace_tab_id: WorkspaceTabId,
        *,
        when: datetime | None = None,
    ) -> WorkspaceTab:
        """Mark one open workspace tab as active."""
        workspace_tab = self.get_workspace_tab(workspace_tab_id)
        if workspace_tab.open_state != TabOpenState.OPEN:
            raise ValueError("Cannot activate a closed workspace tab.")

        timestamp = when or utc_now()
        updated = replace(workspace_tab, updated_at=timestamp)
        self._tabs[workspace_tab_id] = updated
        self._active_workspace_tab_id = workspace_tab_id
        return updated

    def close_workspace(
        self,
        workspace_tab_id: WorkspaceTabId,
        *,
        when: datetime | None = None,
    ) -> WorkspaceTab:
        """Close one workspace tab without deleting its runtime state."""
        workspace_tab = self.get_workspace_tab(workspace_tab_id)
        if workspace_tab.open_state == TabOpenState.CLOSED:
            return workspace_tab

        timestamp = when or utc_now()
        updated = replace(
            workspace_tab,
            open_state=TabOpenState.CLOSED,
            active_session_tab_id=None,
            updated_at=timestamp,
        )
        self._tabs[workspace_tab_id] = updated

        if self._active_workspace_tab_id == workspace_tab_id:
            self._active_workspace_tab_id = self._select_fallback_active_workspace(
                exclude_workspace_tab_id=workspace_tab_id
            )
        return updated

    def set_active_session_tab(
        self,
        workspace_tab_id: WorkspaceTabId,
        session_tab_id: SessionTabId | None,
        *,
        when: datetime | None = None,
    ) -> WorkspaceTab:
        """Store the active session tab id for one workspace tab."""
        workspace_tab = self.get_workspace_tab(workspace_tab_id)
        timestamp = when or utc_now()
        updated = replace(
            workspace_tab,
            active_session_tab_id=session_tab_id,
            updated_at=timestamp,
        )
        self._tabs[workspace_tab_id] = updated
        return updated

    def get_workspace_tab(self, workspace_tab_id: WorkspaceTabId) -> WorkspaceTab:
        """Return one workspace tab by id."""
        try:
            return self._tabs[workspace_tab_id]
        except KeyError as exc:
            raise KeyError(f"Unknown workspace tab id: {workspace_tab_id}") from exc

    def get_active_workspace_tab(self) -> WorkspaceTab | None:
        """Return the active workspace tab when one is selected."""
        if self._active_workspace_tab_id is None:
            return None
        return self._tabs[self._active_workspace_tab_id]

    def find_open_tab_by_path(self, workspace_path: WorkspacePath) -> WorkspaceTab | None:
        """Return the currently open tab for a workspace path if present."""
        normalized_candidate = canonicalize_workspace_path(workspace_path)
        for workspace_tab in self.list_workspace_tabs(include_closed=False):
            if canonicalize_workspace_path(workspace_tab.workspace_path) == normalized_candidate:
                return workspace_tab
        return None

    def list_workspace_tabs(self, *, include_closed: bool = True) -> tuple[WorkspaceTab, ...]:
        """Return workspace tabs ordered by runtime sort order."""
        tabs = self._tabs.values()
        if not include_closed:
            tabs = (tab for tab in tabs if tab.open_state == TabOpenState.OPEN)
        return tuple(
            sorted(
                tabs,
                key=lambda tab: (tab.sort_order, tab.created_at, tab.workspace_tab_id),
            )
        )

    def _next_workspace_tab_id(self) -> WorkspaceTabId:
        workspace_tab_id = f"workspace-tab-{self._next_tab_sequence}"
        self._next_tab_sequence += 1
        return workspace_tab_id

    def _next_workspace_sort_order(self) -> int:
        if not self._tabs:
            return 0
        return max(tab.sort_order for tab in self._tabs.values()) + 1

    def _select_fallback_active_workspace(
        self,
        *,
        exclude_workspace_tab_id: WorkspaceTabId,
    ) -> WorkspaceTabId | None:
        open_tabs = [
            tab
            for tab in self.list_workspace_tabs(include_closed=False)
            if tab.workspace_tab_id != exclude_workspace_tab_id
        ]
        if not open_tabs:
            return None
        return open_tabs[-1].workspace_tab_id
