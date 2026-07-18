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
    def test_progress_log_uses_raw_jsonl_line_when_available(self) -> None:
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
        self.assertEqual(raw_line.rstrip("\r\n"), formatted)
        self.assertIn('"text":"전체 진행 메시지"', formatted)

    def test_progress_log_serializes_full_payload_when_raw_line_is_unavailable(self) -> None:
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

        self.assertEqual(json.dumps(event.payload, ensure_ascii=False), formatted)
        self.assertIn("aggregated_output", formatted)
        self.assertIn("Compiling files\\nDone\\n", formatted)

    def test_progress_log_does_not_truncate_long_raw_line(self) -> None:
        long_text = "x" * 240
        raw_line = json.dumps({"type": "progress", "text": long_text}, ensure_ascii=False)
        event = AgentStreamEvent(
            line_number=1,
            event_type="progress",
            payload=json.loads(raw_line),
            raw_line=raw_line,
        )

        formatted = format_progress_event(event)

        self.assertEqual(raw_line, formatted)
        self.assertIn(long_text, formatted)


if __name__ == "__main__":
    unittest.main()
