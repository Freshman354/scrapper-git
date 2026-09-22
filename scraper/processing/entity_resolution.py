"""Entity resolution: new vs. auto-merge vs. possible-duplicate.
Thresholds match the frozen policy exactly (>=0.95 auto-merge,
0.75-0.94 possible_duplicate, <0.75 create_new). Scores candidates
given to it; never queries storage itself. See scraper_module_contracts.md S7.
"""

from __future__ import annotations

from typing import List, Optional

from scraper.domain.types import EntityResolutionResult, ExistingBusinessSummary, NormalizedRecord

AUTO_MERGE_THRESHOLD = 0.95
POSSIBLE_DUPLICATE_THRESHOLD = 0.75

_STRONG_SCORE = 1.0   # exact canonical_domain match
_MEDIUM_SCORE = 0.85  # shared normalized contact value, different domain


def _score(record: NormalizedRecord, candidate: ExistingBusinessSummary) -> float:
    if record.canonical_domain == candidate.canonical_domain:
        return _STRONG_SCORE
    record_contact_values = {c.field_value for c in record.contacts}
    if record_contact_values & set(candidate.contacts):
        return _MEDIUM_SCORE
    return 0.0


def resolve(record: NormalizedRecord, candidate_matches: List[ExistingBusinessSummary]) -> EntityResolutionResult:
    best_candidate: Optional[ExistingBusinessSummary] = None
    best_score = 0.0
    for candidate in candidate_matches:
        score = _score(record, candidate)
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_candidate is None or best_score < POSSIBLE_DUPLICATE_THRESHOLD:
        return EntityResolutionResult(action="create_new", target_business_id=None, match_score=None, match_level=None)

    if best_score >= AUTO_MERGE_THRESHOLD:
        return EntityResolutionResult(action="auto_merge", target_business_id=best_candidate.business_id,
                                       match_score=best_score, match_level="strong")

    return EntityResolutionResult(action="flag_possible_duplicate", target_business_id=best_candidate.business_id,
                                   match_score=best_score, match_level="medium")
