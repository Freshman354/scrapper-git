# `base.html` and `new_scrape.html` — as they exist right now

**These are the ORIGINAL templates from commit `875a704`. Nothing has been rebuilt.**

Step 1 of [FRONTEND_REBUILD_PLAN.md](../FRONTEND_REBUILD_PLAN.md) — "rewrite `base.html`,
restyle `new_scrape.html`" — has **not** been built. No code has been written, per the
instruction to wait for approval before writing any. This file therefore describes the
**pre-rebuild baseline**, which is the honest answer to "what do these templates look
like" today, and is the thing the plan proposes to replace.

Companion file: [`scrape_new_baseline.html`](scrape_new_baseline.html) — the raw HTML of
`GET /scrape/new`, captured from a live server, byte-for-byte.

- Captured: 2026-09-22
- Source: `scraper/webapp/templates/base.html` (33 lines), `scraper/webapp/templates/new_scrape.html` (11 lines)
- Live capture: `curl http://127.0.0.1:5050/scrape/new` → 200, `text/html; charset=utf-8`, 1968 bytes

---

## 1. `base.html` — the shell

Every page extends this. It is `<head>`, one inline `<style>` block (22 lines), a one-link
nav, and a single content block. There is no footer, no header bar, no logo, no breadcrumb,
no account area.

### Head

| Element | Value |
|---|---|
| `<html>` | **No `lang` attribute** |
| charset | `utf-8` |
| `<title>` | Literal `Scraper` — a hardcoded string, **not** a `{% block title %}`. All pages share one tab title. |
| viewport meta | **Absent.** This is the root of the mobile problem: phones assume a 980px viewport and scale the whole page down. |
| External CSS/JS | **None.** No `<link>`, no `<script src>`, no CDN. All styling is the inline block below. |
| favicon | Not referenced. Every page 404s on `/favicon.ico`. |

### Nav

A single left-aligned text link, rendered above all content:

```
+ New Scrape
```

- One item only — `+ New Scrape` → `/scrape/new`. It functions as "start over", not as
  navigation, since it cannot take you anywhere you aren't already.
- `nav` is `margin-bottom: 24px`, `font-size: 0.9em`.
- `nav a` is `color: #555`, **no underline**, `margin-right: 14px`; underline appears on hover.
- It is inline text at `0.9em` — well under a comfortable 44px touch target.

### Body container

| Property | Value |
|---|---|
| Font stack | `-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif` (system sans; monospace is used *only* for the textarea) |
| `max-width` | `900px` |
| Margin | `40px auto` (centred, 40px top and bottom) |
| Padding | `0 20px` |
| Text colour | `#222` |
| `line-height` | `1.5` |

Background is never declared — it is the browser default white. There is no dark-mode
handling and no `prefers-color-scheme` block, so the page renders bright white regardless
of OS setting.

### The complete stylesheet

22 lines, ~20 rules, 11 selectors. **No CSS custom properties, no colour tokens, no spacing
scale, no type scale** — every value is a literal.

**Typography**

| Selector | Rules |
|---|---|
| `h1` | `font-size: 1.4em`, `margin-bottom: 4px` (browser-default `margin-top` is left in place, so there is still a gap above it) |
| `.muted` | `color: #777`, `font-size: 0.9em` |
| `.error` | `color: #b00020`, `margin: 10px 0` |

Only one heading level is styled. A second level (`h2`, `h3`) would have no styling at all.

**Form controls**

| Selector | Rules |
|---|---|
| `textarea` | `width: 100%`, `height: 160px`, `font-family: monospace`, `padding: 8px`, `box-sizing: border-box`, `border: 1px solid #ccc`, `border-radius: 4px` |
| `button` | `background: #1a1a1a`, `color: white`, `border: none`, `padding: 10px 18px`, `border-radius: 4px`, `cursor: pointer`, `font-size: 1em` |
| `button:hover` | `background: #333` |
| `input` | **Never styled.** The file picker renders as raw browser chrome — the one unstyled control on the page. |

There is no `.btn`/`.button` class. Because the rule targets the `button` element, any
future `<a>` that should look like a button will not.

**Table** (not used by this page, used by `results.html`)

| Selector | Rules |
|---|---|
| `table` | `width: 100%`, `border-collapse: collapse`, `margin-top: 20px` |
| `th, td` | `text-align: left`, `padding: 8px`, `border-bottom: 1px solid #eee`, `vertical-align: top` |
| `th` | `color: #666`, `font-size: 0.85em`, `text-transform: uppercase` |

No `overflow-x` wrapper and no striping or row hover, which is why a long email or URL in a
cell can push the table past the viewport on narrow screens.

**Status badges** (defined here, consumed only by `results.html`)

| Selector | Rules |
|---|---|
| `.status-badge` | `display: inline-block`, `padding: 1px 7px`, `border-radius: 3px`, `font-size: 0.8em`, `margin-left: 4px` |
| `.status-verified` | bg `#d4edda`, text `#155724` (green) |
| `.status-invalid`, `.status-blocked`, `.status-abandoned` | bg `#f8d7da`, text `#721c24` (red) |
| `.status-unknown`, `.status-retry` | bg `#fff3cd`, text `#856404` (amber) |

A `null` status falls through to no background at all, rendering as unbadged text.

### Every colour in the file

| Hex | Used for |
|---|---|
| `#222` | body text |
| `#1a1a1a` | button background (near-black) |
| `#333` | button background on hover |
| `#555` | nav links |
| `#666` | table header text |
| `#777` | `.muted` secondary text |
| `#b00020` | `.error` red |
| `#ccc` | textarea border |
| `#eee` | table row divider |
| `#d4edda` / `#155724` | verified badge (bg / text) |
| `#f8d7da` / `#721c24` | invalid-blocked-abandoned badge (bg / text) |
| `#fff3cd` / `#856404` | unknown-retry badge (bg / text) |

Note the nav link `#555` and the body text `#222` are both near-black greys — there is no
accent colour anywhere in the UI. The only saturated colours are the semantic badge pairs
and the error red.

### Content block

```
{% block content %}{% endblock %}
```

The **only** block the template defines. There is no `title`, `head`, `nav`, or `scripts`
block, which is why every page has an identical tab title and why the two bare-text 404
responses have no way to reuse the shell.

---

## 2. `new_scrape.html` — top to bottom

11 lines. Extends `base.html`, fills `content`.

| # | Element | Detail |
|---|---|---|
| 1 | Nav | `+ New Scrape` (from the shell) |
| 2 | `h1` | `New Scrape` — 1.4em bold |
| 3 | `p.muted` | `Paste store URLs, one per line, or upload a .txt/.csv file.` — 0.9em, `#777` |
| 4 | Error alert | `{% if error %}<p class="error">{{ error }}</p>{% endif %}` — red `#b00020`. **Absent on a clean render**; appears only after an empty submission. |
| 5 | `<form>` | `method="post"`, `enctype="multipart/form-data"`, **no `action`** — posts back to `/scrape/new` |
| 6 | `<textarea name="urls">` | Full width, 160px tall, monospace, placeholder showing two example store URLs on separate lines |
| 7 | `<input type="file" name="file" accept=".txt,.csv">` | Wrapped in its own `<p>`, on its own line. Unstyled. |
| 8 | `<button type="submit">` | `Start Scrape` — near-black `#1a1a1a`, white text, `10px 18px` padding, `4px` radius |

Then the shell closes with `</body></html>`. Nothing below the button — no footer, no
secondary content.

### Rendered output

`GET /scrape/new` returns **1968 bytes**, of which ~1400 are the inline stylesheet. The
body content is roughly 300 bytes: a nav link, an `h1`, a muted paragraph, and a form with
three controls.

Because the error branch is not taken, **lines 31 and 40 of the captured HTML are blank** —
those are where the Jinja `{% block content %}` and form tags sat before rendering. The
captured file is the literal response body, unstripped.

### Visual character

Near-black and white with one grey accent. The only colour on a clean render is the
near-black `Start Scrape` button; everything else is `#222` text on white with `#777` for
the lead-in. It reads as functional-but-unconsidered — no card, no border, no grouping, no
whitespace rhythm beyond the browser defaults plus a 40px page margin. The textarea, file
input, and button are three unrelated-looking controls stacked with default spacing.

### Spacing

| Value | Where |
|---|---|
| `40px` | body top margin |
| `20px` | body side padding |
| `24px` | nav bottom margin |
| `4px` | h1 bottom margin |
| `160px` | textarea height |
| `8px` | textarea padding, table cell padding |
| `10px 18px` | button padding |

Absolute pixels throughout — these do not scale with the browser's font-size setting, while
the `em`-based type sizes do.

---

## 3. Behavioural notes captured at the same time

- **Boots clean with debug off.** `python -m scraper.webapp.app` logs `Debug mode: off` and
  no reloader. No errors, no warnings beyond Werkzeug's standard development-server notice.
- `GET /scrape/new` → `200`
- `GET /` → `302` (redirect to `/scrape/new`)
- Response headers: `Server: Werkzeug/3.1.7 Python/3.12.10`, `Content-Type: text/html; charset=utf-8`,
  `Content-Length: 1968`, `Connection: close`
- The form has **no submit guard** — the button stays fully clickable during the POST/redirect,
  so a double-click submits twice.
- There is **no client-side URL validation**. A bare `example.com` with no scheme is accepted
  silently and fails later inside the crawler, visible only as a `failed` count on the
  progress page.

---

## 4. What the plan proposes to change here

For comparison when reviewing [FRONTEND_REBUILD_PLAN.md](../FRONTEND_REBUILD_PLAN.md) §1.2,
the rebuild of these two files is:

| Aspect | Now | Proposed |
|---|---|---|
| Styling | 22-line inline `<style>` | Tailwind via CDN |
| Palette | greys + near-black button | slate neutrals, `blue-600` primary |
| Nav | one link, inline text | four links, sticky bar, 44px targets |
| `<title>` | literal `Scraper` | `{% block title %}` per page |
| viewport meta | absent | present |
| Layout | single stacked column | two-column form on desktop, stacked on mobile |
| File input | raw browser chrome | styled drop-zone |
| Double-submit | possible | guarded |

The palette, spacing, and typography decisions in that plan are the ones that everything
after step 1 inherits, which is why the plan recommends pausing for a look before step 2.
