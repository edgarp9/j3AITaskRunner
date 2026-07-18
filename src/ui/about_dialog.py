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



class LicenseNoticesDialog(tk.Toplevel):
    """Modal dialog that shows bundled third-party license notices."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        notices: str,
        ui_language: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.withdraw()
        self._notices = notices
        self._ui_scale = get_widget_ui_scale(parent)
        self._language = normalize_ui_language(
            ui_language or getattr(parent, "_ui_language", None)
        )

        self.title(ui_text("settings_licenses", self._language))
        self.geometry(self._ui_scale.geometry(840, 560))
        self.minsize(*self._ui_scale.size(560, 360))
        self.transient(parent)

        apply_dark_theme(self, scale=self._ui_scale)
        self._build_widgets()
        self._bind_shortcuts()
        present_centered_modal(parent, self)

    def show_modal(self) -> None:
        """Block until the dialog is closed."""
        self.wait_window(self)

    def _build_widgets(self) -> None:
        container = ttk.Frame(self, padding=self._ui_scale.padding(16))
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        notices_text = scrolledtext.ScrolledText(container, wrap="word", undo=False)
        configure_text_widget(notices_text, scale=self._ui_scale)
        notices_text.insert(tk.END, self._notices)
        notices_text.configure(state="disabled")
        notices_text.grid(row=0, column=0, sticky="nsew")

        button_row = ttk.Frame(container)
        button_row.grid(row=1, column=0, sticky="e", pady=self._ui_scale.padding(12, 0))
        ttk.Button(
            button_row,
            text=ui_text("button_copy", self._language),
            command=self._copy_notices,
        ).grid(row=0, column=0, padx=self._ui_scale.padding(0, 8))
        ttk.Button(
            button_row,
            text=ui_text("button_close", self._language),
            command=self.destroy,
        ).grid(row=0, column=1)

    def _bind_shortcuts(self) -> None:
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _copy_notices(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._notices)

class AboutDialog(tk.Toplevel):
    """Modal About dialog that displays the bundled About notice."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        app_name: str,
        app_version: str,
        about_notice_loader: AboutNoticeLoader = load_about_notice,
        ui_language: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.withdraw()
        self._app_name = app_name
        self._app_version = app_version
        self._about_notice_loader = about_notice_loader
        self._about_notice = self._load_about_notice()
        self._ui_scale = get_widget_ui_scale(parent)
        self._language = normalize_ui_language(
            ui_language or getattr(parent, "_ui_language", None)
        )

        self.title(ui_text("dialog_about_title", self._language))
        self.geometry(self._ui_scale.geometry(700, 520))
        self.minsize(*self._ui_scale.size(520, 360))
        self.transient(parent)

        apply_dark_theme(self, scale=self._ui_scale)
        self._build_widgets()
        self._bind_shortcuts()
        present_centered_modal(parent, self)

    def show_modal(self) -> None:
        """Block until the dialog is closed."""
        self.wait_window(self)

    def _build_widgets(self) -> None:
        container = ttk.Frame(self, padding=self._ui_scale.padding(16))
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        header = ttk.Frame(container)
        header.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=self._ui_scale.padding(0, 12),
        )
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text=f"{self._app_name} {self._app_version}").grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
        )
        ttk.Label(header, text=ui_text("about_source", self._language)).grid(
            row=1,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
            pady=self._ui_scale.padding(6, 0),
        )
        source_link = ttk.Label(
            header,
            text=ABOUT_SOURCE_URL,
            cursor="hand2",
            style="Link.TLabel",
        )
        source_link.grid(
            row=1,
            column=1,
            sticky="w",
            pady=self._ui_scale.padding(6, 0),
        )
        source_link.bind("<Button-1>", lambda _event: self._open_source_link())

        about_text = scrolledtext.ScrolledText(container, wrap="word", undo=False)
        configure_text_widget(about_text, scale=self._ui_scale)
        about_text.insert(tk.END, self._about_notice)
        about_text.configure(state="disabled")
        about_text.grid(row=1, column=0, sticky="nsew")

        button_row = ttk.Frame(container)
        button_row.grid(
            row=2,
            column=0,
            sticky="e",
            pady=self._ui_scale.padding(16, 0),
        )
        ttk.Button(
            button_row,
            text=ui_text("button_copy", self._language),
            command=self._copy_about_notice,
        ).grid(row=0, column=0, padx=self._ui_scale.padding(0, 8))
        ttk.Button(
            button_row,
            text=ui_text("button_close", self._language),
            command=self.destroy,
        ).grid(row=0, column=1)

    def _bind_shortcuts(self) -> None:
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _load_about_notice(self) -> str:
        try:
            return self._about_notice_loader()
        except Exception:
            LOGGER.exception("Failed to load About notice.")
            return (
                f"{self._app_name}\n\n"
                f"Version: {self._app_version}\n"
                f"Copyright: {APP_COPYRIGHT}\n"
                "License: GNU General Public License v3.0 or later "
                "(GPL-3.0-or-later)\n\n"
                "The bundled about.txt file could not be loaded. See LICENSE and "
                "THIRD_PARTY_NOTICES.txt for license details."
            )

    def _copy_about_notice(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._about_notice)

    def _open_author_link(self) -> None:
        _open_project_author_link("about")

    def _open_source_link(self) -> None:
        _open_project_author_link("about source")

