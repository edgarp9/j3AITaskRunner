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

