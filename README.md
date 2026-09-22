# Shopify Scraper — Local Setup

MVP pipeline: discover store URLs -> crawl -> extract contacts/socials ->
detect platform -> normalize/dedupe -> store -> verify emails -> export CSV.
Usable via a CLI or a small local web UI.

## 1. Prerequisites

- Python 3.10+
- PostgreSQL, reachable from your machine (local install, or a Neon database)
  — must be a **separate database from Lumvi's**, per the frozen design.
  Redis is *not* required to use the CLI or web UI — jobs run synchronously
  in-process. Redis/RQ (`orchestration/queue.py`) is only for a future
  production async worker setup, not needed for local use.

## 2. Install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium     # downloads the browser Tier 2 crawling needs
```

## 3. Configure the database

```bash
# macOS/Linux
export SCRAPER_DATABASE_URL="postgresql://user:password@localhost/scraper_db"
# Windows (PowerShell)
$env:SCRAPER_DATABASE_URL = "postgresql://user:password@localhost/scraper_db"
```

Create the database first (`createdb scraper_db` or via your Postgres client),
then apply the schema:

```bash
python -m scraper.cli init-db
```

## 4. Use it

### Option A — command line

```bash
python -m scraper.cli discover --url https://some-shopify-store.com
python -m scraper.cli list
python -m scraper.cli verify
python -m scraper.cli export --platform shopify --output ./exports
```

### Option B — web UI

```bash
python -m scraper.webapp.app
```

Then open http://127.0.0.1:5050 — paste or upload store URLs, watch progress,
view results, export a CSV. Both options use the exact same pipeline; use
whichever is convenient.

Debug mode is **off** by default. That matters here: the Flask reloader restarts
the process on every file save, and in-progress scrapes are held in memory only
(no Redis, no table), so a restart loses them. Turn it on when you want it while
developing the UI:

```bash
# macOS/Linux
FLASK_DEBUG=1 python -m scraper.webapp.app
# Windows (PowerShell)
$env:FLASK_DEBUG = "1"; python -m scraper.webapp.app
```

## 5. Running the test suite (optional)

Tests run against a **real** local Postgres (database `scraper_test`) and
real Redis — nothing is mocked. Not required to use the scraper, only to
verify the code itself.

```bash
createdb scraper_test          # separate from your working scraper_db
# Redis needs to be running locally (redis-server) for the queue tests
pytest scraper/tests/
```

## Known MVP limitations (by design, not oversights)

- **Discovery**: only `url_import` exists — paste in URLs you already know.
  Search/directory/sitemap providers aren't built yet.
- **Email verification**: DNS MX-record check only, never SMTP probing.
  This confirms a domain *can* receive mail, not that a specific mailbox
  exists. Replace with a real provider (NeverBounce/ZeroBounce/etc.) before
  this matters for actual outreach.
- **Suppression**: a no-op placeholder — nothing is excluded from export yet.
  Must be wired to your real outreach system before sending anything.
- **CSV export** is database-wide (all currently-verified contacts), not
  scoped to a single scrape session.
- **Web UI session tracking** is in-memory — restarting the app loses the
  "which scrape was that" view, though everything already found stays in
  the database.

## Project layout

```
scraper/
├── domain/          shared data types
├── migrations/       schema + migration runner
├── storage/           Postgres repository (all persistence)
├── discovery/         discovery providers (url_import so far)
├── crawler/            tiered HTTP -> Playwright crawler
├── extraction/         HTML -> structured signals
├── detection/           platform detection (Shopify, WooCommerce, ...)
├── processing/          normalization, entity resolution, validation
├── verification/         email verification (DNS-MX placeholder)
├── export/                CSV export
├── orchestration/          job functions + RQ queue wiring (future async use)
├── webapp/                  local web UI
├── cli.py                   local command-line interface
└── tests/                    full test suite
```
