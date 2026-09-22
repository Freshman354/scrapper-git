"""Business-identity fields (name, description, address components).

For V1 this passes through whatever structured_data.py already found
for these field names — JSON-LD/OG/meta/title already give reasonable
coverage for real Shopify themes, so no separate HTML heuristic (e.g.
breadcrumb-based category, heading-based name fallback) is implemented
yet. Deliberate scope choice, not a gap: flagged in delivery notes.
"""

from __future__ import annotations

from typing import List

from bs4 import BeautifulSoup

from scraper.domain.types import ExtractedField

_BUSINESS_FIELD_NAMES = {"name", "description", "street_address", "city", "state", "country", "postal_code"}


def extract_page(soup: BeautifulSoup, source_url: str, structured_fields: List[ExtractedField]) -> List[ExtractedField]:
    return [f for f in structured_fields if f.field_name in _BUSINESS_FIELD_NAMES]
