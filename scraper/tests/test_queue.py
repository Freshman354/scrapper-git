"""Tests for scraper/orchestration/queue.py.

Runs against a real local Redis instance (not fakeredis) to prove RQ's
job_id dedup actually behaves as scraper_job_state_definitions.md
requires — same "real infrastructure over mocks" standard as the rest
of this project.

These test the enqueue mechanics: whether a job lands in the queue, and
whether a duplicate job_id is a no-op. They don't run a worker, so they
don't exercise the entrypoint functions' bodies (several of those
intentionally raise NotImplementedError today — see jobs.py).
"""

import pytest
from redis import Redis
from rq import Queue
from rq.registry import StartedJobRegistry

from scraper.domain.types import DiscoveryCriteria
from scraper.orchestration import queue as queue_module

TEST_REDIS_URL = "redis://localhost:6379/15"  # dedicated DB index, kept separate from any default use


@pytest.fixture
def rq_queue():
    conn = Redis.from_url(TEST_REDIS_URL)
    conn.flushdb()
    q = Queue(queue_module.DEFAULT_QUEUE_NAME, connection=conn)
    yield q
    conn.flushdb()


def _queued_job_ids(q):
    return list(q.job_ids)


# --- get_redis_connection / get_queue -------------------------------------------

def test_get_redis_connection_raises_when_env_unset(monkeypatch):
    monkeypatch.delenv("SCRAPER_REDIS_URL", raising=False)
    with pytest.raises(RuntimeError):
        queue_module.get_redis_connection()


def test_get_redis_connection_uses_explicit_url_override():
    conn = queue_module.get_redis_connection(redis_url=TEST_REDIS_URL)
    assert conn.ping() is True


# --- enqueue_crawl_candidate: job_id dedup ---------------------------------------

def test_enqueue_crawl_candidate_uses_deterministic_job_id(rq_queue):
    queue_module.enqueue_crawl_candidate(42, queue=rq_queue)
    assert "crawl_candidate-42" in _queued_job_ids(rq_queue)


def test_enqueue_crawl_candidate_twice_is_a_noop(rq_queue):
    queue_module.enqueue_crawl_candidate(42, queue=rq_queue)
    queue_module.enqueue_crawl_candidate(42, queue=rq_queue)
    ids = _queued_job_ids(rq_queue)
    assert ids.count("crawl_candidate-42") == 1
    assert len(ids) == 1


def test_enqueue_crawl_candidate_different_ids_both_queue(rq_queue):
    queue_module.enqueue_crawl_candidate(1, queue=rq_queue)
    queue_module.enqueue_crawl_candidate(2, queue=rq_queue)
    ids = _queued_job_ids(rq_queue)
    assert set(ids) == {"crawl_candidate-1", "crawl_candidate-2"}


def test_enqueue_crawl_candidate_reenqueues_after_previous_job_finished(rq_queue):
    """The dedup check must only block re-enqueue for a job that's still
    queued/running — a finished job with the same id (e.g. a candidate's
    prior crawl attempt, later picked up again by the retry sweep) must
    NOT be permanently blocked from being enqueued again."""
    job = queue_module.enqueue_crawl_candidate(7, queue=rq_queue)
    job.set_status(queue_module.JobStatus.FINISHED)
    rq_queue.connection.lrem(rq_queue.key, 0, job.id)  # a worker would have popped it off the waiting list

    queue_module.enqueue_crawl_candidate(7, queue=rq_queue)
    assert _queued_job_ids(rq_queue).count("crawl_candidate-7") == 1


# --- enqueue_discovery: job_id keyed on provider + criteria hash ------------------

def test_enqueue_discovery_same_criteria_dedupes(rq_queue):
    criteria = DiscoveryCriteria(location="Texas", category="Clothing")
    queue_module.enqueue_discovery("search", criteria, queue=rq_queue)
    queue_module.enqueue_discovery("search", criteria, queue=rq_queue)
    assert len(_queued_job_ids(rq_queue)) == 1


def test_enqueue_discovery_different_criteria_both_queue(rq_queue):
    queue_module.enqueue_discovery("search", DiscoveryCriteria(location="Texas"), queue=rq_queue)
    queue_module.enqueue_discovery("search", DiscoveryCriteria(location="Florida"), queue=rq_queue)
    assert len(_queued_job_ids(rq_queue)) == 2


def test_enqueue_discovery_different_provider_both_queue_even_with_same_criteria(rq_queue):
    criteria = DiscoveryCriteria(location="Texas")
    queue_module.enqueue_discovery("search", criteria, queue=rq_queue)
    queue_module.enqueue_discovery("directories", criteria, queue=rq_queue)
    assert len(_queued_job_ids(rq_queue)) == 2


# --- enqueue_email_verification_batch / enqueue_retry_sweep: fixed job_id ----------

def test_enqueue_email_verification_batch_dedupes(rq_queue):
    queue_module.enqueue_email_verification_batch(queue=rq_queue)
    queue_module.enqueue_email_verification_batch(queue=rq_queue)
    assert _queued_job_ids(rq_queue) == ["email_verification_batch"]


def test_enqueue_retry_sweep_dedupes(rq_queue):
    queue_module.enqueue_retry_sweep(queue=rq_queue)
    queue_module.enqueue_retry_sweep(queue=rq_queue)
    assert _queued_job_ids(rq_queue) == ["requeue_retry_candidates"]


# --- enqueue_export: no job_id, both calls queue (not harmful to run twice) -------

def test_enqueue_export_has_no_dedup_by_design(rq_queue):
    from scraper.domain.types import ExportFilters
    queue_module.enqueue_export(ExportFilters(), "csv", queue=rq_queue)
    queue_module.enqueue_export(ExportFilters(), "csv", queue=rq_queue)
    assert len(_queued_job_ids(rq_queue)) == 2
