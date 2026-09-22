"""Validation / confidence roll-up. See scraper_module_contracts.md S8.

A record failing validation still reaches storage (per the frozen
design — save_validated_record writes regardless of is_valid) so it
stays auditable rather than silently disappearing. This module only
computes the flag and the errors; it never drops the record itself.
"""

from __future__ import annotations

from typing import List

from scraper.domain.types import EntityResolutionResult, NormalizedRecord, ValidatedRecord


def validate(record: NormalizedRecord, resolution: EntityResolutionResult) -> ValidatedRecord:
    errors: List[str] = []

    if not record.business_fields.get("name"):
        errors.append("missing business name")
    if not record.contacts and not record.socials:
        errors.append("no contacts or social profiles found")

    field_confidence: dict = {}
    for f in record.contacts + record.socials:
        field_confidence[f.field_name] = max(field_confidence.get(f.field_name, 0.0), f.confidence)

    return ValidatedRecord(
        normalized_record=record,
        resolution=resolution,
        field_confidence=field_confidence,
        is_valid=(len(errors) == 0),
        validation_errors=errors,
    )
