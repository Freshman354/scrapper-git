# Frontend Rebuild Plan — `scraper/webapp/`

**Date:** 2026-09-22
**Status:** Proposed — awaiting approval. No code written.
**Baseline:** commit `875a704`, working tree clean.

Replaces the existing web UI with a rebuild on a new stack, covering all five CLI
capabilities and fixing the open items from [FRONTEND_AUDIT.md](FRONTEND_AUDIT.md).
The progress-poller fix (B1/B2, [PROGRESS_BUG_REPORT.md](PROGRESS_BUG_REPORT.md)) is
preserved, not rewritten.

---

## 0. Scope and stack

**New stack:** Flask + Jinja templates + Tailwind CSS via CDN + minimal vanilla JS.
No build step, no bundler, no frontend framework.

**Design direction:** clean, modern, utilitarian dashboard. Light theme, slate/blue
palette, generous whitespace. Explicitly **not** Lumvi's brand — no dark ink, no gold,
no serif display font. This is an internal tool and should be visually distinct from
Lumvi's product.

**Requirements covered:**

| # | Requirement | Addressed in |
|---|---|---|
| 1 | Dashboard/list — all businesses, platform, contact status, filter/search | §1.1 |
| 2 | New scrape — URL textarea + file upload, existing logic kept | §1.2 |
| 3 | Progress — keep the B1/B2 polling fix, restyle only | §1.3 |
| 4 | Results — clickable socials, `mailto:`, real empty state (B3/B4) | §1.4 |
| 5 | Verify — trigger verification, show counts | §1.5 |
| 6 | Export — CSV download + honest scope disclosure (B5) | §1.6 |
| 7 | A real 404/error page (B7) | §1.7 |

Mobile responsive (viewport meta, scroll-wrapped tables, touch targets), existing
backend logic kept unless a change is justified in §2.

---

## 1. Page-by-page plan

**Shared shell.** Every page extends a rewritten `base.html`: sticky top nav bar
(Dashboard · New Scrape · Verify · Export), `max-w-7xl` centred container, `bg-slate-50`
page with white cards on `border-slate-200` and `rounded-lg`, system sans throughout.
Each page sets its own `<title>` via `{% block title %}`. Palette: `slate` neutrals,
`blue-600` primary action, semantic `emerald`/`amber`/`rose` for status badges. Nav
links get `py-3` so every one clears a 44px tap target.

### 1.1 Dashboard (`/`) — replaces the current redirect to `/scrape/new`

- **Header row:** "Businesses" + a `New Scrape` primary button on the right.
- **Stat tile row** (`grid-cols-2 md:grid-cols-4`): total businesses · with verified
  email · awaiting verification · no contacts found. Each tile links to a
  pre-filtered dashboard. All four numbers come from one aggregate query.
- **Filter bar** in a card: search input (name or domain, debounced ~300ms), Platform
  `<select>` (populated from `platform_facets()`, not hardcoded), Contact status
  `<select>` (All / Has verified / Unverified only / No contacts), and a "Clear" link.
  Submits as GET, so filter state lives in the URL and is bookmarkable and shareable.
- **Results table** wrapped in `overflow-x-auto` with `min-w-[720px]` on the table —
  the page body never scrolls sideways. Columns: Business (name + domain stacked),
  Platform (badge), Contacts (rollup chips: `2 verified` / `1 pending` / `1 invalid`),
  Socials (count plus first few platforms), Last updated.
- **Default filter:** `merge_status = 'active'`. Possible-duplicate rows appear only
  when the user opts in, badged `possible duplicate` in amber.
- **Pagination:** 50 per page, previous/next plus "Showing 51–100 of 412". Preserves
  all query params.
- **Empty states — three distinct cases:** database genuinely empty ("No businesses
  yet. Run a scrape to populate the database." with a New Scrape CTA); filters matched
  nothing ("No businesses match these filters." with a Clear link); search matched
  nothing.

### 1.2 New Scrape (`/scrape/new`) — restyle only

- Two-column on desktop (`md:grid-cols-2`), stacked on mobile. Left card: textarea
  (monospace, ~200px, placeholder showing both example URLs and the "one per line"
  hint). Right card: file upload with a dashed-border drop-zone treatment and explicit
  `.txt`/`.csv` accept.
- Inline hint that pasting either a bare list or a CSV works — the backend already
  takes the first field of comma-separated lines (`app.py:_parse_urls`), which is
  currently undiscoverable from the UI.
- Validation error renders as a rose alert above the form, matching the existing
  `error` string.
- `Start Scrape` gets a client-side disable-on-submit (~1 line of JS) so a double-click
  can't fire two POSTs, which the audit notes is currently possible.
- Below the fold, a muted "What happens next" strip: discover → crawl → extract →
  verify, so the automatic pipeline is predictable rather than surprising.

### 1.3 Progress (`/scrape/<id>/progress`) — restyle, keep the B1/B2 fix

- The existing poller logic moves **verbatim** to `static/js/poll.js` (see §2), with
  every element ID preserved (`#status-line`, `#counts`, `#poll-error`,
  `#retry-button`, `#results-link`) so the two Playwright regression tests keep passing.
- Visual: a stage indicator (`Discover → Crawl → Verify → Done`) driven by `data.status`,
  active stage in blue, completed stages checked. Below it a large count display
  (`2 of 3 succeeded`) and a real progress bar (`bg-slate-200` track, `bg-blue-600`
  fill, width from `(success + failed) / total`).
- The counts sentence becomes three stat chips (succeeded / failed / pending) — same
  data, read at a glance instead of parsed from prose.
- Error and lost-session states render as a rose alert card with the same text and the
  same Retry-hidden-on-404 behaviour.
- Stage list and count display carry `aria-live="polite"` so poller updates are
  announced rather than silent to screen readers.

### 1.4 Results (`/scrape/<id>/results`)

- **Header:** "<n> businesses from this scrape" plus a link to Export.
- **Empty states — the B3/B4 fix. Three distinct cases, no blank table:**
  1. `candidate_ids` empty **and** the submitted domains already exist in `businesses`
     → "Already in the database" — lists each submitted URL with a "View on dashboard →"
     link to the matching business. This is the common re-scrape case that currently
     renders a headers-only table with no explanation.
  2. `candidate_ids` empty and the domains are unknown → "Nothing new was discovered",
     listing the submitted URLs and noting that discovery handles direct URL import only.
  3. Candidates were crawled but no business was persisted → "Crawled, but no usable
     data", with a link to the progress page's failure counts.
- **Table**, wrapped in `overflow-x-auto`: Business · Platform · Contacts · Socials.
  - Contacts render as `mailto:` links, with verification status as a chip (`emerald`
    verified; `amber` pending/unknown; `rose` invalid/blocked/abandoned; `slate`
    unverified-null).
  - Socials render as real links to the URL the backend already fetches and the current
    template discards (`results.html` renders `{{ platform }}` and drops `url`) —
    **behind an http/https scheme allowlist** (see §2, security).
  - Long email and URL cells get `break-all` so one value can't force the table wide.
- Primary `Export verified leads` button links to the new `/export` page rather than the
  misleadingly session-scoped CSV URL. A muted line states plainly that the results
  table is this scrape's while export covers the whole database.

### 1.5 Verify (`/verify`)

- **Before running:** explains what verification does and its two honest limits, lifted
  from the CLI's own docstring — it is a DNS-MX check (confirms the domain accepts mail,
  *not* that the mailbox exists), and nothing is excluded by suppression. Shows the
  pending count (emails with `verification_status IS NULL OR = 'pending'`) and a limit
  input (default 50, matching the CLI default; the pipeline's automatic tail run uses 500).
- **After starting:** POST creates a verification session and redirects to `/verify/<id>`,
  which shows a spinner, "Verifying up to N contacts…", and the poller. `email_verification_job`
  emits no per-contact progress, so the page reports running → done honestly rather than
  faking a percentage.
- **Summary:** four stat tiles from the job's own return dict — Verified · Retried ·
  Exhausted → unknown · Skipped (suppressed) — matching exactly what `cmd_verify` prints,
  so the web UI stops being the one place that hides this.
- A "Verify more" button and a link to the dashboard filtered to verified contacts.

### 1.6 Export (`/export`)

This page exists to make B5 non-misleading, so its job is disclosure plus real filters.

- **"What this export includes"** card, stated as facts rather than a disclaimer:
  - **Scope:** every business in the database, across all scrapes and CLI runs — *not*
    the session you came from.
  - **Eligibility:** contacts where `type = 'email'` and `verification_status = 'verified'`.
  - **Columns:** `business_id, contact_id, email, verification_status`.
  - **Suppression:** none configured — nothing is excluded. The suppression client is a
    no-op today.
  - **Cap:** 5,000 rows (`ExportFilters(limit=5000)`).
  - **Side effect:** downloaded contacts get `last_exported_at` stamped. This is what
    `export_job` does; it is currently invisible and worth stating.
- **Live eligible count** for the current filters, so "0 rows" is known before the
  download rather than after. Computed by reusing `repo.get_eligible_contacts(filters)`
  and taking its length — this keeps the eligibility predicate in exactly one place
  instead of duplicating the SQL.
- **Filter card** exposing the three flags the CLI already has and the web UI has none
  of: Platform, State, Category (all optional).
- **Download button** → `/export.csv?platform=…&state=…&category=…`, same `send_file`
  behaviour and `scraper_export.csv` name as today.
- If the eligible count is 0, the button is disabled with "No verified contacts match —
  run Verify first."

### 1.7 Error page (`error.html`)

One template used by both handlers. Large muted status code, plain-language heading,
short explanation, and two actions: "Back to dashboard" and "New Scrape". The 404 names
the likely cause — a scrape session is in-memory and lost on app restart. The 500 shows a
generic message with no traceback (see §2). The unknown-session routes call `abort(404)`
instead of returning bare strings, keeping their HTTP status while gaining the shell.

---

## 2. Backend and route changes

### No changes needed

`pipeline.py`'s existing `run_scrape` flow, `session_store`'s scrape session model, all
five CLI job calls, and `repository.py`'s write paths are untouched.

### Changes needed, and why

**(a) New module `scraper/webapp/queries.py` — read-only browse queries.**

The dashboard and the B3 fix need queries that don't exist. These belong here rather than
in `repository.py`, following two precedents already in the codebase:
`_fetch_results_for_candidates` (raw psycopg2, in `app.py`) and `cmd_list` (raw psycopg2,
in `cli.py`). `repository.py`'s docstring calls itself the implementation of a *frozen*
contract, and both existing read-only browse paths deliberately bypass it — so adding to
it would be the inconsistent choice.

| Function | Purpose |
|---|---|
| `list_businesses(q, platform, contact_status, include_duplicates, page, per_page)` | → `(rows, total)`. **One query, not N+1**: a grouped aggregate or `LEFT JOIN LATERAL` returns each business with its contact-status counts and social platforms in a single round trip. |
| `platform_facets()` | Distinct platforms with counts, for the filter dropdown. |
| `dashboard_counts()` | The four stat tiles in one query. |
| `find_businesses_by_domains(domains)` | The B3 "already known" lookup. |

The dashboard cannot follow the existing per-business query pattern: `_fetch_results_for_candidates`
issues 2 queries *per business*, which at 50 rows/page is 100 round trips.

**(b) `_fetch_results_for_candidates` — rewrite as one aggregate query.** Same output
shape, so `results.html` is unaffected, but the results page stops issuing `2 × n` queries.

**(c) `session_store.py` — support verification sessions.** Add a `kind` key
(`"scrape"` / `"verify"`) and `create_verification_session(limit)` alongside
`set_verification_summary`. Reuses the same dict and lock; `get_session` still returns a
copy. Needed because the verify job must run off-request — see (d).

**(d) `pipeline.py` — add `start_verification_background(session_id, limit)`.** Calls
`email_verification_job(limit, repo, NoOpSuppressionClient(), DnsMxVerifier())` —
byte-identical to `cmd_verify` and to what the scrape tail already runs. It gets a thread
because DNS-MX over 500 contacts takes tens of seconds; a synchronous request that slow
reads as a hang. This mirrors the existing scrape pattern exactly. Safe to run concurrently
with a scrape: `claim_unverified_contacts` uses `FOR UPDATE SKIP LOCKED` plus a 15-minute
lease, so the two cannot double-verify a contact.

**(e) Routes.**

| Route | Change | Why |
|---|---|---|
| `/` | Now renders the dashboard (was a redirect to `/scrape/new`) | The list page is the natural landing surface. |
| `/scrape/new` (GET/POST) | Unchanged | Works. |
| `/scrape/<id>/progress` | Unchanged | Works. |
| `/scrape/<id>/status.json` | Unchanged | Works. |
| `/scrape/<id>/results` | Add the already-known lookup; `abort(404)` instead of a bare string | B3 + B7 |
| `/verify` GET | New — the page | New capability |
| `/verify` POST | New — create session, spawn thread, redirect | |
| `/verify/<id>` GET | New — polling page | |
| `/verify/<id>/status.json` | New — `{status, summary, error}` | |
| `/export` GET | New — the disclosure + filter page | B5 |
| `/export.csv` GET | New — download, honours the three filter params | B5 |
| `/scrape/<id>/export.csv` | **Keep as a thin alias** to the same handler | See note below |
| `errorhandler(404)` / `errorhandler(500)` | New — render `error.html` | B7 |

**On the legacy export URL.** `test_webapp.py:110` requests `/scrape/<session_id>/export.csv`
and asserts CSV bytes. Keeping that path as an alias keeps the test green with no test
edit, while the UI stops linking to it. The alternative — delete the route and update the
test — is cleaner but touches a passing end-to-end test for no user-visible gain. The alias
is the recommendation.

**(f) `output_dir="/tmp"` (B6).** `app.py:136` writes to `/tmp`, which on Windows silently
becomes `C:\tmp`. Since the file is only a staging artifact for `send_file`, changing it to
`tempfile.gettempdir()` is a one-line fix with no writer change. This is a pre-existing bug
outside the stated UI scope — flagged, not assumed.

### Security notes worth acting on

- **Social URLs are now rendered as `href`s.** They come from scraped third-party HTML.
  Jinja's autoescaping prevents attribute breakout but does *not* stop a `javascript:` or
  `data:` scheme from executing on click. The template must allowlist `http`/`https` and
  render anything else as inert text. This is new risk introduced by the clickable-socials
  requirement, so it is worth doing in the same change.
- **`500` must not echo the exception.** Flask's default handler leaks nothing in production
  mode, but the app runs with `debug` opt-in and the progress page currently surfaces raw
  exception strings through `status.json`. The error template should show a generic message;
  the exception stays in the server log.
- **CSRF (B9) remains out of scope.** Nothing here makes it worse, but the new POST route
  (`/verify`) adds a second unauthenticated state-changing endpoint. Still defensible for a
  loopback single-user tool — flagged so it is a decision, not an oversight.

### Risk: the Tailwind Play CDN needs network access

Offline, the app renders unstyled but fully functional. No test in the suite asserts styling,
and the two Playwright tests assert text and visibility only, so they stay green. Tailwind
also labels the Play CDN "development only"; for a localhost internal tool that is the
tradeoff the stack choice implies, but this UI cannot ship as-is to a hosted environment
without a build step.

---

## 3. Proposed file structure

```
scraper/webapp/
├── app.py                    # MODIFIED — new routes, abort(404), error handlers
├── queries.py                # NEW — read-only browse queries (dashboard, B3 lookup)
├── pipeline.py               # MODIFIED — +start_verification_background
├── session_store.py          # MODIFIED — +kind, verification sessions
├── static/
│   └── js/
│       └── poll.js           # NEW — shared poller (scrape + verify), B1/B2 logic verbatim
└── templates/
    ├── base.html             # REWRITTEN — shell, nav, Tailwind CDN, title/head blocks
    ├── dashboard.html        # NEW
    ├── new_scrape.html       # RESTYLED
    ├── progress.html         # RESTYLED — keeps IDs, JS moves to poll.js
    ├── results.html          # RESTYLED — empty states, mailto, social links
    ├── verify.html           # NEW
    ├── export.html           # NEW
    ├── error.html            # NEW — 404 + 500
    └── _pagination.html      # NEW partial
```

No `package.json`, no bundler, no build step. `static/` finally has a reason to exist — the
audit notes the directory is currently empty with a live URL space behind it. The one file
that moves (the poller) is the one piece of logic with a regression test guarding it.

---

## 4. Build order

Each step ends at a state that can be opened in a browser and judged.

1. **Shell first.** Rewrite `base.html` (Tailwind CDN, nav, container, title blocks) and
   restyle `new_scrape.html` against it. Proves the CDN loads, the palette reads right, and
   the nav works — before any new backend exists. `test_new_scrape_page_renders` and
   `test_start_scrape_with_no_urls_shows_error` should stay green.
   **Pause here for a look** — the visual direction is cheapest to correct now, before
   everything else inherits it.
2. **Error handling.** `error.html` + both handlers + `abort(404)` in the two session routes.
   Small, and it means every later step has somewhere sane to fail.
   `test_progress_page_for_unknown_session_is_404` stays green (status code unchanged).
3. **`queries.py` + dashboard.** The largest new piece. Build the queries, verify row counts
   against `cmd_list` on the same database, then the page with filters, search, pagination,
   and the three empty states. Test at 400px width here rather than at the end.
4. **Results page.** Restyle, add the three empty states and the `find_businesses_by_domains`
   lookup, rewrite `_fetch_results_for_candidates` as one query, add mailto links and the
   social-link scheme allowlist. `test_full_flow_start_progress_results_export` and
   `test_duplicate_url_across_sessions_is_deduped_gracefully` cover this path.
5. **Export page.** Filters, live eligible count, disclosure card, new `/export.csv`. Verify
   the downloaded row count matches the displayed count for each filter combination — that is
   the check that proves the count and the export share a predicate.
6. **Poller extraction + progress restyle.** Move the B1/B2 logic to `static/js/poll.js` and
   restyle the page. **Re-run the two Playwright tests before moving on** — they are the only
   thing standing between this refactor and silently reintroducing the freeze they were written
   to catch. If they fail, the extraction broke behaviour, not just markup.
7. **Verify page last.** Depends on the poller extraction (step 6) and needs the `session_store`
   and `pipeline` changes. Test with a small limit first, then confirm the summary numbers match
   `python -m scraper.cli verify --limit N` on the same database.
8. **Full pass.** Whole suite green, then walk every page at 400px / 768px / 1280px checking:
   no horizontal page scroll, 44px tap targets, and every table inside its own scroll wrapper.

---

## 5. Open decisions

Three calls needed before code starts; recommendations given for each.

1. **Legacy export alias** — keep `/scrape/<id>/export.csv` working as an alias (recommended;
   keeps `test_webapp.py:110` green with no test edit), or cut it and update the test.
2. **`output_dir` (B6)** — change `"/tmp"` to `tempfile.gettempdir()` (recommended; one line,
   fixes the stray `C:\tmp` on Windows), or leave the pre-existing bug outside UI scope.
3. **Pause after step 1** — hold for a visual check once the shell and one restyled page exist
   (recommended), or build straight through to step 8.

---

## 6. Out of scope

- **CSRF tokens (B9)** — no `Flask-WTF` dependency added.
- **`init-db` UI** — the web app still assumes migrations have been applied.
- **Retry/requeue sweep and RQ-backed orchestration** — the web app keeps using a raw
  `threading.Thread`.
- **Cancelling a running scrape** — every stage remains uninterruptible.
- **Per-session export scoping** — `ExportFilters` has no session dimension; the fix adopted
  here is honest labelling plus CLI-parity filters, not a new backend capability.
- **Dark mode** — light theme only, per the design direction.
