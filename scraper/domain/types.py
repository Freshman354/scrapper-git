"""Shared domain types for the Shopify scraper pipeline.

Framework-independent, plain dataclasses only — no Flask, RQ, Redis, or
database imports. Every module in the pipeline (discovery, crawler,
extraction, detection, processing, verification, storage, export)
exchanges these types instead of ORM rows or framework objects.

Source of truth: scraper_module_contracts.md (frozen). Section markers
below (S1, S2, ...) correspond to that document's numbered modules.
Classes are ordered by dependency (a type never references one defined
below it), which differs from the document's section order but avoids
any need for forward references.

Deliberately NOT included here (see delivery notes):
  - Protocols (DiscoveryProvider, Repository, EmailVerifier,
    SuppressionClient) — these belong to their own modules per the
    frozen package structure, not to domain/types.py.
  - Runtime validation of the documented value vocabularies (e.g.
    FetchedPage.fetched_via) — the frozen contracts type these fields
    as plain `str` with a comment, not an enforced enum.

ContactRef and ExportFilters were added later, once the storage/
repository contracts were formalized (they were left unspecified in
the initial pass).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


# --- Extraction (S4) --------------------------------------------------

@dataclass
class ExtractedField:
    field_name: str            # 'email' | 'phone' | 'instagram' | 'category' | ...
    field_value: str
    source_url: str
    extraction_method: str     # 'json-ld' | 'mailto' | 'regex' | 'og-tag' | ...
    confidence: float


# --- Crawl engine (S2) --------------------------------------------------

@dataclass
class DomainPolicySnapshot:
    canonical_domain: str
    consecutive_failures: int
    last_status_code: Optional[int]
    last_attempt_at: Optional[datetime]


@dataclass
class CachedPageRef:
    url: str
    content_hash: Optional[str]
    etag: Optional[str]
    last_modified: Optional[str]


@dataclass
class FetchedPage:
    url: str
    page_type: Optional[str]      # homepage | contact | about | products | collection | blog | other
    fetched_via: str              # 'http' | 'browser'
    http_status_code: Optional[int]
    content_hash: Optional[str]
    raw_html: Optional[str]       # ALWAYS populated when from_cache=False; ALWAYS None when from_cache=True
    from_cache: bool
    fetched_at: datetime


# --- Discovery (S1) ------------------------------------------------------

@dataclass
class DiscoveryCriteria:
    location: Optional[str] = None
    category: Optional[str] = None
    platform_hint: Optional[str] = None     # e.g. "shopify"
    keywords: List[str] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)   # url_import only
    limit: int = 100


@dataclass
class CandidateDiscovery:
    raw_url: str
    source: str                  # this provider's name
    name_hint: Optional[str]
    discovery_metadata: dict     # provider-specific payload (search rank, directory category...)


# --- Crawl engine, continued (S2) ---------------------------------------

@dataclass
class CrawlRequest:
    canonical_domain: str
    seed_url: str
    domain_policy: DomainPolicySnapshot    # current backoff/failure state — read-only input
    cached_pages: List[CachedPageRef]      # from page_cache, supplied by orchestration
    max_pages: int = 10


@dataclass
class CrawlOutcome:
    canonical_domain: str
    status: str                   # 'success' | 'failed' | 'blocked' | 'timeout'
    pages: List[FetchedPage]
    used_browser: bool
    updated_domain_policy: DomainPolicySnapshot
    failure_reason: Optional[str]


# --- Extraction, continued (S4) ------------------------------------------

@dataclass
class ExtractionResult:
    canonical_domain: str
    business_fields: List[ExtractedField]
    contacts: List[ExtractedField]
    socials: List[ExtractedField]
    products: List[ExtractedField]


# --- Technology detection (S5) -------------------------------------------

@dataclass
class DetectionResult:
    canonical_domain: str
    platform: str
    confidence: float
    evidence: List[ExtractedField]   # field_name='platform', e.g. extraction_method='shopify-cdn-asset'


# --- Normalization (S6) ---------------------------------------------------

@dataclass
class NormalizedRecord:
    canonical_domain: str
    business_fields: dict            # cleaned key -> value
    platform: str
    platform_confidence: float
    contacts: List[ExtractedField]   # value now normalized (lowercased email, E.164 phone)
    socials: List[ExtractedField]
    # Detection's platform evidence (S5): why the platform above was claimed,
    # each entry carrying field_name='platform'. Carried through normalization
    # so storage can write it — it used to be discarded here, leaving the
    # platform a bare label (AUDIT.md violation 2). Defaulted so every
    # construction site predating the field stays valid.
    evidence: List[ExtractedField] = field(default_factory=list)


# --- Entity resolution (S7) -----------------------------------------------

@dataclass
class ExistingBusinessSummary:
    business_id: int
    canonical_domain: str
    name: Optional[str]
    contacts: List[str]    # normalized values, for name+phone matching


@dataclass
class EntityResolutionResult:
    action: str                     # 'create_new' | 'auto_merge' | 'flag_possible_duplicate'
    target_business_id: Optional[int]
    match_score: Optional[float]
    match_level: Optional[str]      # 'strong' | 'medium' | 'weak'


# --- Validation / confidence (S8) ------------------------------------------

@dataclass
class ValidatedRecord:
    normalized_record: NormalizedRecord
    resolution: EntityResolutionResult
    field_confidence: dict            # field_name -> confidence
    is_valid: bool
    validation_errors: List[str]


# --- Email verification (S10) -----------------------------------------------

@dataclass
class ContactRef:
    contact_id: int
    email: str
    verification_attempts: int   # lets the verification job decide exhausted vs. retry


@dataclass
class VerificationResult:
    email: str
    status: str            # 'verified' | 'risky' | 'invalid' | 'unknown'
    provider: str
    verified_at: datetime


# --- Export / eligibility (S11) ---------------------------------------------

@dataclass
class ExportFilters:
    platform: Optional[str] = None
    state: Optional[str] = None
    category: Optional[str] = None
    limit: int = 500


@dataclass
class EligibleContact:
    business_id: int
    contact_id: int
    email: str
    verification_status: str
