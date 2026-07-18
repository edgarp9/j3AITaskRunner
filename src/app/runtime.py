"""Application runtime facade for bootstrapping persistence and UI-facing state."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime
import logging
from pathlib import Path
from queue import Empty, Queue
import threading
from typing import Protocol

from domain import (
    AgentExecutionOptions,
    AppSettings,
    InstructionInfo,
    Job,
    JobStatus,
    PresetCandidate,
    QueueStopReason,
    QueueStatus,
    SavedWorkspace,
    SessionTab,
    SessionTabId,
    SessionTabKind,
    SessionTurnHistory,
    WorkspaceTab,
    WorkspaceQueueState,
    execution_options_from_settings,
    extract_candidates,
    select_work_candidates,
    workspace_folder_display_name,
)
from domain.models import TabOpenState, utc_now
from domain.policies import canonicalize_workspace_path
from infra.process_runner import AgentRunStatus
from infra.session_exit_hook import SessionExitHookRunner, launch_session_exit_hook

from .controller import (
    AppController,
    ControllerEvent,
    JobExecutionResultCapturedEvent,
    JobStatusChangedEvent,
    LogAppendedEvent,
)
from .scheduler import WorkspaceJobSummary
from .session_manager import CompletedSessionSummary
from .use_cases import (
    PersistentDataRepository,
    SaveResult,
    UseCaseIssue,
    load_bootstrap_data,
    save_app_settings,
    save_saved_workspaces,
    parse_preset_generated_work_prompts,
    prepare_preset_work_generation_prompt,
)
from .workspace_manager import WorkspaceOpenResult, validate_workspace_path

LOGGER = logging.getLogger(__name__)
MANUAL_PRESET_WORK_PRIORITY = "manual"
PRESET_WORK_PRIORITY_OPTIONS = ("high", "medium", "low", MANUAL_PRESET_WORK_PRIORITY)
DEFAULT_PRESET_WORK_PRIORITY = "medium"
AUTO_COMMIT_PROMPT = "커밋해 주세요."
MAX_JOB_PROGRESS_LOG_LINES = 5_000
_PERSISTENCE_COALESCE_SETTINGS = "settings"
_PERSISTENCE_COALESCE_SAVED_WORKSPACES = "saved_workspaces"


class SystemSleepPreventerProtocol(Protocol):
    """Minimal contract for OS-level idle sleep prevention."""

    def set_active(self, active: bool) -> None:
        """Enable or disable sleep prevention."""

    def release(self) -> None:
        """Release any active sleep prevention request."""


class PromptStoreProtocol(Protocol):
    """Prompt asset lookup contract used by preset sessions."""

    def list_languages(self) -> list[str]:
        """Return available prompt language names."""

    def list_instructions(self, language: str) -> list[InstructionInfo]:
        """Return available instructions for one language."""

    def read_analysis_prompt(self, language: str, instruction: str) -> str:
        """Return the analysis prompt for one preset instruction."""

    def read_work_prompt_template(self, language: str, instruction: str) -> str:
        """Return the work prompt template for one preset instruction."""


@dataclass(slots=True, frozen=True)
class WorkspaceOpenActionResult:
    """Outcome of opening a workspace and saving it for future sessions."""

    open_result: WorkspaceOpenResult
    persistence_issue: UseCaseIssue | None = None


@dataclass(slots=True, frozen=True)
class SettingsUpdateResult:
    """Outcome of saving settings and retrying waiting jobs."""

    persistence_issue: UseCaseIssue | None = None
    retried_job_ids: tuple[str, ...] = ()
    queue_mode_changed: bool = False
    cleared_job_count: int = 0


@dataclass(slots=True, frozen=True)
class ImportedPromptSessionRegistration:
    """One normal session and job pair created by bulk prompt import."""

    session_tab: SessionTab
    prompt_job: Job
    auto_commit_job: Job | None = None


@dataclass(slots=True, frozen=True)
class ImportedPromptSessionsResult:
    """Outcome of registering imported prompt blocks as normal sessions."""

    registrations: tuple[ImportedPromptSessionRegistration, ...]

    @property
    def session_tabs(self) -> tuple[SessionTab, ...]:
        """Return created normal session tabs in first-seen import order."""
        session_tabs: list[SessionTab] = []
        seen_session_tab_ids: set[str] = set()
        for registration in self.registrations:
            session_tab = registration.session_tab
            if session_tab.session_tab_id in seen_session_tab_ids:
                continue
            seen_session_tab_ids.add(session_tab.session_tab_id)
            session_tabs.append(session_tab)
        return tuple(session_tabs)

    @property
    def registered_jobs(self) -> tuple[Job, ...]:
        """Return created jobs in queue registration order."""
        jobs: list[Job] = []
        for registration in self.registrations:
            jobs.append(registration.prompt_job)
            if registration.auto_commit_job is not None:
                jobs.append(registration.auto_commit_job)
        return tuple(jobs)


@dataclass(slots=True, frozen=True)
class PersistenceIssueEvent:
    """UI-safe event describing a background persistence failure."""

    issue: UseCaseIssue


@dataclass(slots=True, frozen=True)
class QueueStartCompletedEvent:
    """UI-safe event emitted after a background queue-start request finishes."""

    workspace_tab_id: str
    display_name: str


@dataclass(slots=True, frozen=True)
class WorkspaceOpenCompletedEvent:
    """UI-safe event emitted after a background workspace-open request finishes."""

    workspace_path: str
    workspace_tab_id: str
    created: bool


@dataclass(slots=True, frozen=True)
class SettingsRetryCompletedEvent:
    """UI-safe event emitted after settings-triggered waiting-job retries finish."""

    retried_job_ids: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class PresetCandidateJobsRegisteredEvent:
    """UI-safe event emitted after preset candidate jobs are registered."""

    workspace_tab_id: str
    parent_session_tab_id: str
    candidate_session_tab_ids: tuple[str, ...]
    registered_job_ids: tuple[str, ...]
    auto_commit_enabled: bool


@dataclass(slots=True, frozen=True)
class PresetManualCandidateSelectionRequiredEvent:
    """UI-safe event emitted when manual preset candidate selection is needed."""

    workspace_tab_id: str
    parent_session_tab_id: str
    candidates: tuple[PresetCandidate, ...]


@dataclass(slots=True, frozen=True)
class PresetManualCandidateSelectionContinuedEvent:
    """UI-safe event emitted after a manual candidate selection starts turn2."""

    workspace_tab_id: str
    parent_session_tab_id: str
    selected_candidate_ids: tuple[str, ...]
    work_generation_job_id: str


@dataclass(slots=True, frozen=True)
class PresetManualCandidateSelectionClearedEvent:
    """UI-safe event emitted when a manual candidate wait state is cleared."""

    workspace_tab_id: str
    parent_session_tab_id: str
    message: str = ""


@dataclass(slots=True, frozen=True)
class PresetAnalysisJobSubmittedEvent:
    """UI-safe event emitted after a preset analysis job is submitted."""

    workspace_tab_id: str
    session_tab_id: str
    job_id: str
    analysis_prompt_prefix: str = ""


@dataclass(slots=True, frozen=True)
class PresetAnalysisJobSubmissionFailedEvent:
    """UI-safe event emitted when preset analysis job submission fails."""

    session_tab_id: str
    title: str
    message: str


@dataclass(slots=True, frozen=True)
class PresetPromptLanguagesLoadedEvent:
    """UI-safe event emitted after preset prompt languages are loaded."""

    request_id: int
    session_tab_id: str
    workspace_tab_id: str
    languages: tuple[str, ...]
    error_message: str | None = None


@dataclass(slots=True, frozen=True)
class PresetPromptInstructionsLoadedEvent:
    """UI-safe event emitted after preset prompt instructions are loaded."""

    request_id: int
    session_tab_id: str
    workspace_tab_id: str
    language: str
    instructions: tuple[str, ...]
    error_message: str | None = None


@dataclass(slots=True, frozen=True)
class RuntimeActionFailedEvent:
    """UI-safe event describing a failed runtime background action."""

    title: str
    message: str
    workspace_tab_id: str | None = None


@dataclass(slots=True, frozen=True)
class RuntimeActionWarningEvent:
    """UI-safe event describing a non-fatal runtime background action warning."""

    title: str
    message: str
    workspace_tab_id: str | None = None


@dataclass(slots=True, frozen=True)
class FileDropCommandRequestedEvent:
    """UI-safe event emitted when a validated file-drop command is received."""

    request_id: str
    command_type: str


@dataclass(slots=True, frozen=True)
class FileDropIssueEvent:
    """UI-safe event describing a non-fatal file-drop polling issue."""

    code: str
    message: str
    detail: str = ""


@dataclass(slots=True, frozen=True)
class _RuntimeActionRequest:
    """One serialized runtime action that must not block the Tkinter thread."""

    action: Callable[[], "RuntimeActionEvent | None"]
    failure_title: str
    failure_message: str
    log_message: str
    workspace_tab_id: str | None = None
    queue_control_generation: tuple[int, int] | None = None
    on_discard: Callable[[], None] | None = None
    drop_completion_when_stale: bool = True


@dataclass(slots=True, frozen=True)
class _RuntimeActionCompletion:
    """Outcome of one runtime background action."""

    event: "RuntimeActionEvent | None"
    queue_control_workspace_tab_id: str | None = None
    queue_control_generation: tuple[int, int] | None = None
    drop_when_stale: bool = True


@dataclass(slots=True, frozen=True)
class _PersistenceSaveRequest:
    """One serialized background persistence request."""

    save_action: Callable[[], SaveResult]
    coalesce_key: str | None = None


@dataclass(slots=True, frozen=True)
class _PersistenceSaveCompletion:
    """Outcome of one background persistence request."""

    issue: UseCaseIssue | None = None


@dataclass(slots=True, frozen=True)
class _PresetAnalysisJobContext:
    """Runtime-only metadata needed when a preset analysis job completes."""

    language: str
    instruction: str
    work_prompt_template: str
    work_priority: str
    auto_commit_enabled: bool
    queue_control_generation: tuple[int, int]
    execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )
    candidate_execution_options: AgentExecutionOptions | None = None

    def resolved_candidate_execution_options(self) -> AgentExecutionOptions:
        """Return candidate-session options, falling back for legacy callers."""
        return self.candidate_execution_options or self.execution_options


@dataclass(slots=True, frozen=True)
class _PresetWorkGenerationJobContext:
    """Runtime-only metadata needed when a prompt-generation job completes."""

    parent_session_tab_id: str
    candidates: tuple[PresetCandidate, ...]
    auto_commit_enabled: bool
    queue_control_generation: tuple[int, int]
    execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )
    candidate_execution_options: AgentExecutionOptions | None = None

    def resolved_candidate_execution_options(self) -> AgentExecutionOptions:
        """Return candidate-session options, falling back for legacy contexts."""
        return self.candidate_execution_options or self.execution_options


@dataclass(slots=True, frozen=True)
class _PresetManualSelectionContext:
    """Runtime-only metadata held while the UI selects preset candidates."""

    workspace_tab_id: str
    parent_session_tab_id: str
    language: str
    instruction: str
    work_prompt_template: str
    candidates: tuple[PresetCandidate, ...]
    auto_commit_enabled: bool
    queue_control_generation: tuple[int, int]
    execution_options: AgentExecutionOptions = field(
        default_factory=AgentExecutionOptions
    )
    candidate_execution_options: AgentExecutionOptions | None = None

    def resolved_candidate_execution_options(self) -> AgentExecutionOptions:
        """Return candidate-session options, falling back for legacy contexts."""
        return self.candidate_execution_options or self.execution_options


RuntimeActionEvent = (
    QueueStartCompletedEvent
    | WorkspaceOpenCompletedEvent
    | SettingsRetryCompletedEvent
    | PresetAnalysisJobSubmittedEvent
    | PresetAnalysisJobSubmissionFailedEvent
    | PresetCandidateJobsRegisteredEvent
    | PresetManualCandidateSelectionRequiredEvent
    | PresetManualCandidateSelectionContinuedEvent
    | PresetManualCandidateSelectionClearedEvent
    | PresetPromptLanguagesLoadedEvent
    | PresetPromptInstructionsLoadedEvent
    | RuntimeActionFailedEvent
    | RuntimeActionWarningEvent
    | FileDropCommandRequestedEvent
    | FileDropIssueEvent
)

AppRuntimeEvent = (
    ControllerEvent
    | PersistenceIssueEvent
    | RuntimeActionEvent
    | PresetCandidateJobsRegisteredEvent
)


def _normalize_preset_work_priority(work_priority: str) -> str:
    normalized = work_priority.strip().lower()
    if normalized not in PRESET_WORK_PRIORITY_OPTIONS:
        valid_values = ", ".join(PRESET_WORK_PRIORITY_OPTIONS)
        raise ValueError(f"우선순위는 {valid_values} 중 하나여야 합니다.")
    return normalized

def _build_preset_analysis_prompt(
    analysis_prompt: str,
    *,
    work_priority: str,
    analysis_prompt_prefix: str = "",
) -> str:
    normalized_prompt = analysis_prompt.strip()
    if not normalized_prompt:
        raise ValueError("프리셋 분석 프롬프트가 비어 있습니다.")
    normalized_prefix = analysis_prompt_prefix.strip()
    if normalized_prefix:
        normalized_prompt = f"{normalized_prefix}\n\n{normalized_prompt}"
    if work_priority == MANUAL_PRESET_WORK_PRIORITY:
        return (
            f"{normalized_prompt}\n\n"
            f"선택된 Work Priority: {work_priority}\n"
            "Work Priority는 UI에서 사용자가 후보를 직접 선택하는 manual 모드이다. "
            "priority threshold로 후보를 제외하지 말고 high/medium/low 후보를 "
            "모두 candidates에 포함하라."
        )
    return (
        f"{normalized_prompt}\n\n"
        f"선택된 Work Priority: {work_priority}\n"
        "Work Priority는 최소 작업 우선순위 threshold이다. "
        "high는 priority=high 후보만 포함하고, "
        "medium은 priority=high 또는 priority=medium 후보를 포함하며, "
        "low는 priority=high/medium/low 후보를 모두 포함하라. "
        "이 기준 밖의 후보는 candidates에서 제외하라."
    )

from .runtime_workspace import AppRuntimeWorkspaceMixin
from .runtime_preset_api import AppRuntimePresetApiMixin
from .runtime_queue import AppRuntimeQueueMixin
from .runtime_workers import AppRuntimeWorkersMixin
from .runtime_preset_flow import AppRuntimePresetFlowMixin
from .runtime_session_exit_hook import AppRuntimeSessionExitHookMixin
from .runtime_file_drop import AppRuntimeFileDropMixin


class AppRuntime(
    AppRuntimeWorkspaceMixin,
    AppRuntimePresetApiMixin,
    AppRuntimeQueueMixin,
    AppRuntimeWorkersMixin,
    AppRuntimePresetFlowMixin,
    AppRuntimeSessionExitHookMixin,
    AppRuntimeFileDropMixin,
):
    """Keep persistent app data and controller event state together for the UI."""

    def __init__(
        self,
        *,
        controller: AppController,
        repository: PersistentDataRepository,
        prompt_store: PromptStoreProtocol | None = None,
        system_sleep_preventer: SystemSleepPreventerProtocol | None = None,
        session_exit_hook_runner: SessionExitHookRunner | None = None,
        file_drop_dir: Path | None = None,
        file_drop_poll_interval_seconds: float = 10.0,
    ) -> None:
        self._controller = controller
        self._repository = repository
        self._prompt_store = prompt_store
        self._system_sleep_preventer = system_sleep_preventer
        self._session_exit_hook_runner = (
            session_exit_hook_runner or launch_session_exit_hook
        )
        self._system_sleep_prevention_lock = threading.Lock()
        self._system_sleep_prevention_dirty = system_sleep_preventer is not None
        self._system_sleep_prevention_active: bool | None = None
        self._event_queue: Queue[AppRuntimeEvent] = Queue()
        self._controller_state_lock: threading.RLock = threading.RLock()
        self._runtime_action_request_queue: Queue[_RuntimeActionRequest | None] = Queue()
        self._runtime_action_completion_queue: Queue[_RuntimeActionCompletion] = Queue()
        self._persistence_request_queue: Queue[_PersistenceSaveRequest | None] = Queue()
        self._persistence_completion_queue: Queue[_PersistenceSaveCompletion] = Queue()
        self._runtime_action_shutdown_requested = False
        self._persistence_shutdown_requested = False
        self._persistence_shutdown_sentinel_enqueued = False
        self._queue_control_global_generation = 0
        self._queue_control_workspace_generations: dict[str, int] = {}
        self._queue_control_lock = threading.Lock()
        self._preset_followup_lock = threading.Lock()
        self._preset_followup_pending_workspace_counts: dict[str, int] = {}
        self._runtime_action_in_progress = False
        self._runtime_action_activity_lock = threading.Lock()
        self._dispatch_action_requested = False
        self._dispatch_action_lock = threading.Lock()
        self._runtime_action_thread = threading.Thread(
            target=self._run_runtime_action_worker,
            name="app-runtime-actions",
            daemon=True,
        )
        self._persistence_thread = threading.Thread(
            target=self._run_persistence_worker,
            name="app-runtime-persistence",
            daemon=True,
        )
        self._job_progress_logs: dict[str, deque[str]] = {}
        self._job_user_messages: dict[str, str] = {}
        self._preset_analysis_job_contexts: dict[str, _PresetAnalysisJobContext] = {}
        self._preset_work_generation_job_contexts: dict[
            str,
            _PresetWorkGenerationJobContext,
        ] = {}
        self._preset_manual_selection_contexts: dict[
            str,
            _PresetManualSelectionContext,
        ] = {}
        self._workspace_session_execution_options: dict[str, AgentExecutionOptions] = {}
        self._session_exit_hook_armed_signatures: dict[str, tuple[str, ...]] = {}
        self._session_exit_hook_evaluated_signatures: dict[str, tuple[str, ...]] = {}

        bootstrap_result = load_bootstrap_data(repository)
        self._settings = bootstrap_result.snapshot.settings
        self._saved_workspaces = self._sort_saved_workspaces(
            bootstrap_result.snapshot.saved_workspaces
        )
        self._startup_issues = bootstrap_result.issues
        self._runtime_action_thread.start()
        self._persistence_thread.start()
        self._start_file_drop_watcher(
            file_drop_dir,
            poll_interval_seconds=file_drop_poll_interval_seconds,
        )

    @property
    def event_queue(self) -> Queue[AppRuntimeEvent]:
        """Return the queue consumed by the Tkinter main thread."""
        return self._event_queue

    @property
    def startup_issues(self) -> tuple[UseCaseIssue, ...]:
        """Return non-fatal bootstrap issues that should be shown once."""
        return self._startup_issues

    @property
    def settings(self) -> AppSettings:
        """Return the current in-memory settings snapshot."""
        return self._settings

