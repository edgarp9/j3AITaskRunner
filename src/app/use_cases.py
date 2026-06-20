"""Application use cases for persistent data bootstrapping and saving."""

from __future__ import annotations

from collections.abc import Callable
import logging
from dataclasses import dataclass, field
from typing import Protocol, Sequence

from domain import (
    AppSettings,
    GeneratedWorkPrompt,
    Job,
    JobStatus,
    PresetAnalysisError,
    PresetCandidate,
    PresetPromptCountMismatchError,
    PresetResponseContractError,
    ProcessMetadata,
    QueueStopReason,
    SavedWorkspace,
    extract_candidates,
    extract_generated_work_prompts,
    render_work_prompt_template,
    select_work_candidates,
)
from infra.repository import PersistenceError
from infra.process_runner import AgentRunResult, AgentRunStatus

from .messages import (
    build_job_status_message,
    build_result_message,
    classify_timeout_result,
)
from .scheduler import Scheduler
from .session_manager import CompletedSessionSummary, SessionManager
from .workspace_manager import WorkspaceManager

LOGGER = logging.getLogger(__name__)


class PersistentDataRepository(Protocol):
    """Repository contract for persistent settings and workspace entries."""

    def load_settings(self) -> AppSettings: ...

    def save_settings(self, settings: AppSettings) -> None: ...

    def load_saved_workspaces(self) -> tuple[SavedWorkspace, ...]: ...

    def save_saved_workspaces(self, workspaces: Sequence[SavedWorkspace]) -> None: ...


@dataclass(slots=True, frozen=True)
class UseCaseIssue:
    """User-facing issue information that higher layers can present."""

    message: str
    operation: str
    severity: str = "error"


@dataclass(slots=True, frozen=True)
class PersistentDataSnapshot:
    """Persistent data loaded for a new app runtime."""

    settings: AppSettings = field(default_factory=AppSettings)
    saved_workspaces: tuple[SavedWorkspace, ...] = ()


@dataclass(slots=True, frozen=True)
class BootstrapLoadResult:
    """Outcome of loading persistent data during app startup."""

    snapshot: PersistentDataSnapshot
    issues: tuple[UseCaseIssue, ...] = ()

    @property
    def success(self) -> bool:
        return not self.issues


@dataclass(slots=True, frozen=True)
class SaveResult:
    """Outcome of a persistent save request."""

    issue: UseCaseIssue | None = None

    @property
    def success(self) -> bool:
        return self.issue is None


@dataclass(slots=True, frozen=True)
class SessionIdConfirmationResult:
    """Outcome of confirming a session id for one running job."""

    job: Job
    assigned_session_id: str | None


@dataclass(slots=True, frozen=True)
class ExecutionCompletionResult:
    """Outcome of applying a background execution result to runtime state."""

    job: Job
    assigned_session_id: str | None = None
    completed_session: CompletedSessionSummary | None = None
    ignored: bool = False


@dataclass(slots=True, frozen=True)
class PresetWorkGenerationPromptResult:
    """Outcome of turning an analysis response into a work-generation prompt."""

    selected_candidates: tuple[PresetCandidate, ...] = ()
    work_generation_prompt: str | None = None
    issue: UseCaseIssue | None = None

    @property
    def success(self) -> bool:
        return self.issue is None


@dataclass(slots=True, frozen=True)
class GeneratedPresetWorkPromptsResult:
    """Outcome of parsing generated work prompts for preset candidates."""

    prompts: tuple[GeneratedWorkPrompt, ...] = ()
    issue: UseCaseIssue | None = None

    @property
    def success(self) -> bool:
        return self.issue is None


def load_bootstrap_data(repository: PersistentDataRepository) -> BootstrapLoadResult:
    """Load settings and saved workspaces for a new runtime."""
    settings = AppSettings()
    workspaces: tuple[SavedWorkspace, ...] = ()
    issues: list[UseCaseIssue] = []

    try:
        settings = repository.load_settings()
    except PersistenceError:
        issues.append(
            UseCaseIssue(
                message="설정을 읽지 못해 기본값으로 시작합니다.",
                operation="load_settings",
            )
        )
    except Exception:
        LOGGER.exception("Unexpected error while loading app settings.")
        issues.append(
            UseCaseIssue(
                message="설정을 읽지 못해 기본값으로 시작합니다.",
                operation="load_settings",
            )
        )

    try:
        workspaces = repository.load_saved_workspaces()
    except PersistenceError:
        issues.append(
            UseCaseIssue(
                message="워크스페이스 목록을 읽지 못해 빈 목록으로 시작합니다.",
                operation="load_saved_workspaces",
            )
        )
    except Exception:
        LOGGER.exception("Unexpected error while loading saved workspaces.")
        issues.append(
            UseCaseIssue(
                message="워크스페이스 목록을 읽지 못해 빈 목록으로 시작합니다.",
                operation="load_saved_workspaces",
            )
        )

    return BootstrapLoadResult(
        snapshot=PersistentDataSnapshot(
            settings=settings,
            saved_workspaces=workspaces,
        ),
        issues=tuple(issues),
    )


def save_app_settings(
    repository: PersistentDataRepository,
    settings: AppSettings,
) -> SaveResult:
    """Save only persistent app settings."""
    return _run_save(
        operation="save_settings",
        message="설정을 저장하지 못했습니다.",
        save_action=lambda: repository.save_settings(settings),
    )


def save_saved_workspaces(
    repository: PersistentDataRepository,
    workspaces: Sequence[SavedWorkspace],
) -> SaveResult:
    """Save only the persistent saved workspace list."""
    return _run_save(
        operation="save_saved_workspaces",
        message="워크스페이스 목록을 저장하지 못했습니다.",
        save_action=lambda: repository.save_saved_workspaces(workspaces),
    )


def confirm_session_id_for_job(
    *,
    scheduler: Scheduler,
    session_manager: SessionManager,
    job_id: str,
    session_id: str,
) -> SessionIdConfirmationResult:
    """Assign a newly confirmed session id when the session tab does not have one yet."""
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise ValueError("session_id must not be blank.")

    job = scheduler.get_job(job_id)
    if job.force_fresh_session:
        return SessionIdConfirmationResult(job=job, assigned_session_id=None)

    session_tab = session_manager.get_session_tab(job.session_tab_id)
    if session_tab.session_id == normalized_session_id:
        return SessionIdConfirmationResult(job=job, assigned_session_id=None)

    if session_tab.session_id is not None and session_tab.session_id != normalized_session_id:
        LOGGER.warning(
            "Ignoring mismatched session id for job. job_id=%s existing=%s incoming=%s",
            job_id,
            session_tab.session_id,
            normalized_session_id,
        )
        return SessionIdConfirmationResult(job=job, assigned_session_id=None)

    try:
        session_manager.assign_session_id(job.session_tab_id, normalized_session_id)
    except ValueError:
        LOGGER.warning(
            "Ignoring conflicting session id for job. job_id=%s session_tab_id=%s incoming=%s",
            job_id,
            job.session_tab_id,
            normalized_session_id,
            exc_info=True,
        )
        return SessionIdConfirmationResult(job=job, assigned_session_id=None)
    return SessionIdConfirmationResult(job=job, assigned_session_id=normalized_session_id)


def prepare_preset_work_generation_prompt(
    *,
    analysis_response_text: str,
    work_prompt_template: str,
    work_priority: str,
) -> PresetWorkGenerationPromptResult:
    """Build the prompt that asks the configured AI runner for candidate work prompts."""
    try:
        candidates = extract_candidates(analysis_response_text)
        selected_candidates = select_work_candidates(candidates, work_priority)
        if not selected_candidates:
            return PresetWorkGenerationPromptResult()
        return PresetWorkGenerationPromptResult(
            selected_candidates=tuple(selected_candidates),
            work_generation_prompt=render_work_prompt_template(
                work_prompt_template,
                selected_candidates,
            ),
        )
    except PresetResponseContractError as exc:
        LOGGER.exception(
            "Preset analysis response violated the expected data contract. response_text=%r",
            analysis_response_text,
        )
        return PresetWorkGenerationPromptResult(
            issue=UseCaseIssue(
                message=str(exc),
                operation="prepare_preset_work_generation_prompt",
            )
        )
    except PresetAnalysisError as exc:
        LOGGER.exception(
            "Failed to parse preset analysis response. response_text=%r",
            analysis_response_text,
        )
        return PresetWorkGenerationPromptResult(
            issue=UseCaseIssue(
                message=str(exc),
                operation="prepare_preset_work_generation_prompt",
            )
        )
    except Exception:
        LOGGER.exception("Unexpected error while preparing preset work-generation prompt.")
        return PresetWorkGenerationPromptResult(
            issue=UseCaseIssue(
                message="프리셋 분석 응답을 처리하지 못했습니다.",
                operation="prepare_preset_work_generation_prompt",
            )
        )


def parse_preset_generated_work_prompts(
    *,
    generation_response_text: str,
    input_candidates: Sequence[PresetCandidate],
) -> GeneratedPresetWorkPromptsResult:
    """Parse generated prompts and keep the original candidate order."""
    try:
        prompts = extract_generated_work_prompts(
            generation_response_text,
            list(input_candidates),
        )
        return GeneratedPresetWorkPromptsResult(prompts=tuple(prompts))
    except PresetPromptCountMismatchError as exc:
        LOGGER.warning(
            "Preset work-prompt generation count mismatch. response_text=%r",
            generation_response_text,
        )
        return GeneratedPresetWorkPromptsResult(
            issue=UseCaseIssue(
                message=str(exc),
                operation="parse_preset_generated_work_prompts",
                severity="warning",
            )
        )
    except PresetResponseContractError as exc:
        LOGGER.exception(
            "Preset work-prompt generation response violated the expected data contract. "
            "response_text=%r",
            generation_response_text,
        )
        return GeneratedPresetWorkPromptsResult(
            issue=UseCaseIssue(
                message=str(exc),
                operation="parse_preset_generated_work_prompts",
            )
        )
    except PresetAnalysisError as exc:
        LOGGER.exception(
            "Failed to parse generated preset work prompts. response_text=%r",
            generation_response_text,
        )
        return GeneratedPresetWorkPromptsResult(
            issue=UseCaseIssue(
                message=str(exc),
                operation="parse_preset_generated_work_prompts",
            )
        )
    except Exception:
        LOGGER.exception("Unexpected error while parsing generated preset work prompts.")
        return GeneratedPresetWorkPromptsResult(
            issue=UseCaseIssue(
                message="작업 프롬프트 응답을 처리하지 못했습니다.",
                operation="parse_preset_generated_work_prompts",
            )
        )


def extract_text_import_prompts(input_text: str) -> tuple[str, ...]:
    """Extract non-empty prompts from Markdown ```text fenced blocks."""
    blocks: list[str] = []
    current_lines: list[str] = []
    in_text_block = False

    for line in input_text.splitlines():
        stripped_line = line.strip()
        if not in_text_block:
            if _is_text_fence_start(stripped_line):
                in_text_block = True
                current_lines = []
            continue

        if _is_fence_close(stripped_line):
            block_text = "\n".join(current_lines).strip()
            if block_text:
                blocks.append(block_text)
            in_text_block = False
            current_lines = []
            continue

        current_lines.append(line)

    if in_text_block:
        raise ValueError("닫히지 않은 ```text 코드 블록이 있습니다.")
    if not blocks:
        raise ValueError("가져올 ```text 코드 블록을 입력하세요.")
    return tuple(blocks)


def _is_text_fence_start(stripped_line: str) -> bool:
    return stripped_line == "```text" or stripped_line.startswith("```text ")


def _is_fence_close(stripped_line: str) -> bool:
    return stripped_line == "```"


def apply_execution_result(
    *,
    scheduler: Scheduler,
    session_manager: SessionManager,
    workspace_manager: WorkspaceManager,
    job_id: str,
    result: AgentRunResult,
) -> ExecutionCompletionResult:
    """Apply one background execution result to runtime job and session state."""
    assigned_session_id: str | None = None
    if result.session_id:
        confirmation = confirm_session_id_for_job(
            scheduler=scheduler,
            session_manager=session_manager,
            job_id=job_id,
            session_id=result.session_id,
        )
        assigned_session_id = confirmation.assigned_session_id

    job = scheduler.get_job(job_id)
    _log_execution_result(job_id=job_id, result=result)
    if job.status != JobStatus.RUNNING:
        return ExecutionCompletionResult(
            job=job,
            assigned_session_id=assigned_session_id,
            ignored=True,
        )

    final_timestamp = result.completed_at or job.completed_at or job.started_at
    process_metadata = ProcessMetadata(
        pid=job.process_metadata.pid if job.process_metadata is not None else None,
        exit_code=result.exit_code,
        launch_command=result.command,
    )
    stop_reason = scheduler.get_queue_state(job.workspace_tab_id).last_stop_reason

    if _is_running_tab_closed_stop_reason(stop_reason):
        canceled_job = scheduler.cancel_running_job(
            job_id,
            when=final_timestamp,
            process_metadata=process_metadata,
            cancel_execution=False,
            user_message=_build_canceled_job_message(stop_reason),
        )
        return ExecutionCompletionResult(
            job=canceled_job,
            assigned_session_id=assigned_session_id,
        )

    if classify_timeout_result(result) is not None:
        failed_job = scheduler.fail_running_job(
            job_id,
            when=final_timestamp,
            process_metadata=process_metadata,
            user_message=build_result_message(result),
        )
        return ExecutionCompletionResult(
            job=failed_job,
            assigned_session_id=assigned_session_id,
        )

    if result.status == AgentRunStatus.COMPLETED:
        completed_job = scheduler.complete_running_job(
            job_id,
            when=final_timestamp,
            process_metadata=process_metadata,
            user_message=build_result_message(result),
        )
        completed_session = _record_completed_session_if_possible(
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            job=completed_job,
            response_text=result.last_message,
        )
        return ExecutionCompletionResult(
            job=completed_job,
            assigned_session_id=assigned_session_id,
            completed_session=completed_session,
        )

    if result.status == AgentRunStatus.CANCELED:
        canceled_job = scheduler.cancel_running_job(
            job_id,
            when=final_timestamp,
            process_metadata=process_metadata,
            cancel_execution=False,
            user_message=_build_canceled_job_message(stop_reason),
        )
        return ExecutionCompletionResult(
            job=canceled_job,
            assigned_session_id=assigned_session_id,
        )

    failed_job = scheduler.fail_running_job(
        job_id,
        when=final_timestamp,
        process_metadata=process_metadata,
        user_message=build_result_message(result),
    )
    return ExecutionCompletionResult(
        job=failed_job,
        assigned_session_id=assigned_session_id,
    )


def _is_running_tab_closed_stop_reason(stop_reason: QueueStopReason | str | None) -> bool:
    return (
        stop_reason == QueueStopReason.RUNNING_TAB_CLOSED
        or stop_reason == QueueStopReason.RUNNING_TAB_CLOSED.value
    )


def _build_canceled_job_message(stop_reason: QueueStopReason | str | None) -> str:
    return (
        build_job_status_message(JobStatus.CANCELED, stop_reason=stop_reason)
        or build_job_status_message(JobStatus.CANCELED)
    )


def _run_save(
    *,
    operation: str,
    message: str,
    save_action: Callable[[], None],
) -> SaveResult:
    try:
        save_action()
    except PersistenceError:
        return SaveResult(issue=UseCaseIssue(message=message, operation=operation))
    except Exception:
        LOGGER.exception("Unexpected error during persistent save. operation=%s", operation)
        return SaveResult(issue=UseCaseIssue(message=message, operation=operation))

    return SaveResult()


def _record_completed_session_if_possible(
    *,
    session_manager: SessionManager,
    workspace_manager: WorkspaceManager,
    job: Job,
    response_text: str | None,
) -> CompletedSessionSummary | None:
    if response_text is None:
        LOGGER.warning("Completed job is missing last response. job_id=%s", job.job_id)
        return None

    if job.started_at is None or job.completed_at is None:
        LOGGER.warning("Completed job is missing timestamps. job_id=%s", job.job_id)
        return None

    session_tab = session_manager.get_session_tab(job.session_tab_id)
    if not session_tab.session_id:
        LOGGER.warning("Completed job is missing a confirmed session id. job_id=%s", job.job_id)
        return None

    workspace_tab = workspace_manager.get_workspace_tab(job.workspace_tab_id)
    session_manager.record_completed_turn(
        job.session_tab_id,
        job_id=job.job_id,
        prompt_text=job.prompt,
        response_text=response_text,
        started_at=job.started_at,
        completed_at=job.completed_at,
        last_activity_at=job.completed_at,
    )
    return session_manager.get_completed_session_summary(
        workspace_tab.workspace_path,
        session_tab.session_id,
    )


def _log_execution_result(*, job_id: str, result: AgentRunResult) -> None:
    artifact_paths = {
        "root_dir": str(result.artifacts.root_dir),
        "stdout_jsonl_path": str(result.artifacts.stdout_jsonl_path),
        "stderr_log_path": str(result.artifacts.stderr_log_path),
        "last_message_path": str(result.artifacts.last_message_path),
        "launch_metadata_path": str(result.artifacts.launch_metadata_path),
    }
    log_method = LOGGER.info if result.status == AgentRunStatus.COMPLETED else LOGGER.warning
    log_method(
        "Execution finished. job_id=%s status=%s exit_code=%s session_id=%s malformed_lines=%s "
        "failure_reason=%s artifacts=%s",
        job_id,
        result.status.value,
        result.exit_code,
        result.session_id,
        list(result.parser_summary.malformed_lines),
        result.failure_reason,
        artifact_paths,
    )
