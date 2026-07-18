from __future__ import annotations

from tests._main_window_helpers import *

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
        self.assertEqual([], window._saved_workspaces_listbox.selection_sets)

    def test_delete_selected_saved_workspace_selects_next_entry_after_delete(self) -> None:
        runtime = _SavedWorkspaceDeleteRuntimeStub(
            _SavedWorkspaceStub(path=r"C:\Repo\Beta", display_name="Beta")
        )
        window = _SavedWorkspaceDeleteWindowStub(
            runtime,
            saved_workspace_paths=[
                r"C:\Repo\Alpha",
                r"C:\Repo\Beta",
                r"C:\Repo\Gamma",
            ],
            selection=(1,),
        )

        MainWindow._delete_selected_saved_workspace(window)

        self.assertEqual(
            [r"C:\Repo\Alpha", r"C:\Repo\Gamma"],
            window._saved_workspace_paths,
        )
        self.assertEqual([1], window._saved_workspaces_listbox.selection_sets)
        self.assertEqual([1], window._saved_workspaces_listbox.activate_calls)
        self.assertEqual([1], window._saved_workspaces_listbox.see_calls)

    def test_delete_selected_saved_workspace_selects_previous_entry_when_last_deleted(
        self,
    ) -> None:
        runtime = _SavedWorkspaceDeleteRuntimeStub(
            _SavedWorkspaceStub(path=r"C:\Repo\Gamma", display_name="Gamma")
        )
        window = _SavedWorkspaceDeleteWindowStub(
            runtime,
            saved_workspace_paths=[
                r"C:\Repo\Alpha",
                r"C:\Repo\Beta",
                r"C:\Repo\Gamma",
            ],
            selection=(2,),
        )

        MainWindow._delete_selected_saved_workspace(window)

        self.assertEqual(
            [r"C:\Repo\Alpha", r"C:\Repo\Beta"],
            window._saved_workspace_paths,
        )
        self.assertEqual([1], window._saved_workspaces_listbox.selection_sets)
        self.assertEqual([1], window._saved_workspaces_listbox.activate_calls)
        self.assertEqual([1], window._saved_workspaces_listbox.see_calls)

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

