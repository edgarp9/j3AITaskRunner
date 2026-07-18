from __future__ import annotations

from tests._process_runner_helpers import *

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
                model="gpt-5.6-sol",
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
                "gpt-5.6-sol",
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

