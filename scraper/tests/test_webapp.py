"""Regression test for scraper/webapp/, using Flask's test client
against a real local target server and real Postgres — same standard
as the rest of this project. This is the automated counterpart to the
manual live-server verification already done by hand.
"""

import threading
import time

import psycopg2
import pytest
from flask import Flask, Response
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from scraper.migrations.run_migrations import MIGRATIONS_DIR, run_migrations
from scraper.webapp import session_store
from scraper.webapp.app import create_app

TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost/scraper_test"

STORE_HTML = """
<html><head><title>Test Store</title>
<script type="application/ld+json">
{"@type": "Organization", "name": "Test Store", "email": "hello@teststore-demo.com"}
</script></head>
<body><script src="https://cdn.shopify.com/theme.js"></script>
<p>Welcome to our shop, we sell wonderful things with care and pride.</p>
<a href="mailto:hello@teststore-demo.com">Email</a></body></html>
"""


@pytest.fixture
def target_server():
    app = Flask(__name__)

    @app.route("/")
    def home():
        return Response(STORE_HTML, mimetype="text/html")

    @app.route("/robots.txt")
    def robots():
        return Response("User-agent: *\nAllow: /\n", mimetype="text/plain")

    srv = make_server("127.0.0.1", 8767, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield "http://127.0.0.1:8767"
    srv.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def db(monkeypatch):
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE;")
        cur.execute("CREATE SCHEMA public;")
    conn.close()
    run_migrations(migrations_dir=MIGRATIONS_DIR, database_url=TEST_DATABASE_URL)
    monkeypatch.setenv("SCRAPER_DATABASE_URL", TEST_DATABASE_URL)


@pytest.fixture
def client(db):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _wait_for_done(client, session_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/scrape/{session_id}/status.json")
        data = resp.get_json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.3)
    raise TimeoutError("scrape did not finish in time")


def test_new_scrape_page_renders(client):
    resp = client.get("/scrape/new")
    assert resp.status_code == 200
    assert b"New Scrape" in resp.data


def test_start_scrape_with_no_urls_shows_error(client):
    resp = client.post("/scrape/new", data={"urls": ""})
    assert resp.status_code == 200
    assert b"Enter at least one URL" in resp.data


def test_full_flow_start_progress_results_export(client, target_server):
    resp = client.post("/scrape/new", data={"urls": target_server + "/"})
    assert resp.status_code == 302
    session_id = resp.headers["Location"].split("/")[2]

    status = _wait_for_done(client, session_id)
    assert status["status"] == "done"
    assert status["counts"]["success"] == 1

    results_resp = client.get(f"/scrape/{session_id}/results")
    assert b"Test Store" in results_resp.data
    assert b"shopify" in results_resp.data
    assert b"hello@teststore-demo.com" in results_resp.data

    export_resp = client.get(f"/scrape/{session_id}/export.csv")
    assert export_resp.status_code == 200
    assert b"business_id,contact_id,email,verification_status" in export_resp.data


def test_duplicate_url_across_sessions_is_deduped_gracefully(client, target_server):
    first = client.post("/scrape/new", data={"urls": target_server + "/"})
    first_id = first.headers["Location"].split("/")[2]
    _wait_for_done(client, first_id)

    second = client.post("/scrape/new", data={"urls": target_server + "/"})
    second_id = second.headers["Location"].split("/")[2]
    status = _wait_for_done(client, second_id)
    assert status["counts"]["total"] == 0  # already-known domain, no new candidate — no crash


def test_file_upload_path(client, target_server):
    from io import BytesIO
    data = {"file": (BytesIO((target_server + "/\n").encode()), "urls.txt")}
    resp = client.post("/scrape/new", data=data, content_type="multipart/form-data")
    assert resp.status_code == 302
    session_id = resp.headers["Location"].split("/")[2]
    status = _wait_for_done(client, session_id)
    assert status["counts"]["success"] == 1


def test_progress_page_for_unknown_session_is_404(client):
    resp = client.get("/scrape/nonexistent/progress")
    assert resp.status_code == 404


@pytest.fixture
def live_app():
    """The real app behind a real HTTP server. The progress page's poller is
    browser JavaScript, so Flask's test client cannot exercise it at all."""
    app = create_app()
    srv = make_server("127.0.0.1", 8768, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield "http://127.0.0.1:8768"
    srv.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


def test_progress_poller_reports_a_lost_session_instead_of_freezing(live_app, browser):
    """B1: after an app restart the in-memory session is gone and status.json
    404s. The old poller threw on `data.counts` inside its .then and never
    re-armed, freezing the page on "Starting..." with no error shown."""
    session_id = session_store.create_session(["https://example-store.com/"])
    page = browser.new_page()
    page.route("**/status.json", lambda route: route.fulfill(
        status=404, content_type="application/json", body='{"error": "not found"}',
    ))
    page.goto(f"{live_app}/scrape/{session_id}/progress")

    error = page.locator("#poll-error")
    expect(error).to_be_visible(timeout=10000)
    expect(error).to_contain_text("no longer available")
    # Retrying a session that no longer exists could never work, so the page
    # points at the "+ New Scrape" nav link instead of offering a dead button.
    expect(page.locator("#retry-button")).to_be_hidden()


def test_progress_poller_recovers_when_retried(live_app, browser):
    """B2: any failed fetch froze the page the same way. It now reports the
    failure and offers a retry that resumes polling."""
    session_id = session_store.create_session(["https://example-store.com/"])
    page = browser.new_page()
    page.route("**/status.json", lambda route: route.abort())
    page.goto(f"{live_app}/scrape/{session_id}/progress")

    error = page.locator("#poll-error")
    expect(error).to_be_visible(timeout=10000)
    expect(error).to_contain_text("Lost contact with the server")

    retry = page.locator("#retry-button")
    expect(retry).to_be_visible()

    page.unroute("**/status.json")  # server reachable again
    retry.click()
    expect(error).to_be_hidden(timeout=10000)
    # No pipeline is running for this session, so it stays on "starting" —
    # which is itself proof the poll loop is live again.
    expect(page.locator("#status-line")).to_have_text("Status: starting", timeout=10000)
