"""In-process UI smoke scenario run by the real Tk application process."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .app_process_scenario import _UiSmokeScenario
from .app_process_shared import SMOKE_TEXT, UiSmokeFailure, has_fatal_output


def run_ui_smoke(
    *,
    window: object,
    workspace_paths: Sequence[str],
    report_path: Path,
    timeout_seconds: float,
) -> int:
    """Run a UI smoke scenario inside the real Tk event loop."""
    scenario = _UiSmokeScenario(
        window=window,
        workspace_paths=tuple(workspace_paths),
        report_path=report_path,
        timeout_seconds=timeout_seconds,
    )
    scenario.install_messagebox_guards()
    scenario.install_dialog_patches()
    scenario.start()
    window.run()
    return scenario.exit_code
