"""Session exit hook orchestration for AppRuntime."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from domain import Job, JobStatus, SessionExitHookConfig
from domain.models import TabOpenState

from .controller import JobStatusChangedEvent
from .runtime import RuntimeActionEvent, _RuntimeActionRequest

LOGGER = logging.getLogger("app.runtime")

_ACTIVE_JOB_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.WAITING_FOR_CONFIGURATION,
    JobStatus.RUNNING,
)
_FINAL_JOB_STATUSES = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED)


@dataclass(slots=True, frozen=True)
class _SessionExitHookLaunchContext:
    session_tab_id: str
    workspace_tab_id: str
    workspace_path: str
    signature: tuple[str, ...]
    config: SessionExitHookConfig


class AppRuntimeSessionExitHookMixin:
    def _record_session_exit_hook_status_event(
        self,
        event: JobStatusChangedEvent,
    ) -> None:
        if event.current_status not in _ACTIVE_JOB_STATUSES:
            return

        signature = self._session_exit_hook_job_signature(event.session_tab_id)
        if not signature:
            return
        self._get_session_exit_hook_armed_signatures()[
            event.session_tab_id
        ] = signature

    def _evaluate_session_exit_hooks(self) -> None:
        armed_signatures = tuple(self._get_session_exit_hook_armed_signatures().items())
        if not armed_signatures:
            return

        pending_followup_workspaces = self._pending_preset_followup_workspace_tab_ids()
        for session_tab_id, signature in armed_signatures:
            context = self._session_exit_hook_launch_context(
                session_tab_id,
                signature,
                pending_followup_workspaces=pending_followup_workspaces,
                mark_evaluated=True,
            )
            if context is None:
                continue
            self._enqueue_session_exit_hook_launch(context)

    def _enqueue_session_exit_hook_launch(
        self,
        context: _SessionExitHookLaunchContext,
    ) -> None:
        self._enqueue_runtime_action(
            _RuntimeActionRequest(
                action=lambda target=context: (
                    self._launch_session_exit_hook_for_worker(target)
                ),
                failure_title="훅 오류",
                failure_message="세션 종료 훅을 실행할 수 없습니다.",
                log_message="Failed to run session exit hook action.",
            )
        )

    def _launch_session_exit_hook_for_worker(
        self,
        context: _SessionExitHookLaunchContext,
    ) -> RuntimeActionEvent | None:
        if getattr(self, "_runtime_action_shutdown_requested", False):
            return None

        latest_context = self._session_exit_hook_launch_context(
            context.session_tab_id,
            context.signature,
            pending_followup_workspaces=self._pending_preset_followup_workspace_tab_ids(),
            mark_evaluated=False,
        )
        if latest_context is None:
            return None

        runner = getattr(self, "_session_exit_hook_runner", None)
        if runner is None:
            LOGGER.warning(
                "Session exit hook runner is not configured. session_tab_id=%s",
                latest_context.session_tab_id,
            )
            return None

        try:
            runner(latest_context.config, latest_context.workspace_path)
        except Exception:
            LOGGER.exception(
                "Session exit hook runner failed. session_tab_id=%s workspace_tab_id=%s",
                latest_context.session_tab_id,
                latest_context.workspace_tab_id,
            )
        return None

    def _session_exit_hook_launch_context(
        self,
        session_tab_id: str,
        signature: tuple[str, ...],
        *,
        pending_followup_workspaces: set[str],
        mark_evaluated: bool,
    ) -> _SessionExitHookLaunchContext | None:
        with self._get_controller_state_lock():
            try:
                session_tab = self._controller.session_manager.get_session_tab(
                    session_tab_id
                )
                workspace_tab = self._controller.workspace_manager.get_workspace_tab(
                    session_tab.workspace_tab_id
                )
            except KeyError:
                self._clear_session_exit_hook_state(session_tab_id)
                return None

            if (
                session_tab.open_state != TabOpenState.OPEN
                or workspace_tab.open_state != TabOpenState.OPEN
            ):
                self._clear_session_exit_hook_state(session_tab_id)
                return None

            if session_tab.workspace_tab_id in pending_followup_workspaces:
                return None

            jobs = self._controller.scheduler.list_session_jobs(session_tab_id)
            if not jobs:
                self._clear_session_exit_hook_state(session_tab_id)
                return None

            current_signature = self._job_signature(jobs)
            if current_signature != signature:
                return None
            if any(job.status in _ACTIVE_JOB_STATUSES for job in jobs):
                return None
            if not all(job.status in _FINAL_JOB_STATUSES for job in jobs):
                return None

            evaluated_signatures = self._get_session_exit_hook_evaluated_signatures()
            if (
                mark_evaluated
                and evaluated_signatures.get(session_tab_id) == current_signature
            ):
                return None

            if mark_evaluated:
                evaluated_signatures[session_tab_id] = current_signature
                self._get_session_exit_hook_armed_signatures().pop(
                    session_tab_id,
                    None,
                )

            if not session_tab.exit_hook.is_runnable:
                return None

            return _SessionExitHookLaunchContext(
                session_tab_id=session_tab_id,
                workspace_tab_id=session_tab.workspace_tab_id,
                workspace_path=workspace_tab.workspace_path,
                signature=current_signature,
                config=session_tab.exit_hook,
            )

    def _session_exit_hook_job_signature(self, session_tab_id: str) -> tuple[str, ...]:
        scheduler = getattr(self._controller, "scheduler", None)
        if scheduler is None:
            return ()
        try:
            jobs = scheduler.list_session_jobs(session_tab_id)
        except KeyError:
            return ()
        return self._job_signature(jobs)

    @staticmethod
    def _job_signature(jobs: tuple[Job, ...]) -> tuple[str, ...]:
        return tuple(job.job_id for job in jobs)

    def _clear_session_exit_hook_state(self, session_tab_id: str) -> None:
        self._get_session_exit_hook_armed_signatures().pop(session_tab_id, None)
        self._get_session_exit_hook_evaluated_signatures().pop(session_tab_id, None)

    def _clear_session_exit_hook_state_for_workspace(
        self,
        workspace_tab_id: str,
    ) -> None:
        session_manager = getattr(self._controller, "session_manager", None)
        list_session_tabs = getattr(session_manager, "list_session_tabs", None)
        if list_session_tabs is None:
            return

        for session_tab in list_session_tabs(
            workspace_tab_id=workspace_tab_id,
            include_closed=True,
        ):
            self._clear_session_exit_hook_state(session_tab.session_tab_id)

    def _clear_session_exit_hook_runtime_state(self) -> None:
        self._get_session_exit_hook_armed_signatures().clear()
        self._get_session_exit_hook_evaluated_signatures().clear()

    def _get_session_exit_hook_armed_signatures(self) -> dict[str, tuple[str, ...]]:
        armed_signatures = getattr(self, "_session_exit_hook_armed_signatures", None)
        if armed_signatures is None:
            armed_signatures = {}
            self._session_exit_hook_armed_signatures = armed_signatures
        return armed_signatures

    def _get_session_exit_hook_evaluated_signatures(self) -> dict[str, tuple[str, ...]]:
        evaluated_signatures = getattr(
            self,
            "_session_exit_hook_evaluated_signatures",
            None,
        )
        if evaluated_signatures is None:
            evaluated_signatures = {}
            self._session_exit_hook_evaluated_signatures = evaluated_signatures
        return evaluated_signatures
