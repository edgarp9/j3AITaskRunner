from __future__ import annotations

import json
import unittest

from app.messages import build_job_status_message, format_progress_event
from domain import JobStatus
from infra.process_runner import AgentStreamEvent


class RuntimeStatusMessageTests(unittest.TestCase):
    def test_queued_and_running_statuses_do_not_create_supplemental_messages(
        self,
    ) -> None:
        self.assertIsNone(build_job_status_message(JobStatus.QUEUED))
        self.assertIsNone(build_job_status_message(JobStatus.RUNNING))


class ProgressLogMessageTests(unittest.TestCase):
    def test_progress_log_uses_concise_item_text_without_raw_jsonl_payload(self) -> None:
        raw_line = (
            '{"type":"item.completed",'
            '"item":{"id":"item_1","type":"agent_message","text":"전체 진행 메시지"}}\n'
        )

        event = AgentStreamEvent(
            line_number=1,
            event_type="item.completed",
            payload=json.loads(raw_line),
            raw_line=raw_line,
        )

        formatted = format_progress_event(event)
        self.assertEqual("item.completed: 전체 진행 메시지", formatted)
        self.assertNotIn('"text":"전체 진행 메시지"', formatted)
        self.assertNotIn(raw_line.strip(), formatted)

    def test_progress_log_keeps_command_event_short_when_raw_line_is_unavailable(self) -> None:
        event = AgentStreamEvent(
            line_number=3,
            event_type="item.completed",
            payload={
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "python -m compileall .",
                    "aggregated_output": "Compiling files\nDone\n",
                },
            },
        )

        formatted = format_progress_event(event)

        self.assertEqual("item.completed: command_execution: python -m compileall .", formatted)
        self.assertNotIn("aggregated_output", formatted)

    def test_progress_log_keeps_known_lifecycle_events_one_line(self) -> None:
        event = AgentStreamEvent(
            line_number=1,
            event_type="thread.started",
            payload={"type": "thread.started", "thread_id": "thread-1"},
            thread_id="thread-1",
        )

        formatted = format_progress_event(event)

        self.assertEqual("세션 시작: thread-1", formatted)


if __name__ == "__main__":
    unittest.main()
