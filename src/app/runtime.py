"""Application runtime facade for bootstrapping persistence and UI-facing state."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime
import logging
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
PRESET_WORK_PRIORITY_OPTIONS = ("high", "medium", "low")
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
        """Return created normal session tabs in import order."""
        return tuple(registration.session_tab for registration in self.registrations)

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


RuntimeActionEvent = (
    QueueStartCompletedEvent
    | WorkspaceOpenCompletedEvent
    | SettingsRetryCompletedEvent
    | PresetAnalysisJobSubmittedEvent
    | PresetAnalysisJobSubmissionFailedEvent
    | PresetCandidateJobsRegisteredEvent
    | PresetPromptLanguagesLoadedEvent
    | PresetPromptInstructionsLoadedEvent
    | RuntimeActionFailedEvent
    | RuntimeActionWarningEvent
)

AppRuntimeEvent = (
    ControllerEvent
    | PersistenceIssueEvent
    | RuntimeActionEvent
    | PresetCandidateJobsRegisteredEvent
)


class AppRuntime:
    """Keep persistent app data and controller event state together for the UI."""

    def __init__(
        self,
        *,
        controller: AppController,
        repository: PersistentDataRepository,
        prompt_store: PromptStoreProtocol | None = None,
        system_sleep_preventer: SystemSleepPreventerProtocol | None = None,
    ) -> None:
        self._controller = controller
        self._repository = repository
        self._prompt_store = prompt_store
        self._system_sleep_preventer = system_sleep_preventer
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
        self._workspace_session_execution_options: dict[str, AgentExecutionOptions] = {}

        bootstrap_result = load_bootstrap_data(repository)
        self._settings = bootstrap_result.snapshot.settings
        self._saved_workspaces = self._sort_saved_workspaces(
            bootstrap_result.snapshot.saved_workspaces
        )
        self._startup_issues = bootstrap_result.issues
        self._runtime_action_thread.start()
        self._persistence_thread.start()

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

    def list_saved_workspaces(self) -> tuple[SavedWorkspace, ...]:
        """Return saved workspaces in UI-friendly order."""
        return self._saved_workspaces

    def delete_saved_workspace(self, workspace_path: str) -> SavedWorkspace | None:
        """Remove one workspace from the persistent saved-workspace list."""
        normalized_path = canonicalize_workspace_path(workspace_path)
        if not normalized_path:
            return None

        deleted_workspace: SavedWorkspace | None = None
        remaining_workspaces: list[SavedWorkspace] = []
        for saved_workspace in self._saved_workspaces:
            if canonicalize_workspace_path(saved_workspace.path) == normalized_path:
                if deleted_workspace is None:
                    deleted_workspace = saved_workspace
                continue
            remaining_workspaces.append(saved_workspace)

        if deleted_workspace is None:
            return None

        updated_workspaces = tuple(remaining_workspaces)
        self._saved_workspaces = updated_workspaces
        self._enqueue_persistence_save(
            lambda persisted_workspaces=updated_workspaces: save_saved_workspaces(
                self._repository,
                persisted_workspaces,
            ),
            coalesce_key=_PERSISTENCE_COALESCE_SAVED_WORKSPACES,
        )
        return deleted_workspace

    def list_workspace_tabs(self, *, include_closed: bool = False) -> tuple[WorkspaceTab, ...]:
        """Return runtime workspace tabs."""
        with self._get_controller_state_lock():
            return self._controller.workspace_manager.list_workspace_tabs(
                include_closed=include_closed
            )

    def list_session_tabs(
        self,
        workspace_tab_id: str,
        *,
        include_closed: bool = False,
    ) -> tuple[SessionTab, ...]:
        """Return runtime session tabs for one workspace."""
        with self._get_controller_state_lock():
            return self._controller.session_manager.list_session_tabs(
                workspace_tab_id=workspace_tab_id,
                include_closed=include_closed,
            )

    def list_jobs(self, *, session_tab_id: SessionTabId | None = None) -> tuple[Job, ...]:
        """Return runtime jobs, optionally filtered to one session."""
        with self._get_controller_state_lock():
            if session_tab_id is None:
                return self._controller.scheduler.list_jobs()
            return self._controller.scheduler.list_session_jobs(session_tab_id)

    def list_workspace_jobs(self, workspace_tab_id: str) -> tuple[Job, ...]:
        """Return runtime jobs for one workspace in scheduler order."""
        with self._get_controller_state_lock():
            self._controller.workspace_manager.get_workspace_tab(workspace_tab_id)
            return self._controller.scheduler.list_workspace_jobs(workspace_tab_id)

    def list_jobs_by_workspace(
        self,
        workspace_tab_ids: Iterable[str],
    ) -> dict[str, tuple[Job, ...]]:
        """Return runtime jobs grouped by workspace in scheduler order."""
        workspace_tab_id_tuple = tuple(workspace_tab_ids)
        if not workspace_tab_id_tuple:
            return {}

        with self._get_controller_state_lock():
            for workspace_tab_id in workspace_tab_id_tuple:
                self._controller.workspace_manager.get_workspace_tab(workspace_tab_id)
            return self._controller.scheduler.list_jobs_by_workspace(workspace_tab_id_tuple)

    def summarize_workspace_jobs(
        self,
        workspace_tab_ids: Iterable[str],
    ) -> dict[str, WorkspaceJobSummary]:
        """Return lightweight job summaries for workspaces without scheduler sorting."""
        workspace_tab_id_tuple = tuple(workspace_tab_ids)
        if not workspace_tab_id_tuple:
            return {}

        with self._get_controller_state_lock():
            for workspace_tab_id in workspace_tab_id_tuple:
                self._controller.workspace_manager.get_workspace_tab(workspace_tab_id)
            return self._controller.scheduler.summarize_workspace_jobs(
                workspace_tab_id_tuple
            )

    def workspace_has_jobs(self, workspace_tab_id: str) -> bool:
        """Return whether one workspace has jobs without scheduler-order sorting."""
        with self._get_controller_state_lock():
            self._controller.workspace_manager.get_workspace_tab(workspace_tab_id)
            return self._controller.scheduler.workspace_has_jobs(workspace_tab_id)

    def workspace_has_runnable_jobs(self, workspace_tab_id: str) -> bool:
        """Return whether one workspace has queued jobs ready to dispatch."""
        with self._get_controller_state_lock():
            self._controller.workspace_manager.get_workspace_tab(workspace_tab_id)
            return self._controller.scheduler.workspace_has_runnable_jobs(
                workspace_tab_id
            )

    def workspace_path_has_running_job(self, workspace_path: str) -> bool:
        """Return whether an open workspace for the path has a running job."""
        normalized_path = canonicalize_workspace_path(workspace_path)
        if not normalized_path:
            return False

        with self._get_controller_state_lock():
            workspace_tab_ids = {
                workspace_tab.workspace_tab_id
                for workspace_tab in self._controller.workspace_manager.list_workspace_tabs(
                    include_closed=False
                )
                if canonicalize_workspace_path(workspace_tab.workspace_path) == normalized_path
            }
            if not workspace_tab_ids:
                return False

            jobs = self._controller.scheduler.list_jobs()

        return any(
            job.workspace_tab_id in workspace_tab_ids and job.status == JobStatus.RUNNING
            for job in jobs
        )

    def get_workspace_tab(self, workspace_tab_id: str) -> WorkspaceTab:
        """Return one workspace tab by id."""
        with self._get_controller_state_lock():
            return self._controller.workspace_manager.get_workspace_tab(workspace_tab_id)

    def get_session_tab(self, session_tab_id: str) -> SessionTab:
        """Return one session tab by id."""
        with self._get_controller_state_lock():
            return self._controller.session_manager.get_session_tab(session_tab_id)

    def get_job(self, job_id: str) -> Job:
        """Return one job by id."""
        with self._get_controller_state_lock():
            return self._controller.scheduler.get_job(job_id)

    def delete_job(self, job_id: str) -> Job:
        """Delete one non-running runtime job and clear its UI-side caches."""
        with self._get_controller_state_lock():
            deleted_job = self._controller.delete_job(job_id)
            self._sync_controller_events()
        self._job_progress_logs.pop(job_id, None)
        self._job_user_messages.pop(job_id, None)
        return deleted_job

    def get_running_job(self) -> Job | None:
        """Return the current running job when present."""
        with self._get_controller_state_lock():
            return self._controller.scheduler.get_running_job()

    def get_queue_state(self, workspace_tab_id: str | None = None) -> WorkspaceQueueState:
        """Return the queue state for one workspace."""
        with self._get_controller_state_lock():
            return self._controller.scheduler.get_queue_state(workspace_tab_id)

    def get_job_logs(self, job_id: str) -> tuple[str, ...]:
        """Return recent buffered progress log lines for one job."""
        return self.get_job_progress_logs(job_id)

    def get_job_progress_logs(self, job_id: str) -> tuple[str, ...]:
        """Return recent buffered JSONL-based progress log lines for one job."""
        return tuple(self._job_progress_logs.get(job_id, ()))

    def get_job_user_message(self, job_id: str) -> str:
        """Return the latest short UI message for one job."""
        return self._job_user_messages.get(job_id, "")

    def list_completed_sessions(self, workspace_tab_id: str) -> tuple[CompletedSessionSummary, ...]:
        """Return runtime completed sessions for one open workspace."""
        with self._get_controller_state_lock():
            workspace_tab = self._controller.workspace_manager.get_workspace_tab(workspace_tab_id)
            return self._controller.session_manager.list_completed_sessions(
                workspace_tab.workspace_path
            )

    def close_session(self, session_tab_id: str):
        """Close one session tab and sync resulting controller events."""
        with self._get_controller_state_lock():
            session_tab = self._controller.session_manager.get_session_tab(session_tab_id)
            workspace_tab_id = session_tab.workspace_tab_id
            advance_active_generation = self._workspace_is_active(workspace_tab_id)
            result = self._controller.close_session(session_tab_id)
            if self._session_close_invalidates_queue_control(result):
                self._advance_queue_control_generation(workspace_tab_id)
                if advance_active_generation:
                    self._advance_queue_control_generation(None)
            self._sync_controller_events()
        self._mark_system_sleep_prevention_dirty()
        self._sync_system_sleep_prevention()
        return result

    def close_workspace(self, workspace_tab_id: str):
        """Close one workspace tab and sync resulting controller events."""
        with self._get_controller_state_lock():
            advance_active_generation = self._workspace_is_active(workspace_tab_id)
        self._advance_queue_control_generation(workspace_tab_id)
        if advance_active_generation:
            self._advance_queue_control_generation(None)
        with self._get_controller_state_lock():
            result = self._controller.close_workspace(workspace_tab_id)
            self._sync_controller_events()
        self._mark_system_sleep_prevention_dirty()
        self._sync_system_sleep_prevention()
        return result

    def list_session_turns(self, session_tab_id: str) -> tuple[SessionTurnHistory, ...]:
        """Return runtime turn history connected to one session tab."""
        with self._get_controller_state_lock():
            return self._controller.session_manager.list_session_tab_turns(session_tab_id)

    def open_workspace(self, workspace_path: str) -> WorkspaceOpenActionResult:
        """Open a workspace and remember it in persistent saved-workspace data."""
        validate_workspace_path(workspace_path)
        with self._get_controller_state_lock():
            open_result = self._controller.workspace_manager.open_validated_workspace(
                workspace_path
            )
        self._remember_saved_workspace(workspace_path)
        return WorkspaceOpenActionResult(open_result=open_result)

    def open_workspace_in_background(self, workspace_path: str) -> None:
        """Open a workspace without running filesystem checks on the Tkinter thread."""
        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=lambda target_path=workspace_path: self._open_workspace_for_worker(
                    target_path
                ),
                failure_title="워크스페이스 오류",
                failure_message="워크스페이스를 열 수 없습니다.",
                log_message="Failed to open workspace in background.",
            )
        )

    def open_session(self, workspace_tab_id: str) -> SessionTab:
        """Open a new session tab."""
        with self._get_controller_state_lock():
            return self._controller.open_session(
                workspace_tab_id,
                execution_options=(
                    self._default_session_execution_options_for_workspace_locked(
                        workspace_tab_id
                    )
                ),
            )

    def open_preset_session(self, workspace_tab_id: str) -> SessionTab:
        """Open a new preset parent session tab."""
        with self._get_controller_state_lock():
            return self._controller.open_preset_session(
                workspace_tab_id,
                execution_options=(
                    self._default_session_execution_options_for_workspace_locked(
                        workspace_tab_id
                    )
                ),
            )

    def _default_session_execution_options_for_workspace_locked(
        self,
        workspace_tab_id: str,
    ) -> AgentExecutionOptions:
        key = self._workspace_execution_options_key_locked(workspace_tab_id)
        remembered_options = self._get_workspace_session_execution_options().get(key)
        if remembered_options is not None:
            return remembered_options
        return execution_options_from_settings(self._settings)

    def _remember_session_execution_options_for_workspace_locked(
        self,
        workspace_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> None:
        key = self._workspace_execution_options_key_locked(workspace_tab_id)
        self._get_workspace_session_execution_options()[key] = execution_options

    def _workspace_execution_options_key_locked(self, workspace_tab_id: str) -> str:
        workspace_manager = getattr(self._controller, "workspace_manager", None)
        get_workspace_tab = getattr(workspace_manager, "get_workspace_tab", None)
        if callable(get_workspace_tab):
            try:
                workspace_tab = get_workspace_tab(workspace_tab_id)
            except Exception:
                LOGGER.debug(
                    "Failed to resolve workspace path for execution-option preference. "
                    "workspace_tab_id=%s",
                    workspace_tab_id,
                    exc_info=True,
                )
            else:
                workspace_path = getattr(workspace_tab, "workspace_path", "")
                return (
                    canonicalize_workspace_path(workspace_path)
                    or workspace_path
                    or workspace_tab_id
                )
        return workspace_tab_id

    def _get_workspace_session_execution_options(
        self,
    ) -> dict[str, AgentExecutionOptions]:
        options = getattr(self, "_workspace_session_execution_options", None)
        if options is None:
            options = {}
            self._workspace_session_execution_options = options
        return options

    def import_prompt_sessions(
        self,
        workspace_tab_id: str,
        prompts: Sequence[str],
        *,
        auto_commit_enabled: bool,
        execution_options: AgentExecutionOptions | None = None,
    ) -> ImportedPromptSessionsResult:
        """Create one normal session and queued prompt job for each imported prompt."""
        normalized_prompts = tuple(prompt.strip() for prompt in prompts if prompt.strip())
        if not normalized_prompts:
            raise ValueError("가져올 지시문이 없습니다.")

        registrations: list[ImportedPromptSessionRegistration] = []
        with self._get_controller_state_lock():
            self._controller.workspace_manager.get_workspace_tab(workspace_tab_id)
            resolved_execution_options = (
                execution_options
                if execution_options is not None
                else self._default_session_execution_options_for_workspace_locked(
                    workspace_tab_id
                )
            )
            if execution_options is not None:
                self._remember_session_execution_options_for_workspace_locked(
                    workspace_tab_id,
                    resolved_execution_options,
                )
            session_tabs: list[SessionTab] = []
            job_requests: list[tuple[SessionTabId, str]] = []
            for prompt in normalized_prompts:
                session_tab = self._controller.open_session(
                    workspace_tab_id,
                    execution_options=resolved_execution_options,
                )
                session_tabs.append(session_tab)
                job_requests.append((session_tab.session_tab_id, prompt))
                if auto_commit_enabled:
                    job_requests.append(
                        (session_tab.session_tab_id, AUTO_COMMIT_PROMPT)
                    )

            registered_jobs = self._controller.submit_jobs(
                job_requests,
                dispatch_immediately=False,
                execution_options=resolved_execution_options,
            )
            session_tabs = [
                self._controller.session_manager.lock_session_execution_options(
                    session_tab.session_tab_id,
                    resolved_execution_options,
                )
                for session_tab in session_tabs
            ]
            job_iterator = iter(registered_jobs)
            for session_tab in session_tabs:
                prompt_job = next(job_iterator)
                auto_commit_job = next(job_iterator) if auto_commit_enabled else None
                registrations.append(
                    ImportedPromptSessionRegistration(
                        session_tab=session_tab,
                        prompt_job=prompt_job,
                        auto_commit_job=auto_commit_job,
                    )
                )
            self._sync_controller_events()

        self._enqueue_dispatch_next_job_if_needed()
        return ImportedPromptSessionsResult(registrations=tuple(registrations))

    def list_prompt_languages(self) -> tuple[str, ...]:
        """Return prompt store languages for preset session inputs."""
        if self._prompt_store is None:
            return ()
        return tuple(self._prompt_store.list_languages())

    def list_prompt_instructions(self, language: str) -> tuple[InstructionInfo, ...]:
        """Return prompt store instructions for one preset language."""
        if self._prompt_store is None:
            return ()
        return tuple(self._prompt_store.list_instructions(language))

    def load_preset_languages_in_background(
        self,
        *,
        request_id: int,
        session_tab_id: str,
        workspace_tab_id: str,
    ) -> None:
        """Load preset prompt languages without blocking the Tkinter thread."""
        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=lambda: self._load_preset_languages_for_worker(
                    request_id=request_id,
                    session_tab_id=session_tab_id,
                    workspace_tab_id=workspace_tab_id,
                ),
                failure_title="프리셋 작업 오류",
                failure_message="프리셋 언어 목록을 읽지 못했습니다.",
                log_message="Failed to load preset prompt languages in background.",
            )
        )

    def load_preset_instructions_in_background(
        self,
        *,
        request_id: int,
        session_tab_id: str,
        workspace_tab_id: str,
        language: str,
    ) -> None:
        """Load preset prompt instructions without blocking the Tkinter thread."""
        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=lambda: self._load_preset_instructions_for_worker(
                    request_id=request_id,
                    session_tab_id=session_tab_id,
                    workspace_tab_id=workspace_tab_id,
                    language=language,
                ),
                failure_title="프리셋 작업 오류",
                failure_message="프리셋 지시문 목록을 읽지 못했습니다.",
                log_message="Failed to load preset prompt instructions in background.",
            )
        )

    def submit_preset_analysis_job_in_background(
        self,
        session_tab_id: str,
        *,
        language: str,
        instruction: str,
        work_priority: str,
        analysis_prompt_prefix: str = "",
        auto_commit_enabled: bool = False,
        execution_options: AgentExecutionOptions | None = None,
        candidate_execution_options: AgentExecutionOptions | None = None,
    ) -> None:
        """Submit a preset analysis job without reading prompt files on the UI thread."""
        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=lambda: self._submit_preset_analysis_job_for_worker(
                    session_tab_id,
                    language=language,
                    instruction=instruction,
                    work_priority=work_priority,
                    analysis_prompt_prefix=analysis_prompt_prefix,
                    auto_commit_enabled=auto_commit_enabled,
                    execution_options=execution_options,
                    candidate_execution_options=candidate_execution_options,
                ),
                failure_title="프리셋 작업 오류",
                failure_message="프리셋 분석 작업을 등록할 수 없습니다.",
                log_message="Failed to submit preset analysis job in background.",
            )
        )

    def submit_preset_analysis_job(
        self,
        session_tab_id: str,
        *,
        language: str,
        instruction: str,
        work_priority: str,
        analysis_prompt_prefix: str = "",
        auto_commit_enabled: bool = False,
        execution_options: AgentExecutionOptions | None = None,
        candidate_execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        """Register a preset analysis job from selected prompt store inputs."""
        normalized_priority = _normalize_preset_work_priority(work_priority)
        if self._prompt_store is None:
            raise ValueError("프리셋 저장소가 설정되지 않았습니다.")

        with self._get_controller_state_lock():
            self._ensure_preset_session_accepts_registration_locked(session_tab_id)
            session_tab = self._controller.session_manager.get_session_tab(
                session_tab_id
            )
            resolved_execution_options = (
                execution_options
                or self._default_session_execution_options_for_workspace_locked(
                    session_tab.workspace_tab_id
                )
            )
            resolved_candidate_execution_options = (
                candidate_execution_options or resolved_execution_options
            )

        try:
            analysis_prompt = self._prompt_store.read_analysis_prompt(language, instruction)
            work_prompt_template = self._prompt_store.read_work_prompt_template(
                language,
                instruction,
            )
        except Exception as exc:
            LOGGER.exception(
                "Failed to read preset prompt pair. language=%s instruction=%s",
                language,
                instruction,
            )
            raise ValueError(
                "프리셋 파일을 읽지 못했습니다. 언어와 지시문을 확인하세요."
            ) from exc

        preset_prompt = _build_preset_analysis_prompt(
            analysis_prompt,
            work_priority=normalized_priority,
            analysis_prompt_prefix=analysis_prompt_prefix,
        )
        with self._get_controller_state_lock():
            self._ensure_preset_session_accepts_registration_locked(session_tab_id)
            job = self._controller.submit_job(
                session_tab_id,
                preset_prompt,
                dispatch_immediately=False,
                execution_options=resolved_execution_options,
            )
            session_tab = self._controller.session_manager.lock_session_execution_options(
                session_tab_id,
                resolved_execution_options,
            )
            self._remember_session_execution_options_for_workspace_locked(
                session_tab.workspace_tab_id,
                resolved_execution_options,
            )
            queue_control_generation = self._get_queue_control_generation(
                session_tab.workspace_tab_id
            )
            self._sync_controller_events()
            self._get_preset_analysis_job_contexts()[
                job.job_id
            ] = _PresetAnalysisJobContext(
                language=language,
                instruction=instruction,
                work_prompt_template=work_prompt_template,
                work_priority=normalized_priority,
                auto_commit_enabled=auto_commit_enabled,
                execution_options=resolved_execution_options,
                candidate_execution_options=resolved_candidate_execution_options,
                queue_control_generation=queue_control_generation,
            )
            LOGGER.info(
                "Preset turn1 registered. job_id=%s workspace_tab_id=%s "
                "session_tab_id=%s language=%s instruction=%s work_priority=%s "
                "auto_commit_enabled=%s",
                job.job_id,
                session_tab.workspace_tab_id,
                session_tab_id,
                language,
                instruction,
                normalized_priority,
                auto_commit_enabled,
            )
        self._enqueue_dispatch_next_job_if_needed()
        return job

    def _ensure_preset_session_accepts_registration_locked(
        self,
        session_tab_id: str,
    ) -> None:
        session_tab = self._controller.session_manager.get_session_tab(session_tab_id)
        if session_tab.open_state != TabOpenState.OPEN:
            raise ValueError("닫힌 프리셋 세션에는 등록할 수 없습니다.")
        if session_tab.kind != SessionTabKind.PRESET:
            raise ValueError("프리셋 분석 작업은 프리셋 세션에서만 등록할 수 있습니다.")
        if any(
            job.session_tab_id == session_tab_id
            for job in self._controller.scheduler.list_jobs()
        ):
            raise ValueError("프리셋 세션은 이미 등록되었습니다.")

    def activate_workspace(self, workspace_tab_id: str) -> WorkspaceTab:
        """Mark one workspace tab as active."""
        with self._get_controller_state_lock():
            return self._controller.workspace_manager.activate_workspace(workspace_tab_id)

    def activate_session(self, session_tab_id: str) -> SessionTab:
        """Mark one session tab as active within its workspace."""
        with self._get_controller_state_lock():
            return self._controller.session_manager.activate_session(session_tab_id)

    def set_session_execution_options(
        self,
        session_tab_id: str,
        execution_options: AgentExecutionOptions,
    ) -> SessionTab:
        """Store the current unlocked execution option selection for one session."""
        with self._get_controller_state_lock():
            session_tab = self._controller.session_manager.get_session_tab(session_tab_id)
            if session_tab.execution_options_locked:
                return session_tab
            updated_session_tab = (
                self._controller.session_manager.set_session_execution_options(
                    session_tab_id,
                    execution_options,
                )
            )
            self._remember_session_execution_options_for_workspace_locked(
                updated_session_tab.workspace_tab_id,
                execution_options,
            )
            return updated_session_tab

    def submit_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        execution_options: AgentExecutionOptions | None = None,
    ) -> Job:
        """Register one new job and sync resulting controller events."""
        resolved_execution_options = (
            execution_options or execution_options_from_settings(self._settings)
        )
        with self._get_controller_state_lock():
            job = self._controller.submit_job(
                session_tab_id,
                prompt,
                dispatch_immediately=False,
                execution_options=resolved_execution_options,
            )
            session_tab = self._controller.session_manager.lock_session_execution_options(
                session_tab_id,
                resolved_execution_options,
            )
            self._remember_session_execution_options_for_workspace_locked(
                session_tab.workspace_tab_id,
                resolved_execution_options,
            )
            self._sync_controller_events()
        self._enqueue_dispatch_next_job_if_needed()
        return job

    def start_queue(self, workspace_tab_id: str | None = None):
        """Start one workspace queue and sync resulting controller events."""
        with self._get_controller_state_lock():
            target_workspace_tab_id = self._resolve_queue_control_workspace_tab_id(
                workspace_tab_id
            )
            with self._defer_controller_dispatch():
                queue_state = self._controller.start_queue(target_workspace_tab_id)
            self._sync_controller_events()
        self._enqueue_dispatch_next_job_if_needed()
        self._mark_system_sleep_prevention_dirty()
        self._sync_system_sleep_prevention()
        return queue_state

    def start_queue_in_background(self, workspace_tab_id: str | None = None) -> None:
        """Start one workspace queue without running validation on the Tkinter thread."""
        with self._get_controller_state_lock():
            target_workspace_tab_id = self._resolve_queue_control_workspace_tab_id(
                workspace_tab_id
            )
        queue_control_generation = self._get_queue_control_generation(
            target_workspace_tab_id
        )
        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=lambda target_id=target_workspace_tab_id: self._start_queue_for_worker(
                    target_id,
                    queue_control_generation,
                ),
                failure_title="큐 오류",
                failure_message="큐를 시작할 수 없습니다.",
                log_message="Failed to start queue in background.",
                workspace_tab_id=target_workspace_tab_id,
                queue_control_generation=queue_control_generation,
            )
        )

    def stop_queue(self, workspace_tab_id: str | None = None):
        """Stop one workspace queue and sync resulting controller events."""
        target_workspace_tab_id = self._resolve_queue_control_workspace_tab_id(
            workspace_tab_id
        )
        self._advance_queue_control_generation(target_workspace_tab_id)
        with self._get_controller_state_lock():
            queue_state = self._controller.stop_queue(target_workspace_tab_id)
            self._sync_controller_events()
        self._mark_system_sleep_prevention_dirty()
        self._sync_system_sleep_prevention()
        return queue_state

    def stop_all_queues(self) -> None:
        """Stop every workspace queue and sync resulting controller events."""
        self._advance_all_queue_control_generations()
        with self._get_controller_state_lock():
            self._controller.stop_all_queues()
            self._sync_controller_events()
        self._mark_system_sleep_prevention_dirty()
        self._sync_system_sleep_prevention()

    def update_settings(self, settings: AppSettings) -> SettingsUpdateResult:
        """Update current settings, persist them, and retry waiting jobs in the background."""
        self._settings = settings
        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=self._retry_waiting_jobs_for_worker,
                failure_title="설정 오류",
                failure_message="설정 필요 작업을 다시 큐에 넣지 못했습니다.",
                log_message="Failed to retry waiting jobs after settings update.",
            )
        )
        self._enqueue_persistence_save(
            lambda persisted_settings=settings: save_app_settings(
                self._repository,
                persisted_settings,
            ),
            coalesce_key=_PERSISTENCE_COALESCE_SETTINGS,
        )
        return SettingsUpdateResult()

    def retry_waiting_jobs(self, *, sync_events: bool = True) -> tuple[str, ...]:
        """Retry every configuration-waiting job using the latest settings."""
        retried_job_ids: list[str] = []
        with self._get_controller_state_lock():
            waiting_job_ids = []
            for job in self._controller.scheduler.list_jobs():
                if job.status != JobStatus.WAITING_FOR_CONFIGURATION:
                    continue
                try:
                    session_tab = self._controller.session_manager.get_session_tab(
                        job.session_tab_id
                    )
                    workspace_tab = self._controller.workspace_manager.get_workspace_tab(
                        job.workspace_tab_id
                    )
                except KeyError:
                    continue
                if (
                    session_tab.open_state == TabOpenState.OPEN
                    and workspace_tab.open_state == TabOpenState.OPEN
                ):
                    waiting_job_ids.append(job.job_id)

            with self._defer_controller_dispatch():
                for job_id in waiting_job_ids:
                    self._controller.retry_waiting_job(job_id)
                    retried_job_ids.append(job_id)

            if sync_events:
                self._sync_controller_events()

        self._enqueue_dispatch_next_job_if_needed()
        return tuple(retried_job_ids)

    def process_background_events(self, *, max_items: int | None = None) -> int:
        """Move background worker results into the runtime event queue."""
        with self._get_controller_state_lock():
            processed = self._controller.process_background_events(
                max_items=max_items,
                dispatch_immediately=False,
            )
            if processed:
                self._mark_system_sleep_prevention_dirty()
            self._sync_controller_events()
        self._enqueue_dispatch_next_job_if_needed()
        remaining = None if max_items is None else max(0, max_items - processed)
        processed += self._process_runtime_action_completions(max_items=remaining)
        remaining = None if max_items is None else max(0, max_items - processed)
        processed += self._process_persistence_completions(max_items=remaining)
        self._sync_system_sleep_prevention()
        return processed

    def drain_events(self, *, max_items: int | None = None) -> tuple[AppRuntimeEvent, ...]:
        """Drain up to ``max_items`` events already mirrored into the runtime queue."""
        events: list[AppRuntimeEvent] = []
        while max_items is None or len(events) < max_items:
            try:
                events.append(self._event_queue.get_nowait())
            except Empty:
                return tuple(events)
        return tuple(events)

    def has_pending_background_work(self) -> bool:
        """Return whether runtime shutdown still has execution cleanup pending."""
        if self._controller.has_pending_background_work():
            return True

        if not self._runtime_action_completion_queue.empty():
            return True

        if self._runtime_action_thread.is_alive() and (
            self._runtime_action_shutdown_requested
            or not self._runtime_action_request_queue.empty()
            or self._is_runtime_action_in_progress()
        ):
            return True

        if not self._persistence_shutdown_requested:
            return False

        self._enqueue_persistence_shutdown_if_ready()
        return (
            self._persistence_thread.is_alive()
            or not self._persistence_request_queue.empty()
            or not self._persistence_completion_queue.empty()
        )

    def shutdown(self) -> None:
        """Stop queue execution before the UI closes."""
        if not self._runtime_action_shutdown_requested:
            self._runtime_action_shutdown_requested = True
            self._advance_all_queue_control_generations()
            self._runtime_action_request_queue.put(None)
        try:
            self.stop_all_queues()
        except Exception:
            LOGGER.exception("Failed to stop queue during shutdown.")
        finally:
            self._release_system_sleep_prevention()
        if not self._persistence_shutdown_requested:
            self._persistence_shutdown_requested = True
        self._enqueue_persistence_shutdown_if_ready()

    def _enqueue_persistence_shutdown_if_ready(self) -> None:
        if (
            self._persistence_shutdown_sentinel_enqueued
            or not self._persistence_shutdown_requested
            or self._runtime_action_thread.is_alive()
            or self._is_runtime_action_in_progress()
            or not self._runtime_action_completion_queue.empty()
        ):
            return
        self._persistence_shutdown_sentinel_enqueued = True
        self._persistence_request_queue.put(None)

    def _remember_saved_workspace(self, workspace_path: str) -> None:
        self._remember_saved_workspaces((workspace_path,))

    def _remember_saved_workspaces(self, workspace_paths: Sequence[str]) -> None:
        if not workspace_paths:
            return

        updated_workspaces: Iterable[SavedWorkspace] = self._saved_workspaces
        for workspace_path in workspace_paths:
            updated_workspaces = self._updated_saved_workspaces(
                updated_workspaces,
                workspace_path,
                timestamp=utc_now(),
            )

        sorted_workspaces = self._sort_saved_workspaces(updated_workspaces)
        self._saved_workspaces = sorted_workspaces
        self._enqueue_persistence_save(
            lambda persisted_workspaces=sorted_workspaces: save_saved_workspaces(
                self._repository,
                persisted_workspaces,
            ),
            coalesce_key=_PERSISTENCE_COALESCE_SAVED_WORKSPACES,
        )

    def _updated_saved_workspaces(
        self,
        workspaces: Iterable[SavedWorkspace],
        workspace_path: str,
        *,
        timestamp: datetime,
    ) -> tuple[SavedWorkspace, ...]:
        normalized_path = canonicalize_workspace_path(workspace_path)
        updated_workspaces: list[SavedWorkspace] = []
        matched = False

        for saved_workspace in workspaces:
            if canonicalize_workspace_path(saved_workspace.path) != normalized_path:
                updated_workspaces.append(saved_workspace)
                continue

            updated_workspaces.append(
                replace(
                    saved_workspace,
                    path=workspace_path,
                    last_selected_at=timestamp,
                )
            )
            matched = True

        if not matched:
            updated_workspaces.append(
                SavedWorkspace(
                    path=workspace_path,
                    display_name=workspace_folder_display_name(workspace_path),
                    added_at=timestamp,
                    last_selected_at=timestamp,
                )
            )

        return tuple(updated_workspaces)

    def _sync_controller_events(self) -> None:
        with self._get_controller_state_lock():
            drain_ui_events = getattr(self._controller, "drain_ui_events", None)
            if drain_ui_events is None:
                return
            for event in drain_ui_events():
                self._apply_event_state(event)
                self._event_queue.put(event)

    def _get_controller_state_lock(self) -> threading.RLock:
        lock = getattr(self, "_controller_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._controller_state_lock = lock
        return lock

    def _defer_controller_dispatch(self):
        scheduler = getattr(self._controller, "scheduler", None)
        defer_dispatch = getattr(scheduler, "defer_dispatch", None)
        if defer_dispatch is None:
            return nullcontext()
        return defer_dispatch()

    def _enqueue_runtime_action(self, request: _RuntimeActionRequest) -> None:
        if getattr(self, "_runtime_action_shutdown_requested", False):
            self._discard_runtime_action_request(request)
            return
        request_queue = getattr(self, "_runtime_action_request_queue", None)
        if request_queue is None:
            if self._runtime_action_request_is_stale(request):
                self._discard_runtime_action_request(request)
                return
            try:
                event = request.action()
            except Exception:
                LOGGER.exception(request.log_message)
                event = RuntimeActionFailedEvent(
                    title=request.failure_title,
                    message=request.failure_message,
                    workspace_tab_id=request.workspace_tab_id,
                )
            if event is not None:
                self._event_queue.put(event)
            return
        request_queue.put(request)

    @staticmethod
    def _discard_runtime_action_request(request: _RuntimeActionRequest) -> None:
        if request.on_discard is None:
            return
        try:
            request.on_discard()
        except Exception:
            LOGGER.exception("Runtime action discard callback failed.")

    def _runtime_action_request_is_stale(self, request: _RuntimeActionRequest) -> bool:
        if request.queue_control_generation is None:
            return False
        return not self._queue_start_is_current(
            request.workspace_tab_id,
            request.queue_control_generation,
        )

    def _get_queue_control_generation(self, workspace_tab_id: str | None) -> tuple[int, int]:
        with self._queue_control_lock:
            return (
                self._queue_control_global_generation,
                self._queue_control_workspace_generations.get(
                    self._queue_control_workspace_key(workspace_tab_id),
                    0,
                ),
            )

    def _advance_queue_control_generation(self, workspace_tab_id: str | None) -> None:
        with self._queue_control_lock:
            key = self._queue_control_workspace_key(workspace_tab_id)
            self._queue_control_workspace_generations[key] = (
                self._queue_control_workspace_generations.get(key, 0) + 1
            )

    def _advance_all_queue_control_generations(self) -> None:
        with self._queue_control_lock:
            self._queue_control_global_generation += 1

    def _queue_start_is_current(
        self,
        workspace_tab_id: str | None,
        queue_control_generation: tuple[int, int],
    ) -> bool:
        with self._queue_control_lock:
            current_generation = (
                self._queue_control_global_generation,
                self._queue_control_workspace_generations.get(
                    self._queue_control_workspace_key(workspace_tab_id),
                    0,
                ),
            )
            return (
                not self._runtime_action_shutdown_requested
                and current_generation == queue_control_generation
            )

    def _snapshot_dispatch_queue_control_generations(self) -> dict[str, tuple[int, int]]:
        scheduler = getattr(self._controller, "scheduler", None)
        list_queue_states = getattr(scheduler, "list_queue_states", None)
        if list_queue_states is None:
            return {}

        generations: dict[str, tuple[int, int]] = {}
        for queue_state in list_queue_states():
            if getattr(queue_state, "status", None) != QueueStatus.STARTED:
                continue
            workspace_tab_id = getattr(queue_state, "workspace_tab_id", None)
            if workspace_tab_id is None:
                continue
            generations[workspace_tab_id] = self._get_queue_control_generation(
                workspace_tab_id
            )
        return generations

    def _get_job_start_queue_control_generation(
        self,
        workspace_tab_id: str,
    ) -> tuple[int, int]:
        dispatch_generations = getattr(
            self,
            "_dispatch_job_start_queue_control_generations",
            None,
        )
        if dispatch_generations is not None:
            queue_control_generation = dispatch_generations.get(workspace_tab_id)
            if queue_control_generation is not None:
                return queue_control_generation
        return self._get_queue_control_generation(workspace_tab_id)

    def _workspace_is_active(self, workspace_tab_id: str) -> bool:
        workspace_manager = getattr(self._controller, "workspace_manager", None)
        get_active_workspace_tab = getattr(
            workspace_manager,
            "get_active_workspace_tab",
            None,
        )
        if get_active_workspace_tab is None:
            return False

        active_workspace = get_active_workspace_tab()
        return (
            active_workspace is not None
            and active_workspace.workspace_tab_id == workspace_tab_id
        )

    def _resolve_queue_control_workspace_tab_id(
        self,
        workspace_tab_id: str | None,
    ) -> str | None:
        if workspace_tab_id is not None:
            return workspace_tab_id

        workspace_manager = getattr(self._controller, "workspace_manager", None)
        get_active_workspace_tab = getattr(
            workspace_manager,
            "get_active_workspace_tab",
            None,
        )
        if get_active_workspace_tab is None:
            return None

        active_workspace = get_active_workspace_tab()
        if active_workspace is None:
            return None
        return active_workspace.workspace_tab_id

    @staticmethod
    def _queue_control_workspace_key(workspace_tab_id: str | None) -> str:
        return workspace_tab_id or "__active_workspace__"

    @staticmethod
    def _session_close_invalidates_queue_control(result: object) -> bool:
        queue_stopped = getattr(result, "queue_stopped", None)
        if queue_stopped is None:
            return True
        return bool(queue_stopped or getattr(result, "canceled_job", None) is not None)

    def _mark_preset_followup_pending(self, workspace_tab_id: str) -> None:
        with self._preset_followup_lock:
            pending_counts = self._get_preset_followup_pending_workspace_counts()
            pending_counts[workspace_tab_id] = pending_counts.get(workspace_tab_id, 0) + 1

    def _clear_preset_followup_pending(self, workspace_tab_id: str) -> None:
        with self._preset_followup_lock:
            pending_counts = self._get_preset_followup_pending_workspace_counts()
            pending_count = pending_counts.get(workspace_tab_id, 0)
            if pending_count <= 1:
                pending_counts.pop(workspace_tab_id, None)
                return
            pending_counts[workspace_tab_id] = pending_count - 1

    def _has_pending_preset_followup(self) -> bool:
        with self._preset_followup_lock:
            pending_counts = self._get_preset_followup_pending_workspace_counts()
            return any(count > 0 for count in pending_counts.values())

    def _pending_preset_followup_workspace_tab_ids(self) -> set[str]:
        with self._preset_followup_lock:
            pending_counts = self._get_preset_followup_pending_workspace_counts()
            return {
                workspace_tab_id
                for workspace_tab_id, count in pending_counts.items()
                if count > 0
            }

    def _get_preset_followup_pending_workspace_counts(self) -> dict[str, int]:
        pending_counts = getattr(
            self,
            "_preset_followup_pending_workspace_counts",
            None,
        )
        if pending_counts is None:
            pending_counts = {}
            self._preset_followup_pending_workspace_counts = pending_counts
        return pending_counts

    def _set_runtime_action_in_progress(self, in_progress: bool) -> None:
        with self._runtime_action_activity_lock:
            self._runtime_action_in_progress = in_progress

    def _is_runtime_action_in_progress(self) -> bool:
        with self._runtime_action_activity_lock:
            return self._runtime_action_in_progress

    def _enqueue_dispatch_next_job_if_needed(self) -> None:
        dispatch_action_lock = getattr(self, "_dispatch_action_lock", None)
        if dispatch_action_lock is None:
            return

        has_pending_dispatch = getattr(self._controller, "has_pending_dispatch", None)
        if has_pending_dispatch is None or not has_pending_dispatch():
            return

        with dispatch_action_lock:
            if self._dispatch_action_requested:
                return
            self._dispatch_action_requested = True

        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=self._dispatch_next_job_for_worker,
                failure_title="작업 오류",
                failure_message="다음 작업을 시작할 수 없습니다.",
                log_message="Failed to dispatch next job in background.",
            )
        )

    def _run_runtime_action_worker(self) -> None:
        while True:
            request = self._runtime_action_request_queue.get()
            if request is None:
                return
            self._set_runtime_action_in_progress(True)
            try:
                if self._runtime_action_shutdown_requested:
                    self._discard_runtime_action_request(request)
                    continue
                if self._runtime_action_request_is_stale(request):
                    self._discard_runtime_action_request(request)
                    continue

                try:
                    event = request.action()
                except Exception:
                    LOGGER.exception(request.log_message)
                    event = RuntimeActionFailedEvent(
                        title=request.failure_title,
                        message=request.failure_message,
                        workspace_tab_id=request.workspace_tab_id,
                    )
            finally:
                self._set_runtime_action_in_progress(False)
            if event is not None:
                self._runtime_action_completion_queue.put(
                    _RuntimeActionCompletion(
                        event=event,
                        queue_control_workspace_tab_id=request.workspace_tab_id,
                        queue_control_generation=request.queue_control_generation,
                        drop_when_stale=request.drop_completion_when_stale,
                    )
                )

    def _start_queue_for_worker(
        self,
        workspace_tab_id: str | None,
        queue_control_generation: tuple[int, int],
    ) -> QueueStartCompletedEvent | None:
        if not self._queue_start_is_current(workspace_tab_id, queue_control_generation):
            return None
        with self._get_controller_state_lock():
            with self._defer_controller_dispatch():
                queue_state = self._controller.start_queue(workspace_tab_id)
            self._sync_controller_events()
        self._mark_system_sleep_prevention_dirty()
        if not self._queue_start_is_current(workspace_tab_id, queue_control_generation):
            with self._get_controller_state_lock():
                self._controller.stop_queue(queue_state.workspace_tab_id)
                self._sync_controller_events()
            self._mark_system_sleep_prevention_dirty()
            return None
        with self._get_controller_state_lock():
            workspace_tab = self._controller.workspace_manager.get_workspace_tab(
                queue_state.workspace_tab_id
            )
        self._enqueue_dispatch_next_job_if_needed()
        return QueueStartCompletedEvent(
            workspace_tab_id=workspace_tab.workspace_tab_id,
            display_name=workspace_tab.display_name,
        )

    def _open_workspace_for_worker(
        self,
        workspace_path: str,
    ) -> WorkspaceOpenCompletedEvent | RuntimeActionFailedEvent | None:
        if self._runtime_action_shutdown_requested:
            return None

        try:
            validate_workspace_path(workspace_path)
        except ValueError as error:
            LOGGER.warning(
                "Invalid workspace path. workspace_path=%s error=%s",
                workspace_path,
                error,
            )
            return RuntimeActionFailedEvent(
                title="워크스페이스 오류",
                message=str(error),
            )

        with self._get_controller_state_lock():
            if self._runtime_action_shutdown_requested:
                return None
            open_result = self._controller.workspace_manager.open_validated_workspace(
                workspace_path
            )

        return WorkspaceOpenCompletedEvent(
            workspace_path=workspace_path,
            workspace_tab_id=open_result.workspace_tab.workspace_tab_id,
            created=open_result.created,
        )

    def _dispatch_next_job_for_worker(self) -> None:
        try:
            if self._runtime_action_shutdown_requested:
                return
            pending_preset_followup_workspace_ids = (
                self._dispatch_excluded_preset_followup_workspace_tab_ids()
            )
            if pending_preset_followup_workspace_ids is None:
                return
            with self._get_controller_state_lock():
                if self._runtime_action_shutdown_requested:
                    return
                pending_preset_followup_workspace_ids = (
                    self._dispatch_excluded_preset_followup_workspace_tab_ids()
                )
                if pending_preset_followup_workspace_ids is None:
                    return
                self._dispatch_job_start_queue_control_generations = (
                    self._snapshot_dispatch_queue_control_generations()
                )
                try:
                    self._controller.dispatch_next_job(
                        excluded_workspace_tab_ids=pending_preset_followup_workspace_ids,
                    )
                    self._sync_controller_events()
                finally:
                    self._dispatch_job_start_queue_control_generations = None
        finally:
            with self._dispatch_action_lock:
                self._dispatch_action_requested = False
            if not self._runtime_action_shutdown_requested:
                self._enqueue_dispatch_next_job_if_needed()

    def _dispatch_excluded_preset_followup_workspace_tab_ids(
        self,
    ) -> set[str] | None:
        pending_preset_followup_workspace_ids = (
            self._pending_preset_followup_workspace_tab_ids()
        )
        if not pending_preset_followup_workspace_ids:
            return set()

        pending_dispatch_workspace_ids = self._pending_dispatch_workspace_tab_ids()
        if not pending_dispatch_workspace_ids:
            LOGGER.debug(
                "Dispatch delayed until pending preset follow-up action finishes."
            )
            return None
        if set(pending_dispatch_workspace_ids).issubset(
            pending_preset_followup_workspace_ids
        ):
            LOGGER.debug(
                "Dispatch delayed until pending preset follow-up action finishes. "
                "workspace_tab_ids=%s",
                sorted(pending_preset_followup_workspace_ids),
            )
            return None

        LOGGER.debug(
            "Dispatch excludes workspaces with pending preset follow-up actions. "
            "workspace_tab_ids=%s",
            sorted(pending_preset_followup_workspace_ids),
        )
        return pending_preset_followup_workspace_ids

    def _pending_dispatch_workspace_tab_ids(self) -> tuple[str, ...]:
        pending_dispatch_workspace_tab_ids = getattr(
            self._controller,
            "pending_dispatch_workspace_tab_ids",
            None,
        )
        if pending_dispatch_workspace_tab_ids is None:
            return ()
        return tuple(pending_dispatch_workspace_tab_ids())

    def _retry_waiting_jobs_for_worker(self) -> SettingsRetryCompletedEvent | None:
        if self._runtime_action_shutdown_requested:
            return None
        retried_job_ids = self.retry_waiting_jobs(sync_events=False)
        return SettingsRetryCompletedEvent(retried_job_ids=retried_job_ids)

    def _load_preset_languages_for_worker(
        self,
        *,
        request_id: int,
        session_tab_id: str,
        workspace_tab_id: str,
    ) -> PresetPromptLanguagesLoadedEvent | None:
        if self._runtime_action_shutdown_requested:
            return None

        prompt_store = self._prompt_store
        if prompt_store is None:
            return PresetPromptLanguagesLoadedEvent(
                request_id=request_id,
                session_tab_id=session_tab_id,
                workspace_tab_id=workspace_tab_id,
                languages=(),
            )

        try:
            languages = tuple(prompt_store.list_languages())
        except Exception:
            LOGGER.exception("Failed to load preset prompt languages.")
            return PresetPromptLanguagesLoadedEvent(
                request_id=request_id,
                session_tab_id=session_tab_id,
                workspace_tab_id=workspace_tab_id,
                languages=(),
                error_message="프리셋 언어 목록을 읽지 못했습니다.",
            )

        return PresetPromptLanguagesLoadedEvent(
            request_id=request_id,
            session_tab_id=session_tab_id,
            workspace_tab_id=workspace_tab_id,
            languages=languages,
        )

    def _load_preset_instructions_for_worker(
        self,
        *,
        request_id: int,
        session_tab_id: str,
        workspace_tab_id: str,
        language: str,
    ) -> PresetPromptInstructionsLoadedEvent | None:
        if self._runtime_action_shutdown_requested:
            return None

        prompt_store = self._prompt_store
        if prompt_store is None:
            return PresetPromptInstructionsLoadedEvent(
                request_id=request_id,
                session_tab_id=session_tab_id,
                workspace_tab_id=workspace_tab_id,
                language=language,
                instructions=(),
            )

        try:
            instructions = tuple(
                instruction.instruction
                for instruction in prompt_store.list_instructions(language)
            )
        except Exception:
            LOGGER.exception(
                "Failed to load preset prompt instructions. language=%s",
                language,
            )
            return PresetPromptInstructionsLoadedEvent(
                request_id=request_id,
                session_tab_id=session_tab_id,
                workspace_tab_id=workspace_tab_id,
                language=language,
                instructions=(),
                error_message="프리셋 지시문 목록을 읽지 못했습니다.",
            )

        return PresetPromptInstructionsLoadedEvent(
            request_id=request_id,
            session_tab_id=session_tab_id,
            workspace_tab_id=workspace_tab_id,
            language=language,
            instructions=instructions,
        )

    def _submit_preset_analysis_job_for_worker(
        self,
        session_tab_id: str,
        *,
        language: str,
        instruction: str,
        work_priority: str,
        analysis_prompt_prefix: str,
        auto_commit_enabled: bool,
        execution_options: AgentExecutionOptions | None,
        candidate_execution_options: AgentExecutionOptions | None,
    ) -> (
        PresetAnalysisJobSubmittedEvent
        | PresetAnalysisJobSubmissionFailedEvent
        | None
    ):
        if self._runtime_action_shutdown_requested:
            return None

        try:
            job = self.submit_preset_analysis_job(
                session_tab_id,
                language=language,
                instruction=instruction,
                work_priority=work_priority,
                analysis_prompt_prefix=analysis_prompt_prefix,
                auto_commit_enabled=auto_commit_enabled,
                execution_options=execution_options,
                candidate_execution_options=candidate_execution_options,
            )
        except ValueError as error:
            return PresetAnalysisJobSubmissionFailedEvent(
                session_tab_id=session_tab_id,
                title="입력 오류",
                message=str(error),
            )
        except Exception:
            LOGGER.exception(
                "Failed to submit preset analysis job. "
                "session_tab_id=%s language=%s instruction=%s",
                session_tab_id,
                language,
                instruction,
            )
            return PresetAnalysisJobSubmissionFailedEvent(
                session_tab_id=session_tab_id,
                title="작업 오류",
                message="프리셋 분석 작업을 등록할 수 없습니다.",
            )

        return PresetAnalysisJobSubmittedEvent(
            workspace_tab_id=job.workspace_tab_id,
            session_tab_id=job.session_tab_id,
            job_id=job.job_id,
            analysis_prompt_prefix=analysis_prompt_prefix,
        )

    def _process_runtime_action_completions(self, *, max_items: int | None = None) -> int:
        processed = 0
        completed_workspace_paths: list[str] = []
        while max_items is None or processed < max_items:
            try:
                completion = self._runtime_action_completion_queue.get_nowait()
            except Empty:
                self._remember_saved_workspaces(completed_workspace_paths)
                self._enqueue_persistence_shutdown_if_ready()
                return processed

            processed += 1
            self._sync_controller_events()
            self._enqueue_dispatch_next_job_if_needed()
            if self._runtime_action_completion_is_stale(completion):
                continue
            if isinstance(completion.event, WorkspaceOpenCompletedEvent):
                completed_workspace_paths.append(completion.event.workspace_path)
            if completion.event is not None:
                self._event_queue.put(completion.event)
        self._remember_saved_workspaces(completed_workspace_paths)
        self._enqueue_persistence_shutdown_if_ready()
        return processed

    def _runtime_action_completion_is_stale(
        self,
        completion: _RuntimeActionCompletion,
    ) -> bool:
        if not completion.drop_when_stale:
            return False
        if completion.queue_control_generation is None:
            return False
        return not self._queue_start_is_current(
            completion.queue_control_workspace_tab_id,
            completion.queue_control_generation,
        )

    def _enqueue_persistence_save(
        self,
        save_action: Callable[[], SaveResult],
        *,
        coalesce_key: str | None = None,
    ) -> None:
        self._persistence_request_queue.put(
            _PersistenceSaveRequest(
                save_action=save_action,
                coalesce_key=coalesce_key,
            )
        )

    def _run_persistence_worker(self) -> None:
        while True:
            request = self._persistence_request_queue.get()
            if request is None:
                return

            requests, shutdown_requested = self._coalesce_persistence_requests(request)
            for save_request in requests:
                save_result = save_request.save_action()
                self._persistence_completion_queue.put(
                    _PersistenceSaveCompletion(issue=save_result.issue)
                )
            if shutdown_requested:
                return

    def _coalesce_persistence_requests(
        self,
        first_request: _PersistenceSaveRequest,
    ) -> tuple[tuple[_PersistenceSaveRequest, ...], bool]:
        requests = [first_request]
        shutdown_requested = False

        while True:
            try:
                request = self._persistence_request_queue.get_nowait()
            except Empty:
                break

            if request is None:
                shutdown_requested = True
                break
            requests.append(request)

        coalesced_requests: list[_PersistenceSaveRequest] = []
        seen_keys: set[str] = set()
        for request in reversed(requests):
            key = request.coalesce_key
            if key is not None:
                if key in seen_keys:
                    continue
                seen_keys.add(key)
            coalesced_requests.append(request)

        coalesced_requests.reverse()
        return tuple(coalesced_requests), shutdown_requested

    def _process_persistence_completions(self, *, max_items: int | None = None) -> int:
        processed = 0
        while max_items is None or processed < max_items:
            try:
                completion = self._persistence_completion_queue.get_nowait()
            except Empty:
                return processed

            processed += 1
            if completion.issue is not None:
                self._event_queue.put(PersistenceIssueEvent(issue=completion.issue))
        return processed

    def _sync_system_sleep_prevention(self) -> None:
        preventer = getattr(self, "_system_sleep_preventer", None)
        if preventer is None:
            return

        with self._system_sleep_prevention_lock:
            if not self._system_sleep_prevention_dirty:
                return
            self._system_sleep_prevention_dirty = False
            previous_active = self._system_sleep_prevention_active

        try:
            active = self._queue_activity_requires_awake()
            if previous_active == active:
                return
            preventer.set_active(active)
        except Exception:
            self._mark_system_sleep_prevention_dirty()
            LOGGER.exception("Failed to update system sleep prevention state.")
            return

        with self._system_sleep_prevention_lock:
            self._system_sleep_prevention_active = active

    def _mark_system_sleep_prevention_dirty(self) -> None:
        if getattr(self, "_system_sleep_preventer", None) is None:
            return
        with self._system_sleep_prevention_lock:
            self._system_sleep_prevention_dirty = True

    def _release_system_sleep_prevention(self) -> None:
        preventer = getattr(self, "_system_sleep_preventer", None)
        if preventer is None:
            return

        try:
            release = getattr(preventer, "release", None)
            if release is not None:
                release()
            else:
                preventer.set_active(False)
            with self._system_sleep_prevention_lock:
                self._system_sleep_prevention_active = False
                self._system_sleep_prevention_dirty = False
        except Exception:
            self._mark_system_sleep_prevention_dirty()
            LOGGER.exception("Failed to release system sleep prevention state.")

    def _queue_activity_requires_awake(self) -> bool:
        scheduler = getattr(self._controller, "scheduler", None)
        if scheduler is None:
            return False

        try:
            return self._has_running_job(scheduler) or self._has_started_queue(scheduler)
        except Exception:
            LOGGER.exception("Failed to inspect queue activity for sleep prevention.")
            return False

    def _has_running_job(self, scheduler: object) -> bool:
        has_running_job = getattr(scheduler, "has_running_job", None)
        if has_running_job is not None:
            return bool(has_running_job())

        list_jobs = getattr(scheduler, "list_jobs", None)
        if list_jobs is None:
            return False

        return any(getattr(job, "status", None) == JobStatus.RUNNING for job in list_jobs())

    def _has_started_queue(self, scheduler: object) -> bool:
        has_started_queue = getattr(scheduler, "has_started_queue", None)
        if has_started_queue is not None:
            return bool(has_started_queue())

        list_queue_states = getattr(scheduler, "list_queue_states", None)
        if list_queue_states is not None:
            return any(
                getattr(queue_state, "status", None) == QueueStatus.STARTED
                for queue_state in list_queue_states()
            )

        workspace_manager = getattr(self._controller, "workspace_manager", None)
        list_workspace_tabs = getattr(workspace_manager, "list_workspace_tabs", None)
        get_queue_state = getattr(scheduler, "get_queue_state", None)
        if list_workspace_tabs is None or get_queue_state is None:
            return False

        return any(
            getattr(get_queue_state(workspace_tab.workspace_tab_id), "status", None)
            == QueueStatus.STARTED
            for workspace_tab in list_workspace_tabs(include_closed=False)
        )

    def _apply_event_state(self, event: AppRuntimeEvent) -> None:
        if isinstance(event, JobExecutionResultCapturedEvent):
            self._handle_preset_execution_result(event)
            return

        if isinstance(event, JobStatusChangedEvent):
            self._refresh_preset_queue_generation_on_job_start(event)
            self._mark_system_sleep_prevention_dirty()
            self._job_user_messages[event.job_id] = event.user_message or ""
            return

        if isinstance(event, LogAppendedEvent):
            self._get_job_progress_log_buffer(event.job_id).append(event.line.rstrip())
            return

    def _get_job_progress_log_buffer(self, job_id: str) -> deque[str]:
        log_buffer = self._job_progress_logs.get(job_id)
        if log_buffer is None:
            log_buffer = deque(maxlen=MAX_JOB_PROGRESS_LOG_LINES)
            self._job_progress_logs[job_id] = log_buffer
        return log_buffer

    def _refresh_preset_queue_generation_on_job_start(
        self,
        event: JobStatusChangedEvent,
    ) -> None:
        if event.current_status != JobStatus.RUNNING:
            return

        queue_control_generation = self._get_job_start_queue_control_generation(
            event.workspace_tab_id
        )
        if self._refresh_preset_context_queue_generation(
            self._get_preset_analysis_job_contexts(),
            job_id=event.job_id,
            workspace_tab_id=event.workspace_tab_id,
            queue_control_generation=queue_control_generation,
            turn_label="turn1",
        ):
            return

        self._refresh_preset_context_queue_generation(
            self._get_preset_work_generation_job_contexts(),
            job_id=event.job_id,
            workspace_tab_id=event.workspace_tab_id,
            queue_control_generation=queue_control_generation,
            turn_label="turn2",
        )

    def _refresh_preset_context_queue_generation(
        self,
        contexts: (
            dict[str, _PresetAnalysisJobContext]
            | dict[str, _PresetWorkGenerationJobContext]
        ),
        *,
        job_id: str,
        workspace_tab_id: str,
        queue_control_generation: tuple[int, int],
        turn_label: str,
    ) -> bool:
        context = contexts.get(job_id)
        if context is None:
            return False
        if context.queue_control_generation == queue_control_generation:
            return True

        contexts[job_id] = replace(
            context,
            queue_control_generation=queue_control_generation,
        )
        LOGGER.debug(
            "Preset %s queue generation refreshed at job start. "
            "job_id=%s workspace_tab_id=%s generation=%s",
            turn_label,
            job_id,
            workspace_tab_id,
            queue_control_generation,
        )
        return True

    def _handle_preset_execution_result(self, event: JobExecutionResultCapturedEvent) -> None:
        if getattr(self, "_runtime_action_shutdown_requested", False):
            return

        analysis_contexts = self._get_preset_analysis_job_contexts()
        analysis_context = analysis_contexts.pop(event.job_id, None)
        if analysis_context is not None:
            LOGGER.info(
                "Preset turn1 result captured. job_id=%s workspace_tab_id=%s "
                "session_tab_id=%s status=%s language=%s instruction=%s "
                "work_priority=%s",
                event.job_id,
                event.workspace_tab_id,
                event.session_tab_id,
                event.status.value,
                analysis_context.language,
                analysis_context.instruction,
                analysis_context.work_priority,
            )
            if event.status == AgentRunStatus.COMPLETED:
                self._enqueue_completed_preset_analysis_job(event, analysis_context)
                return

            candidates_present = self._analysis_response_has_candidates(
                event.last_message,
                work_priority=analysis_context.work_priority,
            )
            if candidates_present:
                self._fail_preset_flow_and_stop_workspace(
                    workspace_tab_id=event.workspace_tab_id,
                    message="프리셋 작업 후보가 있지만 작업 프롬프트 생성 작업이 실행되지 않았습니다.",
                    log_message=(
                        "Preset turn2 skipped because turn1 did not complete even though "
                        "selected candidates were present. "
                        "job_id=%s workspace_tab_id=%s status=%s"
                    ),
                    log_args=(event.job_id, event.workspace_tab_id, event.status.value),
                )
            else:
                LOGGER.warning(
                    "Preset turn2 skipped because turn1 did not complete. "
                    "job_id=%s workspace_tab_id=%s session_tab_id=%s status=%s "
                    "selected_candidates_present=%s",
                    event.job_id,
                    event.workspace_tab_id,
                    event.session_tab_id,
                    event.status.value,
                    candidates_present,
                )
            return

        work_generation_contexts = self._get_preset_work_generation_job_contexts()
        work_generation_context = work_generation_contexts.pop(event.job_id, None)
        if work_generation_context is not None:
            LOGGER.info(
                "Preset turn2 result captured. job_id=%s workspace_tab_id=%s "
                "session_tab_id=%s status=%s parent_session_tab_id=%s "
                "candidate_count=%s",
                event.job_id,
                event.workspace_tab_id,
                event.session_tab_id,
                event.status.value,
                work_generation_context.parent_session_tab_id,
                len(work_generation_context.candidates),
            )
            if event.status == AgentRunStatus.COMPLETED:
                self._enqueue_completed_preset_work_generation_job(
                    event,
                    work_generation_context,
                )
            else:
                self._fail_preset_flow_and_stop_workspace(
                    workspace_tab_id=event.workspace_tab_id,
                    message="프리셋 작업 후보가 있지만 작업 프롬프트 생성 작업이 실행되지 않았습니다.",
                    log_message=(
                        "Preset work-generation turn did not complete. "
                        "job_id=%s workspace_tab_id=%s status=%s candidate_count=%s"
                    ),
                    log_args=(
                        event.job_id,
                        event.workspace_tab_id,
                        event.status.value,
                        len(work_generation_context.candidates),
                    ),
                )
            return

    def _enqueue_completed_preset_analysis_job(
        self,
        event: JobExecutionResultCapturedEvent,
        context: _PresetAnalysisJobContext,
    ) -> None:
        self._enqueue_preset_followup_runtime_action(
            workspace_tab_id=event.workspace_tab_id,
            action=lambda: self._handle_completed_preset_analysis_job(event, context),
            failure_title="프리셋 작업 오류",
            failure_message="프리셋 분석 결과를 처리하지 못했습니다.",
            log_message="Failed to handle completed preset analysis job in background.",
            queue_control_generation=context.queue_control_generation,
            discard_log_message=(
                "Preset turn1 follow-up discarded because queue generation is stale. "
                "job_id=%s workspace_tab_id=%s session_tab_id=%s"
            ),
            discard_log_args=(
                event.job_id,
                event.workspace_tab_id,
                event.session_tab_id,
            ),
        )

    def _enqueue_preset_followup_runtime_action(
        self,
        *,
        workspace_tab_id: str,
        action: Callable[[], RuntimeActionEvent | None],
        failure_title: str,
        failure_message: str,
        log_message: str,
        queue_control_generation: tuple[int, int],
        discard_log_message: str,
        discard_log_args: tuple[object, ...] = (),
    ) -> None:
        if getattr(self, "_runtime_action_shutdown_requested", False):
            return

        self._mark_preset_followup_pending(workspace_tab_id)

        def discard_followup() -> None:
            self._clear_preset_followup_pending(workspace_tab_id)
            if getattr(self, "_runtime_action_shutdown_requested", False):
                LOGGER.info(
                    "Preset follow-up discarded because runtime is shutting down. "
                    "workspace_tab_id=%s",
                    workspace_tab_id,
                )
                return
            LOGGER.info(discard_log_message, *discard_log_args)
            self._enqueue_dispatch_next_job_if_needed()

        def wrapped_action() -> RuntimeActionEvent | None:
            try:
                return action()
            finally:
                self._clear_preset_followup_pending(workspace_tab_id)

        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=wrapped_action,
                failure_title=failure_title,
                failure_message=failure_message,
                log_message=log_message,
                workspace_tab_id=workspace_tab_id,
                queue_control_generation=queue_control_generation,
                on_discard=discard_followup,
                drop_completion_when_stale=False,
            )
        )

    def _handle_completed_preset_analysis_job(
        self,
        event: JobExecutionResultCapturedEvent,
        context: _PresetAnalysisJobContext,
    ) -> None:
        if not self._queue_start_is_current(
            event.workspace_tab_id,
            context.queue_control_generation,
        ):
            LOGGER.info(
                "Preset turn1 completion ignored because queue generation is stale. "
                "job_id=%s workspace_tab_id=%s",
                event.job_id,
                event.workspace_tab_id,
            )
            return

        if not self._session_tab_is_open(event.session_tab_id):
            LOGGER.info(
                "Preset turn2 skipped because parent preset session is closed. "
                "job_id=%s workspace_tab_id=%s session_tab_id=%s",
                event.job_id,
                event.workspace_tab_id,
                event.session_tab_id,
            )
            return

        LOGGER.info(
            "Preset turn1 completed; preparing turn2. job_id=%s workspace_tab_id=%s "
            "session_tab_id=%s language=%s instruction=%s work_priority=%s",
            event.job_id,
            event.workspace_tab_id,
            event.session_tab_id,
            context.language,
            context.instruction,
            context.work_priority,
        )
        response_text = (event.last_message or "").strip()
        if not response_text:
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=event.workspace_tab_id,
                message="프리셋 분석 응답을 읽지 못했습니다.",
                log_message=(
                    "Preset turn2 not started because turn1 response was empty. "
                    "job_id=%s workspace_tab_id=%s session_tab_id=%s"
                ),
                log_args=(
                    event.job_id,
                    event.workspace_tab_id,
                    event.session_tab_id,
                ),
            )
            return

        result = prepare_preset_work_generation_prompt(
            analysis_response_text=response_text,
            work_prompt_template=context.work_prompt_template,
            work_priority=context.work_priority,
        )
        if result.issue is not None:
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=event.workspace_tab_id,
                message=result.issue.message,
                log_message=(
                    "Preset turn2 not started because turn1 response could not be used. "
                    "job_id=%s workspace_tab_id=%s session_tab_id=%s issue=%s"
                ),
                log_args=(
                    event.job_id,
                    event.workspace_tab_id,
                    event.session_tab_id,
                    result.issue.message,
                ),
            )
            return
        if not result.selected_candidates or result.work_generation_prompt is None:
            LOGGER.info(
                "Preset turn2 skipped because no candidates matched work priority. "
                "job_id=%s workspace_tab_id=%s session_tab_id=%s "
                "candidate_count=%s selected_candidate_count=0 work_priority=%s",
                event.job_id,
                event.workspace_tab_id,
                event.session_tab_id,
                self._count_analysis_candidates(response_text),
                context.work_priority,
            )
            return

        try:
            with self._get_controller_state_lock():
                if not self._queue_start_is_current(
                    event.workspace_tab_id,
                    context.queue_control_generation,
                ):
                    LOGGER.info(
                        "Preset turn2 registration skipped because queue generation is stale. "
                        "turn1_job_id=%s workspace_tab_id=%s",
                        event.job_id,
                        event.workspace_tab_id,
                    )
                    return
                if not self._session_tab_is_open(event.session_tab_id):
                    LOGGER.info(
                        "Preset turn2 registration skipped because parent preset "
                        "session is closed. turn1_job_id=%s workspace_tab_id=%s "
                        "session_tab_id=%s",
                        event.job_id,
                        event.workspace_tab_id,
                        event.session_tab_id,
                    )
                    return
                with self._defer_controller_dispatch():
                    job = self._controller.submit_job(
                        event.session_tab_id,
                        result.work_generation_prompt,
                        dispatch_immediately=False,
                        force_fresh_session=True,
                        execution_options=context.execution_options,
                    )
                    self._controller.prioritize_queued_jobs((job.job_id,))
                    self._get_preset_work_generation_job_contexts()[
                        job.job_id
                    ] = _PresetWorkGenerationJobContext(
                        parent_session_tab_id=event.session_tab_id,
                        candidates=result.selected_candidates,
                        auto_commit_enabled=context.auto_commit_enabled,
                        execution_options=context.execution_options,
                        candidate_execution_options=(
                            context.resolved_candidate_execution_options()
                        ),
                        queue_control_generation=context.queue_control_generation,
                    )
                    LOGGER.info(
                        "Preset turn2 registered. turn1_job_id=%s turn2_job_id=%s "
                        "workspace_tab_id=%s session_tab_id=%s candidate_count=%s "
                        "auto_commit_enabled=%s",
                        event.job_id,
                        job.job_id,
                        event.workspace_tab_id,
                        event.session_tab_id,
                        len(result.selected_candidates),
                        context.auto_commit_enabled,
                    )
                    self._controller.start_queue(event.workspace_tab_id)
                self._sync_controller_events()
            self._mark_system_sleep_prevention_dirty()
        except Exception:
            LOGGER.exception(
                "Failed to register preset work-generation job. parent_job_id=%s",
                event.job_id,
            )
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=event.workspace_tab_id,
                message="프리셋 작업 프롬프트 생성 작업을 등록할 수 없습니다.",
                log_message=(
                    "Preset flow stopped because work-generation job registration failed. "
                    "parent_job_id=%s workspace_tab_id=%s"
                ),
                log_args=(event.job_id, event.workspace_tab_id),
            )
            return

        self._enqueue_dispatch_next_job_if_needed()

    def _enqueue_completed_preset_work_generation_job(
        self,
        event: JobExecutionResultCapturedEvent,
        context: _PresetWorkGenerationJobContext,
    ) -> None:
        self._enqueue_preset_followup_runtime_action(
            workspace_tab_id=event.workspace_tab_id,
            action=lambda: self._handle_completed_preset_work_generation_job(
                event,
                context,
            ),
            failure_title="프리셋 작업 오류",
            failure_message="프리셋 후보 작업을 처리하지 못했습니다.",
            log_message=(
                "Failed to handle completed preset work-generation job "
                "in background."
            ),
            queue_control_generation=context.queue_control_generation,
            discard_log_message=(
                "Preset turn2 follow-up discarded because queue generation is stale. "
                "job_id=%s workspace_tab_id=%s session_tab_id=%s "
                "parent_session_tab_id=%s"
            ),
            discard_log_args=(
                event.job_id,
                event.workspace_tab_id,
                event.session_tab_id,
                context.parent_session_tab_id,
            ),
        )

    def _handle_completed_preset_work_generation_job(
        self,
        event: JobExecutionResultCapturedEvent,
        context: _PresetWorkGenerationJobContext,
    ) -> PresetCandidateJobsRegisteredEvent | None:
        if not self._queue_start_is_current(
            event.workspace_tab_id,
            context.queue_control_generation,
        ):
            LOGGER.info(
                "Preset turn2 completion ignored because queue generation is stale. "
                "job_id=%s workspace_tab_id=%s",
                event.job_id,
                event.workspace_tab_id,
            )
            return

        if not self._session_tab_is_open(context.parent_session_tab_id):
            LOGGER.info(
                "Preset candidate job registration skipped because parent preset "
                "session is closed. job_id=%s workspace_tab_id=%s "
                "parent_session_tab_id=%s",
                event.job_id,
                event.workspace_tab_id,
                context.parent_session_tab_id,
            )
            return

        LOGGER.info(
            "Preset turn2 completed; parsing generated prompts. job_id=%s "
            "workspace_tab_id=%s session_tab_id=%s parent_session_tab_id=%s "
            "candidate_count=%s",
            event.job_id,
            event.workspace_tab_id,
            event.session_tab_id,
            context.parent_session_tab_id,
            len(context.candidates),
        )
        response_text = (event.last_message or "").strip()
        if not response_text:
            LOGGER.warning(
                "Preset candidate jobs not registered because turn2 response was empty. "
                "job_id=%s workspace_tab_id=%s session_tab_id=%s candidate_count=%s",
                event.job_id,
                event.workspace_tab_id,
                event.session_tab_id,
                len(context.candidates),
            )
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=event.workspace_tab_id,
                message="작업 프롬프트 응답을 읽지 못했습니다.",
                log_message=(
                    "Preset flow stopped because work-generation response was empty. "
                    "job_id=%s workspace_tab_id=%s candidate_count=%s"
                ),
                log_args=(event.job_id, event.workspace_tab_id, len(context.candidates)),
            )
            return

        result = parse_preset_generated_work_prompts(
            generation_response_text=response_text,
            input_candidates=context.candidates,
        )
        if result.issue is not None:
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=event.workspace_tab_id,
                message=result.issue.message,
                log_message=(
                    "Preset flow stopped while parsing generated work prompts. "
                    "job_id=%s workspace_tab_id=%s candidate_count=%s issue=%s"
                ),
                log_args=(
                    event.job_id,
                    event.workspace_tab_id,
                    len(context.candidates),
                    result.issue.message,
                ),
            )
            return

        LOGGER.info(
            "Preset turn2 parsed generated prompts. job_id=%s workspace_tab_id=%s "
            "candidate_count=%s prompt_count=%s",
            event.job_id,
            event.workspace_tab_id,
            len(context.candidates),
            len(result.prompts),
        )
        candidate_execution_options = context.resolved_candidate_execution_options()
        try:
            with self._get_controller_state_lock():
                if not self._queue_start_is_current(
                    event.workspace_tab_id,
                    context.queue_control_generation,
                ):
                    LOGGER.info(
                        "Preset candidate job registration skipped because queue generation "
                        "is stale. turn2_job_id=%s workspace_tab_id=%s",
                        event.job_id,
                        event.workspace_tab_id,
                    )
                    return
                if not self._session_tab_is_open(context.parent_session_tab_id):
                    LOGGER.info(
                        "Preset candidate job registration skipped because parent "
                        "preset session is closed. turn2_job_id=%s workspace_tab_id=%s "
                        "parent_session_tab_id=%s",
                        event.job_id,
                        event.workspace_tab_id,
                        context.parent_session_tab_id,
                    )
                    return
                with self._defer_controller_dispatch():
                    candidate_sessions = (
                        self._controller.session_manager.open_preset_candidate_sessions(
                            context.parent_session_tab_id,
                            count=len(result.prompts),
                            execution_options=candidate_execution_options,
                        )
                    )
                    candidate_session_tab_ids = [
                        candidate_session.session_tab_id
                        for candidate_session in candidate_sessions
                    ]
                    job_requests: list[tuple[SessionTabId, str]] = []
                    for candidate_session, generated_prompt in zip(
                        candidate_sessions,
                        result.prompts,
                    ):
                        job_requests.append(
                            (candidate_session.session_tab_id, generated_prompt.prompt)
                        )
                        if context.auto_commit_enabled:
                            job_requests.append(
                                (candidate_session.session_tab_id, AUTO_COMMIT_PROMPT)
                            )

                    registered_jobs = self._controller.submit_jobs(
                        job_requests,
                        dispatch_immediately=False,
                        execution_options=candidate_execution_options,
                    )
                    registered_job_ids = [job.job_id for job in registered_jobs]
                    self._controller.prioritize_queued_jobs(registered_job_ids)
                    self._controller.start_queue(event.workspace_tab_id)
                    LOGGER.info(
                        "Preset candidate jobs registered. turn2_job_id=%s "
                        "workspace_tab_id=%s parent_session_tab_id=%s "
                        "candidate_session_count=%s registered_job_count=%s "
                        "auto_commit_enabled=%s",
                        event.job_id,
                        event.workspace_tab_id,
                        context.parent_session_tab_id,
                        len(candidate_sessions),
                        len(registered_jobs),
                        context.auto_commit_enabled,
                    )
            self._mark_system_sleep_prevention_dirty()
        except Exception:
            LOGGER.exception(
                "Failed to register preset candidate jobs. work_generation_job_id=%s",
                event.job_id,
            )
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=event.workspace_tab_id,
                message="프리셋 후보 작업을 등록할 수 없습니다.",
                log_message=(
                    "Preset flow stopped because candidate job registration failed. "
                    "work_generation_job_id=%s workspace_tab_id=%s"
                ),
                log_args=(event.job_id, event.workspace_tab_id),
            )
            return

        self._enqueue_dispatch_next_job_if_needed()
        return PresetCandidateJobsRegisteredEvent(
            workspace_tab_id=event.workspace_tab_id,
            parent_session_tab_id=context.parent_session_tab_id,
            candidate_session_tab_ids=tuple(candidate_session_tab_ids),
            registered_job_ids=tuple(registered_job_ids),
            auto_commit_enabled=context.auto_commit_enabled,
        )

    def _fail_preset_flow_and_stop_workspace(
        self,
        *,
        workspace_tab_id: str,
        message: str,
        log_message: str,
        log_args: tuple[object, ...] = (),
    ) -> None:
        LOGGER.warning(log_message, *log_args)
        self._advance_queue_control_generation(workspace_tab_id)
        with self._get_controller_state_lock():
            self._controller.stop_queue(
                workspace_tab_id,
                reason=QueueStopReason.PRESET_FLOW_FAILED,
            )
            self._sync_controller_events()
        LOGGER.warning(
            "Preset flow stopped workspace queue. workspace_tab_id=%s reason=%s "
            "user_message=%s",
            workspace_tab_id,
            QueueStopReason.PRESET_FLOW_FAILED.value,
            message,
        )
        self._mark_system_sleep_prevention_dirty()
        self._publish_preset_flow_failure(message, workspace_tab_id=workspace_tab_id)

    def _publish_preset_flow_failure(
        self,
        message: str,
        *,
        workspace_tab_id: str | None = None,
    ) -> None:
        self._event_queue.put(
            RuntimeActionFailedEvent(
                title="프리셋 작업 오류",
                message=message,
                workspace_tab_id=workspace_tab_id,
            )
        )

    def _session_tab_is_open(self, session_tab_id: str) -> bool:
        session_manager = getattr(self._controller, "session_manager", None)
        get_session_tab = getattr(session_manager, "get_session_tab", None)
        if get_session_tab is None:
            return True
        try:
            session_tab = get_session_tab(session_tab_id)
        except KeyError:
            return False
        except Exception:
            LOGGER.exception(
                "Failed to inspect session tab before preset follow-up. "
                "session_tab_id=%s",
                session_tab_id,
            )
            return False
        return getattr(session_tab, "open_state", TabOpenState.OPEN) == TabOpenState.OPEN

    @staticmethod
    def _analysis_response_has_candidates(
        response_text: str | None,
        *,
        work_priority: str,
    ) -> bool:
        if not response_text:
            return False
        try:
            candidates = extract_candidates(response_text)
            selected_candidates = select_work_candidates(candidates, work_priority)
        except Exception:
            return False
        return bool(selected_candidates)

    @staticmethod
    def _count_analysis_candidates(response_text: str) -> int | None:
        try:
            return len(extract_candidates(response_text))
        except Exception:
            return None

    def _get_preset_analysis_job_contexts(self) -> dict[str, _PresetAnalysisJobContext]:
        contexts = getattr(self, "_preset_analysis_job_contexts", None)
        if contexts is None:
            contexts = {}
            self._preset_analysis_job_contexts = contexts
        return contexts

    def _get_preset_work_generation_job_contexts(
        self,
    ) -> dict[str, _PresetWorkGenerationJobContext]:
        contexts = getattr(self, "_preset_work_generation_job_contexts", None)
        if contexts is None:
            contexts = {}
            self._preset_work_generation_job_contexts = contexts
        return contexts

    @staticmethod
    def _sort_saved_workspaces(
        workspaces: Iterable[SavedWorkspace],
    ) -> tuple[SavedWorkspace, ...]:
        return tuple(
            sorted(
                workspaces,
                key=AppRuntime._saved_workspace_sort_key,
            )
        )

    @staticmethod
    def _saved_workspace_sort_key(workspace: SavedWorkspace) -> tuple[float, str, str]:
        return (
            -AppRuntime._workspace_sort_timestamp(workspace),
            workspace.display_name.casefold(),
            workspace.path.casefold(),
        )

    @staticmethod
    def _workspace_sort_timestamp(workspace: SavedWorkspace) -> float:
        timestamp_value: datetime = workspace.last_selected_at or workspace.added_at
        try:
            return timestamp_value.timestamp()
        except (OSError, OverflowError, ValueError) as exc:
            LOGGER.warning(
                "Saved workspace timestamp cannot be converted for sorting. path=%s",
                workspace.path,
                exc_info=exc,
            )
            return float("-inf")


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
    return (
        f"{normalized_prompt}\n\n"
        f"선택된 Work Priority: {work_priority}\n"
        "Work Priority는 최소 작업 우선순위 threshold이다. "
        "high는 priority=high 후보만 포함하고, "
        "medium은 priority=high 또는 priority=medium 후보를 포함하며, "
        "low는 priority=high/medium/low 후보를 모두 포함하라. "
        "이 기준 밖의 후보는 candidates에서 제외하라."
    )
