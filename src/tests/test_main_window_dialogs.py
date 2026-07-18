from __future__ import annotations

from tests._main_window_helpers import *
from domain import SessionExitHookConfig
from ui.session_exit_hook_dialog import SessionExitHookDialog, arguments_from_text


class SessionExitHookDialogTests(unittest.TestCase):
    def test_arguments_from_text_uses_one_non_empty_argument_per_line(self) -> None:
        self.assertEqual(
            ("--flag", "value with spaces", "quoted value"),
            arguments_from_text("\n  --flag  \nvalue with spaces\n\nquoted value\n"),
        )

    def test_submit_returns_session_exit_hook_config(self) -> None:
        root: tk.Tk | None = None
        dialog: SessionExitHookDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = SessionExitHookDialog(
                root,
                config=SessionExitHookConfig(
                    enabled=False,
                    executable_path="",
                    arguments=(),
                ),
                ui_language="ko",
            )
            dialog.withdraw()
            root.update_idletasks()

            dialog._enabled_var.set(True)
            dialog._executable_var.set(r"C:\Tools\hook.exe")
            dialog._arguments_text.delete("1.0", tk.END)
            dialog._arguments_text.insert("1.0", "--flag\nvalue with spaces\n")
            dialog._on_submit()

            self.assertEqual(
                SessionExitHookConfig(
                    enabled=True,
                    executable_path=r"C:\Tools\hook.exe",
                    arguments=("--flag", "value with spaces"),
                ),
                dialog.result,
            )
        finally:
            _destroy_dialog_and_root(dialog, root)

from app.agent_cli_options import build_agent_provider_select_options

class MainWindowGeometryTests(unittest.TestCase):
    def test_default_client_size_constants(self) -> None:
        self.assertEqual((1100, 800), (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT))
        self.assertEqual(
            "1100x800", f"{DEFAULT_WINDOW_WIDTH}x{DEFAULT_WINDOW_HEIGHT}"
        )
        self.assertLessEqual(MIN_WINDOW_WIDTH, DEFAULT_WINDOW_WIDTH)
        self.assertLessEqual(MIN_WINDOW_HEIGHT, DEFAULT_WINDOW_HEIGHT)

    def test_main_window_applies_default_client_geometry(self) -> None:
        with TemporaryDirectory() as storage_dir:
            runtime = build_runtime(storage_root=Path(storage_dir))
            window: MainWindow | None = None
            try:
                try:
                    window = MainWindow(runtime)
                except tk.TclError as error:
                    if _is_tk_display_unavailable(error):
                        self.skipTest(f"Tk display is unavailable: {error}")
                    raise

                window.update_idletasks()
                if (
                    window.winfo_screenwidth() <= DEFAULT_WINDOW_WIDTH
                    or window.winfo_screenheight() <= DEFAULT_WINDOW_HEIGHT
                ):
                    self.skipTest(
                        "Screen cannot display the requested default client geometry."
                    )

                self.assertEqual(
                    "1100x800",
                    window.geometry().split("+", maxsplit=1)[0],
                )
                self.assertEqual(
                    (1100, 800),
                    (window.winfo_width(), window.winfo_height()),
                )
            finally:
                if window is not None:
                    _close_tk_window(window)
                else:
                    _shutdown_runtime(runtime)

    def test_sidebar_opens_at_requested_width(self) -> None:
        self.assertEqual(180, SIDEBAR_INITIAL_WIDTH)

    def test_sidebar_collapses_to_requested_width(self) -> None:
        self.assertEqual(0, SIDEBAR_COLLAPSED_WIDTH)

    def test_workspace_split_opens_at_screenshot_widths(self) -> None:
        self.assertEqual(560, WORKSPACE_SESSIONS_INITIAL_WIDTH)
        self.assertEqual(180, WORKSPACE_TASK_LIST_INITIAL_WIDTH)

class MainWindowSidebarCollapseTests(unittest.TestCase):
    def test_set_sidebar_collapsed_hides_content_and_remembers_width(self) -> None:
        window = _SidebarCollapseWindowStub(sash_position=236)

        MainWindow._set_sidebar_collapsed(window, True)

        self.assertTrue(window._sidebar_collapsed)
        self.assertEqual(236, window._sidebar_restore_width)
        self.assertEqual(1, window._sidebar_content.grid_remove_calls)
        self.assertEqual(0, window._sidebar_content.grid_calls)
        self.assertEqual(">", window._sidebar_toggle_button.text)
        self.assertEqual(SIDEBAR_COLLAPSED_WIDTH, window._sidebar.width)
        self.assertEqual(SIDEBAR_COLLAPSED_WIDTH, window._main_splitter.sash_position)
        self.assertFalse(window._sidebar_restore_button.is_gridded)

    def test_set_sidebar_expanded_restores_previous_width(self) -> None:
        window = _SidebarCollapseWindowStub(sash_position=SIDEBAR_COLLAPSED_WIDTH)
        window._sidebar_collapsed = True
        window._sidebar_restore_width = 236

        MainWindow._set_sidebar_collapsed(window, False)

        self.assertFalse(window._sidebar_collapsed)
        self.assertEqual(1, window._sidebar_content.grid_calls)
        self.assertEqual(0, window._sidebar_content.grid_remove_calls)
        self.assertEqual("<", window._sidebar_toggle_button.text)
        self.assertEqual(236, window._sidebar.width)
        self.assertEqual(236, window._main_splitter.sash_position)
        self.assertFalse(window._sidebar_restore_button.is_gridded)

    def test_statusbar_toggle_shows_expand_when_sash_is_hidden(self) -> None:
        window = _SidebarCollapseWindowStub(sash_position=0)

        MainWindow._refresh_sidebar_restore_button(window)

        self.assertEqual(">", window._sidebar_toggle_button.text)
        self.assertFalse(window._sidebar_restore_button.is_gridded)

    def test_statusbar_toggle_expands_when_sash_is_hidden(self) -> None:
        window = _SidebarCollapseWindowStub(sash_position=0)
        window._sidebar_restore_width = 236

        MainWindow._toggle_sidebar(window)

        self.assertFalse(window._sidebar_collapsed)
        self.assertEqual(236, window._sidebar.width)
        self.assertEqual(236, window._main_splitter.sash_position)
        self.assertEqual("<", window._sidebar_toggle_button.text)

    def test_rebuild_static_ui_keeps_sidebar_collapsed_state(self) -> None:
        window = _SidebarRebuildWindowStub()

        MainWindow._rebuild_static_ui(window)

        self.assertTrue(window._sidebar_collapsed)
        self.assertEqual(1, window.build_widgets_calls)
        self.assertEqual(1, window._sidebar_content.grid_remove_calls)
        self.assertEqual(">", window._sidebar_toggle_button.text)
        self.assertEqual(SIDEBAR_COLLAPSED_WIDTH, window._sidebar.width)
        self.assertFalse(window._sidebar_restore_button.is_gridded)

class OptionalLabelVisibilityTests(unittest.TestCase):
    def test_optional_label_hides_when_text_is_empty(self) -> None:
        label = _LabelVisibilityStub()
        value_var = _StringVarStub("old")

        _set_optional_label_text(label, value_var, "")

        self.assertEqual("", value_var.get())
        self.assertEqual(0, label.grid_calls)
        self.assertEqual(1, label.grid_remove_calls)

    def test_optional_label_shows_when_text_exists(self) -> None:
        label = _LabelVisibilityStub()
        value_var = _StringVarStub()

        _set_optional_label_text(label, value_var, "waiting")

        self.assertEqual("waiting", value_var.get())
        self.assertEqual(1, label.grid_calls)
        self.assertEqual(0, label.grid_remove_calls)

class StatusMessageLocalizationTests(unittest.TestCase):
    def test_status_message_localizes_runtime_message_for_current_language(self) -> None:
        window = _StatusLocalizationWindowStub(AppSettings(ui_language="en"))

        self.assertEqual(
            "Check the executable path.",
            _localize_status_message(window, "실행기 경로를 확인하세요."),
        )
        self.assertEqual(
            "Could not read preset languages.",
            _localize_status_message(window, "프리셋 언어 목록을 읽지 못했습니다."),
        )
        self.assertEqual(
            "Could not process the preset analysis result.",
            _localize_status_message(
                window,
                "프리셋 분석 결과를 처리하지 못했습니다.",
            ),
        )
        self.assertEqual(
            "Execution failed: Could not find the final response JSON event.",
            _localize_status_message(
                window,
                "실행 실패: 마지막 응답 JSON 이벤트를 확인하지 못했습니다.",
            ),
        )
        self.assertEqual(
            "Priority must be one of high, medium, low.",
            _localize_status_message(
                window,
                "우선순위는 high, medium, low 중 하나여야 합니다.",
            ),
        )

    def test_set_status_localizes_runtime_message_before_display(self) -> None:
        window = _StatusLocalizationWindowStub(AppSettings(ui_language="en"))

        MainWindow._set_status(window, "설정을 저장하지 못했습니다.")

        self.assertEqual(
            "Could not save settings.",
            window._status_message_var.get(),
        )

class ScheduledRunDialogParsingTests(unittest.TestCase):
    def test_parse_scheduled_run_datetime_accepts_future_split_values(self) -> None:
        now = datetime(2026, 6, 16, 19, 0)

        scheduled_at = parse_scheduled_run_datetime(
            "2026",
            "06",
            "16",
            "19",
            "30",
            now=now,
        )

        self.assertEqual(datetime(2026, 6, 16, 19, 30), scheduled_at)

    def test_parse_scheduled_run_datetime_rejects_invalid_date(self) -> None:
        with self.assertRaises(ScheduledRunValidationError) as context:
            parse_scheduled_run_datetime(
                "2026",
                "02",
                "30",
                "19",
                "30",
                now=datetime(2026, 1, 1),
            )

        self.assertEqual(
            "dialog_scheduled_run_invalid_datetime",
            context.exception.message_key,
        )

    def test_parse_scheduled_run_datetime_rejects_past_time(self) -> None:
        with self.assertRaises(ScheduledRunValidationError) as context:
            parse_scheduled_run_datetime(
                "2026",
                "03",
                "03",
                "19",
                "30",
                now=datetime(2026, 6, 16),
            )

        self.assertEqual(
            "dialog_scheduled_run_future_required",
            context.exception.message_key,
        )

    def test_default_scheduled_run_time_uses_future_minute(self) -> None:
        now = datetime(2026, 6, 16, 19, 0, 45)

        self.assertEqual(
            datetime(2026, 6, 16, 19, 5),
            default_scheduled_run_time(now),
        )

class SessionIdCopyTests(unittest.TestCase):
    def test_copy_session_id_puts_confirmed_id_on_clipboard(self) -> None:
        runtime = _SessionIdCopyRuntimeStub(session_id="thread-1")
        window = _SessionIdCopyWindowStub(runtime)

        MainWindow._copy_session_id(window, "session-1")

        self.assertEqual("thread-1", window.clipboard_text)
        self.assertEqual(["세션 ID 복사됨"], window.status_messages)

    def test_copy_session_id_ignores_pending_session_id(self) -> None:
        runtime = _SessionIdCopyRuntimeStub(session_id=None)
        window = _SessionIdCopyWindowStub(runtime)
        window.clipboard_text = "existing"

        MainWindow._copy_session_id(window, "session-1")

        self.assertEqual("existing", window.clipboard_text)
        self.assertEqual([], window.status_messages)


class AgentSettingsDialogTests(unittest.TestCase):
    def test_provider_change_rebuilds_model_and_reasoning_options(self) -> None:
        root: tk.Tk | None = None
        dialog: AgentSettingsDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = AgentSettingsDialog(
                root,
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
                provider_options=build_agent_provider_select_options("codex"),
                ui_language="ko",
            )
            dialog.withdraw()
            root.update_idletasks()

            dialog._provider_var.set("Pi Coding Agent")
            dialog._on_provider_changed()

            self.assertEqual(("자동",), tuple(dialog._model_combobox.cget("values")))
            self.assertEqual(
                ("자동", "off", "minimal", "low", "medium", "high", "xhigh"),
                tuple(dialog._reasoning_combobox.cget("values")),
            )
            self.assertEqual("자동", dialog._model_var.get())
            self.assertEqual("자동", dialog._reasoning_var.get())
        finally:
            _destroy_dialog_and_root(dialog, root)

    def test_model_change_resets_reasoning_to_auto(self) -> None:
        root: tk.Tk | None = None
        dialog: AgentSettingsDialog | None = None
        try:
            root = _create_tk_root_or_skip(self)
            dialog = AgentSettingsDialog(
                root,
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
                provider_options=build_agent_provider_select_options("codex"),
                ui_language="ko",
            )
            dialog.withdraw()
            root.update_idletasks()

            dialog._model_var.set("gpt-5.4-mini")
            dialog._on_model_changed()

            self.assertEqual("자동", dialog._reasoning_var.get())
            self.assertEqual(
                ("자동", "none", "minimal", "low", "medium", "high", "xhigh"),
                tuple(dialog._reasoning_combobox.cget("values")),
            )
        finally:
            _destroy_dialog_and_root(dialog, root)


class MainWindowSettingsDialogTests(unittest.TestCase):
    def test_settings_summary_shows_configured_agent_provider_only(self) -> None:
        settings = AppSettings(
            agent_provider="claude_code",
            executable_path=r"C:\Tools\Claude\claude.exe",
            output_font_size=13,
            ui_language="ko",
        )

        self.assertEqual(
            "Claude Code",
            _format_settings_summary(settings),
        )

    def test_settings_summary_shows_configured_agent_provider_list(self) -> None:
        settings = AppSettings(
            agent_provider="codex",
            executable_path="codex",
            executable_paths={"claude_code": "claude"},
            output_font_size=13,
            file_logging_enabled=False,
            ui_language="ko",
        )

        self.assertEqual(
            "Codex CLI / Claude Code",
            _format_settings_summary(settings),
        )

    def test_settings_summary_shows_empty_agent_provider_list(self) -> None:
        settings = AppSettings(
            executable_path=None,
            output_font_size=13,
            file_logging_enabled=False,
        )

        self.assertEqual(
            "No AI runners configured",
            _format_settings_summary(settings),
        )

    def test_open_settings_dialog_refreshes_ui_after_successful_save(self) -> None:
        existing_settings = AppSettings(agent_provider="codex", ui_language="ko")
        updated_settings = AppSettings(agent_provider="pi", ui_language="ko")
        runtime = _RuntimeStub(
            settings=existing_settings,
            update_result=SettingsUpdateResult(retried_job_ids=("job-1", "job-2")),
        )
        window = _MainWindowStub(runtime)

        with (
            patch("ui.main_window.SettingsDialog") as dialog_cls,
            patch("ui.main_window.messagebox.showwarning") as showwarning,
        ):
            dialog_cls.return_value.show_modal.return_value = updated_settings

            MainWindow._open_settings_dialog(window)

        dialog_cls.assert_called_once_with(
            window,
            existing_settings,
            app_name=APP_NAME,
            app_version=APP_VERSION,
            agent_cli_version_loader=load_agent_cli_version_text,
            queue_mode_editable=True,
        )
        self.assertEqual([updated_settings], runtime.updated_settings)
        self.assertEqual(0, window.drain_runtime_events_calls)
        self.assertEqual(1, window.refresh_settings_summary_calls)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(0, window.apply_output_font_to_all_sessions_calls)
        self.assertEqual(1, window.refresh_all_session_execution_option_controls_calls)
        self.assertEqual(0, window.refresh_session_outputs_for_all_sessions_calls)
        self.assertEqual(0, window.rebuild_static_ui_calls)
        self.assertEqual(
            ["설정을 저장했습니다."],
            window.status_messages,
        )
        showwarning.assert_not_called()

    def test_open_session_exit_hook_dialog_saves_runtime_config(self) -> None:
        initial_config = SessionExitHookConfig()
        updated_config = SessionExitHookConfig(
            enabled=True,
            executable_path=r"C:\Tools\hook.exe",
            arguments=("--done",),
        )
        runtime = _SessionExitHookRuntimeStub(initial_config)
        window = _SessionExitHookWindowStub(runtime)

        with patch("ui.main_window.SessionExitHookDialog") as dialog_cls:
            dialog_cls.return_value.show_modal.return_value = updated_config

            MainWindow._open_session_exit_hook_dialog(window, "session-1")

        dialog_cls.assert_called_once_with(
            window,
            config=initial_config,
            ui_language="ko",
        )
        self.assertEqual([("session-1", updated_config)], runtime.saved_configs)
        self.assertEqual(["훅 설정 저장"], window.status_messages)

    def test_open_session_exit_hook_dialog_cancel_keeps_runtime_config(self) -> None:
        initial_config = SessionExitHookConfig(
            enabled=True,
            executable_path=r"C:\Tools\hook.exe",
            arguments=("--done",),
        )
        runtime = _SessionExitHookRuntimeStub(initial_config)
        window = _SessionExitHookWindowStub(runtime)

        with patch("ui.main_window.SessionExitHookDialog") as dialog_cls:
            dialog_cls.return_value.show_modal.return_value = None

            MainWindow._open_session_exit_hook_dialog(window, "session-1")

        dialog_cls.assert_called_once_with(
            window,
            config=initial_config,
            ui_language="ko",
        )
        self.assertEqual([], runtime.saved_configs)
        self.assertEqual(initial_config, runtime.get_session_tab("session-1").exit_hook)
        self.assertEqual([], window.status_messages)

    def test_open_about_dialog_shows_about_modal(self) -> None:
        runtime = _RuntimeStub(
            settings=AppSettings(ui_language="ko"),
            update_result=SettingsUpdateResult(),
        )
        window = _MainWindowStub(runtime)

        with patch("ui.main_window.AboutDialog") as dialog_cls:
            MainWindow._open_about_dialog(window)

        dialog_cls.assert_called_once_with(
            window,
            app_name=APP_NAME,
            app_version=APP_VERSION,
            ui_language="ko",
        )
        dialog_cls.return_value.show_modal.assert_called_once_with()

    def test_open_settings_dialog_applies_font_without_refreshing_outputs(self) -> None:
        existing_settings = AppSettings(output_font_size=12, ui_language="ko")
        updated_settings = AppSettings(output_font_size=15, ui_language="ko")
        runtime = _RuntimeStub(
            settings=existing_settings,
            update_result=SettingsUpdateResult(),
        )
        window = _MainWindowStub(runtime)

        with patch("ui.main_window.SettingsDialog") as dialog_cls:
            dialog_cls.return_value.show_modal.return_value = updated_settings

            MainWindow._open_settings_dialog(window)

        self.assertEqual([updated_settings], runtime.updated_settings)
        self.assertEqual(1, window.refresh_settings_summary_calls)
        self.assertEqual(1, window.apply_output_font_to_all_sessions_calls)
        self.assertEqual(1, window.refresh_all_session_execution_option_controls_calls)
        self.assertEqual(0, window.refresh_session_outputs_for_all_sessions_calls)
        self.assertEqual(0, window.rebuild_static_ui_calls)

    def test_open_settings_dialog_rebuilds_ui_when_language_changes(self) -> None:
        existing_settings = AppSettings(ui_language="ko")
        updated_settings = AppSettings(ui_language="en")
        runtime = _RuntimeStub(
            settings=existing_settings,
            update_result=SettingsUpdateResult(),
        )
        window = _MainWindowStub(runtime)

        with patch("ui.main_window.SettingsDialog") as dialog_cls:
            dialog_cls.return_value.show_modal.return_value = updated_settings

            MainWindow._open_settings_dialog(window)

        self.assertEqual([updated_settings], runtime.updated_settings)
        self.assertEqual(1, window.rebuild_static_ui_calls)
        self.assertEqual(0, window.refresh_settings_summary_calls)
        self.assertEqual(0, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(0, window.apply_output_font_to_all_sessions_calls)
        self.assertEqual(0, window.refresh_all_session_execution_option_controls_calls)
        self.assertEqual(0, window.refresh_session_outputs_for_all_sessions_calls)

    def test_settings_retry_completed_event_reports_retried_jobs(self) -> None:
        runtime = _RuntimeStub(
            settings=AppSettings(ui_language="ko"),
            update_result=SettingsUpdateResult(),
        )
        window = _MainWindowStub(runtime)
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_runtime_event(
            window,
            SettingsRetryCompletedEvent(retried_job_ids=("job-1", "job-2")),
            updates,
        )
        MainWindow._apply_runtime_ui_updates(window, updates)

        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            ["설정 저장. 작업 2건을 다시 큐에 넣었습니다."],
            window.status_messages,
        )

    def test_job_status_changed_event_refreshes_changed_workspace_queue_summary_only(
        self,
    ) -> None:
        window = _RuntimeUiUpdateWindowStub()
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_runtime_event(
            window,
            JobStatusChangedEvent(
                job_id="job-1",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                previous_status=JobStatus.QUEUED,
                current_status=JobStatus.RUNNING,
                configuration_wait_reason=None,
                user_message=None,
            ),
            updates,
        )
        MainWindow._apply_runtime_ui_updates(window, updates)

        self.assertFalse(updates.refresh_queue_summaries)
        self.assertEqual(["session-1"], window.refreshed_session_ids)
        self.assertEqual(["workspace-1"], window.refreshed_workspace_ids)
        self.assertEqual(
            [("workspace-1",)],
            window.refreshed_queue_summary_workspace_ids,
        )

    def test_apply_runtime_ui_updates_shows_background_persistence_warning(self) -> None:
        runtime = _RuntimeStub(
            settings=AppSettings(ui_language="ko"),
            update_result=SettingsUpdateResult(),
        )
        window = _MainWindowStub(runtime)
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_runtime_event(
            window,
            PersistenceIssueEvent(
                issue=UseCaseIssue(
                    message="설정을 저장하지 못했습니다.",
                    operation="save_settings",
                )
            ),
            updates,
        )

        with patch("ui.main_window.messagebox.showwarning") as showwarning:
            MainWindow._apply_runtime_ui_updates(window, updates)

        self.assertEqual(["설정을 저장하지 못했습니다."], window.status_messages)
        showwarning.assert_called_once_with(
            "저장 경고",
            "설정을 저장하지 못했습니다.",
            parent=window,
        )

    def test_apply_runtime_ui_updates_shows_runtime_warning(self) -> None:
        runtime = _RuntimeStub(
            settings=AppSettings(ui_language="ko"),
            update_result=SettingsUpdateResult(),
        )
        window = _MainWindowStub(runtime)
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_runtime_event(
            window,
            RuntimeActionWarningEvent(
                title="프리셋 작업 경고",
                message="작업 프롬프트 개수가 선택된 분석 후보 개수와 다릅니다.",
            ),
            updates,
        )

        with patch("ui.main_window.messagebox.showwarning") as showwarning:
            MainWindow._apply_runtime_ui_updates(window, updates)

        self.assertEqual(
            ["작업 프롬프트 개수가 선택된 분석 후보 개수와 다릅니다."],
            window.status_messages,
        )
        showwarning.assert_called_once_with(
            "프리셋 작업 경고",
            "작업 프롬프트 개수가 선택된 분석 후보 개수와 다릅니다.",
            parent=window,
        )


class _SessionExitHookRuntimeStub:
    def __init__(self, exit_hook: SessionExitHookConfig) -> None:
        self.settings = AppSettings(ui_language="ko")
        self._session_tab = SessionTab(
            session_tab_id="session-1",
            workspace_tab_id="workspace-1",
            display_name="S1",
            exit_hook=exit_hook,
        )
        self.saved_configs: list[tuple[str, SessionExitHookConfig]] = []

    def get_session_tab(self, session_tab_id: str) -> SessionTab:
        if session_tab_id != self._session_tab.session_tab_id:
            raise KeyError(session_tab_id)
        return self._session_tab

    def set_session_exit_hook_config(
        self,
        session_tab_id: str,
        config: SessionExitHookConfig,
    ) -> SessionTab:
        self.saved_configs.append((session_tab_id, config))
        self._session_tab = replace(self._session_tab, exit_hook=config)
        return self._session_tab


class _SessionExitHookWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _SessionExitHookRuntimeStub) -> None:
        self._runtime = runtime
        self.status_messages: list[str] = []

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

