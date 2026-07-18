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


class AppRuntimeQueueControlMixin:
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
            if self._runtime_queue_mode_is_shared():
                self._queue_control_global_generation += 1
                return
            key = self._queue_control_workspace_key(workspace_tab_id)
            self._queue_control_workspace_generations[key] = (
                self._queue_control_workspace_generations.get(key, 0) + 1
            )

    def _runtime_queue_mode_is_shared(self) -> bool:
        settings = getattr(self, "_settings", None)
        if settings is None:
            return False
        return settings.queue_mode == QUEUE_MODE_SHARED

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

