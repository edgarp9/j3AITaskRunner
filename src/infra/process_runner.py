"""Common subprocess execution for external agent CLI providers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
from typing import IO, Any

from domain import (
    DEFAULT_AGENT_PROVIDER,
    AppSettings,
    is_execution_control_limit_enabled,
    normalize_agent_provider,
)
from domain.models import (
    EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
    TERMINATION_GRACE_SECONDS_MAX,
    utc_now,
)

from .agent_contract import (
    AgentCliAdapter,
    AgentParseSummary,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    AgentStreamEvent,
    ExecutionArtifactPaths,
    PopenLike,
    SupportsAgentExecutionRequest,
)
from .codex_adapter import (
    CodexCliAdapter,
    build_codex_command,
    build_codex_environment,
    build_codex_popen_kwargs,
)
from .claude_code_adapter import ClaudeCodeCliAdapter, build_claude_code_command
from .open_code_adapter import (
    KiloCodeCliAdapter,
    OpenCodeCliAdapter,
    build_kilo_code_command,
    build_opencode_command,
)
from .pi_adapter import PiCliAdapter, build_pi_command
from .subprocess_options import (
    WINDOWS_CREATE_NO_WINDOW,
    hidden_console_creationflags,
)

LOGGER = logging.getLogger(__name__)

_DEFAULT_TERMINATE_TIMEOUT_SECONDS = 5.0
_TERMINATE_POLL_INTERVAL_SECONDS = 0.1
_TIMEOUT_MONITOR_POLL_INTERVAL_SECONDS = 0.5
_WAIT_POLL_INTERVAL_SECONDS = 0.1
_TIMEOUT_EXIT_FALLBACK_SECONDS = 0.5
_STREAM_READER_JOIN_TIMEOUT_SECONDS = 1.0
_STREAM_READER_JOIN_POLL_INTERVAL_SECONDS = 0.05
_STDIN_WRITE_CHUNK_SIZE = 64 * 1024
_ARTIFACT_FILE_FLUSH_LINE_INTERVAL = 64
_JOB_ID_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")
_WINDOWS_CREATE_NO_WINDOW = WINDOWS_CREATE_NO_WINDOW
_WINDOWS_TASKKILL_TIMEOUT_SECONDS = 5.0


SupportsCodexExecutionRequest = SupportsAgentExecutionRequest
CodexRunRequest = AgentRunRequest
CodexRunStatus = AgentRunStatus
CodexRunResult = AgentRunResult
ClaudeCodeRunRequest = AgentRunRequest
OpenCodeRunRequest = AgentRunRequest
KiloCodeRunRequest = AgentRunRequest
PiRunRequest = AgentRunRequest

_AGENT_ADAPTER_FACTORIES: dict[str, Callable[[], AgentCliAdapter]] = {
    DEFAULT_AGENT_PROVIDER: CodexCliAdapter,
    "claude_code": ClaudeCodeCliAdapter,
    "opencode": OpenCodeCliAdapter,
    "kilo_code": KiloCodeCliAdapter,
    "pi": PiCliAdapter,
}


def build_agent_cli_adapter(provider_id: str | None) -> AgentCliAdapter | None:
    """Return a supported provider adapter, leaving unsupported providers unimplemented."""
    normalized_provider = normalize_agent_provider(provider_id)
    factory = _AGENT_ADAPTER_FACTORIES.get(normalized_provider)
    if factory is None:
        return None
    return factory()


class ProcessLaunchError(RuntimeError):
    """Raised when an agent CLI process cannot be started."""

    def __init__(self, message: str, *, result: AgentRunResult) -> None:
        super().__init__(message)
        self.result = result

def _append_stdin_diagnostic(
    stderr_log_path: Path,
    message: str,
    *,
    file_logging_enabled: bool,
) -> None:
    if file_logging_enabled:
        _append_text_file(stderr_log_path, message)


def _terminate_process_tree(
    process: PopenLike,
    *,
    force: bool,
    os_name: str | None = None,
) -> None:
    """Terminate the process tree when the platform exposes a safe primitive."""
    platform_name = os_name or os.name
    pid = process.pid

    if platform_name == "nt":
        if pid is not None:
            descendant_pids = _collect_windows_descendant_pids(pid)
            if _kill_windows_process_tree(pid, force=force):
                return
            _kill_windows_descendant_processes(descendant_pids, force=True)
        _terminate_single_process(process, force=force)
        return

    if pid is not None and _signal_posix_process_tree(pid, force=force):
        return

    _terminate_single_process(process, force=force)


def _terminate_single_process(process: PopenLike, *, force: bool) -> None:
    if force:
        process.kill()
        return
    process.terminate()


def _kill_windows_process_tree(
    pid: int,
    *,
    force: bool,
    run: Callable[..., subprocess.CompletedProcess[Any]] | None = None,
) -> bool:
    runner = run or subprocess.run
    command = ("taskkill", "/PID", str(pid), "/T")
    if force:
        command = (*command, "/F")
    try:
        completed = runner(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_WINDOWS_TASKKILL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        LOGGER.warning(
            "Failed to run Windows process tree termination helper. pid=%s force=%s",
            pid,
            force,
            exc_info=True,
        )
        return False

    if completed.returncode == 0:
        return True

    LOGGER.warning(
        "Windows process tree termination helper returned non-zero exit code. pid=%s force=%s exit_code=%s",
        pid,
        force,
        completed.returncode,
    )
    return False


def _kill_windows_descendant_processes(descendant_pids: tuple[int, ...], *, force: bool) -> None:
    for descendant_pid in reversed(descendant_pids):
        _kill_windows_process_tree(descendant_pid, force=force)


def _collect_windows_descendant_pids(pid: int) -> tuple[int, ...]:
    if os.name != "nt":
        return ()

    try:
        process_parents = _snapshot_windows_process_parents()
    except Exception:
        LOGGER.warning(
            "Failed to snapshot Windows process tree. pid=%s",
            pid,
            exc_info=True,
        )
        return ()

    descendants: list[int] = []
    pending_parent_pids = [pid]
    while pending_parent_pids:
        parent_pid = pending_parent_pids.pop()
        child_pids = sorted(
            child_pid
            for child_pid, child_parent_pid in process_parents.items()
            if child_parent_pid == parent_pid and child_pid not in descendants
        )
        descendants.extend(child_pids)
        pending_parent_pids.extend(child_pids)
    return tuple(descendants)


def _snapshot_windows_process_parents() -> dict[int, int]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")

    parents: dict[int, int] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        has_entry = process_first(snapshot, ctypes.byref(entry))
        while has_entry:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            has_entry = process_next(snapshot, ctypes.byref(entry))
    finally:
        close_handle(snapshot)
    return parents


def _signal_posix_process_tree(pid: int, *, force: bool) -> bool:
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        process_group_id = os.getpgid(pid)
        if process_group_id == os.getpgrp():
            LOGGER.warning(
                "Refusing to signal current POSIX process group. pid=%s signal=%s",
                pid,
                sig,
            )
            return False
        os.killpg(process_group_id, sig)
        return True
    except ProcessLookupError:
        return True
    except (AttributeError, OSError):
        LOGGER.warning(
            "Failed to signal POSIX process group. pid=%s signal=%s",
            pid,
            sig,
            exc_info=True,
        )
        return False


def _build_codex_popen_kwargs(process_cwd: str, *, os_name: str | None = None) -> dict[str, Any]:
    return build_codex_popen_kwargs(process_cwd, os_name=os_name)


def _build_codex_environment() -> dict[str, str]:
    return build_codex_environment()


def _hidden_console_creationflags(*, os_name: str | None = None) -> int:
    return hidden_console_creationflags(os_name=os_name)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _minutes_to_seconds(value: int) -> float | None:
    if not is_execution_control_limit_enabled(value):
        return None
    bounded_value = _bounded_execution_control_value(
        value,
        max_value=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
        field_name="timeout_minutes",
    )
    return float(bounded_value * 60)


def _termination_timeout_from_settings(settings: AppSettings) -> float | None:
    if settings.termination_grace_seconds < 0:
        return None
    bounded_value = _bounded_execution_control_value(
        settings.termination_grace_seconds,
        max_value=TERMINATION_GRACE_SECONDS_MAX,
        field_name="termination_grace_seconds",
    )
    return float(bounded_value)


def _bounded_execution_control_value(
    value: int,
    *,
    max_value: int,
    field_name: str,
) -> int:
    if value <= max_value:
        return value
    LOGGER.warning(
        "Execution control setting exceeded the maximum; using maximum. field=%s value=%s max=%s",
        field_name,
        value,
        max_value,
    )
    return max_value


def _write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)


def _try_append_text_file(path: Path, content: str, *, context: str, job_id: str) -> None:
    try:
        _append_text_file(path, content)
    except OSError:
        LOGGER.exception(
            "Failed to %s. job_id=%s path=%s",
            context,
            job_id,
            path,
        )


def _should_stop_stdin_write(should_stop: Callable[[], bool] | None) -> bool:
    if should_stop is None:
        return False
    try:
        return should_stop()
    except Exception:
        LOGGER.exception("Failed to check agent CLI stdin cancellation state.")
        return False


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _try_write_json_file(path: Path, payload: dict[str, Any], *, context: str, job_id: str) -> None:
    try:
        _write_json_file(path, payload)
    except OSError:
        LOGGER.exception(
            "Failed to %s. job_id=%s path=%s",
            context,
            job_id,
            path,
        )


def _resolve_workspace_cwd(workspace_path: str | None) -> str:
    normalized_path = _require_text(workspace_path, field_name="workspace_path")
    return str(Path(normalized_path).resolve())


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized:
        return normalized
    return None


def _require_text(value: str | None, *, field_name: str) -> str:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        raise ValueError(f"{field_name} must not be blank.")
    return normalized

from .process_runner_process import RunningAgentProcess
from .process_runner_runner import (
    AgentCliProcessRunner,
    ProviderAgentCliProcessRunner,
    ClaudeCodeCliProcessRunner,
    OpenCodeCliProcessRunner,
    KiloCodeCliProcessRunner,
    PiCliProcessRunner,
    CodexCliProcessRunner,
)

RunningCodexProcess = RunningAgentProcess
