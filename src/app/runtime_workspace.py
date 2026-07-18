"""AppRuntime role mixins split from app.runtime."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
import logging
from queue import Empty
import sys
import threading

from domain import (
    AgentExecutionOptions,
    AppSettings,
    InstructionInfo,
    Job,
    JobStatus,
    QueueStopReason,
    QueueStatus,
    SavedWorkspace,
    SessionTab,
    SessionTabId,
    SessionTabKind,
    SessionTurnHistory,
    StepExecutionMode,
    WorkspaceTab,
    WorkspaceQueueState,
    execution_options_from_settings,
    extract_candidates,
    select_work_candidates,
    workspace_folder_display_name,
)
from domain.models import TabOpenState
from domain.policies import canonicalize_workspace_path
from infra.process_runner import AgentRunStatus

from .controller import JobExecutionResultCapturedEvent, JobStatusChangedEvent, LogAppendedEvent
from .runtime import (
    AUTO_COMMIT_PROMPT,
    DEFAULT_PRESET_WORK_PRIORITY,
    MAX_JOB_PROGRESS_LOG_LINES,
    PRESET_WORK_PRIORITY_OPTIONS,
    AppRuntimeEvent,
    ImportedPromptSessionRegistration,
    ImportedPromptSessionsResult,
    PersistenceIssueEvent,
    PresetAnalysisJobSubmittedEvent,
    PresetAnalysisJobSubmissionFailedEvent,
    PresetCandidateJobsRegisteredEvent,
    PresetPromptInstructionsLoadedEvent,
    PresetPromptLanguagesLoadedEvent,
    QueueStartCompletedEvent,
    RuntimeActionEvent,
    RuntimeActionFailedEvent,
    RuntimeActionWarningEvent,
    SettingsRetryCompletedEvent,
    SettingsUpdateResult,
    WorkspaceOpenActionResult,
    WorkspaceOpenCompletedEvent,
    _PersistenceSaveCompletion,
    _PersistenceSaveRequest,
    _PresetAnalysisJobContext,
    _PresetWorkGenerationJobContext,
    _RuntimeActionCompletion,
    _RuntimeActionRequest,
    _build_preset_analysis_prompt,
    _normalize_preset_work_priority,
    _PERSISTENCE_COALESCE_SAVED_WORKSPACES,
    _PERSISTENCE_COALESCE_SETTINGS,
)
from .scheduler import WorkspaceJobSummary
from .session_manager import CompletedSessionSummary
from .use_cases import (
    SaveResult,
    UseCaseIssue,
    parse_preset_generated_work_prompts,
    prepare_preset_work_generation_prompt,
    save_app_settings,
    save_saved_workspaces,
)

LOGGER = logging.getLogger("app.runtime")


def _runtime_global(name: str):
    return getattr(sys.modules["app.runtime"], name)


class AppRuntimeWorkspaceMixin:
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
        cleared_events = self._clear_preset_manual_selection_contexts(
            parent_session_tab_id=session_tab_id
        )
        self._clear_session_exit_hook_state(session_tab_id)
        self._publish_preset_manual_selection_cleared_events(cleared_events)
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
        cleared_events = self._clear_preset_manual_selection_contexts(
            workspace_tab_id=workspace_tab_id
        )
        self._clear_session_exit_hook_state_for_workspace(workspace_tab_id)
        self._publish_preset_manual_selection_cleared_events(cleared_events)
        self._mark_system_sleep_prevention_dirty()
        self._sync_system_sleep_prevention()
        return result

    def list_session_turns(self, session_tab_id: str) -> tuple[SessionTurnHistory, ...]:
        """Return runtime turn history connected to one session tab."""
        with self._get_controller_state_lock():
            return self._controller.session_manager.list_session_tab_turns(session_tab_id)

    def open_workspace(self, workspace_path: str) -> WorkspaceOpenActionResult:
        """Open a workspace and remember it in persistent saved-workspace data."""
        _runtime_global("validate_workspace_path")(workspace_path)
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
        step_execution_mode: StepExecutionMode = StepExecutionMode.SINGLE_SESSION,
    ) -> ImportedPromptSessionsResult:
        """Create normal session tabs and queued prompt jobs for imported Steps."""
        normalized_prompts = tuple(prompt.strip() for prompt in prompts if prompt.strip())
        if not normalized_prompts:
            raise ValueError("가져올 지시문이 없습니다.")
        resolved_step_execution_mode = StepExecutionMode(step_execution_mode)

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
            if resolved_step_execution_mode == StepExecutionMode.SINGLE_SESSION:
                session_tab = self._controller.open_session(
                    workspace_tab_id,
                    execution_options=resolved_execution_options,
                )
                session_tabs.append(session_tab)
                for prompt in normalized_prompts:
                    job_requests.append((session_tab.session_tab_id, prompt))
                    if auto_commit_enabled:
                        job_requests.append(
                            (session_tab.session_tab_id, AUTO_COMMIT_PROMPT)
                        )
            else:
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
            session_tabs_by_id = {
                session_tab.session_tab_id: (
                    self._controller.session_manager.lock_session_execution_options(
                        session_tab.session_tab_id,
                        resolved_execution_options,
                    )
                )
                for session_tab in session_tabs
            }
            job_iterator = iter(registered_jobs)
            if resolved_step_execution_mode == StepExecutionMode.SINGLE_SESSION:
                session_tab = session_tabs_by_id[session_tabs[0].session_tab_id]
                for _prompt in normalized_prompts:
                    prompt_job = next(job_iterator)
                    auto_commit_job = next(job_iterator) if auto_commit_enabled else None
                    registrations.append(
                        ImportedPromptSessionRegistration(
                            session_tab=session_tab,
                            prompt_job=prompt_job,
                            auto_commit_job=auto_commit_job,
                        )
                    )
            else:
                for session_tab in session_tabs:
                    locked_session_tab = session_tabs_by_id[session_tab.session_tab_id]
                    prompt_job = next(job_iterator)
                    auto_commit_job = (
                        next(job_iterator) if auto_commit_enabled else None
                    )
                    registrations.append(
                        ImportedPromptSessionRegistration(
                            session_tab=locked_session_tab,
                            prompt_job=prompt_job,
                            auto_commit_job=auto_commit_job,
                        )
                    )
            self._sync_controller_events()

        self._enqueue_dispatch_next_job_if_needed()
        return ImportedPromptSessionsResult(registrations=tuple(registrations))

