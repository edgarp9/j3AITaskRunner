"""Presentation-only formatting helpers for Tkinter views."""

from __future__ import annotations

from collections.abc import Iterable

from domain import (
    AppSettings,
    Job,
    JobStatus,
    QueueStopReason,
    SUPPORTED_AGENT_PROVIDERS,
    normalize_agent_provider,
)
from domain.localization import normalize_ui_language

from .i18n import localize_runtime_message, text as ui_text

_DEFAULT_COMPLETED_JOB_MESSAGE = "작업 완료"
_FAILED_MESSAGE_PREFIXES = (
    "실행 실패:",
    "실행 실패",
    "Execution failed:",
    "Execution failed",
)
_AGENT_PROVIDER_LABELS = {
    "codex": "Codex CLI",
    "claude_code": "Claude Code",
    "kilo_code": "Kilo Code",
    "opencode": "OpenCode",
    "pi": "Pi Coding Agent",
}


def format_settings_summary(
    settings: AppSettings, *, language: str | None = None
) -> str:
    """Return compact settings text for the sidebar."""
    language = normalize_ui_language(language or settings.ui_language)
    provider_text = format_available_agent_provider_labels(settings, language=language)
    return ui_text(
        "settings_summary",
        language,
        providers=provider_text,
    )


def format_available_agent_provider_labels(
    settings: AppSettings, *, language: str | None = None
) -> str:
    """Return configured agent provider labels for the sidebar summary."""
    configured_providers = {
        normalize_agent_provider(provider_id)
        for provider_id, executable_path in settings.executable_paths.items()
        if isinstance(executable_path, str) and executable_path.strip()
    }
    if settings.executable_path:
        configured_providers.add(normalize_agent_provider(settings.agent_provider))

    provider_labels = [
        format_agent_provider_label(provider_id)
        for provider_id in SUPPORTED_AGENT_PROVIDERS
        if provider_id in configured_providers
    ]
    if not provider_labels:
        return ui_text("settings_agent_providers_empty", language)
    return " / ".join(provider_labels)


def format_agent_provider_label(agent_provider: str) -> str:
    """Return a compact display label for one agent provider."""
    normalized_provider = normalize_agent_provider(agent_provider)
    return _AGENT_PROVIDER_LABELS.get(normalized_provider, normalized_provider)


def truncate_prompt(prompt: str, *, width: int = 48) -> str:
    """Return one-line prompt preview text."""
    normalized = " ".join(prompt.split())
    if len(normalized) <= width:
        return normalized
    return f"{normalized[: width - 1]}…"


def context_menu_prompt_label(
    prompt: str, *, width: int = 72, language: str | None = None
) -> str:
    """Return the context-menu label for opening a prompt preview."""
    preview = truncate_prompt(prompt, width=width)
    if not preview:
        preview = ui_text("context_prompt_empty", language)
    return ui_text("context_prompt_prefix", language, preview=preview)


def task_column_heading(
    column_id: str, language: str | None, default_heading: str
) -> str:
    """Return localized tree heading text for one workspace job column."""
    return ui_text(f"task_column_{column_id}", language) or default_heading


def job_status_label(status: JobStatus, language: str | None = None) -> str:
    """Return localized status text for a job status enum."""
    return ui_text(f"job_status_{status.value}", language)


def completed_job_numbers_text(jobs: Iterable[Job]) -> str:
    """Return completed job sequence numbers for compact session activity text."""
    completed_numbers: list[str] = []
    for job in jobs:
        if job.status != JobStatus.COMPLETED:
            continue

        sequence_number = _job_sequence_number(job.job_id)
        if sequence_number is not None:
            completed_numbers.append(sequence_number)

    return ", ".join(completed_numbers)


def completed_activity_text(
    jobs: Iterable[Job], *, language: str | None = None
) -> str:
    """Return the session activity text for completed jobs."""
    completed_numbers = completed_job_numbers_text(jobs)
    if not completed_numbers:
        return ui_text("activity_completed_jobs_empty", language)
    return ui_text(
        "activity_completed_jobs",
        language,
        completed_job_ids=f"job-{completed_numbers}",
    )


def running_activity_text(
    running_job: Job,
    jobs: Iterable[Job],
    *,
    language: str | None = None,
) -> str:
    """Return the session activity text for a running job."""
    completed_numbers = completed_job_numbers_text(jobs)
    if completed_numbers:
        return ui_text(
            "activity_running_with_completed_jobs",
            language,
            job_id=running_job.job_id,
            completed_job_numbers=completed_numbers,
        )
    return ui_text("activity_running", language, job_id=running_job.job_id)


def pending_activity_text(
    pending_job: Job,
    jobs: Iterable[Job],
    *,
    language: str | None = None,
) -> str:
    """Return the session activity text for a queued job."""
    completed_numbers = completed_job_numbers_text(jobs)
    if completed_numbers:
        return ui_text(
            "activity_pending_with_completed_jobs",
            language,
            job_id=pending_job.job_id,
            completed_job_numbers=completed_numbers,
        )
    return ui_text("activity_pending", language, job_id=pending_job.job_id)


def failed_activity_text(
    failed_job: Job,
    jobs: Iterable[Job],
    message: str | None,
    *,
    language: str | None = None,
) -> str:
    """Return the session activity text for a failed job."""
    completed_numbers = completed_job_numbers_text(jobs)
    failure_message = _failed_activity_message(message, language=language)
    if completed_numbers and failure_message:
        return ui_text(
            "activity_failed_job_with_completed_jobs_and_message",
            language,
            job_id=failed_job.job_id,
            completed_job_numbers=completed_numbers,
            message=failure_message,
        )
    if completed_numbers:
        return ui_text(
            "activity_failed_job_with_completed_jobs",
            language,
            job_id=failed_job.job_id,
            completed_job_numbers=completed_numbers,
        )
    if failure_message:
        return ui_text(
            "activity_failed_job_with_message",
            language,
            job_id=failed_job.job_id,
            message=failure_message,
        )
    return ui_text("activity_failed_job", language, job_id=failed_job.job_id)


def finished_activity_text(
    focused_job: Job,
    jobs: Iterable[Job],
    message: str | None,
    *,
    language: str | None = None,
) -> str:
    """Return the session activity text for an idle session with jobs."""
    if focused_job.status == JobStatus.QUEUED:
        return pending_activity_text(
            focused_job,
            jobs,
            language=language,
        )

    if focused_job.status == JobStatus.FAILED:
        return failed_activity_text(
            focused_job,
            jobs,
            message,
            language=language,
        )

    activity_message = session_job_message_text(
        focused_job,
        message,
        language=language,
    )
    if activity_message:
        completed_numbers = completed_job_numbers_text(jobs)
        if completed_numbers:
            return ui_text(
                "activity_job_message_with_completed_jobs",
                language,
                job_id=focused_job.job_id,
                completed_job_numbers=completed_numbers,
                message=activity_message,
            )
        return ui_text(
            "activity_job_message",
            language,
            job_id=focused_job.job_id,
            message=activity_message,
        )

    return completed_activity_text(jobs, language=language)


def queue_stop_reason_label(
    reason: QueueStopReason | str, language: str | None = None
) -> str:
    """Return localized queue stop reason text."""
    if reason == QueueStopReason.USER_STOPPED or reason == QueueStopReason.USER_STOPPED.value:
        return ui_text("queue_stopped_user", language)
    if (
        reason == QueueStopReason.RUNNING_TAB_CLOSED
        or reason == QueueStopReason.RUNNING_TAB_CLOSED.value
    ):
        return ui_text("queue_stopped_tab_closed", language)
    if (
        reason == QueueStopReason.ALL_JOBS_COMPLETED
        or reason == QueueStopReason.ALL_JOBS_COMPLETED.value
    ):
        return ui_text("queue_stopped_all_done", language)
    return str(reason)


def job_progress_text(job: Job, *, language: str | None = None) -> str:
    """Return the workspace task-list progress text for one job."""
    if job.status == JobStatus.WAITING_FOR_CONFIGURATION:
        return localize_runtime_message(
            job.configuration_wait_reason
            or job_status_label(JobStatus.WAITING_FOR_CONFIGURATION, language),
            language,
        )
    if job.status == JobStatus.RUNNING:
        return job_status_label(JobStatus.RUNNING, language)
    if job.status == JobStatus.QUEUED:
        return ui_text("job_status_queued_progress", language)
    if job.status == JobStatus.COMPLETED and _is_default_completed_job_message(
        job.user_message
    ):
        return job_status_label(JobStatus.COMPLETED, language)
    return localize_runtime_message(
        job.user_message, language
    ) or job_status_label(job.status, language)


def session_job_message_text(
    job: Job, message: str | None, *, language: str | None = None
) -> str:
    """Return secondary session message text without duplicating completed status."""
    if (
        job.status == JobStatus.COMPLETED
        and _is_default_completed_job_message(message)
        and _job_sequence_number(job.job_id) is not None
    ):
        return ""
    return localize_runtime_message(message, language)


def format_workspace_task_summary(
    jobs: tuple[Job, ...], *, language: str | None = None
) -> str:
    """Return the workspace task-list summary text."""
    if not jobs:
        return ui_text("workspace_task_summary_empty", language)

    counts = {status: 0 for status in JobStatus}
    for job in jobs:
        counts[job.status] += 1

    return ui_text(
        "workspace_task_summary",
        language,
        total=len(jobs),
        completed=counts[JobStatus.COMPLETED],
        running=counts[JobStatus.RUNNING],
        queued=counts[JobStatus.QUEUED],
        waiting=counts[JobStatus.WAITING_FOR_CONFIGURATION],
        failed=counts[JobStatus.FAILED],
        canceled=counts[JobStatus.CANCELED],
    )


def _job_sequence_number(job_id: str) -> str | None:
    prefix = "job-"
    if not job_id.startswith(prefix):
        return None

    sequence_number = job_id[len(prefix) :]
    if not sequence_number.isdecimal():
        return None
    return sequence_number


def _is_default_completed_job_message(message: str | None) -> bool:
    return (message or "").strip() == _DEFAULT_COMPLETED_JOB_MESSAGE


def _failed_activity_message(
    message: str | None, *, language: str | None = None
) -> str:
    localized_message = localize_runtime_message(message, language).strip()
    if not localized_message:
        return ""

    for prefix in _FAILED_MESSAGE_PREFIXES:
        if localized_message == prefix:
            return ""
        if localized_message.startswith(prefix):
            return localized_message[len(prefix) :].strip()
    return localized_message
