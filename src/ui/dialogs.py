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


@dataclass(slots=True, frozen=True)
class ScheduledRunDialogResult:
    """Scheduled run selection from the sidebar dialog."""

    scheduled_at: datetime | None


class ScheduledRunValidationError(ValueError):
    """Validation error with a UI translation key."""

    def __init__(self, message_key: str) -> None:
        super().__init__(message_key)
        self.message_key = message_key


def default_scheduled_run_time(now: datetime | None = None) -> datetime:
    """Return the default local scheduled-run time shown in the dialog."""
    current = now or datetime.now()
    candidate = (current + timedelta(minutes=5)).replace(second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(minutes=1)
    return candidate


def parse_scheduled_run_datetime(
    year_text: str,
    month_text: str,
    day_text: str,
    hour_text: str,
    minute_text: str,
    *,
    now: datetime | None = None,
) -> datetime:
    """Parse and validate split scheduled-run date/time inputs."""
    try:
        year = int(year_text.strip())
        month = int(month_text.strip())
        day = int(day_text.strip())
        hour = int(hour_text.strip())
        minute = int(minute_text.strip())
    except ValueError as exc:
        raise ScheduledRunValidationError(
            "dialog_scheduled_run_integer_required"
        ) from exc

    try:
        scheduled_at = datetime(year, month, day, hour, minute)
    except ValueError as exc:
        raise ScheduledRunValidationError(
            "dialog_scheduled_run_invalid_datetime"
        ) from exc

    if scheduled_at <= (now or datetime.now()):
        raise ScheduledRunValidationError("dialog_scheduled_run_future_required")
    return scheduled_at


class ScheduledRunDialog(tk.Toplevel):
    """Modal dialog for scheduling a one-shot run in the current app session."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        scheduled_at: datetime | None = None,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__(parent)
        self.withdraw()
        self._ui_scale = get_widget_ui_scale(parent)
        self._language = normalize_ui_language(getattr(parent, "_ui_language", None))
        self._now_provider = now_provider
        self._has_existing_schedule = scheduled_at is not None
        self.result: ScheduledRunDialogResult | None = None

        initial = scheduled_at or default_scheduled_run_time(now_provider())
        self._year_var = tk.StringVar(value=f"{initial.year:04d}")
        self._month_var = tk.StringVar(value=f"{initial.month:02d}")
        self._day_var = tk.StringVar(value=f"{initial.day:02d}")
        self._hour_var = tk.StringVar(value=f"{initial.hour:02d}")
        self._minute_var = tk.StringVar(value=f"{initial.minute:02d}")

        self.title(ui_text("dialog_scheduled_run_title", self._language))
        self.resizable(False, False)
        self.transient(parent)
        apply_dark_theme(self, scale=self._ui_scale)
        self._build_widgets()
        self._bind_shortcuts()
        present_centered_modal(parent, self)

    def show_modal(self) -> ScheduledRunDialogResult | None:
        """Block until the dialog closes and return the selected action."""
        self.wait_window(self)
        return self.result

    def _build_widgets(self) -> None:
        container = ttk.Frame(self, padding=self._ui_scale.padding(16))
        container.grid(sticky="nsew")

        ttk.Label(
            container,
            text=ui_text("scheduled_run_datetime", self._language),
        ).grid(row=0, column=0, columnspan=5, sticky="w")

        self._add_time_entry(
            container,
            row=1,
            column=0,
            label_key="scheduled_run_year",
            variable=self._year_var,
            width=6,
        )
        self._add_time_entry(
            container,
            row=1,
            column=1,
            label_key="scheduled_run_month",
            variable=self._month_var,
            width=4,
        )
        self._add_time_entry(
            container,
            row=1,
            column=2,
            label_key="scheduled_run_day",
            variable=self._day_var,
            width=4,
        )
        self._add_time_entry(
            container,
            row=1,
            column=3,
            label_key="scheduled_run_hour",
            variable=self._hour_var,
            width=4,
        )
        self._add_time_entry(
            container,
            row=1,
            column=4,
            label_key="scheduled_run_minute",
            variable=self._minute_var,
            width=4,
        )

        button_row = ttk.Frame(container)
        button_row.grid(
            row=2,
            column=0,
            columnspan=5,
            sticky="e",
            pady=self._ui_scale.padding(16, 0),
        )
        ttk.Button(
            button_row,
            text=ui_text("button_save", self._language),
            command=self._on_submit,
        ).grid(row=0, column=0, padx=self._ui_scale.padding(0, 8))
        if self._has_existing_schedule:
            ttk.Button(
                button_row,
                text=ui_text("button_cancel_schedule", self._language),
                command=self._on_cancel_schedule,
            ).grid(row=0, column=1, padx=self._ui_scale.padding(0, 8))
            cancel_column = 2
        else:
            cancel_column = 1
        ttk.Button(
            button_row,
            text=ui_text("button_cancel", self._language),
            command=self._on_cancel,
        ).grid(row=0, column=cancel_column)

    def _add_time_entry(
        self,
        parent: ttk.Frame,
        *,
        row: int,
        column: int,
        label_key: str,
        variable: tk.StringVar,
        width: int,
    ) -> None:
        field = ttk.Frame(parent)
        field.grid(
            row=row,
            column=column,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
            pady=self._ui_scale.padding(10, 0),
        )
        ttk.Label(field, text=ui_text(label_key, self._language)).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Entry(field, textvariable=variable, width=width).grid(
            row=1,
            column=0,
            sticky="w",
            pady=self._ui_scale.padding(4, 0),
        )

    def _bind_shortcuts(self) -> None:
        self.bind("<Return>", lambda _event: self._on_submit())
        self.bind("<Escape>", lambda _event: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_submit(self) -> None:
        try:
            scheduled_at = parse_scheduled_run_datetime(
                self._year_var.get(),
                self._month_var.get(),
                self._day_var.get(),
                self._hour_var.get(),
                self._minute_var.get(),
                now=self._now_provider(),
            )
        except ScheduledRunValidationError as error:
            messagebox.showerror(
                ui_text("dialog_scheduled_run_error", self._language),
                ui_text(error.message_key, self._language),
                parent=self,
            )
            return

        self.result = ScheduledRunDialogResult(scheduled_at=scheduled_at)
        self.destroy()

    def _on_cancel_schedule(self) -> None:
        self.result = ScheduledRunDialogResult(scheduled_at=None)
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class PromptViewerDialog(tk.Toplevel):
    """Modal dialog that shows one job prompt in a scrollable text editor."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        job_id: str,
        prompt: str,
        ui_language: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.withdraw()
        self._prompt = prompt
        self._ui_scale = get_widget_ui_scale(parent)
        self._language = normalize_ui_language(
            ui_language or getattr(parent, "_ui_language", None)
        )

        self.title(ui_text("dialog_prompt_view_title", self._language, job_id=job_id))
        self.geometry(self._ui_scale.geometry(760, 520))
        self.minsize(*self._ui_scale.size(520, 360))
        self.transient(parent)

        apply_dark_theme(self, scale=self._ui_scale)
        self._build_widgets(job_id=job_id, prompt=prompt)
        self._bind_shortcuts()
        present_centered_modal(parent, self)

    def show_modal(self) -> None:
        """Block until the dialog is closed."""
        self.wait_window(self)

    def _build_widgets(self, *, job_id: str, prompt: str) -> None:
        container = ttk.Frame(self, padding=self._ui_scale.padding(16))
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        ttk.Label(
            container,
            text=ui_text("dialog_prompt_view_title", self._language, job_id=job_id),
            font=("", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=self._ui_scale.padding(0, 8))

        prompt_text = scrolledtext.ScrolledText(container, wrap="word", undo=False)
        configure_text_widget(prompt_text, scale=self._ui_scale)
        prompt_text.insert(tk.END, prompt)
        prompt_text.configure(state="disabled")
        prompt_text.grid(row=1, column=0, sticky="nsew")

        button_row = ttk.Frame(container)
        button_row.grid(row=2, column=0, sticky="e", pady=self._ui_scale.padding(12, 0))
        ttk.Button(
            button_row,
            text=ui_text("button_copy", self._language),
            command=self._copy_prompt,
        ).grid(
            row=0,
            column=0,
            padx=self._ui_scale.padding(0, 8),
        )
        ttk.Button(
            button_row,
            text=ui_text("button_close", self._language),
            command=self.destroy,
        ).grid(row=0, column=1)

    def _bind_shortcuts(self) -> None:
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _copy_prompt(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._prompt)


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
        ttk.Checkbutton(
            button_row,
            text=ui_text("checkbox_auto_commit", self._language),
            variable=self._auto_commit_var,
        ).grid(row=0, column=0, padx=self._ui_scale.padding(0, 8))
        ttk.Button(
            button_row,
            text=ui_text("button_register", self._language),
            command=self._on_submit,
        ).grid(
            row=0,
            column=1,
            padx=self._ui_scale.padding(0, 8),
        )
        ttk.Button(
            button_row,
            text=ui_text("button_cancel", self._language),
            command=self._on_cancel,
        ).grid(row=0, column=2)

    def _bind_shortcuts(self) -> None:
        self.bind("<Escape>", lambda _event: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_submit(self) -> None:
        raw_text = self._text.get("1.0", "end-1c") if self._text is not None else ""
        self.result = BulkPromptImportDialogResult(
            raw_text=raw_text,
            auto_commit_enabled=self._auto_commit_var.get(),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


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


class SettingsDialog(tk.Toplevel):
    """Modal settings dialog centered over the main window."""

    def __init__(
        self,
        parent: tk.Misc,
        current_settings: AppSettings,
        *,
        app_name: str,
        app_version: str,
        agent_cli_version_loader: AgentCliVersionLoader = load_agent_cli_version_text,
        license_notices_loader: LicenseNoticesLoader = load_license_notices,
    ) -> None:
        super().__init__(parent)
        self.withdraw()
        self._ui_scale = get_widget_ui_scale(parent)
        self._language = normalize_ui_language(current_settings.ui_language)
        self.title(ui_text("dialog_settings_title", self._language))
        self.resizable(False, False)
        self.transient(parent)
        self.result: AppSettings | None = None
        self._closed = False
        self._app_name = app_name
        self._app_version = app_version
        self._agent_provider = normalize_agent_provider(current_settings.agent_provider)
        self._executable_paths = normalize_agent_executable_paths(
            current_settings.executable_paths
        )
        if current_settings.executable_path:
            self._executable_paths[self._agent_provider] = current_settings.executable_path
        self._agent_cli_version_loader = agent_cli_version_loader
        self._license_notices_loader = license_notices_loader
        self._agent_cli_version_request_id = 0
        self._agent_cli_version_request_sequence = 0
        self._agent_cli_version_current_key: VersionProbeKey = (
            self._agent_provider,
            None,
        )
        self._agent_cli_version_cache: dict[VersionProbeKey, str] = {}
        self._agent_cli_version_inflight: dict[VersionProbeKey, int] = {}
        self._agent_cli_version_queue: Queue[
            tuple[int, VersionProbeKey, str]
        ] = Queue()
        self._agent_cli_version_after_id: str | None = None

        apply_dark_theme(self, scale=self._ui_scale)
        self._agent_provider_options = build_agent_provider_select_options(
            current_settings.agent_provider
        )
        self._ui_language_options = language_options(self._language)
        self._model_options = self._build_model_options(
            current_settings.default_model
        )
        default_model = self._option_value_or_default(
            self._model_options,
            current_settings.default_model,
        )
        self._reasoning_options = self._build_reasoning_options(
            current_settings.default_reasoning_effort,
            model=default_model,
        )
        default_reasoning_effort = self._option_value_or_default(
            self._reasoning_options,
            current_settings.default_reasoning_effort,
        )
        self._executable_var = tk.StringVar(
            value=self._executable_paths.get(self._agent_provider, "")
        )
        self._agent_provider_var = tk.StringVar(
            value=find_option_label(
                self._agent_provider_options,
                self._agent_provider,
            )
        )
        self._model_var = tk.StringVar(
            value=find_option_label(self._model_options, default_model)
        )
        self._reasoning_var = tk.StringVar(
            value=find_option_label(
                self._reasoning_options,
                default_reasoning_effort,
            )
        )
        self._font_size_var = tk.StringVar(value=str(current_settings.output_font_size))
        self._execution_timeout_var = tk.StringVar(
            value=str(current_settings.execution_timeout_minutes)
        )
        self._inactivity_timeout_var = tk.StringVar(
            value=str(current_settings.inactivity_timeout_minutes)
        )
        self._termination_grace_var = tk.StringVar(
            value=str(current_settings.termination_grace_seconds)
        )
        self._file_logging_var = tk.BooleanVar(
            value=current_settings.file_logging_enabled
        )
        self._ui_language_var = tk.StringVar(
            value=language_label(current_settings.ui_language, self._language)
        )
        self._agent_cli_version_var = tk.StringVar(
            value=ui_text("settings_checking", self._language)
        )

        self._build_widgets()
        self._bind_shortcuts()
        self._start_agent_cli_version_refresh()
        present_centered_modal(parent, self)

    def show_modal(self) -> AppSettings | None:
        """Block until the dialog closes and return the edited settings."""
        self.wait_window(self)
        return self.result

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        container = ttk.Frame(self, padding=self._ui_scale.padding(16))
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)

        overview_section = self._build_overview_section(container)
        overview_section.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=self._ui_scale.padding(0, 10),
        )

        content_frame = ttk.Frame(container)
        content_frame.grid(row=1, column=0, sticky="nsew")
        content_frame.columnconfigure(0, weight=0)
        content_frame.columnconfigure(1, weight=1)

        general_section = self._build_general_section(content_frame)
        general_section.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=self._ui_scale.padding(0, 6),
            pady=self._ui_scale.padding(0, 10),
        )

        execution_section = self._build_execution_limits_section(content_frame)
        execution_section.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=self._ui_scale.padding(0, 6),
        )

        ai_section = self._build_workspace_ai_section(content_frame)
        ai_section.grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="nsew",
            padx=self._ui_scale.padding(6, 0),
        )

        footer_row = ttk.Frame(container)
        footer_row.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=self._ui_scale.padding(16, 0),
        )
        footer_row.columnconfigure(0, weight=1)
        author_link = ttk.Label(
            footer_row,
            text=SETTINGS_AUTHOR_URL,
            cursor="hand2",
            style="Link.TLabel",
        )
        author_link.grid(row=0, column=0, sticky="w")
        author_link.bind("<Button-1>", lambda _event: self._open_author_link())

        button_row = ttk.Frame(footer_row)
        button_row.grid(row=0, column=1, sticky="e")
        ttk.Button(
            button_row,
            text=ui_text("button_save", self._language),
            command=self._on_submit,
        ).grid(
            row=0,
            column=0,
            padx=self._ui_scale.padding(0, 8),
        )
        ttk.Button(
            button_row,
            text=ui_text("button_cancel", self._language),
            command=self._on_cancel,
        ).grid(
            row=0,
            column=1,
        )

    def _build_overview_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        section = ttk.LabelFrame(
            parent,
            text=ui_text("settings_status_section", self._language),
            padding=self._ui_scale.padding(12),
        )
        section.columnconfigure(1, weight=1)
        section.columnconfigure(3, weight=1)
        section.columnconfigure(4, weight=0)

        ttk.Label(section, text=ui_text("settings_app", self._language)).grid(
            row=0,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        ttk.Label(section, text=f"{self._app_name} v{self._app_version}").grid(
            row=0,
            column=1,
            sticky="w",
            padx=self._ui_scale.padding(0, 20),
        )

        ttk.Label(section, text=ui_text("settings_cli_version", self._language)).grid(
            row=0,
            column=2,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
        )
        ttk.Label(
            section,
            textvariable=self._agent_cli_version_var,
            wraplength=self._ui_scale.px(280),
        ).grid(row=0, column=3, sticky="w")
        ttk.Button(
            section,
            text=ui_text("settings_licenses", self._language),
            command=self._open_license_notices_dialog,
        ).grid(
            row=0,
            column=4,
            sticky="e",
            padx=self._ui_scale.padding(12, 0),
        )
        return section

    def _build_general_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        section = ttk.LabelFrame(
            parent,
            text=ui_text("settings_general_section", self._language),
            padding=self._ui_scale.padding(12),
        )
        section.columnconfigure(1, weight=1)

        ttk.Label(section, text=ui_text("settings_ui_language", self._language)).grid(
            row=0,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
            pady=self._ui_scale.padding(0, 8),
        )
        ttk.Combobox(
            section,
            textvariable=self._ui_language_var,
            values=[option.label for option in self._ui_language_options],
            width=SETTINGS_GENERAL_COMBOBOX_WIDTH,
            state="readonly",
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=self._ui_scale.padding(0, 8),
        )

        ttk.Label(section, text=ui_text("settings_font_size", self._language)).grid(
            row=1,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
            pady=self._ui_scale.padding(0, 8),
        )
        ttk.Entry(
            section,
            textvariable=self._font_size_var,
            width=SETTINGS_NUMBER_ENTRY_WIDTH,
        ).grid(
            row=1,
            column=1,
            sticky="w",
            pady=self._ui_scale.padding(0, 8),
        )

        ttk.Checkbutton(
            section,
            text=ui_text("settings_file_logging", self._language),
            variable=self._file_logging_var,
        ).grid(row=2, column=1, sticky="w")
        return section

    def _build_execution_limits_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        section = ttk.LabelFrame(
            parent,
            text=ui_text("settings_execution_limits_section", self._language),
            padding=self._ui_scale.padding(12),
        )
        section.columnconfigure(1, weight=1)

        self._grid_number_setting(
            section,
            row=0,
            label_key="settings_execution_timeout",
            variable=self._execution_timeout_var,
        )
        self._grid_number_setting(
            section,
            row=1,
            label_key="settings_inactivity_timeout",
            variable=self._inactivity_timeout_var,
        )
        self._grid_number_setting(
            section,
            row=2,
            label_key="settings_termination_grace",
            variable=self._termination_grace_var,
            bottom_padding=0,
        )
        return section

    def _grid_number_setting(
        self,
        parent: tk.Misc,
        *,
        row: int,
        label_key: str,
        variable: tk.StringVar,
        bottom_padding: int = 8,
    ) -> None:
        ttk.Label(parent, text=ui_text(label_key, self._language)).grid(
            row=row,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
            pady=self._ui_scale.padding(0, bottom_padding),
        )
        ttk.Entry(
            parent,
            textvariable=variable,
            width=SETTINGS_NUMBER_ENTRY_WIDTH,
        ).grid(
            row=row,
            column=1,
            sticky="w",
            pady=self._ui_scale.padding(0, bottom_padding),
        )

    def _build_workspace_ai_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        section = ttk.LabelFrame(
            parent,
            text=ui_text("settings_workspace_default_ai", self._language),
            padding=self._ui_scale.padding(12),
        )
        section.columnconfigure(1, weight=1)

        ttk.Label(section, text=ui_text("settings_provider", self._language)).grid(
            row=0,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
            pady=self._ui_scale.padding(0, 8),
        )
        provider_combobox = ttk.Combobox(
            section,
            textvariable=self._agent_provider_var,
            values=[option.label for option in self._agent_provider_options],
            width=SETTINGS_COMBOBOX_WIDTH,
            state="readonly",
        )
        provider_combobox.grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=self._ui_scale.padding(0, 8),
        )
        provider_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._on_agent_provider_changed(),
        )
        self._agent_provider_combobox = provider_combobox

        ttk.Label(section, text=ui_text("settings_executable", self._language)).grid(
            row=1,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
            pady=self._ui_scale.padding(0, 8),
        )
        executable_entry = ttk.Entry(
            section,
            textvariable=self._executable_var,
            width=SETTINGS_EXECUTABLE_ENTRY_WIDTH,
        )
        executable_entry.grid(
            row=1,
            column=1,
            sticky="ew",
            pady=self._ui_scale.padding(0, 8),
        )
        executable_entry.bind(
            "<FocusOut>",
            lambda _event: self._on_executable_focus_out(),
        )
        ttk.Button(
            section,
            text=ui_text("button_find", self._language),
            command=self._browse_executable,
        ).grid(
            row=1,
            column=2,
            padx=self._ui_scale.padding(8, 0),
            pady=self._ui_scale.padding(0, 8),
        )

        options_frame = ttk.Frame(section)
        options_frame.grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=self._ui_scale.padding(2, 0),
        )
        options_frame.columnconfigure(0, weight=1, uniform="settings_ai_options")
        options_frame.columnconfigure(1, weight=1, uniform="settings_ai_options")

        model_frame = ttk.Frame(options_frame)
        model_frame.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=self._ui_scale.padding(0, 6),
        )
        model_frame.columnconfigure(0, weight=1)
        ttk.Label(model_frame, text=ui_text("settings_model", self._language)).grid(
            row=0,
            column=0,
            sticky="w",
            pady=self._ui_scale.padding(0, 4),
        )
        model_combobox = ttk.Combobox(
            model_frame,
            textvariable=self._model_var,
            values=[option.label for option in self._model_options],
            width=SETTINGS_COMBOBOX_WIDTH,
            state="readonly",
        )
        model_combobox.grid(row=1, column=0, sticky="ew")
        model_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._on_model_changed(),
        )
        self._model_combobox = model_combobox

        reasoning_frame = ttk.Frame(options_frame)
        reasoning_frame.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=self._ui_scale.padding(6, 0),
        )
        reasoning_frame.columnconfigure(0, weight=1)
        ttk.Label(
            reasoning_frame,
            text=ui_text("settings_reasoning", self._language),
        ).grid(
            row=0,
            column=0,
            sticky="w",
            pady=self._ui_scale.padding(0, 4),
        )
        reasoning_combobox = ttk.Combobox(
            reasoning_frame,
            textvariable=self._reasoning_var,
            values=[option.label for option in self._reasoning_options],
            width=SETTINGS_COMBOBOX_WIDTH,
            state="readonly",
        )
        reasoning_combobox.grid(row=1, column=0, sticky="ew")
        self._reasoning_combobox = reasoning_combobox
        return section

    def _bind_shortcuts(self) -> None:
        self.bind("<Return>", lambda _event: self._on_submit())
        self.bind("<Escape>", lambda _event: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_agent_provider_changed(self) -> None:
        previous_provider = self._agent_provider
        self._remember_executable_path(previous_provider)
        selected_provider = self._selected_value(
            self._agent_provider_options,
            self._agent_provider_var.get(),
        )
        self._agent_provider = normalize_agent_provider(selected_provider)
        self._executable_var.set(self._executable_paths.get(self._agent_provider, ""))
        self._set_default_ai_selection(model_value="", reasoning_value="")
        self._start_agent_cli_version_refresh()

    def _on_model_changed(self) -> None:
        selected_model = self._selected_value(
            self._model_options,
            self._model_var.get(),
        )
        self._set_default_ai_selection(
            model_value=selected_model,
            reasoning_value="",
        )

    def _on_executable_focus_out(self) -> None:
        self._remember_executable_path(self._agent_provider)
        self._start_agent_cli_version_refresh()

    def _remember_executable_path(self, agent_provider: str) -> None:
        executable_path = self._executable_var.get().strip()
        normalized_provider = normalize_agent_provider(agent_provider)
        if executable_path:
            self._executable_paths[normalized_provider] = executable_path
        else:
            self._executable_paths.pop(normalized_provider, None)

    def _browse_executable(self) -> None:
        selected_path = filedialog.askopenfilename(
            parent=self,
            title=ui_text("dialog_executable_select", self._language),
        )
        if selected_path:
            self._executable_var.set(selected_path)
            self._remember_executable_path(self._agent_provider)
            self._start_agent_cli_version_refresh()

    def _start_agent_cli_version_refresh(self) -> None:
        executable_path = self._executable_var.get().strip() or None
        provider = self._current_agent_provider()
        probe_key = (provider, executable_path)
        self._agent_cli_version_current_key = probe_key

        if probe_key in self._agent_cli_version_cache:
            self._agent_cli_version_request_id = (
                self._next_agent_cli_version_request_id()
            )
            self._agent_cli_version_var.set(
                self._localize_agent_cli_version_text(
                    self._agent_cli_version_cache[probe_key]
                )
            )
            return

        inflight_request_id = self._agent_cli_version_inflight.get(probe_key)
        if inflight_request_id is not None:
            self._agent_cli_version_request_id = inflight_request_id
            self._agent_cli_version_var.set(ui_text("settings_checking", self._language))
            self._schedule_agent_cli_version_poll()
            return

        request_id = self._next_agent_cli_version_request_id()
        self._agent_cli_version_request_id = request_id
        self._agent_cli_version_var.set(ui_text("settings_checking", self._language))

        worker = threading.Thread(
            target=self._load_agent_cli_version_in_background,
            args=(request_id, probe_key),
            name="agent-cli-version-probe",
            daemon=True,
        )
        self._agent_cli_version_inflight[probe_key] = request_id
        try:
            worker.start()
        except RuntimeError:
            if self._agent_cli_version_inflight.get(probe_key) == request_id:
                self._agent_cli_version_inflight.pop(probe_key, None)
            LOGGER.exception("Failed to start agent CLI version probe thread.")
            self._agent_cli_version_var.set(
                ui_text("settings_version_unavailable", self._language)
            )
            return
        self._schedule_agent_cli_version_poll()

    def _next_agent_cli_version_request_id(self) -> int:
        self._agent_cli_version_request_sequence += 1
        return self._agent_cli_version_request_sequence

    def _load_agent_cli_version_in_background(
        self,
        request_id: int,
        probe_key: VersionProbeKey,
    ) -> None:
        provider, executable_path = probe_key
        try:
            version_text = self._agent_cli_version_loader(executable_path, provider)
        except Exception:
            LOGGER.exception("Failed to probe agent CLI version.")
            version_text = ui_text("settings_version_unavailable", self._language)
        self._agent_cli_version_queue.put((request_id, probe_key, version_text))

    def _schedule_agent_cli_version_poll(self) -> None:
        if self._closed or self._agent_cli_version_after_id is not None:
            return
        try:
            self._agent_cli_version_after_id = self.after(
                AGENT_CLI_VERSION_POLL_INTERVAL_MS,
                self._poll_agent_cli_version_result,
            )
        except tk.TclError:
            self._agent_cli_version_after_id = None

    def _poll_agent_cli_version_result(self) -> None:
        self._agent_cli_version_after_id = None
        if self._closed:
            return

        applied_current_result = False
        while True:
            try:
                request_id, probe_key, version_text = (
                    self._agent_cli_version_queue.get_nowait()
                )
            except Empty:
                break
            if self._agent_cli_version_inflight.get(probe_key) == request_id:
                self._agent_cli_version_inflight.pop(probe_key, None)
                self._agent_cli_version_cache[probe_key] = version_text
            if request_id == self._agent_cli_version_request_id:
                self._agent_cli_version_var.set(
                    self._localize_agent_cli_version_text(version_text)
                )
                applied_current_result = True

        if (
            not applied_current_result
            and self._agent_cli_version_inflight.get(self._agent_cli_version_current_key)
            == self._agent_cli_version_request_id
        ):
            self._schedule_agent_cli_version_poll()

    def _localize_agent_cli_version_text(self, version_text: str | None) -> str:
        return localize_runtime_message(version_text, self._language)

    def _on_submit(self) -> None:
        try:
            output_font_size = int(self._font_size_var.get().strip())
        except ValueError:
            messagebox.showerror(
                ui_text("dialog_settings_error", self._language),
                ui_text("dialog_font_size_integer", self._language),
                parent=self,
            )
            return

        if not OUTPUT_FONT_SIZE_MIN <= output_font_size <= OUTPUT_FONT_SIZE_MAX:
            messagebox.showerror(
                ui_text("dialog_settings_error", self._language),
                ui_text(
                    "dialog_font_size_range",
                    self._language,
                    min_value=OUTPUT_FONT_SIZE_MIN,
                    max_value=OUTPUT_FONT_SIZE_MAX,
                ),
                parent=self,
            )
            return

        execution_timeout_minutes = self._parse_bounded_non_negative_int(
            self._execution_timeout_var,
            label_key="settings_execution_timeout",
            max_value=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
        )
        if execution_timeout_minutes is None:
            return

        inactivity_timeout_minutes = self._parse_bounded_non_negative_int(
            self._inactivity_timeout_var,
            label_key="settings_inactivity_timeout",
            max_value=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
        )
        if inactivity_timeout_minutes is None:
            return

        termination_grace_seconds = self._parse_bounded_non_negative_int(
            self._termination_grace_var,
            label_key="settings_termination_grace",
            max_value=TERMINATION_GRACE_SECONDS_MAX,
        )
        if termination_grace_seconds is None:
            return

        agent_provider = self._current_agent_provider()
        self._remember_executable_path(agent_provider)
        executable_path = self._executable_paths.get(agent_provider)
        default_model = self._selected_value(self._model_options, self._model_var.get())
        default_reasoning_effort = self._selected_value(
            self._reasoning_options,
            self._reasoning_var.get(),
        )
        self.result = AppSettings(
            executable_path=executable_path,
            executable_paths=self._executable_paths,
            output_font_size=output_font_size,
            execution_timeout_minutes=execution_timeout_minutes,
            inactivity_timeout_minutes=inactivity_timeout_minutes,
            termination_grace_seconds=termination_grace_seconds,
            file_logging_enabled=self._file_logging_var.get(),
            ui_language=self._selected_value(
                self._ui_language_options,
                self._ui_language_var.get(),
            ),
            agent_provider=agent_provider,
            default_model=default_model,
            default_reasoning_effort=default_reasoning_effort,
        )
        self._close_dialog()

    def _on_cancel(self) -> None:
        self.result = None
        self._close_dialog()

    def _open_author_link(self) -> None:
        _open_project_author_link("settings")

    def _open_license_notices_dialog(self) -> None:
        _open_license_notices_dialog(
            self,
            language=self._language,
            license_notices_loader=self._license_notices_loader,
        )

    def _close_dialog(self) -> None:
        self._closed = True
        if self._agent_cli_version_after_id is not None:
            after_id = self._agent_cli_version_after_id
            self._agent_cli_version_after_id = None
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                LOGGER.debug("Failed to cancel agent CLI version poll.", exc_info=True)
        self.destroy()

    def _parse_bounded_non_negative_int(
        self,
        variable: tk.StringVar,
        *,
        label_key: str,
        max_value: int,
    ) -> int | None:
        label = ui_text(label_key, self._language)
        try:
            value = int(variable.get().strip())
        except ValueError:
            messagebox.showerror(
                ui_text("dialog_settings_error", self._language),
                ui_text(
                    "dialog_non_negative_integer",
                    self._language,
                    field=label,
                ),
                parent=self,
            )
            return None

        if value < 0 or value > max_value:
            messagebox.showerror(
                ui_text("dialog_settings_error", self._language),
                ui_text(
                    "dialog_integer_range",
                    self._language,
                    field=label,
                    min_value=0,
                    max_value=max_value,
                ),
                parent=self,
            )
            return None

        return value

    def _current_agent_provider(self) -> str:
        selected_provider = self._selected_value(
            self._agent_provider_options,
            self._agent_provider_var.get(),
        )
        self._agent_provider = normalize_agent_provider(selected_provider)
        return self._agent_provider

    def _build_model_options(self, current_value: str | None) -> tuple[SelectOption, ...]:
        return build_model_select_options(
            current_value,
            agent_provider=self._agent_provider,
            auto_label=ui_text("settings_auto", self._language),
            saved_value_suffix=ui_text("settings_saved_value_suffix", self._language),
        )

    def _build_reasoning_options(
        self,
        current_value: str | None,
        *,
        model: str | None,
    ) -> tuple[SelectOption, ...]:
        return build_reasoning_select_options(
            current_value,
            agent_provider=self._agent_provider,
            model=model,
            auto_label=ui_text("settings_auto", self._language),
            saved_value_suffix=ui_text("settings_saved_value_suffix", self._language),
        )

    def _set_default_ai_selection(
        self,
        *,
        model_value: str,
        reasoning_value: str,
    ) -> None:
        self._model_options = self._build_model_options(model_value)
        resolved_model = self._option_value_or_default(
            self._model_options,
            model_value,
        )
        self._reasoning_options = self._build_reasoning_options(
            reasoning_value,
            model=resolved_model,
        )
        resolved_reasoning = self._option_value_or_default(
            self._reasoning_options,
            reasoning_value,
        )
        self._model_combobox.configure(
            values=[option.label for option in self._model_options],
        )
        self._reasoning_combobox.configure(
            values=[option.label for option in self._reasoning_options],
        )
        self._model_var.set(find_option_label(self._model_options, resolved_model))
        self._reasoning_var.set(
            find_option_label(self._reasoning_options, resolved_reasoning)
        )

    @staticmethod
    def _selected_value(options: tuple[SelectOption, ...], selected_label: str) -> str:
        normalized_label = selected_label.strip()
        for option in options:
            if option.label == normalized_label:
                return option.value
        return normalized_label

    @staticmethod
    def _option_value_or_default(
        options: tuple[SelectOption, ...],
        current_value: str | None,
    ) -> str:
        normalized_value = (current_value or "").strip()
        if normalized_value in {option.value for option in options}:
            return normalized_value
        if options:
            return options[0].value
        return ""


