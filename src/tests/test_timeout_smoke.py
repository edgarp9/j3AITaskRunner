from __future__ import annotations

import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from tempfile import TemporaryDirectory
from unittest import mock

from app.controller import AppController, JobExecutionResultCapturedEvent
from domain.models import AppSettings, JobStatus
from infra import process_runner
from infra.process_runner import AgentRunStatus, CodexCliProcessRunner


_FAKE_CODEX_SOURCE = r"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main() -> int:
    args = sys.argv[1:]
    output_path = _arg_after(args, "-o")
    prompt = sys.stdin.read()
    mode = _extract_mode(prompt)

    if mode == "success":
        _emit({"type": "thread.started", "thread_id": "thread-success"})
        _emit({"type": "turn.completed"})
        if output_path is not None:
            Path(output_path).write_text("fake success", encoding="utf-8")
        return 0

    if mode == "partial_stdout_hang":
        _emit({"type": "thread.started", "thread_id": "thread-partial"})
        _sleep_forever()

    if mode == "child_process_hang":
        _emit({"type": "thread.started", "thread_id": "thread-child"})
        _spawn_sleeping_child()
        _sleep_forever()

    if mode == "quiet_hang":
        _sleep_forever()

    print(f"unknown mode: {mode}", file=sys.stderr, flush=True)
    return 2


def _arg_after(args: list[str], flag: str) -> str | None:
    try:
        index = args.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(args):
        return None
    return args[index + 1]


def _extract_mode(prompt: str) -> str:
    for token in prompt.split():
        if token.startswith("mode="):
            return token.removeprefix("mode=")
    return prompt.strip()


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload), flush=True)


def _spawn_sleeping_child() -> None:
    pid_path = Path.cwd() / "fake-child.pid"
    child_code = (
        "from pathlib import Path\n"
        "import os, sys, time\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, "-c", child_code, str(pid_path)], **kwargs)


def _sleep_forever() -> None:
    while True:
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
"""

_SMOKE_TIMEOUT_SECONDS_PER_MINUTE = 5.0
_SMOKE_SCENARIO_SETTLE_TIMEOUT_SECONDS = 20.0


class CodexTimeoutSmokeTests(unittest.TestCase):
    def test_partial_stdout_hang_times_out_and_queue_continues(self) -> None:
        self._assert_timeout_scenario(
            mode="partial_stdout_hang",
            settings_overrides={
                "execution_timeout_minutes": 1,
                "inactivity_timeout_minutes": 0,
            },
            expected_user_message="실행 시간이 초과되었습니다.",
        )

    def test_child_process_hang_times_out_and_queue_continues(self) -> None:
        self._assert_timeout_scenario(
            mode="child_process_hang",
            settings_overrides={
                "execution_timeout_minutes": 1,
                "inactivity_timeout_minutes": 0,
            },
            expected_user_message="실행 시간이 초과되었습니다.",
        )

    def test_quiet_hang_times_out_on_inactivity_and_queue_continues(self) -> None:
        self._assert_timeout_scenario(
            mode="quiet_hang",
            settings_overrides={
                "execution_timeout_minutes": 0,
                "inactivity_timeout_minutes": 1,
            },
            expected_user_message="진행 로그가 없어 실행을 중단했습니다.",
        )

    def test_work_prompt_assets_forbid_watch_and_dev_server_commands(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        prompt_root = project_root / "prompt"
        work_prompt_paths = sorted(prompt_root.glob("*/*_work.md"))

        self.assertGreater(len(work_prompt_paths), 0)
        for prompt_path in work_prompt_paths:
            with self.subTest(prompt=str(prompt_path.relative_to(project_root))):
                content = prompt_path.read_text(encoding="utf-8")
                self.assertIn("watch mode가 아닌 one-shot", content)
                self.assertIn("실행이 멈출 수 있는 명령에는 제한 시간을 두라", content)
                self.assertIn("npm run dev", content)
                self.assertIn("vite --host", content)
                self.assertIn("vitest --watch", content)
                self.assertIn("jest --watch", content)
                self.assertIn("종료되지 않는 명령은 사용하지 말고", content)
                self.assertIn("timeout 가능한 방식", content)

    def _assert_timeout_scenario(
        self,
        *,
        mode: str,
        settings_overrides: dict[str, int],
        expected_user_message: str,
    ) -> None:
        with TemporaryDirectory() as temp_dir_name:
            root_path = Path(temp_dir_name)
            workspace_path = root_path / "workspace"
            workspace_path.mkdir()
            executable_path = _write_fake_codex_executable(root_path)
            runner = CodexCliProcessRunner(root_path / "artifacts")
            settings = AppSettings(
                executable_path=str(executable_path),
                termination_grace_seconds=0,
                **settings_overrides,
            )
            controller = AppController(
                runner=runner,
                settings_provider=lambda: settings,
            )
            workspace = controller.open_workspace(str(workspace_path)).workspace_tab
            timeout_session = controller.open_session(workspace.workspace_tab_id)
            follow_up_session = controller.open_session(workspace.workspace_tab_id)
            timeout_job = controller.submit_job(timeout_session.session_tab_id, f"mode={mode}")
            follow_up_job = controller.submit_job(
                follow_up_session.session_tab_id,
                "mode=success",
            )

            try:
                with mock.patch(
                    "infra.process_runner._minutes_to_seconds",
                    side_effect=_fast_minutes_to_seconds,
                ):
                    with mock.patch.multiple(
                        process_runner,
                        _TIMEOUT_MONITOR_POLL_INTERVAL_SECONDS=0.02,
                        _STREAM_READER_JOIN_TIMEOUT_SECONDS=0.05,
                        _TIMEOUT_EXIT_FALLBACK_SECONDS=0.05,
                    ):
                        controller.start_queue(workspace.workspace_tab_id)
                        self.assertTrue(
                            _process_until(
                                controller,
                                lambda: (
                                    controller.scheduler.get_job(timeout_job.job_id).status
                                    == JobStatus.FAILED
                                    and controller.scheduler.get_job(follow_up_job.job_id).status
                                    == JobStatus.COMPLETED
                                ),
                            ),
                            _format_job_statuses(
                                controller,
                                timeout_job.job_id,
                                follow_up_job.job_id,
                            ),
                        )
            finally:
                _cleanup_fake_children(workspace_path)

            controller.process_background_events()
            events = controller.drain_ui_events()
            captured_statuses = {
                event.job_id: event.status
                for event in events
                if isinstance(event, JobExecutionResultCapturedEvent)
            }
            failed_job = controller.scheduler.get_job(timeout_job.job_id)
            completed_job = controller.scheduler.get_job(follow_up_job.job_id)

            self.assertEqual(JobStatus.FAILED, failed_job.status)
            self.assertEqual(expected_user_message, failed_job.user_message)
            self.assertEqual(JobStatus.COMPLETED, completed_job.status)
            self.assertEqual(AgentRunStatus.FAILED, captured_statuses[timeout_job.job_id])
            self.assertEqual(AgentRunStatus.COMPLETED, captured_statuses[follow_up_job.job_id])
            self.assertFalse(controller.has_pending_background_work())


def _write_fake_codex_executable(root_path: Path) -> Path:
    script_path = root_path / "fake_codex.py"
    script_path.write_text(textwrap.dedent(_FAKE_CODEX_SOURCE).strip() + "\n", encoding="utf-8")
    if os.name == "nt":
        wrapper_path = root_path / "fake_codex.cmd"
        wrapper_path.write_text(
            f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n',
            encoding="utf-8",
        )
        return wrapper_path

    wrapper_path = root_path / "fake_codex"
    wrapper_path.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(sys.executable)} {shlex.quote(str(script_path))} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper_path.chmod(0o755)
    return wrapper_path


def _fast_minutes_to_seconds(value: int) -> float | None:
    if value <= 0:
        return None
    # Keep subprocess smoke tests fast, but leave enough room for Windows .cmd
    # startup and prior timeout cleanup before the follow-up success job runs.
    return _SMOKE_TIMEOUT_SECONDS_PER_MINUTE


def _process_until(
    controller: AppController,
    predicate,
    *,
    timeout: float = _SMOKE_SCENARIO_SETTLE_TIMEOUT_SECONDS,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        controller.process_background_events()
        if predicate():
            return True
        time.sleep(0.02)
    controller.process_background_events()
    return predicate()


def _format_job_statuses(controller: AppController, *job_ids: str) -> str:
    statuses = {
        job_id: controller.scheduler.get_job(job_id).status.value
        for job_id in job_ids
    }
    return f"job statuses did not settle: {statuses}"


def _cleanup_fake_children(workspace_path: Path) -> None:
    for pid_path in workspace_path.glob("fake-child*.pid"):
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        _terminate_pid(pid)


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ("taskkill", "/PID", str(pid), "/T", "/F"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        except OSError:
            return
        time.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
