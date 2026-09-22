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
