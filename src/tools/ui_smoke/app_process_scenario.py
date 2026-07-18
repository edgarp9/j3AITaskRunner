"""Scenario object for the in-process UI smoke run."""

from __future__ import annotations

from pathlib import Path
import time

from .app_process_launch import UiSmokeLaunchMixin
from .app_process_preset import UiSmokePresetDialogsMixin
from .app_process_report import UiSmokeReportMixin


class _UiSmokeScenario(
    UiSmokeLaunchMixin,
    UiSmokePresetDialogsMixin,
    UiSmokeReportMixin,
):
    def __init__(
        self,
        *,
        window: object,
        workspace_paths: tuple[str, ...],
        report_path: Path,
        timeout_seconds: float,
    ) -> None:
        self.window = window
        self.workspace_paths = workspace_paths
        self.report_path = report_path
        self.timeout_seconds = timeout_seconds
        self.deadline = time.monotonic() + timeout_seconds
        self.actions: list[dict[str, object]] = []
        self.exit_code = 1
        self.finished = False
        self.last_action: str | None = None
        self.about_dialog: dict[str, object] = {}
        self.settings_dialog: dict[str, object] = {}
        self.licenses_dialog: dict[str, object] = {}
        self.scheduled_run_dialog: dict[str, object] = {}
        self.ai_settings_dialogs: dict[str, dict[str, object]] = {}
        self.prompt_viewer_dialog: dict[str, object] = {}
        self.queue_execution: dict[str, object] = {}
        self.bulk_import: dict[str, object] = {}
        self.preset_session: dict[str, object] = {}
        self.manual_candidates: dict[str, object] = {}
        self.sidebar: dict[str, object] = {}
        self._patches: list[tuple[object, str, object]] = []
        self._target_workspace_path: str | None = None
        self._open_workspace_action: dict[str, object] | None = None
        self._session_tab_id: str | None = None
        self._preset_session_tab_id: str | None = None
        self._preset_controls_action: dict[str, object] | None = None
        self._queue_completion_action: dict[str, object] | None = None
        self._queue_execution_session_tab_id: str | None = None
