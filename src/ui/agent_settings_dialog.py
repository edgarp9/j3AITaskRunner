"""Modal AI execution option dialog for session-local settings."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.agent_cli_options import (
    SelectOption,
    build_model_select_options,
    build_reasoning_select_options,
    find_option_label,
)
from domain import AgentExecutionOptions, normalize_ui_language

from .dpi import get_widget_ui_scale
from .i18n import text as ui_text
from .theme import apply_dark_theme
from .windowing import present_centered_modal

AGENT_SETTINGS_COMBOBOX_WIDTH = 24


class AgentSettingsDialog(tk.Toplevel):
    """Modal dialog that edits provider/model/reasoning options."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        execution_options: AgentExecutionOptions,
        provider_options: tuple[SelectOption, ...],
        ui_language: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.withdraw()
        self._ui_scale = get_widget_ui_scale(parent)
        self._language = normalize_ui_language(
            ui_language or getattr(parent, "_ui_language", None)
        )
        self._provider_options = provider_options
        self._model_options: tuple[SelectOption, ...] = ()
        self._reasoning_options: tuple[SelectOption, ...] = ()
        self.result: AgentExecutionOptions | None = None

        provider_value = self._option_value_or_default(
            self._provider_options,
            execution_options.agent_provider,
        )
        self._provider_var = tk.StringVar(
            value=find_option_label(self._provider_options, provider_value)
        )
        self._model_var = tk.StringVar()
        self._reasoning_var = tk.StringVar()

        self.title(ui_text("dialog_ai_settings_title", self._language))
        self.resizable(False, False)
        self.transient(parent)
        apply_dark_theme(self, scale=self._ui_scale)

        self._rebuild_model_options(execution_options.model)
        model_value = self._selected_model_value()
        self._rebuild_reasoning_options(
            execution_options.reasoning_effort,
            model=model_value,
        )
        self._build_widgets()
        self._bind_shortcuts()
        present_centered_modal(parent, self)

    def show_modal(self) -> AgentExecutionOptions | None:
        """Block until the dialog closes and return submitted options."""
        self.wait_window(self)
        return self.result

    def _build_widgets(self) -> None:
        container = ttk.Frame(self, padding=self._ui_scale.padding(16))
        container.grid(sticky="nsew")

        self._agent_provider_combobox = self._add_combobox(
            container,
            row=0,
            label_key="session_agent_provider",
            variable=self._provider_var,
            values=[option.label for option in self._provider_options],
        )
        self._agent_provider_combobox.configure(
            state="readonly" if self._provider_options else "disabled"
        )
        self._agent_provider_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._on_provider_changed(),
        )

        self._model_combobox = self._add_combobox(
            container,
            row=1,
            label_key="session_model",
            variable=self._model_var,
            values=[option.label for option in self._model_options],
        )
        self._model_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._on_model_changed(),
        )

        self._reasoning_combobox = self._add_combobox(
            container,
            row=2,
            label_key="session_reasoning",
            variable=self._reasoning_var,
            values=[option.label for option in self._reasoning_options],
        )

        button_row = ttk.Frame(container)
        button_row.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="e",
            pady=self._ui_scale.padding(16, 0),
        )
        ttk.Button(
            button_row,
            text=ui_text("button_save", self._language),
            command=self._on_submit,
        ).grid(row=0, column=0, padx=self._ui_scale.padding(0, 8))
        ttk.Button(
            button_row,
            text=ui_text("button_cancel", self._language),
            command=self._on_cancel,
        ).grid(row=0, column=1)

    def _add_combobox(
        self,
        parent: ttk.Frame,
        *,
        row: int,
        label_key: str,
        variable: tk.StringVar,
        values: list[str],
    ) -> ttk.Combobox:
        ttk.Label(parent, text=ui_text(label_key, self._language)).grid(
            row=row,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 10),
            pady=self._ui_scale.padding(0 if row == 0 else 10, 0),
        )
        combobox = ttk.Combobox(
            parent,
            textvariable=variable,
            values=values,
            state="readonly" if values else "disabled",
            width=AGENT_SETTINGS_COMBOBOX_WIDTH,
        )
        combobox.grid(
            row=row,
            column=1,
            sticky="w",
            pady=self._ui_scale.padding(0 if row == 0 else 10, 0),
        )
        return combobox

    def _bind_shortcuts(self) -> None:
        self.bind("<Return>", lambda _event: self._on_submit())
        self.bind("<Escape>", lambda _event: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_provider_changed(self) -> None:
        self._rebuild_model_options("")
        self._rebuild_reasoning_options("", model=self._selected_model_value())
        self._model_combobox.configure(
            values=[option.label for option in self._model_options],
            state="readonly" if self._model_options else "disabled",
        )
        self._reasoning_combobox.configure(
            values=[option.label for option in self._reasoning_options],
            state="readonly" if self._reasoning_options else "disabled",
        )

    def _on_model_changed(self) -> None:
        self._rebuild_reasoning_options("", model=self._selected_model_value())
        self._reasoning_combobox.configure(
            values=[option.label for option in self._reasoning_options],
            state="readonly" if self._reasoning_options else "disabled",
        )

    def _on_submit(self) -> None:
        provider_value = self._selected_value(
            self._provider_options,
            self._provider_var.get(),
        )
        if not provider_value:
            return
        self.result = AgentExecutionOptions(
            agent_provider=provider_value,
            model=self._selected_value(self._model_options, self._model_var.get()),
            reasoning_effort=self._selected_value(
                self._reasoning_options,
                self._reasoning_var.get(),
            ),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def _rebuild_model_options(self, current_value: str) -> None:
        provider_value = self._selected_value(
            self._provider_options,
            self._provider_var.get(),
        )
        self._model_options = build_model_select_options(
            current_value,
            agent_provider=provider_value,
            auto_label=ui_text("settings_auto", self._language),
            saved_value_suffix=ui_text("settings_saved_value_suffix", self._language),
        )
        model_value = self._option_value_or_default(self._model_options, current_value)
        self._model_var.set(find_option_label(self._model_options, model_value))

    def _rebuild_reasoning_options(self, current_value: str, *, model: str) -> None:
        provider_value = self._selected_value(
            self._provider_options,
            self._provider_var.get(),
        )
        self._reasoning_options = build_reasoning_select_options(
            current_value,
            agent_provider=provider_value,
            model=model,
            auto_label=ui_text("settings_auto", self._language),
            saved_value_suffix=ui_text("settings_saved_value_suffix", self._language),
        )
        reasoning_value = self._option_value_or_default(
            self._reasoning_options,
            current_value,
        )
        self._reasoning_var.set(
            find_option_label(self._reasoning_options, reasoning_value)
        )

    def _selected_model_value(self) -> str:
        return self._selected_value(self._model_options, self._model_var.get())

    @staticmethod
    def _selected_value(options: tuple[SelectOption, ...], label: str) -> str:
        for option in options:
            if option.label == label:
                return option.value
        normalized_label = label.strip()
        if normalized_label in {option.value for option in options}:
            return normalized_label
        return ""

    @staticmethod
    def _option_value_or_default(
        options: tuple[SelectOption, ...],
        value: str,
    ) -> str:
        if value in {option.value for option in options}:
            return value
        return options[0].value if options else ""
