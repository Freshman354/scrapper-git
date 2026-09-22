"""Light product/category signal extraction — passes through JSON-LD
Product entries found by structured_data.py. V1's crawl depth (homepage
+ a handful of priority-selected pages) can include individual product
pages when page_priority ranks them highly, but there's no dedicated
product-catalog crawl — so this stays intentionally light rather than
attempting full catalog extraction."""

from __future__ import annotations

from typing import List

from bs4 import BeautifulSoup

from scraper.domain.types import ExtractedField

_PRODUCT_FIELD_NAMES = {"product_name", "category"}


def extract_page(soup: BeautifulSoup, source_url: str, structured_fields: List[ExtractedField]) -> List[ExtractedField]:
    return [f for f in structured_fields if f.field_name in _PRODUCT_FIELD_NAMES]
