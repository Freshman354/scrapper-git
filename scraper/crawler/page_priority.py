"""Page classification and selection — which internal pages get
crawled, and in what order. Pure functions, no I/O."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

# (page_type, priority, path pattern) — order matters, first match wins
_PRIORITY_RULES: List[Tuple[str, float, "re.Pattern"]] = [
    ("contact", 0.95, re.compile(r"/(contact|contact-us)/?$", re.IGNORECASE)),
    ("about", 0.90, re.compile(r"/(about|about-us)/?$", re.IGNORECASE)),
    ("products", 0.85, re.compile(r"/(products?)(/|$)", re.IGNORECASE)),
    ("collection", 0.80, re.compile(r"/(collections?|categories?)(/|$)", re.IGNORECASE)),
    ("blog", 0.30, re.compile(r"/(blogs?|news)(/|$)", re.IGNORECASE)),
    ("privacy", 0.10, re.compile(r"/(privacy|terms|legal)", re.IGNORECASE)),
]

DEFAULT_PRIORITY = 0.10
HOMEPAGE_PRIORITY = 1.0


def classify_page_type(url: str) -> str:
    path = urlparse(url).path
    for page_type, _priority, pattern in _PRIORITY_RULES:
        if pattern.search(path):
            return page_type
    return "other"


def page_priority(url: str) -> float:
    path = urlparse(url).path
    if path in ("", "/"):
        return HOMEPAGE_PRIORITY
    for _page_type, priority, pattern in _PRIORITY_RULES:
        if pattern.search(path):
            return priority
    return DEFAULT_PRIORITY


def select_priority_pages(seed_url: str, links: List[Optional[str]], limit: int) -> List[str]:
    """Resolve homepage links against seed_url, dedupe, drop the
    homepage itself and any external-domain links, then return up to
    `limit` URLs ordered by priority (highest first)."""
    if limit <= 0:
        return []
    seed_domain = urlparse(seed_url).netloc
    seed_scheme = urlparse(seed_url).scheme or "https"

    resolved = set()
    for link in links:
        if not link:
            continue
        absolute = urljoin(seed_url, link)
        parsed = urlparse(absolute)
        if parsed.netloc and parsed.netloc != seed_domain:
            continue  # external link
        if parsed.path in ("", "/"):
            continue  # homepage itself, already fetched
        normalized = f"{seed_scheme}://{seed_domain}{parsed.path}"
        resolved.add(normalized)

    ranked = sorted(resolved, key=page_priority, reverse=True)
    return ranked[:limit]
