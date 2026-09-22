"""Local CLI for running the scraper pipeline end-to-end without needing
RQ workers — jobs are called directly, synchronously, in-process. This
is for local testing/exploration (find early candidate leads, sanity
check the pipeline against real sites), not the production job system —
that's orchestration/queue.py + a real RQ worker, for later.

Setup:
    export SCRAPER_DATABASE_URL=postgresql://user:pass@host/scraper_db
    python -m scraper.cli init-db

Usage:
    python -m scraper.cli discover --url https://example-store.com [--url ...]
    python -m scraper.cli verify [--limit 50]
    python -m scraper.cli list
    python -m scraper.cli export [--platform shopify] [--output /tmp]

Notes:
    - `discover` runs discovery + crawl (extraction/detection/processing/
      storage) synchronously for every URL given, one at a time.
    - `verify` uses a DNS-MX-only checker (verification/verifier.py) —
      a real paid provider should replace it before this is used for
      actual outreach; it can't confirm a specific mailbox exists.
    - `export` uses a no-op suppression client — nothing is excluded.
      Wire a real one to your outreach system before sending anything.
"""

from __future__ import annotations

import argparse
import sys

from scraper.crawler import engine as crawler_module
from scraper.detection import platform as detection
from scraper.discovery.providers.url_import import UrlImportProvider
from scraper.domain.types import DiscoveryCriteria, ExportFilters
from scraper.export.writers import build_eligible_list, write_csv_export
import scraper.extraction as extraction_module
from scraper.migrations.run_migrations import MIGRATIONS_DIR, run_migrations
from scraper.orchestration.jobs import crawl_candidate_job, discovery_job, email_verification_job, export_job
from scraper.processing import entity_resolution, normalization, validation
from scraper.storage.repository import PostgresRepository, StorageConnectionError
from scraper.verification.verifier import DnsMxVerifier, NoOpSuppressionClient


def _repo() -> PostgresRepository:
    try:
        return PostgresRepository()
    except StorageConnectionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_init_db(args) -> None:
    applied = run_migrations()
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("Database already up to date.")


def cmd_discover(args) -> None:
    repo = _repo()
    criteria = DiscoveryCriteria(urls=args.url)
    enqueued = []
    new_ids = discovery_job(UrlImportProvider(), criteria, repo, enqueued.append)
    print(f"Discovered {len(new_ids)} new candidate(s).")

    for candidate_id in new_ids:
        domain = repo.get_candidate_domain(candidate_id)
        print(f"  crawling candidate {candidate_id} ({domain}) ...", end=" ", flush=True)
        result = crawl_candidate_job(
            candidate_id, repo,
            crawler=crawler_module, extractor=extraction_module, detector=detection,
            normalizer=normalization, resolver=entity_resolution, validator=validation,
        )
        print(result)


def cmd_verify(args) -> None:
    repo = _repo()
    summary = email_verification_job(args.limit, repo, NoOpSuppressionClient(), DnsMxVerifier())
    print(f"Verified: {summary['verified']}  Retried: {summary['retried']}  "
          f"Exhausted->unknown: {summary['exhausted']}  Skipped (suppressed): {summary['skipped_suppressed']}")


def cmd_list(args) -> None:
    import psycopg2
    conn = psycopg2.connect(repo_url())
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.id, b.name, b.platform, b.canonical_domain
                FROM businesses b ORDER BY b.id;
            """)
            businesses = cur.fetchall()
            for business_id, name, platform, domain in businesses:
                print(f"\n[{business_id}] {name or '(no name)'} — {platform or 'unknown platform'} — {domain}")
                cur.execute("SELECT type, value, verification_status FROM contacts WHERE business_id=%s;", (business_id,))
                for contact_type, value, status in cur.fetchall():
                    print(f"    {contact_type}: {value}  [{status or 'unverified'}]")
                cur.execute("SELECT platform, url FROM social_profiles WHERE business_id=%s;", (business_id,))
                for platform_name, url in cur.fetchall():
                    print(f"    {platform_name}: {url}")
        if not businesses:
            print("No businesses found yet — run `discover` first.")
    finally:
        conn.close()


def repo_url() -> str:
    import os
    url = os.environ.get("SCRAPER_DATABASE_URL")
    if not url:
        print("Error: SCRAPER_DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)
    return url


def cmd_export(args) -> None:
    repo = _repo()
    filters = ExportFilters(platform=args.platform, state=args.state, category=args.category)
    path = export_job(filters, "csv", repo, NoOpSuppressionClient(), build_eligible_list,
                       lambda contacts, fmt: write_csv_export(contacts, fmt, output_dir=args.output))
    print(f"Exported to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m scraper.cli", description="Local scraper CLI (synchronous, no RQ worker needed)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Apply pending database migrations").set_defaults(func=cmd_init_db)

    discover_parser = subparsers.add_parser("discover", help="Discover and crawl one or more URLs")
    discover_parser.add_argument("--url", action="append", required=True, help="A store URL to crawl (repeatable)")
    discover_parser.set_defaults(func=cmd_discover)

    verify_parser = subparsers.add_parser("verify", help="Run email verification (DNS-MX check) on unverified contacts")
    verify_parser.add_argument("--limit", type=int, default=50)
    verify_parser.set_defaults(func=cmd_verify)

    subparsers.add_parser("list", help="List businesses and their contacts/socials").set_defaults(func=cmd_list)

    export_parser = subparsers.add_parser("export", help="Export verified, eligible contacts to CSV")
    export_parser.add_argument("--platform", default=None)
    export_parser.add_argument("--state", default=None)
    export_parser.add_argument("--category", default=None)
    export_parser.add_argument("--output", default="/tmp", help="Output directory (default: /tmp)")
    export_parser.set_defaults(func=cmd_export)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
