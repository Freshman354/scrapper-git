"""Tier 2: Playwright-rendered fetch, used only when Tier 1 signals a
page is likely JS-rendered (http_fetcher.HttpFetchResult.needs_browser).

One browser process is meant to be reused across every page in a single
crawl() call via BrowserSession, rather than launching fresh per page —
"don't launch a browser unless necessary" applies to launch count too,
not just tier selection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DEFAULT_TIMEOUT_MS = 15000


@dataclass
class BrowserFetchResult:
    url: str
    http_status_code: Optional[int]
    raw_html: Optional[str]
    content_hash: Optional[str]
    error: Optional[str] = None


def _hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()


class BrowserSession:
    """Context manager wrapping one Playwright browser process, reused
    across fetch() calls for multiple pages."""

    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        self._timeout_ms = timeout_ms
        self._playwright = None
        self._browser = None

    def __enter__(self) -> "BrowserSession":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def fetch(self, url: str) -> BrowserFetchResult:
        page = self._browser.new_page()
        try:
            response = page.goto(url, timeout=self._timeout_ms, wait_until="networkidle")
            html = page.content()
            status = response.status if response else None
            return BrowserFetchResult(url=url, http_status_code=status, raw_html=html, content_hash=_hash_body(html))
        except PlaywrightTimeoutError as exc:
            return BrowserFetchResult(url=url, http_status_code=None, raw_html=None, content_hash=None, error=str(exc))
        except Exception as exc:
            return BrowserFetchResult(url=url, http_status_code=None, raw_html=None, content_hash=None, error=str(exc))
        finally:
            page.close()
