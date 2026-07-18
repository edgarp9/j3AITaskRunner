from __future__ import annotations

from tests._process_runner_helpers import *

class CodexJsonlParserTests(unittest.TestCase):
    def test_parser_extracts_session_id_and_completion(self) -> None:
        parser = CodexJsonlParser()

        parser.feed_line('{"event":"thread.started","thread":{"id":"thread-abc"}}\n')
        parser.feed_line('{"type":"turn.completed"}\n')

        summary = parser.build_summary()

        self.assertEqual("thread-abc", summary.thread_id)
        self.assertTrue(summary.saw_turn_completed)
        self.assertFalse(summary.has_failure_event)

    def test_parser_accepts_codex_prefixed_session_and_turn_events(self) -> None:
        parser = CodexJsonlParser()

        started_event = parser.feed_line(
            '{"type":"codex.thread.started","thread_id":"thread-prefixed"}\n'
        )
        completed_event = parser.feed_line('{"type":"codex.turn.completed"}\n')

        summary = parser.build_summary()

        self.assertIsNotNone(started_event)
        self.assertIsNotNone(completed_event)
        self.assertEqual("thread.started", started_event.event_type)
        self.assertEqual("turn.completed", completed_event.event_type)
        self.assertEqual("thread-prefixed", summary.thread_id)
        self.assertTrue(summary.saw_turn_completed)

    def test_parser_preserves_raw_line_without_line_break_for_progress_log(self) -> None:
        parser = CodexJsonlParser()
        raw_line = '  {"type":"thread.started","thread_id":"thread-raw"}  \r\n'

        event = parser.feed_line(raw_line)

        self.assertIsNotNone(event)
        self.assertEqual(raw_line.rstrip("\r\n"), event.raw_line)

    def test_parser_records_turn_failed_and_error_events(self) -> None:
        parser = CodexJsonlParser()

        parser.feed_line('{"type":"error","message":"plugin sync failed"}\n')
        parser.feed_line('{"turn.failed":{"message":"model aborted"}}\n')

        summary = parser.build_summary()

        self.assertTrue(summary.has_failure_event)
        self.assertTrue(summary.has_error_event)
        self.assertEqual("plugin sync failed", summary.error_events[0].message)
        self.assertEqual("model aborted", summary.turn_failed_events[0].message)
        self.assertEqual(summary.turn_failed_events, summary.failure_events)

    def test_parser_accepts_codex_prefixed_failure_events(self) -> None:
        parser = CodexJsonlParser()

        parser.feed_line('{"codex.error":{"message":"plugin sync failed"}}\n')
        parser.feed_line('{"codex.turn.failed":{"message":"model aborted"}}\n')

        summary = parser.build_summary()

        self.assertTrue(summary.has_failure_event)
        self.assertTrue(summary.has_error_event)
        self.assertEqual("plugin sync failed", summary.error_events[0].message)
        self.assertEqual("model aborted", summary.turn_failed_events[0].message)

class OpenCodeJsonlParserTests(unittest.TestCase):
    def test_parser_accepts_opencode_run_step_events(self) -> None:
        parser = OpenCodeJsonlParser()

        started_event = parser.feed_line(
            '{"type":"step_start","sessionID":"ses_open_1",'
            '"part":{"type":"step-start","sessionID":"ses_open_1"}}\n'
        )
        text_event = parser.feed_line(
            '{"type":"text","sessionID":"ses_open_1",'
            '"part":{"type":"text","text":"Actual OpenCode answer"}}\n'
        )
        tool_finish_event = parser.feed_line(
            '{"type":"step_finish","sessionID":"ses_open_1",'
            '"part":{"type":"step-finish","reason":"tool-calls"}}\n'
        )
        completed_event = parser.feed_line(
            '{"type":"step_finish","sessionID":"ses_open_1",'
            '"part":{"type":"step-finish","reason":"stop"}}\n'
        )

        summary = parser.build_summary()

        self.assertIsNotNone(started_event)
        self.assertIsNotNone(text_event)
        self.assertIsNotNone(tool_finish_event)
        self.assertIsNotNone(completed_event)
        self.assertEqual("thread.started", started_event.event_type)
        self.assertEqual("text", text_event.event_type)
        self.assertEqual("step_finish", tool_finish_event.event_type)
        self.assertEqual("turn.completed", completed_event.event_type)
        self.assertEqual("ses_open_1", summary.thread_id)
        self.assertTrue(summary.saw_turn_completed)
        self.assertEqual("Actual OpenCode answer", summary.last_message)

    def test_parser_extracts_session_id_and_completion_response(self) -> None:
        parser = OpenCodeJsonlParser()

        started_event = parser.feed_line(
            '{"type":"session.started","session":{"id":"session-open-1"}}\n'
        )
        completed_event = parser.feed_line(
            '{"type":"message.completed","message":{"role":"assistant","content":"Final answer"}}\n'
        )

        summary = parser.build_summary()

        self.assertIsNotNone(started_event)
        self.assertIsNotNone(completed_event)
        self.assertEqual("thread.started", started_event.event_type)
        self.assertEqual("turn.completed", completed_event.event_type)
        self.assertEqual("session-open-1", summary.thread_id)
        self.assertTrue(summary.saw_turn_completed)
        self.assertEqual("Final answer", summary.last_message)

    def test_parser_preserves_raw_line_without_line_break_for_progress_log(self) -> None:
        parser = OpenCodeJsonlParser()
        raw_line = '  {"type":"session.started","session":{"id":"session-raw"}}  \r\n'

        event = parser.feed_line(raw_line)

        self.assertIsNotNone(event)
        self.assertEqual(raw_line.rstrip("\r\n"), event.raw_line)

    def test_parser_records_failure_event(self) -> None:
        parser = OpenCodeJsonlParser()

        event = parser.feed_line('{"type":"turn.failed","message":"model aborted"}\n')

        summary = parser.build_summary()

        self.assertIsNotNone(event)
        self.assertEqual("turn.failed", event.event_type)
        self.assertTrue(summary.has_failure_event)
        self.assertEqual("model aborted", summary.turn_failed_events[0].message)

    def test_parser_records_error_event(self) -> None:
        parser = OpenCodeJsonlParser()

        event = parser.feed_line('{"type":"session.error","error":{"message":"rate limited"}}\n')

        summary = parser.build_summary()

        self.assertIsNotNone(event)
        self.assertEqual("error", event.event_type)
        self.assertTrue(summary.has_error_event)
        self.assertEqual("rate limited", summary.error_events[0].message)

    def test_parser_records_malformed_line_and_continues(self) -> None:
        parser = OpenCodeJsonlParser()

        malformed_event = parser.feed_line("{not json}\n")
        completed_event = parser.feed_line(
            '{"type":"response.completed","response":"Recovered answer"}\n'
        )

        summary = parser.build_summary()

        self.assertIsNone(malformed_event)
        self.assertIsNotNone(completed_event)
        self.assertEqual((1,), summary.malformed_lines)
        self.assertTrue(summary.saw_turn_completed)
        self.assertEqual("Recovered answer", summary.last_message)

class ClaudeCodeJsonlParserTests(unittest.TestCase):
    def test_parser_extracts_session_id_and_stream_json_completion(self) -> None:
        parser = ClaudeCodeJsonlParser()

        started_event = parser.feed_line(
            '{"type":"system","subtype":"init","session_id":"session-claude-1"}\n'
        )
        completed_event = parser.feed_line(
            '{"type":"result","subtype":"success","result":"Claude final answer",'
            '"session_id":"session-claude-1"}\n'
        )

        summary = parser.build_summary()

        self.assertIsNotNone(started_event)
        self.assertIsNotNone(completed_event)
        self.assertEqual("thread.started", started_event.event_type)
        self.assertEqual("turn.completed", completed_event.event_type)
        self.assertEqual("session-claude-1", summary.thread_id)
        self.assertTrue(summary.saw_turn_completed)
        self.assertEqual("Claude final answer", summary.last_message)

    def test_parser_extracts_session_id_from_result_when_init_is_missing(self) -> None:
        parser = ClaudeCodeJsonlParser()

        parser.feed_line(
            '{"type":"result","subtype":"success","result":"Done",'
            '"session_id":"session-from-result"}\n'
        )

        summary = parser.build_summary()

        self.assertEqual("session-from-result", summary.thread_id)
        self.assertTrue(summary.saw_turn_completed)

    def test_parser_preserves_raw_line_without_line_break_for_progress_log(self) -> None:
        parser = ClaudeCodeJsonlParser()
        raw_line = '  {"type":"system","subtype":"init","session_id":"session-raw"}  \r\n'

        event = parser.feed_line(raw_line)

        self.assertIsNotNone(event)
        self.assertEqual(raw_line.rstrip("\r\n"), event.raw_line)

    def test_parser_records_failure_event(self) -> None:
        parser = ClaudeCodeJsonlParser()

        event = parser.feed_line(
            '{"type":"result","subtype":"error_during_execution",'
            '"error":{"message":"permission denied"}}\n'
        )

        summary = parser.build_summary()

        self.assertIsNotNone(event)
        self.assertEqual("turn.failed", event.event_type)
        self.assertTrue(summary.has_failure_event)
        self.assertEqual("permission denied", summary.turn_failed_events[0].message)

    def test_parser_extracts_final_response_from_assistant_content_fixture(self) -> None:
        parser = ClaudeCodeJsonlParser()

        parser.feed_line(
            '{"type":"assistant","message":{"role":"assistant","content":['
            '{"type":"text","text":"Partial assistant answer"}]}}\n'
        )
        parser.feed_line(
            '{"type":"result","subtype":"success","result":"Final result answer"}\n'
        )

        summary = parser.build_summary()

        self.assertTrue(summary.saw_turn_completed)
        self.assertEqual("Final result answer", summary.last_message)

    def test_parser_keeps_unknown_events_and_records_malformed_line(self) -> None:
        parser = ClaudeCodeJsonlParser()

        malformed_event = parser.feed_line("{not json}\n")
        unknown_event = parser.feed_line('{"type":"mystery","message":"still useful"}\n')

        summary = parser.build_summary()

        self.assertIsNone(malformed_event)
        self.assertIsNotNone(unknown_event)
        self.assertEqual("mystery", unknown_event.event_type)
        self.assertEqual("still useful", unknown_event.message)
        self.assertEqual((1,), summary.malformed_lines)
        self.assertFalse(summary.saw_turn_completed)

class PiJsonlParserTests(unittest.TestCase):
    def test_parser_extracts_session_id_and_turn_end_response(self) -> None:
        parser = PiJsonlParser()

        started_event = parser.feed_line(
            '{"type":"session","version":3,"id":"session-pi-1",'
            '"timestamp":"2026-05-27T00:00:00Z","cwd":"C:/Repo/Alpha"}\n'
        )
        completed_event = parser.feed_line(
            '{"type":"turn_end","message":{"role":"assistant","content":['
            '{"type":"text","text":"Pi final answer"}]},"toolResults":[]}\n'
        )

        summary = parser.build_summary()

        self.assertIsNotNone(started_event)
        self.assertIsNotNone(completed_event)
        self.assertEqual("thread.started", started_event.event_type)
        self.assertEqual("turn.completed", completed_event.event_type)
        self.assertEqual("session-pi-1", summary.thread_id)
        self.assertTrue(summary.saw_turn_completed)
        self.assertEqual("Pi final answer", summary.last_message)

    def test_parser_extracts_agent_end_last_assistant_message(self) -> None:
        parser = PiJsonlParser()

        parser.feed_line(
            '{"type":"agent_end","messages":['
            '{"role":"user","content":"hello"},'
            '{"role":"assistant","content":[{"type":"text","text":"Agent done"}]}'
            "]}\n"
        )

        summary = parser.build_summary()

        self.assertTrue(summary.saw_turn_completed)
        self.assertEqual("Agent done", summary.last_message)

    def test_parser_preserves_raw_line_without_line_break_for_progress_log(self) -> None:
        parser = PiJsonlParser()
        raw_line = '  {"type":"session","id":"session-raw"}  \r\n'

        event = parser.feed_line(raw_line)

        self.assertIsNotNone(event)
        self.assertEqual(raw_line.rstrip("\r\n"), event.raw_line)

    def test_parser_records_error_event(self) -> None:
        parser = PiJsonlParser()

        event = parser.feed_line(
            '{"type":"auto_retry_end","success":false,"finalError":"rate limited"}\n'
        )

        summary = parser.build_summary()

        self.assertIsNotNone(event)
        self.assertEqual("error", event.event_type)
        self.assertTrue(summary.has_error_event)
        self.assertEqual("rate limited", summary.error_events[0].message)

    def test_parser_records_malformed_line_and_continues(self) -> None:
        parser = PiJsonlParser()

        malformed_event = parser.feed_line("{not json}\n")
        completed_event = parser.feed_line(
            '{"type":"message_end","message":{"role":"assistant","content":"Recovered"}}\n'
        )
        parser.feed_line('{"type":"agent_end","messages":[]}\n')

        summary = parser.build_summary()

        self.assertIsNone(malformed_event)
        self.assertIsNotNone(completed_event)
        self.assertEqual((1,), summary.malformed_lines)
        self.assertTrue(summary.saw_turn_completed)
        self.assertEqual("Recovered", summary.last_message)

class CodexPopenOptionsTests(unittest.TestCase):
    def test_windows_options_hide_console_window(self) -> None:
        kwargs = process_runner._build_codex_popen_kwargs(
            r"C:\Repo\Alpha",
            os_name="nt",
        )

        self.assertEqual(
            process_runner._WINDOWS_CREATE_NO_WINDOW,
            kwargs["creationflags"],
        )

    def test_non_windows_options_do_not_set_creationflags(self) -> None:
        kwargs = process_runner._build_codex_popen_kwargs(
            "/tmp/repo",
            os_name="posix",
        )

        self.assertNotIn("creationflags", kwargs)
        self.assertTrue(kwargs["start_new_session"])

    def test_popen_options_inject_ci_and_no_color_without_dropping_environment(self) -> None:
        with mock.patch.dict(process_runner.os.environ, {"PATH": "C:\\Tools"}, clear=True):
            kwargs = process_runner._build_codex_popen_kwargs(
                r"C:\Repo\Alpha",
                os_name="nt",
            )

        env = kwargs["env"]
        self.assertEqual("C:\\Tools", env["PATH"])
        self.assertEqual("1", env["CI"])
        self.assertEqual("1", env["NO_COLOR"])

class ProcessTreeTerminationTests(unittest.TestCase):
    def test_windows_process_tree_terminate_uses_taskkill_tree_without_force(self) -> None:
        run = mock.Mock(return_value=subprocess.CompletedProcess(args=(), returncode=0))

        result = process_runner._kill_windows_process_tree(4321, force=False, run=run)

        self.assertTrue(result)
        run.assert_called_once_with(
            ("taskkill", "/PID", "4321", "/T"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=process_runner._WINDOWS_TASKKILL_TIMEOUT_SECONDS,
            check=False,
        )

    def test_windows_process_tree_kill_uses_taskkill_tree_force(self) -> None:
        run = mock.Mock(return_value=subprocess.CompletedProcess(args=(), returncode=0))

        result = process_runner._kill_windows_process_tree(4321, force=True, run=run)

        self.assertTrue(result)
        run.assert_called_once_with(
            ("taskkill", "/PID", "4321", "/T", "/F"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=process_runner._WINDOWS_TASKKILL_TIMEOUT_SECONDS,
            check=False,
        )

    def test_windows_terminate_path_calls_process_tree_helper_before_single_process(self) -> None:
        fake_process = mock.Mock()
        fake_process.pid = 4321

        with mock.patch(
            "infra.process_runner._kill_windows_process_tree",
            return_value=True,
        ) as kill_tree:
            process_runner._terminate_process_tree(fake_process, force=False, os_name="nt")

        kill_tree.assert_called_once_with(4321, force=False)
        fake_process.terminate.assert_not_called()

    def test_windows_terminate_fallback_kills_recorded_descendants(self) -> None:
        fake_process = mock.Mock()
        fake_process.pid = 4321

        with mock.patch(
            "infra.process_runner._collect_windows_descendant_pids",
            return_value=(5000, 5001),
        ):
            with mock.patch(
                "infra.process_runner._kill_windows_process_tree",
                side_effect=(False, True, True),
            ) as kill_tree:
                process_runner._terminate_process_tree(fake_process, force=False, os_name="nt")

        self.assertEqual(
            [
                mock.call(4321, force=False),
                mock.call(5001, force=True),
                mock.call(5000, force=True),
            ],
            kill_tree.mock_calls,
        )
        fake_process.terminate.assert_called_once_with()

