"""Application-facing agent CLI version text formatting."""

from __future__ import annotations

from domain import DEFAULT_AGENT_PROVIDER
from infra.agent_cli_version import query_agent_cli_version

CLI_VERSION_ISSUE_MESSAGES = {
    "missing_executable_path": "실행기 경로 없음",
    "invalid_executable_path": "실행기 경로를 확인하세요.",
    "timeout": "버전 확인 시간 초과",
    "launch_failed": "버전 확인 실행 실패",
    "nonzero_exit": "버전 확인 실패",
    "empty_output": "버전 출력 없음",
}


def load_agent_cli_version_text(
    executable_path: str | None,
    agent_provider: str | None = DEFAULT_AGENT_PROVIDER,
) -> str:
    """Return user-facing version text for the currently configured agent CLI."""
    result = query_agent_cli_version(
        executable_path,
        agent_provider=agent_provider,
    )
    if result.success:
        return result.version_text or "버전 출력 없음"

    return CLI_VERSION_ISSUE_MESSAGES.get(result.issue_code or "", "버전 확인 불가")
