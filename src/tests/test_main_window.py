from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tkinter as tk
from tkinter import scrolledtext, ttk
import unittest
from unittest.mock import patch

from app.agent_cli_version import load_agent_cli_version_text
from app.runtime import (
    ImportedPromptSessionRegistration,
    ImportedPromptSessionsResult,
    JobStatusChangedEvent,
    PersistenceIssueEvent,
    PresetAnalysisJobSubmittedEvent,
    PresetAnalysisJobSubmissionFailedEvent,
    PresetCandidateJobsRegisteredEvent,
    RuntimeActionWarningEvent,
    SettingsRetryCompletedEvent,
    SettingsUpdateResult,
)
from app.scheduler import WorkspaceJobSummary
from app.version import APP_NAME, APP_VERSION
from app.use_cases import UseCaseIssue
from domain import (
    AgentExecutionOptions,
    AppSettings,
    Job,
    JobStatus,
    QueueStatus,
    QueueStopReason,
    SessionTab,
    SessionTabKind,
    WorkspaceQueueState,
)
from ui.main_window import (
    AUTO_COMMIT_PROMPT,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    EVENT_POLL_INTERVAL_MS,
    ExecutionOptionControls,
    MainWindow,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    PRESET_COMBOBOX_WIDTH,
    RuntimeUiUpdateBatch,
    SIDEBAR_COLLAPSED_WIDTH,
    SIDEBAR_INITIAL_WIDTH,
    SESSION_MODEL_COMBOBOX_WIDTH,
    SESSION_PROVIDER_COMBOBOX_WIDTH,
    SESSION_REASONING_COMBOBOX_WIDTH,
    WORKSPACE_SESSIONS_INITIAL_WIDTH,
    WORKSPACE_SESSION_ACTION_BUTTONS,
    WORKSPACE_TASK_LIST_INITIAL_WIDTH,
    _calculate_workspace_task_column_widths,
    _completed_activity_text,
    _finished_activity_text,
    _format_settings_summary,
    _format_workspace_task_summary,
    _job_progress_text,
    _localize_status_message,
    _running_activity_text,
    _session_job_message_text,
    _set_optional_label_text,
    _session_kind_uses_prompt_editor,
)
from ui.formatters import failed_activity_text as _failed_activity_text
from ui.dialogs import (
    AboutDialog,
    ABOUT_SOURCE_URL,
    BULK_IMPORT_EXAMPLE_TEXT,
    SETTINGS_AUTHOR_URL,
    BulkPromptImportDialog,
    BulkPromptImportDialogResult,
    LicenseNoticesDialog,
    ScheduledRunValidationError,
    SettingsDialog,
    default_scheduled_run_time,
    parse_scheduled_run_datetime,
)
from ui.i18n import text as ui_text
from main import build_runtime


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
        self.assertEqual(36, SIDEBAR_COLLAPSED_WIDTH)

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

    def test_restore_button_shows_when_sash_is_hidden_without_collapsed_state(self) -> None:
        window = _SidebarCollapseWindowStub(sash_position=0)

        MainWindow._refresh_sidebar_restore_button(window)

        self.assertTrue(window._sidebar_restore_button.is_gridded)
        self.assertEqual(">", window._sidebar_restore_button.text)

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
                    file_logging_enabled=True,
                    ui_language="ko",
                ),
                dialog.result,
            )
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
                BULK_IMPORT_EXAMPLE_TEXT,
                text_widgets[0].get("1.0", "end-1c"),
            )

            text_widgets[0].delete("1.0", tk.END)
            text_widgets[0].insert(tk.END, "```text\n/goal dialog\n```\n")
            register_buttons[0].invoke()

            self.assertEqual(
                BulkPromptImportDialogResult(
                    raw_text="```text\n/goal dialog\n```\n",
                    auto_commit_enabled=False,
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
                    "Corresponding Source: same-release source package\n"
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
            self.assertIn("same-release source package", about_text)
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


class MainWindowQueueStartTests(unittest.TestCase):
    def test_start_queue_requests_background_start(self) -> None:
        runtime = _QueueRuntimeStub()
        window = _QueueWindowStub(runtime)

        MainWindow._start_queue(window, "workspace-1")

        self.assertEqual(["workspace-1"], runtime.workspace_has_jobs_requests)
        self.assertEqual([], runtime.list_workspace_jobs_requests)
        self.assertEqual(["workspace-1"], runtime.background_starts)
        self.assertEqual({"workspace-1"}, window._queue_start_pending_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(["W1 큐 시작 중"], window.status_messages)

    def test_toggle_queue_starts_when_toggle_is_selected(self) -> None:
        runtime = _QueueRuntimeStub()
        window = _QueueWindowStub(runtime, toggle_value=True)

        MainWindow._toggle_queue(window, "workspace-1")

        self.assertEqual(["workspace-1"], runtime.background_starts)
        self.assertEqual([], runtime.stopped_queue_ids)
        self.assertEqual(["W1 큐 시작 중"], window.status_messages)

    def test_start_queue_does_nothing_when_workspace_task_list_is_empty(self) -> None:
        runtime = _QueueRuntimeStub(jobs=())
        window = _QueueWindowStub(runtime, toggle_value=True)

        MainWindow._start_queue(window, "workspace-1")

        self.assertEqual(["workspace-1"], runtime.workspace_has_jobs_requests)
        self.assertEqual([], runtime.list_workspace_jobs_requests)
        self.assertEqual([], runtime.background_starts)
        self.assertEqual(set(), window._queue_start_pending_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            ["W1 작업 없음. 큐를 시작하지 않았습니다."],
            window.status_messages,
        )

    def test_toggle_queue_stops_when_toggle_is_cleared(self) -> None:
        runtime = _QueueRuntimeStub()
        window = _QueueWindowStub(runtime, toggle_value=False)
        window._queue_start_pending_workspace_ids.add("workspace-1")

        MainWindow._toggle_queue(window, "workspace-1")

        self.assertEqual([], runtime.background_starts)
        self.assertEqual(["workspace-1"], runtime.stopped_queue_ids)
        self.assertEqual(set(), window._queue_start_pending_workspace_ids)
        self.assertEqual(["W1 큐 중지"], window.status_messages)


class MainWindowScheduledRunTests(unittest.TestCase):
    def test_due_schedule_starts_only_open_workspaces_with_queued_jobs(self) -> None:
        queued_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )
        waiting_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-2",
            session_tab_id="session-2",
            prompt="waiting",
            status=JobStatus.WAITING_FOR_CONFIGURATION,
        )
        closed_workspace_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-3",
            session_tab_id="session-3",
            prompt="closed",
            status=JobStatus.QUEUED,
        )
        runtime = _ScheduledRunRuntimeStub(
            jobs=(queued_job, waiting_job, closed_workspace_job),
            open_workspace_ids=("workspace-1", "workspace-2"),
        )
        window = _ScheduledRunWindowStub(runtime)
        window._scheduled_run_at = datetime.now() - timedelta(minutes=1)

        MainWindow._on_scheduled_run_timer(window)

        self.assertIsNone(window._scheduled_run_at)
        self.assertFalse(window._scheduled_run_toggle_var.get())
        self.assertEqual(["workspace-1"], runtime.background_starts)
        self.assertEqual({"workspace-1"}, window._queue_start_pending_workspace_ids)
        self.assertEqual(
            ["W1 큐 시작 중", "예약실행으로 워크스페이스 1개 큐를 시작했습니다."],
            window.status_messages,
        )

    def test_due_schedule_without_queued_jobs_reports_no_work(self) -> None:
        waiting_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="waiting",
            status=JobStatus.WAITING_FOR_CONFIGURATION,
        )
        runtime = _ScheduledRunRuntimeStub(
            jobs=(waiting_job,),
            open_workspace_ids=("workspace-1",),
        )
        window = _ScheduledRunWindowStub(runtime)
        window._scheduled_run_at = datetime.now() - timedelta(minutes=1)

        MainWindow._on_scheduled_run_timer(window)

        self.assertEqual([], runtime.background_starts)
        self.assertEqual(["예약실행: 실행할 대기 작업이 없습니다."], window.status_messages)

    def test_refresh_scheduled_run_display_marks_button_pending(self) -> None:
        runtime = _ScheduledRunRuntimeStub(jobs=(), open_workspace_ids=())
        window = _ScheduledRunWindowStub(runtime)
        window._scheduled_run_at = datetime(2026, 6, 16, 19, 30)

        MainWindow._refresh_scheduled_run_display(window)

        self.assertTrue(window._scheduled_run_toggle_var.get())
        self.assertEqual("예약: 2026-06-16 19:30", window._scheduled_run_var.get())
        self.assertEqual("예약실행", window._scheduled_run_button.text)

    def test_cancel_scheduled_run_cancels_timer_and_clears_display(self) -> None:
        runtime = _ScheduledRunRuntimeStub(jobs=(), open_workspace_ids=())
        window = _ScheduledRunWindowStub(runtime)
        window._scheduled_run_at = datetime(2026, 6, 16, 19, 30)
        window._scheduled_run_after_id = "after-1"

        MainWindow._cancel_scheduled_run(window, update_status=True)

        self.assertIsNone(window._scheduled_run_at)
        self.assertIsNone(window._scheduled_run_after_id)
        self.assertEqual(["after-1"], window.canceled_after_ids)
        self.assertFalse(window._scheduled_run_toggle_var.get())
        self.assertEqual("예약 없음", window._scheduled_run_var.get())
        self.assertEqual(["예약실행을 취소했습니다."], window.status_messages)


class MainWindowPresetSessionTests(unittest.TestCase):
    def test_workspace_button_row_places_preset_add_after_new_session(self) -> None:
        self.assertEqual(
            ("New Session", "New Preset", "Import"),
            tuple(spec.text for spec in WORKSPACE_SESSION_ACTION_BUTTONS),
        )
        self.assertEqual(
            (2, 3, 4),
            tuple(spec.column for spec in WORKSPACE_SESSION_ACTION_BUTTONS),
        )

    def test_create_preset_session_opens_view_and_selects_tab(self) -> None:
        runtime = _CreatePresetSessionRuntimeStub()
        window = _CreatePresetSessionWindowStub(runtime)

        MainWindow._create_preset_session_for_workspace(window, "workspace-1")

        self.assertEqual(["workspace-1"], runtime.open_preset_session_workspace_ids)
        self.assertEqual(["session-preset-1"], window.ensured_session_ids)
        self.assertEqual(["session-preset-1"], window.refreshed_session_ids)
        self.assertEqual(["workspace-1"], window.selected_workspace_ids)
        self.assertEqual([("workspace-1", "session-preset-1")], window.selected_session_ids)
        self.assertEqual(["P1 프리셋 생성"], window.status_messages)

    def test_bulk_import_registers_one_session_per_text_block(self) -> None:
        runtime = _BulkImportRuntimeStub()
        window = _BulkImportWindowStub(runtime)
        raw_text = """ignored
```text
/goal one
```
outside
```text
/goal two
```
"""

        with patch("ui.main_window.BulkPromptImportDialog") as dialog_cls:
            dialog_cls.return_value.show_modal.return_value = (
                BulkPromptImportDialogResult(
                    raw_text=raw_text,
                    auto_commit_enabled=False,
                )
            )

            MainWindow._open_bulk_import_dialog_for_workspace(window, "workspace-1")

        dialog_cls.assert_called_once_with(
            window,
            initial_auto_commit=True,
            ui_language="ko",
        )
        self.assertEqual(
            [("workspace-1", ("/goal one", "/goal two"), False)],
            runtime.import_calls,
        )
        self.assertEqual(["session-1", "session-2"], window.ensured_session_ids)
        self.assertEqual(
            [("session-1", False), ("session-2", False)],
            window.auto_commit_states,
        )
        self.assertEqual(1, window.drain_runtime_events_calls)
        self.assertEqual(
            [("session-1", "job-1"), ("session-2", "job-2")],
            window.refreshed_session_ids,
        )
        self.assertEqual(["workspace-1"], window.selected_workspace_ids)
        self.assertEqual([("workspace-1", "session-1")], window.selected_session_ids)
        self.assertEqual([("workspace-1", "job-1")], window.refreshed_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            ["지시문 2개, 작업 2건 등록"],
            window.status_messages,
        )

    def test_bulk_import_reports_text_block_parse_error(self) -> None:
        runtime = _BulkImportRuntimeStub()
        window = _BulkImportWindowStub(runtime)

        with (
            patch("ui.main_window.BulkPromptImportDialog") as dialog_cls,
            patch("ui.main_window.messagebox.showerror") as showerror,
        ):
            dialog_cls.return_value.show_modal.return_value = (
                BulkPromptImportDialogResult(
                    raw_text="/goal without fence",
                    auto_commit_enabled=True,
                )
            )

            MainWindow._open_bulk_import_dialog_for_workspace(window, "workspace-1")

        self.assertEqual([], runtime.import_calls)
        showerror.assert_called_once_with(
            "가져오기 오류",
            "가져올 ```text 코드 블록을 입력하세요.",
            parent=window,
        )

    def test_bulk_import_button_smoke_creates_sessions_and_jobs_with_runtime(self) -> None:
        with TemporaryDirectory() as storage_dir, TemporaryDirectory() as workspace_dir:
            storage_root = Path(storage_dir)
            runtime = build_runtime(storage_root=storage_root)
            workspace_result = runtime.open_workspace(workspace_dir)
            workspace_tab = workspace_result.open_result.workspace_tab

            window: MainWindow | None = None
            try:
                try:
                    window = MainWindow(runtime)
                except tk.TclError as error:
                    if _is_tk_display_unavailable(error):
                        self.skipTest(f"Tk display is unavailable: {error}")
                    raise

                window.withdraw()
                window.update_idletasks()
                workspace_view = window._workspace_views[workspace_tab.workspace_tab_id]
                import_buttons = _find_widgets_by_text(workspace_view.frame, "Import")
                self.assertEqual(1, len(import_buttons))

                raw_text = """memo
```text
/goal imported one
```
```text
/goal imported two
```
"""
                with patch("ui.main_window.BulkPromptImportDialog") as dialog_cls:
                    dialog_cls.return_value.show_modal.return_value = (
                        BulkPromptImportDialogResult(
                            raw_text=raw_text,
                            auto_commit_enabled=True,
                        )
                    )
                    import_buttons[0].invoke()

                window.update_idletasks()

                sessions = runtime.list_session_tabs(
                    workspace_tab.workspace_tab_id,
                    include_closed=False,
                )
                self.assertEqual(("S1", "S2"), tuple(tab.display_name for tab in sessions))
                self.assertEqual(
                    (SessionTabKind.NORMAL, SessionTabKind.NORMAL),
                    tuple(tab.kind for tab in sessions),
                )
                self.assertEqual(
                    ("session-tab-1", "session-tab-2"),
                    tuple(workspace_view.session_views),
                )

                first_jobs = runtime.list_jobs(session_tab_id=sessions[0].session_tab_id)
                second_jobs = runtime.list_jobs(session_tab_id=sessions[1].session_tab_id)
                self.assertEqual(
                    ("/goal imported one", AUTO_COMMIT_PROMPT),
                    tuple(job.prompt for job in first_jobs),
                )
                self.assertEqual(
                    ("/goal imported two", AUTO_COMMIT_PROMPT),
                    tuple(job.prompt for job in second_jobs),
                )
                self.assertFalse(
                    workspace_view.session_views[sessions[0].session_tab_id].prompt_text
                    is None
                )
                self.assertIn("Imported 2 instructions", window._status_message_var.get())
            finally:
                if window is not None:
                    _close_tk_window(window)
                else:
                    _shutdown_runtime(runtime)

    def test_normal_session_keeps_prompt_editor(self) -> None:
        self.assertTrue(_session_kind_uses_prompt_editor(SessionTabKind.NORMAL))

    def test_preset_session_hides_prompt_editor(self) -> None:
        self.assertFalse(_session_kind_uses_prompt_editor(SessionTabKind.PRESET))

    def test_preset_session_widget_tree_smoke_with_preexisting_normal_session(self) -> None:
        with TemporaryDirectory() as storage_dir, TemporaryDirectory() as workspace_dir:
            storage_root = Path(storage_dir)
            _write_prompt_pair(storage_root, language="Python", instruction="bug")
            runtime = build_runtime(storage_root=storage_root)
            runtime.update_settings(AppSettings(executable_path="codex"))
            workspace_result = runtime.open_workspace(workspace_dir)
            workspace_tab = workspace_result.open_result.workspace_tab
            normal_session = runtime.open_session(workspace_tab.workspace_tab_id)

            window: MainWindow | None = None
            try:
                try:
                    window = MainWindow(runtime)
                except tk.TclError as error:
                    if _is_tk_display_unavailable(error):
                        self.skipTest(f"Tk display is unavailable: {error}")
                    raise

                window.withdraw()
                window.update_idletasks()
                workspace_view = window._workspace_views[workspace_tab.workspace_tab_id]
                preset_buttons = _find_widgets_by_text(workspace_view.frame, "New Preset")
                import_buttons = _find_widgets_by_text(workspace_view.frame, "Import")
                self.assertEqual(1, len(preset_buttons))
                self.assertEqual(1, len(import_buttons))

                preset_buttons[0].invoke()
                window.update_idletasks()

                preset_sessions = [
                    session_tab
                    for session_tab in runtime.list_session_tabs(
                        workspace_tab.workspace_tab_id,
                        include_closed=False,
                    )
                    if session_tab.kind == SessionTabKind.PRESET
                ]
                self.assertEqual(1, len(preset_sessions))
                preset_session = preset_sessions[0]
                self.assertEqual("P2", preset_session.display_name)

                normal_widgets = workspace_view.session_views[normal_session.session_tab_id]
                preset_widgets = workspace_view.session_views[preset_session.session_tab_id]
                for _ in range(100):
                    runtime.process_background_events(max_items=32)
                    window._drain_runtime_events()
                    window.update()
                    if (
                        preset_widgets.preset_language_var.get() == "Python"
                        and preset_widgets.preset_instruction_var.get() == "bug"
                    ):
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(normal_widgets.prompt_text)
                self.assertIsNone(preset_widgets.prompt_text)
                self.assertIsNotNone(preset_widgets.preset_prompt_prefix_text)
                self.assertEqual(
                    ("Codex CLI",),
                    tuple(normal_widgets.agent_provider_combobox.cget("values")),
                )
                self.assertEqual(
                    "readonly",
                    str(normal_widgets.agent_provider_combobox.cget("state")),
                )
                self.assertEqual(
                    "Auto",
                    normal_widgets.model_var.get(),
                )
                preset_scrolled_texts = [
                    widget
                    for widget in _walk_widgets(preset_widgets.prompt_frame)
                    if isinstance(widget, scrolledtext.ScrolledText)
                ]
                self.assertEqual(
                    [preset_widgets.preset_prompt_prefix_text],
                    preset_scrolled_texts,
                )
                self.assertEqual("Python", preset_widgets.preset_language_var.get())
                self.assertEqual("bug", preset_widgets.preset_instruction_var.get())
                self.assertEqual("medium", preset_widgets.preset_work_priority_var.get())
                preset_comboboxes = [
                    widget
                    for widget in _walk_widgets(preset_widgets.prompt_frame)
                    if isinstance(widget, ttk.Combobox)
                ]
                preset_selection_comboboxes = (
                    preset_widgets.preset_language_combobox,
                    preset_widgets.preset_instruction_combobox,
                    preset_widgets.preset_work_priority_combobox,
                )
                preset_action_execution_comboboxes = (
                    preset_widgets.preset_action_agent_provider_combobox,
                    preset_widgets.preset_action_model_combobox,
                    preset_widgets.preset_action_reasoning_combobox,
                )
                shared_execution_combobox_pairs = (
                    (
                        preset_widgets.agent_provider_combobox,
                        preset_widgets.preset_action_agent_provider_combobox,
                    ),
                    (
                        preset_widgets.model_combobox,
                        preset_widgets.preset_action_model_combobox,
                    ),
                    (
                        preset_widgets.reasoning_combobox,
                        preset_widgets.preset_action_reasoning_combobox,
                    ),
                )
                self.assertEqual(6, len(preset_comboboxes))
                self.assertTrue(all(combobox is not None for combobox in preset_selection_comboboxes))
                self.assertTrue(
                    all(combobox is not None for combobox in preset_action_execution_comboboxes)
                )
                for top_combobox, action_combobox in shared_execution_combobox_pairs:
                    assert action_combobox is not None
                    self.assertNotEqual(
                        str(top_combobox.cget("textvariable")),
                        str(action_combobox.cget("textvariable")),
                    )
                    self.assertEqual(
                        tuple(top_combobox.cget("values")),
                        tuple(action_combobox.cget("values")),
                    )
                    self.assertEqual(
                        str(top_combobox.cget("state")),
                        str(action_combobox.cget("state")),
                    )
                for combobox in preset_selection_comboboxes:
                    assert combobox is not None
                    self.assertEqual(PRESET_COMBOBOX_WIDTH, int(combobox.cget("width")))
                    self.assertEqual("w", str(combobox.grid_info()["sticky"]))
                self.assertEqual(
                    (0, 0, 0),
                    tuple(
                        int(combobox.grid_info()["row"])
                        for combobox in preset_selection_comboboxes
                        if combobox is not None
                    ),
                )
                self.assertEqual(
                    (
                        SESSION_PROVIDER_COMBOBOX_WIDTH,
                        SESSION_MODEL_COMBOBOX_WIDTH,
                        SESSION_REASONING_COMBOBOX_WIDTH,
                    ),
                    tuple(
                        int(combobox.cget("width"))
                        for combobox in preset_action_execution_comboboxes
                        if combobox is not None
                    ),
                )
                auto_commit_checkbuttons = _find_widgets_by_text(
                    preset_widgets.prompt_frame,
                    "Auto Commit",
                )
                self.assertEqual(1, len(auto_commit_checkbuttons))
                register_buttons = _find_widgets_by_text(
                    preset_widgets.prompt_frame,
                    "Add",
                )
                self.assertEqual(1, len(register_buttons))

                preset_widgets.auto_commit_var.set(True)
                assert preset_widgets.preset_prompt_prefix_text is not None
                preset_widgets.preset_prompt_prefix_text.insert(
                    "1.0",
                    "custom analysis prefix",
                )
                register_buttons[0].invoke()
                for _ in range(100):
                    runtime.process_background_events(max_items=32)
                    window._drain_runtime_events()
                    window.update()
                    if "Candidate jobs will be created" in window._status_message_var.get():
                        break
                    time.sleep(0.01)
                jobs = runtime.list_jobs(session_tab_id=preset_session.session_tab_id)
                self.assertEqual(1, len(jobs))
                self.assertTrue(jobs[0].prompt.startswith("custom analysis prefix"))
                self.assertIn("analysis prompt", jobs[0].prompt)
                self.assertIn(
                    "Candidate jobs will be created",
                    window._status_message_var.get(),
                )
                self.assertEqual(
                    ("disabled", "disabled", "disabled"),
                    tuple(
                        str(combobox.cget("state"))
                        for combobox in preset_selection_comboboxes
                        if combobox is not None
                    ),
                )
                self.assertEqual(
                    ("disabled", "disabled", "disabled"),
                    tuple(
                        str(combobox.cget("state"))
                        for combobox in (
                            preset_widgets.agent_provider_combobox,
                            preset_widgets.model_combobox,
                            preset_widgets.reasoning_combobox,
                        )
                    ),
                )
                self.assertEqual(
                    ("disabled", "disabled", "disabled"),
                    tuple(
                        str(combobox.cget("state"))
                        for combobox in preset_action_execution_comboboxes
                        if combobox is not None
                    ),
                )
                self.assertEqual(
                    "disabled",
                    str(auto_commit_checkbuttons[0].cget("state")),
                )
                self.assertEqual(
                    "disabled",
                    str(preset_widgets.preset_prompt_prefix_text.cget("state")),
                )
                self.assertEqual("disabled", str(register_buttons[0].cget("state")))
            finally:
                if window is not None:
                    _close_tk_window(window)
                else:
                    _shutdown_runtime(runtime)


class MainWindowExecutionOptionControlTests(unittest.TestCase):
    def test_locked_session_keeps_registered_options_when_settings_candidates_change(
        self,
    ) -> None:
        execution_options = AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4",
            reasoning_effort="high",
        )
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="pi",
                executable_paths={"pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=execution_options,
                execution_options_locked=True,
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)

        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        self.assertEqual("Codex CLI", widgets.agent_provider_var.get())
        self.assertEqual(("Codex CLI",), widgets.agent_provider_combobox.cget("values"))
        self.assertEqual("gpt-5.4", widgets.model_var.get())
        self.assertEqual("high", widgets.reasoning_var.get())
        self.assertEqual("disabled", widgets.agent_provider_combobox.cget("state"))
        self.assertEqual("disabled", widgets.model_combobox.cget("state"))
        self.assertEqual("disabled", widgets.reasoning_combobox.cget("state"))
        self.assertEqual([], runtime.updated_execution_options)

    def test_unlocked_session_moves_to_first_configured_provider_after_settings_change(
        self,
    ) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="pi",
                executable_paths={"pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)

        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        self.assertEqual(
            [AgentExecutionOptions(agent_provider="pi")],
            runtime.updated_execution_options,
        )
        self.assertEqual("Pi Coding Agent", widgets.agent_provider_var.get())
        self.assertEqual(("Pi Coding Agent",), widgets.agent_provider_combobox.cget("values"))
        self.assertEqual("readonly", widgets.agent_provider_combobox.cget("state"))

    def test_pending_preset_registration_keeps_execution_options_disabled(self) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="pi",
                executable_paths={"pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        registration_row_provider_combobox = _ComboboxConfigureStub(())
        registration_row_model_combobox = _ComboboxConfigureStub(())
        registration_row_reasoning_combobox = _ComboboxConfigureStub(())
        widgets = _ExecutionOptionSessionWidgetsStub(
            preset_action_agent_provider_combobox=registration_row_provider_combobox,
            preset_action_model_combobox=registration_row_model_combobox,
            preset_action_reasoning_combobox=registration_row_reasoning_combobox,
        )
        window = _ExecutionOptionWindowStub(
            runtime,
            widgets,
            pending_registration_session_ids={"session-1"},
        )

        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        self.assertEqual("Codex CLI", widgets.agent_provider_var.get())
        self.assertEqual(("Codex CLI",), widgets.agent_provider_combobox.cget("values"))
        self.assertEqual("disabled", widgets.agent_provider_combobox.cget("state"))
        self.assertEqual("disabled", registration_row_provider_combobox.cget("state"))
        self.assertEqual("disabled", registration_row_model_combobox.cget("state"))
        self.assertEqual("disabled", registration_row_reasoning_combobox.cget("state"))
        self.assertEqual([], runtime.updated_execution_options)

    def test_preset_analysis_submission_failure_restores_execution_option_controls(
        self,
    ) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex"},
                ui_language="ko",
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        registration_row_provider_combobox = _ComboboxConfigureStub(())
        registration_row_model_combobox = _ComboboxConfigureStub(())
        registration_row_reasoning_combobox = _ComboboxConfigureStub(())
        widgets = _ExecutionOptionSessionWidgetsStub(
            preset_action_agent_provider_combobox=registration_row_provider_combobox,
            preset_action_model_combobox=registration_row_model_combobox,
            preset_action_reasoning_combobox=registration_row_reasoning_combobox,
        )
        window = _PresetSubmissionEventWindowStub(
            runtime,
            widgets,
            pending_registration_session_ids={"session-1"},
        )
        MainWindow._refresh_session_execution_option_controls(window, "session-1")
        self.assertEqual("disabled", widgets.agent_provider_combobox.cget("state"))
        self.assertEqual("disabled", registration_row_provider_combobox.cget("state"))

        updates = RuntimeUiUpdateBatch()
        MainWindow._apply_preset_analysis_job_submission_failed(
            window,
            PresetAnalysisJobSubmissionFailedEvent(
                session_tab_id="session-1",
                title="프리셋 작업 오류",
                message="등록 실패",
            ),
            updates,
        )

        self.assertEqual(set(), window._preset_registration_pending_session_ids)
        self.assertEqual(["session-1"], window.preset_registration_refreshes)
        self.assertEqual("readonly", widgets.agent_provider_combobox.cget("state"))
        self.assertEqual("readonly", widgets.model_combobox.cget("state"))
        self.assertEqual("readonly", widgets.reasoning_combobox.cget("state"))
        self.assertEqual("readonly", registration_row_provider_combobox.cget("state"))
        self.assertEqual("readonly", registration_row_model_combobox.cget("state"))
        self.assertEqual("readonly", registration_row_reasoning_combobox.cget("state"))
        self.assertEqual([("프리셋 작업 오류", "등록 실패")], updates.errors)
        self.assertEqual("등록 실패", updates.status_message)

    def test_preset_analysis_submission_failure_localizes_popup_for_english(self) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex"},
                ui_language="en",
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(agent_provider="codex"),
            ),
        )
        window = _PresetSubmissionEventWindowStub(
            runtime,
            _ExecutionOptionSessionWidgetsStub(),
            pending_registration_session_ids={"session-1"},
        )
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_preset_analysis_job_submission_failed(
            window,
            PresetAnalysisJobSubmissionFailedEvent(
                session_tab_id="session-1",
                title="프리셋 작업 오류",
                message="프리셋 분석 작업을 등록할 수 없습니다.",
            ),
            updates,
        )

        self.assertEqual(
            [("Preset Job Error", "Could not register the preset analysis job.")],
            updates.errors,
        )
        self.assertEqual(
            "Could not register the preset analysis job.",
            updates.status_message,
        )

    def test_preset_analysis_submission_success_remembers_prefix_after_registration(
        self,
    ) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex"},
                ui_language="ko",
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(agent_provider="codex"),
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub(
            preset_language_combobox=_ComboboxConfigureStub(("Python",)),
            preset_instruction_combobox=_ComboboxConfigureStub(("bug",)),
            preset_work_priority_combobox=_ComboboxConfigureStub(("medium",)),
            preset_prompt_prefix_text=_SubmitPromptTextStub("prefix"),
            preset_auto_commit_checkbutton=_ButtonConfigureStub(),
            preset_register_button=_ButtonConfigureStub(),
        )
        window = _PresetSubmissionEventWindowStub(
            runtime,
            widgets,
            pending_registration_session_ids={"session-1"},
        )
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_preset_analysis_job_submitted(
            window,
            PresetAnalysisJobSubmittedEvent(
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                job_id="job-1",
                analysis_prompt_prefix="prefix",
            ),
            updates,
        )

        self.assertEqual(set(), window._preset_registration_pending_session_ids)
        self.assertEqual([("workspace-1", "prefix")], window.remembered_prompt_prefixes)
        self.assertEqual("disabled", widgets.preset_prompt_prefix_text.state)
        self.assertEqual({"workspace-1"}, updates.workspace_task_lists)
        self.assertEqual(
            "job-1 프리셋 분석 등록. 후보 작업은 분석 후 생성됩니다.",
            updates.status_message,
        )

    def test_agent_provider_selection_resets_dependent_options(self) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex", "pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)
        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        widgets.agent_provider_var.set("Pi Coding Agent")
        MainWindow._handle_session_agent_provider_selected(window, "session-1")

        self.assertEqual(
            AgentExecutionOptions(agent_provider="pi"),
            runtime.updated_execution_options[-1],
        )
        self.assertEqual("Pi Coding Agent", widgets.agent_provider_var.get())
        self.assertEqual("Auto", widgets.model_var.get())
        self.assertEqual("Auto", widgets.reasoning_var.get())

    def test_preset_registration_row_execution_option_selection_updates_candidate_options(
        self,
    ) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex", "pi": "pi"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="P1",
                kind=SessionTabKind.PRESET,
                execution_options=AgentExecutionOptions(agent_provider="codex"),
            ),
        )
        registration_row_provider_combobox = _ComboboxConfigureStub(())
        registration_row_model_combobox = _ComboboxConfigureStub(())
        registration_row_reasoning_combobox = _ComboboxConfigureStub(())
        widgets = _ExecutionOptionSessionWidgetsStub(
            preset_action_agent_provider_combobox=registration_row_provider_combobox,
            preset_action_model_combobox=registration_row_model_combobox,
            preset_action_reasoning_combobox=registration_row_reasoning_combobox,
        )
        window = _ExecutionOptionWindowStub(runtime, widgets)
        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        self.assertEqual(
            widgets.agent_provider_combobox.cget("values"),
            registration_row_provider_combobox.cget("values"),
        )
        self.assertEqual(
            widgets.model_combobox.cget("values"),
            registration_row_model_combobox.cget("values"),
        )
        self.assertEqual(
            widgets.reasoning_combobox.cget("values"),
            registration_row_reasoning_combobox.cget("values"),
        )
        self.assertEqual(
            widgets.agent_provider_combobox.cget("state"),
            registration_row_provider_combobox.cget("state"),
        )
        self.assertEqual(
            widgets.model_combobox.cget("state"),
            registration_row_model_combobox.cget("state"),
        )
        self.assertEqual(
            widgets.reasoning_combobox.cget("state"),
            registration_row_reasoning_combobox.cget("state"),
        )

        assert widgets.preset_action_agent_provider_var is not None
        widgets.preset_action_agent_provider_var.set("Pi Coding Agent")
        MainWindow._handle_preset_action_agent_provider_selected(window, "session-1")

        self.assertEqual(
            [],
            runtime.updated_execution_options,
        )
        self.assertEqual("Codex CLI", widgets.agent_provider_var.get())
        self.assertEqual("Pi Coding Agent", widgets.preset_action_agent_provider_var.get())
        assert widgets.preset_action_model_var is not None
        assert widgets.preset_action_reasoning_var is not None
        self.assertEqual("Auto", widgets.preset_action_model_var.get())
        self.assertEqual("Auto", widgets.preset_action_reasoning_var.get())
        self.assertEqual(
            AgentExecutionOptions(agent_provider="pi"),
            widgets.preset_action_execution_controls.execution_options
            if widgets.preset_action_execution_controls is not None
            else None,
        )
        self.assertEqual(
            ("Auto",),
            registration_row_model_combobox.cget("values"),
        )
        self.assertEqual(
            ("Auto", "off", "minimal", "low", "medium", "high", "xhigh"),
            registration_row_reasoning_combobox.cget("values"),
        )
        self.assertEqual(
            "readonly",
            registration_row_model_combobox.cget("state"),
        )
        self.assertEqual(
            "readonly",
            registration_row_reasoning_combobox.cget("state"),
        )

    def test_model_selection_resets_reasoning_to_auto(self) -> None:
        runtime = _ExecutionOptionRuntimeStub(
            settings=AppSettings(
                agent_provider="codex",
                executable_paths={"codex": "codex"},
            ),
            session_tab=SessionTab(
                session_tab_id="session-1",
                workspace_tab_id="workspace-1",
                display_name="S1",
                execution_options=AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4",
                    reasoning_effort="high",
                ),
            ),
        )
        widgets = _ExecutionOptionSessionWidgetsStub()
        window = _ExecutionOptionWindowStub(runtime, widgets)
        MainWindow._refresh_session_execution_option_controls(window, "session-1")

        widgets.model_var.set("gpt-5.4-mini")
        MainWindow._handle_session_model_selected(window, "session-1")

        self.assertEqual(
            AgentExecutionOptions(agent_provider="codex", model="gpt-5.4-mini"),
            runtime.updated_execution_options[-1],
        )
        self.assertEqual("gpt-5.4-mini", widgets.model_var.get())
        self.assertEqual("Auto", widgets.reasoning_var.get())


class MainWindowSubmitJobTests(unittest.TestCase):
    def test_submit_job_adds_auto_commit_follow_up_when_checked(self) -> None:
        runtime = _SubmitJobRuntimeStub()
        window = _SubmitJobWindowStub(runtime, prompt="implement feature", auto_commit=True)

        MainWindow._submit_job_for_session(window, "session-1")

        self.assertEqual(
            [("session-1", "implement feature"), ("session-1", AUTO_COMMIT_PROMPT)],
            runtime.submitted_jobs,
        )
        self.assertEqual(
            [window.execution_options, window.execution_options],
            runtime.submitted_execution_options,
        )
        self.assertEqual(("1.0", "end"), window.session_widgets.prompt_text.deleted_ranges[0])
        self.assertEqual(1, window.drain_runtime_events_calls)
        self.assertEqual([("session-1", "job-1")], window.refreshed_session_ids)
        self.assertEqual([("workspace-1", "job-1")], window.refreshed_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            ["job-1, job-2 자동커밋 등록"],
            window.status_messages,
        )

    def test_submit_job_skips_auto_commit_follow_up_when_unchecked(self) -> None:
        runtime = _SubmitJobRuntimeStub()
        window = _SubmitJobWindowStub(runtime, prompt="implement feature", auto_commit=False)

        MainWindow._submit_job_for_session(window, "session-1")

        self.assertEqual([("session-1", "implement feature")], runtime.submitted_jobs)
        self.assertEqual([window.execution_options], runtime.submitted_execution_options)
        self.assertEqual(["job-1 등록"], window.status_messages)

    def test_submit_job_stops_when_no_configured_agent_provider_is_selected(self) -> None:
        runtime = _SubmitJobRuntimeStub()
        window = _SubmitJobWindowStub(
            runtime,
            prompt="implement feature",
            auto_commit=False,
            execution_options=None,
        )

        MainWindow._submit_job_for_session(window, "session-1")

        self.assertEqual([], runtime.submitted_jobs)
        self.assertEqual([], window.status_messages)


class MainWindowPresetLanguagePreferenceTests(unittest.TestCase):
    def test_selected_preset_language_becomes_default_for_same_workspace_path(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
        )

        MainWindow._remember_preset_language_for_session(window, "session-preset-1")

        default_language = MainWindow._default_preset_language_for_workspace(
            window,
            "workspace-2",
            ("Python", "Rust", "Tauri"),
        )
        self.assertEqual("Rust", default_language)

    def test_selected_preset_language_is_scoped_to_workspace(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"D:\OtherRepo",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
        )

        MainWindow._remember_preset_language_for_session(window, "session-preset-1")

        default_language = MainWindow._default_preset_language_for_workspace(
            window,
            "workspace-2",
            ("Python", "Rust"),
        )
        self.assertEqual("Python", default_language)

    def test_unavailable_remembered_preset_language_falls_back_to_first_language(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
        )

        MainWindow._remember_preset_language_for_session(window, "session-preset-1")

        default_language = MainWindow._default_preset_language_for_workspace(
            window,
            "workspace-1",
            ("Python", "Tauri"),
        )
        self.assertEqual("Python", default_language)


class MainWindowPresetInstructionPreferenceTests(unittest.TestCase):
    def test_selected_preset_instruction_becomes_default_for_same_workspace_language(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_instruction="optimize",
        )

        MainWindow._remember_preset_instruction_for_session(window, "session-preset-1")

        default_instruction = MainWindow._default_preset_instruction_for_workspace(
            window,
            "workspace-2",
            "Rust",
            ("bug", "optimize", "refactor"),
        )
        self.assertEqual("optimize", default_instruction)

    def test_selected_preset_instruction_is_scoped_to_language(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Python",
            session_instruction="optimize",
        )

        MainWindow._remember_preset_instruction_for_session(window, "session-preset-1")

        default_instruction = MainWindow._default_preset_instruction_for_workspace(
            window,
            "workspace-1",
            "Rust",
            ("bug", "optimize"),
        )
        self.assertEqual("bug", default_instruction)

    def test_unavailable_remembered_preset_instruction_falls_back_to_first_instruction(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Python",
            session_instruction="optimize",
        )

        MainWindow._remember_preset_instruction_for_session(window, "session-preset-1")

        default_instruction = MainWindow._default_preset_instruction_for_workspace(
            window,
            "workspace-1",
            "Python",
            ("bug", "refactor"),
        )
        self.assertEqual("bug", default_instruction)


class MainWindowPresetWorkPriorityPreferenceTests(unittest.TestCase):
    def test_selected_preset_work_priority_becomes_default_for_same_workspace_path(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_work_priority="high",
        )

        MainWindow._remember_preset_work_priority_for_session(
            window,
            "session-preset-1",
        )

        default_priority = MainWindow._default_preset_work_priority_for_workspace(
            window,
            "workspace-2",
        )
        self.assertEqual("high", default_priority)

    def test_selected_preset_work_priority_is_scoped_to_workspace(self) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"D:\OtherRepo",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_work_priority="low",
        )

        MainWindow._remember_preset_work_priority_for_session(
            window,
            "session-preset-1",
        )

        default_priority = MainWindow._default_preset_work_priority_for_workspace(
            window,
            "workspace-2",
        )
        self.assertEqual("medium", default_priority)

    def test_unavailable_remembered_preset_work_priority_falls_back_to_default(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_work_priority="urgent",
        )

        MainWindow._remember_preset_work_priority_for_session(
            window,
            "session-preset-1",
        )

        default_priority = MainWindow._default_preset_work_priority_for_workspace(
            window,
            "workspace-1",
        )
        self.assertEqual("medium", default_priority)


class MainWindowPresetPromptPrefixPreferenceTests(unittest.TestCase):
    def test_submitted_preset_prompt_prefix_becomes_default_for_same_workspace_path(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_prompt_prefix="prefix line",
        )

        MainWindow._remember_preset_prompt_prefix_for_session(
            window,
            "session-preset-1",
        )

        default_prefix = MainWindow._default_preset_prompt_prefix_for_workspace(
            window,
            "workspace-2",
        )
        self.assertEqual("prefix line", default_prefix)

    def test_empty_preset_prompt_prefix_clears_remembered_workspace_default(
        self,
    ) -> None:
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={"workspace-1": r"C:\Repo"},
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_prompt_prefix="",
        )
        window._workspace_preset_prompt_prefixes[
            MainWindow._workspace_preset_language_key(window, "workspace-1")
        ] = "old prefix"

        MainWindow._remember_preset_prompt_prefix_for_session(
            window,
            "session-preset-1",
        )

        default_prefix = MainWindow._default_preset_prompt_prefix_for_workspace(
            window,
            "workspace-1",
        )
        self.assertEqual("", default_prefix)


class MainWindowPresetActionExecutionOptionPreferenceTests(unittest.TestCase):
    def test_selected_preset_action_execution_options_become_default_for_same_workspace_path(
        self,
    ) -> None:
        selected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-pro",
            reasoning_effort="high",
        )
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"c:\repo\\",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_preset_action_execution_options=selected_execution_options,
        )

        MainWindow._remember_preset_action_execution_options_for_session(
            window,
            "session-preset-1",
        )

        default_options = (
            MainWindow._default_preset_action_execution_options_for_workspace(
                window,
                "workspace-2",
                fallback=AgentExecutionOptions(agent_provider="codex"),
            )
        )
        self.assertEqual(selected_execution_options, default_options)

    def test_selected_preset_action_execution_options_are_scoped_to_workspace(
        self,
    ) -> None:
        selected_execution_options = AgentExecutionOptions(
            agent_provider="pi",
            model="pi-pro",
            reasoning_effort="high",
        )
        fallback_execution_options = AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4",
        )
        window = _PresetLanguagePreferenceWindowStub(
            workspace_paths={
                "workspace-1": r"C:\Repo",
                "workspace-2": r"D:\OtherRepo",
            },
            session_workspace_ids={"session-preset-1": "workspace-1"},
            session_language="Rust",
            session_preset_action_execution_options=selected_execution_options,
        )

        MainWindow._remember_preset_action_execution_options_for_session(
            window,
            "session-preset-1",
        )

        default_options = (
            MainWindow._default_preset_action_execution_options_for_workspace(
                window,
                "workspace-2",
                fallback=fallback_execution_options,
            )
        )
        self.assertEqual(fallback_execution_options, default_options)


class MainWindowPresetSubmitTests(unittest.TestCase):
    def test_preset_submit_uses_selected_inputs_without_prompt_editor_text(self) -> None:
        runtime = _SubmitPresetRuntimeStub()
        window = _SubmitPresetWindowStub(
            runtime,
            auto_commit=True,
            analysis_prompt_prefix="prefix text",
        )

        MainWindow._submit_preset_job_for_session(window, "session-preset-1")

        self.assertEqual(
            [("session-preset-1", "Python", "bug", "medium", True)],
            runtime.submitted_preset_jobs,
        )
        self.assertEqual(["prefix text"], runtime.submitted_analysis_prompt_prefixes)
        self.assertEqual(["prefix text"], window.remembered_prompt_prefixes)
        self.assertEqual(
            [AgentExecutionOptions(agent_provider="codex", model="gpt-5.4")],
            runtime.submitted_execution_options,
        )
        self.assertEqual(
            [
                AgentExecutionOptions(
                    agent_provider="codex",
                    model="gpt-5.4-mini",
                    reasoning_effort="low",
                )
            ],
            runtime.submitted_candidate_execution_options,
        )
        self.assertEqual(1, window.drain_runtime_events_calls)
        self.assertEqual([("session-preset-1", "job-1")], window.refreshed_session_ids)
        self.assertEqual([("workspace-1", "job-1")], window.refreshed_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            [
                "job-1 프리셋 분석 등록. 후보 작업은 분석 후 생성됩니다."
            ],
            window.status_messages,
        )

    def test_preset_submit_disables_inputs_and_buttons_after_success(self) -> None:
        runtime = _SubmitPresetRuntimeStub()
        language_combobox = _ComboboxConfigureStub(("Python",))
        instruction_combobox = _ComboboxConfigureStub(("bug",))
        work_priority_combobox = _ComboboxConfigureStub(("high", "medium", "low"))
        auto_commit_checkbutton = _ButtonConfigureStub()
        register_button = _ButtonConfigureStub()
        window = _SubmitPresetWindowStub(
            runtime,
            auto_commit=True,
            language_combobox=language_combobox,
            instruction_combobox=instruction_combobox,
            work_priority_combobox=work_priority_combobox,
            auto_commit_checkbutton=auto_commit_checkbutton,
            register_button=register_button,
        )

        MainWindow._submit_preset_job_for_session(window, "session-preset-1")

        self.assertEqual("disabled", language_combobox.state)
        self.assertEqual("disabled", instruction_combobox.state)
        self.assertEqual("disabled", work_priority_combobox.state)
        self.assertEqual("disabled", window.session_widgets.preset_prompt_prefix_text.state)
        self.assertEqual("disabled", auto_commit_checkbutton.state)
        self.assertEqual("disabled", register_button.state)

    def test_preset_submit_rejects_missing_required_values(self) -> None:
        runtime = _SubmitPresetRuntimeStub()
        window = _SubmitPresetWindowStub(
            runtime,
            auto_commit=True,
            language="",
            instruction="bug",
            work_priority="",
        )

        with patch("ui.main_window.messagebox.showerror") as showerror:
            MainWindow._submit_preset_job_for_session(window, "session-preset-1")

        self.assertEqual([], runtime.submitted_preset_jobs)
        self.assertEqual(0, window.drain_runtime_events_calls)
        self.assertEqual([], window.refreshed_session_ids)
        self.assertEqual([], window.refreshed_workspace_ids)
        showerror.assert_called_once_with(
            "입력 오류",
            "언어, 지시문, 우선순위를 선택하세요.",
            parent=window,
        )

    def test_preset_submit_reports_prompt_pair_error_without_registering_ui_updates(self) -> None:
        runtime = _SubmitPresetRuntimeStub(
            submit_error=ValueError("프리셋 prompt 파일 쌍을 찾거나 읽지 못했습니다.")
        )
        window = _SubmitPresetWindowStub(
            runtime,
            auto_commit=False,
            analysis_prompt_prefix="failed prefix",
        )

        with patch("ui.main_window.messagebox.showerror") as showerror:
            MainWindow._submit_preset_job_for_session(window, "session-preset-1")

        self.assertEqual(
            [("session-preset-1", "Python", "bug", "medium", False)],
            runtime.submitted_preset_jobs,
        )
        self.assertEqual([], window.remembered_prompt_prefixes)
        self.assertEqual(0, window.drain_runtime_events_calls)
        self.assertEqual([], window.refreshed_session_ids)
        self.assertEqual([], window.refreshed_workspace_ids)
        showerror.assert_called_once_with(
            "입력 오류",
            "프리셋 prompt 파일 쌍을 찾거나 읽지 못했습니다.",
            parent=window,
        )

    def test_preset_candidate_tab_insert_index_places_candidate_after_parent(self) -> None:
        window = _SessionOrderWindowStub(
            ordered_session_ids=("session-1", "preset-parent", "candidate-1", "session-2"),
            existing_session_ids=("session-1", "preset-parent", "session-2"),
        )

        insert_index = MainWindow._session_tab_insert_index(
            window,
            "workspace-1",
            "candidate-1",
        )

        self.assertEqual(2, insert_index)

    def test_preset_candidate_registration_event_reports_registered_job_count(self) -> None:
        window = _PresetCandidateRegistrationWindowStub()
        updates = RuntimeUiUpdateBatch()

        MainWindow._apply_runtime_event(
            window,
            PresetCandidateJobsRegisteredEvent(
                workspace_tab_id="workspace-1",
                parent_session_tab_id="preset-parent",
                candidate_session_tab_ids=("candidate-1", "candidate-2"),
                registered_job_ids=("job-1", "job-2", "job-3", "job-4"),
                auto_commit_enabled=True,
            ),
            updates,
        )
        MainWindow._apply_runtime_ui_updates(window, updates)

        self.assertEqual(["candidate-1", "candidate-2"], window.ensured_session_ids)
        self.assertEqual(
            {"candidate-1": True, "candidate-2": True},
            {
                session_tab_id: widgets.auto_commit_var.get()
                for session_tab_id, widgets in window.session_widgets.items()
            },
        )
        self.assertEqual(["candidate-1", "candidate-2"], window.refreshed_session_ids)
        self.assertEqual(["workspace-1"], window.refreshed_workspace_ids)
        self.assertEqual(["workspace-1"], window.synced_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(
            ["후보 세션 2개, 작업 4건 등록"],
            window.status_messages,
        )


class MainWindowWorkspaceIndicatorTests(unittest.TestCase):
    def test_workspace_indicator_clears_when_started_empty_queue_has_no_running_jobs(self) -> None:
        runtime = _WorkspaceQueueSummaryRuntimeStub(())
        window = _WorkspaceQueueSummaryWindowStub(runtime)

        MainWindow._refresh_workspace_queue_summaries(window)

        self.assertEqual("큐: 시작", window.workspace_view.queue_var.value)
        self.assertTrue(window.workspace_view.queue_toggle_var.get())
        self.assertEqual("중지", window.workspace_view.queue_toggle_button.text)
        self.assertEqual([("workspace-1", False)], window.indicator_calls)

    def test_workspace_queue_summary_shows_all_jobs_completed_stop_reason(self) -> None:
        completed_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done",
            status=JobStatus.COMPLETED,
        )
        runtime = _WorkspaceQueueSummaryRuntimeStub(
            (completed_job,),
            queue_status=QueueStatus.STOPPED,
            last_stop_reason=QueueStopReason.ALL_JOBS_COMPLETED,
        )
        window = _WorkspaceQueueSummaryWindowStub(runtime)

        MainWindow._refresh_workspace_queue_summaries(window)

        self.assertEqual("큐: 중지 (모든 작업 종료)", window.workspace_view.queue_var.value)
        self.assertFalse(window.workspace_view.queue_toggle_var.get())
        self.assertEqual("시작", window.workspace_view.queue_toggle_button.text)
        self.assertEqual([("workspace-1", False)], window.indicator_calls)

    def test_workspace_queue_summary_shows_pending_start_on_toggle(self) -> None:
        runtime = _WorkspaceQueueSummaryRuntimeStub(
            (),
            queue_status=QueueStatus.STOPPED,
        )
        window = _WorkspaceQueueSummaryWindowStub(runtime)
        window._queue_start_pending_workspace_ids.add("workspace-1")

        MainWindow._refresh_workspace_queue_summaries(window)

        self.assertEqual("큐: 시작 중", window.workspace_view.queue_var.value)
        self.assertTrue(window.workspace_view.queue_toggle_var.get())
        self.assertEqual("중지", window.workspace_view.queue_toggle_button.text)

    def test_workspace_queue_summary_disables_start_when_task_list_is_empty(self) -> None:
        runtime = _WorkspaceQueueSummaryRuntimeStub(
            (),
            queue_status=QueueStatus.STOPPED,
        )
        window = _WorkspaceQueueSummaryWindowStub(runtime)

        MainWindow._refresh_workspace_queue_summaries(window)

        self.assertEqual("큐: 중지", window.workspace_view.queue_var.value)
        self.assertFalse(window.workspace_view.queue_toggle_var.get())
        self.assertEqual("시작", window.workspace_view.queue_toggle_button.text)
        self.assertEqual("disabled", window.workspace_view.queue_toggle_button.state)

    def test_workspace_indicator_shows_when_workspace_has_running_job(self) -> None:
        running_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        runtime = _WorkspaceQueueSummaryRuntimeStub((running_job,))
        window = _WorkspaceQueueSummaryWindowStub(runtime)

        MainWindow._refresh_workspace_queue_summaries(window)

        self.assertEqual([("workspace-1", True)], window.indicator_calls)

    def test_workspace_queue_summary_refresh_can_target_one_workspace(self) -> None:
        running_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-2",
            session_tab_id="session-1",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        runtime = _WorkspaceQueueSummaryRuntimeStub((running_job,))
        window = _WorkspaceQueueSummaryWindowStub(runtime)
        workspace_2_view = _WorkspaceQueueSummaryViewStub(
            queue_var=_StringVarStub(),
            queue_toggle_var=_BoolVarStub(False),
            queue_toggle_button=_ButtonConfigureStub(),
        )
        window._workspace_views["workspace-2"] = workspace_2_view

        MainWindow._refresh_workspace_queue_summaries(window, ("workspace-2",))

        self.assertEqual([("workspace-2",)], runtime.summarize_workspace_jobs_requests)
        self.assertEqual([], runtime.list_jobs_by_workspace_requests)
        self.assertEqual([], runtime.list_workspace_jobs_requests)
        self.assertEqual("", window.workspace_view.queue_var.value)
        self.assertEqual("큐: 시작", workspace_2_view.queue_var.value)
        self.assertEqual([("workspace-2", True)], window.indicator_calls)


class MainWindowLogTextTests(unittest.TestCase):
    def test_log_refresh_autoscrolls_when_log_was_empty(self) -> None:
        widget = _TextWidgetStub(content="", yview=(0.0, 1.0))

        MainWindow._set_text_content(
            object(),
            widget,
            "line 1\nline 2",
            auto_scroll_to_end=True,
        )

        self.assertEqual("line 1\nline 2", widget.content)
        self.assertEqual(["normal", "disabled"], widget.states)
        self.assertEqual(["end"], widget.see_calls)

    def test_log_refresh_follows_when_view_is_already_at_bottom(self) -> None:
        widget = _TextWidgetStub(content="old log", yview=(0.75, 0.99))

        MainWindow._set_text_content(
            object(),
            widget,
            "old log\nnew log",
            auto_scroll_to_end=True,
        )

        self.assertEqual(["end"], widget.see_calls)

    def test_log_refresh_does_not_steal_manual_scroll_position(self) -> None:
        widget = _TextWidgetStub(content="old log", yview=(0.0, 0.5))

        MainWindow._set_text_content(
            object(),
            widget,
            "old log\nnew log",
            auto_scroll_to_end=True,
        )

        self.assertEqual([], widget.see_calls)


class MainWindowSessionHistoryTests(unittest.TestCase):
    def test_rendered_history_turns_cache_content_end_offsets(self) -> None:
        window = _SessionHistoryWindowStub()
        turns = (
            _HistoryTurnStub(
                started_at=_history_dt(1),
                completed_at=_history_dt(2),
                prompt_text="first prompt",
                response_text="first response",
            ),
            _HistoryTurnStub(
                started_at=_history_dt(3),
                completed_at=None,
                prompt_text="second prompt",
                response_text=None,
            ),
        )

        rendered_history = window._render_session_history_turns(
            turns,
            start_index=1,
            language="ko",
            content_length=0,
        )
        rendered_turns = tuple(
            rendered_turn for rendered_turn, _block_text in rendered_history
        )
        joined_history = window._join_session_history_blocks(rendered_history)

        self.assertEqual(
            len(rendered_history[0][1]),
            window._session_history_prefix_length(rendered_turns, 1),
        )
        self.assertEqual(
            len(joined_history),
            window._session_history_prefix_length(rendered_turns, 2),
        )


class MainWindowSessionSelectionTests(unittest.TestCase):
    def test_completed_activity_lists_completed_job_numbers_only(self) -> None:
        completed_first = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done first",
            status=JobStatus.COMPLETED,
        )
        completed_second = Job(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done second",
            status=JobStatus.COMPLETED,
        )
        failed_job = Job(
            job_id="job-4",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="failed",
            status=JobStatus.FAILED,
        )
        completed_without_number = Job(
            job_id="manual-job",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="manual",
            status=JobStatus.COMPLETED,
        )

        self.assertEqual(
            "종료: 완료 job-1, 2",
            _completed_activity_text(
                (
                    completed_first,
                    failed_job,
                    completed_without_number,
                    completed_second,
                ),
                language="ko",
            ),
        )

    def test_completed_activity_shows_empty_text_without_completed_jobs(self) -> None:
        queued_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )

        self.assertEqual(
            "종료: 없음",
            _completed_activity_text((queued_job,), language="ko"),
        )

    def test_finished_activity_shows_pending_job_instead_of_completed_label(self) -> None:
        queued_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )

        self.assertEqual(
            "대기중: job-1",
            _finished_activity_text(queued_job, (queued_job,), "", language="ko"),
        )

    def test_finished_activity_shows_pending_job_with_completed_numbers(self) -> None:
        completed_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done",
            status=JobStatus.COMPLETED,
        )
        queued_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )

        self.assertEqual(
            "대기중: job-2 (1)",
            _finished_activity_text(
                queued_job,
                (completed_job, queued_job),
                "",
                language="ko",
            ),
        )

    def test_failed_activity_merges_failure_message_and_completed_numbers(self) -> None:
        completed_first = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done first",
            status=JobStatus.COMPLETED,
        )
        completed_second = Job(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done second",
            status=JobStatus.COMPLETED,
        )
        failed_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="failed",
            status=JobStatus.FAILED,
        )

        self.assertEqual(
            "종료: 실패 job-3 (1, 2) Reconnecting... 2/5 (request timed out)",
            _failed_activity_text(
                failed_job,
                (completed_first, completed_second, failed_job),
                "실행 실패: Reconnecting... 2/5 (request timed out)",
                language="ko",
            ),
        )

    def test_failed_activity_omits_default_failure_message(self) -> None:
        failed_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="failed",
            status=JobStatus.FAILED,
        )

        self.assertEqual(
            "종료: 실패 job-3",
            _failed_activity_text(failed_job, (failed_job,), "실행 실패", language="ko"),
        )

    def test_finished_activity_merges_canceled_job_message_into_activity_line(self) -> None:
        completed_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done",
            status=JobStatus.COMPLETED,
        )
        canceled_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="canceled",
            status=JobStatus.CANCELED,
        )

        self.assertEqual(
            "종료: 작업을 취소했습니다. job-3 (1)",
            _finished_activity_text(
                canceled_job,
                (completed_job, canceled_job),
                "작업을 취소했습니다.",
                language="ko",
            ),
        )

    def test_finished_activity_uses_completed_summary_without_secondary_message(self) -> None:
        completed_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done",
            status=JobStatus.COMPLETED,
        )

        self.assertEqual(
            "종료: 완료 job-1",
            _finished_activity_text(
                completed_job,
                (completed_job,),
                "작업 완료",
                language="ko",
            ),
        )

    def test_session_summary_merges_failed_job_message_into_activity_line(self) -> None:
        completed_first = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done first",
            status=JobStatus.COMPLETED,
        )
        completed_second = Job(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done second",
            status=JobStatus.COMPLETED,
        )
        failed_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="failed",
            status=JobStatus.FAILED,
        )
        window = _SessionSelectionWindowStub(
            (completed_first, completed_second, failed_job),
            selected_job_id="job-3",
            job_user_messages={
                "job-3": "실행 실패: Reconnecting... 2/5 (request timed out)"
            },
        )

        MainWindow._refresh_session_summary(window, "session-1")

        self.assertEqual(
            "종료: 실패 job-3 (1, 2) Reconnecting... 2/5 (request timed out)",
            window.session_widgets.activity_var.value,
        )
        self.assertEqual("", window.session_widgets.message_var.value)
        self.assertEqual(1, window.session_widgets.message_label.grid_remove_calls)

    def test_session_summary_merges_canceled_job_message_into_activity_line(self) -> None:
        canceled_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="canceled",
            status=JobStatus.CANCELED,
        )
        window = _SessionSelectionWindowStub(
            (canceled_job,),
            selected_job_id="job-3",
            job_user_messages={"job-3": "작업을 취소했습니다."},
        )

        MainWindow._refresh_session_summary(window, "session-1")

        self.assertEqual(
            "종료: 작업을 취소했습니다. job-3",
            window.session_widgets.activity_var.value,
        )
        self.assertEqual("", window.session_widgets.message_var.value)
        self.assertEqual(1, window.session_widgets.message_label.grid_remove_calls)

    def test_session_summary_shows_pending_activity_for_selected_queued_job(self) -> None:
        queued_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )
        window = _SessionSelectionWindowStub(
            (queued_job,),
            selected_job_id="job-1",
        )

        MainWindow._refresh_session_summary(window, "session-1")

        self.assertEqual("대기중: job-1", window.session_widgets.activity_var.value)
        self.assertEqual("", window.session_widgets.message_var.value)
        self.assertEqual(1, window.session_widgets.message_label.grid_remove_calls)

    def test_running_activity_lists_completed_job_numbers_only(self) -> None:
        completed_first = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done first",
            status=JobStatus.COMPLETED,
        )
        completed_second = Job(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done second",
            status=JobStatus.COMPLETED,
        )
        failed_job = Job(
            job_id="job-4",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="failed",
            status=JobStatus.FAILED,
        )
        queued_job = Job(
            job_id="job-5",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )
        completed_without_number = Job(
            job_id="manual-job",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="manual",
            status=JobStatus.COMPLETED,
        )
        running_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="running",
            status=JobStatus.RUNNING,
        )

        self.assertEqual(
            "실행 중: job-3 (1, 2)",
            _running_activity_text(
                running_job,
                (
                    completed_first,
                    completed_second,
                    failed_job,
                    queued_job,
                    completed_without_number,
                    running_job,
                ),
                language="ko",
            ),
        )

    def test_running_activity_omits_parentheses_without_completed_jobs(self) -> None:
        running_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        queued_job = Job(
            job_id="job-4",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )

        self.assertEqual(
            "실행 중: job-3",
            _running_activity_text(running_job, (queued_job, running_job), language="ko"),
        )

    def test_session_job_selection_prefers_running_job_over_selected_queued_job(self) -> None:
        running_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        queued_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )
        window = _SessionSelectionWindowStub(
            (running_job, queued_job),
            selected_job_id="job-2",
        )

        MainWindow._refresh_session_job_selection(window, "session-1")

        self.assertEqual("job-1", window.session_widgets.selected_job_id)

    def test_session_job_selection_uses_preferred_job_when_no_job_is_running(self) -> None:
        first_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="first",
            status=JobStatus.QUEUED,
        )
        second_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="second",
            status=JobStatus.QUEUED,
        )
        window = _SessionSelectionWindowStub(
            (first_job, second_job),
            selected_job_id="job-1",
        )

        MainWindow._refresh_session_job_selection(
            window,
            "session-1",
            preferred_job_id="job-2",
        )

        self.assertEqual("job-2", window.session_widgets.selected_job_id)

    def test_log_refresh_switches_to_appended_running_job(self) -> None:
        running_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        queued_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )
        window = _SessionSelectionWindowStub(
            (running_job, queued_job),
            selected_job_id="job-2",
            progress_logs={"job-1": ("세션 시작", "turn.started")},
        )

        MainWindow._refresh_session_output(
            window,
            "session-1",
            appended_job_id="job-1",
        )

        self.assertEqual("job-1", window.session_widgets.selected_job_id)
        self.assertEqual("세션 시작\nturn.started", window.session_widgets.log_text.content)


class MainWindowWorkspaceOpenTests(unittest.TestCase):
    def test_open_workspace_path_requests_background_open(self) -> None:
        runtime = _WorkspaceOpenRuntimeStub()
        window = _WorkspaceOpenWindowStub(runtime)

        MainWindow._open_workspace_path(window, r"C:\Repo\Alpha")

        self.assertEqual([r"C:\Repo\Alpha"], runtime.background_open_paths)
        self.assertEqual(["Alpha 열기 중"], window.status_messages)

    def test_open_startup_workspaces_schedules_background_open_requests(self) -> None:
        runtime = _WorkspaceOpenRuntimeStub()
        window = _StartupWorkspaceOpenWindowStub(runtime)

        MainWindow.open_startup_workspaces(
            window,
            (r"C:\Repo\Alpha", r"C:\Repo\Beta"),
        )

        self.assertEqual([], runtime.background_open_paths)
        self.assertEqual([0], window.after_intervals)

        window.run_scheduled_callbacks()

        self.assertEqual([r"C:\Repo\Alpha", r"C:\Repo\Beta"], runtime.background_open_paths)
        self.assertEqual(["Alpha 열기 중", "Beta 열기 중"], window.status_messages)

    def test_open_startup_workspaces_ignores_empty_paths(self) -> None:
        runtime = _WorkspaceOpenRuntimeStub()
        window = _StartupWorkspaceOpenWindowStub(runtime)

        MainWindow.open_startup_workspaces(window, ())

        self.assertEqual([], window.after_intervals)
        self.assertEqual([], runtime.background_open_paths)

    def test_saved_workspace_drop_requests_background_open_for_dropped_paths(self) -> None:
        runtime = _WorkspaceOpenRuntimeStub()
        window = _WorkspaceDropWindowStub(
            runtime,
            split_paths=(r"C:\Repo\Alpha", r"C:\Repo\Beta"),
        )

        action = MainWindow._on_saved_workspace_drop(
            window,
            _DropEvent(data=r"{C:\Repo\Alpha} {C:\Repo\Beta}"),
        )

        self.assertEqual("copy", action)
        self.assertEqual([r"C:\Repo\Alpha", r"C:\Repo\Beta"], runtime.background_open_paths)
        self.assertEqual(["워크스페이스 2개 등록 중"], window.status_messages)

    def test_saved_workspace_drop_reports_empty_drop_data(self) -> None:
        runtime = _WorkspaceOpenRuntimeStub()
        window = _WorkspaceDropWindowStub(runtime, split_paths=())

        action = MainWindow._on_saved_workspace_drop(window, _DropEvent(data=""))

        self.assertEqual("copy", action)
        self.assertEqual([], runtime.background_open_paths)
        self.assertEqual(["등록할 폴더를 찾을 수 없습니다."], window.status_messages)

    def test_delete_selected_saved_workspace_removes_entry_without_confirmation_when_not_running(
        self,
    ) -> None:
        runtime = _SavedWorkspaceDeleteRuntimeStub(
            _SavedWorkspaceStub(path=r"C:\Repo\Alpha", display_name="Alpha")
        )
        window = _SavedWorkspaceDeleteWindowStub(
            runtime,
            saved_workspace_paths=[r"C:\Repo\Alpha"],
            selection=(0,),
        )

        with patch("ui.main_window.messagebox.askyesno") as askyesno:
            MainWindow._delete_selected_saved_workspace(window)

        askyesno.assert_not_called()
        self.assertEqual([r"C:\Repo\Alpha"], runtime.running_checks)
        self.assertEqual([r"C:\Repo\Alpha"], runtime.deleted_paths)
        self.assertEqual(1, window.refresh_saved_workspace_list_calls)
        self.assertEqual(
            ["Alpha 저장 목록에서 제거됨"],
            window.status_messages,
        )

    def test_delete_selected_saved_workspace_prompts_when_workspace_is_running(self) -> None:
        runtime = _SavedWorkspaceDeleteRuntimeStub(
            _SavedWorkspaceStub(path=r"C:\Repo\Alpha", display_name="Alpha"),
            running_workspace_paths=(r"C:\Repo\Alpha",),
        )
        window = _SavedWorkspaceDeleteWindowStub(
            runtime,
            saved_workspace_paths=[r"C:\Repo\Alpha"],
            selection=(0,),
        )

        with patch("ui.main_window.messagebox.askyesno", return_value=True) as askyesno:
            MainWindow._delete_selected_saved_workspace(window)

        askyesno.assert_called_once_with(
            "워크스페이스 삭제",
            "Alpha 워크스페이스가 실행 중입니다.\n"
            "저장 목록에서 제거할까요?\n"
            "열린 탭과 실제 폴더는 유지됩니다.",
            parent=window,
        )
        self.assertEqual([r"C:\Repo\Alpha"], runtime.running_checks)
        self.assertEqual([r"C:\Repo\Alpha"], runtime.deleted_paths)
        self.assertEqual(1, window.refresh_saved_workspace_list_calls)
        self.assertEqual(
            ["Alpha 저장 목록에서 제거됨"],
            window.status_messages,
        )

    def test_delete_selected_saved_workspace_requires_selection(self) -> None:
        runtime = _SavedWorkspaceDeleteRuntimeStub(
            _SavedWorkspaceStub(path=r"C:\Repo\Alpha", display_name="Alpha")
        )
        window = _SavedWorkspaceDeleteWindowStub(
            runtime,
            saved_workspace_paths=[r"C:\Repo\Alpha"],
            selection=(),
        )

        with patch("ui.main_window.messagebox.askyesno") as askyesno:
            MainWindow._delete_selected_saved_workspace(window)

        askyesno.assert_not_called()
        self.assertEqual([], runtime.deleted_paths)
        self.assertEqual(["삭제할 워크스페이스를 선택하세요."], window.status_messages)


class MainWindowTabCloseTests(unittest.TestCase):
    def test_close_session_prompts_before_removing_pending_jobs(self) -> None:
        jobs = (
            Job(
                job_id="job-1",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                prompt="queued",
                status=JobStatus.QUEUED,
            ),
        )
        runtime = _TabCloseRuntimeStub(jobs)
        window = _TabCloseWindowStub(runtime)

        with patch("ui.main_window.messagebox.askyesno", return_value=True) as askyesno:
            MainWindow._close_session(window, "session-1")

        askyesno.assert_called_once_with(
            "세션 닫기",
            "대기 작업 1건 삭제 후 닫을까요?",
            parent=window,
        )
        self.assertEqual(["session-1"], runtime.closed_session_ids)
        self.assertEqual(["session-1"], window.removed_session_views)
        self.assertEqual(["workspace-1"], window.refreshed_workspace_ids)
        self.assertEqual(["대기 작업 1건 삭제 후 세션 닫힘"], window.status_messages)

    def test_close_session_stops_when_pending_job_removal_is_declined(self) -> None:
        jobs = (
            Job(
                job_id="job-1",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                prompt="waiting",
                status=JobStatus.WAITING_FOR_CONFIGURATION,
            ),
        )
        runtime = _TabCloseRuntimeStub(jobs)
        window = _TabCloseWindowStub(runtime)

        with patch("ui.main_window.messagebox.askyesno", return_value=False):
            MainWindow._close_session(window, "session-1")

        self.assertEqual([], runtime.closed_session_ids)
        self.assertEqual([], window.removed_session_views)
        self.assertEqual([], window.status_messages)

    def test_close_workspace_prompts_before_removing_pending_jobs(self) -> None:
        jobs = (
            Job(
                job_id="job-1",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                prompt="queued",
                status=JobStatus.QUEUED,
            ),
            Job(
                job_id="job-2",
                workspace_tab_id="workspace-1",
                session_tab_id="session-2",
                prompt="waiting",
                status=JobStatus.WAITING_FOR_CONFIGURATION,
            ),
        )
        runtime = _TabCloseRuntimeStub(jobs)
        window = _TabCloseWindowStub(runtime)

        with patch("ui.main_window.messagebox.askyesno", return_value=True) as askyesno:
            MainWindow._close_workspace(window, "workspace-1")

        askyesno.assert_called_once_with(
            "워크스페이스 닫기",
            "대기 작업 2건 삭제 후 닫을까요?",
            parent=window,
        )
        self.assertEqual(["workspace-1"], runtime.closed_workspace_ids)
        self.assertEqual(["workspace-1"], window.removed_workspace_views)
        self.assertEqual(["대기 작업 2건 삭제 후 워크스페이스 닫힘"], window.status_messages)

    def test_close_active_workspace_delegates_to_selected_workspace(self) -> None:
        window = _CloseActiveWorkspaceWindowStub(selected_tab="frame-1")

        MainWindow._close_active_workspace(window)

        self.assertEqual(["workspace-1"], window.closed_workspace_ids)
        self.assertEqual([], window.status_messages)

    def test_close_active_workspace_requires_selected_workspace(self) -> None:
        window = _CloseActiveWorkspaceWindowStub(selected_tab="")

        MainWindow._close_active_workspace(window)

        self.assertEqual([], window.closed_workspace_ids)
        self.assertEqual(["닫을 워크스페이스를 선택하세요."], window.status_messages)


class MainWindowWorkspaceTaskListTests(unittest.TestCase):
    def test_workspace_task_columns_shrink_with_base_width_ratio(self) -> None:
        self.assertEqual(
            (37, 35, 75, 150),
            _calculate_workspace_task_column_widths(297),
        )

    def test_job_context_menu_shows_delete_command_for_row(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="first line\nsecond line",
        )
        tree = _ContextMenuTreeStub(row_id="job-1")
        window = _ContextMenuWindowStub(tree, job)
        event = _ContextMenuEvent(y=12, x_root=100, y_root=200)

        with patch("ui.main_window.tk.Menu", _FakeContextMenu):
            result = MainWindow._show_job_context_menu(
                window,
                event,
                "workspace-1",
            )

        self.assertEqual("break", result)
        self.assertEqual(["job-1"], tree.selection_sets)
        self.assertEqual(["job-1"], tree.focus_sets)
        self.assertEqual([("workspace-1", "job-1")], window.selected_jobs)
        self.assertEqual(
            ["프롬프트: first line second line", "삭제"],
            window._job_context_menu.command_labels,
        )
        self.assertEqual(1, window._job_context_menu.separator_calls)
        self.assertEqual((100, 200), window._job_context_menu.popup_position)

        window._job_context_menu.commands[0]()
        window._job_context_menu.commands[1]()

        self.assertEqual(["job-1"], window.prompt_dialog_job_ids)
        self.assertEqual(["job-1"], window.deleted_job_ids)

    def test_show_job_prompt_dialog_opens_prompt_viewer(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="full\nprompt",
        )
        runtime = _JobLookupRuntimeStub(job)
        window = _PromptDialogWindowStub(runtime)

        with patch("ui.main_window.PromptViewerDialog") as dialog_cls:
            MainWindow._show_job_prompt_dialog(window, "job-1")

        dialog_cls.assert_called_once_with(
            window,
            job_id="job-1",
            prompt="full\nprompt",
            ui_language="ko",
        )
        dialog_cls.return_value.show_modal.assert_called_once_with()

    def test_workspace_task_list_does_not_show_job_id_column_value(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="visible prompt",
            queue_order=7,
        )
        tree = _TaskListTreeStub()
        window = _TaskListWindowStub((job,), tree)

        MainWindow._refresh_workspace_task_list(
            window,
            "workspace-1",
            preferred_job_id="job-1",
        )

        self.assertEqual(("7", "S1", "대기 중", "visible prompt"), tree.items["job-1"])
        self.assertEqual(["job-1"], tree.inserted_iids)
        self.assertEqual(["job-1"], tree.selection_sets)
        self.assertNotIn("job-1", tree.items["job-1"])

    def test_workspace_task_summary_counts_jobs_by_progress_state(self) -> None:
        jobs = (
            Job(
                job_id="job-1",
                workspace_tab_id="workspace-1",
                session_tab_id="session-1",
                prompt="done",
                status=JobStatus.COMPLETED,
            ),
            Job(
                job_id="job-2",
                workspace_tab_id="workspace-1",
                session_tab_id="session-2",
                prompt="running",
                status=JobStatus.RUNNING,
            ),
            Job(
                job_id="job-3",
                workspace_tab_id="workspace-1",
                session_tab_id="session-2",
                prompt="waiting",
                status=JobStatus.WAITING_FOR_CONFIGURATION,
            ),
        )

        self.assertEqual(
            "전체 3건 / 종료 1건 / 실행 중 1건 / 대기 0건 / 설정 필요 1건 / 실패 0건 / 취소 0건",
            _format_workspace_task_summary(jobs, language="ko"),
        )

    def test_completed_job_progress_text_uses_finished_label(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done",
            status=JobStatus.COMPLETED,
        )

        self.assertEqual("종료", _job_progress_text(job, language="ko"))

    def test_completed_job_progress_text_ignores_default_completed_message(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done",
            status=JobStatus.COMPLETED,
            user_message="작업 완료",
        )

        self.assertEqual("종료", _job_progress_text(job, language="ko"))

    def test_default_completed_message_is_merged_into_activity_line(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done",
            status=JobStatus.COMPLETED,
        )

        self.assertEqual(
            "",
            _session_job_message_text(job, "작업 완료", language="ko"),
        )

    def test_default_completed_message_remains_for_unlisted_completed_job(self) -> None:
        job = Job(
            job_id="manual-job",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="done",
            status=JobStatus.COMPLETED,
        )

        self.assertEqual(
            "작업 완료",
            _session_job_message_text(job, "작업 완료", language="ko"),
        )

    def test_job_progress_text_prefers_configuration_wait_reason(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="needs config",
            status=JobStatus.WAITING_FOR_CONFIGURATION,
            configuration_wait_reason="실행기 경로를 확인하세요.",
        )

        self.assertEqual(
            "실행기 경로를 확인하세요.",
            _job_progress_text(job, language="ko"),
        )

    def test_job_progress_text_localizes_runtime_messages_in_english(self) -> None:
        waiting_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="needs config",
            status=JobStatus.WAITING_FOR_CONFIGURATION,
            configuration_wait_reason="실행기 경로를 확인하세요.",
        )
        failed_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="failed",
            status=JobStatus.FAILED,
            user_message="실행 실패: 마지막 응답 JSON 이벤트를 확인하지 못했습니다.",
        )

        self.assertEqual(
            "Check the executable path.",
            _job_progress_text(waiting_job, language="en"),
        )
        self.assertEqual(
            "Execution failed: Could not find the final response JSON event.",
            _job_progress_text(failed_job, language="en"),
        )

    def test_delete_job_removes_non_running_job_and_refreshes_lists(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="delete me",
            status=JobStatus.QUEUED,
        )
        runtime = _JobDeleteRuntimeStub(job)
        window = _JobDeleteWindowStub(runtime)

        with patch("ui.main_window.messagebox.askyesno", return_value=True):
            MainWindow._delete_job(window, "job-1")

        self.assertEqual(["job-1"], runtime.deleted_job_ids)
        self.assertEqual(1, window.drain_runtime_events_calls)
        self.assertEqual(["session-1"], window.refreshed_session_ids)
        self.assertEqual(["workspace-1"], window.refreshed_workspace_ids)
        self.assertEqual(1, window.refresh_workspace_queue_summaries_calls)
        self.assertEqual(["job-1 삭제"], window.status_messages)

    def test_delete_job_rejects_running_job(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        runtime = _JobDeleteRuntimeStub(job)
        window = _JobDeleteWindowStub(runtime)

        with (
            patch("ui.main_window.messagebox.showinfo") as showinfo,
            patch("ui.main_window.messagebox.askyesno") as askyesno,
        ):
            MainWindow._delete_job(window, "job-1")

        self.assertEqual([], runtime.deleted_job_ids)
        askyesno.assert_not_called()
        showinfo.assert_called_once()


class MainWindowEventPollTests(unittest.TestCase):
    def test_schedule_event_poll_reschedules_after_background_event_exception(self) -> None:
        runtime = _PollingRuntimeStub(background_exception=RuntimeError("boom"))
        window = _PollingWindowStub(runtime)

        with patch("ui.main_window.LOGGER.exception") as logger_exception:
            MainWindow._schedule_event_poll(window)

        self.assertEqual([EVENT_POLL_INTERVAL_MS], window.after_intervals)
        self.assertEqual("after-1", window._after_id)
        self.assertEqual(1, runtime.process_background_events_calls)
        self.assertEqual(0, window.drain_runtime_events_calls)
        logger_exception.assert_called_once_with("Failed while polling runtime events.")

    def test_schedule_event_poll_reschedules_after_runtime_drain_exception(self) -> None:
        runtime = _PollingRuntimeStub()
        window = _PollingWindowStub(runtime, drain_exception=RuntimeError("boom"))

        with patch("ui.main_window.LOGGER.exception") as logger_exception:
            MainWindow._schedule_event_poll(window)

        self.assertEqual([EVENT_POLL_INTERVAL_MS], window.after_intervals)
        self.assertEqual("after-1", window._after_id)
        self.assertEqual(1, runtime.process_background_events_calls)
        self.assertEqual(1, window.drain_runtime_events_calls)
        logger_exception.assert_called_once_with("Failed while polling runtime events.")


class MainWindowShutdownTests(unittest.TestCase):
    def test_continue_close_retries_after_shutdown_wait_exception(self) -> None:
        cases = (
            (
                "background event",
                _PollingRuntimeStub(background_exception=RuntimeError("boom")),
                None,
            ),
            (
                "runtime drain",
                _PollingRuntimeStub(),
                RuntimeError("boom"),
            ),
            (
                "pending check",
                _PollingRuntimeStub(pending_exception=RuntimeError("boom")),
                None,
            ),
        )

        for case_name, runtime, drain_exception in cases:
            with self.subTest(case_name=case_name):
                window = _ShutdownWindowStub(runtime, drain_exception=drain_exception)

                with patch("ui.main_window.LOGGER.exception") as logger_exception:
                    MainWindow._continue_close(window)

                self.assertEqual(0, window.finalize_close_calls)
                self.assertEqual([EVENT_POLL_INTERVAL_MS], window.after_intervals)
                self.assertEqual("after-1", window._shutdown_after_id)
                self.assertEqual(
                    ["종료 상태 확인에 실패했습니다. 다시 시도합니다."],
                    window.status_messages,
                )
                logger_exception.assert_called_once_with(
                    "Failed while waiting for runtime shutdown."
                )


def _write_prompt_pair(root: Path, *, language: str, instruction: str) -> None:
    prompt_dir = root / "prompt" / language
    prompt_dir.mkdir(parents=True)
    (prompt_dir / f"{instruction}.md").write_text("analysis prompt", encoding="utf-8")
    (prompt_dir / f"{instruction}_work.md").write_text(
        "work prompt {{candidates_payload}}",
        encoding="utf-8",
    )


def _walk_widgets(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from _walk_widgets(child)


def _find_widgets_by_text(widget: tk.Misc, text: str) -> list[tk.Misc]:
    return [child for child in _walk_widgets(widget) if _widget_text(child) == text]


def _widget_text(widget: tk.Misc) -> str:
    try:
        return str(widget.cget("text"))
    except tk.TclError:
        return ""


def _is_tk_display_unavailable(error: tk.TclError) -> bool:
    message = str(error).casefold()
    return (
        "no display" in message
        or "couldn't connect to display" in message
        or "cannot open display" in message
        or "can't find a usable init.tcl" in message
        or "can't find a usable tk.tcl" in message
        or "tcl wasn't installed properly" in message
        or "tk wasn't installed properly" in message
    )


def _create_tk_root_or_skip(test_case: unittest.TestCase) -> tk.Tk:
    try:
        root = tk.Tk()
    except tk.TclError as error:
        if _is_tk_display_unavailable(error):
            test_case.skipTest(f"Tk display is unavailable: {error}")
        raise
    root.withdraw()
    return root


def _destroy_dialog_and_root(
    dialog: tk.Toplevel | None,
    root: tk.Tk | None,
) -> None:
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


def _close_tk_window(window: MainWindow) -> None:
    try:
        window.close()
        for _ in range(100):
            try:
                window.update()
                if not window.winfo_exists():
                    return
            except tk.TclError:
                return
            time.sleep(0.01)
    finally:
        try:
            if window.winfo_exists():
                window.destroy()
        except tk.TclError:
            pass


def _shutdown_runtime(runtime: object) -> None:
    runtime.shutdown()
    for _ in range(100):
        runtime.process_background_events(max_items=32)
        if not runtime.has_pending_background_work():
            return
        time.sleep(0.01)


@dataclass(slots=True, frozen=True)
class _ContextMenuEvent:
    y: int
    x_root: int
    y_root: int


class _KoreanUiLanguageStub:
    _ui_language = "ko"


class _ContextMenuTreeStub:
    def __init__(self, *, row_id: str) -> None:
        self._row_id = row_id
        self.selection_sets: list[str] = []
        self.focus_sets: list[str] = []

    def identify_row(self, y: int) -> str:
        del y
        return self._row_id

    def selection_set(self, job_id: str) -> None:
        self.selection_sets.append(job_id)

    def focus(self, job_id: str) -> None:
        self.focus_sets.append(job_id)


class _FakeContextMenu:
    def __init__(self, parent: object, *, tearoff: bool) -> None:
        del parent, tearoff
        self.command_labels: list[str] = []
        self.commands: list[object] = []
        self.separator_calls = 0
        self.popup_position: tuple[int, int] | None = None
        self.grab_release_calls = 0

    def add_command(self, *, label: str, command: object) -> None:
        self.command_labels.append(label)
        self.commands.append(command)

    def add_separator(self) -> None:
        self.separator_calls += 1

    def tk_popup(self, x_root: int, y_root: int) -> None:
        self.popup_position = (x_root, y_root)

    def grab_release(self) -> None:
        self.grab_release_calls += 1


@dataclass(slots=True)
class _ContextMenuWorkspaceViewStub:
    workspace_jobs_tree: _ContextMenuTreeStub


class _ContextMenuWindowStub(_KoreanUiLanguageStub):
    def __init__(self, tree: _ContextMenuTreeStub, job: Job) -> None:
        self._runtime = _JobLookupRuntimeStub(job)
        self._job_context_menu = None
        self._workspace_views = {"workspace-1": _ContextMenuWorkspaceViewStub(tree)}
        self.selected_jobs: list[tuple[str, str]] = []
        self.prompt_dialog_job_ids: list[str] = []
        self.deleted_job_ids: list[str] = []
        self.status_messages: list[str] = []

    def _select_workspace_job(self, workspace_tab_id: str, job_id: str) -> None:
        self.selected_jobs.append((workspace_tab_id, job_id))

    def _show_job_prompt_dialog(self, job_id: str) -> None:
        self.prompt_dialog_job_ids.append(job_id)

    def _delete_job(self, job_id: str) -> None:
        self.deleted_job_ids.append(job_id)

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _TaskListTreeStub:
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, ...]] = {}
        self.inserted_iids: list[str] = []
        self.deleted_iids: list[str] = []
        self.moves: list[tuple[str, str, int]] = []
        self.selection_sets: list[str] = []
        self.focus_sets: list[str] = []
        self.selection_remove_calls: list[tuple[str, ...]] = []
        self._selection: tuple[str, ...] = ()

    def get_children(self) -> tuple[str, ...]:
        return tuple(self.items)

    def selection(self) -> tuple[str, ...]:
        return self._selection

    def delete(self, iid: str) -> None:
        self.deleted_iids.append(iid)
        self.items.pop(iid, None)

    def exists(self, iid: str) -> bool:
        return iid in self.items

    def item(self, iid: str, *, values: tuple[str, ...]) -> None:
        self.items[iid] = tuple(values)

    def move(self, iid: str, parent: str, index: int) -> None:
        self.moves.append((iid, parent, index))

    def insert(self, parent: str, index: str, *, iid: str, values: tuple[str, ...]) -> None:
        del parent, index
        self.items[iid] = tuple(values)
        self.inserted_iids.append(iid)

    def selection_remove(self, selection: tuple[str, ...]) -> None:
        self.selection_remove_calls.append(selection)
        self._selection = ()

    def selection_set(self, iid: str) -> None:
        self.selection_sets.append(iid)
        self._selection = (iid,)

    def focus(self, iid: str) -> None:
        self.focus_sets.append(iid)


class _StringVarStub:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _StatusLocalizationWindowStub:
    def __init__(self, settings: AppSettings) -> None:
        self._runtime = _SettingsRuntimeStub(settings)
        self._status_message_var = _StringVarStub()


class _SettingsRuntimeStub:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings


class _LabelVisibilityStub:
    def __init__(self) -> None:
        self.grid_calls = 0
        self.grid_remove_calls = 0

    def grid(self) -> None:
        self.grid_calls += 1

    def grid_remove(self) -> None:
        self.grid_remove_calls += 1


@dataclass(slots=True, frozen=True)
class _SessionIdCopySessionTabStub:
    session_id: str | None


class _SessionIdCopyRuntimeStub:
    def __init__(self, *, session_id: str | None) -> None:
        self._session_id = session_id

    def get_session_tab(self, session_tab_id: str) -> _SessionIdCopySessionTabStub:
        del session_tab_id
        return _SessionIdCopySessionTabStub(session_id=self._session_id)


class _SessionIdCopyWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _SessionIdCopyRuntimeStub) -> None:
        self._runtime = runtime
        self.clipboard_text = ""
        self.status_messages: list[str] = []

    def clipboard_clear(self) -> None:
        self.clipboard_text = ""

    def clipboard_append(self, value: str) -> None:
        self.clipboard_text += value

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _IdentityUiScaleStub:
    def px(self, value: int | float) -> int:
        return int(value)

    def padding(self, *values: int | float) -> int | tuple[int, ...]:
        scaled = tuple(int(value) for value in values)
        if len(scaled) == 1:
            return scaled[0]
        return scaled


class _ConfigurableWidgetStub:
    def __init__(self) -> None:
        self.width: int | None = None
        self.configured_options: list[dict[str, object]] = []

    def configure(self, **options: object) -> None:
        self.configured_options.append(options)
        if "width" in options:
            self.width = int(options["width"])


class _GridWidgetStub(_ConfigurableWidgetStub):
    def __init__(self) -> None:
        super().__init__()
        self.grid_calls = 0
        self.grid_remove_calls = 0

    def grid(self) -> None:
        self.grid_calls += 1

    def grid_remove(self) -> None:
        self.grid_remove_calls += 1


class _PanedWindowStub:
    def __init__(self, sash_position: int = SIDEBAR_INITIAL_WIDTH) -> None:
        self.sash_position = sash_position
        self.destroy_calls = 0
        self.width = DEFAULT_WINDOW_WIDTH

    def sashpos(self, index: int, position: int | None = None) -> int | None:
        self.sashpos_index = index
        if position is None:
            return self.sash_position
        self.sash_position = position
        return None

    def winfo_width(self) -> int:
        return self.width

    def destroy(self) -> None:
        self.destroy_calls += 1


class _SidebarCollapseWindowStub:
    _remember_sidebar_restore_width = MainWindow._remember_sidebar_restore_width
    _apply_sidebar_layout = MainWindow._apply_sidebar_layout
    _position_sidebar_sash = MainWindow._position_sidebar_sash
    _refresh_sidebar_restore_button = MainWindow._refresh_sidebar_restore_button
    _is_sidebar_sash_hidden = MainWindow._is_sidebar_sash_hidden
    _expanded_sidebar_width = MainWindow._expanded_sidebar_width

    def __init__(self, *, sash_position: int = SIDEBAR_INITIAL_WIDTH) -> None:
        self._ui_scale = _IdentityUiScaleStub()
        self._main_splitter = _PanedWindowStub(sash_position)
        self._sidebar = _ConfigurableWidgetStub()
        self._sidebar_content = _GridWidgetStub()
        self._sidebar_toggle_button = _ButtonConfigureStub()
        self._sidebar_restore_button = _ButtonConfigureStub()
        self._sidebar_collapsed = False
        self._sidebar_restore_width = SIDEBAR_INITIAL_WIDTH


class _SidebarRebuildWindowStub:
    _refresh_sidebar_restore_button = MainWindow._refresh_sidebar_restore_button
    _is_sidebar_sash_hidden = MainWindow._is_sidebar_sash_hidden
    _position_sidebar_sash = MainWindow._position_sidebar_sash
    _expanded_sidebar_width = MainWindow._expanded_sidebar_width

    def __init__(self) -> None:
        self._ui_scale = _IdentityUiScaleStub()
        self._workspace_views: dict[str, object] = {}
        self._workspace_frame_map: dict[str, str] = {}
        self._session_frame_map: dict[str, tuple[str, str]] = {}
        self._preset_language_request_ids: dict[str, int] = {}
        self._preset_instruction_request_ids: dict[str, int] = {}
        self._job_context_menu = object()
        self._main_splitter = _PanedWindowStub()
        self._sidebar = _ConfigurableWidgetStub()
        self._sidebar_content = _GridWidgetStub()
        self._sidebar_toggle_button = _ButtonConfigureStub()
        self._sidebar_restore_button = _ButtonConfigureStub()
        self._sidebar_collapsed = True
        self._sidebar_restore_width = 236
        self._main_area = object()
        self._status_bar = object()
        self._settings_summary_label = object()
        self._scheduled_run_button = object()
        self._scheduled_run_status_label = object()
        self._saved_workspace_paths = ["workspace"]
        self.build_widgets_calls = 0
        self.refresh_saved_workspace_list_calls = 0
        self.refresh_scheduled_run_display_calls = 0
        self.refresh_settings_summary_calls = 0
        self.rebuild_workspace_tabs_calls = 0

    def _build_widgets(self) -> None:
        self.build_widgets_calls += 1
        self._main_splitter = _PanedWindowStub()
        self._sidebar = _ConfigurableWidgetStub()
        self._sidebar_content = _GridWidgetStub()
        self._sidebar_toggle_button = _ButtonConfigureStub()
        self._sidebar_restore_button = _ButtonConfigureStub()
        MainWindow._apply_sidebar_layout(self)

    def _refresh_saved_workspace_list(self) -> None:
        self.refresh_saved_workspace_list_calls += 1

    def _refresh_scheduled_run_display(self) -> None:
        self.refresh_scheduled_run_display_calls += 1

    def _refresh_settings_summary(self) -> None:
        self.refresh_settings_summary_calls += 1

    def _rebuild_workspace_tabs(self) -> None:
        self.rebuild_workspace_tabs_calls += 1


class _ButtonConfigureStub:
    def __init__(self) -> None:
        self.text = ""
        self.state = ""
        self.is_gridded = False
        self.grid_calls = 0
        self.grid_remove_calls = 0
        self.lift_calls = 0
        self.configured_options: list[dict[str, object]] = []

    def configure(self, **options: object) -> None:
        self.configured_options.append(options)
        if "text" in options:
            self.text = str(options["text"])
        if "state" in options:
            self.state = str(options["state"])

    def grid(self) -> None:
        self.is_gridded = True
        self.grid_calls += 1

    def grid_remove(self) -> None:
        self.is_gridded = False
        self.grid_remove_calls += 1

    def lift(self) -> None:
        self.lift_calls += 1


class _ComboboxConfigureStub:
    def __init__(self, values: tuple[str, ...]) -> None:
        self._values = values
        self.state = ""
        self.configured_options: list[dict[str, object]] = []

    def cget(self, option: str) -> object:
        if option == "values":
            return self._values
        if option == "state":
            return self.state
        raise KeyError(option)

    def configure(self, **options: object) -> None:
        self.configured_options.append(options)
        if "values" in options:
            values = options["values"]
            self._values = tuple(values) if isinstance(values, (tuple, list)) else tuple()
        if "state" in options:
            self.state = str(options["state"])


@dataclass(slots=True)
class _ExecutionOptionSessionWidgetsStub:
    agent_provider_var: _StringVarStub = field(default_factory=_StringVarStub)
    model_var: _StringVarStub = field(default_factory=_StringVarStub)
    reasoning_var: _StringVarStub = field(default_factory=_StringVarStub)
    agent_provider_combobox: _ComboboxConfigureStub = field(
        default_factory=lambda: _ComboboxConfigureStub(())
    )
    model_combobox: _ComboboxConfigureStub = field(
        default_factory=lambda: _ComboboxConfigureStub(())
    )
    reasoning_combobox: _ComboboxConfigureStub = field(
        default_factory=lambda: _ComboboxConfigureStub(())
    )
    preset_action_agent_provider_combobox: _ComboboxConfigureStub | None = None
    preset_action_model_combobox: _ComboboxConfigureStub | None = None
    preset_action_reasoning_combobox: _ComboboxConfigureStub | None = None
    preset_language_combobox: _ComboboxConfigureStub | None = None
    preset_instruction_combobox: _ComboboxConfigureStub | None = None
    preset_work_priority_combobox: _ComboboxConfigureStub | None = None
    preset_prompt_prefix_text: _SubmitPromptTextStub | None = None
    preset_auto_commit_checkbutton: _ButtonConfigureStub | None = None
    preset_register_button: _ButtonConfigureStub | None = None
    agent_provider_options: tuple[object, ...] = ()
    model_options: tuple[object, ...] = ()
    reasoning_options: tuple[object, ...] = ()
    preset_action_agent_provider_var: _StringVarStub | None = field(
        default_factory=_StringVarStub
    )
    preset_action_model_var: _StringVarStub | None = field(default_factory=_StringVarStub)
    preset_action_reasoning_var: _StringVarStub | None = field(
        default_factory=_StringVarStub
    )
    preset_action_agent_provider_options: tuple[object, ...] = ()
    preset_action_model_options: tuple[object, ...] = ()
    preset_action_reasoning_options: tuple[object, ...] = ()
    preset_action_execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )
    execution_controls: ExecutionOptionControls = field(init=False)
    preset_action_execution_controls: ExecutionOptionControls | None = field(
        init=False,
    )

    def __post_init__(self) -> None:
        self.execution_controls = ExecutionOptionControls(
            agent_provider_var=self.agent_provider_var,
            model_var=self.model_var,
            reasoning_var=self.reasoning_var,
            agent_provider_combobox=self.agent_provider_combobox,
            model_combobox=self.model_combobox,
            reasoning_combobox=self.reasoning_combobox,
        )
        if (
            self.preset_action_agent_provider_combobox is None
            or self.preset_action_model_combobox is None
            or self.preset_action_reasoning_combobox is None
            or self.preset_action_agent_provider_var is None
            or self.preset_action_model_var is None
            or self.preset_action_reasoning_var is None
        ):
            self.preset_action_execution_controls = None
            return
        self.preset_action_execution_controls = ExecutionOptionControls(
            agent_provider_var=self.preset_action_agent_provider_var,
            model_var=self.preset_action_model_var,
            reasoning_var=self.preset_action_reasoning_var,
            agent_provider_combobox=self.preset_action_agent_provider_combobox,
            model_combobox=self.preset_action_model_combobox,
            reasoning_combobox=self.preset_action_reasoning_combobox,
            execution_options=self.preset_action_execution_options,
        )


class _ExecutionOptionRuntimeStub:
    def __init__(self, *, settings: AppSettings, session_tab: SessionTab) -> None:
        self.settings = settings
        self.session_tab = session_tab
        self.updated_execution_options: list[AgentExecutionOptions] = []

    def get_session_tab(self, session_tab_id: str) -> SessionTab:
        if session_tab_id != self.session_tab.session_tab_id:
            raise KeyError(session_tab_id)
        return self.session_tab

    def set_session_execution_options(
        self,
        session_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> SessionTab:
        if session_tab_id != self.session_tab.session_tab_id:
            raise KeyError(session_tab_id)
        self.updated_execution_options.append(execution_options)
        if not self.session_tab.execution_options_locked:
            self.session_tab = replace(
                self.session_tab,
                execution_options=execution_options,
            )
        return self.session_tab


class _ExecutionOptionWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _ExecutionOptionRuntimeStub,
        widgets: _ExecutionOptionSessionWidgetsStub,
        *,
        pending_registration_session_ids: set[str] | None = None,
    ) -> None:
        self._runtime = runtime
        self._widgets = widgets
        self._preset_registration_pending_session_ids = (
            pending_registration_session_ids or set()
        )

    def _has_session_view(self, session_tab_id: str) -> bool:
        del session_tab_id
        return True

    def _get_session_widgets(
        self,
        session_tab_id: str,
    ) -> _ExecutionOptionSessionWidgetsStub:
        del session_tab_id
        return self._widgets

    def _refresh_session_execution_option_controls(self, session_tab_id: str) -> None:
        MainWindow._refresh_session_execution_option_controls(self, session_tab_id)

    def _refresh_preset_action_execution_option_controls(
        self,
        session_tab_id: str,
    ) -> None:
        MainWindow._refresh_preset_action_execution_option_controls(
            self,
            session_tab_id,
        )

    def _remember_preset_action_execution_options_for_session(
        self,
        session_tab_id: str,
    ) -> None:
        MainWindow._remember_preset_action_execution_options_for_session(
            self,
            session_tab_id,
        )

    def _resolve_execution_option_control_values(
        self,
        execution_options: AgentExecutionOptions,
        *,
        locked: bool,
    ):
        return MainWindow._resolve_execution_option_control_values(
            self,
            execution_options,
            locked=locked,
        )

    def _apply_execution_option_control_values(self, **kwargs: object) -> None:
        MainWindow._apply_execution_option_control_values(**kwargs)

    def _set_execution_option_combobox_states(self, **kwargs: object) -> None:
        MainWindow._set_execution_option_combobox_states(**kwargs)

    def _selected_execution_options(self, **kwargs: object):
        return MainWindow._selected_execution_options(self, **kwargs)

    def _selected_execution_options_from_controls(
        self,
        controls: ExecutionOptionControls,
        *,
        include_model: bool,
        include_reasoning: bool,
    ):
        return MainWindow._selected_execution_options_from_controls(
            self,
            controls,
            include_model=include_model,
            include_reasoning=include_reasoning,
        )

    def _selected_option_value(
        self,
        options: tuple[object, ...],
        selected_label: str,
    ) -> str:
        return MainWindow._selected_option_value(options, selected_label)

    def _option_value_or_default(
        self,
        options: tuple[object, ...],
        value: str,
    ) -> str:
        return MainWindow._option_value_or_default(options, value)

    def _set_preset_action_execution_option_controls_enabled(
        self,
        session_widgets: _ExecutionOptionSessionWidgetsStub,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_action_execution_option_controls_enabled(
            self,
            session_widgets,
            enabled=enabled,
        )

    def _agent_provider_option_for_value(self, provider_value: str):
        return MainWindow._agent_provider_option_for_value(provider_value)


class _PresetSubmissionEventWindowStub(_ExecutionOptionWindowStub):
    def __init__(
        self,
        runtime: _ExecutionOptionRuntimeStub,
        widgets: _ExecutionOptionSessionWidgetsStub,
        *,
        pending_registration_session_ids: set[str] | None = None,
    ) -> None:
        super().__init__(
            runtime,
            widgets,
            pending_registration_session_ids=pending_registration_session_ids,
        )
        self.preset_registration_refreshes: list[str] = []
        self.remembered_prompt_prefixes: list[tuple[str, str]] = []

    def _refresh_preset_registration_controls(self, session_tab_id: str) -> None:
        self.preset_registration_refreshes.append(session_tab_id)

    def _set_preset_registration_controls_enabled(
        self,
        session_widgets: _ExecutionOptionSessionWidgetsStub,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_registration_controls_enabled(
            self,
            session_widgets,
            enabled=enabled,
        )

    def _set_preset_combobox_enabled(
        self,
        combobox: _ComboboxConfigureStub | None,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_combobox_enabled(combobox, enabled=enabled)

    def _remember_preset_prompt_prefix_for_workspace(
        self,
        workspace_tab_id: str,
        prompt_prefix: str,
    ) -> None:
        self.remembered_prompt_prefixes.append((workspace_tab_id, prompt_prefix))


@dataclass(slots=True)
class _WorkspaceQueueSummaryViewStub:
    queue_var: _StringVarStub
    queue_toggle_var: _BoolVarStub
    queue_toggle_button: _ButtonConfigureStub


class _WorkspaceQueueSummaryRuntimeStub:
    def __init__(
        self,
        jobs: tuple[Job, ...],
        *,
        queue_status: QueueStatus = QueueStatus.STARTED,
        last_stop_reason: QueueStopReason | str | None = None,
    ) -> None:
        self._jobs = jobs
        self._queue_status = queue_status
        self._last_stop_reason = last_stop_reason
        self.list_workspace_jobs_requests: list[str] = []
        self.list_jobs_by_workspace_requests: list[tuple[str, ...]] = []
        self.summarize_workspace_jobs_requests: list[tuple[str, ...]] = []

    def get_queue_state(self, workspace_tab_id: str) -> WorkspaceQueueState:
        return WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id,
            status=self._queue_status,
            last_stop_reason=self._last_stop_reason,
        )

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        self.list_workspace_jobs_requests.append(workspace_tab_id)
        return tuple(job for job in self._jobs if job.workspace_tab_id == workspace_tab_id)

    def list_jobs_by_workspace(
        self,
        workspace_tab_ids: tuple[str, ...],
    ) -> dict[str, tuple[Job, ...]]:
        self.list_jobs_by_workspace_requests.append(workspace_tab_ids)
        return {
            workspace_tab_id: tuple(
                job for job in self._jobs if job.workspace_tab_id == workspace_tab_id
            )
            for workspace_tab_id in workspace_tab_ids
        }

    def summarize_workspace_jobs(
        self,
        workspace_tab_ids: tuple[str, ...],
    ) -> dict[str, WorkspaceJobSummary]:
        self.summarize_workspace_jobs_requests.append(workspace_tab_ids)
        return {
            workspace_tab_id: WorkspaceJobSummary(
                has_jobs=any(
                    job.workspace_tab_id == workspace_tab_id for job in self._jobs
                ),
                has_runnable_jobs=any(
                    job.workspace_tab_id == workspace_tab_id
                    and job.status == JobStatus.QUEUED
                    for job in self._jobs
                ),
                has_running_job=any(
                    job.workspace_tab_id == workspace_tab_id
                    and job.status == JobStatus.RUNNING
                    for job in self._jobs
                ),
            )
            for workspace_tab_id in workspace_tab_ids
        }


class _WorkspaceQueueSummaryWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _WorkspaceQueueSummaryRuntimeStub) -> None:
        self._runtime = runtime
        self._queue_start_pending_workspace_ids: set[str] = set()
        self.workspace_view = _WorkspaceQueueSummaryViewStub(
            queue_var=_StringVarStub(),
            queue_toggle_var=_BoolVarStub(False),
            queue_toggle_button=_ButtonConfigureStub(),
        )
        self._workspace_views = {"workspace-1": self.workspace_view}
        self.indicator_calls: list[tuple[str, bool]] = []

    def _format_queue_label(self, queue_state: WorkspaceQueueState) -> str:
        return MainWindow._format_queue_label(self, queue_state)

    def _queue_start_is_pending(self, workspace_tab_id: str) -> bool:
        return MainWindow._queue_start_is_pending(self, workspace_tab_id)

    def _set_queue_toggle_state(
        self,
        workspace_view: object,
        *,
        active: bool,
        enabled: bool = True,
    ) -> None:
        MainWindow._set_queue_toggle_state(self, workspace_view, active=active, enabled=enabled)

    def _workspace_has_running_job(self, workspace_tab_id: str) -> bool:
        return MainWindow._workspace_has_running_job(self, workspace_tab_id)

    def _refresh_workspace_tab_indicator(self, workspace_tab_id: str, *, running: bool) -> None:
        self.indicator_calls.append((workspace_tab_id, running))


@dataclass(slots=True)
class _TaskListWorkspaceViewStub:
    workspace_jobs_tree: _TaskListTreeStub
    workspace_jobs_summary_var: _StringVarStub


class _TaskListRuntimeStub:
    def __init__(self, jobs: tuple[Job, ...]) -> None:
        self._jobs = jobs

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        del workspace_tab_id
        return self._jobs


class _TaskListWindowStub(_KoreanUiLanguageStub):
    def __init__(self, jobs: tuple[Job, ...], tree: _TaskListTreeStub) -> None:
        self._runtime = _TaskListRuntimeStub(jobs)
        self._workspace_views = {
            "workspace-1": _TaskListWorkspaceViewStub(
                workspace_jobs_tree=tree,
                workspace_jobs_summary_var=_StringVarStub(),
            )
        }

    def _job_session_label(self, job: Job) -> str:
        del job
        return "S1"


class _JobLookupRuntimeStub:
    def __init__(self, job: Job) -> None:
        self._job = job

    def get_job(self, job_id: str) -> Job:
        if job_id != self._job.job_id:
            raise KeyError(job_id)
        return self._job


class _PromptDialogWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _JobLookupRuntimeStub) -> None:
        self._runtime = runtime
        self.status_messages: list[str] = []

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _JobDeleteRuntimeStub:
    def __init__(self, job: Job) -> None:
        self._job = job
        self.deleted_job_ids: list[str] = []

    def get_job(self, job_id: str) -> Job:
        if job_id != self._job.job_id:
            raise KeyError(job_id)
        return self._job

    def delete_job(self, job_id: str) -> Job:
        if job_id != self._job.job_id:
            raise KeyError(job_id)
        self.deleted_job_ids.append(job_id)
        return self._job


class _JobDeleteWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _JobDeleteRuntimeStub) -> None:
        self._runtime = runtime
        self.drain_runtime_events_calls = 0
        self.refreshed_session_ids: list[str] = []
        self.refreshed_workspace_ids: list[str] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _has_session_view(self, session_tab_id: str) -> bool:
        del session_tab_id
        return True

    def _refresh_session_view(self, session_tab_id: str) -> None:
        self.refreshed_session_ids.append(session_tab_id)

    def _refresh_workspace_task_list(self, workspace_tab_id: str) -> None:
        self.refreshed_workspace_ids.append(workspace_tab_id)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _RuntimeStub:
    def __init__(self, *, settings: AppSettings, update_result: SettingsUpdateResult) -> None:
        self.settings = settings
        self._update_result = update_result
        self.updated_settings: list[AppSettings] = []

    def update_settings(self, settings: AppSettings) -> SettingsUpdateResult:
        self.updated_settings.append(settings)
        self.settings = settings
        return self._update_result


class _MainWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _RuntimeStub) -> None:
        self._runtime = runtime
        self.drain_runtime_events_calls = 0
        self.refresh_settings_summary_calls = 0
        self.refresh_workspace_queue_summaries_calls = 0
        self.apply_output_font_to_all_sessions_calls = 0
        self.refresh_all_session_execution_option_controls_calls = 0
        self.refresh_session_outputs_for_all_sessions_calls = 0
        self.rebuild_static_ui_calls = 0
        self.status_messages: list[str] = []

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _refresh_settings_summary(self) -> None:
        self.refresh_settings_summary_calls += 1

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _apply_output_font_to_all_sessions(self) -> None:
        self.apply_output_font_to_all_sessions_calls += 1

    def _refresh_all_session_execution_option_controls(self) -> None:
        self.refresh_all_session_execution_option_controls_calls += 1

    def _refresh_session_outputs_for_all_sessions(self) -> None:
        self.refresh_session_outputs_for_all_sessions_calls += 1

    def _rebuild_static_ui(self) -> None:
        self.rebuild_static_ui_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _RuntimeUiUpdateRuntimeStub:
    def __init__(self) -> None:
        self.list_jobs_by_workspace_requests: list[tuple[str, ...]] = []

    def list_jobs_by_workspace(
        self,
        workspace_tab_ids: tuple[str, ...],
    ) -> dict[str, tuple[Job, ...]]:
        self.list_jobs_by_workspace_requests.append(workspace_tab_ids)
        return {workspace_tab_id: () for workspace_tab_id in workspace_tab_ids}


class _RuntimeUiUpdateWindowStub(_KoreanUiLanguageStub):
    _queue_full_session_view_refresh = MainWindow._queue_full_session_view_refresh

    def __init__(self) -> None:
        self._runtime = _RuntimeUiUpdateRuntimeStub()
        self._workspace_views = {
            "workspace-1": object(),
            "workspace-2": object(),
        }
        self.synced_workspace_ids: list[str] = []
        self.refreshed_session_ids: list[str] = []
        self.refreshed_workspace_ids: list[str] = []
        self.refreshed_queue_summary_workspace_ids: list[tuple[str, ...] | None] = []

    def _sync_session_tab_order(self, workspace_tab_id: str) -> None:
        self.synced_workspace_ids.append(workspace_tab_id)

    def _has_session_view(self, session_tab_id: str) -> bool:
        del session_tab_id
        return True

    def _refresh_session_view(self, session_tab_id: str) -> None:
        self.refreshed_session_ids.append(session_tab_id)

    def _refresh_workspace_task_list(
        self,
        workspace_tab_id: str,
        *,
        jobs: tuple[Job, ...] = (),
    ) -> None:
        del jobs
        self.refreshed_workspace_ids.append(workspace_tab_id)

    def _refresh_workspace_queue_summaries(
        self,
        workspace_tab_ids: object = None,
    ) -> None:
        if workspace_tab_ids is None:
            self.refreshed_queue_summary_workspace_ids.append(None)
            return
        self.refreshed_queue_summary_workspace_ids.append(tuple(workspace_tab_ids))


@dataclass(slots=True)
class _HistoryTurnStub:
    started_at: datetime
    completed_at: datetime | None
    prompt_text: str
    response_text: str | None


class _SessionHistoryWindowStub:
    _render_session_history_turns = MainWindow._render_session_history_turns
    _render_session_history_turn = MainWindow._render_session_history_turn
    _format_session_history_turn = MainWindow._format_session_history_turn
    _join_session_history_blocks = MainWindow._join_session_history_blocks
    _session_history_prefix_length = MainWindow._session_history_prefix_length


def _history_dt(minute: int) -> datetime:
    return datetime(2025, 1, 1, 0, minute, tzinfo=timezone.utc)


class _PollingRuntimeStub:
    def __init__(
        self,
        *,
        background_exception: Exception | None = None,
        pending_exception: Exception | None = None,
    ) -> None:
        self._background_exception = background_exception
        self._pending_exception = pending_exception
        self.process_background_events_calls = 0
        self.has_pending_background_work_calls = 0

    def process_background_events(self, *, max_items: int | None = None) -> int:
        del max_items
        self.process_background_events_calls += 1
        if self._background_exception is not None:
            raise self._background_exception
        return 0

    def has_pending_background_work(self) -> bool:
        self.has_pending_background_work_calls += 1
        if self._pending_exception is not None:
            raise self._pending_exception
        return False


@dataclass(slots=True, frozen=True)
class _WorkspaceTabStub:
    display_name: str


@dataclass(slots=True, frozen=True)
class _CreatedSessionTabStub:
    session_tab_id: str
    display_name: str


class _CreatePresetSessionRuntimeStub:
    def __init__(self) -> None:
        self.open_preset_session_workspace_ids: list[str] = []

    def open_preset_session(self, workspace_tab_id: str) -> _CreatedSessionTabStub:
        self.open_preset_session_workspace_ids.append(workspace_tab_id)
        return _CreatedSessionTabStub(
            session_tab_id="session-preset-1",
            display_name="P1",
        )


class _CreatePresetSessionWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _CreatePresetSessionRuntimeStub) -> None:
        self._runtime = runtime
        self.ensured_session_ids: list[str] = []
        self.refreshed_session_ids: list[str] = []
        self.selected_workspace_ids: list[str] = []
        self.selected_session_ids: list[tuple[str, str]] = []
        self.status_messages: list[str] = []

    def _ensure_session_view(self, session_tab_id: str) -> None:
        self.ensured_session_ids.append(session_tab_id)

    def _refresh_session_view(self, session_tab_id: str) -> None:
        self.refreshed_session_ids.append(session_tab_id)

    def _select_workspace_tab(self, workspace_tab_id: str) -> None:
        self.selected_workspace_ids.append(workspace_tab_id)

    def _select_session_tab(self, workspace_tab_id: str, session_tab_id: str) -> None:
        self.selected_session_ids.append((workspace_tab_id, session_tab_id))

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


@dataclass(slots=True)
class _BulkImportSessionWidgetsStub:
    auto_commit_var: "_BoolVarStub"


class _BulkImportRuntimeStub:
    def __init__(self) -> None:
        self.import_calls: list[tuple[str, tuple[str, ...], bool]] = []

    def import_prompt_sessions(
        self,
        workspace_tab_id: str,
        prompts: tuple[str, ...],
        *,
        auto_commit_enabled: bool,
    ) -> ImportedPromptSessionsResult:
        self.import_calls.append((workspace_tab_id, tuple(prompts), auto_commit_enabled))
        registrations: list[ImportedPromptSessionRegistration] = []
        next_job_number = 1
        for index, prompt in enumerate(prompts, start=1):
            session_tab_id = f"session-{index}"
            prompt_job = Job(
                job_id=f"job-{next_job_number}",
                workspace_tab_id=workspace_tab_id,
                session_tab_id=session_tab_id,
                prompt=prompt,
                status=JobStatus.QUEUED,
            )
            next_job_number += 1
            auto_commit_job = None
            if auto_commit_enabled:
                auto_commit_job = Job(
                    job_id=f"job-{next_job_number}",
                    workspace_tab_id=workspace_tab_id,
                    session_tab_id=session_tab_id,
                    prompt=AUTO_COMMIT_PROMPT,
                    status=JobStatus.QUEUED,
                )
                next_job_number += 1
            registrations.append(
                ImportedPromptSessionRegistration(
                    session_tab=SessionTab(
                        session_tab_id=session_tab_id,
                        workspace_tab_id=workspace_tab_id,
                        display_name=f"S{index}",
                    ),
                    prompt_job=prompt_job,
                    auto_commit_job=auto_commit_job,
                )
            )
        return ImportedPromptSessionsResult(registrations=tuple(registrations))


class _BulkImportWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _BulkImportRuntimeStub) -> None:
        self._runtime = runtime
        self.ensured_session_ids: list[str] = []
        self.auto_commit_states: list[tuple[str, bool]] = []
        self.drain_runtime_events_calls = 0
        self.refreshed_session_ids: list[tuple[str, str | None]] = []
        self.refreshed_workspace_ids: list[tuple[str, str | None]] = []
        self.selected_workspace_ids: list[str] = []
        self.selected_session_ids: list[tuple[str, str]] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _ensure_session_view(self, session_tab_id: str) -> _BulkImportSessionWidgetsStub:
        self.ensured_session_ids.append(session_tab_id)
        return _BulkImportSessionWidgetsStub(
            auto_commit_var=_BulkImportBoolVarStub(self, session_tab_id)
        )

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _refresh_session_view(
        self,
        session_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_session_ids.append((session_tab_id, preferred_job_id))

    def _select_workspace_tab(self, workspace_tab_id: str) -> None:
        self.selected_workspace_ids.append(workspace_tab_id)

    def _select_session_tab(self, workspace_tab_id: str, session_tab_id: str) -> None:
        self.selected_session_ids.append((workspace_tab_id, session_tab_id))

    def _refresh_workspace_task_list(
        self,
        workspace_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_workspace_ids.append((workspace_tab_id, preferred_job_id))

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _BulkImportBoolVarStub:
    def __init__(self, window: _BulkImportWindowStub, session_tab_id: str) -> None:
        self._window = window
        self._session_tab_id = session_tab_id

    def set(self, value: bool) -> None:
        self._window.auto_commit_states.append((self._session_tab_id, value))


class _QueueRuntimeStub:
    def __init__(self, *, jobs: tuple[Job, ...] | None = None) -> None:
        self._jobs = (
            jobs
            if jobs is not None
            else (
                Job(
                    job_id="job-1",
                    workspace_tab_id="workspace-1",
                    session_tab_id="session-1",
                    prompt="queued",
                ),
            )
        )
        self.background_starts: list[str] = []
        self.stopped_queue_ids: list[str] = []
        self.list_workspace_jobs_requests: list[str] = []
        self.workspace_has_jobs_requests: list[str] = []

    def has_pending_background_work(self) -> bool:
        return False

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        self.list_workspace_jobs_requests.append(workspace_tab_id)
        return tuple(job for job in self._jobs if job.workspace_tab_id == workspace_tab_id)

    def workspace_has_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_jobs_requests.append(workspace_tab_id)
        return any(job.workspace_tab_id == workspace_tab_id for job in self._jobs)

    def workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_jobs_requests.append(workspace_tab_id)
        return any(
            job.workspace_tab_id == workspace_tab_id and job.status == JobStatus.QUEUED
            for job in self._jobs
        )

    def start_queue_in_background(self, workspace_tab_id: str) -> None:
        self.background_starts.append(workspace_tab_id)

    def stop_queue(self, workspace_tab_id: str) -> WorkspaceQueueState:
        self.stopped_queue_ids.append(workspace_tab_id)
        return WorkspaceQueueState(
            workspace_tab_id=workspace_tab_id,
            status=QueueStatus.STOPPED,
        )

    def get_workspace_tab(self, workspace_tab_id: str) -> _WorkspaceTabStub:
        return _WorkspaceTabStub(display_name="W1")


class _QueueWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _QueueRuntimeStub, *, toggle_value: bool = False) -> None:
        self._runtime = runtime
        self._workspace_views = {
            "workspace-1": _WorkspaceQueueSummaryViewStub(
                queue_var=_StringVarStub(),
                queue_toggle_var=_BoolVarStub(toggle_value),
                queue_toggle_button=_ButtonConfigureStub(),
            )
        }
        self._queue_start_pending_workspace_ids: set[str] = set()
        self.drain_runtime_events_calls = 0
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _start_queue(self, workspace_tab_id: str) -> bool:
        return MainWindow._start_queue(self, workspace_tab_id)

    def _stop_queue(self, workspace_tab_id: str) -> bool:
        return MainWindow._stop_queue(self, workspace_tab_id)

    def _workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        return MainWindow._workspace_has_runnable_jobs(self, workspace_tab_id)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


@dataclass(slots=True, frozen=True)
class _ScheduledRunWorkspaceTabStub:
    workspace_tab_id: str
    display_name: str


class _ScheduledRunRuntimeStub:
    def __init__(
        self,
        *,
        jobs: tuple[Job, ...],
        open_workspace_ids: tuple[str, ...],
    ) -> None:
        self._jobs = jobs
        self._open_workspace_ids = open_workspace_ids
        self.background_starts: list[str] = []
        self.workspace_has_runnable_jobs_requests: list[str] = []

    def list_workspace_tabs(
        self,
        *,
        include_closed: bool = False,
    ) -> tuple[_ScheduledRunWorkspaceTabStub, ...]:
        del include_closed
        return tuple(
            _ScheduledRunWorkspaceTabStub(
                workspace_tab_id=workspace_tab_id,
                display_name=workspace_tab_id.replace("workspace-", "W"),
            )
            for workspace_tab_id in self._open_workspace_ids
        )

    def workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        self.workspace_has_runnable_jobs_requests.append(workspace_tab_id)
        return any(
            job.workspace_tab_id == workspace_tab_id and job.status == JobStatus.QUEUED
            for job in self._jobs
        )

    def start_queue_in_background(self, workspace_tab_id: str) -> None:
        self.background_starts.append(workspace_tab_id)

    def get_workspace_tab(self, workspace_tab_id: str) -> _ScheduledRunWorkspaceTabStub:
        return _ScheduledRunWorkspaceTabStub(
            workspace_tab_id=workspace_tab_id,
            display_name=workspace_tab_id.replace("workspace-", "W"),
        )


class _ScheduledRunWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _ScheduledRunRuntimeStub) -> None:
        self._runtime = runtime
        self._closed = False
        self._scheduled_run_at: datetime | None = None
        self._scheduled_run_after_id: str | None = None
        self._scheduled_run_var = _StringVarStub()
        self._scheduled_run_toggle_var = _BoolVarStub(False)
        self._scheduled_run_button = _ButtonConfigureStub()
        self._scheduled_run_status_label = None
        self._queue_start_pending_workspace_ids: set[str] = set()
        self.status_messages: list[str] = []
        self.canceled_after_ids: list[str] = []
        self.after_intervals: list[int] = []
        self.refresh_workspace_queue_summaries_calls = 0

    def _cancel_scheduled_run(self, *, update_status: bool = False) -> None:
        MainWindow._cancel_scheduled_run(self, update_status=update_status)

    def _cancel_scheduled_run_timer(self) -> None:
        MainWindow._cancel_scheduled_run_timer(self)

    def _schedule_scheduled_run_check(self) -> None:
        MainWindow._schedule_scheduled_run_check(self)

    def _start_scheduled_run_queues(self, scheduled_at: datetime) -> None:
        MainWindow._start_scheduled_run_queues(self, scheduled_at)

    def _refresh_scheduled_run_display(self) -> None:
        MainWindow._refresh_scheduled_run_display(self)

    def _workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        return MainWindow._workspace_has_runnable_jobs(self, workspace_tab_id)

    def _start_queue(self, workspace_tab_id: str) -> bool:
        return MainWindow._start_queue(self, workspace_tab_id)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

    def after(self, interval_ms: int, callback: object) -> str:
        del callback
        self.after_intervals.append(interval_ms)
        return f"after-{len(self.after_intervals)}"

    def after_cancel(self, after_id: str) -> None:
        self.canceled_after_ids.append(after_id)


class _SubmitPromptTextStub:
    def __init__(self, content: str) -> None:
        self.content = content
        self.state = "normal"
        self.deleted_ranges: list[tuple[str, str]] = []

    def get(self, start: str, end: str) -> str:
        del start, end
        return self.content

    def cget(self, option: str) -> object:
        if option == "state":
            return self.state
        raise KeyError(option)

    def configure(self, **options: object) -> None:
        if "state" in options:
            self.state = str(options["state"])

    def grid(self) -> None:
        self.is_gridded = True
        self.grid_calls += 1

    def grid_remove(self) -> None:
        self.is_gridded = False
        self.grid_remove_calls += 1

    def lift(self) -> None:
        self.lift_calls += 1

    def delete(self, start: str, end: str) -> None:
        self.deleted_ranges.append((start, end))
        self.content = ""


class _BoolVarStub:
    def __init__(self, value: bool) -> None:
        self._value = value

    def get(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = value


@dataclass(slots=True)
class _SubmitSessionWidgetsStub:
    prompt_text: _SubmitPromptTextStub
    auto_commit_var: _BoolVarStub


class _SubmitJobRuntimeStub:
    def __init__(self) -> None:
        self.submitted_jobs: list[tuple[str, str]] = []
        self.submitted_execution_options: list[AgentExecutionOptions | None] = []

    def submit_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        self.submitted_jobs.append((session_tab_id, prompt))
        self.submitted_execution_options.append(execution_options)
        job_number = len(self.submitted_jobs)
        return Job(
            job_id=f"job-{job_number}",
            workspace_tab_id="workspace-1",
            session_tab_id=session_tab_id,
            prompt=prompt,
            status=JobStatus.QUEUED,
        )


class _SubmitJobWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _SubmitJobRuntimeStub,
        *,
        prompt: str,
        auto_commit: bool,
        execution_options: AgentExecutionOptions | None = AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4",
        ),
    ) -> None:
        self._runtime = runtime
        self.execution_options = execution_options
        self.session_widgets = _SubmitSessionWidgetsStub(
            prompt_text=_SubmitPromptTextStub(prompt),
            auto_commit_var=_BoolVarStub(auto_commit),
        )
        self.drain_runtime_events_calls = 0
        self.refreshed_session_ids: list[tuple[str, str | None]] = []
        self.refreshed_workspace_ids: list[tuple[str, str | None]] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []
        self.execution_option_refreshes: list[str] = []

    def _get_session_widgets(self, session_tab_id: str) -> _SubmitSessionWidgetsStub:
        del session_tab_id
        return self.session_widgets

    def _execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        del session_tab_id
        return self.execution_options

    def _refresh_session_execution_option_controls(self, session_tab_id: str) -> None:
        self.execution_option_refreshes.append(session_tab_id)

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _refresh_session_view(
        self,
        session_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_session_ids.append((session_tab_id, preferred_job_id))

    def _refresh_workspace_task_list(
        self,
        workspace_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_workspace_ids.append((workspace_tab_id, preferred_job_id))

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


@dataclass(slots=True)
class _PresetLanguageSessionWidgetsStub:
    preset_language_var: _StringVarStub | None
    preset_instruction_var: _StringVarStub | None
    preset_work_priority_var: _StringVarStub | None = None
    preset_prompt_prefix_text: _SubmitPromptTextStub | None = None
    preset_action_execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )


@dataclass(slots=True)
class _PresetLanguageSessionTabStub:
    workspace_tab_id: str


@dataclass(slots=True)
class _PresetLanguageWorkspaceTabStub:
    workspace_path: str


class _PresetLanguageRuntimeStub:
    def __init__(
        self,
        *,
        workspace_paths: dict[str, str],
        session_workspace_ids: dict[str, str],
    ) -> None:
        self._workspace_paths = workspace_paths
        self._session_workspace_ids = session_workspace_ids

    def get_session_tab(self, session_tab_id: str) -> _PresetLanguageSessionTabStub:
        return _PresetLanguageSessionTabStub(
            workspace_tab_id=self._session_workspace_ids[session_tab_id]
        )

    def get_workspace_tab(self, workspace_tab_id: str) -> _PresetLanguageWorkspaceTabStub:
        return _PresetLanguageWorkspaceTabStub(
            workspace_path=self._workspace_paths[workspace_tab_id]
        )


class _PresetLanguagePreferenceWindowStub:
    def __init__(
        self,
        *,
        workspace_paths: dict[str, str],
        session_workspace_ids: dict[str, str],
        session_language: str,
        session_instruction: str = "bug",
        session_work_priority: str = "medium",
        session_prompt_prefix: str = "",
        session_preset_action_execution_options: AgentExecutionOptions | None = None,
    ) -> None:
        self._runtime = _PresetLanguageRuntimeStub(
            workspace_paths=workspace_paths,
            session_workspace_ids=session_workspace_ids,
        )
        self._workspace_preset_languages: dict[str, str] = {}
        self._workspace_preset_instructions: dict[tuple[str, str], str] = {}
        self._workspace_preset_work_priorities: dict[str, str] = {}
        self._workspace_preset_prompt_prefixes: dict[str, str] = {}
        self._workspace_preset_action_execution_options: dict[
            str,
            AgentExecutionOptions,
        ] = {}
        self._session_widgets = _PresetLanguageSessionWidgetsStub(
            preset_language_var=_StringVarStub(session_language),
            preset_instruction_var=_StringVarStub(session_instruction),
            preset_work_priority_var=_StringVarStub(session_work_priority),
            preset_prompt_prefix_text=_SubmitPromptTextStub(session_prompt_prefix),
            preset_action_execution_options=(
                session_preset_action_execution_options or AgentExecutionOptions()
            ),
        )

    def _get_session_widgets(
        self,
        session_tab_id: str,
    ) -> _PresetLanguageSessionWidgetsStub:
        del session_tab_id
        return self._session_widgets

    def _workspace_preset_language_key(self, workspace_tab_id: str) -> str:
        return MainWindow._workspace_preset_language_key(self, workspace_tab_id)

    def _workspace_preset_instruction_key(
        self,
        workspace_tab_id: str,
        language: str,
    ) -> tuple[str, str]:
        return MainWindow._workspace_preset_instruction_key(
            self,
            workspace_tab_id,
            language,
        )

    def _preset_prompt_prefix_for_session(self, session_tab_id: str) -> str:
        return MainWindow._preset_prompt_prefix_for_session(self, session_tab_id)

    def _remember_preset_prompt_prefix_for_workspace(
        self,
        workspace_tab_id: str,
        prompt_prefix: str,
    ) -> None:
        MainWindow._remember_preset_prompt_prefix_for_workspace(
            self,
            workspace_tab_id,
            prompt_prefix,
        )


@dataclass(slots=True)
class _SubmitPresetSessionWidgetsStub:
    preset_language_var: _StringVarStub
    preset_instruction_var: _StringVarStub
    preset_work_priority_var: _StringVarStub
    auto_commit_var: _BoolVarStub
    preset_prompt_prefix_text: _SubmitPromptTextStub | None = None
    preset_language_combobox: _ComboboxConfigureStub | None = None
    preset_instruction_combobox: _ComboboxConfigureStub | None = None
    preset_work_priority_combobox: _ComboboxConfigureStub | None = None
    preset_auto_commit_checkbutton: _ButtonConfigureStub | None = None
    preset_register_button: _ButtonConfigureStub | None = None
    preset_action_agent_provider_var: _StringVarStub | None = field(
        default_factory=_StringVarStub
    )
    preset_action_model_var: _StringVarStub | None = field(default_factory=_StringVarStub)
    preset_action_reasoning_var: _StringVarStub | None = field(
        default_factory=_StringVarStub
    )
    preset_action_agent_provider_combobox: _ComboboxConfigureStub | None = None
    preset_action_model_combobox: _ComboboxConfigureStub | None = None
    preset_action_reasoning_combobox: _ComboboxConfigureStub | None = None
    preset_action_agent_provider_options: tuple[object, ...] = ()
    preset_action_model_options: tuple[object, ...] = ()
    preset_action_reasoning_options: tuple[object, ...] = ()
    preset_action_execution_options: AgentExecutionOptions = field(
        default_factory=lambda: AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4-mini",
            reasoning_effort="low",
        )
    )


class _SubmitPresetRuntimeStub:
    def __init__(self, *, submit_error: Exception | None = None) -> None:
        self._submit_error = submit_error
        self.submitted_preset_jobs: list[tuple[str, str, str, str, bool]] = []
        self.submitted_analysis_prompt_prefixes: list[str] = []
        self.submitted_execution_options: list[AgentExecutionOptions | None] = []
        self.submitted_candidate_execution_options: list[
            AgentExecutionOptions | None
        ] = []

    def submit_preset_analysis_job(
        self,
        session_tab_id: str,
        *,
        language: str,
        instruction: str,
        work_priority: str,
        analysis_prompt_prefix: str = "",
        auto_commit_enabled: bool = False,
        execution_options: AgentExecutionOptions | None = None,
        candidate_execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        self.submitted_preset_jobs.append(
            (session_tab_id, language, instruction, work_priority, auto_commit_enabled)
        )
        self.submitted_analysis_prompt_prefixes.append(analysis_prompt_prefix)
        self.submitted_execution_options.append(execution_options)
        self.submitted_candidate_execution_options.append(candidate_execution_options)
        if self._submit_error is not None:
            raise self._submit_error
        return Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id=session_tab_id,
            prompt="preset analysis",
            status=JobStatus.QUEUED,
        )


class _SubmitPresetWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _SubmitPresetRuntimeStub,
        *,
        auto_commit: bool,
        language: str = "Python",
        instruction: str = "bug",
        work_priority: str = "medium",
        analysis_prompt_prefix: str = "",
        language_combobox: _ComboboxConfigureStub | None = None,
        instruction_combobox: _ComboboxConfigureStub | None = None,
        work_priority_combobox: _ComboboxConfigureStub | None = None,
        auto_commit_checkbutton: _ButtonConfigureStub | None = None,
        register_button: _ButtonConfigureStub | None = None,
    ) -> None:
        self._runtime = runtime
        self.session_widgets = _SubmitPresetSessionWidgetsStub(
            preset_language_var=_StringVarStub(language),
            preset_instruction_var=_StringVarStub(instruction),
            preset_work_priority_var=_StringVarStub(work_priority),
            auto_commit_var=_BoolVarStub(auto_commit),
            preset_prompt_prefix_text=_SubmitPromptTextStub(analysis_prompt_prefix),
            preset_language_combobox=language_combobox,
            preset_instruction_combobox=instruction_combobox,
            preset_work_priority_combobox=work_priority_combobox,
            preset_auto_commit_checkbutton=auto_commit_checkbutton,
            preset_register_button=register_button,
        )
        self.drain_runtime_events_calls = 0
        self.refreshed_session_ids: list[tuple[str, str | None]] = []
        self.refreshed_workspace_ids: list[tuple[str, str | None]] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []
        self.execution_option_controls_enabled: list[bool] = []
        self.remembered_prompt_prefixes: list[str] = []

    def _get_session_widgets(self, session_tab_id: str) -> _SubmitPresetSessionWidgetsStub:
        del session_tab_id
        return self.session_widgets

    def _execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        del session_tab_id
        return AgentExecutionOptions(agent_provider="codex", model="gpt-5.4")

    def _preset_action_execution_options_for_registration(
        self,
        session_tab_id: str,
    ) -> AgentExecutionOptions | None:
        del session_tab_id
        return AgentExecutionOptions(
            agent_provider="codex",
            model="gpt-5.4-mini",
            reasoning_effort="low",
        )

    def _preset_prompt_prefix_for_session(self, session_tab_id: str) -> str:
        return MainWindow._preset_prompt_prefix_for_session(self, session_tab_id)

    def _remember_preset_prompt_prefix_for_session(self, session_tab_id: str) -> None:
        self.remembered_prompt_prefixes.append(
            self._preset_prompt_prefix_for_session(session_tab_id)
        )

    def _remember_preset_prompt_prefix_for_workspace(
        self,
        workspace_tab_id: str,
        prompt_prefix: str,
    ) -> None:
        del workspace_tab_id
        self.remembered_prompt_prefixes.append(prompt_prefix)

    def _drain_runtime_events(self) -> None:
        self.drain_runtime_events_calls += 1

    def _set_preset_registration_controls_enabled(
        self,
        session_widgets: _SubmitPresetSessionWidgetsStub,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_registration_controls_enabled(
            self,
            session_widgets,
            enabled=enabled,
        )

    def _set_session_execution_option_controls_enabled(
        self,
        session_widgets: _SubmitPresetSessionWidgetsStub,
        *,
        enabled: bool,
    ) -> None:
        del session_widgets
        self.execution_option_controls_enabled.append(enabled)

    def _set_preset_combobox_enabled(
        self,
        combobox: _ComboboxConfigureStub | None,
        *,
        enabled: bool,
    ) -> None:
        MainWindow._set_preset_combobox_enabled(combobox, enabled=enabled)

    def _refresh_session_view(
        self,
        session_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_session_ids.append((session_tab_id, preferred_job_id))

    def _refresh_workspace_task_list(
        self,
        workspace_tab_id: str,
        preferred_job_id: str | None = None,
    ) -> None:
        self.refreshed_workspace_ids.append((workspace_tab_id, preferred_job_id))

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


@dataclass(slots=True)
class _PresetCandidateSessionWidgetsStub:
    auto_commit_var: _BoolVarStub


@dataclass(slots=True)
class _OrderedSessionTabStub:
    session_tab_id: str


@dataclass(slots=True)
class _SessionOrderWorkspaceViewStub:
    session_views: dict[str, object]
    session_notebook: "_SessionOrderNotebookStub"


class _SessionOrderNotebookStub:
    def __init__(self, tab_count: int) -> None:
        self._tab_count = tab_count

    def tabs(self) -> tuple[str, ...]:
        return tuple(f"tab-{index}" for index in range(self._tab_count))


class _SessionOrderRuntimeStub:
    def __init__(self, ordered_session_ids: tuple[str, ...]) -> None:
        self._ordered_session_ids = ordered_session_ids

    def list_session_tabs(
        self,
        workspace_tab_id: str,
        *,
        include_closed: bool = False,
    ) -> tuple[_OrderedSessionTabStub, ...]:
        del workspace_tab_id, include_closed
        return tuple(
            _OrderedSessionTabStub(session_tab_id)
            for session_tab_id in self._ordered_session_ids
        )


class _SessionOrderWindowStub:
    def __init__(
        self,
        *,
        ordered_session_ids: tuple[str, ...],
        existing_session_ids: tuple[str, ...],
    ) -> None:
        self._runtime = _SessionOrderRuntimeStub(ordered_session_ids)
        self._workspace_views = {
            "workspace-1": _SessionOrderWorkspaceViewStub(
                session_views={
                    session_tab_id: object() for session_tab_id in existing_session_ids
                },
                session_notebook=_SessionOrderNotebookStub(len(existing_session_ids)),
            )
        }


class _PresetCandidateRegistrationWindowStub(_KoreanUiLanguageStub):
    def __init__(self) -> None:
        self.session_widgets: dict[str, _PresetCandidateSessionWidgetsStub] = {}
        self.ensured_session_ids: list[str] = []
        self.refreshed_session_ids: list[str] = []
        self.refreshed_workspace_ids: list[str] = []
        self.synced_workspace_ids: list[str] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _ensure_session_view(self, session_tab_id: str) -> _PresetCandidateSessionWidgetsStub:
        self.ensured_session_ids.append(session_tab_id)
        self.session_widgets.setdefault(
            session_tab_id,
            _PresetCandidateSessionWidgetsStub(auto_commit_var=_BoolVarStub(False)),
        )
        return self.session_widgets[session_tab_id]

    def _has_session_view(self, session_tab_id: str) -> bool:
        return session_tab_id in self.session_widgets

    def _refresh_session_view(self, session_tab_id: str) -> None:
        self.refreshed_session_ids.append(session_tab_id)

    def _refresh_workspace_task_list(self, workspace_tab_id: str) -> None:
        self.refreshed_workspace_ids.append(workspace_tab_id)

    def _sync_session_tab_order(self, workspace_tab_id: str) -> None:
        self.synced_workspace_ids.append(workspace_tab_id)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _TextWidgetStub:
    def __init__(self, *, content: str, yview: tuple[float, float]) -> None:
        self.content = content
        self._yview = yview
        self.states: list[str] = []
        self.see_calls: list[str] = []

    def configure(self, *, state: str) -> None:
        self.states.append(state)

    def delete(self, start: str, end: str) -> None:
        del start, end
        self.content = ""

    def insert(self, index: str, content: str) -> None:
        del index
        self.content += content

    def get(self, start: str, end: str) -> str:
        del start, end
        return self.content

    def yview(self) -> tuple[float, float]:
        return self._yview

    def see(self, index: str) -> None:
        self.see_calls.append(index)


@dataclass(slots=True)
class _SessionSelectionWidgetsStub:
    selected_job_id: str | None
    log_text: _TextWidgetStub
    rendered_log_job_id: str | None = None
    rendered_log_line_count: int = 0
    rendered_log_last_line: str | None = None
    rendered_log_language: str | None = None
    session_id_var: _StringVarStub = field(default_factory=_StringVarStub)
    activity_var: _StringVarStub = field(default_factory=_StringVarStub)
    message_var: _StringVarStub = field(default_factory=_StringVarStub)
    wait_reason_var: _StringVarStub = field(default_factory=_StringVarStub)
    message_label: _LabelVisibilityStub = field(default_factory=_LabelVisibilityStub)
    wait_reason_label: _LabelVisibilityStub = field(
        default_factory=_LabelVisibilityStub
    )


class _SessionSelectionRuntimeStub:
    def __init__(
        self,
        jobs: tuple[Job, ...],
        *,
        progress_logs: dict[str, tuple[str, ...]] | None = None,
        job_user_messages: dict[str, str] | None = None,
    ) -> None:
        self._jobs = jobs
        self._progress_logs = progress_logs or {}
        self._job_user_messages = job_user_messages or {}

    def list_jobs(self, *, session_tab_id: str | None = None) -> tuple[Job, ...]:
        del session_tab_id
        return self._jobs

    def get_session_tab(self, session_tab_id: str) -> SessionTab:
        return SessionTab(
            session_tab_id=session_tab_id,
            workspace_tab_id="workspace-1",
            display_name="S1",
            session_id="session-id-1",
        )

    def list_session_turns(self, session_tab_id: str) -> tuple[object, ...]:
        del session_tab_id
        return ()

    def get_job(self, job_id: str) -> Job:
        for job in self._jobs:
            if job.job_id == job_id:
                return job
        raise KeyError(job_id)

    def get_job_user_message(self, job_id: str) -> str:
        return self._job_user_messages.get(job_id, "")

    def get_job_progress_logs(self, job_id: str) -> tuple[str, ...]:
        return self._progress_logs.get(job_id, ())


class _SessionSelectionWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        jobs: tuple[Job, ...],
        *,
        selected_job_id: str | None,
        progress_logs: dict[str, tuple[str, ...]] | None = None,
        job_user_messages: dict[str, str] | None = None,
    ) -> None:
        self._runtime = _SessionSelectionRuntimeStub(
            jobs,
            progress_logs=progress_logs,
            job_user_messages=job_user_messages,
        )
        self.session_widgets = _SessionSelectionWidgetsStub(
            selected_job_id,
            _TextWidgetStub(content="", yview=(0.0, 1.0)),
        )
        self.session_tab_indicator_calls: list[tuple[str, bool]] = []

    def _get_session_widgets(self, session_tab_id: str) -> _SessionSelectionWidgetsStub:
        del session_tab_id
        return self.session_widgets

    def _refresh_session_tab_indicator(
        self, session_tab_id: str, *, started: bool
    ) -> None:
        self.session_tab_indicator_calls.append((session_tab_id, started))

    def _set_text_content(
        self,
        widget: _TextWidgetStub,
        content: str,
        *,
        auto_scroll_to_end: bool = False,
    ) -> None:
        MainWindow._set_text_content(
            self,
            widget,
            content,
            auto_scroll_to_end=auto_scroll_to_end,
        )

    def _select_appended_running_job(
        self,
        session_widgets: _SessionSelectionWidgetsStub,
        *,
        selected_job_id: str,
        appended_job_id: str,
    ) -> str:
        return MainWindow._select_appended_running_job(
            self,
            session_widgets,
            selected_job_id=selected_job_id,
            appended_job_id=appended_job_id,
        )

    def _mark_session_output_rendered(
        self,
        session_widgets: _SessionSelectionWidgetsStub,
        *,
        job_id: str | None,
        line_count: int,
        last_line: str | None,
        language: str | None,
    ) -> None:
        MainWindow._mark_session_output_rendered(
            self,
            session_widgets,
            job_id=job_id,
            line_count=line_count,
            last_line=last_line,
            language=language,
        )


class _WorkspaceOpenRuntimeStub:
    def __init__(self) -> None:
        self.background_open_paths: list[str] = []

    def open_workspace_in_background(self, workspace_path: str) -> None:
        self.background_open_paths.append(workspace_path)


class _WorkspaceOpenWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _WorkspaceOpenRuntimeStub) -> None:
        self._runtime = runtime
        self.status_messages: list[str] = []

    def _request_workspace_open(self, workspace_path: str) -> bool:
        return MainWindow._request_workspace_open(self, workspace_path)

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _StartupWorkspaceOpenWindowStub(_WorkspaceOpenWindowStub):
    def __init__(self, runtime: _WorkspaceOpenRuntimeStub) -> None:
        super().__init__(runtime)
        self.after_intervals: list[int] = []
        self.after_callbacks: list[object] = []

    def _open_workspace_path(self, workspace_path: str) -> None:
        MainWindow._open_workspace_path(self, workspace_path)

    def _open_startup_workspace_paths(self, workspace_paths: tuple[str, ...]) -> None:
        MainWindow._open_startup_workspace_paths(self, workspace_paths)

    def after(self, interval_ms: int, callback: object) -> str:
        self.after_intervals.append(interval_ms)
        self.after_callbacks.append(callback)
        return f"after-{len(self.after_intervals)}"

    def run_scheduled_callbacks(self) -> None:
        for callback in self.after_callbacks:
            if not callable(callback):
                raise AssertionError("scheduled callback is not callable")
            callback()


@dataclass(slots=True, frozen=True)
class _DropEvent:
    data: str


class _DropTkStub:
    def __init__(self, split_paths: tuple[str, ...]) -> None:
        self._split_paths = split_paths

    def splitlist(self, _data: str) -> tuple[str, ...]:
        return self._split_paths


class _WorkspaceDropWindowStub(_WorkspaceOpenWindowStub):
    def __init__(
        self,
        runtime: _WorkspaceOpenRuntimeStub,
        *,
        split_paths: tuple[str, ...],
    ) -> None:
        super().__init__(runtime)
        self.tk = _DropTkStub(split_paths)


@dataclass(slots=True, frozen=True)
class _SavedWorkspaceStub:
    path: str
    display_name: str


class _SavedWorkspaceDeleteRuntimeStub:
    def __init__(
        self,
        deleted_workspace: _SavedWorkspaceStub | None,
        *,
        running_workspace_paths: tuple[str, ...] = (),
    ) -> None:
        self._deleted_workspace = deleted_workspace
        self._running_workspace_paths = set(running_workspace_paths)
        self.running_checks: list[str] = []
        self.deleted_paths: list[str] = []

    def workspace_path_has_running_job(self, workspace_path: str) -> bool:
        self.running_checks.append(workspace_path)
        return workspace_path in self._running_workspace_paths

    def delete_saved_workspace(self, workspace_path: str) -> _SavedWorkspaceStub | None:
        self.deleted_paths.append(workspace_path)
        return self._deleted_workspace


class _SavedWorkspaceListboxStub:
    def __init__(self, selection: tuple[int, ...]) -> None:
        self._selection = selection

    def curselection(self) -> tuple[int, ...]:
        return self._selection


class _SavedWorkspaceDeleteWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _SavedWorkspaceDeleteRuntimeStub,
        *,
        saved_workspace_paths: list[str],
        selection: tuple[int, ...],
    ) -> None:
        self._runtime = runtime
        self._saved_workspace_paths = saved_workspace_paths
        self._saved_workspaces_listbox = _SavedWorkspaceListboxStub(selection)
        self.refresh_saved_workspace_list_calls = 0
        self.status_messages: list[str] = []

    def _refresh_saved_workspace_list(self) -> None:
        self.refresh_saved_workspace_list_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


@dataclass(slots=True, frozen=True)
class _ClosedSessionTabStub:
    workspace_tab_id: str


@dataclass(slots=True, frozen=True)
class _CloseResultStub:
    session_tab: _ClosedSessionTabStub | None = None
    canceled_job: Job | None = None
    removed_queued_job_count: int = 0


class _TabCloseRuntimeStub:
    def __init__(self, jobs: tuple[Job, ...]) -> None:
        self._jobs = jobs
        self.closed_session_ids: list[str] = []
        self.closed_workspace_ids: list[str] = []

    def list_jobs(self, *, session_tab_id: str | None = None) -> tuple[Job, ...]:
        if session_tab_id is None:
            return self._jobs
        return tuple(job for job in self._jobs if job.session_tab_id == session_tab_id)

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        return tuple(job for job in self._jobs if job.workspace_tab_id == workspace_tab_id)

    def close_session(self, session_tab_id: str) -> _CloseResultStub:
        self.closed_session_ids.append(session_tab_id)
        removed_count = len(
            [
                job
                for job in self._jobs
                if job.session_tab_id == session_tab_id
                and job.status in (JobStatus.QUEUED, JobStatus.WAITING_FOR_CONFIGURATION)
            ]
        )
        return _CloseResultStub(
            session_tab=_ClosedSessionTabStub(workspace_tab_id="workspace-1"),
            removed_queued_job_count=removed_count,
        )

    def close_workspace(self, workspace_tab_id: str) -> _CloseResultStub:
        self.closed_workspace_ids.append(workspace_tab_id)
        removed_count = len(
            [
                job
                for job in self._jobs
                if job.workspace_tab_id == workspace_tab_id
                and job.status in (JobStatus.QUEUED, JobStatus.WAITING_FOR_CONFIGURATION)
            ]
        )
        return _CloseResultStub(removed_queued_job_count=removed_count)


class _TabCloseWindowStub(_KoreanUiLanguageStub):
    def __init__(self, runtime: _TabCloseRuntimeStub) -> None:
        self._runtime = runtime
        self._queue_start_pending_workspace_ids: set[str] = set()
        self.removed_session_views: list[str] = []
        self.removed_workspace_views: list[str] = []
        self.refreshed_workspace_ids: list[str] = []
        self.refresh_workspace_queue_summaries_calls = 0
        self.status_messages: list[str] = []

    def _remove_session_view(self, session_tab_id: str) -> None:
        self.removed_session_views.append(session_tab_id)

    def _remove_workspace_view(self, workspace_tab_id: str) -> None:
        self.removed_workspace_views.append(workspace_tab_id)

    def _session_has_running_job(self, session_tab_id: str) -> bool:
        return MainWindow._session_has_running_job(self, session_tab_id)

    def _workspace_has_running_job(self, workspace_tab_id: str) -> bool:
        return MainWindow._workspace_has_running_job(self, workspace_tab_id)

    def _session_pending_job_count(self, session_tab_id: str) -> int:
        return MainWindow._session_pending_job_count(self, session_tab_id)

    def _workspace_pending_job_count(self, workspace_tab_id: str) -> int:
        return MainWindow._workspace_pending_job_count(self, workspace_tab_id)

    def _confirm_tab_close(
        self,
        *,
        title: str,
        has_running_job: bool,
        pending_job_count: int,
    ) -> bool:
        return MainWindow._confirm_tab_close(
            self,
            title=title,
            has_running_job=has_running_job,
            pending_job_count=pending_job_count,
        )

    def _refresh_workspace_task_list(self, workspace_tab_id: str) -> None:
        self.refreshed_workspace_ids.append(workspace_tab_id)

    def _refresh_workspace_queue_summaries(self) -> None:
        self.refresh_workspace_queue_summaries_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _WorkspaceNotebookSelectStub:
    def __init__(self, selected_tab: str) -> None:
        self._selected_tab = selected_tab

    def select(self) -> str:
        return self._selected_tab


class _CloseActiveWorkspaceWindowStub(_KoreanUiLanguageStub):
    def __init__(self, *, selected_tab: str) -> None:
        self._workspace_notebook = _WorkspaceNotebookSelectStub(selected_tab)
        self._workspace_frame_map = {"frame-1": "workspace-1"}
        self.closed_workspace_ids: list[str] = []
        self.status_messages: list[str] = []

    def _close_workspace(self, workspace_tab_id: str) -> None:
        self.closed_workspace_ids.append(workspace_tab_id)

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)


class _PollingWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _PollingRuntimeStub,
        *,
        drain_exception: Exception | None = None,
    ) -> None:
        self._runtime = runtime
        self._closed = False
        self._after_id: str | None = None
        self._event_poll_idle_interval_ms = EVENT_POLL_INTERVAL_MS
        self._drain_exception = drain_exception
        self.after_intervals: list[int] = []
        self.drain_runtime_events_calls = 0

    def _schedule_event_poll(self) -> None:
        raise AssertionError("scheduled callback should not run during the test")

    def _drain_runtime_events(self, *, max_items: int | None = None) -> int:
        del max_items
        self.drain_runtime_events_calls += 1
        if self._drain_exception is not None:
            raise self._drain_exception
        return 0

    def _next_event_poll_interval(
        self,
        *,
        processed: int,
        drained: int,
        poll_failed: bool = False,
    ) -> int:
        return MainWindow._next_event_poll_interval(
            self,
            processed=processed,
            drained=drained,
            poll_failed=poll_failed,
        )

    def after(self, interval_ms: int, callback: object) -> str:
        del callback
        self.after_intervals.append(interval_ms)
        return f"after-{len(self.after_intervals)}"


class _ShutdownWindowStub(_KoreanUiLanguageStub):
    def __init__(
        self,
        runtime: _PollingRuntimeStub,
        *,
        drain_exception: Exception | None = None,
    ) -> None:
        self._runtime = runtime
        self._shutdown_after_id: str | None = None
        self._drain_exception = drain_exception
        self.after_intervals: list[int] = []
        self.finalize_close_calls = 0
        self.status_messages: list[str] = []

    def _continue_close(self) -> None:
        raise AssertionError("scheduled callback should not run during the test")

    def _drain_runtime_events(self, *, max_items: int | None = None) -> int:
        del max_items
        if self._drain_exception is not None:
            raise self._drain_exception
        return 0

    def _finalize_close(self) -> None:
        self.finalize_close_calls += 1

    def _set_status(self, message: str) -> None:
        self.status_messages.append(message)

    def after(self, interval_ms: int, callback: object) -> str:
        del callback
        self.after_intervals.append(interval_ms)
        return f"after-{len(self.after_intervals)}"


if __name__ == "__main__":
    unittest.main()


