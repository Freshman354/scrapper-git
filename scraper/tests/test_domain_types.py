"""Focused tests for scraper/domain/types.py.

Covers construction, required vs. optional fields, and the documented
value vocabularies for fields the frozen contracts describe with a fixed
set of legal values in a comment (e.g. FetchedPage.fetched_via,
CrawlOutcome.status). The vocabulary tests confirm each documented value
constructs successfully — they do NOT assert that other values are
rejected, since domain/types.py does not enforce them (see the module
docstring and the discrepancy notes in the delivery summary).
"""

import dataclasses
from datetime import datetime, timezone

import pytest

from scraper.domain.types import (
    CachedPageRef,
    CandidateDiscovery,
    ContactRef,
    CrawlOutcome,
    CrawlRequest,
    DetectionResult,
    DiscoveryCriteria,
    DomainPolicySnapshot,
    EligibleContact,
    EntityResolutionResult,
    ExistingBusinessSummary,
    ExportFilters,
    ExtractedField,
    ExtractionResult,
    FetchedPage,
    NormalizedRecord,
    ValidatedRecord,
    VerificationResult,
)

NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


# --- ExtractedField ---------------------------------------------------

def test_extracted_field_construction():
    f = ExtractedField(
        field_name="email",
        field_value="hello@example.com",
        source_url="https://example.com/contact",
        extraction_method="mailto",
        confidence=0.98,
    )
    assert f.field_value == "hello@example.com"
    assert f.confidence == 0.98


def test_extracted_field_all_fields_required():
    with pytest.raises(TypeError):
        ExtractedField(field_name="email", field_value="hello@example.com")


# --- DomainPolicySnapshot ----------------------------------------------

def test_domain_policy_snapshot_construction_with_optionals_set():
    p = DomainPolicySnapshot(
        canonical_domain="example.com",
        consecutive_failures=2,
        last_status_code=503,
        last_attempt_at=NOW,
    )
    assert p.consecutive_failures == 2
    assert p.last_status_code == 503


def test_domain_policy_snapshot_optionals_accept_none():
    p = DomainPolicySnapshot(
        canonical_domain="example.com",
        consecutive_failures=0,
        last_status_code=None,
        last_attempt_at=None,
    )
    assert p.last_status_code is None
    assert p.last_attempt_at is None


def test_domain_policy_snapshot_required_fields_enforced():
    with pytest.raises(TypeError):
        DomainPolicySnapshot(canonical_domain="example.com", consecutive_failures=0)


# --- CachedPageRef -------------------------------------------------------

def test_cached_page_ref_construction():
    ref = CachedPageRef(
        url="https://example.com/",
        content_hash="abc123",
        etag='"etag-value"',
        last_modified="Wed, 21 Oct 2015 07:28:00 GMT",
    )
    assert ref.content_hash == "abc123"


def test_cached_page_ref_optionals_accept_none():
    ref = CachedPageRef(url="https://example.com/", content_hash=None, etag=None, last_modified=None)
    assert ref.content_hash is None


# --- FetchedPage -----------------------------------------------------------

@pytest.mark.parametrize("fetched_via", ["http", "browser"])
def test_fetched_page_documented_fetched_via_values(fetched_via):
    page = FetchedPage(
        url="https://example.com/",
        page_type="homepage",
        fetched_via=fetched_via,
        http_status_code=200,
        content_hash="abc123",
        raw_html="<html></html>",
        from_cache=False,
        fetched_at=NOW,
    )
    assert page.fetched_via == fetched_via


def test_fetched_page_cache_hit_has_no_raw_html():
    page = FetchedPage(
        url="https://example.com/",
        page_type="homepage",
        fetched_via="http",
        http_status_code=200,
        content_hash="abc123",
        raw_html=None,
        from_cache=True,
        fetched_at=NOW,
    )
    assert page.from_cache is True
    assert page.raw_html is None


def test_fetched_page_required_fields_enforced():
    with pytest.raises(TypeError):
        FetchedPage(url="https://example.com/", fetched_via="http")


# --- DiscoveryCriteria -----------------------------------------------------

def test_discovery_criteria_defaults():
    c = DiscoveryCriteria()
    assert c.location is None
    assert c.category is None
    assert c.platform_hint is None
    assert c.keywords == []
    assert c.urls == []
    assert c.limit == 100


def test_discovery_criteria_default_lists_are_independent_instances():
    a = DiscoveryCriteria()
    b = DiscoveryCriteria()
    a.keywords.append("shopify")
    assert b.keywords == []


def test_discovery_criteria_explicit_values():
    c = DiscoveryCriteria(
        location="Texas",
        category="Clothing",
        platform_hint="shopify",
        keywords=["boutique"],
        urls=["https://example.com"],
        limit=50,
    )
    assert c.location == "Texas"
    assert c.limit == 50


# --- CandidateDiscovery -----------------------------------------------------

def test_candidate_discovery_construction():
    cd = CandidateDiscovery(
        raw_url="https://example.com/",
        source="search",
        name_hint="Example Store",
        discovery_metadata={"rank": 3},
    )
    assert cd.source == "search"


def test_candidate_discovery_name_hint_accepts_none_but_is_required():
    cd = CandidateDiscovery(
        raw_url="https://example.com/",
        source="url_import",
        name_hint=None,
        discovery_metadata={},
    )
    assert cd.name_hint is None
    with pytest.raises(TypeError):
        CandidateDiscovery(raw_url="https://example.com/", source="url_import", discovery_metadata={})


# --- CrawlRequest --------------------------------------------------------

def test_crawl_request_default_max_pages():
    policy = DomainPolicySnapshot("example.com", 0, None, None)
    req = CrawlRequest(
        canonical_domain="example.com",
        seed_url="https://example.com/",
        domain_policy=policy,
        cached_pages=[],
    )
    assert req.max_pages == 10


def test_crawl_request_explicit_max_pages():
    policy = DomainPolicySnapshot("example.com", 0, None, None)
    req = CrawlRequest(
        canonical_domain="example.com",
        seed_url="https://example.com/",
        domain_policy=policy,
        cached_pages=[],
        max_pages=25,
    )
    assert req.max_pages == 25


# --- CrawlOutcome --------------------------------------------------------

@pytest.mark.parametrize("status", ["success", "failed", "blocked", "timeout"])
def test_crawl_outcome_documented_status_values(status):
    policy = DomainPolicySnapshot("example.com", 0, None, None)
    outcome = CrawlOutcome(
        canonical_domain="example.com",
        status=status,
        pages=[],
        used_browser=False,
        updated_domain_policy=policy,
        failure_reason=None,
    )
    assert outcome.status == status


def test_crawl_outcome_required_fields_enforced():
    with pytest.raises(TypeError):
        CrawlOutcome(canonical_domain="example.com", status="success")


# --- ExtractionResult / DetectionResult / NormalizedRecord ---------------

def test_extraction_result_construction():
    contact_field = ExtractedField("email", "hello@example.com", "https://example.com/contact", "mailto", 0.9)
    result = ExtractionResult(
        canonical_domain="example.com",
        business_fields=[],
        contacts=[contact_field],
        socials=[],
        products=[],
    )
    assert result.contacts == [contact_field]


def test_detection_result_construction():
    evidence = ExtractedField("platform", "shopify", "https://example.com/", "shopify-cdn-asset", 0.97)
    result = DetectionResult(
        canonical_domain="example.com",
        platform="shopify",
        confidence=0.97,
        evidence=[evidence],
    )
    assert result.platform == "shopify"


def test_normalized_record_construction():
    record = NormalizedRecord(
        canonical_domain="example.com",
        business_fields={"name": "Example Store"},
        platform="shopify",
        platform_confidence=0.97,
        contacts=[],
        socials=[],
    )
    assert record.business_fields["name"] == "Example Store"


# --- ExistingBusinessSummary / EntityResolutionResult ---------------------

def test_existing_business_summary_construction():
    summary = ExistingBusinessSummary(
        business_id=1,
        canonical_domain="example.com",
        name="Example Store",
        contacts=["hello@example.com"],
    )
    assert summary.business_id == 1


@pytest.mark.parametrize("action", ["create_new", "auto_merge", "flag_possible_duplicate"])
def test_entity_resolution_result_documented_action_values(action):
    result = EntityResolutionResult(
        action=action, target_business_id=None, match_score=None, match_level=None,
    )
    assert result.action == action


@pytest.mark.parametrize("match_level", ["strong", "medium", "weak"])
def test_entity_resolution_result_documented_match_level_values(match_level):
    result = EntityResolutionResult(
        action="flag_possible_duplicate", target_business_id=5, match_score=0.85, match_level=match_level,
    )
    assert result.match_level == match_level


def test_entity_resolution_result_optionals_accept_none():
    result = EntityResolutionResult(
        action="create_new", target_business_id=None, match_score=None, match_level=None,
    )
    assert result.target_business_id is None
    assert result.match_score is None
    assert result.match_level is None


# --- ValidatedRecord -------------------------------------------------------

def test_validated_record_construction_when_valid():
    record = NormalizedRecord("example.com", {}, "shopify", 0.9, [], [])
    resolution = EntityResolutionResult("create_new", None, None, None)
    validated = ValidatedRecord(
        normalized_record=record,
        resolution=resolution,
        field_confidence={"email": 0.9},
        is_valid=True,
        validation_errors=[],
    )
    assert validated.is_valid is True
    assert validated.validation_errors == []


def test_validated_record_can_hold_validation_errors_when_invalid():
    record = NormalizedRecord("example.com", {}, "unknown", 0.0, [], [])
    resolution = EntityResolutionResult("create_new", None, None, None)
    validated = ValidatedRecord(
        normalized_record=record,
        resolution=resolution,
        field_confidence={},
        is_valid=False,
        validation_errors=["missing business name"],
    )
    assert validated.is_valid is False
    assert "missing business name" in validated.validation_errors


# --- VerificationResult -----------------------------------------------------

@pytest.mark.parametrize("status", ["verified", "risky", "invalid", "unknown"])
def test_verification_result_documented_status_values(status):
    result = VerificationResult(
        email="hello@example.com", status=status, provider="example-provider", verified_at=NOW,
    )
    assert result.status == status


def test_verification_result_required_fields_enforced():
    with pytest.raises(TypeError):
        VerificationResult(email="hello@example.com", status="verified")


# --- ContactRef ---------------------------------------------------------------

def test_contact_ref_construction():
    ref = ContactRef(contact_id=7, email="hello@example.com", verification_attempts=2)
    assert ref.verification_attempts == 2


def test_contact_ref_required_fields_enforced():
    with pytest.raises(TypeError):
        ContactRef(contact_id=7, email="hello@example.com")


# --- ExportFilters --------------------------------------------------------------

def test_export_filters_defaults():
    f = ExportFilters()
    assert f.platform is None
    assert f.state is None
    assert f.category is None
    assert f.limit == 500


def test_export_filters_explicit_values():
    f = ExportFilters(platform="shopify", state="TX", category="Fashion", limit=100)
    assert f.platform == "shopify"
    assert f.limit == 100


# --- EligibleContact ---------------------------------------------------------

def test_eligible_contact_construction():
    contact = EligibleContact(
        business_id=1, contact_id=10, email="hello@example.com", verification_status="verified",
    )
    assert contact.contact_id == 10


def test_eligible_contact_required_fields_enforced():
    with pytest.raises(TypeError):
        EligibleContact(business_id=1, contact_id=10, email="hello@example.com")


# --- General: every type is a plain, framework-independent dataclass -------

def test_all_domain_types_are_dataclasses():
    import scraper.domain.types as types_module

    dataclass_names = [
        "ExtractedField", "DomainPolicySnapshot", "CachedPageRef", "FetchedPage",
        "DiscoveryCriteria", "CandidateDiscovery", "CrawlRequest", "CrawlOutcome",
        "ExtractionResult", "DetectionResult", "NormalizedRecord",
        "ExistingBusinessSummary", "EntityResolutionResult", "ValidatedRecord",
        "ContactRef", "VerificationResult", "ExportFilters", "EligibleContact",
    ]
    for name in dataclass_names:
        cls = getattr(types_module, name)
        assert dataclasses.is_dataclass(cls), f"{name} is not a dataclass"
