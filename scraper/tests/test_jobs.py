"""Tests for scraper/orchestration/jobs.py.

The core job functions take their collaborators as parameters, so these
tests use small fakes for the modules that don't exist yet (discovery
providers, crawler, extraction, detection, normalization, entity
resolution, validation, verifier, suppression client, export writer)
while exercising the real PostgresRepository against a real Postgres
database — the same pattern as test_repository.py.
"""

from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from scraper.domain.types import (
    CandidateDiscovery,
    CrawlOutcome,
    DetectionResult,
    DiscoveryCriteria,
    DomainPolicySnapshot,
    EligibleContact,
    EntityResolutionResult,
    ExportFilters,
    ExtractedField,
    ExtractionResult,
    FetchedPage,
    NormalizedRecord,
    ValidatedRecord,
    VerificationResult,
)
from scraper.migrations.run_migrations import MIGRATIONS_DIR, run_migrations
from scraper.orchestration import jobs
from scraper.storage.repository import PostgresRepository

TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost/scraper_test"


# --- fixtures ------------------------------------------------------------------

@pytest.fixture
def repo():
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE;")
        cur.execute("CREATE SCHEMA public;")
    conn.close()
    run_migrations(migrations_dir=MIGRATIONS_DIR, database_url=TEST_DATABASE_URL)
    return PostgresRepository(database_url=TEST_DATABASE_URL)


def _fetch_one(sql, params=()):
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


# --- fakes -----------------------------------------------------------------------

class FakeDiscoveryProvider:
    def __init__(self, candidates):
        self._candidates = candidates
        self.calls = []

    def discover(self, criteria):
        self.calls.append(criteria)
        return self._candidates


class FakeCrawlEngine:
    def __init__(self, outcome=None, exc=None):
        self._outcome = outcome
        self._exc = exc
        self.requests = []

    def crawl(self, request):
        self.requests.append(request)
        if self._exc is not None:
            raise self._exc
        return self._outcome


class FakeExtractor:
    def __init__(self, result):
        self._result = result

    def extract(self, pages):
        return self._result


class FakeDetector:
    def __init__(self, result):
        self._result = result

    def detect(self, pages, structured_data):
        return self._result


class FakeNormalizer:
    def __init__(self, result):
        self._result = result

    def normalize(self, extraction, detection):
        return self._result


class FakeResolver:
    def __init__(self, result):
        self._result = result

    def resolve(self, record, candidate_matches):
        return self._result


class FakeValidator:
    def __init__(self, result_fn=None):
        self._result_fn = result_fn

    def validate(self, record, resolution):
        if self._result_fn:
            return self._result_fn(record, resolution)
        return ValidatedRecord(
            normalized_record=record, resolution=resolution,
            field_confidence={}, is_valid=True, validation_errors=[],
        )


class FakeSuppressionClient:
    def __init__(self, suppressed_emails=(), exc=None):
        self._suppressed = set(suppressed_emails)
        self._exc = exc
        self.checked = []

    def is_suppressed(self, email):
        self.checked.append(email)
        if self._exc is not None:
            raise self._exc
        return email in self._suppressed


class FakeVerifier:
    def __init__(self, results=None, exc=None):
        self._results = results or {}
        self._exc = exc
        self.calls = []

    def verify(self, email):
        self.calls.append(email)
        if self._exc is not None:
            raise self._exc
        return self._results.get(email, VerificationResult(email=email, status="verified", provider="fake", verified_at=datetime.now(timezone.utc)))


def _no_op_backoff(attempt_number):
    return timedelta(seconds=0)


# --- discovery_job ------------------------------------------------------------

def test_discovery_job_enqueues_only_new_candidates(repo):
    provider = FakeDiscoveryProvider([
        CandidateDiscovery("https://a.com/", "search", None, {}),
        CandidateDiscovery("https://b.com/", "search", None, {}),
    ])
    enqueued = []
    ids = jobs.discovery_job(provider, DiscoveryCriteria(), repo, enqueued.append)

    assert len(ids) == 2
    assert enqueued == ids


def test_discovery_job_skips_duplicates_on_rerun(repo):
    provider = FakeDiscoveryProvider([CandidateDiscovery("https://a.com/", "search", None, {})])
    enqueued = []
    jobs.discovery_job(provider, DiscoveryCriteria(), repo, enqueued.append)
    enqueued.clear()

    second_ids = jobs.discovery_job(provider, DiscoveryCriteria(), repo, enqueued.append)
    assert second_ids == []
    assert enqueued == []


# --- crawl_candidate_job --------------------------------------------------------

def _seed_candidate(repo, domain="example.com"):
    [cid] = repo.save_discovery_batch([CandidateDiscovery(f"https://{domain}/", "search", None, {})])
    return cid


def _success_collaborators(domain="example.com", email="hello@example.com"):
    page = FetchedPage(url=f"https://{domain}/", page_type="homepage", fetched_via="http",
                        http_status_code=200, content_hash="hash1", raw_html="<html></html>",
                        from_cache=False, fetched_at=datetime.now(timezone.utc))
    policy = DomainPolicySnapshot(domain, 0, 200, datetime.now(timezone.utc))
    outcome = CrawlOutcome(canonical_domain=domain, status="success", pages=[page],
                            used_browser=False, updated_domain_policy=policy, failure_reason=None)
    extraction = ExtractionResult(
        canonical_domain=domain, business_fields=[],
        contacts=[ExtractedField("email", email, f"https://{domain}/contact", "mailto", 0.9)],
        socials=[], products=[],
    )
    detection = DetectionResult(canonical_domain=domain, platform="shopify", confidence=0.9, evidence=[])
    normalized = NormalizedRecord(
        canonical_domain=domain, business_fields={"name": "Test Biz"}, platform="shopify",
        platform_confidence=0.9, contacts=extraction.contacts, socials=[],
    )
    resolution = EntityResolutionResult(action="create_new", target_business_id=None, match_score=None, match_level=None)

    return dict(
        crawler=FakeCrawlEngine(outcome=outcome),
        extractor=FakeExtractor(extraction),
        detector=FakeDetector(detection),
        normalizer=FakeNormalizer(normalized),
        resolver=FakeResolver(resolution),
        validator=FakeValidator(),
    )


def test_crawl_candidate_job_success_path(repo):
    cid = _seed_candidate(repo)
    collaborators = _success_collaborators()

    result = jobs.crawl_candidate_job(cid, repo, **collaborators, compute_candidate_backoff=_no_op_backoff)

    assert result == "success"
    row = _fetch_one("SELECT state, business_id FROM candidates WHERE id=%s;", (cid,))
    assert row[0] == "success"
    assert row[1] is not None


def test_crawl_candidate_job_skips_when_claim_fails(repo):
    cid = _seed_candidate(repo)
    repo.claim_candidate(cid)  # already claimed by "someone else"
    collaborators = _success_collaborators()

    result = jobs.crawl_candidate_job(cid, repo, **collaborators, compute_candidate_backoff=_no_op_backoff)

    assert result == "skipped"
    assert collaborators["crawler"].requests == []  # never even attempted


def test_crawl_candidate_job_retries_on_timeout(repo):
    cid = _seed_candidate(repo)
    collaborators = _success_collaborators()
    collaborators["crawler"] = FakeCrawlEngine(exc=jobs.CrawlTimeout("timed out"))

    result = jobs.crawl_candidate_job(cid, repo, **collaborators, compute_candidate_backoff=_no_op_backoff)

    assert result == "retry"
    row = _fetch_one("SELECT state, attempts, failure_reason FROM candidates WHERE id=%s;", (cid,))
    assert row[0] == "retry"
    assert row[1] == 1
    assert row[2] == "timed out"


def test_crawl_candidate_job_abandons_after_max_attempts(repo):
    cid = _seed_candidate(repo)
    # simulate 4 prior failed attempts
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE candidates SET attempts = 4 WHERE id=%s;", (cid,))
    conn.commit()
    conn.close()

    collaborators = _success_collaborators()
    collaborators["crawler"] = FakeCrawlEngine(exc=jobs.CrawlTimeout("still failing"))

    result = jobs.crawl_candidate_job(cid, repo, **collaborators, compute_candidate_backoff=_no_op_backoff)

    assert result == "abandoned"
    row = _fetch_one("SELECT state FROM candidates WHERE id=%s;", (cid,))
    assert row[0] == "abandoned"


def test_crawl_candidate_job_marks_blocked(repo):
    cid = _seed_candidate(repo)
    collaborators = _success_collaborators()
    collaborators["crawler"] = FakeCrawlEngine(exc=jobs.CrawlBlocked("403"))

    result = jobs.crawl_candidate_job(cid, repo, **collaborators, compute_candidate_backoff=_no_op_backoff)

    assert result == "blocked"
    row = _fetch_one("SELECT state, failure_reason FROM candidates WHERE id=%s;", (cid,))
    assert row == ("blocked", "403")


def test_crawl_candidate_job_skips_extraction_for_cached_pages(repo):
    cid = _seed_candidate(repo)
    collaborators = _success_collaborators()
    cached_page = FetchedPage(url="https://example.com/", page_type="homepage", fetched_via="http",
                               http_status_code=200, content_hash="hash1", raw_html=None,
                               from_cache=True, fetched_at=datetime.now(timezone.utc))
    policy = DomainPolicySnapshot("example.com", 0, 200, datetime.now(timezone.utc))
    collaborators["crawler"] = FakeCrawlEngine(outcome=CrawlOutcome(
        canonical_domain="example.com", status="success", pages=[cached_page],
        used_browser=False, updated_domain_policy=policy, failure_reason=None,
    ))

    captured = {}
    real_extract = collaborators["extractor"].extract
    def _capturing_extract(pages):
        captured["pages"] = pages
        return real_extract(pages)
    collaborators["extractor"].extract = _capturing_extract

    jobs.crawl_candidate_job(cid, repo, **collaborators, compute_candidate_backoff=_no_op_backoff)
    assert captured["pages"] == []  # the cached page was excluded


# --- email_verification_job -----------------------------------------------------

def _seed_unverified_contact(repo, domain, email):
    [cid] = repo.save_discovery_batch([CandidateDiscovery(f"https://{domain}/", "search", None, {})])
    record = NormalizedRecord(
        canonical_domain=domain, business_fields={"name": "Test"}, platform="shopify",
        platform_confidence=0.9,
        contacts=[ExtractedField("email", email, f"https://{domain}/contact", "mailto", 0.9)],
        socials=[],
    )
    resolution = EntityResolutionResult(action="create_new", target_business_id=None, match_score=None, match_level=None)
    repo.save_validated_record(ValidatedRecord(record, resolution, {}, True, []))


def test_email_verification_job_success(repo):
    _seed_unverified_contact(repo, "shop-a.com", "a@shop-a.com")
    result = jobs.email_verification_job(10, repo, FakeSuppressionClient(), FakeVerifier())
    assert result["verified"] == 1
    row = _fetch_one("SELECT verification_status FROM contacts WHERE value='a@shop-a.com';")
    assert row[0] == "verified"


def test_email_verification_job_skips_suppressed_without_calling_verifier(repo):
    _seed_unverified_contact(repo, "shop-b.com", "a@shop-b.com")
    verifier = FakeVerifier()
    result = jobs.email_verification_job(10, repo, FakeSuppressionClient(suppressed_emails=["a@shop-b.com"]), verifier)
    assert result["skipped_suppressed"] == 1
    assert verifier.calls == []


def test_email_verification_job_retries_on_provider_failure(repo):
    _seed_unverified_contact(repo, "shop-c.com", "a@shop-c.com")
    verifier = FakeVerifier(exc=jobs.VerificationProviderUnavailable("down"))
    result = jobs.email_verification_job(10, repo, FakeSuppressionClient(), verifier)
    assert result["retried"] == 1
    row = _fetch_one("SELECT verification_status, verification_attempts FROM contacts WHERE value='a@shop-c.com';")
    assert row[0] is None
    assert row[1] == 1


def test_email_verification_job_exhausts_to_unknown(repo):
    _seed_unverified_contact(repo, "shop-d.com", "a@shop-d.com")
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE contacts SET verification_attempts = 4 WHERE value='a@shop-d.com';")
    conn.commit()
    conn.close()

    verifier = FakeVerifier(exc=jobs.VerificationProviderUnavailable("down"))
    result = jobs.email_verification_job(10, repo, FakeSuppressionClient(), verifier)
    assert result["exhausted"] == 1
    row = _fetch_one("SELECT verification_status FROM contacts WHERE value='a@shop-d.com';")
    assert row[0] == "unknown"


def test_email_verification_job_aborts_batch_on_suppression_outage(repo):
    _seed_unverified_contact(repo, "shop-e.com", "a@shop-e.com")
    _seed_unverified_contact(repo, "shop-f.com", "b@shop-f.com")
    verifier = FakeVerifier()

    with pytest.raises(jobs.SuppressionServiceUnavailable):
        jobs.email_verification_job(10, repo, FakeSuppressionClient(exc=jobs.SuppressionServiceUnavailable("down")), verifier)

    assert verifier.calls == []  # no provider calls were made at all


# --- export_job -------------------------------------------------------------------

def test_export_job_writes_file_first_then_marks_exported(repo):
    _seed_unverified_contact(repo, "shop-g.com", "a@shop-g.com")
    [ref] = repo.claim_unverified_contacts(limit=1)
    repo.save_verification_success(ref.contact_id, VerificationResult(ref.email, "verified", "test", datetime.now(timezone.utc)))

    call_order = []

    def fake_build_eligible_list(filters, suppression_client, repository):
        call_order.append("build")
        return repository.get_eligible_contacts(filters)

    def fake_write_export(contacts, fmt):
        call_order.append("write")
        return "/tmp/fake-export.csv"

    path = jobs.export_job(ExportFilters(), "csv", repo, FakeSuppressionClient(), fake_build_eligible_list, fake_write_export)

    assert path == "/tmp/fake-export.csv"
    assert call_order == ["build", "write"]
    row = _fetch_one("SELECT last_exported_at FROM contacts WHERE id=%s;", (ref.contact_id,))
    assert row[0] is not None


def test_export_job_does_not_mark_exported_if_write_fails(repo):
    _seed_unverified_contact(repo, "shop-h.com", "a@shop-h.com")
    [ref] = repo.claim_unverified_contacts(limit=1)
    repo.save_verification_success(ref.contact_id, VerificationResult(ref.email, "verified", "test", datetime.now(timezone.utc)))

    def fake_build_eligible_list(filters, suppression_client, repository):
        return repository.get_eligible_contacts(filters)

    def failing_write(contacts, fmt):
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        jobs.export_job(ExportFilters(), "csv", repo, FakeSuppressionClient(), fake_build_eligible_list, failing_write)

    row = _fetch_one("SELECT last_exported_at FROM contacts WHERE id=%s;", (ref.contact_id,))
    assert row[0] is None  # never marked, since the write never succeeded


# --- retry sweep --------------------------------------------------------------------

def test_requeue_retry_candidates_enqueues_due_candidates_only(repo):
    cid_due = _seed_candidate(repo, "due.com")
    repo.claim_candidate(cid_due)
    repo.mark_candidate_retry(cid_due, "timeout", datetime.now(timezone.utc) - timedelta(minutes=1))

    cid_not_due = _seed_candidate(repo, "notdue.com")
    repo.claim_candidate(cid_not_due)
    repo.mark_candidate_retry(cid_not_due, "timeout", datetime.now(timezone.utc) + timedelta(hours=1))

    enqueued = []
    result = jobs.requeue_retry_candidates(repo, enqueued.append, limit=10)

    assert result == [cid_due]
    assert enqueued == [cid_due]
