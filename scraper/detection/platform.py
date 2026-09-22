"""Technology/platform detection — Shopify, WooCommerce, WordPress,
etc, with confidence and cited evidence. See scraper_module_contracts.md S5.

Scans raw_html directly rather than relying on the structured_data hint
alone: business_info.py's field whitelist (see extraction/business_info.py)
doesn't pass a "generator" meta-tag signal through to
ExtractionResult.business_fields, so relying only on the hint would
leave detect() with nothing useful most of the time. detect() has full
page access anyway per its signature, so it uses that directly; the
structured_data hint is still checked first as a shortcut when a
generator tag IS present in it.
"""

from __future__ import annotations

import re
from typing import List
from urllib.parse import urlparse

from scraper.domain.types import DetectionResult, ExtractedField, FetchedPage

# (platform, [(pattern, confidence, evidence-method), ...])
_PLATFORM_RULES = [
    ("shopify", [
        (re.compile(r"cdn\.shopify\.com", re.IGNORECASE), 0.97, "shopify-cdn-asset"),
        (re.compile(r"Shopify\.shop\s*=", re.IGNORECASE), 0.97, "shopify-js-global"),
        (re.compile(r"myshopify\.com", re.IGNORECASE), 0.90, "myshopify-domain-reference"),
        (re.compile(r'name=["\']shopify-digital-wallet["\']', re.IGNORECASE), 0.90, "shopify-meta-tag"),
    ]),
    ("woocommerce", [
        (re.compile(r"wp-content/plugins/woocommerce", re.IGNORECASE), 0.95, "woocommerce-plugin-path"),
        (re.compile(r"woocommerce", re.IGNORECASE), 0.80, "woocommerce-asset-reference"),
    ]),
    ("wordpress", [
        (re.compile(r'name=["\']generator["\']\s+content=["\']WordPress', re.IGNORECASE), 0.95, "wordpress-generator-tag"),
        (re.compile(r"wp-content/", re.IGNORECASE), 0.75, "wp-content-path"),
    ]),
    ("wix", [(re.compile(r"static\.wixstatic\.com", re.IGNORECASE), 0.95, "wix-static-asset")]),
    ("squarespace", [(re.compile(r"squarespace\.com", re.IGNORECASE), 0.90, "squarespace-asset-reference")]),
    ("webflow", [(re.compile(r"webflow\.com", re.IGNORECASE), 0.90, "webflow-asset-reference")]),
    ("bigcommerce", [(re.compile(r"bigcommerce\.com", re.IGNORECASE), 0.90, "bigcommerce-asset-reference")]),
    ("magento", [(re.compile(r"Magento|/skin/frontend/", re.IGNORECASE), 0.80, "magento-signal")]),
]


def _domain_from_pages(pages: List[FetchedPage]) -> str:
    return urlparse(pages[0].url).netloc if pages else ""


def detect(pages: List[FetchedPage], structured_data: List[ExtractedField]) -> DetectionResult:
    canonical_domain = _domain_from_pages(pages)

    for field in structured_data:
        if field.field_name == "generator":
            for platform, _rules in _PLATFORM_RULES:
                if platform in field.field_value.lower():
                    evidence = [ExtractedField("platform", platform, field.source_url, "generator-meta-tag", 0.95)]
                    return DetectionResult(canonical_domain=canonical_domain, platform=platform, confidence=0.95, evidence=evidence)

    scored_evidence: List[ExtractedField] = []
    for page in pages:
        if not page.raw_html:
            continue
        for platform, rules in _PLATFORM_RULES:
            for pattern, confidence, method in rules:
                if pattern.search(page.raw_html):
                    scored_evidence.append(ExtractedField("platform", platform, page.url, method, confidence))

    if not scored_evidence:
        return DetectionResult(canonical_domain=canonical_domain, platform="unknown", confidence=0.0, evidence=[])

    best = max(scored_evidence, key=lambda e: e.confidence)
    platform_evidence = [e for e in scored_evidence if e.field_value == best.field_value]
    return DetectionResult(canonical_domain=canonical_domain, platform=best.field_value, confidence=best.confidence, evidence=platform_evidence)
