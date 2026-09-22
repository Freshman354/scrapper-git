-- =============================================================
-- Migration 0001 — Initial scraper schema
--
-- Applied via migrations/run_migrations.py, which records completed
-- migrations in schema_migrations. That tracking table — not this file
-- — is the primary guard against re-running an already-applied
-- migration. The IF NOT EXISTS / CREATE OR REPLACE clauses below are a
-- secondary safety net for the fresh-database case (e.g. if this file
-- is ever run directly, outside the runner, against a database that
-- already has these objects) — they are not a claim that every
-- statement here is independently idempotent against a partially
-- modified schema.
--
-- Scope: this file creates exactly the objects in the frozen
-- scraper_schema.sql, nothing else. No Lumvi tables, no knowledge_base
-- references, no cross-database logic — the scraper database is a
-- fresh, isolated Neon database with no prior schema to reconcile.
--
-- Design notes tied to the frozen architecture:
--   * Suppression is NOT a table here and never will be. The
--     outreach system is the sole source of truth for contact
--     history/opt-outs; export queries it live, and that check
--     runs BEFORE email verification to avoid wasting verification
--     credits on contacts that would be excluded anyway.
--   * page_cache is the persistent crawl-result cache. Redis holds
--     only transient RQ queue/job state — no caching responsibility.
--   * `candidates.state` is the durable source of truth for the
--     crawl state machine; RQ workers read/update it.
--   * Entity-resolution thresholds (>=0.95 auto-merge, 0.75-0.94
--     possible_duplicate, <0.75 separate) are enforced in app code
--     (entity_resolution.py) and just recorded here via
--     merge_status / duplicate_of_id / match_score.
--   * Bounded verification retries: contacts.verification_attempts
--     caps provider retries; exhausting them sets verification_status
--     to 'unknown', never 'invalid' — a provider failure never proves
--     the email itself is undeliverable.
--   * Deliberately out of scope: CRM, outreach, local suppression,
--     or general lead-management tables. This schema covers
--     discovery -> crawl -> extraction -> storage only.
-- =============================================================

-- ---------------------------------------------------------
-- businesses: canonical business/store records
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS businesses (
    id                   BIGSERIAL PRIMARY KEY,
    canonical_domain     TEXT NOT NULL UNIQUE,   -- normalized: no scheme, no www, no trailing slash
    name                 TEXT,
    website              TEXT,
    description          TEXT,
    category             TEXT,
    subcategory          TEXT,
    country              TEXT,
    state                TEXT,
    city                 TEXT,
    address              TEXT,
    platform             TEXT,                   -- shopify, woocommerce, wordpress, webflow, wix, squarespace, bigcommerce, magento, custom, unknown
    platform_confidence  NUMERIC(3,2) CHECK (platform_confidence BETWEEN 0 AND 1),

    -- entity resolution
    merge_status         TEXT NOT NULL DEFAULT 'active'
                          CHECK (merge_status IN ('active', 'possible_duplicate', 'merged')),
    duplicate_of_id       BIGINT REFERENCES businesses(id),  -- set when merge_status IN ('merged','possible_duplicate')
    match_score           NUMERIC(3,2) CHECK (match_score BETWEEN 0 AND 1),  -- score vs duplicate_of_id

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_businesses_platform     ON businesses (platform);
CREATE INDEX IF NOT EXISTS idx_businesses_location     ON businesses (state, city);
CREATE INDEX IF NOT EXISTS idx_businesses_merge_status ON businesses (merge_status);


-- ---------------------------------------------------------
-- contacts: emails and phone numbers
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS contacts (
    id                     BIGSERIAL PRIMARY KEY,
    business_id            BIGINT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    type                   TEXT NOT NULL CHECK (type IN ('email', 'phone')),
    value                  TEXT NOT NULL,          -- normalized: lowercased email, E.164 phone
    source_url             TEXT,
    extraction_confidence  NUMERIC(3,2) CHECK (extraction_confidence BETWEEN 0 AND 1),

    -- email verification (unused for type = 'phone')
    verification_status    TEXT CHECK (verification_status IN ('pending', 'verified', 'risky', 'invalid', 'unknown')),
    verification_provider  TEXT,
    verified_at            TIMESTAMPTZ,

    -- bounded verification retry tracking — after max attempts, status becomes
    -- 'unknown' rather than 'invalid', since a provider failure never proves
    -- the email itself is undeliverable
    verification_attempts        INT NOT NULL DEFAULT 0,
    last_verification_attempt_at TIMESTAMPTZ,
    next_verification_attempt_at TIMESTAMPTZ,
    verification_failure_reason  TEXT,

    last_exported_at       TIMESTAMPTZ,

    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (business_id, type, value)
);

CREATE INDEX IF NOT EXISTS idx_contacts_business_id         ON contacts (business_id);
CREATE INDEX IF NOT EXISTS idx_contacts_verification_status ON contacts (verification_status);
CREATE INDEX IF NOT EXISTS idx_contacts_value               ON contacts (value);  -- entity resolution / suppression lookups
CREATE INDEX IF NOT EXISTS idx_contacts_next_verification_attempt ON contacts (next_verification_attempt_at)
    WHERE verification_status IS NULL OR verification_status = 'pending';


-- ---------------------------------------------------------
-- social_profiles
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS social_profiles (
    id          BIGSERIAL PRIMARY KEY,
    business_id BIGINT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    platform    TEXT NOT NULL CHECK (platform IN
                 ('instagram', 'facebook', 'tiktok', 'linkedin', 'x', 'youtube', 'pinterest')),
    url         TEXT NOT NULL,
    source_url  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (business_id, platform, url)
);

CREATE INDEX IF NOT EXISTS idx_social_profiles_business_id ON social_profiles (business_id);


-- ---------------------------------------------------------
-- evidence: auditable provenance for every extracted field
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence (
    id                 BIGSERIAL PRIMARY KEY,
    business_id        BIGINT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    field_name         TEXT NOT NULL,    -- e.g. 'platform', 'email', 'category'
    field_value        TEXT NOT NULL,
    source_url         TEXT,
    extraction_method  TEXT,             -- e.g. 'json-ld', 'mailto', 'shopify-cdn-asset', 'regex'
    confidence         NUMERIC(3,2) CHECK (confidence BETWEEN 0 AND 1),
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evidence_business_field ON evidence (business_id, field_name);


-- ---------------------------------------------------------
-- candidates: discovery output + crawl state machine
-- one row per domain tracked through the pipeline
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidates (
    id                  BIGSERIAL PRIMARY KEY,
    canonical_domain    TEXT NOT NULL UNIQUE,
    source              TEXT NOT NULL,        -- discovery provider: search, directories, places_api, sitemap, url_import
    discovery_metadata  JSONB,

    state               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (state IN ('pending', 'crawling', 'success', 'retry', 'blocked', 'abandoned')),
    attempts            INT NOT NULL DEFAULT 0,
    max_attempts         INT NOT NULL DEFAULT 5,
    last_attempt_at     TIMESTAMPTZ,
    next_attempt_at     TIMESTAMPTZ,
    failure_reason      TEXT,               -- diagnostics for state IN ('retry','blocked','abandoned'), e.g. 'timeout', '403 blocked', 'max_attempts_exceeded'

    business_id         BIGINT REFERENCES businesses(id),  -- set once resolved into a business record

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_candidates_state         ON candidates (state);
CREATE INDEX IF NOT EXISTS idx_candidates_next_attempt  ON candidates (next_attempt_at) WHERE state = 'retry';


-- ---------------------------------------------------------
-- crawl_runs: log of each individual crawl attempt
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS crawl_runs (
    id                BIGSERIAL PRIMARY KEY,
    candidate_id      BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    status            TEXT CHECK (status IN ('success', 'failed', 'blocked', 'timeout')),
    http_status_code  INT,
    pages_crawled     INT NOT NULL DEFAULT 0,
    used_browser      BOOLEAN NOT NULL DEFAULT false,  -- true if Tier 2 (Playwright) fallback fired
    error_message     TEXT
);

CREATE INDEX IF NOT EXISTS idx_crawl_runs_candidate_id ON crawl_runs (candidate_id);


-- ---------------------------------------------------------
-- crawl_pages: individual URLs fetched within a crawl_run
-- (audit trail — which pages were hit, how, and with what result)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS crawl_pages (
    id                BIGSERIAL PRIMARY KEY,
    crawl_run_id      BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
    url               TEXT NOT NULL,
    page_type         TEXT,             -- homepage, contact, about, products, collection, blog, other
    fetched_via       TEXT CHECK (fetched_via IN ('http', 'browser')),
    http_status_code  INT,
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    -- correlates to page_cache.url for cached content_hash/etag; no FK,
    -- kept as an append-only log independent of cache eviction
);

CREATE INDEX IF NOT EXISTS idx_crawl_pages_crawl_run_id ON crawl_pages (crawl_run_id);
CREATE INDEX IF NOT EXISTS idx_crawl_pages_url          ON crawl_pages (url);


-- ---------------------------------------------------------
-- page_cache: persistent crawl-result cache (skip re-fetching unchanged pages)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS page_cache (
    id            BIGSERIAL PRIMARY KEY,
    url           TEXT NOT NULL UNIQUE,
    content_hash  TEXT,
    status_code   INT,
    etag          TEXT,
    last_modified TEXT,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ---------------------------------------------------------
-- updated_at auto-touch
-- ---------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_businesses_updated_at
    BEFORE UPDATE ON businesses
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_candidates_updated_at
    BEFORE UPDATE ON candidates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
