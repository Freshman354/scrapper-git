"""Normalization: clean/standardize raw extracted values. Pure
transformation, no business-rule decisions. See scraper_module_contracts.md S6.

Uses phonenumbers/email-validator per the frozen allowed-dependencies
list. Invalid values are dropped (logged, non-fatal), matching
NormalizationError's documented "field dropped, not fatal for the
whole record" behavior — no exception actually propagates out of
normalize() for a single bad field.
"""

from __future__ import annotations

from typing import List, Optional

import phonenumbers
from email_validator import EmailNotValidError, validate_email

from scraper.domain.types import DetectionResult, ExtractedField, ExtractionResult, NormalizedRecord

DEFAULT_PHONE_REGION = "US"  # smallest reasonable V1 default; no country signal is reliably available yet


def _pick_best_per_field(fields: List[ExtractedField]) -> dict:
    best: dict = {}
    best_confidence: dict = {}
    for f in fields:
        if f.field_name not in best or f.confidence > best_confidence[f.field_name]:
            best[f.field_name] = f.field_value
            best_confidence[f.field_name] = f.confidence
    return best


def _normalize_email(value: str) -> Optional[str]:
    try:
        result = validate_email(value, check_deliverability=False)
        return result.normalized.lower()
    except EmailNotValidError:
        return None


def _normalize_phone(value: str) -> Optional[str]:
    try:
        parsed = phonenumbers.parse(value, DEFAULT_PHONE_REGION)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize(extraction: ExtractionResult, detection: DetectionResult) -> NormalizedRecord:
    business_fields = _pick_best_per_field(extraction.business_fields)

    normalized_contacts: List[ExtractedField] = []
    for c in extraction.contacts:
        if c.field_name == "email":
            value = _normalize_email(c.field_value)
        elif c.field_name == "phone":
            value = _normalize_phone(c.field_value)
        else:
            value = c.field_value
        if value is None:
            continue  # dropped, non-fatal — matches NormalizationError's documented behavior
        normalized_contacts.append(ExtractedField(c.field_name, value, c.source_url, c.extraction_method, c.confidence))

    return NormalizedRecord(
        canonical_domain=extraction.canonical_domain,
        business_fields=business_fields,
        platform=detection.platform,
        platform_confidence=detection.confidence,
        contacts=normalized_contacts,
        socials=list(extraction.socials),  # URLs pass through as-is for V1
        evidence=list(detection.evidence),  # why the platform was claimed; storage writes these as evidence rows
    )
