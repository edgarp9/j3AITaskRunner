"""Modal dialogs used by the Tkinter UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import webbrowser

from app.agent_cli_version import load_agent_cli_version_text
from app.agent_cli_options import (
    SelectOption,
    build_agent_provider_select_options,
    build_model_select_options,
    build_reasoning_select_options,
    find_option_label,
)
from app.version import APP_COPYRIGHT
from domain import (
    AppSettings,
    StepExecutionMode,
    normalize_agent_executable_paths,
    normalize_agent_provider,
    normalize_ui_language,
)
from domain.models import (
    EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
    TERMINATION_GRACE_SECONDS_MAX,
)
from infra.license_notices import load_about_notice, load_license_notices

from .dpi import get_widget_ui_scale
from .i18n import (
    language_label,
    language_options,
    localize_runtime_message,
    text as ui_text,
)
from .text_context_menu import bind_editable_text_context_menu
from .theme import apply_dark_theme, configure_text_widget
from .windowing import present_centered_modal

OUTPUT_FONT_SIZE_MIN = 1
OUTPUT_FONT_SIZE_MAX = 72
AGENT_CLI_VERSION_POLL_INTERVAL_MS = 80
BULK_IMPORT_EXAMPLE_TEXT = "```text\nstep 1\n```\n\n```text\nstep 2\n```\n"
PROJECT_AUTHOR_URL = "https://github.com/edgarp9"
ABOUT_SOURCE_URL = PROJECT_AUTHOR_URL
SETTINGS_AUTHOR_URL = PROJECT_AUTHOR_URL
SETTINGS_COMBOBOX_WIDTH = 22
SETTINGS_EXECUTABLE_ENTRY_WIDTH = 24
SETTINGS_GENERAL_COMBOBOX_WIDTH = 18
SETTINGS_NUMBER_ENTRY_WIDTH = 8

LOGGER = logging.getLogger(__name__)
AgentCliVersionLoader = Callable[[str | None, str | None], str]
LicenseNoticesLoader = Callable[[], str]
AboutNoticeLoader = Callable[[], str]
VersionProbeKey = tuple[str, str | None]


def _open_project_author_link(context: str) -> None:
    try:
        opened = webbrowser.open_new_tab(PROJECT_AUTHOR_URL)
    except Exception:
        LOGGER.exception("Failed to open %s author link.", context)
        return
    if not opened:
        LOGGER.warning(
            "Browser did not report opening %s author link. url=%s",
            context,
            PROJECT_AUTHOR_URL,
        )


def _open_license_notices_dialog(
    parent: tk.Misc,
    *,
    language: str,
    license_notices_loader: LicenseNoticesLoader,
) -> None:
    try:
        notices = license_notices_loader()
    except Exception:
        LOGGER.exception("Failed to load third-party license notices.")
        messagebox.showerror(
            ui_text("dialog_licenses_error", language),
            ui_text("dialog_licenses_load_failed", language),
            parent=parent,
        )
        return

    dialog = LicenseNoticesDialog(
        parent,
        notices=notices,
        ui_language=language,
    )
    dialog.show_modal()



from .about_dialog import AboutDialog, LicenseNoticesDialog
from .bulk_prompt_import_dialog import BulkPromptImportDialog, BulkPromptImportDialogResult
from .prompt_viewer_dialog import PromptViewerDialog
from .scheduled_run_dialog import (
    ScheduledRunDialog,
    ScheduledRunDialogResult,
    ScheduledRunValidationError,
    default_scheduled_run_time,
    parse_scheduled_run_datetime,
)
from .session_exit_hook_dialog import SessionExitHookDialog
from .settings_dialog import SettingsDialog
