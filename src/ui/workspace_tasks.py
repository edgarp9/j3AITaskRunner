"""Workspace task list display rules for the main window."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from tkinter import ttk

from domain import Job

from .formatters import (
    format_workspace_task_summary,
    job_progress_text,
    task_column_heading,
    truncate_prompt,
)

WORKSPACE_TASK_COLUMN_MIN_WIDTH = 1

WORKSPACE_TASK_COLUMNS = (
    ("order", "Order", 74, "center"),
    ("session", "Session", 70, "center"),
    ("progress", "Status", 150, "w"),
    ("prompt", "Prompt", 300, "w"),
)


class TextVariable(Protocol):
    def get(self) -> str:
        ...

    def set(self, value: str) -> None:
        ...


def workspace_task_column_ids() -> tuple[str, ...]:
    return tuple(
        column_id for column_id, _heading, _width, _anchor in WORKSPACE_TASK_COLUMNS
    )


def configure_workspace_task_tree_columns(
    jobs_tree: ttk.Treeview,
    *,
    language: str,
    initial_width: int,
) -> None:
    initial_column_widths = calculate_workspace_task_column_widths(initial_width)
    for (column_id, heading, _base_width, anchor), width in zip(
        WORKSPACE_TASK_COLUMNS,
        initial_column_widths,
    ):
        jobs_tree.heading(
            column_id,
            text=task_column_heading(column_id, language, heading),
        )
        jobs_tree.column(
            column_id,
            width=width,
            minwidth=WORKSPACE_TASK_COLUMN_MIN_WIDTH,
            anchor=anchor,
            stretch=False,
        )


def calculate_workspace_task_column_widths(available_width: int) -> tuple[int, ...]:
    base_widths = tuple(
        width for _column_id, _heading, width, _anchor in WORKSPACE_TASK_COLUMNS
    )
    if available_width <= 0:
        return tuple(
            max(WORKSPACE_TASK_COLUMN_MIN_WIDTH, width) for width in base_widths
        )

    total_base_width = sum(base_widths)
    raw_widths = [width * available_width / total_base_width for width in base_widths]
    widths = [max(WORKSPACE_TASK_COLUMN_MIN_WIDTH, int(width)) for width in raw_widths]

    remaining_width = available_width - sum(widths)
    if remaining_width > 0:
        remainder_order = sorted(
            range(len(raw_widths)),
            key=lambda index: (
                raw_widths[index] - int(raw_widths[index]),
                base_widths[index],
            ),
            reverse=True,
        )
        for index in remainder_order:
            if remaining_width == 0:
                break
            widths[index] += 1
            remaining_width -= 1
    elif remaining_width < 0:
        shrink_order = sorted(
            range(len(widths)),
            key=lambda index: (widths[index], base_widths[index]),
            reverse=True,
        )
        while remaining_width < 0:
            changed = False
            for index in shrink_order:
                if remaining_width == 0:
                    break
                if widths[index] <= WORKSPACE_TASK_COLUMN_MIN_WIDTH:
                    continue
                widths[index] -= 1
                remaining_width += 1
                changed = True
            if not changed:
                break

    return tuple(widths)


def resize_workspace_task_columns(
    jobs_tree: ttk.Treeview,
    available_width: int,
) -> None:
    if available_width <= 1:
        return

    widths = calculate_workspace_task_column_widths(available_width)
    for (column_id, _heading, _base_width, _anchor), width in zip(
        WORKSPACE_TASK_COLUMNS,
        widths,
    ):
        jobs_tree.column(column_id, width=width)


def sync_workspace_task_list(
    jobs_tree: ttk.Treeview,
    summary_var: TextVariable,
    jobs: tuple[Job, ...],
    *,
    language: str,
    job_session_label: Callable[[Job], str],
    preferred_job_id: str | None = None,
) -> None:
    summary = format_workspace_task_summary(jobs, language=language)
    if summary_var.get() != summary:
        summary_var.set(summary)

    current_order = list(jobs_tree.get_children())
    existing_job_ids = set(current_order)
    desired_job_ids = tuple(job.job_id for job in jobs)
    desired_job_id_set = set(desired_job_ids)
    current_selection = jobs_tree.selection()
    selected_job_id = (
        preferred_job_id if preferred_job_id in desired_job_id_set else None
    )
    if (
        selected_job_id is None
        and current_selection
        and current_selection[0] in desired_job_id_set
    ):
        selected_job_id = current_selection[0]

    stale_job_ids = existing_job_ids - desired_job_id_set
    for stale_job_id in stale_job_ids:
        jobs_tree.delete(stale_job_id)
    if stale_job_ids:
        current_order = [
            job_id for job_id in current_order if job_id not in stale_job_ids
        ]

    for index, job in enumerate(jobs):
        values = (
            str(job.queue_order) if job.queue_order is not None else "-",
            job_session_label(job),
            job_progress_text(job, language=language),
            truncate_prompt(job.prompt, width=60),
        )
        if jobs_tree.exists(job.job_id):
            if tuple(jobs_tree.item(job.job_id, "values")) != values:
                jobs_tree.item(job.job_id, values=values)
            if index >= len(current_order) or current_order[index] != job.job_id:
                jobs_tree.move(job.job_id, "", index)
                current_order.remove(job.job_id)
                current_order.insert(index, job.job_id)
        else:
            jobs_tree.insert("", index, iid=job.job_id, values=values)
            current_order.insert(index, job.job_id)

    if selected_job_id is None:
        jobs_tree.selection_remove(jobs_tree.selection())
        return

    jobs_tree.selection_set(selected_job_id)
    jobs_tree.focus(selected_job_id)
