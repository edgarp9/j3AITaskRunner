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


class AppRuntimePresetApiMixin:
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
        if (
            normalized_priority == MANUAL_PRESET_WORK_PRIORITY
            and self._runtime_queue_mode_is_shared()
        ):
            raise ValueError(
                "manual 우선순위는 워크스페이스 개별큐에서만 사용할 수 있습니다."
            )
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

