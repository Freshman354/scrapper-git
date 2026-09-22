# Shopify Scraper: Original Design

The intended design is a modular business/store data scraper. It is not a CRM or an outreach platform.

## Overall pipeline

```
Discovery → Crawl → Extraction → Detection → Processing → Storage → Verification → Export
```

Orchestration sits around the pipeline. It coordinates stages and manages jobs and state, rather than embedding business logic in every module.

---

## 1. Discovery

Find candidate business URLs from interchangeable sources:

- Search engines
- Directories
- Places API
- Sitemaps
- Direct URL import

Each source implements the same `DiscoveryProvider` interface, so adding a new source should not require changing the rest of the system.

Providers only discover URLs. They do not normalize, deduplicate, or save them.

## 2. Crawling

Take a candidate domain and crawl it intelligently.

Approach: **HTTP first, Playwright only when necessary.**

The crawler should:

- Respect `robots.txt`
- Rate-limit requests
- Handle 403/429 responses and circuit breakers
- Prioritize useful internal pages
- Use cached pages where appropriate
- Produce `FetchedPage` objects

The crawler must not know about Postgres, Redis, Flask, or extraction. It is an isolated component.

## 3. Extraction

Turn crawled HTML into raw signals.

Order: **structured data first, then specialized extraction.**

Extracted fields:

- Business name, category, etc.
- Emails
- Phone numbers
- Social profiles
- Products

Every extracted field carries provenance:

| Provenance field | Meaning |
|---|---|
| field | What was extracted |
| value | The extracted value |
| source URL | The page it came from |
| extraction method | How it was extracted |
| confidence | How reliable it is |

The system knows not just what it found, but where and how it found it.

## 4. Detection

Determine the store platform, for example:

- Shopify
- WooCommerce
- Other platforms

Retain the evidence supporting each detection. Instead of storing only:

> platform = Shopify

the system stores:

> platform = Shopify + evidence showing why

## 5. Processing

Clean and reconcile the raw information:

```
Normalization → Entity Resolution → Validation → Confidence
```

This is where messy discoveries become usable business records. For example, multiple pages may produce slightly different representations of the same business. Entity resolution decides whether they are the same entity instead of blindly creating duplicates.

## 6. Storage

Postgres is the persistent source of truth.

The design separates domain objects from the database implementation. Modules exchange plain dataclasses, not ORM rows or database objects.

Core tables:

- `businesses`
- `contacts`
- `social_profiles`
- `evidence`
- `candidates`
- `crawl_runs`
- `crawl_pages`
- `page_cache`

The storage layer translates between domain objects and Postgres.

## 7. Verification

Email verification is deliberately separate from crawling.

```
contacts discovered
       ↓
suppression check
       ↓
email verification
       ↓
verified / risky / invalid / unknown
```

**Export must never trigger a paid verification lookup.** Verification runs independently, on its own job or schedule.

## 8. Export

Produce usable datasets in these formats:

- CSV
- JSON
- XLSX

Export applies eligibility rules, such as verification status and external suppression, before producing the file.

The file must be written successfully **before** `last_exported_at` is updated. This prevents a failed export from falsely marking contacts as exported.

---

## Architecture

```
┌──────────────┐
│  DISCOVERY   │
└──────┬───────┘
       ↓
┌──────────────┐
│    CRAWL     │
└──────┬───────┘
       ↓
┌──────────────┐
│  EXTRACTION  │
└──────┬───────┘
       ↓
┌──────────────┐
│  DETECTION   │
└──────┬───────┘
       ↓
┌──────────────┐
│  PROCESSING  │
└──────┬───────┘
       ↓
┌──────────────┐
│   STORAGE    │
└──────┬───────┘
       ↓
┌──────┴────────────┐
↓                   ↓
VERIFICATION      EXPORT
```

## Key design principle

> Each stage does one job, communicates through simple domain types, and can be replaced or expanded without rewriting the rest of the scraper.

This is why a new discovery source or a better email-verification provider can be added without redesigning the crawler or extraction system.
