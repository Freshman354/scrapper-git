"""Structured-data extraction: JSON-LD, OpenGraph, and standard meta
tags. Runs first among the extraction sub-modules; its output is
passed to the others as a hint, per scraper_module_contracts.md S4.
"""

from __future__ import annotations

import json
from typing import List

from bs4 import BeautifulSoup

from scraper.domain.types import ExtractedField

JSON_LD_CONFIDENCE = 0.92
OPENGRAPH_CONFIDENCE = 0.75
META_TAG_CONFIDENCE = 0.55

_JSON_LD_SCALAR_FIELDS = {
    "name": "name",
    "description": "description",
    "email": "email",
    "telephone": "telephone",
}

_JSON_LD_ADDRESS_FIELDS = {
    "streetAddress": "street_address",
    "addressLocality": "city",
    "addressRegion": "state",
    "addressCountry": "country",
    "postalCode": "postal_code",
}


def _fields_from_json_ld_entry(entry: dict, source_url: str) -> List[ExtractedField]:
    fields: List[ExtractedField] = []

    for json_key, field_name in _JSON_LD_SCALAR_FIELDS.items():
        value = entry.get(json_key)
        if isinstance(value, str) and value.strip():
            fields.append(ExtractedField(field_name, value.strip(), source_url, "json-ld", JSON_LD_CONFIDENCE))

    address = entry.get("address")
    if isinstance(address, dict):
        for json_key, field_name in _JSON_LD_ADDRESS_FIELDS.items():
            value = address.get(json_key)
            if isinstance(value, str) and value.strip():
                fields.append(ExtractedField(field_name, value.strip(), source_url, "json-ld", JSON_LD_CONFIDENCE))

    same_as = entry.get("sameAs")
    if same_as:
        urls = same_as if isinstance(same_as, list) else [same_as]
        for url in urls:
            if isinstance(url, str) and url.strip():
                fields.append(ExtractedField("sameAs", url.strip(), source_url, "json-ld", JSON_LD_CONFIDENCE))

    entry_type = entry.get("@type", "")
    types = entry_type if isinstance(entry_type, list) else [entry_type]
    if any(isinstance(t, str) and "Product" in t for t in types):
        product_name = entry.get("name")
        if isinstance(product_name, str) and product_name.strip():
            fields.append(ExtractedField("product_name", product_name.strip(), source_url, "json-ld", JSON_LD_CONFIDENCE))
        category = entry.get("category")
        if isinstance(category, str) and category.strip():
            fields.append(ExtractedField("category", category.strip(), source_url, "json-ld", JSON_LD_CONFIDENCE))

    return fields


def _extract_json_ld(soup: BeautifulSoup, source_url: str) -> List[ExtractedField]:
    fields: List[ExtractedField] = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue  # malformed JSON-LD — non-fatal, skip this block
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if isinstance(entry, dict):
                fields.extend(_fields_from_json_ld_entry(entry, source_url))
    return fields


_OG_PROPERTY_MAP = {"og:title": "name", "og:site_name": "name", "og:description": "description"}


def _extract_opengraph(soup: BeautifulSoup, source_url: str) -> List[ExtractedField]:
    fields = []
    for meta in soup.find_all("meta"):
        prop = meta.get("property")
        content = meta.get("content")
        if prop in _OG_PROPERTY_MAP and content and content.strip():
            fields.append(ExtractedField(_OG_PROPERTY_MAP[prop], content.strip(), source_url, "og-tag", OPENGRAPH_CONFIDENCE))
    return fields


def _extract_meta_tags(soup: BeautifulSoup, source_url: str) -> List[ExtractedField]:
    fields = []
    description = soup.find("meta", attrs={"name": "description"})
    if description and description.get("content", "").strip():
        fields.append(ExtractedField("description", description["content"].strip(), source_url, "meta-tag", META_TAG_CONFIDENCE))
    generator = soup.find("meta", attrs={"name": "generator"})
    if generator and generator.get("content", "").strip():
        fields.append(ExtractedField("generator", generator["content"].strip(), source_url, "meta-tag", META_TAG_CONFIDENCE))
    if soup.title and soup.title.string and soup.title.string.strip():
        fields.append(ExtractedField("name", soup.title.string.strip(), source_url, "title-tag", META_TAG_CONFIDENCE))
    return fields


def extract_page(soup: BeautifulSoup, source_url: str) -> List[ExtractedField]:
    """All structured-data signals on one page, JSON-LD first (highest
    confidence), then OpenGraph, then plain meta/title tags."""
    return _extract_json_ld(soup, source_url) + _extract_opengraph(soup, source_url) + _extract_meta_tags(soup, source_url)
