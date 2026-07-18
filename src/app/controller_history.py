"""Session history helpers for AppController."""

from __future__ import annotations

import logging

from domain import Job, JobStatus
from infra.process_runner import AgentRunResult

LOGGER = logging.getLogger("app.controller")


class AppControllerHistoryMixin:
    def _sync_session_turn_for_status_change(
        self,
        previous_status: JobStatus | None,
        job: Job,
    ) -> None:
        if job.status == JobStatus.RUNNING and previous_status != JobStatus.RUNNING:
            self._record_started_turn(job)
            return

        if (
            job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED)
            and previous_status == JobStatus.RUNNING
        ):
            self._finalize_session_turn(job)

    def _record_started_turn(self, job: Job) -> None:
        if job.started_at is None:
            LOGGER.warning("Running job is missing started_at. job_id=%s", job.job_id)
            return

        try:
            self.session_manager.record_started_turn(
                job.session_tab_id,
                job_id=job.job_id,
                prompt_text=job.prompt,
                started_at=job.started_at,
                last_activity_at=job.started_at,
            )
        except Exception:
            LOGGER.exception("Failed to record started session turn. job_id=%s", job.job_id)

    def _finalize_session_turn(self, job: Job) -> None:
        if job.completed_at is None:
            LOGGER.warning("Finished job is missing completed_at. job_id=%s", job.job_id)
            return

        try:
            self.session_manager.finalize_turn(
                job.session_tab_id,
                job_id=job.job_id,
                completed_at=job.completed_at,
                last_activity_at=job.completed_at,
            )
        except Exception:
            LOGGER.exception("Failed to finalize session turn. job_id=%s", job.job_id)

    def _record_failed_session_turn_error(
        self,
        job: Job,
        result: AgentRunResult,
    ) -> None:
        if job.status != JobStatus.FAILED or job.completed_at is None:
            return

        error_text = _history_error_text_from_result(result)
        if error_text is None:
            return

        try:
            self.session_manager.finalize_turn(
                job.session_tab_id,
                job_id=job.job_id,
                completed_at=job.completed_at,
                last_activity_at=job.completed_at,
                error_text=error_text,
            )
        except Exception:
            LOGGER.exception(
                "Failed to record failed session turn error. job_id=%s",
                job.job_id,
            )


def _history_error_text_from_result(result: AgentRunResult) -> str | None:
    failure_reason = (result.failure_reason or "").strip()
    if failure_reason:
        return failure_reason

    for event in (
        *result.parser_summary.failure_events,
        *result.parser_summary.error_events,
    ):
        message = (event.message or "").strip()
        if message:
            return message
        raw_line = (event.raw_line or "").strip()
        if raw_line:
            return raw_line

    return None
