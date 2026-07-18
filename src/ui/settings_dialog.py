"""Settings dialog split from ui.dialogs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

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
    QUEUE_MODE_PER_WORKSPACE,
    QUEUE_MODE_SHARED,
    normalize_agent_executable_paths,
    normalize_agent_provider,
    normalize_queue_mode,
    normalize_ui_language,
)
from domain.models import (
    EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
    TERMINATION_GRACE_SECONDS_MAX,
)
from infra.license_notices import load_license_notices

from .dialogs import (
    AGENT_CLI_VERSION_POLL_INTERVAL_MS,
    OUTPUT_FONT_SIZE_MAX,
    OUTPUT_FONT_SIZE_MIN,
    SETTINGS_AUTHOR_URL,
    SETTINGS_COMBOBOX_WIDTH,
    SETTINGS_EXECUTABLE_ENTRY_WIDTH,
    SETTINGS_GENERAL_COMBOBOX_WIDTH,
    SETTINGS_NUMBER_ENTRY_WIDTH,
    AgentCliVersionLoader,
    LicenseNoticesLoader,
    VersionProbeKey,
    _open_license_notices_dialog,
    _open_project_author_link,
)
from .dpi import get_widget_ui_scale
from .i18n import (
    language_label,
    language_options,
    localize_runtime_message,
    text as ui_text,
)
from .theme import apply_dark_theme
from .windowing import present_centered_modal
from .settings_dialog_ai import SettingsDialogAiMixin

LOGGER = logging.getLogger("ui.dialogs")

class SettingsDialog(SettingsDialogAiMixin, tk.Toplevel):
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
        queue_mode_editable: bool = True,
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
        self._queue_mode_editable = queue_mode_editable
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
        self._save_button: ttk.Button | None = None
        self._cancel_button: ttk.Button | None = None

        apply_dark_theme(self, scale=self._ui_scale)
        self._agent_provider_options = build_agent_provider_select_options(
            current_settings.agent_provider
        )
        self._queue_mode_options = self._build_queue_mode_options()
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
        self._queue_mode_var = tk.StringVar(
            value=find_option_label(
                self._queue_mode_options,
                normalize_queue_mode(current_settings.queue_mode),
            )
        )
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
        self._save_button = ttk.Button(
            button_row,
            text=ui_text("button_save", self._language),
            command=self._on_submit,
        )
        self._save_button.grid(
            row=0,
            column=0,
            padx=self._ui_scale.padding(0, 8),
        )
        self._cancel_button = ttk.Button(
            button_row,
            text=ui_text("button_cancel", self._language),
            command=self._on_cancel,
        )
        self._cancel_button.grid(
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

        ttk.Label(section, text=ui_text("settings_queue_mode", self._language)).grid(
            row=1,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
            pady=self._ui_scale.padding(0, 8),
        )
        self._queue_mode_combobox = ttk.Combobox(
            section,
            textvariable=self._queue_mode_var,
            values=[option.label for option in self._queue_mode_options],
            width=SETTINGS_GENERAL_COMBOBOX_WIDTH,
            state="readonly" if self._queue_mode_editable else "disabled",
        )
        self._queue_mode_combobox.grid(
            row=1,
            column=1,
            sticky="ew",
            pady=self._ui_scale.padding(0, 8),
        )

        ttk.Label(section, text=ui_text("settings_font_size", self._language)).grid(
            row=2,
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
            row=2,
            column=1,
            sticky="w",
            pady=self._ui_scale.padding(0, 8),
        )

        ttk.Checkbutton(
            section,
            text=ui_text("settings_file_logging", self._language),
            variable=self._file_logging_var,
        ).grid(row=3, column=1, sticky="w")
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
            queue_mode=self._selected_value(
                self._queue_mode_options,
                self._queue_mode_var.get(),
            ),
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







