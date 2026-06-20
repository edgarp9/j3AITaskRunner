"""Executable reference normalization and lookup helpers."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

from domain import DEFAULT_AGENT_PROVIDER, normalize_agent_provider

CODEX_EXECUTABLE_NAMES = ("codex", "codex.exe")
CODEX_EXECUTABLE_GLOB_PATTERNS = ("codex-*", "codex*.exe")

PROVIDER_EXECUTABLE_NAMES = {
    DEFAULT_AGENT_PROVIDER: CODEX_EXECUTABLE_NAMES,
    "claude_code": ("claude", "claude.exe"),
    "kilo_code": ("kilo", "kilo.exe"),
    "opencode": ("opencode", "opencode.exe"),
    "pi": ("pi", "pi.exe"),
}
PROVIDER_EXECUTABLE_GLOB_PATTERNS = {
    DEFAULT_AGENT_PROVIDER: CODEX_EXECUTABLE_GLOB_PATTERNS,
    "claude_code": (),
    "kilo_code": (),
    "opencode": (),
    "pi": (),
}


def normalize_executable_reference(value: str | None) -> str | None:
    """Return a cleaned executable path or command name."""
    normalized = (value or "").strip()
    if not normalized:
        return None
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        normalized = normalized[1:-1].strip()
    return normalized or None


def resolve_executable_reference(
    value: str | None,
    *,
    agent_provider: str | None = DEFAULT_AGENT_PROVIDER,
) -> Path | None:
    """Resolve an executable path or a command available on PATH."""
    normalized = normalize_executable_reference(value)
    if normalized is None:
        return None

    normalized_provider = normalize_agent_provider(agent_provider)
    candidate = Path(normalized).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    if candidate.is_dir():
        return _resolve_provider_executable_directory(
            candidate,
            agent_provider=normalized_provider,
        )

    if _looks_like_path_reference(normalized):
        return None

    resolved_command = shutil.which(normalized)
    if resolved_command is None:
        return None
    return Path(resolved_command).resolve()


def executable_command_for_launch(
    value: str | None,
    *,
    agent_provider: str | None = DEFAULT_AGENT_PROVIDER,
) -> str:
    """Return the subprocess command text for an executable reference."""
    normalized = normalize_executable_reference(value)
    if normalized is None:
        raise ValueError("executable reference must not be blank.")

    resolved = resolve_executable_reference(
        normalized,
        agent_provider=agent_provider,
    )
    if resolved is not None:
        return str(resolved)
    return normalized


def _looks_like_path_reference(value: str) -> bool:
    if "/" in value or "\\" in value:
        return True
    return Path(value).expanduser().is_absolute()


def _resolve_provider_executable_directory(
    directory: Path,
    *,
    agent_provider: str,
) -> Path | None:
    """Return a provider executable inside a configured directory."""
    names = PROVIDER_EXECUTABLE_NAMES.get(agent_provider, ())
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate.resolve()

    candidates: list[Path] = []
    patterns = PROVIDER_EXECUTABLE_GLOB_PATTERNS.get(agent_provider, ())
    for pattern in patterns:
        candidates.extend(path for path in directory.glob(pattern) if path.is_file())

    if not candidates:
        return None

    unique_candidates = sorted(set(candidates), key=lambda path: path.name.lower())
    executable_candidates = [
        candidate for candidate in unique_candidates if os.access(candidate, os.X_OK)
    ]
    return (executable_candidates or unique_candidates)[0].resolve()
