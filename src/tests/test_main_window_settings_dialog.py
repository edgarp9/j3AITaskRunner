from __future__ import annotations

from tests._main_window_helpers import *

from app.agent_cli_options import build_agent_provider_select_options


class SettingsDialogExecutionControlTests(unittest.TestCase):
    def test_execution_control_labels_are_localized(self) -> None:
        self.assertEqual("상태", ui_text("settings_status_section", "ko"))
        self.assertEqual("기본 설정", ui_text("settings_general_section", "ko"))
        self.assertEqual(
            "실행 제한",
            ui_text("settings_execution_limits_section", "ko"),
        )
        self.assertEqual(
            "전체 실행 제한(분)",
            ui_text("settings_execution_timeout", "ko"),
        )
        self.assertEqual(
            "무활동 제한(분)",
            ui_text("settings_inactivity_timeout", "ko"),
        )
        self.assertEqual(
            "종료 유예(초)",
            ui_text("settings_termination_grace", "ko"),
        )
        self.assertEqual(
            "Execution Timeout (min)",
            ui_text("settings_execution_timeout", "en"),
        )
        self.assertEqual(
            "Inactivity Timeout (min)",
            ui_text("settings_inactivity_timeout", "en"),
        )
        self.assertEqual(
            "Termination Grace (sec)",
            ui_text("settings_termination_grace", "en"),
        )
        self.assertEqual("Status", ui_text("settings_status_section", "en"))
        self.assertEqual("General", ui_text("settings_general_section", "en"))
        self.assertEqual(
            "Execution Limits",
            ui_text("settings_execution_limits_section", "en"),
        )

    def test_settings_dialog_saves_execution_control_values(self) -> None:
        root: tk.Tk | None = None
        dialog: SettingsDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            current_settings = AppSettings(
                execution_timeout_minutes=120,
                inactivity_timeout_minutes=30,
                termination_grace_seconds=5,
                ui_language="ko",
            )
            dialog = SettingsDialog(
                root,
                current_settings,
                app_name=APP_NAME,
                app_version=APP_VERSION,
                agent_cli_version_loader=lambda _path, _provider: "agent-cli test",
            )
            dialog.withdraw()
            root.update_idletasks()

            labels = {_widget_text(widget) for widget in _walk_widgets(dialog)}
            self.assertIn("전체 실행 제한(분)", labels)
            self.assertIn("무활동 제한(분)", labels)
            self.assertIn("종료 유예(초)", labels)
            self.assertFalse(dialog._file_logging_var.get())

            dialog._execution_timeout_var.set("0")
            dialog._inactivity_timeout_var.set("45")
            dialog._termination_grace_var.set("9")
            dialog._on_submit()

            self.assertEqual(
                AppSettings(
                    output_font_size=12,
                    execution_timeout_minutes=0,
                    inactivity_timeout_minutes=45,
                    termination_grace_seconds=9,
                    file_logging_enabled=False,
                    ui_language="ko",
                ),
                dialog.result,
            )
            dialog = None
        finally:
            _destroy_dialog_and_root(dialog, root)

    def test_settings_dialog_saves_queue_mode_and_can_disable_it(self) -> None:
        root: tk.Tk | None = None
        dialog: SettingsDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = SettingsDialog(
                root,
                AppSettings(ui_language="ko"),
                app_name=APP_NAME,
                app_version=APP_VERSION,
                agent_cli_version_loader=lambda _path, _provider: "agent-cli test",
                queue_mode_editable=False,
            )
            dialog.withdraw()
            root.update_idletasks()

            labels = {_widget_text(widget) for widget in _walk_widgets(dialog)}
            self.assertIn("작업큐 방식", labels)
            self.assertEqual("disabled", str(dialog._queue_mode_combobox.cget("state")))

            dialog._queue_mode_var.set("공유큐")
            dialog._on_submit()

            self.assertIsNotNone(dialog.result)
            self.assertEqual("shared", dialog.result.queue_mode)
            dialog = None
        finally:
            _destroy_dialog_and_root(dialog, root)

    def test_settings_dialog_localizes_agent_cli_version_issue(self) -> None:
        dialog = object.__new__(SettingsDialog)
        dialog._language = "en"

        self.assertEqual(
            "Check the executable path.",
            dialog._localize_agent_cli_version_text("실행기 경로를 확인하세요."),
        )
        self.assertEqual(
            "Executable path is not set.",
            dialog._localize_agent_cli_version_text("실행기 경로 없음"),
        )
        self.assertEqual(
            "agent-cli 1.2.3",
            dialog._localize_agent_cli_version_text("agent-cli 1.2.3"),
        )

    def test_settings_dialog_shows_author_link_and_opens_browser(self) -> None:
        root: tk.Tk | None = None
        dialog: SettingsDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = SettingsDialog(
                root,
                AppSettings(ui_language="ko"),
                app_name=APP_NAME,
                app_version=APP_VERSION,
                agent_cli_version_loader=lambda _path, _provider: "agent-cli test",
            )
            dialog.withdraw()
            root.update_idletasks()

            link_widgets = _find_widgets_by_text(dialog, SETTINGS_AUTHOR_URL)
            self.assertEqual(1, len(link_widgets))
            self.assertEqual("hand2", str(link_widgets[0].cget("cursor")))

            with patch("ui.dialogs.webbrowser.open_new_tab", return_value=True) as open_link:
                dialog._open_author_link()

            open_link.assert_called_once_with(SETTINGS_AUTHOR_URL)
            dialog = None
        finally:
            _destroy_dialog_and_root(dialog, root)

    def test_settings_dialog_shows_licenses_button_and_opens_notices(self) -> None:
        root: tk.Tk | None = None
        dialog: SettingsDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = SettingsDialog(
                root,
                AppSettings(ui_language="ko"),
                app_name=APP_NAME,
                app_version=APP_VERSION,
                agent_cli_version_loader=lambda _path, _provider: "agent-cli test",
                license_notices_loader=lambda: "license notice text",
            )
            dialog.withdraw()
            root.update_idletasks()

            licenses_buttons = _find_widgets_by_text(dialog, "Licenses")
            self.assertEqual(1, len(licenses_buttons))

            with patch("ui.dialogs.LicenseNoticesDialog") as dialog_cls:
                licenses_buttons[0].invoke()

            dialog_cls.assert_called_once_with(
                dialog,
                notices="license notice text",
                ui_language="ko",
            )
            dialog_cls.return_value.show_modal.assert_called_once_with()
            dialog = None
        finally:
            _destroy_dialog_and_root(dialog, root)

    def test_settings_dialog_saves_default_ai_options(self) -> None:
        root: tk.Tk | None = None
        dialog: SettingsDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = SettingsDialog(
                root,
                AppSettings(ui_language="ko"),
                app_name=APP_NAME,
                app_version=APP_VERSION,
                agent_cli_version_loader=lambda _path, _provider: "agent-cli test",
            )
            dialog.withdraw()
            root.update_idletasks()

            labels = {_widget_text(widget) for widget in _walk_widgets(dialog)}
            self.assertIn("상태", labels)
            self.assertIn("기본 설정", labels)
            self.assertIn("실행 제한", labels)
            self.assertIn("워크스페이스 기본 AI 설정", labels)
            self.assertIn("AI 실행기", labels)
            self.assertIn("model", labels)
            self.assertIn("추론레벨", labels)

            general_section = _find_widgets_by_text(dialog, "기본 설정")[0]
            execution_section = _find_widgets_by_text(dialog, "실행 제한")[0]
            workspace_ai_section = _find_widgets_by_text(
                dialog,
                "워크스페이스 기본 AI 설정",
            )[0]
            self.assertIsInstance(general_section, ttk.LabelFrame)
            self.assertIsInstance(execution_section, ttk.LabelFrame)
            self.assertIsInstance(workspace_ai_section, ttk.LabelFrame)
            self.assertEqual(0, int(general_section.grid_info()["column"]))
            self.assertEqual(0, int(execution_section.grid_info()["column"]))
            self.assertEqual(1, int(execution_section.grid_info()["row"]))
            self.assertEqual(1, int(workspace_ai_section.grid_info()["column"]))
            self.assertEqual(2, int(workspace_ai_section.grid_info()["rowspan"]))
            content_frame = general_section.master
            self.assertEqual(0, int(content_frame.grid_columnconfigure(0)["weight"]))
            self.assertEqual(1, int(content_frame.grid_columnconfigure(1)["weight"]))
            self.assertIsNone(content_frame.grid_columnconfigure(0)["uniform"])
            self.assertIsNone(content_frame.grid_columnconfigure(1)["uniform"])
            self.assertEqual(
                ("Codex CLI", "Claude Code", "Kilo Code", "OpenCode", "Pi Coding Agent"),
                tuple(dialog._agent_provider_combobox.cget("values")),
            )

            dialog._model_var.set("gpt-5.4")
            dialog._on_model_changed()
            dialog._reasoning_var.set("high")

            dialog._on_submit()

            self.assertIsNotNone(dialog.result)
            self.assertEqual("codex", dialog.result.agent_provider)
            self.assertEqual("gpt-5.4", dialog.result.default_model)
            self.assertEqual("high", dialog.result.default_reasoning_effort)
            dialog = None
        finally:
            _destroy_dialog_and_root(dialog, root)

    def test_settings_dialog_preserves_executable_path_per_provider(self) -> None:
        root: tk.Tk | None = None
        dialog: SettingsDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = SettingsDialog(
                root,
                AppSettings(
                    agent_provider="codex",
                    executable_path=r"C:\Tools\codex.exe",
                    executable_paths={
                        "opencode": r"C:\Tools\opencode.exe",
                    },
                    ui_language="ko",
                ),
                app_name=APP_NAME,
                app_version=APP_VERSION,
                agent_cli_version_loader=lambda _path, _provider: "agent-cli test",
            )
            dialog.withdraw()
            root.update_idletasks()

            self.assertEqual(r"C:\Tools\codex.exe", dialog._executable_var.get())

            dialog._executable_var.set(r"C:\Tools\codex-new.exe")
            dialog._agent_provider_var.set("OpenCode")
            dialog._on_agent_provider_changed()
            self.assertEqual(r"C:\Tools\opencode.exe", dialog._executable_var.get())

            dialog._executable_var.set(r"C:\Tools\opencode-new.exe")
            dialog._agent_provider_var.set("Codex CLI")
            dialog._on_agent_provider_changed()
            self.assertEqual(r"C:\Tools\codex-new.exe", dialog._executable_var.get())

            dialog._on_submit()

            self.assertIsNotNone(dialog.result)
            self.assertEqual("codex", dialog.result.agent_provider)
            self.assertEqual(r"C:\Tools\codex-new.exe", dialog.result.executable_path)
            self.assertEqual(
                r"C:\Tools\codex-new.exe",
                dialog.result.executable_paths["codex"],
            )
            self.assertEqual(
                r"C:\Tools\opencode-new.exe",
                dialog.result.executable_paths["opencode"],
            )
            dialog = None
        finally:
            _destroy_dialog_and_root(dialog, root)

    def test_settings_dialog_rejects_invalid_execution_control_values(self) -> None:
        invalid_cases = (
            (
                "_execution_timeout_var",
                "",
                "전체 실행 제한(분) 값은 0 이상의 정수로 입력하세요.",
            ),
            (
                "_execution_timeout_var",
                "abc",
                "전체 실행 제한(분) 값은 0 이상의 정수로 입력하세요.",
            ),
            (
                "_execution_timeout_var",
                "-1",
                "전체 실행 제한(분) 값은 0-525600 사이의 정수로 입력하세요.",
            ),
            (
                "_inactivity_timeout_var",
                "",
                "무활동 제한(분) 값은 0 이상의 정수로 입력하세요.",
            ),
            (
                "_inactivity_timeout_var",
                "abc",
                "무활동 제한(분) 값은 0 이상의 정수로 입력하세요.",
            ),
            (
                "_inactivity_timeout_var",
                "-1",
                "무활동 제한(분) 값은 0-525600 사이의 정수로 입력하세요.",
            ),
            (
                "_termination_grace_var",
                "",
                "종료 유예(초) 값은 0 이상의 정수로 입력하세요.",
            ),
            (
                "_termination_grace_var",
                "abc",
                "종료 유예(초) 값은 0 이상의 정수로 입력하세요.",
            ),
            (
                "_termination_grace_var",
                "-1",
                "종료 유예(초) 값은 0-86400 사이의 정수로 입력하세요.",
            ),
        )

        for variable_name, invalid_value, expected_message in invalid_cases:
            with self.subTest(variable_name=variable_name, invalid_value=invalid_value):
                root: tk.Tk | None = None
                dialog: SettingsDialog | None = None
                try:
                    root = _create_tk_root_or_skip(self)
                    dialog = SettingsDialog(
                        root,
                        AppSettings(ui_language="ko"),
                        app_name=APP_NAME,
                        app_version=APP_VERSION,
                        agent_cli_version_loader=lambda _path, _provider: "agent-cli test",
                    )
                    dialog.withdraw()
                    root.update_idletasks()

                    getattr(dialog, variable_name).set(invalid_value)

                    with patch("ui.dialogs.messagebox.showerror") as showerror:
                        dialog._on_submit()

                    self.assertIsNone(dialog.result)
                    self.assertTrue(dialog.winfo_exists())
                    showerror.assert_called_once_with(
                        "설정 오류",
                        expected_message,
                        parent=dialog,
                    )
                finally:
                    _destroy_dialog_and_root(dialog, root)

class BulkPromptImportDialogTests(unittest.TestCase):
    def test_dialog_exposes_multiline_editor_and_bulk_register_button(self) -> None:
        root: tk.Tk | None = None
        dialog: BulkPromptImportDialog | None = None
        try:
            try:
                root = tk.Tk()
            except tk.TclError as error:
                if _is_tk_display_unavailable(error):
                    self.skipTest(f"Tk display is unavailable: {error}")
                raise

            root.withdraw()
            dialog = BulkPromptImportDialog(root, initial_auto_commit=False)
            dialog.withdraw()
            root.update_idletasks()

            text_widgets = [
                widget
                for widget in _walk_widgets(dialog)
                if isinstance(widget, scrolledtext.ScrolledText)
            ]
            self.assertEqual(1, len(text_widgets))
            register_buttons = _find_widgets_by_text(dialog, "Add")
            self.assertEqual(1, len(register_buttons))
            self.assertEqual(
                1,
                len(_find_widgets_by_text(dialog, "Step Execution Mode")),
            )
            mode_comboboxes = [
                widget
                for widget in _walk_widgets(dialog)
                if isinstance(widget, ttk.Combobox)
            ]
            self.assertEqual(1, len(mode_comboboxes))
            self.assertEqual("Single Session", mode_comboboxes[0].get())
            self.assertEqual(
                ("Single Session", "Session per Step"),
                tuple(mode_comboboxes[0].cget("values")),
            )
            self.assertEqual(
                BULK_IMPORT_EXAMPLE_TEXT,
                text_widgets[0].get("1.0", "end-1c"),
            )

            text_widgets[0].delete("1.0", tk.END)
            text_widgets[0].insert(tk.END, "```text\n/goal dialog\n```\n")
            mode_comboboxes[0].set("Session per Step")
            register_buttons[0].invoke()

            self.assertEqual(
                BulkPromptImportDialogResult(
                    raw_text="```text\n/goal dialog\n```\n",
                    auto_commit_enabled=False,
                    step_execution_mode=StepExecutionMode.PER_STEP_SESSION,
                ),
                dialog.result,
            )
        finally:
            if dialog is not None:
                try:
                    if dialog.winfo_exists():
                        dialog.destroy()
                except tk.TclError:
                    pass
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass

class AboutDialogTests(unittest.TestCase):
    def test_dialog_shows_about_text_without_licenses_action(self) -> None:
        root: tk.Tk | None = None
        dialog: AboutDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = AboutDialog(
                root,
                app_name=APP_NAME,
                app_version=APP_VERSION,
                about_notice_loader=lambda: (
                    "j3AITaskRunner\n\n"
                    "License: GPL-3.0-or-later\n"
                    "Corresponding Source: release source code\n"
                    "THIRD_PARTY_NOTICES.txt"
                ),
                ui_language="ko",
            )
            dialog.withdraw()
            root.update_idletasks()

            about_widgets = [
                widget
                for widget in _walk_widgets(dialog)
                if isinstance(widget, scrolledtext.ScrolledText)
            ]
            self.assertEqual(1, len(about_widgets))
            about_text = about_widgets[0].get("1.0", "end-1c")
            self.assertIn(APP_NAME, about_text)
            self.assertNotIn(APP_VERSION, about_text)
            self.assertIn("GPL-3.0-or-later", about_text)
            self.assertIn("release source code", about_text)
            self.assertIn("THIRD_PARTY_NOTICES.txt", about_text)

            licenses_buttons = _find_widgets_by_text(dialog, "Licenses")
            self.assertEqual([], licenses_buttons)
            dialog = None
        finally:
            _destroy_dialog_and_root(dialog, root)

    def test_dialog_shows_version_label_and_source_link(self) -> None:
        root: tk.Tk | None = None
        dialog: AboutDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = AboutDialog(
                root,
                app_name=APP_NAME,
                app_version=APP_VERSION,
                about_notice_loader=lambda: "about notice",
                ui_language="ko",
            )
            dialog.withdraw()
            root.update_idletasks()

            self.assertEqual(
                1,
                len(_find_widgets_by_text(dialog, f"{APP_NAME} {APP_VERSION}")),
            )
            self.assertEqual(1, len(_find_widgets_by_text(dialog, "소스 코드")))

            source_link_widgets = _find_widgets_by_text(dialog, ABOUT_SOURCE_URL)
            self.assertEqual(1, len(source_link_widgets))
            self.assertEqual("hand2", str(source_link_widgets[0].cget("cursor")))

            with patch("ui.dialogs.webbrowser.open_new_tab", return_value=True) as open_link:
                dialog._open_source_link()

            open_link.assert_called_once_with(ABOUT_SOURCE_URL)
            dialog = None
        finally:
            _destroy_dialog_and_root(dialog, root)

class LicenseNoticesDialogTests(unittest.TestCase):
    def test_dialog_shows_read_only_license_notices(self) -> None:
        root: tk.Tk | None = None
        dialog: LicenseNoticesDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = LicenseNoticesDialog(
                root,
                notices="# Licenses\n\nSample notice",
                ui_language="en",
            )
            dialog.withdraw()
            root.update_idletasks()

            text_widgets = [
                widget
                for widget in _walk_widgets(dialog)
                if isinstance(widget, scrolledtext.ScrolledText)
            ]
            self.assertEqual(1, len(text_widgets))
            self.assertEqual(
                "# Licenses\n\nSample notice",
                text_widgets[0].get("1.0", "end-1c"),
            )
            self.assertEqual("disabled", str(text_widgets[0].cget("state")))
            dialog = None
        finally:
            _destroy_dialog_and_root(dialog, root)

