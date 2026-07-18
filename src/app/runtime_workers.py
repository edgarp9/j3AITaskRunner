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


class AppRuntimeWorkersMixin:
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
            _runtime_global("validate_workspace_path")(workspace_path)
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
            self._record_session_exit_hook_status_event(event)
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

