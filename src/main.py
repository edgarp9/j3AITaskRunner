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


def build_runtime(*, storage_root: Path | None = None) -> AppRuntime:
    """Build the application runtime with persistence and process runner wiring."""
    resolved_storage_root = storage_root or resolve_default_storage_root()
    repository = LocalJsonRepository(resolved_storage_root)
    prompt_store = PromptStore(resolved_storage_root)
    runner = ProviderAgentCliProcessRunner(
        resolved_storage_root / APP_ARTIFACT_DIR_NAME / "artifacts"
    )

    runtime: AppRuntime | None = None
    controller = AppController(
        runner=runner,
        settings_provider=lambda: runtime.settings if runtime is not None else AppSettings(),
    )
    runtime = AppRuntime(
        controller=controller,
        repository=repository,
        prompt_store=prompt_store,
        system_sleep_preventer=SystemSleepPreventer(),
    )
    return runtime


def build_main_window(*, storage_root: Path | None = None) -> MainWindow:
    """Build the Tkinter main window for execution or smoke testing."""
    configure_windows_dpi_awareness()
    runtime = build_runtime(storage_root=storage_root)
    return MainWindow(runtime)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for source execution."""
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Tkinter desktop application."""
    parse_args(argv)
    configure_logging()
    LOGGER.info("Starting j3AITaskRunner.")
    window = build_main_window()
    window.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
