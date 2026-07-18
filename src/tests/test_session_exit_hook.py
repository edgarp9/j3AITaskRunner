from __future__ import annotations

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.controller import AppController
from app.runtime import AUTO_COMMIT_PROMPT, AppRuntime, RuntimeActionFailedEvent
from domain import AppSettings, JobStatus, SessionExitHookConfig
from infra.process_runner import AgentRunStatus
from infra.session_exit_hook import launch_session_exit_hook

from tests._app_runtime_helpers import _RuntimePersistenceRepositoryStub, _wait_until
from tests._controller_helpers import _FakeBackgroundRunner, _Scenario


class SessionExitHookRunnerTests(unittest.TestCase):
    def test_launch_uses_argv_cwd_devnull_hidden_console_and_no_shell(self) -> None:
        calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

        def fake_popen(command: tuple[str, ...], **kwargs: object) -> object:
            calls.append((command, kwargs))
            return object()

        launched = launch_session_exit_hook(
            SessionExitHookConfig(
                enabled=True,
                executable_path=r"C:\Tools\hook.exe",
                arguments=("--flag", "value with spaces"),
            ),
            r"C:\Repo\Alpha",
            popen_factory=fake_popen,
            os_name="nt",
        )

        self.assertTrue(launched)
        self.assertEqual(
            (
                r"C:\Tools\hook.exe",
                "--flag",
                "value with spaces",
            ),
            calls[0][0],
        )
        self.assertEqual(r"C:\Repo\Alpha", calls[0][1]["cwd"])
        self.assertIs(subprocess.DEVNULL, calls[0][1]["stdin"])
        self.assertIs(subprocess.DEVNULL, calls[0][1]["stdout"])
        self.assertIs(subprocess.DEVNULL, calls[0][1]["stderr"])
        self.assertIs(False, calls[0][1]["shell"])
        self.assertIn("creationflags", calls[0][1])

    def test_launch_skips_disabled_or_blank_executable_config(self) -> None:
        calls: list[object] = []

        def fake_popen(command: tuple[str, ...], **kwargs: object) -> object:
            calls.append((command, kwargs))
            return object()

        self.assertFalse(
            launch_session_exit_hook(
                SessionExitHookConfig(enabled=False, executable_path="hook"),
                r"C:\Repo",
                popen_factory=fake_popen,
            )
        )
        self.assertFalse(
            launch_session_exit_hook(
                SessionExitHookConfig(enabled=True, executable_path=""),
                r"C:\Repo",
                popen_factory=fake_popen,
            )
        )
        self.assertEqual([], calls)


class AppRuntimeSessionExitHookTests(unittest.TestCase):
    def test_launches_once_when_session_jobs_all_finish(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime, runner, workspace_id, session_id, launches = _build_hook_runtime(
                Path(temp_dir)
            )
            try:
                runtime.set_session_exit_hook_config(
                    session_id,
                    SessionExitHookConfig(
                        enabled=True,
                        executable_path="hook",
                        arguments=("--done",),
                    ),
                )
                job = runtime.submit_job(session_id, "prompt")
                runtime.start_queue(workspace_id)

                self.assertTrue(_wait_until(lambda: len(runner.launched_requests) == 1))
                runner.resolve(job.job_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and len(launches) == 1
                    )
                )
                runtime.process_background_events()

                self.assertEqual(1, len(launches))
                self.assertEqual("--done", launches[0][0].arguments[0])
                self.assertEqual(str(Path(temp_dir)), launches[0][1])
                for _ in range(3):
                    runtime.process_background_events()
                self.assertEqual(1, len(launches))
            finally:
                _shutdown_runtime(runtime)

    def test_launches_for_failed_and_canceled_final_statuses(self) -> None:
        scenarios = (
            ("failed", AgentRunStatus.FAILED, JobStatus.FAILED),
            ("canceled", AgentRunStatus.CANCELED, JobStatus.CANCELED),
        )
        for prompt, agent_status, job_status in scenarios:
            with self.subTest(prompt=prompt), TemporaryDirectory() as temp_dir:
                runtime, runner, workspace_id, session_id, launches = _build_hook_runtime(
                    Path(temp_dir)
                )
                try:
                    runner.prepare(
                        prompt,
                        _Scenario(
                            status=agent_status,
                            failure_reason=f"{prompt} result",
                        ),
                    )
                    runtime.set_session_exit_hook_config(
                        session_id,
                        SessionExitHookConfig(enabled=True, executable_path="hook"),
                    )
                    job = runtime.submit_job(session_id, prompt)
                    runtime.start_queue(workspace_id)

                    self.assertTrue(
                        _wait_until(lambda: len(runner.launched_requests) == 1)
                    )
                    runner.resolve(job.job_id)
                    self.assertTrue(
                        _wait_until(
                            lambda: runtime.process_background_events() >= 0
                            and len(launches) == 1
                        )
                    )

                    self.assertEqual(job_status, runtime.get_job(job.job_id).status)
                    self.assertEqual(1, len(launches))
                finally:
                    _shutdown_runtime(runtime)

    def test_waits_for_followup_job_before_launching(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime, runner, workspace_id, session_id, launches = _build_hook_runtime(
                Path(temp_dir)
            )
            try:
                runtime.set_session_exit_hook_config(
                    session_id,
                    SessionExitHookConfig(enabled=True, executable_path="hook"),
                )
                first_job = runtime.submit_job(session_id, "prompt")
                followup_job = runtime.submit_job(session_id, "followup")
                runtime.start_queue(workspace_id)

                self.assertTrue(_wait_until(lambda: len(runner.launched_requests) == 1))
                runner.resolve(first_job.job_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and len(runner.launched_requests) == 2
                    )
                )
                self.assertEqual([], launches)

                runner.resolve(followup_job.job_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and len(launches) == 1
                    )
                )
            finally:
                _shutdown_runtime(runtime)

    def test_waits_for_auto_commit_job_before_launching(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime, runner, workspace_id, session_id, launches = _build_hook_runtime(
                Path(temp_dir)
            )
            try:
                runtime.set_session_exit_hook_config(
                    session_id,
                    SessionExitHookConfig(enabled=True, executable_path="hook"),
                )
                prompt_job = runtime.submit_job(session_id, "prompt")
                auto_commit_job = runtime.submit_job(session_id, AUTO_COMMIT_PROMPT)
                runtime.start_queue(workspace_id)

                self.assertTrue(_wait_until(lambda: len(runner.launched_requests) == 1))
                runner.resolve(prompt_job.job_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and len(runner.launched_requests) == 2
                    )
                )
                self.assertEqual([], launches)

                runner.resolve(auto_commit_job.job_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and len(launches) == 1
                    )
                )
            finally:
                _shutdown_runtime(runtime)

    def test_setting_hook_after_completed_session_waits_for_new_work(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime, runner, workspace_id, session_id, launches = _build_hook_runtime(
                Path(temp_dir)
            )
            try:
                first_job = runtime.submit_job(session_id, "first")
                runtime.start_queue(workspace_id)
                self.assertTrue(_wait_until(lambda: len(runner.launched_requests) == 1))
                runner.resolve(first_job.job_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and not runtime.has_pending_background_work()
                    )
                )
                self.assertEqual([], launches)

                runtime.set_session_exit_hook_config(
                    session_id,
                    SessionExitHookConfig(enabled=True, executable_path="hook"),
                )
                runtime.process_background_events()
                self.assertEqual([], launches)

                second_job = runtime.submit_job(session_id, "second")
                runtime.start_queue(workspace_id)
                self.assertTrue(_wait_until(lambda: len(runner.launched_requests) == 2))
                runner.resolve(second_job.job_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and len(launches) == 1
                    )
                )
            finally:
                _shutdown_runtime(runtime)

    def test_pending_preset_followup_delays_launch_until_cleared(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime, runner, workspace_id, session_id, launches = _build_hook_runtime(
                Path(temp_dir)
            )
            try:
                runtime.set_session_exit_hook_config(
                    session_id,
                    SessionExitHookConfig(enabled=True, executable_path="hook"),
                )
                job = runtime.submit_job(session_id, "prompt")
                runtime.start_queue(workspace_id)
                self.assertTrue(_wait_until(lambda: len(runner.launched_requests) == 1))

                runtime._mark_preset_followup_pending(workspace_id)
                runner.resolve(job.job_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and not runtime.has_pending_background_work()
                    )
                )
                self.assertEqual([], launches)

                runtime._clear_preset_followup_pending(workspace_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and len(launches) == 1
                    )
                )
            finally:
                _shutdown_runtime(runtime)

    def test_closed_session_completion_does_not_launch_hook(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime, runner, workspace_id, session_id, launches = _build_hook_runtime(
                Path(temp_dir)
            )
            try:
                runtime.set_session_exit_hook_config(
                    session_id,
                    SessionExitHookConfig(enabled=True, executable_path="hook"),
                )
                runtime.submit_job(session_id, "prompt")
                runtime.start_queue(workspace_id)
                self.assertTrue(_wait_until(lambda: len(runner.launched_requests) == 1))

                runtime.close_session(session_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and not runtime.has_pending_background_work()
                    )
                )
                self.assertEqual([], launches)
            finally:
                _shutdown_runtime(runtime)

    def test_hook_runner_failure_does_not_change_finished_job_status(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime, runner, workspace_id, session_id, launches = _build_hook_runtime(
                Path(temp_dir)
            )
            try:
                def fail_hook(
                    _config: SessionExitHookConfig,
                    _workspace_path: str,
                ) -> bool:
                    raise RuntimeError("hook failed")

                runtime._session_exit_hook_runner = fail_hook
                runtime.set_session_exit_hook_config(
                    session_id,
                    SessionExitHookConfig(enabled=True, executable_path="hook"),
                )
                job = runtime.submit_job(session_id, "prompt")
                runtime.start_queue(workspace_id)

                self.assertTrue(_wait_until(lambda: len(runner.launched_requests) == 1))
                runner.resolve(job.job_id)
                self.assertTrue(
                    _wait_until(
                        lambda: runtime.process_background_events() >= 0
                        and not runtime.has_pending_background_work()
                    )
                )

                self.assertEqual(JobStatus.COMPLETED, runtime.get_job(job.job_id).status)
                self.assertEqual([], launches)
                self.assertFalse(
                    any(
                        isinstance(event, RuntimeActionFailedEvent)
                        for event in runtime.drain_events()
                    )
                )
            finally:
                _shutdown_runtime(runtime)


def _build_hook_runtime(
    root_path: Path,
) -> tuple[
    AppRuntime,
    _FakeBackgroundRunner,
    str,
    str,
    list[tuple[SessionExitHookConfig, str]],
]:
    executable_path = root_path / "fake-agent.exe"
    executable_path.write_text("", encoding="utf-8")
    artifacts_root = root_path / "artifacts"
    runner = _FakeBackgroundRunner(artifacts_root)
    runner.prepare(
        "prompt",
        _Scenario(status=AgentRunStatus.COMPLETED, last_message="done"),
    )
    runner.prepare(
        "first",
        _Scenario(status=AgentRunStatus.COMPLETED, last_message="first done"),
    )
    runner.prepare(
        "second",
        _Scenario(status=AgentRunStatus.COMPLETED, last_message="second done"),
    )
    runner.prepare(
        "followup",
        _Scenario(status=AgentRunStatus.COMPLETED, last_message="followup done"),
    )
    runner.prepare(
        AUTO_COMMIT_PROMPT,
        _Scenario(status=AgentRunStatus.COMPLETED, last_message="commit done"),
    )
    settings = AppSettings(
        executable_path=str(executable_path),
        executable_paths={"codex": str(executable_path)},
        agent_provider="codex",
    )
    controller = AppController(
        runner=runner,
        settings_provider=lambda: settings,
    )
    launches: list[tuple[SessionExitHookConfig, str]] = []
    runtime = AppRuntime(
        controller=controller,
        repository=_RuntimePersistenceRepositoryStub(initial_settings=settings),
        session_exit_hook_runner=lambda config, workspace_path: launches.append(
            (config, workspace_path)
        )
        or True,
    )
    workspace = runtime.open_workspace(str(root_path)).open_result.workspace_tab
    session = runtime.open_session(workspace.workspace_tab_id)
    return runtime, runner, workspace.workspace_tab_id, session.session_tab_id, launches


def _shutdown_runtime(runtime: AppRuntime) -> None:
    runtime.shutdown()
    _wait_until(
        lambda: runtime.process_background_events() >= 0
        and not runtime.has_pending_background_work()
    )
