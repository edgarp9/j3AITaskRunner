from __future__ import annotations

from tests._main_window_helpers import *
from app.runtime import FileDropIssueEvent

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

    def test_due_schedule_shared_queue_starts_once(self) -> None:
        first_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="first",
            status=JobStatus.QUEUED,
        )
        second_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-2",
            session_tab_id="session-2",
            prompt="second",
            status=JobStatus.QUEUED,
        )
        runtime = _ScheduledRunRuntimeStub(
            jobs=(first_job, second_job),
            open_workspace_ids=("workspace-1", "workspace-2"),
            settings=AppSettings(queue_mode="shared", ui_language="ko"),
        )
        window = _ScheduledRunWindowStub(runtime)
        window._scheduled_run_at = datetime.now() - timedelta(minutes=1)

        MainWindow._on_scheduled_run_timer(window)

        self.assertEqual(["workspace-1"], runtime.background_starts)
        self.assertEqual({"workspace-1"}, window._queue_start_pending_workspace_ids)
        self.assertEqual(
            ["W1 큐 시작 중", "예약실행으로 워크스페이스 1개 큐를 시작했습니다."],
            window.status_messages,
        )

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


class MainWindowFileDropQueueStartTests(unittest.TestCase):
    def test_file_drop_shared_queue_starts_once(self) -> None:
        first_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="first",
            status=JobStatus.QUEUED,
        )
        second_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-2",
            session_tab_id="session-2",
            prompt="second",
            status=JobStatus.QUEUED,
        )
        runtime = _ScheduledRunRuntimeStub(
            jobs=(first_job, second_job),
            open_workspace_ids=("workspace-1", "workspace-2"),
            settings=AppSettings(queue_mode="shared", ui_language="ko"),
        )
        window = _ScheduledRunWindowStub(runtime)

        MainWindow._start_file_drop_registered_jobs(window, "1234567890")

        self.assertEqual(["workspace-1"], runtime.background_starts)
        self.assertEqual({"workspace-1"}, window._queue_start_pending_workspace_ids)
        self.assertEqual(
            [
                "W1 큐 시작 중",
                "파일 드롭 요청으로 워크스페이스 1개 큐를 시작했습니다.",
            ],
            window.status_messages,
        )

    def test_file_drop_per_workspace_starts_all_open_runnable_queues(self) -> None:
        first_job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="first",
            status=JobStatus.QUEUED,
        )
        second_job = Job(
            job_id="job-2",
            workspace_tab_id="workspace-2",
            session_tab_id="session-2",
            prompt="second",
            status=JobStatus.QUEUED,
        )
        waiting_job = Job(
            job_id="job-3",
            workspace_tab_id="workspace-3",
            session_tab_id="session-3",
            prompt="waiting",
            status=JobStatus.WAITING_FOR_CONFIGURATION,
        )
        closed_workspace_job = Job(
            job_id="job-4",
            workspace_tab_id="workspace-4",
            session_tab_id="session-4",
            prompt="closed",
            status=JobStatus.QUEUED,
        )
        runtime = _ScheduledRunRuntimeStub(
            jobs=(first_job, second_job, waiting_job, closed_workspace_job),
            open_workspace_ids=("workspace-1", "workspace-2", "workspace-3"),
        )
        window = _ScheduledRunWindowStub(runtime)

        MainWindow._start_file_drop_registered_jobs(window, "1234567890")

        self.assertEqual(["workspace-1", "workspace-2"], runtime.background_starts)
        self.assertEqual(
            {"workspace-1", "workspace-2"},
            window._queue_start_pending_workspace_ids,
        )
        self.assertEqual(
            [
                "W1 큐 시작 중",
                "W2 큐 시작 중",
                "파일 드롭 요청으로 워크스페이스 2개 큐를 시작했습니다.",
            ],
            window.status_messages,
        )

    def test_file_drop_unknown_command_status_is_localized(self) -> None:
        runtime = _ScheduledRunRuntimeStub(jobs=(), open_workspace_ids=())
        window = _ScheduledRunWindowStub(runtime)
        event = FileDropIssueEvent(
            code="unknown_command_type",
            message="지원하지 않는 파일 드롭 command type입니다.",
            detail="unknown",
        )

        message = MainWindow._file_drop_issue_status_message(window, event)

        self.assertEqual(
            "파일 드롭 요청 실패: 지원하지 않는 command type: unknown",
            message,
        )
