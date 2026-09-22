"""Lightweight email verifier for the MVP: checks the domain has MX
records via DNS lookup only — never connects to the mail server itself
(no SMTP probing), per the frozen decision that SMTP probing risks
triggering abuse controls. This is a placeholder for a real paid
verification provider (NeverBounce/ZeroBounce/Hunter/etc.), not a
long-term replacement — it can only tell you "this domain could
plausibly receive mail," not "this specific mailbox exists."
"""

from __future__ import annotations

from datetime import datetime, timezone

import dns.exception
import dns.resolver
from email_validator import EmailNotValidError, validate_email

from scraper.domain.types import VerificationResult


class DnsMxVerifier:
    provider_name = "dns-mx-check"

    def verify(self, email: str) -> VerificationResult:
        try:
            validated = validate_email(email, check_deliverability=False)
        except EmailNotValidError:
            return VerificationResult(email=email, status="invalid", provider=self.provider_name,
                                       verified_at=datetime.now(timezone.utc))

        domain = validated.domain
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=5.0)
            status = "verified" if len(answers) > 0 else "invalid"
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            status = "invalid"
        except dns.exception.DNSException:
            status = "unknown"  # DNS timeout/SERVFAIL etc — inconclusive, not proof of invalidity

        return VerificationResult(email=email, status=status, provider=self.provider_name,
                                   verified_at=datetime.now(timezone.utc))


class NoOpSuppressionClient:
    """Placeholder until the scraper is wired to the real outreach
    system (QuickMail/MailFlow). Always returns False — nothing is
    suppressed. Must be replaced before this is used for real outreach,
    not just lead collection/testing."""

    def is_suppressed(self, email: str) -> bool:
        return False
