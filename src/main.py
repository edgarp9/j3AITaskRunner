#!/usr/bin/env python3
"""Application entry point for j3AITaskRunner."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
from pathlib import Path, PureWindowsPath
import sys

from app import AppController, AppRuntime
from app.version import APP_NAME, APP_VERSION
from domain import AppSettings
from infra.process_runner import ProviderAgentCliProcessRunner
from infra.repository import LocalJsonRepository, PromptStore
from infra.system_sleep import SystemSleepPreventer
from infra.windows_dpi import configure_windows_dpi_awareness
from ui import MainWindow

LOGGER = logging.getLogger(__name__)
APP_ARTIFACT_DIR_NAME = ".j3aitaskrunner"


def configure_logging() -> None:
    """Configure a minimal logging setup for the desktop app."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def resolve_default_storage_root() -> Path:
    """Return the directory where persistent app data should live."""
    if getattr(sys, "frozen", False):
        return _parent_directory(sys.executable)
    return _parent_directory(__file__)


def _parent_directory(path_text: str) -> Path:
    if "\\" in path_text or PureWindowsPath(path_text).drive:
        return Path(str(PureWindowsPath(path_text).parent))
    return Path(path_text).resolve().parent


def build_runtime(
    *,
    storage_root: Path | None = None,
    app_base_dir: Path | None = None,
) -> AppRuntime:
    """Build the application runtime with persistence and process runner wiring."""
    resolved_storage_root = storage_root or resolve_default_storage_root()
    resolved_app_base_dir = app_base_dir or resolved_storage_root
    repository = LocalJsonRepository(resolved_storage_root)
    prompt_store = PromptStore(resolved_app_base_dir)
    runner = ProviderAgentCliProcessRunner(
        resolved_storage_root / APP_ARTIFACT_DIR_NAME / "artifacts"
    )

    runtime: AppRuntime | None = None
    controller = AppController(
        runner=runner,
        settings_provider=lambda: (
            runtime.settings if runtime is not None else AppSettings()
        ),
    )
    runtime = AppRuntime(
        controller=controller,
        repository=repository,
        prompt_store=prompt_store,
        system_sleep_preventer=SystemSleepPreventer(),
        file_drop_dir=resolved_app_base_dir / "watch",
    )
    return runtime


def build_main_window(
    *,
    storage_root: Path | None = None,
    app_base_dir: Path | None = None,
) -> MainWindow:
    """Build the Tkinter main window for execution or smoke testing."""
    configure_windows_dpi_awareness()
    if app_base_dir is None:
        runtime = build_runtime(storage_root=storage_root)
    else:
        runtime = build_runtime(storage_root=storage_root, app_base_dir=app_base_dir)
    return MainWindow(runtime)


def resolve_startup_workspace_paths(
    workspace_paths: Sequence[str],
    *,
    base_dir: Path | None = None,
) -> tuple[str, ...]:
    """Resolve startup workspace paths relative to cwd or an explicit base dir."""
    resolved_base_dir = base_dir or Path.cwd()
    return tuple(
        _resolve_startup_workspace_path(path, base_dir=resolved_base_dir)
        for path in workspace_paths
    )


def resolve_data_dir_path(
    data_dir: str | None, *, base_dir: Path | None = None
) -> Path | None:
    """Resolve an optional persistent data directory argument."""
    if data_dir is None:
        return None

    resolved_base_dir = base_dir or Path.cwd()
    try:
        path = Path(data_dir).expanduser()
        if not path.is_absolute():
            path = resolved_base_dir / path
        return path.resolve()
    except (OSError, RuntimeError):
        LOGGER.warning(
            "Failed to resolve data directory; passing it to runtime. data_dir=%s",
            data_dir,
            exc_info=True,
        )
        return Path(data_dir)


def _resolve_startup_workspace_path(workspace_path: str, *, base_dir: Path) -> str:
    try:
        path = Path(workspace_path).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        return str(path.resolve())
    except (OSError, RuntimeError):
        LOGGER.warning(
            "Failed to resolve startup workspace path; passing it to runtime. "
            "workspace_path=%s",
            workspace_path,
            exc_info=True,
        )
        return workspace_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for source execution."""
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
    )
    parser.add_argument(
        "--data-dir",
        metavar="path",
        help="Directory for persistent app data and execution artifacts.",
    )
    parser.add_argument(
        "--ui-smoke-report",
        metavar="path",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--ui-smoke-timeout",
        type=float,
        default=30.0,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "workspace_paths",
        nargs="*",
        metavar="workspace_path",
        help="Workspace path to open after startup.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Tkinter desktop application."""
    args = parse_args(argv)
    configure_logging()
    startup_workspace_paths = resolve_startup_workspace_paths(args.workspace_paths)
    storage_root = resolve_data_dir_path(args.data_dir)
    app_base_dir = resolve_default_storage_root() if storage_root is not None else None
    LOGGER.info("Starting j3AITaskRunner.")
    if storage_root is None:
        window = build_main_window()
    else:
        window = build_main_window(
            storage_root=storage_root,
            app_base_dir=app_base_dir,
        )
    if args.ui_smoke_report is not None:
        from tools.ui_smoke.app_process import run_ui_smoke

        return run_ui_smoke(
            window=window,
            workspace_paths=startup_workspace_paths,
            report_path=Path(args.ui_smoke_report),
            timeout_seconds=args.ui_smoke_timeout,
        )
    if startup_workspace_paths:
        window.open_startup_workspaces(startup_workspace_paths)
    window.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
