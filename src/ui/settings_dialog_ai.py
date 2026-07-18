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

LOGGER = logging.getLogger("ui.dialogs")

class SettingsDialogAiMixin:
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

    def _build_queue_mode_options(self) -> tuple[SelectOption, ...]:
        return (
            SelectOption(
                ui_text("settings_queue_mode_per_workspace", self._language),
                QUEUE_MODE_PER_WORKSPACE,
            ),
            SelectOption(
                ui_text("settings_queue_mode_shared", self._language),
                QUEUE_MODE_SHARED,
            ),
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

