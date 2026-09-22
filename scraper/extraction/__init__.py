"""Extraction: given fetched pages, extract raw structured signals.

structured_data.py runs first per page; its output is passed to the
other four sub-modules as a hint. Purely functional — HTML in, fields
out, no persistence, no crawling. See scraper_module_contracts.md S4.

The `extract(pages)` function below is the Extractor protocol's single
entry point (`class Extractor(Protocol): def extract(self, pages)`, in
orchestration/jobs.py). Python modules satisfy that structurally: pass
this module object itself as `extractor` to crawl_candidate_job, e.g.
`crawl_candidate_job(..., extractor=scraper.extraction, ...)` — no
wrapper class needed.
"""

from __future__ import annotations

from typing import List
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from scraper.domain.types import ExtractedField, ExtractionResult, FetchedPage
from scraper.extraction import business_info, contacts, products, socials, structured_data


class ExtractionParseError(Exception):
    """Malformed HTML for one page. Non-fatal by design — extract()
    catches this internally and continues with the pages that did
    parse; it is never raised out of extract()."""


def extract(pages: List[FetchedPage]) -> ExtractionResult:
    fresh_pages = [p for p in pages if p.raw_html]
    if not fresh_pages:
        # Degenerate input: in V1's crawl-once model (no re-crawl path
        # yet), jobs.py never actually calls extract([]) — filtered
        # pages are always non-empty in practice. Handled gracefully
        # here rather than crashing, in case that changes later.
        return ExtractionResult(canonical_domain="", business_fields=[], contacts=[], socials=[], products=[])

    canonical_domain = urlparse(fresh_pages[0].url).netloc

    business_fields: List[ExtractedField] = []
    all_contacts: List[ExtractedField] = []
    all_socials: List[ExtractedField] = []
    all_products: List[ExtractedField] = []

    for page in fresh_pages:
        try:
            soup = BeautifulSoup(page.raw_html, "lxml")
        except Exception:
            continue  # ExtractionParseError-worthy; non-fatal, skip this page only

        structured = structured_data.extract_page(soup, page.url)
        business_fields.extend(business_info.extract_page(soup, page.url, structured))
        all_contacts.extend(contacts.extract_page(soup, page.url, structured))
        all_socials.extend(socials.extract_page(soup, page.url, structured))
        all_products.extend(products.extract_page(soup, page.url, structured))

    return ExtractionResult(
        canonical_domain=canonical_domain,
        business_fields=business_fields,
        contacts=all_contacts,
        socials=all_socials,
        products=all_products,
    )
