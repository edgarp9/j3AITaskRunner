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
    QUEUE_MODE_SHARED,
    QueueStopReason,
    QueueStatus,
    SavedWorkspace,
    SessionExitHookConfig,
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

from .runtime_queue_control import AppRuntimeQueueControlMixin

LOGGER = logging.getLogger("app.runtime")


def _runtime_global(name: str):
    return getattr(sys.modules["app.runtime"], name)


class AppRuntimeQueueMixin(AppRuntimeQueueControlMixin):
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

    def set_session_exit_hook_config(
        self,
        session_tab_id: str,
        exit_hook: SessionExitHookConfig,
    ) -> SessionTab:
        """Store the runtime-only session completion hook configuration."""
        with self._get_controller_state_lock():
            return self._controller.session_manager.set_session_exit_hook_config(
                session_tab_id,
                exit_hook,
            )

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

    def submit_immediate_job(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        auto_commit_enabled: bool,
        execution_options: AgentExecutionOptions | None = None,
    ) -> None:
        """Request one normal-session job to start outside the queue slot."""
        resolved_execution_options = (
            execution_options or execution_options_from_settings(self._settings)
        )
        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=lambda: self._submit_immediate_job_for_worker(
                    session_tab_id,
                    prompt,
                    auto_commit_enabled=auto_commit_enabled,
                    execution_options=resolved_execution_options,
                ),
                failure_title="작업 오류",
                failure_message="바로실행 작업을 시작할 수 없습니다.",
                log_message="Failed to submit immediate job in background.",
            )
        )

    def _submit_immediate_job_for_worker(
        self,
        session_tab_id: str,
        prompt: str,
        *,
        auto_commit_enabled: bool,
        execution_options: AgentExecutionOptions,
    ) -> None:
        if self._runtime_action_shutdown_requested:
            return None

        with self._get_controller_state_lock():
            self._controller.submit_immediate_job(
                session_tab_id,
                prompt,
                execution_options=execution_options,
            )
            session_tab = self._controller.session_manager.lock_session_execution_options(
                session_tab_id,
                execution_options,
            )
            self._remember_session_execution_options_for_workspace_locked(
                session_tab.workspace_tab_id,
                execution_options,
            )
            if auto_commit_enabled:
                self._controller.submit_job(
                    session_tab_id,
                    AUTO_COMMIT_PROMPT,
                    dispatch_immediately=False,
                    execution_options=execution_options,
                )
            self._sync_controller_events()

        self._enqueue_dispatch_next_job_if_needed()
        return None

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
        cleared_events = self._clear_preset_manual_selection_contexts(
            workspace_tab_id=target_workspace_tab_id
        )
        self._publish_preset_manual_selection_cleared_events(cleared_events)
        self._mark_system_sleep_prevention_dirty()
        self._sync_system_sleep_prevention()
        return queue_state

    def stop_all_queues(self) -> None:
        """Stop every workspace queue and sync resulting controller events."""
        self._advance_all_queue_control_generations()
        with self._get_controller_state_lock():
            self._controller.stop_all_queues()
            self._sync_controller_events()
        cleared_events = self._clear_preset_manual_selection_contexts()
        self._publish_preset_manual_selection_cleared_events(cleared_events)
        self._mark_system_sleep_prevention_dirty()
        self._sync_system_sleep_prevention()

    def update_settings(self, settings: AppSettings) -> SettingsUpdateResult:
        """Update current settings, persist them, and retry waiting jobs in the background."""
        previous_settings = self._settings
        queue_mode_changed = settings.queue_mode != previous_settings.queue_mode
        cleared_job_count = 0
        if queue_mode_changed:
            with self._get_controller_state_lock():
                if self._controller.scheduler.has_running_job():
                    raise ValueError(
                        "작업이 진행 중이면 작업큐 방식을 변경할 수 없습니다."
                    )
                cleared_job_count = self._controller.clear_all_jobs()
                self._sync_controller_events()
            self._advance_all_queue_control_generations()
            cleared_events = self._clear_preset_manual_selection_contexts()
            self._clear_runtime_job_state()
            self._publish_preset_manual_selection_cleared_events(cleared_events)

        self._settings = settings
        if not queue_mode_changed:
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
        return SettingsUpdateResult(
            queue_mode_changed=queue_mode_changed,
            cleared_job_count=cleared_job_count,
        )

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

    def _clear_runtime_job_state(self) -> None:
        self._job_progress_logs.clear()
        self._job_user_messages.clear()
        self._preset_analysis_job_contexts.clear()
        self._preset_work_generation_job_contexts.clear()
        self._clear_session_exit_hook_runtime_state()
        self._get_preset_manual_selection_contexts().clear()
        with self._preset_followup_lock:
            self._preset_followup_pending_workspace_counts.clear()

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
        self._evaluate_session_exit_hooks()
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
        file_drop_watcher_is_alive = getattr(
            self,
            "_file_drop_watcher_is_alive",
            None,
        )
        if callable(file_drop_watcher_is_alive) and file_drop_watcher_is_alive():
            return True

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
        stop_file_drop_watcher = getattr(self, "_stop_file_drop_watcher", None)
        if callable(stop_file_drop_watcher):
            stop_file_drop_watcher()

        if not self._runtime_action_shutdown_requested:
            self._runtime_action_shutdown_requested = True
            self._advance_all_queue_control_generations()
            self._clear_preset_manual_selection_contexts()
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
                timestamp=_runtime_global("utc_now")(),
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






















