"""Email and phone extraction: mailto:/tel: links (highest confidence),
JSON-LD (passed through via structured_fields), and a regex fallback
over visible text for emails only. Phone-number regex over free text
was deliberately left out — too many false positives (prices, dates,
SKUs) relative to the value it adds; tel: links and JSON-LD cover the
reliable cases.
"""

from __future__ import annotations

import re
from typing import List, Set, Tuple

from bs4 import BeautifulSoup

from scraper.domain.types import ExtractedField

MAILTO_CONFIDENCE = 0.97
TEL_CONFIDENCE = 0.95
REGEX_EMAIL_CONFIDENCE = 0.6

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def extract_page(soup: BeautifulSoup, source_url: str, structured_fields: List[ExtractedField]) -> List[ExtractedField]:
    fields: List[ExtractedField] = []
    seen: Set[Tuple[str, str]] = set()

    for field in structured_fields:
        if field.field_name not in ("email", "telephone"):
            continue
        name = "email" if field.field_name == "email" else "phone"
        key = (name, field.field_value)
        if key not in seen:
            seen.add(key)
            fields.append(ExtractedField(name, field.field_value, source_url, field.extraction_method, field.confidence))

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?")[0].strip()
            key = ("email", email)
            if email and key not in seen:
                seen.add(key)
                fields.append(ExtractedField("email", email, source_url, "mailto", MAILTO_CONFIDENCE))
        elif href.lower().startswith("tel:"):
            phone = href[4:].strip()
            key = ("phone", phone)
            if phone and key not in seen:
                seen.add(key)
                fields.append(ExtractedField("phone", phone, source_url, "tel", TEL_CONFIDENCE))

    for match in _EMAIL_RE.findall(soup.get_text(" ")):
        key = ("email", match)
        if key not in seen:
            seen.add(key)
            fields.append(ExtractedField("email", match, source_url, "regex", REGEX_EMAIL_CONFIDENCE))

    return fields
