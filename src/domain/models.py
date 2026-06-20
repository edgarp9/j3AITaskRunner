"""Core domain models for j3AITaskRunner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import TypeAlias

WorkspacePath: TypeAlias = str
WorkspaceTabId: TypeAlias = str
SessionTabId: TypeAlias = str
JobId: TypeAlias = str
SessionId: TypeAlias = str
AgentProvider: TypeAlias = str

EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX = 525_600
TERMINATION_GRACE_SECONDS_MAX = 86_400
DEFAULT_AGENT_PROVIDER: AgentProvider = "codex"
SUPPORTED_AGENT_PROVIDERS: tuple[AgentProvider, ...] = (
    "codex",
    "claude_code",
    "kilo_code",
    "opencode",
    "pi",
)
_AGENT_PROVIDER_ALIASES: dict[str, AgentProvider] = {
    "open_code": "opencode",
    "pi_coding_agent": "pi",
    "pi_dev": "pi",
    "pi.dev": "pi",
    "https://pi.dev": "pi",
}


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(tz=timezone.utc)


def normalize_agent_provider(value: object) -> AgentProvider:
    """Return a supported agent provider, falling back to the current default."""
    return _normalize_agent_provider_key(value) or DEFAULT_AGENT_PROVIDER


def normalize_agent_executable_paths(value: object) -> dict[AgentProvider, str]:
    """Return executable references keyed by supported agent provider ids."""
    if not isinstance(value, Mapping):
        return {}

    normalized_paths: dict[AgentProvider, str] = {}
    for raw_provider, raw_path in value.items():
        provider = _normalize_agent_provider_key(raw_provider)
        executable_path = _normalize_optional_text(raw_path)
        if provider is None or executable_path is None:
            continue
        normalized_paths[provider] = executable_path
    return normalized_paths


def _normalize_agent_provider_key(value: object) -> AgentProvider | None:
    if not isinstance(value, str):
        return None

    candidate = "_".join(value.strip().lower().replace("-", "_").split())
    candidate = _AGENT_PROVIDER_ALIASES.get(candidate, candidate)
    if candidate in SUPPORTED_AGENT_PROVIDERS:
        return candidate
    return None


def _normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


class QueueStatus(str, Enum):
    """Runtime state of one workspace queue."""

    STARTED = "started"
    STOPPED = "stopped"


class QueueStopReason(str, Enum):
    """Common reasons for stopping one workspace queue."""

    USER_STOPPED = "user_stopped"
    RUNNING_TAB_CLOSED = "running_tab_closed"
    PRESET_FLOW_FAILED = "preset_flow_failed"
    ALL_JOBS_COMPLETED = "all_jobs_completed"


class TabOpenState(str, Enum):
    """Open or closed runtime state for tabs."""

    OPEN = "open"
    CLOSED = "closed"


class SessionTabKind(str, Enum):
    """Business role of a session tab inside one workspace tab."""

    NORMAL = "normal"
    PRESET = "preset"
    PRESET_CANDIDATE = "preset_candidate"


class JobStatus(str, Enum):
    """Execution lifecycle state of a job."""

    QUEUED = "queued"
    WAITING_FOR_CONFIGURATION = "waiting_for_configuration"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(slots=True, frozen=True)
class AppSettings:
    """Persistent application settings."""

    executable_path: str | None = None
    executable_paths: Mapping[AgentProvider, str] = field(
        default_factory=dict,
        hash=False,
    )
    output_font_size: int = 12
    execution_timeout_minutes: int = 120
    inactivity_timeout_minutes: int = 30
    termination_grace_seconds: int = 5
    file_logging_enabled: bool = True
    ui_language: str = "en"
    agent_provider: AgentProvider = DEFAULT_AGENT_PROVIDER
    default_model: str = ""
    default_reasoning_effort: str = ""

    def __post_init__(self) -> None:
        normalized_provider = normalize_agent_provider(self.agent_provider)
        executable_paths = normalize_agent_executable_paths(self.executable_paths)
        executable_path = _normalize_optional_text(self.executable_path)
        if executable_path is not None:
            executable_paths[normalized_provider] = executable_path
        else:
            executable_path = executable_paths.get(normalized_provider)

        object.__setattr__(
            self,
            "agent_provider",
            normalized_provider,
        )
        object.__setattr__(
            self,
            "default_model",
            _normalize_execution_option(self.default_model),
        )
        object.__setattr__(
            self,
            "default_reasoning_effort",
            _normalize_execution_option(self.default_reasoning_effort),
        )
        object.__setattr__(self, "executable_path", executable_path)
        object.__setattr__(
            self,
            "executable_paths",
            MappingProxyType(dict(executable_paths)),
        )
        _validate_bounded_non_negative_int_setting(
            "execution_timeout_minutes",
            self.execution_timeout_minutes,
            max_value=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
        )
        _validate_bounded_non_negative_int_setting(
            "inactivity_timeout_minutes",
            self.inactivity_timeout_minutes,
            max_value=EXECUTION_CONTROL_TIMEOUT_MINUTES_MAX,
        )
        _validate_bounded_non_negative_int_setting(
            "termination_grace_seconds",
            self.termination_grace_seconds,
            max_value=TERMINATION_GRACE_SECONDS_MAX,
        )


def _validate_bounded_non_negative_int_setting(
    field_name: str,
    value: int,
    *,
    max_value: int,
) -> None:
    if type(value) is not int or value < 0 or value > max_value:
        raise ValueError(f"{field_name} must be an integer between 0 and {max_value}.")


@dataclass(slots=True, frozen=True)
class InstructionInfo:
    """Prompt instruction metadata resolved from the prompt asset tree."""

    language: str
    instruction: str
    analysis_prompt_path: str
    work_prompt_template_path: str


@dataclass(slots=True, frozen=True)
class SavedWorkspace:
    """Persistent workspace entry shown to users."""

    path: WorkspacePath
    display_name: str
    added_at: datetime
    last_selected_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class WorkspaceQueueState:
    """Runtime state of one workspace queue."""

    workspace_tab_id: WorkspaceTabId
    status: QueueStatus = QueueStatus.STOPPED
    running_job_id: JobId | None = None
    last_stop_reason: QueueStopReason | str | None = None


@dataclass(slots=True, frozen=True)
class TabNameState:
    """Runtime counters for default session tab names."""

    next_session_numbers: Mapping[WorkspaceTabId, int] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkspaceTab:
    """Runtime state of a workspace tab."""

    workspace_tab_id: WorkspaceTabId
    workspace_path: WorkspacePath
    display_name: str
    open_state: TabOpenState = TabOpenState.OPEN
    sort_order: int = 0
    active_session_tab_id: SessionTabId | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True, frozen=True)
class AgentExecutionOptions:
    """Provider/model/reasoning selection captured for a session or job."""

    agent_provider: AgentProvider = DEFAULT_AGENT_PROVIDER
    model: str = ""
    reasoning_effort: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "agent_provider",
            normalize_agent_provider(self.agent_provider),
        )
        object.__setattr__(self, "model", _normalize_execution_option(self.model))
        object.__setattr__(
            self,
            "reasoning_effort",
            _normalize_execution_option(self.reasoning_effort),
        )


def execution_options_from_settings(settings: AppSettings) -> AgentExecutionOptions:
    """Return session execution options seeded from app settings."""
    return AgentExecutionOptions(
        agent_provider=settings.agent_provider,
        model=settings.default_model,
        reasoning_effort=settings.default_reasoning_effort,
    )


def _normalize_execution_option(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


@dataclass(slots=True, frozen=True)
class SessionTab:
    """Runtime state of a session tab inside a workspace tab."""

    session_tab_id: SessionTabId
    workspace_tab_id: WorkspaceTabId
    display_name: str
    kind: SessionTabKind = SessionTabKind.NORMAL
    session_id: SessionId | None = None
    parent_session_tab_id: SessionTabId | None = None
    candidate_index: int | None = None
    execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )
    execution_options_locked: bool = False
    open_state: TabOpenState = TabOpenState.OPEN
    sort_order: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True, frozen=True)
class ProcessMetadata:
    """Runtime process details captured for debugging."""

    pid: int | None = None
    exit_code: int | None = None
    launch_command: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ExecutionMetadata:
    """Last applied execution settings captured for debugging."""

    model: str | None = None
    reasoning_effort: str | None = None
    codex_cli_version: str | None = None
    agent_provider: AgentProvider = DEFAULT_AGENT_PROVIDER
    agent_version: str | None = None

    def __post_init__(self) -> None:
        normalized_provider = normalize_agent_provider(self.agent_provider)
        object.__setattr__(self, "agent_provider", normalized_provider)
        if self.agent_version is None and self.codex_cli_version is not None:
            object.__setattr__(self, "agent_version", self.codex_cli_version)
        elif (
            self.codex_cli_version is None
            and normalized_provider == DEFAULT_AGENT_PROVIDER
            and self.agent_version is not None
        ):
            object.__setattr__(self, "codex_cli_version", self.agent_version)


@dataclass(slots=True, frozen=True)
class Job:
    """Runtime job state for a single prompt execution request."""

    job_id: JobId
    workspace_tab_id: WorkspaceTabId
    session_tab_id: SessionTabId
    prompt: str
    status: JobStatus = JobStatus.QUEUED
    configuration_wait_reason: str | None = None
    user_message: str | None = None
    queue_order: int | None = None
    process_metadata: ProcessMetadata | None = None
    applied_execution_metadata: ExecutionMetadata | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    force_fresh_session: bool = False
    execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )


@dataclass(slots=True, frozen=True)
class SessionTurnHistory:
    """Runtime turn history kept for a session within the current app run."""

    workspace_path: WorkspacePath
    session_tab_id: SessionTabId
    session_id: SessionId | None
    prompt_text: str
    response_text: str | None
    started_at: datetime
    completed_at: datetime | None
    last_activity_at: datetime
    job_id: JobId | None = None
