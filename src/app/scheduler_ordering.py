"""Ordering helpers for scheduler job lists and dispatch priority."""

from __future__ import annotations

from datetime import datetime

from domain.models import Job, JobId


def job_dispatch_priority_key(job: Job) -> tuple[int, datetime, JobId]:
    queue_order = job.queue_order if job.queue_order is not None else 2**31 - 1
    return (queue_order, job.created_at, job.job_id)


def job_list_order_key(job: Job) -> tuple[float, datetime, JobId]:
    queue_order = job.queue_order if job.queue_order is not None else float("inf")
    return (queue_order, job.created_at, job.job_id)
