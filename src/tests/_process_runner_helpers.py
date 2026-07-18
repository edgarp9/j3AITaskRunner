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

__all__ = [name for name in globals() if not name.startswith("__")]
