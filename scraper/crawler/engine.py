"""Tiered crawl orchestration for one domain: Tier 1 HTTP first, Tier 2
browser rendering only when Tier 1 signals it's needed, robots.txt
honored, cache hints respected. Returns raw content + fetch metadata
only — no parsing, no persistence. See scraper_module_contracts.md S2.
"""

from __future__ import annotations

import time
import urllib.robotparser
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse

from scraper.crawler import page_priority
from scraper.crawler.browser_fetcher import BrowserSession
from scraper.crawler.http_fetcher import fetch_http
from scraper.crawler.policy import CrawlDomainCircuitOpen, check_circuit, inter_request_delay_seconds
from scraper.domain.types import CachedPageRef, CrawlOutcome, CrawlRequest, DomainPolicySnapshot, FetchedPage

_BLOCKING_STATUS_CODES = {403, 429}


class CrawlTimeout(Exception):
    """Reserved for catastrophic failures where no CrawlOutcome can be
    constructed at all. In practice, per-page timeouts are encoded via
    CrawlOutcome.status='timeout' instead (see delivery notes) — kept
    importable since jobs.py handles both paths."""


class CrawlBlocked(Exception):
    """Reserved the same way as CrawlTimeout — persistent blocks are
    normally encoded via CrawlOutcome.status='blocked' rather than
    raised."""


def _fetch_robots(seed_url: str):
    """Returns (parser, available). Python's RobotFileParser defaults to
    DISALLOW (can_fetch returns False) when read() never succeeds — the
    opposite of the intended "unreachable robots.txt means allow
    everything" behavior. Verified against a real connection failure,
    not assumed: a plain URLError (e.g. connection refused) isn't an
    HTTPError, so robotparser's own internal handling doesn't catch it,
    and an unread parser's can_fetch() defaults to False. Callers must
    check `available` and skip the robots check entirely when False,
    rather than trusting can_fetch() on an unread parser."""
    parsed = urlparse(seed_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
        return parser, True
    except Exception:
        return parser, False  # unreachable — caller treats as "allow everything"


def _extract_links(html: str) -> List[Optional[str]]:
    if not html:
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    return [a.get("href") for a in soup.find_all("a", href=True)]


def _cached_ref_for(url: str, cache_by_url: Dict[str, CachedPageRef]) -> Optional[CachedPageRef]:
    return cache_by_url.get(url)


def _classify_homepage_failure(result) -> str:
    if result.is_timeout:
        return "timeout"
    if result.http_status_code in _BLOCKING_STATUS_CODES:
        return "blocked"
    return "failed"


def crawl(request: CrawlRequest) -> CrawlOutcome:
    check_circuit(request.domain_policy)  # raises CrawlDomainCircuitOpen before any request

    cache_by_url: Dict[str, CachedPageRef] = {ref.url: ref for ref in request.cached_pages}
    robots, robots_available = _fetch_robots(request.seed_url)
    user_agent = "*"

    def _can_fetch(url: str) -> bool:
        return True if not robots_available else robots.can_fetch(user_agent, url)

    if not _can_fetch(request.seed_url):
        return CrawlOutcome(
            canonical_domain=request.canonical_domain, status="blocked", pages=[],
            used_browser=False, updated_domain_policy=request.domain_policy,
            failure_reason="robots.txt disallow",
        )

    homepage_cached = _cached_ref_for(request.seed_url, cache_by_url)
    # require_body: the homepage is this crawl's link source, and a cache
    # hit supplies no body — see http_fetcher.fetch_http.
    homepage_result = fetch_http(request.seed_url, homepage_cached, require_body=True)

    if homepage_result.error is not None and not homepage_result.from_cache:
        return CrawlOutcome(
            canonical_domain=request.canonical_domain,
            status=_classify_homepage_failure(homepage_result),
            pages=[], used_browser=False,
            updated_domain_policy=DomainPolicySnapshot(
                canonical_domain=request.canonical_domain,
                consecutive_failures=request.domain_policy.consecutive_failures,
                last_status_code=homepage_result.http_status_code,
                last_attempt_at=datetime.now(timezone.utc),
            ),
            failure_reason=homepage_result.error,
        )

    pages: List[FetchedPage] = []
    used_browser = False
    browser_session: Optional[BrowserSession] = None

    try:
        if homepage_result.needs_browser:
            browser_session = BrowserSession()
            browser_session.__enter__()
            used_browser = True
            browser_result = browser_session.fetch(request.seed_url)
            homepage_page = FetchedPage(
                url=request.seed_url, page_type="homepage", fetched_via="browser",
                http_status_code=browser_result.http_status_code, content_hash=browser_result.content_hash,
                raw_html=browser_result.raw_html, from_cache=False, fetched_at=datetime.now(timezone.utc),
            )
            homepage_html_for_links = browser_result.raw_html or ""
        else:
            homepage_page = FetchedPage(
                url=request.seed_url, page_type="homepage", fetched_via="http",
                http_status_code=homepage_result.http_status_code, content_hash=homepage_result.content_hash,
                raw_html=homepage_result.raw_html, from_cache=homepage_result.from_cache,
                fetched_at=datetime.now(timezone.utc),
            )
            homepage_html_for_links = homepage_result.raw_html or ""
        pages.append(homepage_page)

        links = _extract_links(homepage_html_for_links)
        candidate_urls = page_priority.select_priority_pages(request.seed_url, links, max(request.max_pages - 1, 0))
        internal_urls = [u for u in candidate_urls if _can_fetch(u)]

        for url in internal_urls:
            time.sleep(inter_request_delay_seconds())
            cached_ref = _cached_ref_for(url, cache_by_url)
            result = fetch_http(url, cached_ref)
            if result.error is not None and not result.from_cache:
                continue  # skip this one page, don't fail the whole crawl over it

            page_type = page_priority.classify_page_type(url)
            if result.needs_browser:
                if browser_session is None:
                    browser_session = BrowserSession()
                    browser_session.__enter__()
                used_browser = True
                browser_result = browser_session.fetch(url)
                pages.append(FetchedPage(
                    url=url, page_type=page_type, fetched_via="browser",
                    http_status_code=browser_result.http_status_code, content_hash=browser_result.content_hash,
                    raw_html=browser_result.raw_html, from_cache=False, fetched_at=datetime.now(timezone.utc),
                ))
            else:
                pages.append(FetchedPage(
                    url=url, page_type=page_type, fetched_via="http",
                    http_status_code=result.http_status_code, content_hash=result.content_hash,
                    raw_html=result.raw_html, from_cache=result.from_cache, fetched_at=datetime.now(timezone.utc),
                ))
    finally:
        if browser_session is not None:
            browser_session.__exit__(None, None, None)

    updated_domain_policy = DomainPolicySnapshot(
        canonical_domain=request.canonical_domain,
        consecutive_failures=request.domain_policy.consecutive_failures,
        last_status_code=homepage_result.http_status_code,
        last_attempt_at=datetime.now(timezone.utc),
    )

    # A crawl that leaves extraction nothing to read is not a success.
    # jobs.py drops from_cache pages before extraction (a cache hit carries
    # no content), so reporting 'success' with no content-bearing page would
    # hand extraction zero pages and, downstream, write a businesses row with
    # an empty canonical_domain while leaving the candidate stuck in
    # 'crawling' (AUDIT.md violation 1). 'failed' instead routes through
    # jobs._handle_retryable_crawl_failure — the retryable path. Pages are
    # kept on the outcome so save_crawl_outcome still records what the crawl
    # actually saw.
    if not any(p.raw_html and not p.from_cache for p in pages):
        return CrawlOutcome(
            canonical_domain=request.canonical_domain, status="failed", pages=pages,
            used_browser=used_browser, updated_domain_policy=updated_domain_policy,
            failure_reason="no extractable pages",
        )

    return CrawlOutcome(
        canonical_domain=request.canonical_domain, status="success", pages=pages,
        used_browser=used_browser, updated_domain_policy=updated_domain_policy,
        failure_reason=None,
    )
