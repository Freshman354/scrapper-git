"""Social profile link extraction: known-domain <a href> links plus
JSON-LD sameAs (passed through via structured_fields)."""

from __future__ import annotations

from typing import List, Optional, Set, Tuple

from bs4 import BeautifulSoup

from scraper.domain.types import ExtractedField

LINK_CONFIDENCE = 0.85
JSON_LD_SAMEAS_CONFIDENCE = 0.92

# Matches social_profiles.platform's CHECK constraint exactly
_PLATFORM_DOMAINS = {
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "tiktok.com": "tiktok",
    "linkedin.com": "linkedin",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "pinterest.com": "pinterest",
}


def _platform_for_url(url: str) -> Optional[str]:
    lowered = url.lower()
    for domain, platform in _PLATFORM_DOMAINS.items():
        if domain in lowered:
            return platform
    return None


def extract_page(soup: BeautifulSoup, source_url: str, structured_fields: List[ExtractedField]) -> List[ExtractedField]:
    fields: List[ExtractedField] = []
    seen: Set[Tuple[str, str]] = set()

    for field in structured_fields:
        if field.field_name != "sameAs":
            continue
        platform = _platform_for_url(field.field_value)
        if platform and (platform, field.field_value) not in seen:
            seen.add((platform, field.field_value))
            fields.append(ExtractedField(platform, field.field_value, source_url, "json-ld", JSON_LD_SAMEAS_CONFIDENCE))

    for a in soup.find_all("a", href=True):
        href = a["href"]
        platform = _platform_for_url(href)
        if platform and (platform, href) not in seen:
            seen.add((platform, href))
            fields.append(ExtractedField(platform, href, source_url, "link", LINK_CONFIDENCE))

    return fields
