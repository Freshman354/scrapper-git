"""Tests for detection/platform.py and processing/*.py."""

from datetime import datetime, timezone

from scraper.detection import platform as detection
from scraper.domain.types import (
    DetectionResult,
    EntityResolutionResult,
    ExistingBusinessSummary,
    ExtractedField,
    ExtractionResult,
    FetchedPage,
)
from scraper.processing import entity_resolution, normalization, validation

FETCHED_AT = datetime.now(timezone.utc)


def _page(url, html):
    return FetchedPage(url=url, page_type="homepage", fetched_via="http", http_status_code=200,
                        content_hash="h", raw_html=html, from_cache=False, fetched_at=FETCHED_AT)


# --- detection ---------------------------------------------------------------

def test_detect_shopify_from_cdn_reference():
    html = '<html><body><script src="https://cdn.shopify.com/s/files/1/theme.js"></script></body></html>'
    result = detection.detect([_page("https://store.com/", html)], [])
    assert result.platform == "shopify"
    assert result.confidence == 0.97
    assert result.evidence[0].extraction_method == "shopify-cdn-asset"


def test_detect_wordpress_generator_tag():
    html = '<html><head><meta name="generator" content="WordPress 6.4"></head></html>'
    result = detection.detect([_page("https://store.com/", html)], [])
    assert result.platform == "wordpress"


def test_detect_unknown_when_no_signals():
    html = "<html><body>Just a plain page with nothing special.</body></html>"
    result = detection.detect([_page("https://store.com/", html)], [])
    assert result.platform == "unknown"
    assert result.confidence == 0.0
    assert result.evidence == []


def test_detect_uses_structured_data_generator_hint_first():
    structured = [ExtractedField("generator", "Shopify", "https://store.com/", "meta-tag", 0.55)]
    result = detection.detect([_page("https://store.com/", "<html></html>")], structured)
    assert result.platform == "shopify"
    assert result.confidence == 0.95


def test_detect_picks_highest_confidence_across_multiple_signals():
    html = '<html><body>cdn.shopify.com and also wp-content/ somehow mixed</body></html>'
    result = detection.detect([_page("https://store.com/", html)], [])
    assert result.platform == "shopify"  # 0.97 beats wordpress's 0.75


# --- normalization -------------------------------------------------------------

def test_normalize_lowercases_and_validates_email():
    extraction = ExtractionResult(
        canonical_domain="store.com", business_fields=[],
        contacts=[ExtractedField("email", "Hello@Example.COM", "https://store.com/", "mailto", 0.9)],
        socials=[], products=[],
    )
    detection_result = DetectionResult("store.com", "shopify", 0.9, [])
    normalized = normalization.normalize(extraction, detection_result)
    assert normalized.contacts[0].field_value == "hello@example.com"


def test_normalize_drops_invalid_email():
    extraction = ExtractionResult(
        canonical_domain="store.com", business_fields=[],
        contacts=[ExtractedField("email", "not-an-email", "https://store.com/", "regex", 0.5)],
        socials=[], products=[],
    )
    normalized = normalization.normalize(extraction, DetectionResult("store.com", "unknown", 0.0, []))
    assert normalized.contacts == []


def test_normalize_formats_phone_to_e164():
    extraction = ExtractionResult(
        canonical_domain="store.com", business_fields=[],
        contacts=[ExtractedField("phone", "(202) 456-1111", "https://store.com/", "tel", 0.95)],
        socials=[], products=[],
    )
    normalized = normalization.normalize(extraction, DetectionResult("store.com", "unknown", 0.0, []))
    assert normalized.contacts[0].field_value == "+12024561111"


def test_normalize_drops_invalid_phone():
    extraction = ExtractionResult(
        canonical_domain="store.com", business_fields=[],
        contacts=[ExtractedField("phone", "123", "https://store.com/", "regex", 0.5)],
        socials=[], products=[],
    )
    normalized = normalization.normalize(extraction, DetectionResult("store.com", "unknown", 0.0, []))
    assert normalized.contacts == []


def test_normalize_picks_highest_confidence_business_field():
    extraction = ExtractionResult(
        canonical_domain="store.com",
        business_fields=[
            ExtractedField("name", "Low Confidence Name", "https://store.com/", "title-tag", 0.55),
            ExtractedField("name", "High Confidence Name", "https://store.com/", "json-ld", 0.92),
        ],
        contacts=[], socials=[], products=[],
    )
    normalized = normalization.normalize(extraction, DetectionResult("store.com", "unknown", 0.0, []))
    assert normalized.business_fields["name"] == "High Confidence Name"


def test_normalize_carries_platform_from_detection():
    extraction = ExtractionResult("store.com", [], [], [], [])
    normalized = normalization.normalize(extraction, DetectionResult("store.com", "shopify", 0.97, []))
    assert normalized.platform == "shopify"
    assert normalized.platform_confidence == 0.97


def test_normalize_carries_detection_evidence_onto_the_record():
    """Violation 2 regression: the evidence showing *why* a platform was
    claimed must survive normalization. Dropping it here leaves storage
    able to write only a bare platform label."""
    html = '<html><body><script src="https://cdn.shopify.com/s/files/1/theme.js"></script></body></html>'
    detection_result = detection.detect([_page("https://store.com/", html)], [])
    extraction = ExtractionResult("store.com", [], [], [], [])

    normalized = normalization.normalize(extraction, detection_result)

    assert normalized.evidence == detection_result.evidence
    assert len(normalized.evidence) == 1
    assert normalized.evidence[0].field_name == "platform"
    assert normalized.evidence[0].field_value == "shopify"
    assert normalized.evidence[0].source_url == "https://store.com/"
    assert normalized.evidence[0].extraction_method == "shopify-cdn-asset"
    assert normalized.evidence[0].confidence == 0.97


# --- entity_resolution ----------------------------------------------------------

def _record(domain="store.com", email="hello@store.com"):
    from scraper.domain.types import NormalizedRecord
    contacts = [ExtractedField("email", email, f"https://{domain}/", "mailto", 0.9)] if email else []
    return NormalizedRecord(domain, {"name": "Store"}, "shopify", 0.9, contacts, [])


def test_resolve_create_new_when_no_matches():
    result = entity_resolution.resolve(_record(), [])
    assert result.action == "create_new"
    assert result.target_business_id is None


def test_resolve_auto_merge_on_exact_domain_match():
    candidate = ExistingBusinessSummary(business_id=1, canonical_domain="store.com", name="Store", contacts=[])
    result = entity_resolution.resolve(_record(domain="store.com"), [candidate])
    assert result.action == "auto_merge"
    assert result.target_business_id == 1
    assert result.match_score == 1.0


def test_resolve_flags_possible_duplicate_on_shared_contact_different_domain():
    candidate = ExistingBusinessSummary(business_id=2, canonical_domain="other.com", name="Other",
                                         contacts=["hello@store.com"])
    result = entity_resolution.resolve(_record(domain="store.com", email="hello@store.com"), [candidate])
    assert result.action == "flag_possible_duplicate"
    assert result.target_business_id == 2
    assert result.match_level == "medium"


def test_resolve_picks_best_of_multiple_candidates():
    weak = ExistingBusinessSummary(business_id=3, canonical_domain="unrelated.com", name="X", contacts=[])
    strong = ExistingBusinessSummary(business_id=4, canonical_domain="store.com", name="Store", contacts=[])
    result = entity_resolution.resolve(_record(domain="store.com"), [weak, strong])
    assert result.target_business_id == 4


# --- validation -----------------------------------------------------------------

def test_validate_valid_record():
    record = _record()
    resolution = EntityResolutionResult("create_new", None, None, None)
    validated = validation.validate(record, resolution)
    assert validated.is_valid is True
    assert validated.validation_errors == []


def test_validate_flags_missing_name():
    from scraper.domain.types import NormalizedRecord
    record = NormalizedRecord("store.com", {}, "shopify", 0.9,
                               [ExtractedField("email", "a@store.com", "https://store.com/", "mailto", 0.9)], [])
    resolution = EntityResolutionResult("create_new", None, None, None)
    validated = validation.validate(record, resolution)
    assert validated.is_valid is False
    assert "missing business name" in validated.validation_errors


def test_validate_flags_no_contacts_or_socials():
    from scraper.domain.types import NormalizedRecord
    record = NormalizedRecord("store.com", {"name": "Store"}, "shopify", 0.9, [], [])
    resolution = EntityResolutionResult("create_new", None, None, None)
    validated = validation.validate(record, resolution)
    assert validated.is_valid is False
    assert "no contacts or social profiles found" in validated.validation_errors


def test_validate_still_returns_record_even_when_invalid():
    """Per the frozen contract: invalid records still flow to storage,
    marked invalid, not silently dropped."""
    from scraper.domain.types import NormalizedRecord
    record = NormalizedRecord("store.com", {}, "unknown", 0.0, [], [])
    resolution = EntityResolutionResult("create_new", None, None, None)
    validated = validation.validate(record, resolution)
    assert validated.normalized_record is record
    assert validated.is_valid is False
