"""RQ enqueue/dequeue boundary.

The only module that constructs Redis/RQ connections. Job_id patterns
here match scraper_job_state_definitions.md exactly, since those are
what give discovery_job, crawl_candidate_job, and the retry sweep their
"same job queued twice is a no-op" guarantee (job doc rows #11 for each).

Reads SCRAPER_REDIS_URL only — same isolation pattern as
SCRAPER_DATABASE_URL for the migration runner and repository.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

from redis import Redis
from rq import Queue
from rq.job import Job, JobStatus

from scraper.domain.types import DiscoveryCriteria

ENV_VAR = "SCRAPER_REDIS_URL"
DEFAULT_QUEUE_NAME = "scraper"

_ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED)


def get_redis_connection(redis_url: Optional[str] = None) -> Redis:
    url = redis_url or os.environ.get(ENV_VAR)
    if not url:
        raise RuntimeError(
            f"{ENV_VAR} is not set. The queue module reads only this variable."
        )
    return Redis.from_url(url)


def get_queue(name: str = DEFAULT_QUEUE_NAME, redis_url: Optional[str] = None) -> Queue:
    return Queue(name, connection=get_redis_connection(redis_url))


def _enqueue_deduped(queue: Queue, func, *args, job_id: str, **kwargs) -> Job:
    """Passing job_id= to Queue.enqueue() does NOT by itself prevent a
    duplicate queue entry — RQ will happily push a second list entry for
    a job_id that's already queued (verified against real Redis, not
    assumed). The frozen contract's "same job queued twice is a no-op"
    requires checking first. A finished/failed job with this id is NOT
    treated as still active — that would permanently block re-enqueueing
    (e.g. the retry sweep re-enqueueing a candidate after a completed
    attempt), so only queued/started/deferred/scheduled counts as "already
    in flight"."""
    if Job.exists(job_id, connection=queue.connection):
        existing = Job.fetch(job_id, connection=queue.connection)
        if existing.get_status() in _ACTIVE_STATUSES:
            return existing
    return queue.enqueue(func, *args, job_id=job_id, **kwargs)


def _criteria_hash(criteria: DiscoveryCriteria) -> str:
    payload = json.dumps(
        {
            "location": criteria.location, "category": criteria.category,
            "platform_hint": criteria.platform_hint, "keywords": sorted(criteria.keywords),
            "urls": sorted(criteria.urls), "limit": criteria.limit,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def enqueue_discovery(provider_name: str, criteria: DiscoveryCriteria, *, queue: Optional[Queue] = None) -> Job:
    from scraper.orchestration.jobs import discovery_job_entrypoint

    q = queue or get_queue()
    job_id = f"discovery-{provider_name}-{_criteria_hash(criteria)}"
    return _enqueue_deduped(q, discovery_job_entrypoint, provider_name, criteria, job_id=job_id)


def enqueue_crawl_candidate(candidate_id: int, *, queue: Optional[Queue] = None) -> Job:
    from scraper.orchestration.jobs import crawl_candidate_job_entrypoint

    q = queue or get_queue()
    job_id = f"crawl_candidate-{candidate_id}"
    return _enqueue_deduped(q, crawl_candidate_job_entrypoint, candidate_id, job_id=job_id)


def enqueue_email_verification_batch(*, queue: Optional[Queue] = None) -> Job:
    from scraper.orchestration.jobs import email_verification_job_entrypoint

    q = queue or get_queue()
    return _enqueue_deduped(q, email_verification_job_entrypoint, job_id="email_verification_batch")


def enqueue_export(filters, fmt: str = "csv", *, queue: Optional[Queue] = None) -> Job:
    from scraper.orchestration.jobs import export_job_entrypoint

    q = queue or get_queue()
    return q.enqueue(export_job_entrypoint, filters, fmt)  # no job_id: not harmful to run twice, per the frozen contract


def enqueue_retry_sweep(*, queue: Optional[Queue] = None) -> Job:
    from scraper.orchestration.jobs import requeue_retry_candidates_entrypoint

    q = queue or get_queue()
    return _enqueue_deduped(q, requeue_retry_candidates_entrypoint, job_id="requeue_retry_candidates")
