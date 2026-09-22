"""Pure backoff/circuit-breaker math — no I/O, no side effects.

Owns the parts of "crawl resilience" that don't require an active
connection: whether a domain should be attempted at all right now, and
how long to wait between requests to the same domain within one crawl.
"""

from __future__ import annotations

from scraper.domain.types import DomainPolicySnapshot


class CrawlDomainCircuitOpen(Exception):
    """Raised before any request is made if domain_policy indicates
    this domain shouldn't be attempted right now."""


_CIRCUIT_BREAKING_STATUS_CODES = {403, 429}

DEFAULT_INTER_REQUEST_DELAY_SECONDS = 0.5


def check_circuit(domain_policy: DomainPolicySnapshot) -> None:
    """Raises CrawlDomainCircuitOpen if the domain's last known status
    suggests it's actively blocking/rate-limiting us. A single-signal
    heuristic — DomainPolicySnapshot only carries last_status_code, not
    a richer block-detection history."""
    if domain_policy.last_status_code in _CIRCUIT_BREAKING_STATUS_CODES:
        raise CrawlDomainCircuitOpen(
            f"{domain_policy.canonical_domain} last responded with "
            f"{domain_policy.last_status_code}; circuit open"
        )


def inter_request_delay_seconds() -> float:
    """In-process delay between requests to the same domain within one
    crawl() call. Cross-invocation/cross-worker rate limiting isn't
    covered here — that would need shared state (e.g. a Redis token
    bucket) not part of the frozen design. Flagged as a scope boundary."""
    return DEFAULT_INTER_REQUEST_DELAY_SECONDS
