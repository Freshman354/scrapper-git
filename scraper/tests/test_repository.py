"""Tests for scraper/storage/repository.py.

Runs against a real local Postgres database, migrated fresh via the
approved migration runner before each test — not mocks. Concurrency
tests (candidate claim races, SKIP LOCKED partitioning) exercise actual
Postgres locking, not simulated behavior.
"""

import threading
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from scraper.detection import platform as detection
from scraper.domain.types import (
    CandidateDiscovery,
    ContactRef,
    CrawlOutcome,
    DomainPolicySnapshot,
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
from scraper.processing import normalization
from scraper.storage.repository import (
    PostgresRepository,
    StorageConnectionError,
    StorageIntegrityError,
    _normalize_url_to_domain,
    get_database_url,
)

TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost/scraper_test"


# --- fixtures / helpers ------------------------------------------------------

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


def _raw_conn():
    return psycopg2.connect(TEST_DATABASE_URL)


def _fetch_one(sql, params=()):
    conn = _raw_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def _fetch_all(sql, params=()):
    conn = _raw_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _seed_candidate(repo, domain):
    ids = repo.save_discovery_batch(
        [CandidateDiscovery(raw_url=f"https://{domain}/", source="search", name_hint=None, discovery_metadata={})]
    )
    return ids[0] if ids else None


def _make_normalized_record(domain="example.com", name="Example Store", email="hello@example.com", platform="shopify"):
    contacts = []
    if email:
        contacts.append(
            ExtractedField(field_name="email", field_value=email, source_url=f"https://{domain}/contact",
                            extraction_method="mailto", confidence=0.95)
        )
    business_fields = {"name": name, "category": "Fashion"}
    return NormalizedRecord(
        canonical_domain=domain,
        business_fields=business_fields,
        platform=platform,
        platform_confidence=0.9,
        contacts=contacts,
        socials=[
            ExtractedField(field_name="instagram", field_value=f"https://instagram.com/{domain}",
                            source_url=f"https://{domain}/", extraction_method="link", confidence=0.8)
        ],
    )


def _make_validated_record(action="create_new", target_business_id=None, match_score=None, **kwargs):
    record = _make_normalized_record(**kwargs)
    resolution = EntityResolutionResult(
        action=action, target_business_id=target_business_id, match_score=match_score, match_level=None
    )
    return ValidatedRecord(
        normalized_record=record, resolution=resolution, field_confidence={}, is_valid=True, validation_errors=[]
    )


def _make_crawl_outcome(domain="example.com", pages=None):
    if pages is None:
        pages = [
            FetchedPage(url=f"https://{domain}/", page_type="homepage", fetched_via="http", http_status_code=200,
                        content_hash="hash1", raw_html="<html></html>", from_cache=False,
                        fetched_at=datetime.now(timezone.utc))
        ]
    policy = DomainPolicySnapshot(canonical_domain=domain, consecutive_failures=0, last_status_code=200,
                                   last_attempt_at=datetime.now(timezone.utc))
    return CrawlOutcome(canonical_domain=domain, status="success", pages=pages, used_browser=False,
                         updated_domain_policy=policy, failure_reason=None)


def _seed_verified_contact(repo, domain, email):
    _seed_candidate(repo, domain)
    repo.save_validated_record(_make_validated_record(domain=domain, email=email))
    [ref] = repo.claim_unverified_contacts(limit=1)
    repo.save_verification_success(
        ref.contact_id, VerificationResult(email=ref.email, status="verified", provider="test", verified_at=datetime.now(timezone.utc))
    )
    return ref.contact_id


# --- pure unit test: URL normalization ---------------------------------------

def test_normalize_url_to_domain():
    assert _normalize_url_to_domain("https://Example.com/") == "example.com"
    assert _normalize_url_to_domain("http://www.example.com") == "example.com"
    assert _normalize_url_to_domain("example.com") == "example.com"


# --- env var isolation --------------------------------------------------------

def test_get_database_url_raises_when_unset(monkeypatch):
    monkeypatch.delenv("SCRAPER_DATABASE_URL", raising=False)
    with pytest.raises(StorageConnectionError):
        get_database_url()


def test_storage_connection_error_when_database_unreachable():
    bad_repo = PostgresRepository(database_url="postgresql://postgres:postgres@localhost:59999/nonexistent")
    with pytest.raises(StorageConnectionError):
        bad_repo.get_domain_policy("example.com")


# --- candidate upsert / deduplication (save_discovery_batch) -----------------

def test_save_discovery_batch_dedupes_by_canonical_domain(repo):
    candidates = [
        CandidateDiscovery(raw_url="https://Example.com/", source="search", name_hint="Example Store", discovery_metadata={"rank": 1}),
        CandidateDiscovery(raw_url="https://www.example.com", source="search", name_hint=None, discovery_metadata={"rank": 2}),
    ]
    first_ids = repo.save_discovery_batch(candidates)
    assert len(first_ids) == 1

    second_ids = repo.save_discovery_batch(candidates)
    assert second_ids == []

    rows = _fetch_all("SELECT canonical_domain FROM candidates;")
    assert [r[0] for r in rows] == ["example.com"]


def test_save_discovery_batch_returns_only_new_ids_in_mixed_batch(repo):
    repo.save_discovery_batch([CandidateDiscovery("https://existing.com/", "search", None, {})])
    ids = repo.save_discovery_batch([
        CandidateDiscovery("https://existing.com/", "search", None, {}),
        CandidateDiscovery("https://new.com/", "search", None, {}),
    ])
    assert len(ids) == 1
    row = _fetch_one("SELECT canonical_domain FROM candidates WHERE id=%s;", (ids[0],))
    assert row[0] == "new.com"


def test_save_discovery_batch_folds_name_hint_into_metadata(repo):
    repo.save_discovery_batch(
        [CandidateDiscovery(raw_url="https://example.com/", source="search", name_hint="Example Store", discovery_metadata={"rank": 1})]
    )
    row = _fetch_one("SELECT discovery_metadata FROM candidates WHERE canonical_domain='example.com';")
    assert row[0]["name_hint"] == "Example Store"
    assert row[0]["rank"] == 1


# --- atomic candidate claiming -------------------------------------------------

def test_claim_candidate_succeeds_from_pending(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    assert repo.claim_candidate(cid) is True
    row = _fetch_one("SELECT state FROM candidates WHERE id=%s;", (cid,))
    assert row[0] == "crawling"


def test_claim_candidate_fails_on_already_crawling_fresh(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    assert repo.claim_candidate(cid) is True
    assert repo.claim_candidate(cid) is False


def test_claim_candidate_fails_on_terminal_states(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    repo.claim_candidate(cid)
    repo.mark_candidate_blocked(cid, "robots disallow")
    assert repo.claim_candidate(cid) is False


# --- stale crawling recovery ---------------------------------------------------

def test_claim_candidate_recovers_stale_crawling_row(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    repo.claim_candidate(cid)
    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE candidates SET last_attempt_at = now() - INTERVAL '31 minutes' WHERE id=%s;", (cid,))
    conn.commit()
    conn.close()

    assert repo.claim_candidate(cid, stale_after_minutes=30) is True


def test_claim_candidate_does_not_recover_fresh_crawling_row(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    repo.claim_candidate(cid)
    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE candidates SET last_attempt_at = now() - INTERVAL '10 minutes' WHERE id=%s;", (cid,))
    conn.commit()
    conn.close()

    assert repo.claim_candidate(cid, stale_after_minutes=30) is False


# --- concurrent candidate claims ------------------------------------------------

def test_concurrent_candidate_claims_only_one_wins(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])

    results = []
    results_lock = threading.Lock()

    def _claim():
        r = PostgresRepository(database_url=TEST_DATABASE_URL)
        outcome = r.claim_candidate(cid)
        with results_lock:
            results.append(outcome)

    threads = [threading.Thread(target=_claim) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == 9


# --- verification SKIP LOCKED partitioning --------------------------------------

def test_verification_skip_locked_partitions_concurrent_claims(repo):
    """Deterministic, not timing-dependent: holds one transaction's lock
    open and proves a second connection's SKIP LOCKED select excludes
    exactly those rows."""
    _seed_candidate(repo, "shop-a.com")
    repo.save_validated_record(_make_validated_record(
        domain="shop-a.com",
        email=None,
    ))
    # insert 5 distinct contacts directly under that business for this test
    business_id = _fetch_one("SELECT id FROM businesses WHERE canonical_domain='shop-a.com';")[0]
    conn = _raw_conn()
    with conn.cursor() as cur:
        for i in range(5):
            cur.execute(
                "INSERT INTO contacts (business_id, type, value) VALUES (%s, 'email', %s);",
                (business_id, f"user{i}@shop-a.com"),
            )
    conn.commit()
    conn.close()

    conn_a = _raw_conn()
    conn_b = _raw_conn()
    try:
        with conn_a.cursor() as cur_a:
            cur_a.execute(
                """
                SELECT id FROM contacts
                WHERE type='email' AND (verification_status IS NULL OR verification_status='pending')
                ORDER BY id LIMIT 3 FOR UPDATE SKIP LOCKED;
                """
            )
            claimed_a = {r[0] for r in cur_a.fetchall()}
        assert len(claimed_a) == 3

        with conn_b.cursor() as cur_b:
            cur_b.execute(
                """
                SELECT id FROM contacts
                WHERE type='email' AND (verification_status IS NULL OR verification_status='pending')
                ORDER BY id LIMIT 3 FOR UPDATE SKIP LOCKED;
                """
            )
            claimed_b = {r[0] for r in cur_b.fetchall()}

        assert len(claimed_b) == 2
        assert claimed_a.isdisjoint(claimed_b)
    finally:
        conn_a.commit()
        conn_b.commit()
        conn_a.close()
        conn_b.close()


def test_claim_unverified_contacts_leases_prevent_second_claim_of_same_rows(repo):
    _seed_candidate(repo, "shop-b.com")
    repo.save_validated_record(_make_validated_record(domain="shop-b.com", email=None))
    business_id = _fetch_one("SELECT id FROM businesses WHERE canonical_domain='shop-b.com';")[0]
    conn = _raw_conn()
    with conn.cursor() as cur:
        for i in range(5):
            cur.execute(
                "INSERT INTO contacts (business_id, type, value) VALUES (%s, 'email', %s);",
                (business_id, f"user{i}@shop-b.com"),
            )
    conn.commit()
    conn.close()

    first = repo.claim_unverified_contacts(limit=3)
    assert len(first) == 3
    second = repo.claim_unverified_contacts(limit=10)
    assert len(second) == 2
    assert {c.contact_id for c in first}.isdisjoint({c.contact_id for c in second})


# --- verification lease creation and expiry -------------------------------------

def test_claim_sets_lease_approximately_15_minutes_out(repo):
    _seed_candidate(repo, "shop-c.com")
    repo.save_validated_record(_make_validated_record(domain="shop-c.com", email="a@shop-c.com"))
    claimed = repo.claim_unverified_contacts(limit=1, lease_minutes=15)
    assert len(claimed) == 1
    row = _fetch_one("SELECT next_verification_attempt_at FROM contacts WHERE id=%s;", (claimed[0].contact_id,))
    lease_at = row[0]
    delta = lease_at - datetime.now(timezone.utc)
    assert timedelta(minutes=14) < delta < timedelta(minutes=16)


def test_expired_lease_allows_reclaim(repo):
    _seed_candidate(repo, "shop-d.com")
    repo.save_validated_record(_make_validated_record(domain="shop-d.com", email="a@shop-d.com"))
    claimed = repo.claim_unverified_contacts(limit=1, lease_minutes=15)
    contact_id = claimed[0].contact_id

    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE contacts SET next_verification_attempt_at = now() - INTERVAL '1 minute' WHERE id=%s;", (contact_id,))
    conn.commit()
    conn.close()

    reclaimed = repo.claim_unverified_contacts(limit=1)
    assert [c.contact_id for c in reclaimed] == [contact_id]


# --- candidate state transitions -----------------------------------------------

def test_mark_candidate_retry_increments_attempts_and_sets_fields(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    repo.claim_candidate(cid)
    next_attempt = datetime.now(timezone.utc) + timedelta(minutes=2)
    repo.mark_candidate_retry(cid, "timeout", next_attempt)
    row = _fetch_one("SELECT state, attempts, failure_reason, next_attempt_at FROM candidates WHERE id=%s;", (cid,))
    assert row[0] == "retry"
    assert row[1] == 1
    assert row[2] == "timeout"
    assert row[3] is not None


def test_mark_candidate_blocked_is_terminal(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    repo.claim_candidate(cid)
    repo.mark_candidate_blocked(cid, "403 blocked")
    row = _fetch_one("SELECT state, failure_reason FROM candidates WHERE id=%s;", (cid,))
    assert row == ("blocked", "403 blocked")
    assert repo.claim_candidate(cid) is False


def test_mark_candidate_abandoned_is_terminal(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    repo.claim_candidate(cid)
    repo.mark_candidate_abandoned(cid, "max_attempts_exceeded")
    row = _fetch_one("SELECT state FROM candidates WHERE id=%s;", (cid,))
    assert row[0] == "abandoned"


def test_get_retry_due_candidates(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    repo.claim_candidate(cid)
    repo.mark_candidate_retry(cid, "timeout", datetime.now(timezone.utc) - timedelta(minutes=1))
    due = repo.get_retry_due_candidates(limit=10)
    assert due == [cid]


def test_get_domain_policy_reflects_attempts_and_last_status(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    repo.claim_candidate(cid)
    repo.save_crawl_outcome(_make_crawl_outcome("example.com"))
    repo.mark_candidate_retry(cid, "timeout", datetime.now(timezone.utc) + timedelta(minutes=2))

    policy = repo.get_domain_policy("example.com")
    assert policy.consecutive_failures == 1
    assert policy.last_status_code == 200


def test_get_domain_policy_raises_for_unknown_domain(repo):
    with pytest.raises(StorageIntegrityError):
        repo.get_domain_policy("nonexistent.com")


def test_get_candidate_domain_returns_canonical_domain(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    assert repo.get_candidate_domain(cid) == "example.com"


def test_get_candidate_domain_raises_for_unknown_id(repo):
    with pytest.raises(StorageIntegrityError):
        repo.get_candidate_domain(999999)


def test_get_candidate_seed_url_preserves_original_scheme(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("http://example.com/", "search", None, {})])
    assert repo.get_candidate_seed_url(cid) == "http://example.com/"


def test_get_candidate_seed_url_defaults_to_https(repo):
    [cid] = repo.save_discovery_batch([CandidateDiscovery("https://example.com/", "search", None, {})])
    assert repo.get_candidate_seed_url(cid) == "https://example.com/"


# --- verification success/failure persistence -----------------------------------

def test_save_verification_success_persists_status(repo):
    _seed_candidate(repo, "shop-e.com")
    repo.save_validated_record(_make_validated_record(domain="shop-e.com", email="a@shop-e.com"))
    [ref] = repo.claim_unverified_contacts(limit=1)
    result = VerificationResult(email=ref.email, status="verified", provider="test-provider", verified_at=datetime.now(timezone.utc))
    repo.save_verification_success(ref.contact_id, result)
    row = _fetch_one("SELECT verification_status, verification_provider, verification_attempts FROM contacts WHERE id=%s;", (ref.contact_id,))
    assert row[0] == "verified"
    assert row[1] == "test-provider"
    assert row[2] == 1


def test_record_verification_failure_with_attempts_remaining_stays_pending(repo):
    _seed_candidate(repo, "shop-f.com")
    repo.save_validated_record(_make_validated_record(domain="shop-f.com", email="a@shop-f.com"))
    [ref] = repo.claim_unverified_contacts(limit=1)
    next_attempt = datetime.now(timezone.utc) + timedelta(minutes=5)
    repo.record_verification_failure(ref.contact_id, "provider_unavailable", next_attempt)
    row = _fetch_one("SELECT verification_status, verification_attempts, verification_failure_reason FROM contacts WHERE id=%s;", (ref.contact_id,))
    assert row[0] is None
    assert row[1] == 1
    assert row[2] == "provider_unavailable"


def test_record_verification_failure_exhausted_becomes_unknown(repo):
    _seed_candidate(repo, "shop-g.com")
    repo.save_validated_record(_make_validated_record(domain="shop-g.com", email="a@shop-g.com"))
    [ref] = repo.claim_unverified_contacts(limit=1)
    repo.record_verification_failure(ref.contact_id, "provider_unavailable", None)
    row = _fetch_one("SELECT verification_status, next_verification_attempt_at FROM contacts WHERE id=%s;", (ref.contact_id,))
    assert row[0] == "unknown"
    assert row[1] is None


# --- append-only crawl / evidence records ----------------------------------------

def test_save_crawl_outcome_is_append_only_across_runs(repo):
    _seed_candidate(repo, "example.com")
    repo.save_crawl_outcome(_make_crawl_outcome("example.com"))
    repo.save_crawl_outcome(_make_crawl_outcome("example.com"))
    rows = _fetch_all("SELECT id FROM crawl_runs;")
    assert len(rows) == 2


def test_save_crawl_outcome_with_zero_pages_does_not_violate_not_null(repo):
    """Regression test: a blocked-before-any-fetch outcome has no page
    to derive started_at from, and crawl_runs.started_at is NOT NULL —
    found via the CLI against a real robots-disallowed crawl."""
    _seed_candidate(repo, "example.com")
    policy = DomainPolicySnapshot("example.com", 0, None, datetime.now(timezone.utc))
    blocked_outcome = CrawlOutcome(canonical_domain="example.com", status="blocked", pages=[],
                                    used_browser=False, updated_domain_policy=policy,
                                    failure_reason="robots.txt disallow")
    repo.save_crawl_outcome(blocked_outcome)  # must not raise
    row = _fetch_one("SELECT status, started_at FROM crawl_runs WHERE candidate_id=(SELECT id FROM candidates WHERE canonical_domain='example.com');")
    assert row[0] == "blocked"
    assert row[1] is not None


def test_evidence_accumulates_across_recrawls_not_deduped(repo):
    _seed_candidate(repo, "example.com")
    repo.save_validated_record(_make_validated_record(domain="example.com"))
    repo.save_validated_record(_make_validated_record(domain="example.com"))

    evidence_count = _fetch_one("SELECT COUNT(*) FROM evidence WHERE field_name='email';")[0]
    assert evidence_count == 2  # two observations, not deduped

    contact_count = _fetch_one("SELECT COUNT(*) FROM contacts;")[0]
    assert contact_count == 1  # contacts stay deduped via ON CONFLICT DO NOTHING


def test_platform_evidence_is_persisted_with_the_business(repo):
    """Violation 2 regression: the evidence showing why a platform was
    claimed must reach storage, not just the bare platform label. Runs the
    real detect() and normalize() so the whole detect -> normalize -> save
    path is exercised, not a hand-built record."""
    _seed_candidate(repo, "example.com")
    html = '<html><body><script src="https://cdn.shopify.com/s/files/1/theme.js"></script></body></html>'
    detection_result = detection.detect(
        [FetchedPage(url="https://example.com/", page_type="homepage", fetched_via="http",
                     http_status_code=200, content_hash="h", raw_html=html, from_cache=False,
                     fetched_at=datetime.now(timezone.utc))],
        [],
    )
    extraction = ExtractionResult(
        canonical_domain="example.com",
        business_fields=[ExtractedField("name", "Example Store", "https://example.com/", "title-tag", 0.9)],
        contacts=[], socials=[], products=[],
    )
    record = normalization.normalize(extraction, detection_result)
    resolution = EntityResolutionResult(action="create_new", target_business_id=None, match_score=None, match_level=None)

    business_id = repo.save_validated_record(
        ValidatedRecord(normalized_record=record, resolution=resolution,
                        field_confidence={}, is_valid=True, validation_errors=[])
    )

    rows = _fetch_all(
        "SELECT field_name, field_value, source_url, extraction_method, confidence FROM evidence "
        "WHERE business_id=%s AND field_name='platform';",
        (business_id,),
    )
    assert len(rows) == 1  # the evidence row exists at all — this is what violation 2 was about
    field_name, field_value, source_url, extraction_method, confidence = rows[0]
    assert field_name == "platform"
    assert field_value == "shopify"
    assert source_url == "https://example.com/"
    assert extraction_method == "shopify-cdn-asset"
    assert float(confidence) == pytest.approx(0.97)


# --- page-cache reads/writes -------------------------------------------------------

def test_get_cached_pages_empty_for_never_crawled_domain(repo):
    _seed_candidate(repo, "example.com")
    assert repo.get_cached_pages("example.com") == []


def test_page_cache_populated_and_retrievable_after_crawl(repo):
    _seed_candidate(repo, "example.com")
    repo.save_crawl_outcome(_make_crawl_outcome("example.com"))
    cached = repo.get_cached_pages("example.com")
    assert len(cached) == 1
    assert cached[0].content_hash == "hash1"


def test_page_cache_upsert_refreshes_hash_not_duplicates(repo):
    _seed_candidate(repo, "example.com")
    page1 = FetchedPage(url="https://example.com/", page_type="homepage", fetched_via="http", http_status_code=200,
                         content_hash="hash-old", raw_html="<a>", from_cache=False, fetched_at=datetime.now(timezone.utc))
    repo.save_crawl_outcome(_make_crawl_outcome("example.com", pages=[page1]))

    page2 = FetchedPage(url="https://example.com/", page_type="homepage", fetched_via="http", http_status_code=200,
                         content_hash="hash-new", raw_html="<a>", from_cache=False, fetched_at=datetime.now(timezone.utc))
    repo.save_crawl_outcome(_make_crawl_outcome("example.com", pages=[page2]))

    rows = _fetch_all("SELECT content_hash FROM page_cache WHERE url='https://example.com/';")
    assert len(rows) == 1
    assert rows[0][0] == "hash-new"


# --- ContactRef / ExportFilters (functional, via the repository) -------------------

def test_get_eligible_contacts_filters_by_platform(repo):
    _seed_verified_contact(repo, "shop-h.com", "a@shop-h.com")

    matches = repo.get_eligible_contacts(ExportFilters(platform="shopify"))
    assert len(matches) == 1
    assert matches[0].email == "a@shop-h.com"

    no_matches = repo.get_eligible_contacts(ExportFilters(platform="woocommerce"))
    assert no_matches == []


def test_get_eligible_contacts_excludes_unverified(repo):
    _seed_candidate(repo, "shop-i.com")
    repo.save_validated_record(_make_validated_record(domain="shop-i.com", email="a@shop-i.com"))
    assert repo.get_eligible_contacts(ExportFilters()) == []


def test_mark_contacts_exported_sets_timestamp(repo):
    contact_id = _seed_verified_contact(repo, "shop-j.com", "a@shop-j.com")
    repo.mark_contacts_exported([contact_id])
    row = _fetch_one("SELECT last_exported_at FROM contacts WHERE id=%s;", (contact_id,))
    assert row[0] is not None


def test_claim_unverified_contacts_returns_contact_ref_shape(repo):
    _seed_candidate(repo, "shop-k.com")
    repo.save_validated_record(_make_validated_record(domain="shop-k.com", email="a@shop-k.com"))
    [ref] = repo.claim_unverified_contacts(limit=1)
    assert isinstance(ref, ContactRef)
    assert ref.email == "a@shop-k.com"
    assert ref.verification_attempts == 0


# --- entity resolution branches (auto_merge / flag_possible_duplicate) --------------

def test_get_candidate_matches_finds_by_domain_and_shared_contact(repo):
    _seed_candidate(repo, "example.com")
    business_id = repo.save_validated_record(_make_validated_record(domain="example.com", email="shared@example.com"))

    matches = repo.get_candidate_matches(_make_normalized_record(domain="example.com", email="other@nowhere.com"))
    assert any(m.business_id == business_id for m in matches)

    matches2 = repo.get_candidate_matches(_make_normalized_record(domain="totally-different.com", email="shared@example.com"))
    assert any(m.business_id == business_id for m in matches2)

    matches3 = repo.get_candidate_matches(_make_normalized_record(domain="unrelated.com", email="nobody@unrelated.com"))
    assert matches3 == []


def test_save_validated_record_auto_merge_fills_null_only(repo):
    _seed_candidate(repo, "example.com")
    first_id = repo.save_validated_record(_make_validated_record(domain="example.com", name="Original Name"))

    merge_record = _make_normalized_record(domain="example.com", name="Different Name")
    merge_record.business_fields["description"] = "A great shop"
    resolution = EntityResolutionResult(action="auto_merge", target_business_id=first_id, match_score=0.97, match_level="strong")
    validated = ValidatedRecord(normalized_record=merge_record, resolution=resolution, field_confidence={}, is_valid=True, validation_errors=[])
    result_id = repo.save_validated_record(validated)

    assert result_id == first_id
    row = _fetch_one("SELECT name, description FROM businesses WHERE id=%s;", (first_id,))
    assert row[0] == "Original Name"   # NOT overwritten
    assert row[1] == "A great shop"    # backfilled, was previously NULL


def test_save_validated_record_flag_possible_duplicate_creates_separate_row(repo):
    _seed_candidate(repo, "example.com")
    first_id = repo.save_validated_record(_make_validated_record(domain="example.com"))
    _seed_candidate(repo, "example-2.com")

    dup_record = _make_normalized_record(domain="example-2.com", email="hello2@example-2.com")
    resolution = EntityResolutionResult(action="flag_possible_duplicate", target_business_id=first_id, match_score=0.85, match_level="medium")
    validated = ValidatedRecord(normalized_record=dup_record, resolution=resolution, field_confidence={}, is_valid=True, validation_errors=[])
    second_id = repo.save_validated_record(validated)

    assert second_id != first_id
    row = _fetch_one("SELECT merge_status, duplicate_of_id, match_score FROM businesses WHERE id=%s;", (second_id,))
    assert row[0] == "possible_duplicate"
    assert row[1] == first_id
    assert float(row[2]) == pytest.approx(0.85)


# --- transaction rollback behavior ---------------------------------------------------

def test_save_validated_record_rolls_back_fully_on_contact_write_failure(repo):
    _seed_candidate(repo, "example.com")
    bad_contact = ExtractedField(field_name="email", field_value=None, source_url="https://example.com/contact",
                                  extraction_method="mailto", confidence=0.9)
    record = NormalizedRecord(canonical_domain="example.com", business_fields={"name": "Example Store"},
                               platform="shopify", platform_confidence=0.9, contacts=[bad_contact], socials=[])
    resolution = EntityResolutionResult(action="create_new", target_business_id=None, match_score=None, match_level=None)
    validated = ValidatedRecord(normalized_record=record, resolution=resolution, field_confidence={}, is_valid=True, validation_errors=[])

    with pytest.raises(StorageIntegrityError):
        repo.save_validated_record(validated)

    business_count = _fetch_one("SELECT COUNT(*) FROM businesses WHERE canonical_domain='example.com';")[0]
    assert business_count == 0  # the business insert earlier in the same transaction also rolled back

    candidate_row = _fetch_one("SELECT state FROM candidates WHERE canonical_domain='example.com';")
    assert candidate_row[0] == "pending"  # never reached 'success'
