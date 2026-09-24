"""Frontend MVP: scrape -> enter/upload URLs -> start -> progress ->
results -> CSV export. Deliberately minimal — no CRM, campaigns,
analytics, or personalization. A thin UI over the existing pipeline;
every job call here is identical to what the CLI already does.

Run with:
    export SCRAPER_DATABASE_URL=postgresql://user:pass@host/scraper_db
    python -m scraper.webapp.app
Then open http://127.0.0.1:5050/

Debug mode (and the auto-reloader that comes with it) is off by default —
see _debug_enabled. Set FLASK_DEBUG=1 to turn it on for local development.
"""

from __future__ import annotations

import os

import psycopg2
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from scraper.domain.types import ExportFilters
from scraper.export.writers import build_eligible_list, write_csv_export
from scraper.orchestration.jobs import export_job
from scraper.storage.repository import PostgresRepository, get_database_url
from scraper.verification.verifier import NoOpSuppressionClient
from scraper.webapp import pipeline, session_store


def _parse_urls(text: str) -> list:
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "," in line:  # tolerate a CSV upload — take the first field
            line = line.split(",")[0].strip()
        urls.append(line)
    return urls


def _debug_enabled() -> bool:
    """Opt-in only: Flask's reloader restarts the process on every file
    save, which wipes the in-memory session store (docs/FRONTEND_AUDIT.md
    B10/B1). Enable with FLASK_DEBUG=1."""
    value = os.environ.get("FLASK_DEBUG")
    return bool(value and value.lower() not in {"0", "false", "no"})


def _fetch_results_for_candidates(candidate_ids: list) -> list:
    if not candidate_ids:
        return []
    conn = psycopg2.connect(get_database_url())
    try:
        businesses = []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT b.id, b.name, b.platform, b.canonical_domain
                FROM businesses b
                JOIN candidates c ON c.business_id = b.id
                WHERE c.id = ANY(%s)
                ORDER BY b.id;
                """,
                (candidate_ids,),
            )
            rows = cur.fetchall()
        for business_id, name, platform, domain in rows:
            with conn.cursor() as cur:
                cur.execute("SELECT type, value, verification_status FROM contacts WHERE business_id=%s;", (business_id,))
                contacts = cur.fetchall()
                cur.execute("SELECT platform, url FROM social_profiles WHERE business_id=%s;", (business_id,))
                socials = cur.fetchall()
            businesses.append({
                "id": business_id, "name": name, "platform": platform, "domain": domain,
                "contacts": contacts, "socials": socials,
            })
        return businesses
    finally:
        conn.close()


def create_app() -> Flask:
    app = Flask(__name__)

    # --- step 1 scaffolding -------------------------------------------------
    # The rebuilt shell's nav links to four destinations, but url_for raises
    # BuildError at render time for an unregistered endpoint — so the three
    # pages that don't exist yet are stubbed here purely to make the nav
    # resolve. No database access, no logic. Each body is replaced wholesale
    # by its own step: dashboard -> step 3, verify -> step 7, export -> step 5
    # of docs/FRONTEND_REBUILD_PLAN.md. The _placeholder.html template goes
    # away with them.

    @app.route("/")
    def dashboard():
        return render_template("_placeholder.html", page_title="Dashboard", step=3)

    @app.route("/verify")
    def verify_page():
        return render_template("_placeholder.html", page_title="Verify", step=7)

    @app.route("/export")
    def export_page():
        return render_template("_placeholder.html", page_title="Export", step=5)

    @app.route("/scrape/new", methods=["GET"])
    def new_scrape():
        return render_template("new_scrape.html", error=None)

    @app.route("/scrape/new", methods=["POST"])
    def start_scrape():
        urls = _parse_urls(request.form.get("urls", ""))
        uploaded = request.files.get("file")
        if uploaded and uploaded.filename:
            urls += _parse_urls(uploaded.read().decode("utf-8", errors="ignore"))
        urls = list(dict.fromkeys(u for u in urls if u))  # dedupe, keep order

        if not urls:
            return render_template("new_scrape.html", error="Enter at least one URL, or upload a file.")

        session_id = session_store.create_session(urls)
        pipeline.start_scrape_background(session_id, urls)
        return redirect(url_for("progress", session_id=session_id))

    @app.route("/scrape/<session_id>/progress")
    def progress(session_id):
        session = session_store.get_session(session_id)
        if session is None:
            return "Scrape not found — the app may have restarted.", 404
        return render_template("progress.html", session=session)

    @app.route("/scrape/<session_id>/status.json")
    def status_json(session_id):
        session = session_store.get_session(session_id)
        if session is None:
            return jsonify({"error": "not found"}), 404
        counts = {"total": len(session["candidate_ids"]), "success": 0, "failed": 0, "pending": 0}
        for candidate_id in session["candidate_ids"]:
            result = session["crawl_results"].get(candidate_id)
            if result is None:
                counts["pending"] += 1
            elif result == "success":
                counts["success"] += 1
            else:
                counts["failed"] += 1
        return jsonify({"status": session["status"], "counts": counts, "error": session.get("error")})

    @app.route("/scrape/<session_id>/results")
    def results(session_id):
        session = session_store.get_session(session_id)
        if session is None:
            return "Scrape not found — the app may have restarted.", 404
        businesses = _fetch_results_for_candidates(session["candidate_ids"])
        return render_template("results.html", session=session, businesses=businesses)

    @app.route("/scrape/<session_id>/export.csv")
    def export_csv(session_id):
        # Exports ALL currently-eligible (verified) contacts, not just
        # this session's — ExportFilters has no per-session scope, and
        # adding one would be a backend change. Labeled plainly in the
        # UI rather than silently implying it's scoped to this scrape.
        repo = PostgresRepository()
        path = export_job(
            ExportFilters(limit=5000), "csv", repo, NoOpSuppressionClient(),
            build_eligible_list, lambda contacts, fmt: write_csv_export(contacts, fmt, output_dir="/tmp"),
        )
        return send_file(path, as_attachment=True, download_name="scraper_export.csv")

    return app


if __name__ == "__main__":
    create_app().run(debug=_debug_enabled(), port=5050)
