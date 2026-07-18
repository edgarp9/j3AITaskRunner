from __future__ import annotations

from tests._main_window_helpers import *


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

    def test_progress_log_refresh_applies_escaped_line_breaks(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        raw_line = '{"message":"Line 1\\r\\nLine 2","path":"C:\\\\new"}'
        window = _SessionSelectionWindowStub(
            (job,),
            selected_job_id="job-1",
            progress_logs={"job-1": (raw_line,)},
        )

        MainWindow._refresh_session_output(window, "session-1")

        self.assertEqual(
            '{"message":"Line 1\nLine 2","path":"C:\\\\new"}',
            window.session_widgets.log_text.content,
        )

    def test_progress_log_append_applies_escaped_line_breaks(self) -> None:
        job = Job(
            job_id="job-1",
            workspace_tab_id="workspace-1",
            session_tab_id="session-1",
            prompt="running",
            status=JobStatus.RUNNING,
        )
        output_append = type(
            "OutputAppend",
            (),
            {"job_id": "job-1", "lines": ['{"message":"Line 1\\nLine 2"}']},
        )()
        window = _SessionSelectionWindowStub((job,), selected_job_id="job-1")
        window.session_widgets.rendered_log_job_id = "job-1"
        window.session_widgets.rendered_log_language = "ko"

        MainWindow._refresh_session_output(
            window,
            "session-1",
            output_append=output_append,
        )

        self.assertEqual(
            '{"message":"Line 1\nLine 2"}',
            window.session_widgets.log_text.content,
        )

class MainWindowSessionHistoryTests(unittest.TestCase):
    def test_history_rendering_keeps_prompt_and_response_turn_format(self) -> None:
        running_turn = _HistoryTurnStub(
            started_at=_history_dt(1),
            completed_at=None,
            prompt_text="running prompt",
            response_text=None,
        )
        completed_turn = _HistoryTurnStub(
            started_at=_history_dt(2),
            completed_at=_history_dt(3),
            prompt_text="completed prompt",
            response_text="completed response",
        )

        running_history = render_session_history_turns(
            (running_turn,),
            start_index=1,
            language="ko",
            content_length=0,
        )[0][1]
        full_history = join_session_history_blocks(
            render_session_history_turns(
                (running_turn, completed_turn),
                start_index=1,
                language="ko",
                content_length=0,
            )
        )

        self.assertIn("Prompt:\nrunning prompt", running_history)
        self.assertNotIn("Response:", running_history)
        self.assertIn("Prompt:\ncompleted prompt", full_history)
        self.assertIn("Response:\ncompleted response", full_history)

    def test_history_rendering_shows_failed_turn_error(self) -> None:
        failed_turn = _HistoryTurnStub(
            started_at=_history_dt(1),
            completed_at=_history_dt(2),
            prompt_text="failed prompt",
            response_text=None,
            error_text="invalid request",
        )

        rendered_history = render_session_history_turns(
            (failed_turn,),
            start_index=1,
            language="ko",
            content_length=0,
        )[0][1]

        self.assertIn("Prompt:\nfailed prompt", rendered_history)
        self.assertNotIn("Response:", rendered_history)
        self.assertIn("Error:\ninvalid request", rendered_history)

    def test_rendered_history_turns_cache_content_end_offsets(self) -> None:
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

        rendered_history = render_session_history_turns(
            turns,
            start_index=1,
            language="ko",
            content_length=0,
        )
        rendered_turns = tuple(
            rendered_turn for rendered_turn, _block_text in rendered_history
        )
        joined_history = join_session_history_blocks(rendered_history)

        self.assertEqual(
            len(rendered_history[0][1]),
            session_history_prefix_length(rendered_turns, 1),
        )
        self.assertEqual(
            len(joined_history),
            session_history_prefix_length(rendered_turns, 2),
        )

