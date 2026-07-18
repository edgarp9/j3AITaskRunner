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



@dataclass(slots=True, frozen=True)
class BulkPromptImportDialogResult:
    """Raw text and options submitted from the bulk import dialog."""

    raw_text: str
    auto_commit_enabled: bool
    step_execution_mode: StepExecutionMode = StepExecutionMode.SINGLE_SESSION

class BulkPromptImportDialog(tk.Toplevel):
    """Modal dialog that accepts multiple prompt blocks for bulk registration."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_auto_commit: bool = True,
        ui_language: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.withdraw()
        self._ui_scale = get_widget_ui_scale(parent)
        self._language = normalize_ui_language(
            ui_language or getattr(parent, "_ui_language", None)
        )
        self._auto_commit_var = tk.BooleanVar(value=initial_auto_commit)
        self._step_execution_mode_var = tk.StringVar(
            value=ui_text("step_execution_mode_single_session", self._language)
        )
        self._text: scrolledtext.ScrolledText | None = None
        self.result: BulkPromptImportDialogResult | None = None

        self.title(ui_text("dialog_import_title", self._language))
        self.geometry(self._ui_scale.geometry(760, 520))
        self.minsize(*self._ui_scale.size(520, 360))
        self.transient(parent)

        apply_dark_theme(self, scale=self._ui_scale)
        self._build_widgets()
        self._bind_shortcuts()
        present_centered_modal(parent, self)
        if self._text is not None:
            self._text.focus_set()

    def show_modal(self) -> BulkPromptImportDialogResult | None:
        """Block until the dialog closes and return the submitted text."""
        self.wait_window(self)
        return self.result

    def _build_widgets(self) -> None:
        container = ttk.Frame(self, padding=self._ui_scale.padding(16))
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        import_text = scrolledtext.ScrolledText(container, wrap="word", undo=True)
        configure_text_widget(import_text, scale=self._ui_scale)
        bind_editable_text_context_menu(
            import_text,
            menu_parent=self,
            language=lambda: self._language,
        )
        import_text.insert(tk.END, BULK_IMPORT_EXAMPLE_TEXT)
        import_text.edit_reset()
        import_text.grid(row=0, column=0, sticky="nsew")
        self._text = import_text

        button_row = ttk.Frame(container)
        button_row.grid(row=1, column=0, sticky="e", pady=self._ui_scale.padding(12, 0))
        ttk.Label(
            button_row,
            text=ui_text("step_execution_mode_label", self._language),
        ).grid(row=0, column=0, padx=self._ui_scale.padding(0, 6))
        ttk.Combobox(
            button_row,
            textvariable=self._step_execution_mode_var,
            values=self._step_execution_mode_labels(),
            state="readonly",
            width=16,
        ).grid(row=0, column=1, padx=self._ui_scale.padding(0, 12))
        ttk.Checkbutton(
            button_row,
            text=ui_text("checkbox_auto_commit", self._language),
            variable=self._auto_commit_var,
        ).grid(row=0, column=2, padx=self._ui_scale.padding(0, 8))
        ttk.Button(
            button_row,
            text=ui_text("button_register", self._language),
            command=self._on_submit,
        ).grid(
            row=0,
            column=3,
            padx=self._ui_scale.padding(0, 8),
        )
        ttk.Button(
            button_row,
            text=ui_text("button_cancel", self._language),
            command=self._on_cancel,
        ).grid(row=0, column=4)

    def _step_execution_mode_labels(self) -> tuple[str, str]:
        return (
            ui_text("step_execution_mode_single_session", self._language),
            ui_text("step_execution_mode_per_step_session", self._language),
        )

    def _selected_step_execution_mode(self) -> StepExecutionMode:
        if self._step_execution_mode_var.get() == ui_text(
            "step_execution_mode_per_step_session",
            self._language,
        ):
            return StepExecutionMode.PER_STEP_SESSION
        return StepExecutionMode.SINGLE_SESSION

    def _bind_shortcuts(self) -> None:
        self.bind("<Escape>", lambda _event: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_submit(self) -> None:
        raw_text = self._text.get("1.0", "end-1c") if self._text is not None else ""
        self.result = BulkPromptImportDialogResult(
            raw_text=raw_text,
            auto_commit_enabled=self._auto_commit_var.get(),
            step_execution_mode=self._selected_step_execution_mode(),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

