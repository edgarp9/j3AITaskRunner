from __future__ import annotations

from tests._main_window_helpers import *


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

    def test_immediate_run_button_enabled_for_normal_session_without_active_jobs(
        self,
    ) -> None:
        window = _SessionSelectionWindowStub((), selected_job_id=None)
        window.session_widgets.immediate_run_button = _ButtonConfigureStub()

        MainWindow._refresh_immediate_run_button(window, "session-1")

        self.assertEqual("normal", window.session_widgets.immediate_run_button.state)

    def test_immediate_run_button_disabled_for_active_and_preset_sessions(self) -> None:
        for status in (
            JobStatus.QUEUED,
            JobStatus.WAITING_FOR_CONFIGURATION,
            JobStatus.RUNNING,
        ):
            with self.subTest(status=status):
                job = Job(
                    job_id="job-1",
                    workspace_tab_id="workspace-1",
                    session_tab_id="session-1",
                    prompt="prompt",
                    status=status,
                )
                window = _SessionSelectionWindowStub((job,), selected_job_id=None)
                window.session_widgets.immediate_run_button = _ButtonConfigureStub()

                MainWindow._refresh_immediate_run_button(window, "session-1")

                self.assertEqual(
                    "disabled",
                    window.session_widgets.immediate_run_button.state,
                )

        preset_window = _SessionSelectionWindowStub(
            (),
            selected_job_id=None,
            session_kind=SessionTabKind.PRESET,
        )
        preset_window.session_widgets.immediate_run_button = _ButtonConfigureStub()

        MainWindow._refresh_immediate_run_button(preset_window, "session-1")

        self.assertEqual(
            "disabled",
            preset_window.session_widgets.immediate_run_button.state,
        )

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

    def test_workspace_task_selection_refreshes_log_without_switching_body_tab(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="queued",
            status=JobStatus.QUEUED,
        )
        window = _WorkspaceJobSelectionWindowStub(job)

        MainWindow._select_workspace_job(window, "workspace-1", "job-1")

        self.assertEqual("job-1", window.session_widgets.selected_job_id)
        self.assertEqual([("workspace-1", "session-1")], window.selected_session_ids)
        self.assertEqual(["session-1"], window.refreshed_summary_ids)
        self.assertEqual(["session-1"], window.refreshed_output_ids)
        self.assertEqual([], window.session_widgets.body_notebook.selected_tabs)

