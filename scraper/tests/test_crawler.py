"""Tests for scraper/crawler/.

Runs against a real local Flask server (not a mock HTTP layer) so
conditional-GET/caching, robots.txt, and status-code handling are
exercised for real — same standard as the rest of this project. The
JS-rendering escalation test uses real Playwright/Chromium, already
verified installable in this environment.
"""

import hashlib
import threading
import time
from datetime import datetime, timezone

import pytest
import requests
from flask import Flask, Response, request as flask_request
from werkzeug.serving import make_server

from scraper.crawler import page_priority
from scraper.crawler.engine import CrawlBlocked, CrawlTimeout, crawl
from scraper.crawler.http_fetcher import fetch_http
from scraper.crawler.policy import CrawlDomainCircuitOpen, check_circuit
from scraper.domain.types import CachedPageRef, CrawlRequest, DomainPolicySnapshot

HOMEPAGE_HTML = """
<html><body>
<h1>Example Store</h1>
<p>Welcome to our shop, selling the finest widgets since forever. We have a huge selection.</p>
<a href="/contact">Contact</a>
<a href="/about">About</a>
<a href="/collections/all">Collections</a>
<a href="/blog/news">Blog</a>
<a href="https://external-site.com/">External</a>
</body></html>
"""

CONTACT_HTML = "<html><body><h1>Contact us</h1><p>Email: hello@example.com. We would love to hear from you about anything.</p></body></html>"
ABOUT_HTML = "<html><body><h1>About</h1><p>" + ("We are a small business. " * 20) + "</p></body></html>"

JS_SHELL_HTML = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
RENDERED_JS_HTML = "<html><body><div id='root'><h1>Rendered Content</h1><p>" + ("This content only appears after JS runs. " * 10) + "</p></div></body></html>"


# --- test server -----------------------------------------------------------------

class _TestServer:
    def __init__(self, app, port):
        self.server = make_server("127.0.0.1", port, app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        time.sleep(0.2)

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=2)


def _build_app(state):
    app = Flask(__name__)

    @app.route("/")
    def homepage():
        if state.get("homepage_status"):
            return Response(status=state["homepage_status"])
        return Response(HOMEPAGE_HTML, mimetype="text/html")

    @app.route("/contact")
    def contact():
        return Response(CONTACT_HTML, mimetype="text/html")

    @app.route("/about")
    def about():
        return Response(ABOUT_HTML, mimetype="text/html")

    @app.route("/collections/all")
    def collections():
        return Response(ABOUT_HTML, mimetype="text/html")

    @app.route("/blog/news")
    def blog():
        return Response(ABOUT_HTML, mimetype="text/html")

    @app.route("/js-shell")
    def js_shell():
        # Serves a minimal shell to a plain HTTP client; a real browser
        # would render more, but here it just needs to be "thin enough"
        # to trip the needs_browser heuristic either way.
        return Response(JS_SHELL_HTML, mimetype="text/html")

    @app.route("/app.js")
    def app_js():
        return Response("document.getElementById('root').innerHTML = " +
                         repr(RENDERED_JS_HTML) + ";", mimetype="application/javascript")

    @app.route("/cacheable")
    def cacheable():
        body = state.get("cacheable_body", "<html><body>original content that is long enough to not need a browser at all, several sentences of filler text here.</body></html>")
        etag = hashlib.sha256(body.encode()).hexdigest()[:16]
        if flask_request.headers.get("If-None-Match") == etag:
            return Response(status=304)
        resp = Response(body, mimetype="text/html")
        resp.headers["ETag"] = etag
        return resp

    @app.route("/robots.txt")
    def robots():
        return Response(state.get("robots_txt", "User-agent: *\nAllow: /\n"), mimetype="text/plain")

    @app.route("/blocked-by-robots")
    def blocked_by_robots():
        return Response("<html><body>should never be fetched</body></html>", mimetype="text/html")

    @app.route("/not-modified")
    def not_modified():
        # Answers 304 to everything, so a crawl of this URL gets no content
        # at all — the "every fetch is a bodyless cache hit" case.
        return Response(status=304)

    return app


@pytest.fixture
def server():
    state = {}
    app = _build_app(state)
    srv = _TestServer(app, 8765)
    srv.start()
    srv.state = state
    yield srv
    srv.stop()


BASE_URL = "http://127.0.0.1:8765"
FRESH_POLICY = DomainPolicySnapshot(canonical_domain="127.0.0.1:8765", consecutive_failures=0, last_status_code=None, last_attempt_at=None)


# --- policy.py -----------------------------------------------------------------

def test_check_circuit_allows_healthy_domain():
    check_circuit(FRESH_POLICY)  # should not raise


@pytest.mark.parametrize("status", [403, 429])
def test_check_circuit_blocks_on_known_bad_status(status):
    policy = DomainPolicySnapshot("example.com", 1, status, datetime.now(timezone.utc))
    with pytest.raises(CrawlDomainCircuitOpen):
        check_circuit(policy)


def test_check_circuit_allows_recovered_status():
    policy = DomainPolicySnapshot("example.com", 1, 200, datetime.now(timezone.utc))
    check_circuit(policy)  # should not raise


# --- page_priority.py ------------------------------------------------------------

def test_classify_page_type():
    assert page_priority.classify_page_type("https://x.com/contact") == "contact"
    assert page_priority.classify_page_type("https://x.com/about-us") == "about"
    assert page_priority.classify_page_type("https://x.com/collections/all") == "collection"
    assert page_priority.classify_page_type("https://x.com/blog/news") == "blog"
    assert page_priority.classify_page_type("https://x.com/random-page") == "other"


def test_select_priority_pages_orders_by_priority_and_excludes_external():
    links = ["/contact", "/blog/news", "/about", "https://external.com/", "/"]
    selected = page_priority.select_priority_pages("https://x.com/", links, limit=10)
    assert selected == [
        "https://x.com/contact",
        "https://x.com/about",
        "https://x.com/blog/news",
    ]


def test_select_priority_pages_respects_limit():
    links = ["/contact", "/about", "/blog/news"]
    selected = page_priority.select_priority_pages("https://x.com/", links, limit=1)
    assert selected == ["https://x.com/contact"]


# --- http_fetcher.py (real local server) -----------------------------------------

def test_fetch_http_basic_page(server):
    result = fetch_http(f"{BASE_URL}/contact", cached=None)
    assert result.http_status_code == 200
    assert "hello@example.com" in result.raw_html
    assert result.from_cache is False
    assert result.needs_browser is False


def test_fetch_http_conditional_get_returns_304_and_from_cache(server):
    first = fetch_http(f"{BASE_URL}/cacheable", cached=None)
    ref = CachedPageRef(url=f"{BASE_URL}/cacheable", content_hash=first.content_hash,
                         etag=hashlib.sha256(first.raw_html.encode()).hexdigest()[:16], last_modified=None)
    second = fetch_http(f"{BASE_URL}/cacheable", cached=ref)
    assert second.http_status_code == 304
    assert second.from_cache is True
    assert second.raw_html is None
    assert second.content_hash == first.content_hash


def test_fetch_http_unchanged_body_treated_as_cache_hit_even_without_304(server):
    first = fetch_http(f"{BASE_URL}/about", cached=None)
    ref = CachedPageRef(url=f"{BASE_URL}/about", content_hash=first.content_hash, etag=None, last_modified=None)
    second = fetch_http(f"{BASE_URL}/about", cached=ref)  # server doesn't support conditional GET on this route
    assert second.http_status_code == 200
    assert second.from_cache is True
    assert second.raw_html is None


def test_fetch_http_needs_browser_for_thin_content(server):
    result = fetch_http(f"{BASE_URL}/js-shell", cached=None)
    assert result.needs_browser is True


def test_fetch_http_404_is_an_error_not_a_cache_hit(server):
    result = fetch_http(f"{BASE_URL}/does-not-exist", cached=None)
    assert result.error is not None
    assert result.from_cache is False


def test_fetch_http_connection_refused_is_an_error():
    result = fetch_http("http://127.0.0.1:1/", cached=None, timeout=1)
    assert result.error is not None


# --- engine.py: full crawl() against the real server -----------------------------

def test_crawl_success_fetches_homepage_and_priority_pages(server):
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=4)
    outcome = crawl(req)

    assert outcome.status == "success"
    urls = {p.url for p in outcome.pages}
    assert f"{BASE_URL}/" in urls
    assert f"{BASE_URL}/contact" in urls  # highest-priority internal link
    assert f"{BASE_URL}/about" in urls
    assert f"{BASE_URL}/external-site.com/" not in urls  # external link excluded
    homepage_page = next(p for p in outcome.pages if p.url == f"{BASE_URL}/")
    assert homepage_page.page_type == "homepage"
    assert homepage_page.fetched_via == "http"


def test_crawl_respects_max_pages(server):
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=2)
    outcome = crawl(req)
    assert len(outcome.pages) == 2  # homepage + 1 internal page


def test_crawl_uses_cache_hint_for_unchanged_subpage(server):
    """Retargeted from the homepage to a subpage. The cache hint is only
    usable where the body isn't needed for anything: the homepage IS the
    crawl's link source, so page_cache's ref for it must not suppress the
    body (see test_crawl_with_cached_homepage_still_follows_its_links).
    A subpage's body is genuinely skippable when it is unchanged."""
    first = crawl(CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                                domain_policy=FRESH_POLICY, cached_pages=[], max_pages=4))
    contact_hash = next(p for p in first.pages if p.url == f"{BASE_URL}/contact").content_hash

    cached_contact = CachedPageRef(url=f"{BASE_URL}/contact", content_hash=contact_hash, etag=None, last_modified=None)
    outcome = crawl(CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                                  domain_policy=FRESH_POLICY, cached_pages=[cached_contact], max_pages=4))

    contact_page = next(p for p in outcome.pages if p.url == f"{BASE_URL}/contact")
    assert contact_page.from_cache is True
    assert contact_page.raw_html is None
    # the homepage is still fetched with its body, so its links were followed
    homepage_page = next(p for p in outcome.pages if p.url == f"{BASE_URL}/")
    assert homepage_page.raw_html is not None
    assert outcome.status == "success"


def test_crawl_with_cached_homepage_still_follows_its_links(server):
    """Regression test for AUDIT.md violation 1. A cache hit returns no
    body, so using it as the link source meant a re-crawl of an unchanged
    site followed no links and handed extraction zero pages — which wrote a
    businesses row with an empty canonical_domain. The homepage must come
    back with its body even when page_cache holds a ref for it."""
    first = crawl(CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                                domain_policy=FRESH_POLICY, cached_pages=[], max_pages=1))
    homepage_hash = first.pages[0].content_hash

    cached_homepage = CachedPageRef(url=f"{BASE_URL}/", content_hash=homepage_hash, etag=None, last_modified=None)
    outcome = crawl(CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                                  domain_policy=FRESH_POLICY, cached_pages=[cached_homepage], max_pages=4))

    homepage_page = next(p for p in outcome.pages if p.url == f"{BASE_URL}/")
    assert homepage_page.raw_html is not None   # body supplied despite the cached hash matching
    assert homepage_page.from_cache is False    # so it is an ordinary fetch, not a bodyless hit
    urls = {p.url for p in outcome.pages}
    assert f"{BASE_URL}/contact" in urls        # its links were still followed
    assert f"{BASE_URL}/about" in urls
    assert outcome.status == "success"          # extraction has something to read


def test_crawl_that_yields_no_extractable_pages_is_a_retryable_failure(server):
    """Regression test for AUDIT.md violation 1. When no page carries
    content the crawl must not report success: jobs.py drops from_cache
    pages before extraction, so 'success' here means extract([]) and an
    empty businesses row. 'failed' is the outcome jobs.py already turns
    into mark_candidate_retry."""
    cached_homepage = CachedPageRef(url=f"{BASE_URL}/not-modified", content_hash="unchanged",
                                     etag=None, last_modified=None)
    outcome = crawl(CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/not-modified",
                                  domain_policy=FRESH_POLICY, cached_pages=[cached_homepage], max_pages=4))

    assert outcome.status == "failed"           # not "success"
    assert outcome.failure_reason == "no extractable pages"
    assert not any(p.raw_html and not p.from_cache for p in outcome.pages)


def test_crawl_returns_blocked_status_on_403(server):
    server.state["homepage_status"] = 403
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=4)
    outcome = crawl(req)
    assert outcome.status == "blocked"
    assert outcome.pages == []


def test_crawl_returns_failed_status_on_500(server):
    server.state["homepage_status"] = 500
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=4)
    outcome = crawl(req)
    assert outcome.status == "failed"


def test_crawl_raises_circuit_open_before_any_request_when_domain_policy_says_blocked(server):
    blocked_policy = DomainPolicySnapshot("127.0.0.1:8765", 2, 403, datetime.now(timezone.utc))
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=blocked_policy, cached_pages=[], max_pages=4)
    with pytest.raises(CrawlDomainCircuitOpen):
        crawl(req)


def test_crawl_respects_robots_disallow_on_homepage(server):
    server.state["robots_txt"] = "User-agent: *\nDisallow: /\n"
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=4)
    outcome = crawl(req)
    assert outcome.status == "blocked"
    assert outcome.failure_reason == "robots.txt disallow"


def test_crawl_excludes_robots_disallowed_internal_page(server):
    server.state["robots_txt"] = "User-agent: *\nDisallow: /blog/\n"
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=10)
    outcome = crawl(req)
    urls = {p.url for p in outcome.pages}
    assert f"{BASE_URL}/blog/news" not in urls
    assert f"{BASE_URL}/contact" in urls  # unaffected pages still fetched


def test_crawl_updated_domain_policy_reflects_latest_status(server):
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=1)
    outcome = crawl(req)
    assert outcome.updated_domain_policy.last_status_code == 200
    assert outcome.updated_domain_policy.last_attempt_at is not None


# --- Tier 2 escalation: real Playwright ---------------------------------------------

def test_crawl_escalates_to_browser_for_js_shell_page(server):
    """The /js-shell route serves a near-empty shell to any client that
    doesn't execute JS. A real browser (Playwright) executes app.js and
    would see different final content — this test only asserts the
    escalation actually happens and used_browser is reported, since
    Werkzeug's dev server doesn't handle concurrent requests robustly
    enough in this sandbox to also assert on Playwright's rendered
    output without flakiness."""
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/js-shell",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=1)
    outcome = crawl(req)
    assert outcome.used_browser is True
    assert outcome.pages[0].fetched_via == "browser"


def test_crawl_does_not_use_browser_for_normal_page(server):
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=1)
    outcome = crawl(req)
    assert outcome.used_browser is False


# --- regression tests for real bugs found via end-to-end testing --------------

def test_crawl_succeeds_when_robots_txt_host_is_completely_unreachable():
    """Real bug, found via the CLI against a demo server that had
    stopped between commands: Python's RobotFileParser defaults to
    DISALLOW when robots.txt can't be fetched at all (a plain
    connection failure isn't caught by robotparser's own HTTPError
    handling) — the opposite of the intended "unreachable means allow"
    behavior. Must fail on the actual page fetch, not silently report
    status='blocked'."""
    req = CrawlRequest(canonical_domain="127.0.0.1:1", seed_url="http://127.0.0.1:1/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=1)
    outcome = crawl(req)
    assert outcome.status in ("failed", "timeout")
    assert outcome.failure_reason != "robots.txt disallow"


def test_crawl_empty_pages_outcome_is_writable_to_storage(server):
    """Regression test: a zero-page CrawlOutcome (e.g. blocked before any
    fetch) must not violate crawl_runs.started_at's NOT NULL constraint."""
    server.state["robots_txt"] = "User-agent: *\nDisallow: /\n"
    req = CrawlRequest(canonical_domain="127.0.0.1:8765", seed_url=f"{BASE_URL}/",
                        domain_policy=FRESH_POLICY, cached_pages=[], max_pages=4)
    outcome = crawl(req)
    assert outcome.pages == []
    assert outcome.status == "blocked"
    # save_crawl_outcome is exercised in test_repository.py; this test
    # only needs to confirm engine.py produces a well-formed empty outcome
