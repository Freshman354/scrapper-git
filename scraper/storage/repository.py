"""PostgreSQL implementation of the frozen Repository contract.

Uses psycopg2 directly rather than SQLAlchemy — see the delivery notes
for why. Scoped exclusively to the scraper's own database:

  - Reads SCRAPER_DATABASE_URL only (same isolation rule as the
    migration runner). No fallback to DATABASE_URL or any other
    connection string.
  - Every method opens its own short-lived connection and does its work
    in exactly the transaction boundary documented in the frozen
    contracts (scraper_module_contracts.md S9, scraper_job_state_definitions.md).

This module owns every write to candidates.state and
contacts.verification_status. No other component should issue a raw
UPDATE against either column.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlsplit

import psycopg2
import psycopg2.errors
from psycopg2.extras import Json

from scraper.domain.types import (
    CachedPageRef,
    CandidateDiscovery,
    ContactRef,
    CrawlOutcome,
    DomainPolicySnapshot,
    EligibleContact,
    ExistingBusinessSummary,
    ExportFilters,
    NormalizedRecord,
    ValidatedRecord,
    VerificationResult,
)

ENV_VAR = "SCRAPER_DATABASE_URL"


class StorageIntegrityError(RuntimeError):
    """A constraint violation, or an expected row that wasn't found."""


class StorageConnectionError(RuntimeError):
    """The scraper database was unreachable."""


def get_database_url() -> str:
    """Read the scraper's own database URL. No fallback to any other
    env var — duplicated from migrations/run_migrations.py deliberately
    (see delivery notes) rather than importing across components."""
    url = os.environ.get(ENV_VAR)
    if not url:
        raise StorageConnectionError(
            f"{ENV_VAR} is not set. The repository reads only this "
            "variable and will not fall back to DATABASE_URL or any "
            "other connection string."
        )
    return url


def _normalize_url_to_domain(raw_url: str) -> str:
    """raw_url -> canonical_domain: no scheme, no www, no trailing
    slash, lowercased. Minimal implementation — no IDN/port handling,
    since none of the frozen contracts specify those edge cases."""
    candidate = raw_url if "://" in raw_url else f"//{raw_url}"
    netloc = urlsplit(candidate).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _build_discovery_metadata(candidate: CandidateDiscovery) -> dict:
    """CandidateDiscovery.name_hint has no dedicated column in
    `candidates` — folded into discovery_metadata rather than silently
    dropped. See delivery notes. Also folds in the original URL scheme
    (http/https), since canonical_domain strips it — without this,
    reconstructing a seed URL later would have to guess the scheme."""
    metadata = dict(candidate.discovery_metadata)
    if candidate.name_hint is not None:
        metadata.setdefault("name_hint", candidate.name_hint)
    scheme = urlsplit(candidate.raw_url if "://" in candidate.raw_url else f"//{candidate.raw_url}").scheme
    metadata.setdefault("scheme", scheme or "https")
    return metadata


class PostgresRepository:
    def __init__(self, database_url: Optional[str] = None):
        # Override param exists only for tests, same pattern as the
        # migration runner — production code always reads the env var.
        self._database_url = database_url or get_database_url()

    def _connect(self):
        try:
            return psycopg2.connect(self._database_url)
        except psycopg2.OperationalError as exc:
            raise StorageConnectionError(str(exc)) from exc

    @contextmanager
    def _transaction(self):
        """One connection, one transaction. Commits on clean exit, rolls
        back and translates the error on failure. Integrity errors not
        already absorbed by an ON CONFLICT clause become
        StorageIntegrityError; anything else re-raises as-is after the
        rollback."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except psycopg2.IntegrityError as exc:
            # Covers UniqueViolation, NotNullViolation, ForeignKeyViolation,
            # CheckViolation, etc. — anything not already absorbed by an
            # ON CONFLICT clause.
            conn.rollback()
            raise StorageIntegrityError(str(exc)) from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- discovery / candidates -------------------------------------

    def save_discovery_batch(self, candidates: List[CandidateDiscovery]) -> List[int]:
        """Each row commits independently — see scraper_job_state_definitions.md
        discovery_job #12. Returns ids of newly-inserted candidates only."""
        new_ids: List[int] = []
        for candidate in candidates:
            canonical_domain = _normalize_url_to_domain(candidate.raw_url)
            metadata = _build_discovery_metadata(candidate)
            with self._transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO candidates (canonical_domain, source, discovery_metadata)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (canonical_domain) DO NOTHING
                    RETURNING id;
                    """,
                    (canonical_domain, candidate.source, Json(metadata)),
                )
                row = cur.fetchone()
            if row is not None:
                new_ids.append(row[0])
        return new_ids

    def get_candidate_domain(self, candidate_id: int) -> str:
        """Plain read accessor — not part of the originally frozen list,
        added because crawl_candidate_job's only input is candidate_id
        but it needs canonical_domain to build a CrawlRequest. Flagged
        in delivery notes; no policy decision involved."""
        with self._transaction() as cur:
            cur.execute("SELECT canonical_domain FROM candidates WHERE id = %s;", (candidate_id,))
            row = cur.fetchone()
        if row is None:
            raise StorageIntegrityError(f"No candidate found with id {candidate_id}")
        return row[0]

    def get_candidate_seed_url(self, candidate_id: int) -> str:
        """Reconstructs the seed URL with the ORIGINAL scheme (stored in
        discovery_metadata by save_discovery_batch), rather than
        assuming https — a real bug found via end-to-end testing: a
        hardcoded https:// silently discarded the discovered scheme
        entirely. Falls back to https if scheme is missing (e.g. rows
        inserted before this field existed)."""
        with self._transaction() as cur:
            cur.execute("SELECT canonical_domain, discovery_metadata FROM candidates WHERE id = %s;", (candidate_id,))
            row = cur.fetchone()
        if row is None:
            raise StorageIntegrityError(f"No candidate found with id {candidate_id}")
        canonical_domain, discovery_metadata = row
        scheme = (discovery_metadata or {}).get("scheme", "https")
        return f"{scheme}://{canonical_domain}/"

    def get_domain_policy(self, canonical_domain: str) -> DomainPolicySnapshot:
        with self._transaction() as cur:
            cur.execute(
                """
                SELECT c.attempts, c.last_attempt_at,
                       (SELECT cr.http_status_code FROM crawl_runs cr
                        WHERE cr.candidate_id = c.id
                        ORDER BY cr.started_at DESC LIMIT 1) AS last_status_code
                FROM candidates c
                WHERE c.canonical_domain = %s;
                """,
                (canonical_domain,),
            )
            row = cur.fetchone()
        if row is None:
            raise StorageIntegrityError(f"No candidate found for domain {canonical_domain!r}")
        attempts, last_attempt_at, last_status_code = row
        return DomainPolicySnapshot(
            canonical_domain=canonical_domain,
            consecutive_failures=attempts,
            last_status_code=last_status_code,
            last_attempt_at=last_attempt_at,
        )

    def get_cached_pages(self, canonical_domain: str) -> List[CachedPageRef]:
        # page_cache has no domain column — recovered via the URLs this
        # domain's candidate has actually been crawled at before.
        with self._transaction() as cur:
            cur.execute(
                """
                SELECT DISTINCT pc.url, pc.content_hash, pc.etag, pc.last_modified
                FROM page_cache pc
                JOIN crawl_pages cp ON cp.url = pc.url
                JOIN crawl_runs cr ON cr.id = cp.crawl_run_id
                JOIN candidates c ON c.id = cr.candidate_id
                WHERE c.canonical_domain = %s;
                """,
                (canonical_domain,),
            )
            rows = cur.fetchall()
        return [CachedPageRef(url=r[0], content_hash=r[1], etag=r[2], last_modified=r[3]) for r in rows]

    def claim_candidate(self, candidate_id: int, stale_after_minutes: int = 30) -> bool:
        with self._transaction() as cur:
            cur.execute(
                """
                UPDATE candidates
                SET state = 'crawling', last_attempt_at = now()
                WHERE id = %s
                  AND (
                    state IN ('pending', 'retry')
                    OR (state = 'crawling' AND last_attempt_at < now() - (%s || ' minutes')::interval)
                  )
                RETURNING id;
                """,
                (candidate_id, stale_after_minutes),
            )
            row = cur.fetchone()
        return row is not None

    def _update_candidate_terminal(
        self, candidate_id: int, state: str, failure_reason: str, next_attempt_at: Optional[datetime] = None
    ) -> None:
        with self._transaction() as cur:
            cur.execute(
                """
                UPDATE candidates
                SET state = %s, failure_reason = %s, next_attempt_at = %s, attempts = attempts + 1
                WHERE id = %s;
                """,
                (state, failure_reason, next_attempt_at, candidate_id),
            )

    def mark_candidate_retry(self, candidate_id: int, failure_reason: str, next_attempt_at: datetime) -> None:
        self._update_candidate_terminal(candidate_id, "retry", failure_reason, next_attempt_at)

    def mark_candidate_blocked(self, candidate_id: int, failure_reason: str) -> None:
        self._update_candidate_terminal(candidate_id, "blocked", failure_reason)

    def mark_candidate_abandoned(self, candidate_id: int, failure_reason: str) -> None:
        self._update_candidate_terminal(candidate_id, "abandoned", failure_reason)

    def get_retry_due_candidates(self, limit: int) -> List[int]:
        with self._transaction() as cur:
            cur.execute(
                """
                SELECT id FROM candidates
                WHERE state = 'retry' AND next_attempt_at <= now()
                ORDER BY next_attempt_at
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
        return [r[0] for r in rows]

    # --- crawl results -------------------------------------------------

    def save_crawl_outcome(self, outcome: CrawlOutcome) -> None:
        """Writes crawl_runs + crawl_pages (append-only) + page_cache
        (upserted) in one transaction. Never touches candidates.state."""
        with self._transaction() as cur:
            cur.execute("SELECT id FROM candidates WHERE canonical_domain = %s;", (outcome.canonical_domain,))
            candidate_row = cur.fetchone()
            if candidate_row is None:
                raise StorageIntegrityError(f"No candidate found for domain {outcome.canonical_domain!r}")
            candidate_id = candidate_row[0]

            # A zero-page outcome (e.g. blocked before any page was
            # fetched) has no page timestamp to derive started_at from,
            # and the column is NOT NULL — found via real end-to-end
            # testing against a robots-disallowed crawl. now() is the
            # reasonable fallback: this crawl_run genuinely started and
            # ended at effectively the same instant in that case.
            started_at = min((p.fetched_at for p in outcome.pages), default=None) or datetime.now(timezone.utc)
            representative_status = outcome.pages[0].http_status_code if outcome.pages else None

            cur.execute(
                """
                INSERT INTO crawl_runs
                    (candidate_id, started_at, completed_at, status, http_status_code, pages_crawled, used_browser, error_message)
                VALUES (%s, %s, now(), %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    candidate_id, started_at, outcome.status, representative_status,
                    len(outcome.pages), outcome.used_browser, outcome.failure_reason,
                ),
            )
            crawl_run_id = cur.fetchone()[0]

            for page in outcome.pages:
                cur.execute(
                    """
                    INSERT INTO crawl_pages (crawl_run_id, url, page_type, fetched_via, http_status_code, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (crawl_run_id, page.url, page.page_type, page.fetched_via, page.http_status_code, page.fetched_at),
                )
                # FetchedPage carries no etag/last_modified (see delivery
                # notes) — written NULL here until the crawler component
                # is extended to carry them.
                cur.execute(
                    """
                    INSERT INTO page_cache (url, content_hash, status_code, etag, last_modified, fetched_at)
                    VALUES (%s, %s, %s, NULL, NULL, %s)
                    ON CONFLICT (url) DO UPDATE SET
                        content_hash = EXCLUDED.content_hash,
                        status_code = EXCLUDED.status_code,
                        fetched_at = EXCLUDED.fetched_at;
                    """,
                    (page.url, page.content_hash, page.http_status_code, page.fetched_at),
                )

    # --- entity resolution / persistence --------------------------------

    def get_candidate_matches(self, record: NormalizedRecord) -> List[ExistingBusinessSummary]:
        contact_values = [c.field_value for c in record.contacts]
        with self._transaction() as cur:
            cur.execute(
                """
                SELECT b.id, b.canonical_domain, b.name,
                       ARRAY(SELECT value FROM contacts WHERE business_id = b.id) AS contact_values
                FROM businesses b
                WHERE b.canonical_domain = %s
                   OR b.id IN (SELECT business_id FROM contacts WHERE value = ANY(%s))
                LIMIT 20;
                """,
                (record.canonical_domain, contact_values),
            )
            rows = cur.fetchall()
        return [
            ExistingBusinessSummary(business_id=r[0], canonical_domain=r[1], name=r[2], contacts=list(r[3] or []))
            for r in rows
        ]

    def save_validated_record(self, record: ValidatedRecord) -> int:
        """One transaction: businesses + contacts + social_profiles +
        evidence + candidates.state='success'. See delivery notes for
        the evidence-coverage limitation (business_fields/platform have
        no per-field provenance to write, only contacts/socials do)."""
        normalized = record.normalized_record
        resolution = record.resolution
        bf = normalized.business_fields

        with self._transaction() as cur:
            if resolution.action == "create_new":
                business_id = self._insert_business(cur, normalized)
            elif resolution.action == "auto_merge":
                business_id = resolution.target_business_id
                self._merge_business_fill_null_only(cur, business_id, normalized)
            elif resolution.action == "flag_possible_duplicate":
                business_id = self._insert_business(
                    cur, normalized,
                    merge_status="possible_duplicate",
                    duplicate_of_id=resolution.target_business_id,
                    match_score=resolution.match_score,
                )
            else:
                raise ValueError(f"Unknown EntityResolutionResult.action: {resolution.action!r}")

            for c in normalized.contacts:
                cur.execute(
                    """
                    INSERT INTO contacts (business_id, type, value, source_url, extraction_confidence)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (business_id, type, value) DO NOTHING;
                    """,
                    (business_id, c.field_name, c.field_value, c.source_url, c.confidence),
                )
                cur.execute(
                    """
                    INSERT INTO evidence (business_id, field_name, field_value, source_url, extraction_method, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (business_id, c.field_name, c.field_value, c.source_url, c.extraction_method, c.confidence),
                )

            for s in normalized.socials:
                cur.execute(
                    """
                    INSERT INTO social_profiles (business_id, platform, url, source_url)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (business_id, platform, url) DO NOTHING;
                    """,
                    (business_id, s.field_name, s.field_value, s.source_url),
                )
                cur.execute(
                    """
                    INSERT INTO evidence (business_id, field_name, field_value, source_url, extraction_method, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (business_id, s.field_name, s.field_value, s.source_url, s.extraction_method, s.confidence),
                )

            # The platform label alone is not the claim: detection's evidence
            # for it is written too, mirroring the contacts/socials rows above.
            # Plain INSERT, no ON CONFLICT — evidence is an append-only
            # observation log, so a re-crawl appends rather than overwrites.
            for e in normalized.evidence:
                cur.execute(
                    """
                    INSERT INTO evidence (business_id, field_name, field_value, source_url, extraction_method, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (business_id, e.field_name, e.field_value, e.source_url, e.extraction_method, e.confidence),
                )

            cur.execute(
                "UPDATE candidates SET state = 'success', business_id = %s WHERE canonical_domain = %s;",
                (business_id, normalized.canonical_domain),
            )
        return business_id

    @staticmethod
    def _insert_business(
        cur, normalized: NormalizedRecord,
        merge_status: str = "active",
        duplicate_of_id: Optional[int] = None,
        match_score: Optional[float] = None,
    ) -> int:
        bf = normalized.business_fields
        cur.execute(
            """
            INSERT INTO businesses
                (canonical_domain, name, website, description, category, subcategory,
                 country, state, city, address, platform, platform_confidence,
                 merge_status, duplicate_of_id, match_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (canonical_domain) DO NOTHING
            RETURNING id;
            """,
            (
                normalized.canonical_domain, bf.get("name"), bf.get("website"), bf.get("description"),
                bf.get("category"), bf.get("subcategory"), bf.get("country"), bf.get("state"),
                bf.get("city"), bf.get("address"), normalized.platform, normalized.platform_confidence,
                merge_status, duplicate_of_id, match_score,
            ),
        )
        row = cur.fetchone()
        if row is None:
            # Race: canonical_domain already exists despite the resolver's
            # create_new/flag_possible_duplicate decision. Fall back to
            # the existing row rather than erroring.
            cur.execute("SELECT id FROM businesses WHERE canonical_domain = %s;", (normalized.canonical_domain,))
            row = cur.fetchone()
        return row[0]

    @staticmethod
    def _merge_business_fill_null_only(cur, business_id: int, normalized: NormalizedRecord) -> None:
        bf = normalized.business_fields
        cur.execute(
            """
            UPDATE businesses SET
                name = COALESCE(name, %s), website = COALESCE(website, %s),
                description = COALESCE(description, %s), category = COALESCE(category, %s),
                subcategory = COALESCE(subcategory, %s), country = COALESCE(country, %s),
                state = COALESCE(state, %s), city = COALESCE(city, %s),
                address = COALESCE(address, %s), platform = COALESCE(platform, %s),
                platform_confidence = COALESCE(platform_confidence, %s)
            WHERE id = %s;
            """,
            (
                bf.get("name"), bf.get("website"), bf.get("description"), bf.get("category"),
                bf.get("subcategory"), bf.get("country"), bf.get("state"), bf.get("city"),
                bf.get("address"), normalized.platform, normalized.platform_confidence, business_id,
            ),
        )

    # --- verification --------------------------------------------------

    def claim_unverified_contacts(self, limit: int, lease_minutes: int = 15) -> List[ContactRef]:
        """Claim-and-lease in one transaction: SKIP LOCKED select, then
        a same-transaction UPDATE setting the lease, then commit — all
        before any provider call happens (that's the caller's job)."""
        with self._transaction() as cur:
            cur.execute(
                """
                SELECT id, value, verification_attempts
                FROM contacts
                WHERE type = 'email'
                  AND (verification_status IS NULL OR verification_status = 'pending')
                  AND (next_verification_attempt_at IS NULL OR next_verification_attempt_at <= now())
                ORDER BY id
                LIMIT %s
                FOR UPDATE SKIP LOCKED;
                """,
                (limit,),
            )
            rows = cur.fetchall()
            if rows:
                ids = [r[0] for r in rows]
                cur.execute(
                    """
                    UPDATE contacts
                    SET next_verification_attempt_at = now() + (%s || ' minutes')::interval
                    WHERE id = ANY(%s);
                    """,
                    (lease_minutes, ids),
                )
        return [ContactRef(contact_id=r[0], email=r[1], verification_attempts=r[2]) for r in rows]

    def save_verification_success(self, contact_id: int, result: VerificationResult) -> None:
        with self._transaction() as cur:
            cur.execute(
                """
                UPDATE contacts
                SET verification_status = %s, verification_provider = %s, verified_at = %s,
                    verification_attempts = verification_attempts + 1,
                    last_verification_attempt_at = now(),
                    verification_failure_reason = NULL
                WHERE id = %s;
                """,
                (result.status, result.provider, result.verified_at, contact_id),
            )

    def record_verification_failure(
        self, contact_id: int, failure_reason: str, next_verification_attempt_at: Optional[datetime]
    ) -> None:
        with self._transaction() as cur:
            if next_verification_attempt_at is not None:
                cur.execute(
                    """
                    UPDATE contacts
                    SET verification_attempts = verification_attempts + 1,
                        last_verification_attempt_at = now(),
                        verification_failure_reason = %s,
                        next_verification_attempt_at = %s
                    WHERE id = %s;
                    """,
                    (failure_reason, next_verification_attempt_at, contact_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE contacts
                    SET verification_attempts = verification_attempts + 1,
                        last_verification_attempt_at = now(),
                        verification_failure_reason = %s,
                        verification_status = 'unknown',
                        next_verification_attempt_at = NULL
                    WHERE id = %s;
                    """,
                    (failure_reason, contact_id),
                )

    # --- export ----------------------------------------------------------

    def get_eligible_contacts(self, filters: ExportFilters) -> List[EligibleContact]:
        conditions = ["c.type = 'email'", "c.verification_status = 'verified'"]
        params: list = []
        if filters.platform is not None:
            conditions.append("b.platform = %s")
            params.append(filters.platform)
        if filters.state is not None:
            conditions.append("b.state = %s")
            params.append(filters.state)
        if filters.category is not None:
            conditions.append("b.category = %s")
            params.append(filters.category)
        where_clause = " AND ".join(conditions)
        params.append(filters.limit)

        with self._transaction() as cur:
            cur.execute(
                f"""
                SELECT c.business_id, c.id, c.value, c.verification_status
                FROM contacts c
                JOIN businesses b ON b.id = c.business_id
                WHERE {where_clause}
                ORDER BY c.id
                LIMIT %s;
                """,
                params,
            )
            rows = cur.fetchall()
        return [EligibleContact(business_id=r[0], contact_id=r[1], email=r[2], verification_status=r[3]) for r in rows]

    def mark_contacts_exported(self, contact_ids: List[int]) -> None:
        if not contact_ids:
            return
        with self._transaction() as cur:
            cur.execute(
                "UPDATE contacts SET last_exported_at = now() WHERE id = ANY(%s);",
                (contact_ids,),
            )
