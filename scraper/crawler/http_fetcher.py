"""Tier 1: plain HTTP fetch, with conditional-GET cache support and a
heuristic for whether a page looks JS-rendered and needs Tier 2."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from scraper.domain.types import CachedPageRef

DEFAULT_TIMEOUT_SECONDS = 10
MIN_VISIBLE_TEXT_LENGTH = 50  # below this, treat as likely JS-rendered
USER_AGENT = "Mozilla/5.0 (compatible; LumviScraperBot/1.0; +https://lumvi.net)"


@dataclass
class HttpFetchResult:
    url: str
    http_status_code: Optional[int]
    raw_html: Optional[str]
    content_hash: Optional[str]
    from_cache: bool
    needs_browser: bool
    is_timeout: bool = False
    error: Optional[str] = None


def _hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()


def _visible_text_length(html: str) -> int:
    try:
        soup = BeautifulSoup(html, "lxml")
        return len(soup.get_text(strip=True))
    except Exception:
        return len(html)


def fetch_http(
    url: str,
    cached: Optional[CachedPageRef],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    require_body: bool = False,
) -> HttpFetchResult:
    """require_body=True means the caller needs the response body, which
    the cache path deliberately withholds: both cache-hit exits below
    return raw_html=None by contract. The engine passes it for the
    homepage, which is the link source — a bodyless cache hit there leaves
    no links to follow and nothing for extraction to read, so a re-crawl
    of an unchanged site would come back empty (AUDIT.md violation 1).
    With require_body the request carries no validators and an unchanged
    body is returned as an ordinary fetch (from_cache=False), preserving
    the "from_cache=True never carries a body" invariant jobs.py filters
    on. A stored hash is still compared nowhere else, so no other caller
    is affected."""
    headers = {"User-Agent": USER_AGENT}
    use_cache_hint = cached is not None and not require_body
    if use_cache_hint:
        if cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        return HttpFetchResult(url=url, http_status_code=None, raw_html=None, content_hash=None,
                                from_cache=False, needs_browser=False, is_timeout=True, error=str(exc))
    except requests.RequestException as exc:
        return HttpFetchResult(url=url, http_status_code=None, raw_html=None, content_hash=None,
                                from_cache=False, needs_browser=False, error=str(exc))

    if response.status_code == 304:
        # Reachable under require_body only from a server that answers 304
        # to a request carrying no validators. That is a bodyless hit like
        # any other; engine.crawl turns an outcome with no content-bearing
        # page into a retryable failure rather than a corrupt record.
        return HttpFetchResult(url=url, http_status_code=304, raw_html=None,
                                content_hash=cached.content_hash if cached else None,
                                from_cache=True, needs_browser=False)

    if not response.ok:
        return HttpFetchResult(url=url, http_status_code=response.status_code, raw_html=None,
                                content_hash=None, from_cache=False, needs_browser=False,
                                error=f"HTTP {response.status_code}")

    body = response.text
    content_hash = _hash_body(body)
    if use_cache_hint and cached.content_hash == content_hash:
        # server didn't honor conditional GET, but content is unchanged
        return HttpFetchResult(url=url, http_status_code=response.status_code, raw_html=None,
                                content_hash=content_hash, from_cache=True, needs_browser=False)

    needs_browser = _visible_text_length(body) < MIN_VISIBLE_TEXT_LENGTH
    return HttpFetchResult(url=url, http_status_code=response.status_code, raw_html=body,
                            content_hash=content_hash, from_cache=False, needs_browser=needs_browser)
