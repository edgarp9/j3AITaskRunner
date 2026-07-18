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
    PresetAnalysisError,
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
    render_work_prompt_template,
    select_manual_work_candidates,
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
    MANUAL_PRESET_WORK_PRIORITY,
    MAX_JOB_PROGRESS_LOG_LINES,
    PRESET_WORK_PRIORITY_OPTIONS,
    AppRuntimeEvent,
    ImportedPromptSessionRegistration,
    ImportedPromptSessionsResult,
    PersistenceIssueEvent,
    PresetAnalysisJobSubmittedEvent,
    PresetAnalysisJobSubmissionFailedEvent,
    PresetCandidateJobsRegisteredEvent,
    PresetManualCandidateSelectionClearedEvent,
    PresetManualCandidateSelectionContinuedEvent,
    PresetManualCandidateSelectionRequiredEvent,
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
    _PresetManualSelectionContext,
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

from .runtime_preset_manual_selection import AppRuntimePresetManualSelectionMixin

LOGGER = logging.getLogger("app.runtime")


def _runtime_global(name: str):
    return getattr(sys.modules["app.runtime"], name)


class AppRuntimePresetFlowMixin(AppRuntimePresetManualSelectionMixin):
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
    ) -> RuntimeActionEvent | None:
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

        if context.work_priority == MANUAL_PRESET_WORK_PRIORITY:
            return self._handle_completed_manual_preset_analysis_job(
                event,
                context,
                response_text,
            )

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

        job = self._register_preset_work_generation_job(
            source_job_id=event.job_id,
            workspace_tab_id=event.workspace_tab_id,
            parent_session_tab_id=event.session_tab_id,
            work_generation_prompt=result.work_generation_prompt,
            selected_candidates=result.selected_candidates,
            auto_commit_enabled=context.auto_commit_enabled,
            execution_options=context.execution_options,
            candidate_execution_options=context.resolved_candidate_execution_options(),
            queue_control_generation=context.queue_control_generation,
        )
        if job is not None:
            self._enqueue_dispatch_next_job_if_needed()
        return None


    def _register_preset_work_generation_job(
        self,
        *,
        source_job_id: str,
        workspace_tab_id: str,
        parent_session_tab_id: str,
        work_generation_prompt: str,
        selected_candidates: Sequence[PresetCandidate],
        auto_commit_enabled: bool,
        execution_options: AgentExecutionOptions,
        candidate_execution_options: AgentExecutionOptions,
        queue_control_generation: tuple[int, int],
    ) -> Job | None:
        try:
            with self._get_controller_state_lock():
                if not self._queue_start_is_current(
                    workspace_tab_id,
                    queue_control_generation,
                ):
                    LOGGER.info(
                        "Preset turn2 registration skipped because queue generation is stale. "
                        "turn1_job_id=%s workspace_tab_id=%s",
                        source_job_id,
                        workspace_tab_id,
                    )
                    return None
                if not self._session_tab_is_open(parent_session_tab_id):
                    LOGGER.info(
                        "Preset turn2 registration skipped because parent preset "
                        "session is closed. turn1_job_id=%s workspace_tab_id=%s "
                        "session_tab_id=%s",
                        source_job_id,
                        workspace_tab_id,
                        parent_session_tab_id,
                    )
                    return None
                with self._defer_controller_dispatch():
                    job = self._controller.submit_job(
                        parent_session_tab_id,
                        work_generation_prompt,
                        dispatch_immediately=False,
                        force_fresh_session=True,
                        execution_options=execution_options,
                    )
                    if not self._runtime_queue_mode_is_shared():
                        self._controller.prioritize_queued_jobs((job.job_id,))
                    self._get_preset_work_generation_job_contexts()[
                        job.job_id
                    ] = _PresetWorkGenerationJobContext(
                        parent_session_tab_id=parent_session_tab_id,
                        candidates=tuple(selected_candidates),
                        auto_commit_enabled=auto_commit_enabled,
                        execution_options=execution_options,
                        candidate_execution_options=candidate_execution_options,
                        queue_control_generation=queue_control_generation,
                    )
                    LOGGER.info(
                        "Preset turn2 registered. turn1_job_id=%s turn2_job_id=%s "
                        "workspace_tab_id=%s session_tab_id=%s candidate_count=%s "
                        "auto_commit_enabled=%s",
                        source_job_id,
                        job.job_id,
                        workspace_tab_id,
                        parent_session_tab_id,
                        len(selected_candidates),
                        auto_commit_enabled,
                    )
                    self._controller.start_queue(workspace_tab_id)
                self._sync_controller_events()
            self._mark_system_sleep_prevention_dirty()
            return job
        except Exception:
            LOGGER.exception(
                "Failed to register preset work-generation job. parent_job_id=%s",
                source_job_id,
            )
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=workspace_tab_id,
                message="프리셋 작업 프롬프트 생성 작업을 등록할 수 없습니다.",
                log_message=(
                    "Preset flow stopped because work-generation job registration failed. "
                    "parent_job_id=%s workspace_tab_id=%s"
                ),
                log_args=(source_job_id, workspace_tab_id),
            )
            return None


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
            if work_priority == MANUAL_PRESET_WORK_PRIORITY:
                return bool(candidates)
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
                key=_runtime_global("AppRuntime")._saved_workspace_sort_key,
            )
        )

    @staticmethod
    def _saved_workspace_sort_key(workspace: SavedWorkspace) -> tuple[float, str, str]:
        return (
            -_runtime_global("AppRuntime")._workspace_sort_timestamp(workspace),
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

