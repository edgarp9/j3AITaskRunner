"""Manual preset candidate selection flow for AppRuntime."""

from __future__ import annotations

from collections.abc import Sequence
import logging

from domain import (
    PresetAnalysisError,
    extract_candidates,
    render_work_prompt_template,
    select_manual_work_candidates,
)

from .controller import JobExecutionResultCapturedEvent
from .runtime import (
    PresetManualCandidateSelectionClearedEvent,
    PresetManualCandidateSelectionContinuedEvent,
    PresetManualCandidateSelectionRequiredEvent,
    RuntimeActionFailedEvent,
    RuntimeActionWarningEvent,
    _PresetAnalysisJobContext,
    _PresetManualSelectionContext,
    _RuntimeActionRequest,
)
from .runtime_preset_work_generation import AppRuntimePresetWorkGenerationMixin

LOGGER = logging.getLogger("app.runtime")


class AppRuntimePresetManualSelectionMixin(AppRuntimePresetWorkGenerationMixin):
    def _handle_completed_manual_preset_analysis_job(
        self,
        event: JobExecutionResultCapturedEvent,
        context: _PresetAnalysisJobContext,
        response_text: str,
    ) -> PresetManualCandidateSelectionRequiredEvent | None:
        try:
            candidates = tuple(extract_candidates(response_text))
        except PresetAnalysisError as exc:
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=event.workspace_tab_id,
                message=str(exc),
                log_message=(
                    "Preset manual selection not started because turn1 response "
                    "could not be used. job_id=%s workspace_tab_id=%s "
                    "session_tab_id=%s issue=%s"
                ),
                log_args=(
                    event.job_id,
                    event.workspace_tab_id,
                    event.session_tab_id,
                    str(exc),
                ),
            )
            return None

        if not candidates:
            LOGGER.info(
                "Preset manual selection skipped because analysis returned no candidates. "
                "job_id=%s workspace_tab_id=%s session_tab_id=%s",
                event.job_id,
                event.workspace_tab_id,
                event.session_tab_id,
            )
            return None

        marked_pending = False
        try:
            with self._get_controller_state_lock():
                if not self._queue_start_is_current(
                    event.workspace_tab_id,
                    context.queue_control_generation,
                ):
                    LOGGER.info(
                        "Preset manual selection skipped because queue generation is stale. "
                        "turn1_job_id=%s workspace_tab_id=%s",
                        event.job_id,
                        event.workspace_tab_id,
                    )
                    return None
                if not self._session_tab_is_open(event.session_tab_id):
                    LOGGER.info(
                        "Preset manual selection skipped because parent preset session "
                        "is closed. turn1_job_id=%s workspace_tab_id=%s "
                        "session_tab_id=%s",
                        event.job_id,
                        event.workspace_tab_id,
                        event.session_tab_id,
                    )
                    return None

                manual_contexts = self._get_preset_manual_selection_contexts()
                previous_context = manual_contexts.pop(event.session_tab_id, None)
                if previous_context is not None:
                    self._clear_preset_followup_pending(
                        previous_context.workspace_tab_id
                    )
                manual_contexts[event.session_tab_id] = _PresetManualSelectionContext(
                    workspace_tab_id=event.workspace_tab_id,
                    parent_session_tab_id=event.session_tab_id,
                    language=context.language,
                    instruction=context.instruction,
                    work_prompt_template=context.work_prompt_template,
                    candidates=candidates,
                    auto_commit_enabled=context.auto_commit_enabled,
                    execution_options=context.execution_options,
                    candidate_execution_options=(
                        context.resolved_candidate_execution_options()
                    ),
                    queue_control_generation=context.queue_control_generation,
                )
                self._mark_preset_followup_pending(event.workspace_tab_id)
                marked_pending = True
        except Exception:
            if marked_pending:
                self._clear_preset_followup_pending(event.workspace_tab_id)
                self._get_preset_manual_selection_contexts().pop(
                    event.session_tab_id,
                    None,
                )
            LOGGER.exception(
                "Failed to create preset manual selection context. parent_job_id=%s",
                event.job_id,
            )
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=event.workspace_tab_id,
                message="프리셋 분석 결과를 처리하지 못했습니다.",
                log_message=(
                    "Preset flow stopped because manual selection context creation "
                    "failed. parent_job_id=%s workspace_tab_id=%s"
                ),
                log_args=(event.job_id, event.workspace_tab_id),
            )
            return None

        LOGGER.info(
            "Preset manual selection required. turn1_job_id=%s workspace_tab_id=%s "
            "session_tab_id=%s candidate_count=%s",
            event.job_id,
            event.workspace_tab_id,
            event.session_tab_id,
            len(candidates),
        )
        return PresetManualCandidateSelectionRequiredEvent(
            workspace_tab_id=event.workspace_tab_id,
            parent_session_tab_id=event.session_tab_id,
            candidates=candidates,
        )

    def continue_preset_manual_selection_in_background(
        self,
        parent_session_tab_id: str,
        selected_candidate_ids: Sequence[str],
    ) -> None:
        """Continue a manual preset flow from UI-selected candidate ids."""
        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=lambda: self._continue_preset_manual_selection_for_worker(
                    parent_session_tab_id,
                    selected_candidate_ids=tuple(selected_candidate_ids),
                ),
                failure_title="프리셋 작업 오류",
                failure_message="프리셋 후보 선택을 처리하지 못했습니다.",
                log_message="Failed to continue preset manual selection.",
            )
        )

    def _continue_preset_manual_selection_for_worker(
        self,
        parent_session_tab_id: str,
        *,
        selected_candidate_ids: tuple[str, ...],
    ) -> (
        PresetManualCandidateSelectionContinuedEvent
        | PresetManualCandidateSelectionClearedEvent
        | RuntimeActionWarningEvent
        | RuntimeActionFailedEvent
        | None
    ):
        if self._runtime_action_shutdown_requested:
            return None

        context = self._get_preset_manual_selection_contexts().get(
            parent_session_tab_id
        )
        if context is None:
            return RuntimeActionWarningEvent(
                title="프리셋 작업 경고",
                message="대기 중인 후보 선택이 없습니다.",
            )

        if self._runtime_queue_mode_is_shared():
            self._pop_preset_manual_selection_context(parent_session_tab_id)
            return RuntimeActionFailedEvent(
                title="입력 오류",
                message=(
                    "manual 우선순위는 워크스페이스 개별큐에서만 사용할 수 있습니다."
                ),
                workspace_tab_id=context.workspace_tab_id,
            )

        if not self._queue_start_is_current(
            context.workspace_tab_id,
            context.queue_control_generation,
        ):
            self._pop_preset_manual_selection_context(parent_session_tab_id)
            return PresetManualCandidateSelectionClearedEvent(
                workspace_tab_id=context.workspace_tab_id,
                parent_session_tab_id=parent_session_tab_id,
                message="manual 후보 선택 대기를 정리했습니다.",
            )

        if not self._session_tab_is_open(parent_session_tab_id):
            self._pop_preset_manual_selection_context(parent_session_tab_id)
            return PresetManualCandidateSelectionClearedEvent(
                workspace_tab_id=context.workspace_tab_id,
                parent_session_tab_id=parent_session_tab_id,
                message="manual 후보 선택 대기를 정리했습니다.",
            )

        selected_candidates = tuple(
            select_manual_work_candidates(
                list(context.candidates),
                [str(candidate_id) for candidate_id in selected_candidate_ids],
            )
        )
        if not selected_candidates:
            return RuntimeActionWarningEvent(
                title="프리셋 작업 경고",
                message="선택한 후보가 없습니다.",
                workspace_tab_id=context.workspace_tab_id,
            )

        try:
            work_generation_prompt = render_work_prompt_template(
                context.work_prompt_template,
                list(selected_candidates),
            )
        except PresetAnalysisError as exc:
            self._pop_preset_manual_selection_context(parent_session_tab_id)
            self._fail_preset_flow_and_stop_workspace(
                workspace_tab_id=context.workspace_tab_id,
                message=str(exc),
                log_message=(
                    "Preset manual selection could not render work-generation prompt. "
                    "workspace_tab_id=%s parent_session_tab_id=%s issue=%s"
                ),
                log_args=(
                    context.workspace_tab_id,
                    parent_session_tab_id,
                    str(exc),
                ),
            )
            return None

        job = self._register_preset_work_generation_job(
            source_job_id=f"{parent_session_tab_id}:manual",
            workspace_tab_id=context.workspace_tab_id,
            parent_session_tab_id=parent_session_tab_id,
            work_generation_prompt=work_generation_prompt,
            selected_candidates=selected_candidates,
            auto_commit_enabled=context.auto_commit_enabled,
            execution_options=context.execution_options,
            candidate_execution_options=context.resolved_candidate_execution_options(),
            queue_control_generation=context.queue_control_generation,
        )
        if job is None:
            self._pop_preset_manual_selection_context(parent_session_tab_id)
            return None

        self._pop_preset_manual_selection_context(parent_session_tab_id)
        self._enqueue_dispatch_next_job_if_needed()
        return PresetManualCandidateSelectionContinuedEvent(
            workspace_tab_id=context.workspace_tab_id,
            parent_session_tab_id=parent_session_tab_id,
            selected_candidate_ids=tuple(candidate.id for candidate in selected_candidates),
            work_generation_job_id=job.job_id,
        )

    def _pop_preset_manual_selection_context(
        self,
        parent_session_tab_id: str,
    ) -> _PresetManualSelectionContext | None:
        context = self._get_preset_manual_selection_contexts().pop(
            parent_session_tab_id,
            None,
        )
        if context is not None:
            self._clear_preset_followup_pending(context.workspace_tab_id)
        return context

    def _clear_preset_manual_selection_contexts(
        self,
        *,
        workspace_tab_id: str | None = None,
        parent_session_tab_id: str | None = None,
        message: str = "manual 후보 선택 대기를 정리했습니다.",
    ) -> tuple[PresetManualCandidateSelectionClearedEvent, ...]:
        contexts = self._get_preset_manual_selection_contexts()
        matched_session_ids = [
            session_tab_id
            for session_tab_id, context in contexts.items()
            if (
                (workspace_tab_id is None or context.workspace_tab_id == workspace_tab_id)
                and (
                    parent_session_tab_id is None
                    or context.parent_session_tab_id == parent_session_tab_id
                )
            )
        ]
        events: list[PresetManualCandidateSelectionClearedEvent] = []
        for session_tab_id in matched_session_ids:
            context = self._pop_preset_manual_selection_context(session_tab_id)
            if context is None:
                continue
            events.append(
                PresetManualCandidateSelectionClearedEvent(
                    workspace_tab_id=context.workspace_tab_id,
                    parent_session_tab_id=context.parent_session_tab_id,
                    message=message,
                )
            )
        return tuple(events)

    def _publish_preset_manual_selection_cleared_events(
        self,
        events: Sequence[PresetManualCandidateSelectionClearedEvent],
    ) -> None:
        for event in events:
            self._event_queue.put(event)
        if events:
            self._enqueue_dispatch_next_job_if_needed()

    def _get_preset_manual_selection_contexts(
        self,
    ) -> dict[str, _PresetManualSelectionContext]:
        contexts = getattr(self, "_preset_manual_selection_contexts", None)
        if contexts is None:
            contexts = {}
            self._preset_manual_selection_contexts = contexts
        return contexts
