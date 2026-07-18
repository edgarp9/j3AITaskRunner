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


class AppRuntimePresetWorkGenerationMixin:
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
                    if not self._runtime_queue_mode_is_shared():
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

