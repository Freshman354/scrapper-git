"""Runs the pipeline in a background thread so the HTTP request that
starts a scrape returns immediately. Calls the exact same job
functions the CLI does (discovery_job, crawl_candidate_job,
email_verification_job) — no new backend logic, just a different
caller.
"""

from __future__ import annotations

import threading
from typing import List

import scraper.extraction as extraction_module
from scraper.crawler import engine as crawler_module
from scraper.detection import platform as detection
from scraper.discovery.providers.url_import import UrlImportProvider
from scraper.domain.types import DiscoveryCriteria
from scraper.orchestration.jobs import crawl_candidate_job, discovery_job, email_verification_job
from scraper.processing import entity_resolution, normalization, validation
from scraper.storage.repository import PostgresRepository
from scraper.verification.verifier import DnsMxVerifier, NoOpSuppressionClient
from scraper.webapp import session_store

VERIFICATION_BATCH_LIMIT = 500  # generous enough to cover one scrape's worth of contacts


def run_scrape(session_id: str, urls: List[str]) -> None:
    try:
        repo = PostgresRepository()

        session_store.update_session(session_id, status="discovering")
        criteria = DiscoveryCriteria(urls=urls)
        new_ids = discovery_job(UrlImportProvider(), criteria, repo, lambda candidate_id: None)
        session_store.update_session(session_id, candidate_ids=new_ids, status="crawling")

        for candidate_id in new_ids:
            result = crawl_candidate_job(
                candidate_id, repo,
                crawler=crawler_module, extractor=extraction_module, detector=detection,
                normalizer=normalization, resolver=entity_resolution, validator=validation,
            )
            session_store.set_candidate_result(session_id, candidate_id, result)

        session_store.update_session(session_id, status="verifying")
        email_verification_job(VERIFICATION_BATCH_LIMIT, repo, NoOpSuppressionClient(), DnsMxVerifier())

        session_store.update_session(session_id, status="done")
    except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
        session_store.update_session(session_id, status="error", error=str(exc))


def start_scrape_background(session_id: str, urls: List[str]) -> None:
    thread = threading.Thread(target=run_scrape, args=(session_id, urls), daemon=True)
    thread.start()
