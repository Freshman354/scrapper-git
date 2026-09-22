"""RQ job orchestration.

Each job function here is the composition described in
scraper_job_state_definitions.md — it calls collaborators in the
documented order and translates their results into repository writes.
It contains no business logic of its own beyond that sequencing.

Collaborators (discovery providers, the crawler engine, extraction,
detection, normalization, entity resolution, validation, the email
verifier, the suppression client, the export writer/eligibility
builder) are none of them implemented yet — this project's build order
put the RQ jobs before them. So every job function here takes its
collaborators as explicit parameters (dependency injection), typed
against small local Protocols that mirror the frozen module contracts
exactly. This keeps every job fully testable today with fakes standing
in for those collaborators, against the real repository.

The `*_entrypoint` functions at the bottom are what RQ actually calls —
minimal signatures matching the frozen job contracts' input arguments.
Where a job's collaborators don't exist yet, its entrypoint raises
NotImplementedError rather than silently doing something wrong;
`requeue_retry_candidates_entrypoint` is the one exception, since it
only depends on the repository and the queue module, both of which
exist. Nothing about the core job functions above the entrypoints
changes when the missing modules are eventually built — only the
entrypoints get filled in.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Protocol

from scraper.domain.types import (
    CandidateDiscovery,
    CrawlOutcome,
    CrawlRequest,
    DiscoveryCriteria,
    DomainPolicySnapshot,
    EligibleContact,
    EntityResolutionResult,
    ExistingBusinessSummary,
    ExportFilters,
    ExtractedField,
    ExtractionResult,
    DetectionResult,
    FetchedPage,
    NormalizedRecord,
    ValidatedRecord,
    VerificationResult,
)
from scraper.storage.repository import PostgresRepository, StorageIntegrityError
from scraper.crawler.engine import CrawlTimeout, CrawlBlocked
from scraper.crawler.policy import CrawlDomainCircuitOpen

# --- Placeholder collaborator contracts -------------------------------------
#
# These mirror scraper_module_contracts.md exactly. They live here only
# because the real modules (discovery/, crawler/, extraction/,
# detection/, processing/, verification/, export/) don't exist yet —
# this is a mechanical relocation once each one is built, not a design
# decision made here.

class DiscoveryProvider(Protocol):
    def discover(self, criteria: DiscoveryCriteria) -> List[CandidateDiscovery]: ...


class CrawlEngine(Protocol):
    def crawl(self, request: CrawlRequest) -> CrawlOutcome: ...


class Extractor(Protocol):
    def extract(self, pages: List[FetchedPage]) -> ExtractionResult: ...


class Detector(Protocol):
    def detect(self, pages: List[FetchedPage], structured_data: List[ExtractedField]) -> DetectionResult: ...


class Normalizer(Protocol):
    def normalize(self, extraction: ExtractionResult, detection: DetectionResult) -> NormalizedRecord: ...


class EntityResolver(Protocol):
    def resolve(self, record: NormalizedRecord, candidate_matches: List[ExistingBusinessSummary]) -> EntityResolutionResult: ...


class Validator(Protocol):
    def validate(self, record: NormalizedRecord, resolution: EntityResolutionResult) -> ValidatedRecord: ...


class EmailVerifier(Protocol):
    def verify(self, email: str) -> VerificationResult: ...


class SuppressionClient(Protocol):
    def is_suppressed(self, email: str) -> bool: ...


class ExportWriter(Protocol):
    def export(self, contacts: List[EligibleContact], fmt: str) -> str: ...  # Path, kept as str here to avoid a pathlib dependency in the Protocol


# Placeholder exceptions — same relocation note as above. These mirror
# the Errors sections of the frozen crawler/verification/export
# contracts (module_contracts.md §2, §10, §11).

class VerificationProviderUnavailable(Exception): ...
class VerificationQuotaExceeded(Exception): ...
class SuppressionServiceUnavailable(Exception): ...

# CrawlTimeout, CrawlBlocked, CrawlDomainCircuitOpen used to be
# placeholders here too — now that scraper/crawler/ is implemented,
# these are the real exceptions, imported below rather than redefined.


# --- Backoff schedules -------------------------------------------------------
#
# Per scraper_job_state_definitions.md: candidate backoff is explicitly
# "computed by crawler.policy" (delegated — see compute_candidate_backoff
# parameter below); verification backoff is explicitly "computed by the
# job" (kept local here, matching that wording precisely).

MAX_CANDIDATE_ATTEMPTS = 5     # candidates.max_attempts default; DomainPolicySnapshot
                                # doesn't carry a per-row override, so this is a constant
                                # for now — see delivery notes.
MAX_VERIFICATION_ATTEMPTS = 5

_VERIFICATION_BACKOFF_MINUTES = [5, 30, 120, 720, 2880]  # 5min, 30min, 2hr, 12hr, 48hr


def compute_verification_backoff(attempt_number: int) -> timedelta:
    index = max(min(attempt_number, len(_VERIFICATION_BACKOFF_MINUTES)) - 1, 0)
    return timedelta(minutes=_VERIFICATION_BACKOFF_MINUTES[index])


def default_candidate_backoff(attempt_number: int) -> timedelta:
    """Default implementation of the schedule crawler.policy will one
    day own: 2min, 8min, 32min, 2hr, 8hr. crawl_candidate_job takes this
    as an injected parameter (not hardcoded internally) so the real
    crawler.policy module can be swapped in later without touching
    jobs.py."""
    minutes = [2, 8, 32, 120, 480]
    index = max(min(attempt_number, len(minutes)) - 1, 0)
    return timedelta(minutes=minutes[index])


# --- discovery_job -----------------------------------------------------------

def discovery_job(
    provider: DiscoveryProvider,
    criteria: DiscoveryCriteria,
    repository: PostgresRepository,
    enqueue_crawl_candidate: Callable[[int], None],
) -> List[int]:
    """Per scraper_job_state_definitions.md discovery_job. Enqueues
    crawl_candidate_job only for ids save_discovery_batch actually
    returned, each right after that candidate's row has committed (the
    repository already commits per-row internally)."""
    candidates = provider.discover(criteria)
    new_ids = repository.save_discovery_batch(candidates)
    for candidate_id in new_ids:
        enqueue_crawl_candidate(candidate_id)
    return new_ids


# --- crawl_candidate_job ------------------------------------------------------

def _handle_retryable_crawl_failure(
    repository: PostgresRepository,
    candidate_id: int,
    domain_policy: DomainPolicySnapshot,
    failure_reason: str,
    compute_candidate_backoff: Callable[[int], timedelta],
) -> str:
    attempt_number = domain_policy.consecutive_failures + 1
    if attempt_number >= MAX_CANDIDATE_ATTEMPTS:
        repository.mark_candidate_abandoned(candidate_id, failure_reason)
        return "abandoned"
    next_attempt_at = datetime.now(timezone.utc) + compute_candidate_backoff(attempt_number)
    repository.mark_candidate_retry(candidate_id, failure_reason, next_attempt_at)
    return "retry"


def crawl_candidate_job(
    candidate_id: int,
    repository: PostgresRepository,
    crawler: CrawlEngine,
    extractor: Extractor,
    detector: Detector,
    normalizer: Normalizer,
    resolver: EntityResolver,
    validator: Validator,
    compute_candidate_backoff: Callable[[int], timedelta] = default_candidate_backoff,
    stale_after_minutes: int = 30,
) -> str:
    """Per scraper_job_state_definitions.md crawl_candidate_job — the
    composition root. Returns the resulting outcome
    ('skipped'|'success'|'retry'|'blocked'|'abandoned') for observability
    and testing; RQ itself doesn't need the return value.
    """
    claimed = repository.claim_candidate(candidate_id, stale_after_minutes=stale_after_minutes)
    if not claimed:
        return "skipped"

    canonical_domain = repository.get_candidate_domain(candidate_id)
    domain_policy = repository.get_domain_policy(canonical_domain)
    cached_pages = repository.get_cached_pages(canonical_domain)
    crawl_request = CrawlRequest(
        canonical_domain=canonical_domain,
        seed_url=repository.get_candidate_seed_url(candidate_id),
        domain_policy=domain_policy,
        cached_pages=cached_pages,
    )

    try:
        outcome = crawler.crawl(crawl_request)
    except CrawlBlocked as exc:
        repository.mark_candidate_blocked(candidate_id, str(exc) or "blocked")
        return "blocked"
    except (CrawlTimeout, CrawlDomainCircuitOpen) as exc:
        return _handle_retryable_crawl_failure(
            repository, candidate_id, domain_policy, str(exc) or "timeout", compute_candidate_backoff
        )

    repository.save_crawl_outcome(outcome)

    if outcome.status == "blocked":
        repository.mark_candidate_blocked(candidate_id, outcome.failure_reason or "blocked")
        return "blocked"
    if outcome.status != "success":
        return _handle_retryable_crawl_failure(
            repository, candidate_id, domain_policy, outcome.failure_reason or outcome.status, compute_candidate_backoff
        )

    # Per the frozen cache-hit rule: a from_cache=True page carries no
    # content and must not be fed into extraction.
    fresh_pages = [p for p in outcome.pages if not p.from_cache]

    extraction = extractor.extract(fresh_pages)
    # detect() needs a structured-data hint; ExtractionResult doesn't
    # expose structured_data.extract()'s output separately from
    # business_fields, so business_fields is used as the closest
    # available proxy — see delivery notes.
    detection = detector.detect(fresh_pages, extraction.business_fields)
    normalized = normalizer.normalize(extraction, detection)
    candidate_matches = repository.get_candidate_matches(normalized)
    resolution = resolver.resolve(normalized, candidate_matches)
    validated = validator.validate(normalized, resolution)

    try:
        repository.save_validated_record(validated)
    except StorageIntegrityError:
        repository.mark_candidate_abandoned(candidate_id, "duplicate_domain")
        return "abandoned"

    return "success"


# --- email_verification_job ---------------------------------------------------

def email_verification_job(
    batch_limit: int,
    repository: PostgresRepository,
    suppression_client: SuppressionClient,
    verifier: EmailVerifier,
    lease_minutes: int = 15,
) -> dict:
    """Per scraper_job_state_definitions.md email_verification_job.
    Returns per-run counts for observability/testing.

    A SuppressionServiceUnavailable from suppression_client.is_suppressed
    is deliberately NOT caught here — it propagates and aborts the
    remainder of the batch, per the frozen "aborts the entire batch
    before any provider calls" rule. Contacts already resolved earlier
    in this same loop keep their result, since each is its own
    committed transaction.
    """
    claimed = repository.claim_unverified_contacts(batch_limit, lease_minutes=lease_minutes)

    summary = {"verified": 0, "skipped_suppressed": 0, "retried": 0, "exhausted": 0}
    for contact in claimed:
        if suppression_client.is_suppressed(contact.email):
            summary["skipped_suppressed"] += 1
            continue

        try:
            result = verifier.verify(contact.email)
        except (VerificationProviderUnavailable, VerificationQuotaExceeded) as exc:
            attempt_number = contact.verification_attempts + 1
            reason = str(exc) or exc.__class__.__name__
            if attempt_number >= MAX_VERIFICATION_ATTEMPTS:
                repository.record_verification_failure(contact.contact_id, reason, None)
                summary["exhausted"] += 1
            else:
                next_attempt = datetime.now(timezone.utc) + compute_verification_backoff(attempt_number)
                repository.record_verification_failure(contact.contact_id, reason, next_attempt)
                summary["retried"] += 1
            continue

        repository.save_verification_success(contact.contact_id, result)
        summary["verified"] += 1

    return summary


# --- export_job ----------------------------------------------------------------

def export_job(
    filters: ExportFilters,
    fmt: str,
    repository: PostgresRepository,
    suppression_client: SuppressionClient,
    build_eligible_list: Callable[[ExportFilters, SuppressionClient, PostgresRepository], List[EligibleContact]],
    write_export: Callable[[List[EligibleContact], str], str],
) -> str:
    """Per scraper_job_state_definitions.md export_job. Suppression
    filtering is delegated to build_eligible_list (export/eligibility.py's
    job, not this orchestration layer's) — if it raises
    SuppressionServiceUnavailable, that propagates here and nothing gets
    written or marked, which is the frozen fail-closed behavior.
    File-first: mark_contacts_exported only runs after write_export
    returns successfully.
    """
    eligible = build_eligible_list(filters, suppression_client, repository)
    path = write_export(eligible, fmt)
    repository.mark_contacts_exported([c.contact_id for c in eligible])
    return path


# --- retry / requeue sweep ------------------------------------------------------

def requeue_retry_candidates(
    repository: PostgresRepository,
    enqueue_crawl_candidate: Callable[[int], None],
    limit: int = 100,
) -> List[int]:
    """Per the Scheduling notes in scraper_job_state_definitions.md —
    the lightweight periodic sweep used instead of rq-scheduler."""
    due_ids = repository.get_retry_due_candidates(limit)
    for candidate_id in due_ids:
        enqueue_crawl_candidate(candidate_id)
    return due_ids


# --- RQ entrypoints --------------------------------------------------------------
#
# Minimal signatures matching the frozen job contracts' documented
# input arguments. These are what get registered with RQ. Each one
# resolves its own dependencies rather than accepting them as RQ job
# arguments (RQ arguments must be serializable; live collaborator
# objects aren't a good fit for that even once they exist).

def discovery_job_entrypoint(provider_name: str, criteria: DiscoveryCriteria) -> List[int]:
    raise NotImplementedError(
        "discovery_job_entrypoint requires a DiscoveryProvider registry, which "
        "doesn't exist yet (no discovery providers are implemented). Call "
        "discovery_job() directly with an explicit provider until then."
    )


def crawl_candidate_job_entrypoint(candidate_id: int) -> str:
    raise NotImplementedError(
        "crawl_candidate_job_entrypoint requires the crawler, extraction, "
        "detection, normalization, entity_resolution, and validation modules, "
        "none of which are implemented yet. Call crawl_candidate_job() directly "
        "with explicit dependencies until then."
    )


def email_verification_job_entrypoint(batch_limit: int = 200) -> dict:
    raise NotImplementedError(
        "email_verification_job_entrypoint requires a real EmailVerifier and "
        "SuppressionClient, neither implemented yet. Call email_verification_job() "
        "directly with explicit dependencies until then."
    )


def export_job_entrypoint(filters: ExportFilters, fmt: str = "csv") -> str:
    raise NotImplementedError(
        "export_job_entrypoint requires a real SuppressionClient and export "
        "writer, neither implemented yet. Call export_job() directly with "
        "explicit dependencies until then."
    )


def requeue_retry_candidates_entrypoint(limit: int = 100) -> List[int]:
    """The one entrypoint that's fully real today — only depends on the
    repository and the queue module, both of which exist."""
    from scraper.orchestration.queue import enqueue_crawl_candidate

    repository = PostgresRepository()
    return requeue_retry_candidates(repository, enqueue_crawl_candidate, limit=limit)
