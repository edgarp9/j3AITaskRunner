from __future__ import annotations

from tests._process_runner_helpers import *

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
                file_logging_enabled=True,
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
                file_logging_enabled=True,
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
                file_logging_enabled=True,
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

