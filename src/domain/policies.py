"""Pure domain policies for j3AITaskRunner."""

from __future__ import annotations

import ntpath
import posixpath
import sys
from dataclasses import replace
from datetime import datetime
from typing import Iterable, Sequence

from .models import (
    Job,
    JobStatus,
    SessionTab,
    SessionTabId,
    SessionTabKind,
    TabNameState,
    TabOpenState,
    WorkspacePath,
)

SESSION_NAME_PREFIX = "S"
PRESET_SESSION_NAME_PREFIX = "P"

_VALID_JOB_STATUS_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {
        JobStatus.QUEUED,
        JobStatus.WAITING_FOR_CONFIGURATION,
        JobStatus.RUNNING,
    },
    JobStatus.WAITING_FOR_CONFIGURATION: {
        JobStatus.QUEUED,
        JobStatus.RUNNING,
    },
    JobStatus.RUNNING: {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELED,
    },
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELED: set(),
}
_PENDING_JOB_GROUP_STATUSES = frozenset(
    {
        JobStatus.QUEUED,
        JobStatus.WAITING_FOR_CONFIGURATION,
    }
)


def canonicalize_workspace_path(path: WorkspacePath) -> str:
    """Normalize a workspace path string for duplicate detection."""
    stripped_path = path.strip()
    if not stripped_path:
        return ""

    if _looks_like_windows_path(stripped_path):
        return ntpath.normcase(ntpath.normpath(stripped_path))

    return posixpath.normpath(stripped_path)


def is_duplicate_workspace_path(
    existing_paths: Iterable[WorkspacePath],
    candidate_path: WorkspacePath,
) -> bool:
    """Return True when candidate_path matches an existing workspace path."""
    normalized_candidate = canonicalize_workspace_path(candidate_path)
    return any(
        canonicalize_workspace_path(existing_path) == normalized_candidate
        for existing_path in existing_paths
    )


def workspace_folder_display_name(workspace_path: WorkspacePath) -> str:
    """Return the final folder name used for a workspace tab label."""
    stripped_path = workspace_path.strip()
    if not stripped_path:
        return ""

    if _looks_like_windows_path(stripped_path):
        normalized_path = ntpath.normpath(stripped_path)
        return ntpath.basename(normalized_path) or normalized_path

    normalized_path = posixpath.normpath(stripped_path)
    return posixpath.basename(normalized_path) or normalized_path


def reset_session_name_counter_if_all_closed(
    state: TabNameState,
    workspace_tab_id: str,
    session_tabs: Sequence[SessionTab],
) -> TabNameState:
    """Reset session tab numbering for a workspace when no session tab remains open."""
    if any(
        tab.workspace_tab_id == workspace_tab_id and tab.open_state == TabOpenState.OPEN
        for tab in session_tabs
    ):
        return state

    next_session_numbers = dict(state.next_session_numbers)
    next_session_numbers[workspace_tab_id] = 1
    return replace(state, next_session_numbers=next_session_numbers)


def issue_session_tab_name(
    state: TabNameState,
    workspace_tab_id: str,
    session_tabs: Sequence[SessionTab],
    *,
    kind: SessionTabKind = SessionTabKind.NORMAL,
) -> tuple[str, TabNameState]:
    """Issue the next default session tab name inside one workspace tab."""
    if kind == SessionTabKind.NORMAL:
        prefix = SESSION_NAME_PREFIX
    elif kind == SessionTabKind.PRESET:
        prefix = PRESET_SESSION_NAME_PREFIX
    else:
        raise ValueError(f"Unsupported numbered session tab kind: {kind}")

    normalized_state = reset_session_name_counter_if_all_closed(
        state=state,
        workspace_tab_id=workspace_tab_id,
        session_tabs=session_tabs,
    )
    next_session_number = normalized_state.next_session_numbers.get(workspace_tab_id, 1)
    name = f"{prefix}{next_session_number}"

    next_session_numbers = dict(normalized_state.next_session_numbers)
    next_session_numbers[workspace_tab_id] = next_session_number + 1
    next_state = replace(normalized_state, next_session_numbers=next_session_numbers)
    return name, next_state


def issue_preset_candidate_session_tab_name(
    parent_session_tab: SessionTab,
    session_tabs: Sequence[SessionTab],
) -> tuple[str, int]:
    """Issue the next candidate tab name for one preset parent session tab."""
    if parent_session_tab.kind != SessionTabKind.PRESET:
        raise ValueError("Preset candidate session tabs require a preset parent tab.")

    next_candidate_index = _next_preset_candidate_index(
        parent_session_tab=parent_session_tab,
        session_tabs=session_tabs,
    )
    return f"{parent_session_tab.display_name}-{next_candidate_index}", next_candidate_index


def is_valid_job_status_transition(current_status: JobStatus, next_status: JobStatus) -> bool:
    """Validate whether a job status transition is allowed by the domain rules."""
    return next_status in _VALID_JOB_STATUS_TRANSITIONS[current_status]


def is_execution_control_limit_enabled(value: int) -> bool:
    """Return whether an external execution control limit should be enforced."""
    return value > 0


def select_next_runnable_job(
    jobs: Iterable[Job],
    previous_job: Job | None = None,
) -> Job | None:
    """Select the next runnable job by queue order."""
    del previous_job

    return min(
        (job for job in jobs if job.status == JobStatus.QUEUED),
        key=_job_priority_key,
        default=None,
    )


def order_pending_jobs_by_queue_order(jobs: Sequence[Job]) -> tuple[Job, ...]:
    """Return pending jobs in queue order without session grouping."""
    pending_jobs = [
        job for job in jobs if job.status in _PENDING_JOB_GROUP_STATUSES
    ]
    return tuple(sorted(pending_jobs, key=_job_priority_key))


def order_pending_jobs_by_session_group(
    jobs: Sequence[Job],
    *,
    preferred_session_tab_id: SessionTabId | None = None,
) -> tuple[Job, ...]:
    """Return pending jobs in queue order.

    Kept as a compatibility wrapper for older callers; session grouping is no
    longer part of the default queue policy.
    """
    del preferred_session_tab_id

    return order_pending_jobs_by_queue_order(jobs)


def _looks_like_windows_path(path: str) -> bool:
    return "\\" in path or bool(ntpath.splitdrive(path)[0])


def _next_preset_candidate_index(
    *,
    parent_session_tab: SessionTab,
    session_tabs: Sequence[SessionTab],
) -> int:
    candidate_indexes = [
        tab.candidate_index
        for tab in session_tabs
        if tab.parent_session_tab_id == parent_session_tab.session_tab_id
        and tab.candidate_index is not None
    ]
    if not candidate_indexes:
        return 1
    return max(candidate_indexes) + 1


def _job_priority_key(job: Job) -> tuple[int, datetime, str]:
    queue_order = job.queue_order if job.queue_order is not None else sys.maxsize
    return (queue_order, job.created_at, job.job_id)
