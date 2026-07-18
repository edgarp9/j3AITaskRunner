"""User-facing message helpers for runtime status and progress logs."""

from __future__ import annotations

import json
from typing import Any

from domain import JobStatus, QueueStopReason
from infra.process_runner import AgentRunResult, AgentRunStatus, AgentStreamEvent

_TIMEOUT_FAILURE_PREFIX = "시간 제한 초과"
_EXECUTION_TIMEOUT_MARKERS = ("전체 실행 제한",)
_INACTIVITY_TIMEOUT_MARKERS = ("출력 무활동 제한", "무활동")


def build_job_status_message(
    status: JobStatus,
    *,
    configuration_wait_reason: str | None = None,
    stop_reason: QueueStopReason | str | None = None,
) -> str | None:
    """Return a short Korean message suitable for the UI."""
    if status == JobStatus.QUEUED:
        return None

    if status == JobStatus.WAITING_FOR_CONFIGURATION:
        return configuration_wait_reason or "설정이 필요합니다."

    if status == JobStatus.RUNNING:
        return None

    if status == JobStatus.COMPLETED:
        return "작업 완료"

    if status == JobStatus.CANCELED:
        if stop_reason == QueueStopReason.RUNNING_TAB_CLOSED:
            return "탭 닫기로 취소했습니다."
        if stop_reason == QueueStopReason.PRESET_FLOW_FAILED:
            return "프리셋 오류로 중단했습니다."
        return "작업을 취소했습니다."

    if status == JobStatus.FAILED:
        return "실행 실패"

    return None


def build_retry_queued_message() -> str:
    """Return a short message for a retried waiting job."""
    return "설정 반영 후 다시 큐에 넣었습니다."


def build_launch_failure_message() -> str:
    """Return a short message for launch failures before the process starts."""
    return "실행기를 시작하지 못했습니다."


def build_internal_validation_failure_message() -> str:
    """Return a short message for unexpected validation errors."""
    return "실행 준비 중 오류가 발생했습니다."


def build_result_message(result: AgentRunResult) -> str:
    """Map one process result to a short user-facing message."""
    timeout_message = build_timeout_result_message(result)
    if timeout_message is not None:
        return timeout_message

    if result.status == AgentRunStatus.COMPLETED:
        return build_job_status_message(JobStatus.COMPLETED) or "작업 완료"

    if result.status == AgentRunStatus.CANCELED:
        return build_job_status_message(JobStatus.CANCELED) or "작업을 취소했습니다."

    failure_reason = (result.failure_reason or "").strip()
    if "turn.completed" in failure_reason:
        return "완료 신호를 받지 못했습니다."
    if "마지막 응답 파일" in failure_reason:
        return "결과 파일을 읽지 못했습니다."
    if "비정상 종료" in failure_reason:
        return "실행기가 비정상 종료되었습니다."
    if "프로세스를 시작하지 못했습니다" in failure_reason:
        return build_launch_failure_message()
    if failure_reason:
        return _with_reason("실행 실패:", failure_reason)
    return build_job_status_message(JobStatus.FAILED) or "실행 실패"


def build_timeout_result_message(result: AgentRunResult) -> str | None:
    """Return a distinct user message when the run ended because of a timeout."""
    timeout_kind = classify_timeout_result(result)
    if timeout_kind == "inactivity":
        return "진행 로그가 없어 실행을 중단했습니다."
    if timeout_kind == "execution":
        return "실행 시간이 초과되었습니다."
    return None


def classify_timeout_result(result: AgentRunResult) -> str | None:
    """Classify timeout failures without treating user cancellation as timeout."""
    if result.status == AgentRunStatus.CANCELED:
        return None
    return _classify_timeout_failure_reason(result.failure_reason)


def format_progress_event(event: AgentStreamEvent) -> str:
    """Return the full provider progress line for the UI log."""
    if event.raw_line is not None:
        raw_line = event.raw_line.rstrip("\r\n")
        if raw_line:
            return raw_line

    if event.payload:
        return _serialize_progress_payload(event.payload)

    if event.message is not None:
        return event.message

    return event.event_type


def _serialize_progress_payload(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(payload)


def _classify_timeout_failure_reason(failure_reason: str | None) -> str | None:
    normalized_reason = (failure_reason or "").strip()
    if _TIMEOUT_FAILURE_PREFIX not in normalized_reason:
        return None

    if any(marker in normalized_reason for marker in _INACTIVITY_TIMEOUT_MARKERS):
        return "inactivity"
    if any(marker in normalized_reason for marker in _EXECUTION_TIMEOUT_MARKERS):
        return "execution"
    return "execution"


def _with_reason(prefix: str, reason: str | None, *, max_length: int = 80) -> str:
    normalized_reason = (reason or "").strip()
    if not normalized_reason:
        return prefix

    if len(normalized_reason) > max_length:
        normalized_reason = f"{normalized_reason[: max_length - 1]}…"
    return f"{prefix} {normalized_reason}"
