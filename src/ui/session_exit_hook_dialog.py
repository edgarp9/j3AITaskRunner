"""Session exit hook settings dialog."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

from domain import SessionExitHookConfig, normalize_ui_language

from .dpi import get_widget_ui_scale
from .i18n import text as ui_text
from .text_context_menu import bind_editable_text_context_menu
from .theme import apply_dark_theme, configure_text_widget
from .windowing import present_centered_modal

SESSION_EXIT_HOOK_ENTRY_WIDTH = 42
SESSION_EXIT_HOOK_ARGUMENT_HEIGHT = 7


def arguments_from_text(raw_text: str) -> tuple[str, ...]:
    """Return one argv argument per non-empty input line."""
    return tuple(line.strip() for line in raw_text.splitlines() if line.strip())


class SessionExitHookDialog(tk.Toplevel):
    """Modal dialog that edits one session's exit hook settings."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        config: SessionExitHookConfig,
        ui_language: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.withdraw()
        self._ui_scale = get_widget_ui_scale(parent)
        self._language = normalize_ui_language(
            ui_language or getattr(parent, "_ui_language", None)
        )
        self.result: SessionExitHookConfig | None = None

        self._enabled_var = tk.BooleanVar(value=config.enabled)
        self._executable_var = tk.StringVar(value=config.executable_path)

        self.title(ui_text("dialog_session_exit_hook_title", self._language))
        self.resizable(False, False)
        self.transient(parent)
        apply_dark_theme(self, scale=self._ui_scale)

        self._build_widgets(config)
        self._bind_shortcuts()
        present_centered_modal(parent, self)

    def show_modal(self) -> SessionExitHookConfig | None:
        """Block until the dialog closes and return submitted hook settings."""
        self.wait_window(self)
        return self.result

    def _build_widgets(self, config: SessionExitHookConfig) -> None:
        container = ttk.Frame(self, padding=self._ui_scale.padding(16))
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            container,
            text=ui_text("session_exit_hook_enabled", self._language),
            variable=self._enabled_var,
        ).grid(row=0, column=1, sticky="w", pady=self._ui_scale.padding(0, 10))

        ttk.Label(
            container,
            text=ui_text("session_exit_hook_executable", self._language),
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=self._ui_scale.padding(0, 8),
            pady=self._ui_scale.padding(0, 8),
        )
        executable_entry = ttk.Entry(
            container,
            textvariable=self._executable_var,
            width=SESSION_EXIT_HOOK_ENTRY_WIDTH,
        )
        executable_entry.grid(
            row=1,
            column=1,
            sticky="ew",
            pady=self._ui_scale.padding(0, 8),
        )
        ttk.Button(
            container,
            text=ui_text("button_browse", self._language),
            command=self._browse_executable,
        ).grid(
            row=1,
            column=2,
            sticky="w",
            padx=self._ui_scale.padding(8, 0),
            pady=self._ui_scale.padding(0, 8),
        )

        ttk.Label(
            container,
            text=ui_text("session_exit_hook_arguments", self._language),
        ).grid(
            row=2,
            column=0,
            sticky="nw",
            padx=self._ui_scale.padding(0, 8),
        )
        self._arguments_text = scrolledtext.ScrolledText(
            container,
            height=SESSION_EXIT_HOOK_ARGUMENT_HEIGHT,
            width=SESSION_EXIT_HOOK_ENTRY_WIDTH,
            wrap="word",
        )
        configure_text_widget(self._arguments_text, scale=self._ui_scale)
        bind_editable_text_context_menu(
            self._arguments_text,
            menu_parent=self,
            language=lambda: self._language,
        )
        if config.arguments:
            self._arguments_text.insert("1.0", "\n".join(config.arguments))
        self._arguments_text.grid(row=2, column=1, columnspan=2, sticky="ew")

        button_row = ttk.Frame(container)
        button_row.grid(
            row=3,
            column=0,
            columnspan=3,
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

    def _bind_shortcuts(self) -> None:
        self.bind("<Escape>", lambda _event: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _browse_executable(self) -> None:
        selected_path = filedialog.askopenfilename(
            parent=self,
            title=ui_text("dialog_session_exit_hook_executable_select", self._language),
        )
        if selected_path:
            self._executable_var.set(selected_path)

    def _on_submit(self) -> None:
        self.result = SessionExitHookConfig(
            enabled=self._enabled_var.get(),
            executable_path=self._executable_var.get(),
            arguments=arguments_from_text(
                self._arguments_text.get("1.0", tk.END),
            ),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()
