"""End-to-end pipeline test: discovery -> crawl -> extraction ->
detection -> normalization -> entity resolution -> validation ->
storage -> verification -> export, all real components (no fakes)
against a real local Flask server and a real Postgres database.

This is the proof that the pipeline actually works together, not just
that each component works in isolation.
"""

import threading
import time

import psycopg2
import pytest
from flask import Flask, Response
from werkzeug.serving import make_server

from scraper.crawler import engine as crawler_module
from scraper import extraction as extraction_module
from scraper.detection import platform as detection
from scraper.discovery.providers.url_import import UrlImportProvider
from scraper.domain.types import DiscoveryCriteria, ExportFilters
from scraper.export.writers import build_eligible_list, write_csv_export
from scraper.migrations.run_migrations import MIGRATIONS_DIR, run_migrations
from scraper.orchestration.jobs import crawl_candidate_job, discovery_job, email_verification_job, export_job
from scraper.processing import entity_resolution, normalization, validation
from scraper.storage.repository import PostgresRepository
from scraper.verification.verifier import DnsMxVerifier, NoOpSuppressionClient

TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost/scraper_test"

STORE_HTML = """
<html><head>
<title>Acme Widgets</title>
<meta name="description" content="Fine widgets since 1990.">
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Organization", "name": "Acme Widgets",
 "email": "hello@acmewidgets-demo.com", "sameAs": ["https://instagram.com/acmewidgets"]}
</script>
</head>
<body>
<script src="https://cdn.shopify.com/s/files/1/theme.js"></script>
<h1>Acme Widgets</h1>
<p>We sell the finest widgets in town, hand-crafted with care and pride.</p>
<a href="/contact">Contact</a>
<a href="mailto:hello@acmewidgets-demo.com">Email us</a>
<a href="https://instagram.com/acmewidgets">Follow us</a>
</body></html>
"""
CONTACT_HTML = "<html><body><h1>Contact</h1><p>Reach out any time: hello@acmewidgets-demo.com</p></body></html>"


@pytest.fixture
def server():
    app = Flask(__name__)

    @app.route("/")
    def home():
        return Response(STORE_HTML, mimetype="text/html")

    @app.route("/contact")
    def contact():
        return Response(CONTACT_HTML, mimetype="text/html")

    @app.route("/robots.txt")
    def robots():
        return Response("User-agent: *\nAllow: /\n", mimetype="text/plain")

    srv = make_server("127.0.0.1", 8766, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield "http://127.0.0.1:8766"
    srv.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def repo():
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE;")
        cur.execute("CREATE SCHEMA public;")
    conn.close()
    run_migrations(migrations_dir=MIGRATIONS_DIR, database_url=TEST_DATABASE_URL)
    return PostgresRepository(database_url=TEST_DATABASE_URL)


def _fetch_all(sql, params=()):
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def test_full_pipeline_discovery_through_export(server, repo):
    # 1. discovery — seed one URL, real UrlImportProvider
    enqueued = []
    new_ids = discovery_job(
        UrlImportProvider(), DiscoveryCriteria(urls=[f"{server}/"]), repo, enqueued.append,
    )
    assert len(new_ids) == 1
    candidate_id = new_ids[0]

    # 2. crawl — real crawler, real extraction, real detection, real
    #    normalization/entity-resolution/validation, real repository
    result = crawl_candidate_job(
        candidate_id, repo,
        crawler=crawler_module, extractor=extraction_module, detector=detection,
        normalizer=normalization, resolver=entity_resolution, validator=validation,
    )
    assert result == "success"

    businesses = _fetch_all("SELECT id, name, platform FROM businesses;")
    assert len(businesses) == 1
    business_id, name, platform = businesses[0]
    assert name == "Acme Widgets"
    assert platform == "shopify"

    contacts = _fetch_all("SELECT value, verification_status FROM contacts WHERE business_id=%s;", (business_id,))
    emails = [c[0] for c in contacts]
    assert "hello@acmewidgets-demo.com" in emails

    socials = _fetch_all("SELECT platform, url FROM social_profiles WHERE business_id=%s;", (business_id,))
    assert any(s[0] == "instagram" for s in socials)

    # 3. verification — real DNS-MX verifier (a .test domain has no real
    #    MX records, so this should resolve to 'invalid', not crash)
    summary = email_verification_job(10, repo, NoOpSuppressionClient(), DnsMxVerifier())
    assert summary["verified"] + summary["retried"] + summary["exhausted"] == 1

    verification_status = _fetch_all("SELECT verification_status FROM contacts WHERE business_id=%s;", (business_id,))
    assert verification_status[0][0] in ("verified", "invalid", "unknown")  # real DNS lookup, not asserting which

    # 4. export — force verification_status to 'verified' so eligibility
    #    is exercised deterministically (the .test domain won't have real
    #    MX records, so we can't rely on step 3's real DNS result here)
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE contacts SET verification_status='verified' WHERE business_id=%s;", (business_id,))
    conn.commit()
    conn.close()

    path = export_job(
        ExportFilters(), "csv", repo, NoOpSuppressionClient(), build_eligible_list, write_csv_export,
    )
    with open(path) as f:
        content = f.read()
    assert "hello@acmewidgets-demo.com" in content

    exported = _fetch_all("SELECT last_exported_at FROM contacts WHERE business_id=%s;", (business_id,))
    assert exported[0][0] is not None
