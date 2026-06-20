from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest
from unittest.mock import patch

from app.agent_cli_version import (
    load_agent_cli_version_text,
)
from infra.agent_cli_version import query_agent_cli_version
from infra.codex_cli_version import query_codex_cli_version
from infra.executable import resolve_executable_reference


class AgentCliVersionTextTests(unittest.TestCase):
    def test_load_agent_cli_version_text_reports_missing_path(self) -> None:
        self.assertEqual("실행기 경로 없음", load_agent_cli_version_text(None))


class CodexCliVersionCompatibilityTests(unittest.TestCase):
    def test_query_codex_cli_version_returns_stdout_line(self) -> None:
        with TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "codex.exe"
            executable.write_text("", encoding="utf-8")

            completed = subprocess.CompletedProcess(
                args=(str(executable), "--version"),
                returncode=0,
                stdout="codex-cli 0.126.0-alpha.8\n",
                stderr="",
            )
            with patch("infra.agent_cli_version.subprocess.run", return_value=completed):
                result = query_codex_cli_version(str(executable))

        self.assertTrue(result.success)
        self.assertEqual("codex-cli 0.126.0-alpha.8", result.version_text)

    def test_query_codex_cli_version_accepts_path_command_name(self) -> None:
        completed = subprocess.CompletedProcess(
            args=("/usr/local/bin/codex", "--version"),
            returncode=0,
            stdout="codex-cli 0.126.0-alpha.8\n",
            stderr="",
        )

        with (
            patch("infra.executable.shutil.which", return_value="/usr/local/bin/codex"),
            patch("infra.agent_cli_version.subprocess.run", return_value=completed) as run,
        ):
            result = query_codex_cli_version("codex")

        self.assertTrue(result.success)
        self.assertEqual("codex-cli 0.126.0-alpha.8", result.version_text)
        self.assertEqual(
            (str(Path("/usr/local/bin/codex").resolve()), "--version"),
            run.call_args.args[0],
        )

    def test_query_codex_cli_version_accepts_codex_cli_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "codex-x86_64-unknown-linux-musl"
            executable.write_text("", encoding="utf-8")
            executable.chmod(0o755)

            completed = subprocess.CompletedProcess(
                args=(str(executable), "--version"),
                returncode=0,
                stdout="codex-cli 0.131.0\n",
                stderr="",
            )

            with patch("infra.agent_cli_version.subprocess.run", return_value=completed) as run:
                result = query_codex_cli_version(temp_dir)

        self.assertTrue(result.success)
        self.assertEqual("codex-cli 0.131.0", result.version_text)
        self.assertEqual((str(executable.resolve()), "--version"), run.call_args.args[0])

    def test_resolve_executable_reference_uses_provider_directory_candidates(self) -> None:
        provider_files = {
            "codex": "codex-x86_64-unknown-linux-musl",
            "claude_code": "claude.exe",
            "kilo_code": "kilo.exe",
            "opencode": "opencode.exe",
            "pi": "pi.exe",
        }

        for provider, executable_name in provider_files.items():
            with self.subTest(provider=provider), TemporaryDirectory() as temp_dir:
                executable = Path(temp_dir) / executable_name
                executable.write_text("", encoding="utf-8")

                resolved = resolve_executable_reference(
                    temp_dir,
                    agent_provider=provider,
                )

                self.assertEqual(executable.resolve(), resolved)

    def test_query_agent_cli_version_accepts_provider_command_name(self) -> None:
        completed = subprocess.CompletedProcess(
            args=("/usr/local/bin/claude", "--version"),
            returncode=0,
            stdout="claude-code 1.2.3\n",
            stderr="",
        )

        with (
            patch("infra.executable.shutil.which", return_value="/usr/local/bin/claude"),
            patch("infra.agent_cli_version.subprocess.run", return_value=completed) as run,
        ):
            result = query_agent_cli_version("claude", agent_provider="claude_code")

        self.assertTrue(result.success)
        self.assertEqual("claude-code 1.2.3", result.version_text)
        self.assertEqual(
            (str(Path("/usr/local/bin/claude").resolve()), "--version"),
            run.call_args.args[0],
        )

    def test_query_agent_cli_version_uses_provider_adapter_version_command(self) -> None:
        class _VersionAdapter:
            def build_version_command(self, executable_reference: str) -> tuple[str, ...]:
                return (executable_reference, "version", "--json")

        with TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "custom-agent.exe"
            executable.write_text("", encoding="utf-8")

            completed = subprocess.CompletedProcess(
                args=(str(executable), "version", "--json"),
                returncode=0,
                stdout='{"version":"custom-agent 1.2.3"}\n',
                stderr="",
            )

            with (
                patch(
                    "infra.agent_cli_version.build_agent_cli_adapter",
                    return_value=_VersionAdapter(),
                ),
                patch("infra.agent_cli_version.subprocess.run", return_value=completed) as run,
            ):
                result = query_agent_cli_version(
                    str(executable),
                    agent_provider="opencode",
                )

        self.assertTrue(result.success)
        self.assertEqual('{"version":"custom-agent 1.2.3"}', result.version_text)
        self.assertEqual(
            (str(executable.resolve()), "version", "--json"),
            run.call_args.args[0],
        )

    def test_load_agent_cli_version_text_uses_provider_directory_resolution(self) -> None:
        with TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "opencode.exe"
            executable.write_text("", encoding="utf-8")

            completed = subprocess.CompletedProcess(
                args=(str(executable), "--version"),
                returncode=0,
                stdout="opencode 0.9.0\n",
                stderr="",
            )

            with patch("infra.agent_cli_version.subprocess.run", return_value=completed):
                version_text = load_agent_cli_version_text(
                    temp_dir,
                    agent_provider="opencode",
                )

        self.assertEqual("opencode 0.9.0", version_text)

    def test_load_agent_cli_version_text_formats_invalid_path(self) -> None:
        self.assertEqual(
            "실행기 경로를 확인하세요.",
            load_agent_cli_version_text(r"C:\missing\agent.exe"),
        )


if __name__ == "__main__":
    unittest.main()


