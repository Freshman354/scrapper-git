# Frontend Audit — `scraper/webapp/`

Scope: the web UI only (`scraper/webapp/`). Backend pipeline, CLI, storage, and
tests are referenced only where the UI's behavior depends on them. Read-only
audit — no files were modified and none were created except this one.

Audited at commit `474e23f` (initial commit), working tree clean.

---

## 1. File inventory

### UI files

| File | Lines | Role |
|---|---:|---|
| [scraper/webapp/app.py](../../scraper/webapp/app.py) | 144 | Flask app, all 7 routes, template rendering |
| [scraper/webapp/pipeline.py](../../scraper/webapp/pipeline.py) | 54 | Background thread that runs discovery → crawl → verify |
| [scraper/webapp/session_store.py](../../scraper/webapp/session_store.py) | 54 | In-memory session dict + lock |
| [scraper/webapp/__init__.py](../../scraper/webapp/__init__.py) | 0 | Empty package marker |
| [scraper/webapp/templates/base.html](../../scraper/webapp/templates/base.html) | 33 | Layout shell: `<head>`, all CSS, nav |
| [scraper/webapp/templates/new_scrape.html](../../scraper/webapp/templates/new_scrape.html) | 11 | URL entry form |
| [scraper/webapp/templates/progress.html](../../scraper/webapp/templates/progress.html) | 30 | Polling status page |
| [scraper/webapp/templates/results.html](../../scraper/webapp/templates/results.html) | 27 | Business/contact table |
| `scraper/webapp/static/` | — | **Directory exists, contains zero files** |

**Totals: 252 lines of Python, 101 lines of HTML (of which 22 are CSS, 22 are JS).**
All CSS lives inside one `<style>` block. All JavaScript lives inside one
`<script>` block. There is no `.css` file and no `.js` file anywhere in the
repository.

### Absences worth stating plainly

- No `static/` content — the directory is empty and nothing references it. Flask
  still auto-registers `/static/<path>` against it, so the app exposes a live URL
  space with nothing behind it.
- No `package.json`, no `node_modules`, no build step, no bundler.
- No template macros, partials, or component files — four standalone templates.
- No `favicon.ico`; every page 404s on `/favicon.ico`.

---

## 2. Routes and what each actually does

Defined in [app.py:73-138](../../scraper/webapp/app.py#L73-L138). Seven routes.

| # | Route | Methods | Renders / returns | Behavior |
|---|---|---|---|---|
| 1 | `/` | GET | 302 | Redirects to `/scrape/new`. No landing page. |
| 2 | `/scrape/new` | GET | `new_scrape.html` | Static form. `error=None`. |
| 3 | `/scrape/new` | POST | 302 → `/scrape/<id>/progress` | Parses textarea lines + optional uploaded file, splits on commas taking field 1, dedupes order-preserving, creates a session, spawns a daemon thread, redirects. If zero URLs survive: re-renders the form with `error="Enter at least one URL, or upload a file."` |
| 4 | `/scrape/<session_id>/progress` | GET | `progress.html` | Looks up the session; **404 with a bare-text body** (`"Scrape not found — the app may have restarted."`) if missing. |
| 5 | `/scrape/<session_id>/status.json` | GET | JSON | Used only by the progress page's poller. Returns `{status, counts:{total,success,failed,pending}, error}`. Counts are derived by walking `session["candidate_ids"]` against `crawl_results`. 404s with `{"error": "not found"}` for an unknown session. |
| 6 | `/scrape/<session_id>/results` | GET | `results.html` | `_fetch_results_for_candidates` ([app.py:37-67](../../scraper/webapp/app.py#L37-L67)) opens a **fresh psycopg2 connection**, joins `businesses` against `candidates` on the session's candidate ids, then runs a further 2 queries *per business* (contacts, social profiles). 404 bare text for unknown session. |
| 7 | `/scrape/<session_id>/export.csv` | GET | File download | Runs `export_job` over `ExportFilters(limit=5000)`, writes to `output_dir="/tmp"`, `send_file(...)` as `scraper_export.csv`. **`session_id` is accepted but never read.** |

### What the UI does *not* do

The pipeline thread ([pipeline.py:27-49](../../scraper/webapp/pipeline.py#L27-L49))
runs, in order: `discovery_job` (URL import only) → `crawl_candidate_job` per
candidate → `email_verification_job(500)` → `status="done"`. Every stage is
automatic and uninterruptible. The user has exactly two inputs (URLs, and
whether to press Start) and one intervention point (download a CSV). There is
no cancel, no retry, no per-URL feedback, no way to act on an individual result.

---

## 3. CSS framework

**None. Entirely hand-rolled.**

No Tailwind, no Bootstrap, no Bulma, no CDN `<link>`, no `<script src>`, no
class-name conventions from any framework. A grep for `tailwind|bootstrap|cdn|
link rel|script src` across all templates returns zero matches.

The complete stylesheet is [base.html:6-27](../../scraper/webapp/templates/base.html#L6-L27)
— a single inline `<style>` block, 22 lines, roughly 20 rules, covering 11
selectors: `body`, `h1`, `textarea`, `button`, `button:hover`, `table`, `th, td`,
`th`, `.error`, `.muted`, `.status-badge`, three `.status-*` variants, `nav`,
`nav a`, `nav a:hover`.

Notable gaps in coverage:

- `input` is never styled — the file picker on the new-scrape form is raw
  browser default chrome.
- `a` is only styled inside `nav`. Links in page bodies (`View results →`,
  `Export CSV of verified leads →`) render as default blue underlined text.
- No `.button`/`.btn` class; the one styled `button` selector means any future
  `<a>` that should look like a button won't.
- No form-label styles, no `.card`/`.panel` container, no table striping, no
  hover state on table rows.

---

## 4. Consistent layout, or ad hoc?

**A consistent shell, but a very thin one — the "design system" is 22 lines of
CSS and a single Jinja block.**

What is consistent:

- All three real pages extend `base.html` and fill `{% block content %}`
  ([new_scrape.html:1](../../scraper/webapp/templates/new_scrape.html#L1),
  [progress.html:1](../../scraper/webapp/templates/progress.html#L1),
  [results.html:1](../../scraper/webapp/templates/results.html#L1)).
- The same nav bar renders on every page.
- The same palette, font stack, and 900px measure apply everywhere.
- Every page opens with an `<h1>` and a `.muted` or plain `<p>` lead-in.

What breaks the consistency:

- **`base.html` blocks nothing but content.** No `{% block title %}`, so every
  page's browser tab reads "Scraper" ([base.html:4](../../scraper/webapp/templates/base.html#L4)).
- **Two routes escape the templates entirely.** The unknown-session responses in
  routes 4 and 6 return bare strings. Those pages have no nav, no styling, no
  `<html>` element, and no link back — a dead end that looks nothing like the
  rest of the app.
- **Layout decisions leak into page templates as inline styles** —
  `style="display:none;"` on [progress.html:7](../../scraper/webapp/templates/progress.html#L7)
  is the only inline style in the codebase and belongs in the stylesheet.
- **The `.status-badge` system is used by exactly one page.** Defined in
  [base.html:20-23](../../scraper/webapp/templates/base.html#L20-L23), consumed
  only in [results.html:17](../../scraper/webapp/templates/results.html#L17). The
  progress page reports failures as plain text and could reuse it.
- **Nav has exactly one item** (`+ New Scrape`), so it functions as a "start
  over" link rather than navigation — it cannot take you anywhere you aren't.

There is no spacing scale, no color tokens, no type scale — values are literal
(`#1a1a1a`, `#b00020`, `#eee`, `margin: 40px auto`, `1.4em`). Consistent because
the surface is tiny, not because a system enforces it. Any fifth page would have
to re-derive every choice.

---

## 5. Functional vs decorative vs broken

### Functional

- **New-scrape form.** Textarea and file upload both wired
  ([app.py:83-93](../../scraper/webapp/app.py#L83-L93)). Comma-tolerance, blank-line
  skipping, and order-preserving dedupe all work. Empty input produces a real
  inline error. `enctype="multipart/form-data"` is correct for the upload.
- **Background pipeline.** Request returns immediately; work happens on a daemon
  thread. Status transitions `starting → discovering → crawling → verifying →
  done` (or `→ error`) drive the UI honestly.
- **Progress polling.** Correct for an existing session: 1.5s `setTimeout`
  re-arm, terminal states stop the loop, the results link appears on
  `done`/`error`, and errors surface the exception string.
- **Results table.** Correctly renders name/domain, platform, contacts with
  verification badges, and socials, with `{% else %}` fallbacks for empty
  contact and social lists.
- **Export.** Produces a real CSV with the header
  `business_id,contact_id,email,verification_status` and downloads it. Confirmed
  by [test_webapp.py:109-111](../../scraper/tests/test_webapp.py#L109-L111).

### Decorative / orphaned

- **`static/` is an empty directory.** Zero files, zero references.
- **`session_id` in the export URL is inert.** [results.html:5](../../scraper/webapp/templates/results.html#L5)
  builds `/scrape/<id>/export.csv`, but the handler
  ([app.py:127-138](../../scraper/webapp/app.py#L127-L138)) never touches the
  parameter. Looks scoped; isn't.
- **`.status-badge` classes on the progress page** — none; the CSS exists but
  that page doesn't use it.
- **`h1 { font-size: 1.4em }`** is the only heading rule; a second heading level
  has no styling at all.

### Broken, in rough severity order

**B1 — The progress poller dies permanently on a 404, freezing the page.**
[progress.html:10-28](../../scraper/webapp/templates/progress.html#L10-L28). On an
unknown session, `status.json` returns `{"error": "not found"}` with a 404
([app.py:107](../../scraper/webapp/app.py#L107)). The handler does not check the
status code: `data.counts` is `undefined`, so `c.success` on the next line throws
a `TypeError` inside the `.then`. The rejection is unhandled (no `.catch()`), so
`setTimeout(poll, 1500)` on the line below **never runs**. The page is stuck on
"Starting..." forever with no error shown. This is reachable in normal use: the
session store is in-memory and explicitly lost on restart
([session_store.py:5-11](../../scraper/webapp/session_store.py#L5-L11)), so any
Flask reload mid-scrape (and `debug=True` auto-reloads on every file save —
[app.py:144](../../scraper/webapp/app.py#L144)) strands an open progress tab.

**B2 — The poller has no network error handling at all.** Same code path. Any
failed `fetch` (server down, port closed, tab throttled and connection dropped)
rejects identically and freezes the page the same way. No user-visible signal.

**B3 — Re-scraping an already-known domain shows a blank table with no
explanation.** `_fetch_results_for_candidates` only returns businesses joined via
*this session's* candidate ids ([app.py:44-53](../../scraper/webapp/app.py#L44-L53)).
When a URL's domain is already in the DB, discovery returns no new candidates, so
`candidate_ids` is empty and the results page renders a table with headers and no
rows — even though the business and its contacts are sitting in Postgres. The
test suite treats this as expected behavior
([test_webapp.py:114-122](../../scraper/tests/test_webapp.py#L114-L122), asserting
`counts["total"] == 0`), so it is a known outcome with no UI treatment. Compounded
by B4.

**B4 — No empty state on the results page.** [results.html:7-26](../../scraper/webapp/templates/results.html#L7-L26)
has no `{% if businesses %}` guard. Zero results render as a bare
header-only `<table>`. The lead-in text says "0 business(es) found from this
scrape.", which is the only hint, and it doesn't distinguish "scrape found
nothing" from "these URLs were already known."

**B5 — Export ignores the session entirely, and succeeds for sessions that don't
exist.** [app.py:127-138](../../scraper/webapp/app.py#L127-L138). Routes 4 and 6
404 on an unknown session; route 7 does not check at all and returns a full
database export. Additionally `ExportFilters(limit=5000)` carries no session
scope, so the file contains every eligible contact in the DB — across all
sessions and all CLI runs. This is disclosed honestly in the UI
([results.html:6](../../scraper/webapp/templates/results.html#L6)) and in a code
comment ([app.py:129-132](../../scraper/webapp/app.py#L129-L132)) rather than
hidden, but the link text "Export CSV of verified leads" next to a per-session
results table still reads as scoped.

**B6 — Export writes to a hardcoded `/tmp`, which on Windows becomes a stray
`C:\tmp`.** [app.py:136](../../scraper/webapp/app.py#L136) passes
`output_dir="/tmp"`; [writers.py:26](../../scraper/export/writers.py#L26) does
`Path(output_dir).mkdir(parents=True, exist_ok=True)`. On Windows `Path("/tmp")`
resolves against the current drive, so this typically succeeds by silently
creating `C:\tmp\scraper_export.csv` on the user's machine — a directory that has
nothing to do with the app and that the user never chose. It works, but it puts
files somewhere surprising. The CLI exposes `--output` for exactly this; the web
UI exposes no equivalent.

**B7 — Bare-text 404s with no way back.** [app.py:100](../../scraper/webapp/app.py#L100)
and [app.py:123](../../scraper/webapp/app.py#L123) return un-styled strings. A
user who lands on one gets a browser-default page with no nav, no brand, and no
link to start a new scrape. These are the app's only two error pages and they are
the two that look least like the app.

**B8 — Every page has the same `<title>`.** [base.html:4](../../scraper/webapp/templates/base.html#L4)
is a literal `Scraper` with no `{% block title %}`. Three pages, three tabs, one
identical label; indistinguishable in browser history or a bookmarks bar.

**B9 — No CSRF token on the scrape form.** No `Flask-WTF` in
[requirements.txt](../../requirements.txt) and no token in
[new_scrape.html:6](../../scraper/webapp/templates/new_scrape.html#L6). Defensible
for a localhost single-user tool, but it is a real gap if this is ever bound to
anything but loopback.

**B10 — Debug mode is on unconditionally.**
[app.py:144](../../scraper/webapp/app.py#L144) hardcodes `debug=True`, which
enables the Werkzeug interactive debugger. It binds `127.0.0.1` by default so it
is not remotely reachable, but any local process can reach the debugger PIN
prompt, and the auto-reloader is what makes B1 easy to hit.

### Dead links

**None.** Every `href` in the codebase resolves to a registered route:
`url_for('new_scrape')` ([base.html:30](../../scraper/webapp/templates/base.html#L30)),
`url_for('results', ...)` ([progress.html:7](../../scraper/webapp/templates/progress.html#L7)),
`url_for('export_csv', ...)` ([results.html:5](../../scraper/webapp/templates/results.html#L5)).
`url_for` would raise at render time for an unknown endpoint, and the templates
render in tests. The problem is not dead links — it's pages that don't exist.

### Missing pages for CLI features

The CLI has five subcommands ([cli.py:127-152](../../scraper/cli.py#L127-L152)).
Two have no UI surface at all.

| CLI command | Web equivalent | Gap |
|---|---|---|
| `init-db` | **none** | No setup UI. Web app assumes migrations already applied; a fresh DB fails at the first query with a raw exception surfaced through `status: error`. |
| `discover --url ...` | `/scrape/new` | Partial. URL import only, which matches the CLI's provider. No CSV-schema preview, no validation. |
| `verify --limit N` | **none** | No on-demand verification. It runs once automatically at the tail of every scrape with a fixed limit of 500 ([pipeline.py:24](../../scraper/webapp/pipeline.py#L24), [pipeline.py:44-45](../../scraper/webapp/pipeline.py#L44-L45)). A user cannot verify newly-arrived contacts, cannot raise the limit, and gets no summary of what verification did — the CLI prints verified/retried/exhausted/suppressed counts ([cli.py:82-83](../../scraper/cli.py#L82-L83)); the web UI shows none of it. |
| `list` | **none** | No browse-all view. Results are reachable only through the session id of a scrape started in this same process lifetime. There is no way to see the businesses already in the database — which is exactly what B3 leaves a user wanting. |
| `export --platform --state --category --output` | `/scrape/<id>/export.csv` | No filter UI for any of the three filter flags, no output-directory choice, and the session-scoping is inert (B5/B6). |

Two further CLI features have no UI: the retry/requeue sweep
([jobs.py:333](../../scraper/orchestration/jobs.py#L333)) and anything RQ-backed
([orchestration/queue.py](../../scraper/orchestration/queue.py)) — the web app
uses a raw `threading.Thread` instead.

---

## 6. Screenshot-equivalent — structural description

Global shell from [base.html:29-32](../../scraper/webapp/templates/base.html#L29-L32),
applied to all three real pages:

```
┌──────────────────────────────────────────────────────────┐
│  + New Scrape                              ← nav, 0.9em  │
│  ─────────────────────────────────────────────────────   │
│                                                          │
│  <h1>  per-page heading, 1.4em bold                      │
│  <p>   per-page lead-in, often .muted                    │
│                                                          │
│  ... page content ...                                    │
│                                                          │
└──────────────────────────────────────────────────────────┘
   max-width 900px, centered, 40px top margin, 20px side padding,
   system font stack, #222 on white, line-height 1.5
```

No header bar, no footer, no logo, no breadcrumb, no user/account area, no
global action buttons. The `nav` is a single left-aligned text link with 24px
below it.

### 6a. `/scrape/new` — "New Scrape"

```
┌──────────────────────────────────────────────────────────┐
│  + New Scrape                                            │
│                                                          │
│  New Scrape                                              │
│  Paste store URLs, one per line, or upload a .txt/.csv    │
│  file.                                    (.muted, #777) │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ https://example-store.com                          │  │
│  │ https://another-store.com                          │  │
│  │                                                    │  │
│  │        monospace, full width, 160px tall           │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  [ Choose File ] No file chosen     ← unstyled, default  │
│                                                          │
│  ┌───────────────┐                                       │
│  │ Start Scrape  │   black bg, white text, radius 4      │
│  └───────────────┘                                       │
└──────────────────────────────────────────────────────────┘
```

- Form has no `action`, so it POSTs back to `/scrape/new`.
- Disabled/loading state: none. The button stays fully clickable during the
  redirect, so a double-click submits twice.
- Validation error, when present, renders as a red `<p class="error">` between
  the lead-in and the textarea ([new_scrape.html:5](../../scraper/webapp/templates/new_scrape.html#L5)).
  No error styling is applied to the offending input itself.
- No client-side URL validation — `example.com` (no scheme) is accepted silently
  and fails later inside the crawler, where the failure is only visible as a
  `failed` count on the progress page.

### 6b. `/scrape/<id>/progress` — "Scraping in progress"

```
┌──────────────────────────────────────────────────────────┐
│  + New Scrape                                            │
│                                                          │
│  Scraping in progress                                    │
│  3 URL(s) submitted.                         (.muted)    │
│                                                          │
│  Status: crawling                    ← id="status-line"  │
│  2 succeeded, 0 failed, 1 pending (of 3 total)           │
│                                              (.muted)    │
│                                                          │
│  View results →         ← hidden until done/error        │
└──────────────────────────────────────────────────────────┘
```

- Only page with JavaScript. A 1.5s poll rewrites the two text nodes in place.
- No spinner, no progress bar, no percentage — just the four counts as a
  sentence.
- No live per-URL table; which URLs succeeded and which failed is invisible until
  the results page, and even there failures are not listed at all (failed URLs
  appear nowhere in the UI).
- No cancel button. Once started, a scrape runs to completion or errors.
- On `error`, the results link still appears and the status line becomes
  `Error: <exception string>` — the raw Python exception text is rendered
  verbatim into the page.
- **This is the page that freezes on B1/B2.**

### 6c. `/scrape/<id>/results` — "Results"

```
┌──────────────────────────────────────────────────────────┐
│  + New Scrape                                            │
│                                                          │
│  Results                                                 │
│  2 business(es) found from this scrape.  Export CSV of    │
│  verified leads →                                        │
│  Export includes all currently-verified contacts in the   │
│  database, not only this scrape's — run verification and  │
│  export again later if more come in.         (.muted)    │
│                                                          │
│  BUSINESS      PLATFORM   CONTACTS         SOCIALS       │
│  ────────────────────────────────────────────────────    │
│  Test Store    shopify    email:            instagram    │
│  teststore.com            hello@test…                    │
│                           [verified]                     │
│                                                          │
│  (no name)     unknown    none found        none found   │
│  example.com                                             │
└──────────────────────────────────────────────────────────┘
```

- Four columns: Business (name + domain stacked, domain `.muted`), Platform,
  Contacts, Socials. Header row is uppercase 0.85em `#666`.
- Contact badges: green `.status-verified`, red for
  `.status-invalid/.status-blocked/.status-abandoned`, amber for
  `.status-unknown/.status-retry`, plus a default for null → `unverified`.
- Socials render as platform name only — **the URL is not a link and is not
  shown**. The data is fetched ([app.py:59-60](../../scraper/webapp/app.py#L59-L60))
  and then the template renders `{{ platform }}` and discards `url`
  ([results.html:21](../../scraper/webapp/templates/results.html#L21)). Contact
  emails are likewise plain text, not `mailto:` links.
- No sorting, filtering, searching, pagination, or row detail view.
- No way to act on a row — no copy button, no per-row export.
- Zero businesses → header-only table, no empty state (B4).

### 6d. Unknown-session responses

```
Scrape not found — the app may have restarted.
```

Rendered by the browser in its default serif font at default size, black on
white, full window width. No nav, no heading, no styling, no link. Verbatim from
[app.py:100](../../scraper/webapp/app.py#L100) / [app.py:123](../../scraper/webapp/app.py#L123).

---

## 7. Responsive / mobile handling

**Effectively none. This is a desktop-only UI by construction.**

| Concern | Status |
|---|---|
| `<meta name="viewport">` | **Absent.** [base.html:3-5](../../scraper/webapp/templates/base.html#L3-L5) goes charset → title → style. Without it, mobile browsers assume a 980px viewport and render the whole page scaled down — text will be tiny and require pinch-zoom on every page. |
| Media queries | **None.** Zero `@media` rules anywhere (grep-confirmed across all templates). |
| Fluid container | **Partially.** `max-width: 900px; margin: 40px auto; padding: 0 20px` ([base.html:7-8](../../scraper/webapp/templates/base.html#L7-L8)) means the body correctly shrinks below 900px and keeps a 20px gutter. This is the one thing that prevents horizontal overflow on narrow screens. |
| Results table | **Overflows.** `table { width: 100% }` with four columns and no `overflow-x: auto` wrapper ([base.html:15](../../scraper/webapp/templates/base.html#L15)). A long email address or social value in a cell has no break opportunity, so the table pushes past the viewport and the page scrolls horizontally. This is the most likely mobile failure in practice. |
| Textarea | **Fine.** `width: 100%` with `box-sizing: border-box` ([base.html:10-11](../../scraper/webapp/templates/base.html#L10-L11)). |
| File input + button | **Fine.** Natural width, would wrap or fit at 400px. |
| Type scale | Mixed. Text sizes are relative (`1.4em`, `0.9em`, `0.85em`, `0.8em`) so they scale with the browser default; spacing is absolute px (`40px`, `20px`, `8px`, `24px`) and does not. |
| Touch targets | Nav links and the results/export links are inline text at `0.9em` — below the ~44px comfortable tap target. The `button` has `10px 18px` padding and is adequate. |
| Dark mode | None. No `prefers-color-scheme` block, no `color-scheme` declaration. Hardcoded white background and `#222` text; renders as a bright white page regardless of OS setting. |

Combined verdict: at ~400px the layout does not break structurally — no
horizontal scroll from the shell — but text is rendered at the 980px fallback
scale, the results table can overflow, and nothing about the design was
considered for touch. There is no evidence responsive behavior was attempted;
the fluid body is a side effect of `max-width` rather than a responsive
decision.

---

## 8. Summary

The web UI is a genuinely thin, honest MVP: 252 lines of Python and 101 lines of
template implementing exactly the scrape → progress → results → CSV path its
docstring claims ([app.py:1-10](../../scraper/webapp/app.py#L1-L10)), with no
invented product surface. The core flow works and is covered by a real
end-to-end test against live Postgres and a live target server
([test_webapp.py:95-111](../../scraper/tests/test_webapp.py#L95-L111)).

Its weaknesses are all of omission rather than overreach:

1. **One real bug** — the progress poller freezes permanently on a 404 or any
   network error (B1/B2), reachable through normal use because sessions are
   in-memory and debug auto-reload is on.
2. **The most common user outcome is unexplained** — scraping a URL already in
   the database yields a blank table (B3/B4), which the test suite encodes as
   expected.
3. **Export is misleadingly labeled in practice** — per-session only in
   appearance, whole-database in fact (B5), writing to a hardcoded `/tmp` that
   becomes a stray `C:\tmp` on Windows (B6).
4. **Two of five CLI capabilities have no UI at all** — `verify` and `list`,
   plus `init-db` (B-table in §5).
5. **No design system to speak of** — 22 lines of CSS with no tokens, no
   spacing scale, and two routes that bypass the shell entirely.
6. **No responsive handling** — no viewport meta, no media queries, a table that
   overflows, and no dark mode.

There are no dead links. The links are fine; the pages behind them are what's
missing.
