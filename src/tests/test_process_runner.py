from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from domain import AgentExecutionOptions, AppSettings
from domain.models import (
    EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
    TERMINATION_GRACE_SECONDS_MAX,
)
from infra import process_runner
from infra.claude_code_jsonl import ClaudeCodeJsonlParser
from infra.codex_jsonl import CodexJsonlParser
from infra.open_code_jsonl import OpenCodeJsonlParser
from infra.pi_jsonl import PiJsonlParser
from infra.process_runner import (
    ClaudeCodeCliProcessRunner,
    ClaudeCodeRunRequest,
    CodexCliProcessRunner,
    CodexRunRequest,
    AgentRunStatus,
    KiloCodeCliProcessRunner,
    KiloCodeRunRequest,
    OpenCodeCliProcessRunner,
    OpenCodeRunRequest,
    PiCliProcessRunner,
    PiRunRequest,
    build_claude_code_command,
    build_codex_command,
    build_kilo_code_command,
    build_opencode_command,
    build_pi_command,
)

_RUN_REAL_AGENT_SMOKE = os.environ.get("J3AITASKRUNNER_RUN_REAL_AGENT_SMOKE") == "1"


class CodexCommandBuilderTests(unittest.TestCase):
    def test_build_codex_command_for_initial_execution(self) -> None:
        request = CodexRunRequest(
            job_id="job-1",
            workspace_path=r"C:\Repo\Alpha",
            prompt="hello",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\codex.exe",
            ),
            execution_options=AgentExecutionOptions(
                model="gpt-5.4",
                reasoning_effort="high",
            ),
            session_id=None,
        )

        command = build_codex_command(
            request,
            last_message_path=r"C:\artifacts\job-1\last_message.txt",
        )

        self.assertEqual(
            (
                r"C:\Tools\codex.exe",
                "exec",
                "--json",
                "--skip-git-repo-check",
                "-C",
                r"C:\Repo\Alpha",
                "-m",
                "gpt-5.4",
                "-c",
                'model_reasoning_effort="high"',
                "-o",
                r"C:\artifacts\job-1\last_message.txt",
                "-",
            ),
            command,
        )

    def test_build_codex_command_for_resume_execution(self) -> None:
        request = CodexRunRequest(
            job_id="job-2",
            workspace_path=r"C:\Repo\Alpha",
            prompt="follow up",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\codex.exe",
            ),
            session_id="thread-123",
        )

        command = build_codex_command(
            request,
            last_message_path=r"C:\artifacts\job-2\last_message.txt",
        )

        self.assertEqual(
            (
                r"C:\Tools\codex.exe",
                "exec",
                "resume",
                "--json",
                "--skip-git-repo-check",
                "thread-123",
                "-o",
                r"C:\artifacts\job-2\last_message.txt",
                "-",
            ),
            command,
        )
        self.assertNotIn("-C", command)
        self.assertNotIn("--ephemeral", command)

    def test_build_codex_command_resolves_path_command_name(self) -> None:
        request = CodexRunRequest(
            job_id="job-1",
            workspace_path="/repo/alpha",
            prompt="hello",
            operational_settings=AppSettings(executable_path="codex"),
            session_id=None,
        )

        with mock.patch("infra.executable.shutil.which", return_value="/usr/local/bin/codex"):
            command = build_codex_command(
                request,
                last_message_path="/artifacts/job-1/last_message.txt",
            )

        self.assertEqual(str(Path("/usr/local/bin/codex").resolve()), command[0])

    def test_build_codex_command_resolves_codex_cli_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "codex-x86_64-unknown-linux-musl"
            executable.write_text("", encoding="utf-8")
            executable.chmod(0o755)
            request = CodexRunRequest(
                job_id="job-1",
                workspace_path="/repo/alpha",
                prompt="hello",
                operational_settings=AppSettings(executable_path=temp_dir),
                session_id=None,
            )

            command = build_codex_command(
                request,
                last_message_path="/artifacts/job-1/last_message.txt",
            )

        self.assertEqual(str(executable.resolve()), command[0])


class OpenCodeFamilyCommandBuilderTests(unittest.TestCase):
    def test_provider_registry_builds_opencode_and_kilo_adapters(self) -> None:
        opencode_adapter = process_runner.build_agent_cli_adapter("opencode")
        kilo_adapter = process_runner.build_agent_cli_adapter("kilo_code")

        self.assertIsNotNone(opencode_adapter)
        self.assertIsNotNone(kilo_adapter)
        self.assertEqual("opencode", opencode_adapter.provider_id)
        self.assertEqual("kilo_code", kilo_adapter.provider_id)

    def test_build_opencode_command_for_initial_execution(self) -> None:
        request = OpenCodeRunRequest(
            job_id="job-open-1",
            workspace_path=r"C:\Repo\Alpha",
            prompt="hello from opencode",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\opencode.exe",
                agent_provider="opencode",
            ),
            session_id=None,
        )

        command = build_opencode_command(
            request,
            last_message_path=r"C:\artifacts\job-open-1\last_message.txt",
        )

        self.assertEqual(
            (
                r"C:\Tools\opencode.exe",
                "run",
                "--format",
                "json",
                "--dir",
                r"C:\Repo\Alpha",
                "hello from opencode",
            ),
            command,
        )
        self.assertNotIn("--dangerously-skip-permissions", command)
        self.assertNotIn("--auto", command)

    def test_build_opencode_command_for_session_resume(self) -> None:
        request = OpenCodeRunRequest(
            job_id="job-open-2",
            workspace_path=r"C:\Repo\Alpha",
            prompt="follow up",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\opencode.exe",
                agent_provider="opencode",
            ),
            session_id="session-open-123",
        )

        command = build_opencode_command(
            request,
            last_message_path=r"C:\artifacts\job-open-2\last_message.txt",
        )

        self.assertEqual(
            (
                r"C:\Tools\opencode.exe",
                "run",
                "--format",
                "json",
                "--dir",
                r"C:\Repo\Alpha",
                "--session",
                "session-open-123",
                "follow up",
            ),
            command,
        )

    def test_build_kilo_command_for_initial_execution(self) -> None:
        request = KiloCodeRunRequest(
            job_id="job-kilo-1",
            workspace_path=r"C:\Repo\Alpha",
            prompt="hello from kilo",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\kilo.exe",
                agent_provider="kilo_code",
            ),
            session_id=None,
        )

        command = build_kilo_code_command(
            request,
            last_message_path=r"C:\artifacts\job-kilo-1\last_message.txt",
        )

        self.assertEqual(
            (
                r"C:\Tools\kilo.exe",
                "run",
                "--format",
                "json",
                "--dir",
                r"C:\Repo\Alpha",
                "hello from kilo",
            ),
            command,
        )
        self.assertNotIn("--dangerously-skip-permissions", command)
        self.assertNotIn("--auto", command)

    def test_build_kilo_command_for_session_resume(self) -> None:
        request = KiloCodeRunRequest(
            job_id="job-kilo-2",
            workspace_path=r"C:\Repo\Alpha",
            prompt="follow up",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\kilo.exe",
                agent_provider="kilo_code",
            ),
            session_id="session-kilo-123",
        )

        command = build_kilo_code_command(
            request,
            last_message_path=r"C:\artifacts\job-kilo-2\last_message.txt",
        )

        self.assertEqual(
            (
                r"C:\Tools\kilo.exe",
                "run",
                "--format",
                "json",
                "--dir",
                r"C:\Repo\Alpha",
                "--session",
                "session-kilo-123",
                "follow up",
            ),
            command,
        )

    def test_build_kilo_command_applies_model_and_variant(self) -> None:
        request = KiloCodeRunRequest(
            job_id="job-kilo-model",
            workspace_path=r"C:\Repo\Alpha",
            prompt="use configured kilo model",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\kilo.exe",
                agent_provider="kilo_code",
            ),
            execution_options=AgentExecutionOptions(
                agent_provider="kilo_code",
                model="anthropic/claude-sonnet-4-5",
                reasoning_effort="high",
            ),
            session_id=None,
        )

        command = build_kilo_code_command(
            request,
            last_message_path=r"C:\artifacts\job-kilo-model\last_message.txt",
        )

        self.assertIn("--model", command)
        self.assertEqual(
            "anthropic/claude-sonnet-4-5",
            command[command.index("--model") + 1],
        )
        self.assertIn("--variant", command)
        self.assertEqual("high", command[command.index("--variant") + 1])
        self.assertEqual("use configured kilo model", command[-1])

    def test_build_opencode_command_applies_model_and_variant(self) -> None:
        request = OpenCodeRunRequest(
            job_id="job-open-model",
            workspace_path=r"C:\Repo\Alpha",
            prompt="use configured model",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\opencode.exe",
                agent_provider="opencode",
            ),
            execution_options=AgentExecutionOptions(
                agent_provider="opencode",
                model="openai/gpt-5.4",
                reasoning_effort="high",
            ),
            session_id=None,
        )

        command = build_opencode_command(
            request,
            last_message_path=r"C:\artifacts\job-open-model\last_message.txt",
        )

        self.assertIn("--model", command)
        self.assertIn("openai/gpt-5.4", command)
        self.assertIn("--variant", command)
        self.assertIn("high", command)
        self.assertEqual("use configured model", command[-1])


class ClaudeCodeCommandBuilderTests(unittest.TestCase):
    def test_provider_registry_builds_claude_code_adapter(self) -> None:
        adapter = process_runner.build_agent_cli_adapter("claude_code")

        self.assertIsNotNone(adapter)
        self.assertEqual("claude_code", adapter.provider_id)

    def test_build_claude_code_command_for_initial_execution(self) -> None:
        request = ClaudeCodeRunRequest(
            job_id="job-claude-1",
            workspace_path=r"C:\Repo\Alpha",
            prompt="hello from claude",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\claude.exe",
                agent_provider="claude_code",
            ),
            session_id=None,
        )

        command = build_claude_code_command(
            request,
            last_message_path=r"C:\artifacts\job-claude-1\last_message.txt",
        )

        self.assertEqual(
            (
                r"C:\Tools\claude.exe",
                "-p",
                "hello from claude",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
            ),
            command,
        )
        self.assertNotIn("--dangerously-skip-permissions", command)
        self.assertNotIn("bypassPermissions", command)

    def test_build_claude_code_command_for_session_resume(self) -> None:
        request = ClaudeCodeRunRequest(
            job_id="job-claude-2",
            workspace_path=r"C:\Repo\Alpha",
            prompt="follow up",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\claude.exe",
                agent_provider="claude_code",
            ),
            session_id="session-claude-123",
        )

        command = build_claude_code_command(
            request,
            last_message_path=r"C:\artifacts\job-claude-2\last_message.txt",
        )

        self.assertIn("--resume", command)
        self.assertEqual("session-claude-123", command[command.index("--resume") + 1])
        self.assertNotIn("-r", command)

    def test_build_claude_code_command_applies_model(self) -> None:
        request = ClaudeCodeRunRequest(
            job_id="job-claude-model",
            workspace_path=r"C:\Repo\Alpha",
            prompt="use configured model",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\claude.exe",
                agent_provider="claude_code",
            ),
            execution_options=AgentExecutionOptions(
                agent_provider="claude_code",
                model="claude-sonnet-4-5",
            ),
            session_id=None,
        )

        command = build_claude_code_command(
            request,
            last_message_path=r"C:\artifacts\job-claude-model\last_message.txt",
        )

        self.assertIn("--model", command)
        self.assertEqual("claude-sonnet-4-5", command[command.index("--model") + 1])


class PiCommandBuilderTests(unittest.TestCase):
    def test_provider_registry_builds_pi_adapter(self) -> None:
        adapter = process_runner.build_agent_cli_adapter("pi")

        self.assertIsNotNone(adapter)
        self.assertEqual("pi", adapter.provider_id)

    def test_build_pi_command_for_initial_execution(self) -> None:
        request = PiRunRequest(
            job_id="job-pi-1",
            workspace_path=r"C:\Repo\Alpha",
            prompt="hello from pi",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\pi.exe",
                agent_provider="pi",
            ),
            session_id=None,
        )

        command = build_pi_command(
            request,
            last_message_path=r"C:\artifacts\job-pi-1\last_message.txt",
        )

        self.assertEqual(
            (
                r"C:\Tools\pi.exe",
                "--mode",
                "json",
                "hello from pi",
            ),
            command,
        )
        self.assertNotIn("--tools", command)
        self.assertNotIn("--no-tools", command)

    def test_build_pi_command_for_session_resume(self) -> None:
        request = PiRunRequest(
            job_id="job-pi-2",
            workspace_path=r"C:\Repo\Alpha",
            prompt="follow up",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\pi.exe",
                agent_provider="pi",
            ),
            session_id="session-pi-123",
        )

        command = build_pi_command(
            request,
            last_message_path=r"C:\artifacts\job-pi-2\last_message.txt",
        )

        self.assertIn("--session", command)
        self.assertEqual("session-pi-123", command[command.index("--session") + 1])
        self.assertNotIn("--continue", command)
        self.assertNotIn("--fork", command)

    def test_build_pi_command_applies_model_and_thinking(self) -> None:
        request = PiRunRequest(
            job_id="job-pi-model",
            workspace_path=r"C:\Repo\Alpha",
            prompt="use configured model",
            operational_settings=AppSettings(
                executable_path=r"C:\Tools\pi.exe",
                agent_provider="pi",
            ),
            execution_options=AgentExecutionOptions(
                agent_provider="pi",
                model="openai/gpt-4o",
                reasoning_effort="none",
            ),
            session_id=None,
        )

        command = build_pi_command(
            request,
            last_message_path=r"C:\artifacts\job-pi-model\last_message.txt",
        )

        self.assertIn("--model", command)
        self.assertEqual("openai/gpt-4o", command[command.index("--model") + 1])
        self.assertIn("--thinking", command)
        self.assertEqual("off", command[command.index("--thinking") + 1])
        self.assertEqual("use configured model", command[-1])


class AppSettingsExecutionControlValidationTests(unittest.TestCase):
    def test_rejects_execution_control_values_above_domain_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "execution_timeout_minutes"):
            AppSettings(
                execution_timeout_minutes=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX + 1,
            )

        with self.assertRaisesRegex(ValueError, "inactivity_timeout_minutes"):
            AppSettings(
                inactivity_timeout_minutes=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX + 1,
            )

        with self.assertRaisesRegex(ValueError, "termination_grace_seconds"):
            AppSettings(
                termination_grace_seconds=TERMINATION_GRACE_SECONDS_MAX + 1,
            )


class ExecutionControlConversionTests(unittest.TestCase):
    def test_minutes_to_seconds_clamps_values_above_domain_limit(self) -> None:
        with self.assertLogs("infra.process_runner", level="WARNING"):
            seconds = process_runner._minutes_to_seconds(10**1000)

        self.assertEqual(float(EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX * 60), seconds)

    def test_termination_timeout_clamps_values_above_domain_limit(self) -> None:
        settings = mock.Mock(termination_grace_seconds=10**1000)

        with self.assertLogs("infra.process_runner", level="WARNING"):
            seconds = process_runner._termination_timeout_from_settings(settings)

        self.assertEqual(float(TERMINATION_GRACE_SECONDS_MAX), seconds)


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


class CodexCliProcessRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.runner_root = Path(self.temp_dir.name)
        self.request = CodexRunRequest(
            job_id="job-1",
            workspace_path=self.temp_dir.name,
            prompt="Solve this task.",
            operational_settings=AppSettings(
                executable_path=str(_create_fake_executable(self.runner_root, "codex.exe")),
            ),
            execution_options=AgentExecutionOptions(
                model="gpt-5.4",
                reasoning_effort="high",
            ),
            session_id=None,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_marks_success_with_completed_turn_and_last_message(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                stderr_lines=("warning: diagnostic only\n",),
                exit_code=0,
                last_message_text="Final answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertTrue(result.success)
        self.assertEqual("thread-123", result.session_id)
        self.assertEqual("Final answer", result.last_message)
        self.assertEqual("Solve this task.", result.artifacts.prompt_path.read_text(encoding="utf-8"))
        self.assertIn("turn.completed", result.artifacts.stdout_jsonl_path.read_text(encoding="utf-8"))
        self.assertIn("diagnostic only", result.artifacts.stderr_log_path.read_text(encoding="utf-8"))

        metadata = json.loads(result.artifacts.launch_metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", metadata["result_status"])
        self.assertEqual("thread-123", metadata["resolved_session_id"])
        self.assertEqual(str(Path(self.request.workspace_path).resolve()), metadata["process_cwd"])
        self.assertEqual(
            str(Path(self.request.workspace_path).resolve()),
            factory.kwargs_calls[0]["cwd"],
        )
        expected_creationflags = process_runner._hidden_console_creationflags()
        if expected_creationflags:
            self.assertEqual(
                expected_creationflags,
                factory.kwargs_calls[0]["creationflags"],
            )
        else:
            self.assertNotIn("creationflags", factory.kwargs_calls[0])

        process = factory.instances[0]
        self.assertEqual("Solve this task.", process.stdin.content)
        self.assertTrue(process.stdin.closed)

    def test_run_with_file_logging_disabled_keeps_ui_parsing_without_log_artifacts(self) -> None:
        request = CodexRunRequest(
            job_id="job-no-file-log",
            workspace_path=self.temp_dir.name,
            prompt="Solve without files.",
            operational_settings=AppSettings(
                executable_path=self.request.operational_settings.executable_path,
                file_logging_enabled=False,
            ),
            execution_options=AgentExecutionOptions(
                model="gpt-5.4",
                reasoning_effort="high",
            ),
            session_id=None,
        )
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-no-file"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                stderr_lines=("diagnostic not saved\n",),
                exit_code=0,
                last_message_text="Final answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("thread-no-file", result.session_id)
        self.assertEqual("Final answer", result.last_message)
        self.assertFalse((self.runner_root / "artifacts").exists())
        self.assertFalse(result.artifacts.root_dir.exists())
        self.assertFalse(result.artifacts.prompt_path.exists())
        self.assertFalse(result.artifacts.stdout_jsonl_path.exists())
        self.assertFalse(result.artifacts.stderr_log_path.exists())
        self.assertFalse(result.artifacts.launch_metadata_path.exists())
        self.assertFalse(result.artifacts.last_message_path.exists())

    def test_run_keeps_stdout_parsing_when_artifact_file_logging_fails(self) -> None:
        original_open = Path.open

        for failure_mode in ("open", "write", "flush"):
            with self.subTest(failure_mode=failure_mode):
                request = CodexRunRequest(
                    job_id=f"job-artifact-{failure_mode}",
                    workspace_path=self.temp_dir.name,
                    prompt="Solve with artifact failure.",
                    operational_settings=self.request.operational_settings,
                    session_id=None,
                )
                stdout_lines = _stdout_lines_for_artifact_failure(failure_mode)
                factory = _FakePopenFactory(
                    _FakeProcessScenario(
                        stdout_lines=stdout_lines,
                        exit_code=0,
                        last_message_text="Final answer",
                    )
                )
                runner = CodexCliProcessRunner(
                    self.runner_root / "artifacts",
                    popen_factory=factory,
                )
                parsed_event_types: list[str] = []
                forwarded_stdout_lines: list[str] = []

                def fail_stdout_artifact_open(path: Path, *args: object, **kwargs: object):
                    mode = str(args[0] if args else kwargs.get("mode", "r"))
                    if path.name == "stdout.jsonl" and "a" in mode:
                        if failure_mode == "open":
                            raise OSError("artifact open failed")
                        return _FailingArtifactFile(failure_mode)
                    return original_open(path, *args, **kwargs)

                with mock.patch.object(Path, "open", new=fail_stdout_artifact_open):
                    with self.assertLogs("infra.process_runner", level="ERROR") as logs:
                        handle = runner.launch(
                            request,
                            on_stdout_line=forwarded_stdout_lines.append,
                            on_json_event=lambda event: parsed_event_types.append(
                                event.event_type
                            ),
                        )
                        result = handle.wait()

                self.assertEqual(AgentRunStatus.COMPLETED, result.status)
                self.assertEqual(f"thread-{failure_mode}", result.session_id)
                self.assertTrue(result.parser_summary.saw_turn_completed)
                self.assertIn("thread.started", parsed_event_types)
                self.assertIn("turn.completed", parsed_event_types)
                self.assertEqual(stdout_lines, tuple(forwarded_stdout_lines))
                self.assertTrue(
                    any("artifact file" in message for message in logs.output),
                    logs.output,
                )

    def test_validate_allows_resume_without_inspecting_external_session_files(self) -> None:
        request = CodexRunRequest(
            job_id="job-resume",
            workspace_path=self.temp_dir.name,
            prompt="follow up",
            operational_settings=self.request.operational_settings,
            session_id="thread-123",
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts")

        self.assertIsNone(runner.validate(request))

    def test_validate_accepts_path_command_name(self) -> None:
        request = CodexRunRequest(
            job_id="job-command",
            workspace_path=self.temp_dir.name,
            prompt="hello",
            operational_settings=AppSettings(executable_path="codex"),
            session_id=None,
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts")

        with mock.patch("infra.executable.shutil.which", return_value=sys.executable):
            self.assertIsNone(runner.validate(request))

    def test_validate_accepts_codex_cli_directory(self) -> None:
        executable = self.runner_root / "codex-x86_64-unknown-linux-musl"
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o755)
        request = CodexRunRequest(
            job_id="job-directory",
            workspace_path=self.temp_dir.name,
            prompt="hello",
            operational_settings=AppSettings(executable_path=str(self.runner_root)),
            session_id=None,
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts")

        self.assertIsNone(runner.validate(request))

    def test_run_records_resume_mode_without_session_metadata(self) -> None:
        request = CodexRunRequest(
            job_id="job-resume",
            workspace_path=self.temp_dir.name,
            prompt="follow up",
            operational_settings=self.request.operational_settings,
            session_id="thread-123",
        )
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                exit_code=0,
                last_message_text="Follow-up answer",
            )
        )
        runner = CodexCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertNotIn("-C", factory.calls[0])
        self.assertEqual(
            str(Path(self.temp_dir.name).resolve()),
            factory.kwargs_calls[0]["cwd"],
        )
        launch_metadata = json.loads(
            result.artifacts.launch_metadata_path.read_text(encoding="utf-8")
        )
        self.assertEqual("resume", launch_metadata["mode"])
        self.assertEqual("thread-123", launch_metadata["session_id"])
        self.assertNotIn("session_metadata_cwd", launch_metadata)
        self.assertNotIn("session_metadata_path", launch_metadata)

    def test_run_keeps_started_process_when_stdin_close_raises_oserror(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                exit_code=0,
                last_message_text="Final answer",
                stdin_close_error=BrokenPipeError("broken pipe"),
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        with self.assertLogs("infra.process_runner", level="WARNING"):
            result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        process = factory.instances[0]
        self.assertEqual("Solve this task.", process.stdin.content)
        self.assertTrue(process.stdin.closed)
        self.assertFalse(process.terminated)
        self.assertIn(
            "STDIN_CLOSE_ERROR: broken pipe",
            result.artifacts.stderr_log_path.read_text(encoding="utf-8"),
        )

    def test_launch_exposes_handle_before_stdin_write_and_honors_early_cancel(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                exit_code=0,
                last_message_text="Final answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        def cancel_before_stdin_write(handle) -> None:
            handle.terminate(timeout=0)

        handle = runner.launch(self.request, on_handle_created=cancel_before_stdin_write)
        result = handle.wait()

        process = factory.instances[0]
        self.assertEqual(AgentRunStatus.CANCELED, result.status)
        self.assertTrue(process.terminated)
        self.assertEqual("", process.stdin.content)
        self.assertTrue(process.stdin.closed)
        self.assertIn(
            "STDIN_WRITE_CANCELED",
            result.artifacts.stderr_log_path.read_text(encoding="utf-8"),
        )

    def test_run_marks_failed_on_turn_failed_event(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-999"}\n',
                    '{"type":"turn.failed","message":"tool execution failed"}\n',
                ),
                stderr_lines=("warning only\n",),
                exit_code=0,
                last_message_text="ignored",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertEqual("thread-999", result.session_id)
        self.assertEqual("tool execution failed", result.failure_reason)

    def test_run_allows_transient_error_when_turn_completed(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"turn.started"}\n',
                    '{"type":"error","message":"Reconnecting... 2/5 (request timed out)"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                exit_code=0,
                last_message_text="Final answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("Final answer", result.last_message)
        self.assertTrue(result.parser_summary.has_error_event)
        self.assertFalse(result.parser_summary.has_failure_event)
        metadata = json.loads(result.artifacts.launch_metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("completed", metadata["result_status"])
        self.assertTrue(metadata["has_error_event"])
        self.assertFalse(metadata["has_failure_event"])

    def test_run_marks_failed_on_error_without_completed_turn(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-123"}\n',
                    '{"type":"error","message":"model stream failed"}\n',
                ),
                exit_code=0,
                last_message_text="Partial answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertEqual("model stream failed", result.failure_reason)

    def test_run_marks_failed_on_abnormal_exit_without_failure_event(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=('{"type":"thread.started","thread_id":"thread-321"}\n',),
                stderr_lines=("stderr noise\n",),
                exit_code=7,
                last_message_text=None,
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertIn("exit_code=7", result.failure_reason or "")

    def test_run_returns_failed_result_when_process_start_raises_oserror(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                raise_error=OSError("spawn failed"),
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        with self.assertLogs("infra.process_runner", level="ERROR"):
            result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertIn("spawn failed", result.failure_reason or "")
        self.assertIn(
            "PROCESS_START_ERROR",
            result.artifacts.stderr_log_path.read_text(encoding="utf-8"),
        )

    def test_launch_terminates_process_when_launch_metadata_update_fails_after_spawn(self) -> None:
        factory = _FakePopenFactory(_FakeProcessScenario())
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        call_count = 0

        def fail_on_second_write(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("launch metadata rewrite failed")

        with mock.patch("infra.process_runner._write_json_file", side_effect=fail_on_second_write):
            with self.assertRaisesRegex(OSError, "launch metadata rewrite failed"):
                runner.launch(self.request)

        process = factory.instances[0]
        self.assertTrue(process.terminated)
        self.assertTrue(process.stdin.closed)
        self.assertEqual({}, runner._active_handles)

    def test_launch_cleans_up_process_and_active_handle_when_handle_start_fails(self) -> None:
        factory = _FakePopenFactory(_FakeProcessScenario())
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        with mock.patch(
            "infra.process_runner.RunningCodexProcess.start",
            side_effect=RuntimeError("start failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "start failed"):
                runner.launch(self.request)

        process = factory.instances[0]
        self.assertTrue(process.terminated)
        self.assertTrue(process.stdin.closed)
        self.assertEqual({}, runner._active_handles)

    def test_cancel_terminates_running_process_and_marks_result_canceled(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-cancel"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                stderr_lines=(),
                exit_code=0,
                last_message_text="should not win",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        handle = runner.launch(self.request)
        runner.cancel(handle)
        result = handle.wait()

        self.assertEqual(AgentRunStatus.CANCELED, result.status)
        self.assertTrue(factory.instances[0].terminated)
        self.assertEqual("사용자가 실행을 취소했습니다.", result.failure_reason)
        self.assertNotIn("시간 제한", result.failure_reason or "")

    def test_wait_returns_failed_when_execution_timeout_expires(self) -> None:
        request = CodexRunRequest(
            job_id="job-timeout",
            workspace_path=self.temp_dir.name,
            prompt="Will time out.",
            operational_settings=AppSettings(
                executable_path=self.request.operational_settings.executable_path,
                execution_timeout_minutes=1,
                inactivity_timeout_minutes=0,
                termination_grace_seconds=0,
            ),
        )
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                wait_blocks_until_terminated=True,
                last_message_text="ignored",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        handle = runner.launch(request)
        with self.assertLogs("infra.process_runner", level="WARNING"):
            with handle._lock:
                handle._started_monotonic = time.monotonic() - 61
            result = handle.wait()

        process = factory.instances[0]
        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertIn("시간 제한 초과", result.failure_reason or "")
        self.assertIn("전체 실행", result.failure_reason or "")
        self.assertTrue(process.terminated)
        self.assertNotIn(None, process.wait_timeouts)

    def test_wait_returns_failed_when_inactivity_timeout_expires(self) -> None:
        request = CodexRunRequest(
            job_id="job-inactivity-timeout",
            workspace_path=self.temp_dir.name,
            prompt="Will go idle.",
            operational_settings=AppSettings(
                executable_path=self.request.operational_settings.executable_path,
                execution_timeout_minutes=0,
                inactivity_timeout_minutes=1,
                termination_grace_seconds=0,
            ),
        )
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                wait_blocks_until_terminated=True,
                last_message_text="ignored",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        handle = runner.launch(request)
        with self.assertLogs("infra.process_runner", level="WARNING"):
            with handle._lock:
                handle._last_activity_monotonic = time.monotonic() - 61
            result = handle.wait()

        process = factory.instances[0]
        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertIn("시간 제한 초과", result.failure_reason or "")
        self.assertIn("무활동", result.failure_reason or "")
        self.assertTrue(process.terminated)
        self.assertNotIn(None, process.wait_timeouts)

    def test_cancel_remains_distinct_even_when_timeout_settings_are_enabled(self) -> None:
        request = CodexRunRequest(
            job_id="job-cancel-with-timeouts",
            workspace_path=self.temp_dir.name,
            prompt="Cancel before timeout.",
            operational_settings=AppSettings(
                executable_path=self.request.operational_settings.executable_path,
                execution_timeout_minutes=1,
                inactivity_timeout_minutes=1,
                termination_grace_seconds=0,
            ),
        )
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                wait_blocks_until_terminated=True,
                last_message_text="ignored",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        handle = runner.launch(request)
        runner.cancel(handle)
        with handle._lock:
            handle._started_monotonic = time.monotonic() - 61
            handle._last_activity_monotonic = time.monotonic() - 61
        result = handle.wait()

        self.assertEqual(AgentRunStatus.CANCELED, result.status)
        self.assertEqual("사용자가 실행을 취소했습니다.", result.failure_reason)
        self.assertNotIn("시간 제한", result.failure_reason or "")

    def test_timeout_wait_returns_failed_when_process_ignores_termination(self) -> None:
        request = CodexRunRequest(
            job_id="job-unkillable-timeout",
            workspace_path=self.temp_dir.name,
            prompt="Will time out and ignore termination.",
            operational_settings=AppSettings(
                executable_path=self.request.operational_settings.executable_path,
                execution_timeout_minutes=1,
                inactivity_timeout_minutes=0,
                termination_grace_seconds=0,
            ),
        )
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                wait_blocks_until_terminated=True,
                ignore_termination=True,
                last_message_text="ignored",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        handle = runner.launch(request)
        with mock.patch("infra.process_runner._TIMEOUT_EXIT_FALLBACK_SECONDS", 0.01):
            with self.assertLogs("infra.process_runner", level="WARNING") as logs:
                with handle._lock:
                    handle._started_monotonic = time.monotonic() - 61
                result = handle.wait()

        process = factory.instances[0]
        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertIsNone(result.exit_code)
        self.assertIn("시간 제한 초과", result.failure_reason or "")
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertNotIn(request.job_id, runner._active_handles)
        self.assertTrue(
            any("without waiting forever" in message for message in logs.output),
            logs.output,
        )

    def test_wait_drains_active_stdout_reader_before_resolving_result(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-delayed"}\n',
                    '{"type":"turn.started"}\n',
                    '{"type":"item.completed"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                stdout_starts_after_wait=True,
                stdout_line_interval_seconds=0.015,
                exit_code=0,
                last_message_text="Final answer",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        with mock.patch("infra.process_runner._STREAM_READER_JOIN_TIMEOUT_SECONDS", 0.03):
            result = runner.run(self.request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("thread-delayed", result.session_id)
        self.assertTrue(result.parser_summary.saw_turn_completed)

    def test_stream_reader_join_does_not_wait_forever(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                block_stdout_reader=True,
                exit_code=0,
                last_message_text="ignored",
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        with mock.patch("infra.process_runner._STREAM_READER_JOIN_TIMEOUT_SECONDS", 0.01):
            with self.assertLogs("infra.process_runner", level="WARNING") as logs:
                result = runner.run(self.request)

        process = factory.instances[0]
        self.assertIsNotNone(result.completed_at)
        self.assertTrue(
            any("stream reader did not finish" in message for message in logs.output),
            logs.output,
        )
        process.release_blocked_streams()

    def test_wait_timeout_returns_control_for_real_subprocess(self) -> None:
        command = (
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if process.stdin is not None:
            process.stdin.close()

        artifacts_root = self.runner_root / "real-wait-timeout"
        artifacts_root.mkdir()
        artifacts = process_runner.ExecutionArtifactPaths(
            root_dir=artifacts_root,
            prompt_path=artifacts_root / "prompt.txt",
            stdout_jsonl_path=artifacts_root / "stdout.jsonl",
            stderr_log_path=artifacts_root / "stderr.log",
            last_message_path=artifacts_root / "last_message.txt",
            launch_metadata_path=artifacts_root / "launch.json",
        )
        artifacts.stdout_jsonl_path.touch()
        artifacts.stderr_log_path.touch()
        handle = process_runner.RunningCodexProcess(
            request=CodexRunRequest(
                job_id="real-wait-timeout",
                workspace_path=self.temp_dir.name,
                prompt="",
                operational_settings=AppSettings(
                    executable_path=sys.executable,
                    execution_timeout_minutes=0,
                    inactivity_timeout_minutes=0,
                ),
            ),
            command=command,
            process=process,
            artifacts=artifacts,
            parser=CodexJsonlParser(),
            started_at=process_runner.utc_now(),
            launch_metadata={"job_id": "real-wait-timeout"},
        )

        started = time.monotonic()
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                handle.wait(timeout=0.05)
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            if process.poll() is None:
                process_runner._terminate_process_tree(process, force=True)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def test_terminate_uses_kill_fallback_when_terminate_raises_and_process_is_alive(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"thread.started","thread_id":"thread-cancel"}\n',
                    '{"type":"turn.completed"}\n',
                ),
                exit_code=0,
                last_message_text="should not win",
                terminate_error=OSError("terminate failed"),
            )
        )
        runner = CodexCliProcessRunner(self.runner_root / "artifacts", popen_factory=factory)

        handle = runner.launch(self.request)
        with mock.patch("infra.process_runner._kill_windows_process_tree", return_value=False):
            with self.assertLogs("infra.process_runner", level="WARNING"):
                handle.terminate(timeout=0)
        result = handle.wait()

        process = factory.instances[0]
        self.assertEqual(AgentRunStatus.CANCELED, result.status)
        self.assertFalse(process.terminated)
        self.assertTrue(process.killed)

class OpenCodeFamilyProcessRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.runner_root = Path(self.temp_dir.name)
        self.workspace_path = self.runner_root / "workspace"
        self.workspace_path.mkdir()
        self.opencode_executable = _create_fake_executable(
            self.runner_root,
            "opencode.exe",
        )
        self.kilo_executable = _create_fake_executable(self.runner_root, "kilo.exe")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_opencode_run_marks_success_with_json_response_and_workspace_cwd(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"session.started","session":{"id":"session-open-123"}}\n',
                    (
                        '{"type":"message.completed","message":'
                        '{"role":"assistant","content":"OpenCode answer"}}\n'
                    ),
                ),
                stderr_lines=("diagnostic only\n",),
                exit_code=0,
            )
        )
        runner = OpenCodeCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = OpenCodeRunRequest(
            job_id="job-opencode-success",
            workspace_path=str(self.workspace_path),
            prompt="Solve with OpenCode.",
            operational_settings=AppSettings(
                executable_path=str(self.opencode_executable),
                agent_provider="opencode",
            ),
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("session-open-123", result.session_id)
        self.assertEqual("OpenCode answer", result.last_message)
        self.assertEqual(
            "OpenCode answer",
            result.artifacts.last_message_path.read_text(encoding="utf-8"),
        )
        self.assertIn("--dir", factory.calls[0])
        self.assertEqual(str(self.workspace_path), factory.calls[0][factory.calls[0].index("--dir") + 1])
        self.assertEqual("Solve with OpenCode.", factory.calls[0][-1])
        self.assertEqual(str(self.workspace_path.resolve()), factory.kwargs_calls[0]["cwd"])
        self.assertEqual(subprocess.DEVNULL, factory.kwargs_calls[0]["stdin"])
        metadata = json.loads(result.artifacts.launch_metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("opencode", metadata["provider_id"])
        self.assertEqual("argument", metadata["prompt_delivery"]["method"])
        self.assertFalse(metadata["applied_settings"]["dangerous_permission_flags_enabled"])

    def test_opencode_run_marks_success_with_step_event_stream(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"step_start","sessionID":"ses_open_actual",'
                    '"part":{"type":"step-start","sessionID":"ses_open_actual"}}\n',
                    '{"type":"text","sessionID":"ses_open_actual",'
                    '"part":{"type":"text","text":"OpenCode step answer"}}\n',
                    '{"type":"step_finish","sessionID":"ses_open_actual",'
                    '"part":{"type":"step-finish","reason":"tool-calls"}}\n',
                    '{"type":"step_finish","sessionID":"ses_open_actual",'
                    '"part":{"type":"step-finish","reason":"stop"}}\n',
                ),
                exit_code=0,
            )
        )
        runner = OpenCodeCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = OpenCodeRunRequest(
            job_id="job-opencode-step-success",
            workspace_path=str(self.workspace_path),
            prompt="Solve with OpenCode step events.",
            operational_settings=AppSettings(
                executable_path=str(self.opencode_executable),
                agent_provider="opencode",
            ),
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("ses_open_actual", result.session_id)
        self.assertEqual("OpenCode step answer", result.last_message)
        self.assertTrue(result.parser_summary.saw_turn_completed)

    def test_kilo_run_marks_failed_on_failure_event(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=('{"type":"turn.failed","message":"model aborted"}\n',),
                exit_code=0,
            )
        )
        runner = KiloCodeCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = KiloCodeRunRequest(
            job_id="job-kilo-failed",
            workspace_path=str(self.workspace_path),
            prompt="Fail with Kilo.",
            operational_settings=AppSettings(
                executable_path=str(self.kilo_executable),
                agent_provider="kilo_code",
            ),
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertEqual("model aborted", result.failure_reason)

    def test_provider_dispatching_runner_selects_request_provider(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"session.started","session_id":"session-dispatch"}\n',
                    '{"type":"response.completed","response":"dispatched answer"}\n',
                ),
                exit_code=0,
            )
        )
        runner = process_runner.ProviderAgentCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = OpenCodeRunRequest(
            job_id="job-provider-dispatch",
            workspace_path=str(self.workspace_path),
            prompt="Dispatch through provider registry.",
            operational_settings=AppSettings(
                executable_path=str(self.opencode_executable),
                agent_provider="opencode",
            ),
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("opencode.exe", Path(factory.calls[0][0]).name)
        self.assertEqual("dispatched answer", result.last_message)

    def test_opencode_cancel_marks_result_canceled(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"session.started","session_id":"session-cancel"}\n',
                    '{"type":"response.completed","response":"should not win"}\n',
                ),
                exit_code=0,
            )
        )
        runner = OpenCodeCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = OpenCodeRunRequest(
            job_id="job-opencode-cancel",
            workspace_path=str(self.workspace_path),
            prompt="Cancel OpenCode.",
            operational_settings=AppSettings(
                executable_path=str(self.opencode_executable),
                agent_provider="opencode",
            ),
        )

        handle = runner.launch(request)
        runner.cancel(handle)
        result = handle.wait()

        self.assertEqual(AgentRunStatus.CANCELED, result.status)
        self.assertTrue(factory.instances[0].terminated)
        self.assertEqual("사용자가 실행을 취소했습니다.", result.failure_reason)

    def test_kilo_timeout_marks_result_failed(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                wait_blocks_until_terminated=True,
            )
        )
        runner = KiloCodeCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = KiloCodeRunRequest(
            job_id="job-kilo-timeout",
            workspace_path=str(self.workspace_path),
            prompt="Timeout Kilo.",
            operational_settings=AppSettings(
                executable_path=str(self.kilo_executable),
                agent_provider="kilo_code",
                execution_timeout_minutes=1,
                inactivity_timeout_minutes=0,
                termination_grace_seconds=0,
            ),
        )

        handle = runner.launch(request)
        with self.assertLogs("infra.process_runner", level="WARNING"):
            with handle._lock:
                handle._started_monotonic = time.monotonic() - 61
            result = handle.wait()

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertIn("시간 제한 초과", result.failure_reason or "")
        self.assertTrue(factory.instances[0].terminated)

class ClaudeCodeProcessRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.runner_root = Path(self.temp_dir.name)
        self.workspace_path = self.runner_root / "workspace"
        self.workspace_path.mkdir()
        self.claude_executable = _create_fake_executable(
            self.runner_root,
            "claude.exe",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_claude_run_marks_success_with_stream_json_result_and_workspace_cwd(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"system","subtype":"init","session_id":"session-claude-123"}\n',
                    (
                        '{"type":"assistant","message":{"role":"assistant","content":'
                        '[{"type":"text","text":"intermediate answer"}]}}\n'
                    ),
                    (
                        '{"type":"result","subtype":"success",'
                        '"result":"Claude final answer","session_id":"session-claude-123"}\n'
                    ),
                ),
                stderr_lines=("diagnostic only\n",),
                exit_code=0,
            )
        )
        runner = ClaudeCodeCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = ClaudeCodeRunRequest(
            job_id="job-claude-success",
            workspace_path=str(self.workspace_path),
            prompt="Solve with Claude.",
            operational_settings=AppSettings(
                executable_path=str(self.claude_executable),
                agent_provider="claude_code",
            ),
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("session-claude-123", result.session_id)
        self.assertEqual("Claude final answer", result.last_message)
        self.assertEqual(
            "Claude final answer",
            result.artifacts.last_message_path.read_text(encoding="utf-8"),
        )
        self.assertEqual("-p", factory.calls[0][1])
        self.assertEqual("Solve with Claude.", factory.calls[0][2])
        self.assertEqual("stream-json", factory.calls[0][factory.calls[0].index("--output-format") + 1])
        self.assertEqual(str(self.workspace_path.resolve()), factory.kwargs_calls[0]["cwd"])
        self.assertEqual(subprocess.DEVNULL, factory.kwargs_calls[0]["stdin"])
        metadata = json.loads(result.artifacts.launch_metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("claude_code", metadata["provider_id"])
        self.assertEqual("argument", metadata["prompt_delivery"]["method"])
        self.assertFalse(metadata["applied_settings"]["dangerous_permission_flags_enabled"])

    def test_claude_run_marks_failed_on_result_error(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"result","subtype":"error_during_execution",'
                    '"error":{"message":"permission denied"}}\n',
                ),
                exit_code=0,
            )
        )
        runner = ClaudeCodeCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = ClaudeCodeRunRequest(
            job_id="job-claude-failed",
            workspace_path=str(self.workspace_path),
            prompt="Fail with Claude.",
            operational_settings=AppSettings(
                executable_path=str(self.claude_executable),
                agent_provider="claude_code",
            ),
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertEqual("permission denied", result.failure_reason)

    def test_claude_cancel_marks_result_canceled(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"system","subtype":"init","session_id":"session-cancel"}\n',
                    '{"type":"result","subtype":"success","result":"should not win"}\n',
                ),
                exit_code=0,
            )
        )
        runner = ClaudeCodeCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = ClaudeCodeRunRequest(
            job_id="job-claude-cancel",
            workspace_path=str(self.workspace_path),
            prompt="Cancel Claude.",
            operational_settings=AppSettings(
                executable_path=str(self.claude_executable),
                agent_provider="claude_code",
            ),
        )

        handle = runner.launch(request)
        runner.cancel(handle)
        result = handle.wait()

        self.assertEqual(AgentRunStatus.CANCELED, result.status)
        self.assertTrue(factory.instances[0].terminated)
        self.assertEqual("사용자가 실행을 취소했습니다.", result.failure_reason)

    def test_claude_timeout_marks_result_failed(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                wait_blocks_until_terminated=True,
            )
        )
        runner = ClaudeCodeCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = ClaudeCodeRunRequest(
            job_id="job-claude-timeout",
            workspace_path=str(self.workspace_path),
            prompt="Timeout Claude.",
            operational_settings=AppSettings(
                executable_path=str(self.claude_executable),
                agent_provider="claude_code",
                execution_timeout_minutes=1,
                inactivity_timeout_minutes=0,
                termination_grace_seconds=0,
            ),
        )

        handle = runner.launch(request)
        with self.assertLogs("infra.process_runner", level="WARNING"):
            with handle._lock:
                handle._started_monotonic = time.monotonic() - 61
            result = handle.wait()

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertIn("시간 제한 초과", result.failure_reason or "")
        self.assertTrue(factory.instances[0].terminated)

class PiProcessRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.runner_root = Path(self.temp_dir.name)
        self.workspace_path = self.runner_root / "workspace"
        self.workspace_path.mkdir()
        self.pi_executable = _create_fake_executable(
            self.runner_root,
            "pi.exe",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_pi_run_marks_success_with_json_event_stream_and_workspace_cwd(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"session","version":3,"id":"session-pi-123",'
                    '"timestamp":"2026-05-27T00:00:00Z","cwd":"/repo"}\n',
                    '{"type":"agent_start"}\n',
                    (
                        '{"type":"turn_end","message":{"role":"assistant","content":'
                        '[{"type":"text","text":"Pi final answer"}]},"toolResults":[]}\n'
                    ),
                    '{"type":"agent_end","messages":[]}\n',
                ),
                stderr_lines=("diagnostic only\n",),
                exit_code=0,
            )
        )
        runner = PiCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = PiRunRequest(
            job_id="job-pi-success",
            workspace_path=str(self.workspace_path),
            prompt="Solve with Pi.",
            operational_settings=AppSettings(
                executable_path=str(self.pi_executable),
                agent_provider="pi",
            ),
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.COMPLETED, result.status)
        self.assertEqual("session-pi-123", result.session_id)
        self.assertEqual("Pi final answer", result.last_message)
        self.assertEqual(
            "Pi final answer",
            result.artifacts.last_message_path.read_text(encoding="utf-8"),
        )
        self.assertEqual("--mode", factory.calls[0][1])
        self.assertEqual("json", factory.calls[0][2])
        self.assertEqual("Solve with Pi.", factory.calls[0][-1])
        self.assertEqual(str(self.workspace_path.resolve()), factory.kwargs_calls[0]["cwd"])
        self.assertEqual(subprocess.DEVNULL, factory.kwargs_calls[0]["stdin"])
        self.assertEqual("1", factory.kwargs_calls[0]["env"]["PI_SKIP_VERSION_CHECK"])
        metadata = json.loads(result.artifacts.launch_metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("pi", metadata["provider_id"])
        self.assertEqual("argument", metadata["prompt_delivery"]["method"])
        self.assertEqual("json", metadata["stream_output"]["mode"])

    def test_pi_run_marks_failed_without_completion(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"session","version":3,"id":"session-pi-failed"}\n',
                    '{"type":"message_end","message":{"role":"assistant","content":"unfinished"}}\n',
                ),
                exit_code=0,
            )
        )
        runner = PiCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = PiRunRequest(
            job_id="job-pi-no-completion",
            workspace_path=str(self.workspace_path),
            prompt="Fail without Pi completion.",
            operational_settings=AppSettings(
                executable_path=str(self.pi_executable),
                agent_provider="pi",
            ),
        )

        result = runner.run(request)

        self.assertEqual(AgentRunStatus.FAILED, result.status)
        self.assertEqual("turn.completed 이벤트를 확인하지 못했습니다.", result.failure_reason)

    def test_pi_cancel_marks_result_canceled(self) -> None:
        factory = _FakePopenFactory(
            _FakeProcessScenario(
                stdout_lines=(
                    '{"type":"session","version":3,"id":"session-pi-cancel"}\n',
                    '{"type":"agent_end","messages":[{"role":"assistant","content":"late"}]}\n',
                ),
                exit_code=0,
            )
        )
        runner = PiCliProcessRunner(
            self.runner_root / "artifacts",
            popen_factory=factory,
        )
        request = PiRunRequest(
            job_id="job-pi-cancel",
            workspace_path=str(self.workspace_path),
            prompt="Cancel Pi.",
            operational_settings=AppSettings(
                executable_path=str(self.pi_executable),
                agent_provider="pi",
            ),
        )

        handle = runner.launch(request)
        runner.cancel(handle)
        result = handle.wait()

        self.assertEqual(AgentRunStatus.CANCELED, result.status)
        self.assertTrue(factory.instances[0].terminated)
        self.assertEqual("사용자가 실행을 취소했습니다.", result.failure_reason)


@unittest.skipUnless(
    _RUN_REAL_AGENT_SMOKE,
    "Set J3AITASKRUNNER_RUN_REAL_AGENT_SMOKE=1 to run installed CLI smoke tests.",
)
class OpenCodeFamilyInstalledCliSmokeTests(unittest.TestCase):
    def test_installed_opencode_run_help_exposes_expected_contract_flags(self) -> None:
        self._assert_run_help_contract(
            command_name="opencode",
            expected_flags=(
                "--format",
                "--dir",
                "--session",
                "--model",
                "--variant",
                "--dangerously-skip-permissions",
            ),
        )

    def test_installed_kilo_run_help_exposes_expected_contract_flags(self) -> None:
        self._assert_run_help_contract(
            command_name="kilo",
            expected_flags=(
                "--format",
                "--dir",
                "--session",
                "--model",
                "--variant",
                "--dangerously-skip-permissions",
                "--auto",
            ),
        )

    def _assert_run_help_contract(
        self,
        *,
        command_name: str,
        expected_flags: tuple[str, ...],
    ) -> None:
        executable = shutil.which(command_name)
        if executable is None:
            self.skipTest(f"{command_name} is not installed on PATH.")

        completed = subprocess.run(
            (executable, "run", "--help"),
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=10,
        )

        help_text = f"{completed.stdout}\n{completed.stderr}"
        self.assertEqual(0, completed.returncode, help_text)
        for expected_flag in expected_flags:
            with self.subTest(command_name=command_name, flag=expected_flag):
                self.assertIn(expected_flag, help_text)


@unittest.skipUnless(
    _RUN_REAL_AGENT_SMOKE,
    "Set J3AITASKRUNNER_RUN_REAL_AGENT_SMOKE=1 to run installed CLI smoke tests.",
)
class PiInstalledCliSmokeTests(unittest.TestCase):
    def test_installed_pi_help_exposes_expected_contract_flags(self) -> None:
        executable = shutil.which("pi")
        if executable is None:
            self.skipTest("pi is not installed on PATH.")

        completed = subprocess.run(
            (executable, "--help"),
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=10,
        )

        help_text = f"{completed.stdout}\n{completed.stderr}"
        self.assertEqual(0, completed.returncode, help_text)
        for expected_flag in ("--mode", "--session", "--model", "--thinking", "--version"):
            with self.subTest(flag=expected_flag):
                self.assertIn(expected_flag, help_text)


def _create_fake_executable(root: Path, name: str) -> Path:
    executable_path = root / name
    executable_path.write_text("", encoding="utf-8")
    return executable_path


def _stdout_lines_for_artifact_failure(failure_mode: str) -> tuple[str, ...]:
    thread_started = f'{{"type":"thread.started","thread_id":"thread-{failure_mode}"}}\n'
    turn_completed = '{"type":"turn.completed"}\n'
    if failure_mode != "flush":
        return (thread_started, turn_completed)

    filler_lines = tuple(
        f'{{"type":"progress","index":{index}}}\n'
        for index in range(process_runner._ARTIFACT_FILE_FLUSH_LINE_INTERVAL - 1)
    )
    return (thread_started, *filler_lines, turn_completed)


class _FailingArtifactFile:
    def __init__(self, failure_mode: str) -> None:
        self._failure_mode = failure_mode
        self.closed = False

    def write(self, value: str) -> int:
        if self._failure_mode == "write":
            raise OSError("artifact write failed")
        return len(value)

    def flush(self) -> None:
        if self._failure_mode == "flush":
            raise OSError("artifact flush failed")

    def close(self) -> None:
        self.closed = True


@dataclass(slots=True, frozen=True)
class _FakeProcessScenario:
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    exit_code: int = 0
    last_message_text: str | None = None
    raise_error: OSError | None = None
    stdin_close_error: OSError | None = None
    terminate_error: OSError | None = None
    wait_blocks_until_terminated: bool = False
    block_stdout_reader: bool = False
    stdout_starts_after_wait: bool = False
    stdout_line_interval_seconds: float = 0.0
    ignore_termination: bool = False
    pid: int | None = None


class _RecordingStdin:
    def __init__(self, *, close_error: OSError | None = None) -> None:
        self.content = ""
        self.closed = False
        self._close_error = close_error

    def write(self, value: str) -> int:
        if self.closed:
            raise ValueError("stdin is closed")
        self.content += value
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class _BlockingTextStream:
    def __init__(self) -> None:
        self.closed = False
        self._released = threading.Event()

    def __iter__(self):
        return self

    def __next__(self) -> str:
        self._released.wait()
        raise StopIteration

    def close(self) -> None:
        self.closed = True

    def release(self) -> None:
        self._released.set()


class _PacedTextStream:
    def __init__(
        self,
        lines: tuple[str, ...],
        *,
        start_event: threading.Event,
        line_interval_seconds: float,
    ) -> None:
        self.closed = False
        self._lines = lines
        self._start_event = start_event
        self._line_interval_seconds = line_interval_seconds
        self._index = 0

    def __iter__(self):
        return self

    def __next__(self) -> str:
        self._start_event.wait()
        if self._index >= len(self._lines):
            raise StopIteration
        if self._line_interval_seconds > 0:
            time.sleep(self._line_interval_seconds)
        line = self._lines[self._index]
        self._index += 1
        return line

    def close(self) -> None:
        self.closed = True

    def release(self) -> None:
        self._start_event.set()


class _FakePopen:
    def __init__(
        self,
        command: tuple[str, ...],
        *,
        scenario: _FakeProcessScenario,
    ) -> None:
        self.command = command
        self.pid = scenario.pid
        self.stdin = _RecordingStdin(close_error=scenario.stdin_close_error)
        self._stdout_start_event = threading.Event()
        if scenario.block_stdout_reader:
            self.stdout = _BlockingTextStream()
        elif scenario.stdout_starts_after_wait:
            self.stdout = _PacedTextStream(
                scenario.stdout_lines,
                start_event=self._stdout_start_event,
                line_interval_seconds=scenario.stdout_line_interval_seconds,
            )
        else:
            self.stdout = io.StringIO("".join(scenario.stdout_lines))
        self.stderr = io.StringIO("".join(scenario.stderr_lines))
        self._exit_code = scenario.exit_code
        self._terminate_error = scenario.terminate_error
        self._wait_blocks_until_terminated = scenario.wait_blocks_until_terminated
        self._ignore_termination = scenario.ignore_termination
        self._finished = False
        self.terminated = False
        self.killed = False
        self.wait_timeouts: list[float | None] = []

        if scenario.last_message_text is not None and "-o" in command:
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text(scenario.last_message_text, encoding="utf-8")

    def poll(self) -> int | None:
        if self._finished:
            return self._exit_code
        return None

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self._wait_blocks_until_terminated and not self._finished:
            raise subprocess.TimeoutExpired(self.command, timeout)
        self._finished = True
        self._stdout_start_event.set()
        return self._exit_code

    def terminate(self) -> None:
        if self._terminate_error is not None:
            raise self._terminate_error
        self.terminated = True
        if self._ignore_termination:
            return
        self._finished = True
        self._exit_code = -15

    def kill(self) -> None:
        self.killed = True
        if self._ignore_termination:
            return
        self._finished = True
        self._exit_code = -9

    def release_blocked_streams(self) -> None:
        for stream in (self.stdout, self.stderr):
            release = getattr(stream, "release", None)
            if callable(release):
                release()


class _FakePopenFactory:
    def __init__(self, *scenarios: _FakeProcessScenario) -> None:
        self._scenarios = list(scenarios)
        self.calls: list[tuple[str, ...]] = []
        self.kwargs_calls: list[dict[str, object]] = []
        self.instances: list[_FakePopen] = []

    def __call__(self, command: tuple[str, ...], **kwargs: object) -> _FakePopen:
        scenario = self._scenarios.pop(0)
        self.calls.append(tuple(command))
        self.kwargs_calls.append(dict(kwargs))
        if scenario.raise_error is not None:
            raise scenario.raise_error

        instance = _FakePopen(tuple(command), scenario=scenario)
        self.instances.append(instance)
        return instance


if __name__ == "__main__":
    unittest.main()


