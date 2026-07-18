from __future__ import annotations

from tests._process_runner_helpers import *

class CodexCliProcessRunnerTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.runner_root = Path(self.temp_dir.name)
        self.request = CodexRunRequest(
            job_id="job-1",
            workspace_path=self.temp_dir.name,
            prompt="Solve this task.",
            operational_settings=AppSettings(
                executable_path=str(_create_fake_executable(self.runner_root, "codex.exe")),
                file_logging_enabled=True,
            ),
            execution_options=AgentExecutionOptions(
                model="gpt-5.4",
                reasoning_effort="high",
            ),
            session_id=None,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

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

