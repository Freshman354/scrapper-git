# Step 1 — AFTER: rebuilt `base.html` and `new_scrape.html`

**Status:** Step 1 built and verified. Step 2 not started.
**Date:** 2026-09-22
**Scope:** `scraper/webapp/templates/base.html`, `scraper/webapp/templates/new_scrape.html`, plus the scaffolding in §6.

Companion to [`CURRENT_TEMPLATES.md`](CURRENT_TEMPLATES.md), which describes the
pre-rebuild baseline this replaces. Raw captures and screenshots below.

---

## 1. What changed

| File | Change |
|---|---|
| [`base.html`](../../scraper/webapp/templates/base.html) | Rewritten — 33 → 57 lines. Inline `<style>` removed entirely; Tailwind via CDN; four-item nav; `title`/`head` blocks; viewport meta and `lang`. |
| [`new_scrape.html`](../../scraper/webapp/templates/new_scrape.html) | Rewritten — 11 → 83 lines. Two-column card layout, styled file input, error alert, "what happens next" strip, submit guard. |
| `app.py` | Placeholder routes only — see §6. No logic, no queries. |
| `_placeholder.html` | New, throwaway — see §6. |

Page weight went from **1,968 bytes to 7,426 bytes** (CSS now comes from the CDN rather
than an inline block).

---

## 2. New `base.html` — the shell

### Head

| Element | Value |
|---|---|
| `<html>` | **`lang="en"`** — was missing entirely |
| charset | `utf-8` (unchanged) |
| viewport | **`width=device-width, initial-scale=1`** — was absent, which is what forced mobile browsers to assume a 980px viewport |
| `<title>` | `{% block title %}Scraper{% endblock %}` — was a hardcoded literal |
| Tailwind | `<script src="https://cdn.tailwindcss.com"></script>` |
| `{% block head %}` | New — per-page head additions |
| favicon | Still not referenced |

The old 22-line `<style>` block is **gone**, not ported. Everything it defined (`.muted`,
`.error`, `.status-badge` and its variants, `h1`, `table`, `th`, `td`, `button`,
`textarea`, `nav`) is now expressed as Tailwind utilities in the markup that uses them.

### Nav

```
┌──────────────────────────────────────────────────────────────┐
│  [S] Scraper   Dashboard  [New Scrape]  Verify  Export       │  ← white, sticky, 1px slate-200 bottom border
└──────────────────────────────────────────────────────────────┘
```

- White bar, `sticky top-0 z-40`, `border-b border-slate-200`, `bg-white/95` with
  `backdrop-blur` so content scrolling under it stays legible.
- Brand mark: a 24×24 `blue-600` rounded square containing a bold white `S`, then the
  wordmark `Scraper` in `slate-900` semibold. The mark is `aria-hidden` — it's decoration,
  the wordmark carries the meaning.
- Four nav links. **Active page** gets `bg-blue-50 text-blue-700` plus `aria-current="page"`;
  inactive links are `text-slate-600` with `hover:bg-slate-100`.
- Every nav link is `min-h-[44px]` — the tap-target requirement, applied rather than assumed.
- **Wrap behaviour is deliberate.** The brand sits outside the link group, and the links
  live in their own `flex flex-wrap` wrapper. Below ~420px the brand takes line one and all
  four links wrap to line two *as a unit* — rather than filling line one until it overflows
  and stranding `Export` alone on line two, which is what a single flat `flex-wrap` produces.

### Container and page frame

| Property | Value |
|---|---|
| Page background | `bg-slate-50` (was browser-default white) |
| Body text | `text-slate-900`, `antialiased` |
| Body layout | `flex min-h-screen flex-col` — so the footer sits at the bottom on short pages |
| Shell width | `max-w-7xl` (1280px) — was 900px |
| Main padding | `px-4 py-8 sm:px-6 lg:px-8` |
| Footer | New — one line of `text-xs text-slate-400` |

A **skip-to-content link** was added as the first focusable element: `sr-only` until
focused, then it appears top-left over the page. It targets `#main`.

---

## 3. New `new_scrape.html` — top to bottom

| # | Element | Detail |
|---|---|---|
| 1 | Nav | Four links, `New Scrape` active |
| 2 | `h1` | `New Scrape` — `text-2xl` semibold, tight tracking |
| 3 | Lead | `Paste store URLs, one per line, or upload a .txt / .csv file.` — `text-sm text-slate-500` |
| 4 | Error alert | Only when `error` is set — rose card, `role="alert"` |
| 5 | Two-column grid | `md:grid-cols-2`, `gap-6`; stacks below 768px |
| 6 | Left card — Store URLs | Label, "One per line." hint, 10-row monospace textarea with the two example URLs as placeholder |
| 7 | Right card — upload | Label, ".txt or .csv" hint, dashed drop-zone containing a styled file input, footnote that a pasted CSV also works |
| 8 | Action row | `Start Scrape` primary button + "Runs in the background" note |
| 9 | "What happens next" | Four numbered stages in a card |
| 10 | Footer | From the shell |

**Cards** are `rounded-lg border border-slate-200 bg-white p-5 shadow-sm` — the page's basic
unit, replacing the old bare stacked controls.

**The textarea** keeps `name="urls"` and the same two example URLs. It added `rows="10"`,
`font-mono`, a `resize-y` handle, and a `blue-500` focus ring. It's `flex-1` inside a
`flex flex-col` card, so both cards match height in the two-column layout.

**The file input** keeps `name="file"` and `accept=".txt,.csv"` — the form contract is
untouched. It's styled with Tailwind's `file:` modifier, which styles the native button
without replacing the input (replacing it would break the multipart upload). It sits inside
a `border-2 border-dashed border-slate-300 bg-slate-50` box for the drop-zone affordance.

**The error alert** is now a rose card with `role="alert"` rather than a bare red `<p>`.
The message text comes straight from `app.py` and is unchanged — `Enter at least one URL,
or upload a file.`

**The "what happens next" strip** names the four automatic stages (Discover → Crawl →
Extract → Verify). It exists because every stage runs unattended, which was previously
undiscoverable from the UI.

**One line of JavaScript** disables the submit button and relabels it `Starting…` on
submit. The audit found the button stayed clickable through the POST/redirect, so a
double-click submitted twice. If the server re-renders this page on a validation error the
button comes back fresh — verified, see §5.

---

## 4. Colours and spacing

### Palette — Tailwind tokens, no custom config

| Token | Where |
|---|---|
| `slate-50` | Page background |
| `slate-100` | Nav hover background, step-number badge background |
| `slate-200` | Header bottom border, card borders |
| `slate-300` | Textarea border, dashed drop-zone border |
| `slate-400` | Footer text, textarea placeholder |
| `slate-500` | Lead text, hints, footnotes |
| `slate-600` | Inactive nav links, file-input text, step badge text |
| `slate-900` | Headings, body text, labels |
| `blue-50` | Active nav link background |
| `blue-500` | Focus rings and focus borders |
| `blue-600` | Primary button, brand mark, file-input button |
| `blue-700` | Button hover, active nav text |
| `rose-50` / `rose-200` / `rose-800` | Error alert (bg / border / text) |
| `white` | Header, cards |

Stock Tailwind — **no `tailwind.config` block, no custom colours, no CSS variables.** The
palette is the framework's own slate/blue scales, which keeps the whole thing swappable by
changing class names alone.

This is deliberately nothing like Lumvi: no dark ink ground, no gold, no serif. Sans
throughout (Tailwind's default `font-sans` system stack), grey-and-blue, flat.

### Spacing

| Value | Where |
|---|---|
| `max-w-7xl` | Shell container |
| `max-w-4xl` | New-scrape content |
| `px-4 py-8` → `sm:px-6 lg:px-8` | Main padding, scaling with breakpoint |
| `gap-6` / `space-y-6` | Grid and form rhythm |
| `mt-8` | Between the form and the "what happens next" card |
| `p-5` / `p-6` | Cards / drop-zone |
| `min-h-[44px]` | Every nav link and both buttons |
| `rounded-lg` / `rounded-md` | Cards / controls |

The old hardcoded `40px` / `24px` / `8px` pixel values are gone; spacing now comes from
Tailwind's 4px scale, so it's consistent by construction rather than by coincidence.

---

## 5. Verification

### Boots clean

```
 * Serving Flask app 'app'
 * Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment.
 * Running on http://127.0.0.1:5050
Press CTRL+C to quit
```

`Debug mode: off`, **zero** occurrences of `traceback` / `error` / `exception` in the log.
`FLASK_DEBUG` was not set — the opt-in fix holds.

### Routes

| Route | Status |
|---|---|
| `/` | 200 (placeholder — was 302) |
| `/scrape/new` | 200 |
| `/verify` | 200 (placeholder) |
| `/export` | 200 (placeholder) |
| `/scrape/nonexistent/progress` | 404 (unchanged) |
| `POST /scrape/new` with empty `urls` | 200, renders the error alert |

### Tests — the two requested

```
scraper/tests/test_webapp.py::test_new_scrape_page_renders PASSED        [ 50%]
scraper/tests/test_webapp.py::test_start_scrape_with_no_urls_shows_error PASSED [100%]
============================== 2 passed in 1.95s ==============================
```

These assert on rendered text, not markup — the restyle kept both strings, so they pass
unchanged. They confirm the form and its error path survived, not that the page looks right.

### Full suite — run because the shell rewrite could have broken it

```
8 passed in 31.26s
```

All eight, including both Playwright poller tests (`test_progress_poller_reports_a_lost_session_instead_of_freezing`,
`test_progress_poller_recovers_when_retried`). Worth stating why I ran these: the poller
tests drive real browser JavaScript, and they depend on elements that live inside the
template I rewrote. They pass against the new shell, so the B1/B2 fix is intact.

### Tailwind CDN actually loads

`https://cdn.tailwindcss.com` → 302 → **200, 407,279 bytes, `text/javascript`**. The design
is not a no-op; the utility classes resolve.

### Screenshots

| File | Shows |
|---|---|
| [`new_scrape_desktop.png`](new_scrape_desktop.png) | 1280px — two-column form, nav, staged strip |
| [`new_scrape_mobile.png`](new_scrape_mobile.png) | 375px — nav wrapped, cards stacked |
| [`new_scrape_error_state.png`](new_scrape_error_state.png) | The rose validation alert |
| [`progress_NOT_restyled.png`](progress_NOT_restyled.png) | The transition state — see §7 |

Raw HTML: [`scrape_new_after.html`](scrape_new_after.html) (clean, 7,426 bytes) and
[`scrape_new_after_error.html`](scrape_new_after_error.html) (error state, 7,627 bytes).

### Mobile check

Measured at 375px: `scrollWidth=375`, `clientWidth=375` — **no horizontal overflow.** The
nav wraps as designed (brand on line one, all four links on line two), and the two-column
grid stacks to one column.

### Error state and the submit guard

With an empty submission the alert renders with the exact expected text, and the submit
button reads **`Start Scrape`** again — not `Starting…`. That confirms the disable-on-submit
guard doesn't survive a server re-render and leave the form permanently unusable, which was
the obvious failure mode of that one line of JS.

---

## 6. Deviations from the plan — read this

**Three placeholder routes and one throwaway template were added that the plan did not
call for.** Both are scaffolding for step 1, not step 2 work.

The plan's §1 nav has four links, but three of their destinations don't exist until steps
3, 5 and 6. `url_for` raises `BuildError` at render time for an unregistered endpoint — so
a four-link nav would have thrown a 500 on every page. Rather than ship a nav with dead
links or a one-link nav that defeats the point of reviewing its shape, I added:

| Route | Endpoint | Renders | Replaced by |
|---|---|---|---|
| `/` | `dashboard` | `_placeholder.html` | Step 3 |
| `/verify` | `verify_page` | `_placeholder.html` | Step 7 |
| `/export` | `export_page` | `_placeholder.html` | Step 5 |

These contain **no database access and no logic** — they render a title and a "not built
yet" note. `_placeholder.html` is deleted as the last of them lands.

Two knock-on effects worth naming:

- **`/` changed from a 302 redirect to a 200 placeholder.** It previously redirected to
  `/scrape/new`. No test covers `/`, but it is a behaviour change in step 1.
- **`NEW` — two pages are now visually unstyled.** See §7. This is the more significant one.

If you'd rather I hadn't touched `app.py` at all, say so and I'll rip the three routes out
and reduce the nav to the one link that resolves — but the nav shape is not reviewable in
that state.

---

## 7. Known consequence: `progress.html` and `results.html` are now unstyled

Removing the inline `<style>` from `base.html` removed the rules those two pages still
depend on. They still extend the shell and still reference `.muted`, `.error` and
`.status-badge`, which **no longer exist anywhere**.

They are **functionally intact** — all 8 tests pass, including both browser-driven poller
tests — but visually they are now plain unstyled text. See
[`progress_NOT_restyled.png`](progress_NOT_restyled.png): the nav and page frame are the new
design, the content below it is browser defaults. The `1 URL(s) submitted.` line has lost
its grey `.muted` treatment, and the `h1` has lost its sizing.

This is expected mid-rebuild and resolves at steps 4 (results) and 6 (progress). Flagging it
because it's a visible regression in the working tree right now, not something you'd want to
discover by opening the app.

---

## 8. Before / after

| Aspect | Before | After |
|---|---|---|
| Styling | 22-line inline `<style>` | Tailwind via CDN, stock config |
| Page background | browser white | `slate-50` |
| Accent colour | none — greys only | `blue-600` |
| Primary button | `#1a1a1a` near-black | `blue-600` |
| Nav | 1 link, inline text, sub-44px | 4 links, sticky bar, 44px targets, active state |
| `<title>` | literal `Scraper` everywhere | `{% block title %}` per page |
| `<html lang>` | absent | `en` |
| viewport meta | absent | present |
| Layout | single stacked column | 2-column cards on desktop, stacked on mobile |
| File input | raw browser chrome | styled via `file:` variants in a dashed drop-zone |
| Error display | bare red `<p>` | rose card with `role="alert"` |
| Double-submit | possible | guarded |
| Shell width | 900px | 1280px |
| Accessibility | none | skip link, `aria-current`, `aria-label`, `role="alert"`, `aria-hidden` on decoration |
| Response size | 1,968 bytes | 7,426 bytes |

**Net:** the baseline was functional-but-unconsidered — near-black on white, one nav link,
three unrelated-looking controls stacked with default spacing. Step 1 replaces it with a
card-based slate/blue shell that carries a real nav, works at 375px, and has a place for
per-page titles and head content. Steps 2–8 inherit these decisions.
