"""Tests for scraper/migrations/run_migrations.py.

Runs against a real local Postgres database (not a mock) so that
transaction/rollback behavior is genuine, not simulated. Each test
resets the public schema first for isolation.
"""

import psycopg2
import pytest

from scraper.migrations.run_migrations import (
    MIGRATIONS_DIR,
    MigrationError,
    discover_migration_files,
    get_database_url,
    run_migrations,
)

TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost/scraper_test"


@pytest.fixture
def clean_db():
    """Reset the test database to a blank public schema before the test."""
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE;")
        cur.execute("CREATE SCHEMA public;")
    conn.close()
    yield TEST_DATABASE_URL


def _write(path, name, sql):
    (path / name).write_text(sql)


def _table_exists(database_url, table_name) -> bool:
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s);",
                (table_name,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _applied_filenames(database_url):
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT filename FROM schema_migrations ORDER BY applied_at;")
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


# --- discover_migration_files (pure, no DB) --------------------------------

def test_discover_migration_files_sorts_by_filename(tmp_path):
    _write(tmp_path, "0002_second.sql", "SELECT 1;")
    _write(tmp_path, "0001_first.sql", "SELECT 1;")
    _write(tmp_path, "0010_tenth.sql", "SELECT 1;")

    files = discover_migration_files(tmp_path)

    assert [f.name for f in files] == ["0001_first.sql", "0002_second.sql", "0010_tenth.sql"]


# --- get_database_url isolation ---------------------------------------------

def test_get_database_url_raises_when_unset(monkeypatch):
    monkeypatch.delenv("SCRAPER_DATABASE_URL", raising=False)
    with pytest.raises(MigrationError):
        get_database_url()


def test_get_database_url_does_not_fall_back_to_database_url(monkeypatch):
    """Even if a generic DATABASE_URL is set (e.g. Lumvi's own), the
    runner must not use it — isolation from Lumvi's connection config
    is the whole point."""
    monkeypatch.delenv("SCRAPER_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://not-the-scraper-db/lumvi")
    with pytest.raises(MigrationError):
        get_database_url()


def test_get_database_url_reads_scraper_specific_var(monkeypatch):
    monkeypatch.setenv("SCRAPER_DATABASE_URL", TEST_DATABASE_URL)
    assert get_database_url() == TEST_DATABASE_URL


# --- ordering + successful application --------------------------------------

def test_migrations_applied_in_filename_order_even_when_written_out_of_order(tmp_path, clean_db):
    # 0002 depends on a column existing only after 0001 runs — this only
    # succeeds if 0001 is applied first, regardless of directory order.
    _write(tmp_path, "0002_add_column.sql", "ALTER TABLE widgets ADD COLUMN name TEXT;")
    _write(tmp_path, "0001_create_table.sql", "CREATE TABLE widgets (id SERIAL PRIMARY KEY);")

    applied = run_migrations(migrations_dir=tmp_path, database_url=clean_db)

    assert applied == ["0001_create_table.sql", "0002_add_column.sql"]
    assert _applied_filenames(clean_db) == ["0001_create_table.sql", "0002_add_column.sql"]


def test_successful_migration_creates_table_and_records_it(tmp_path, clean_db):
    _write(tmp_path, "0001_create_widgets.sql", "CREATE TABLE widgets (id SERIAL PRIMARY KEY);")

    applied = run_migrations(migrations_dir=tmp_path, database_url=clean_db)

    assert applied == ["0001_create_widgets.sql"]
    assert _table_exists(clean_db, "widgets") is True
    assert _applied_filenames(clean_db) == ["0001_create_widgets.sql"]


# --- rollback on failure -----------------------------------------------------

def test_failed_migration_rolls_back_completely(tmp_path, clean_db):
    # First statement would succeed in isolation; second is invalid SQL.
    # Because both run in one transaction, the whole migration must
    # roll back — including the otherwise-valid CREATE TABLE.
    _write(
        tmp_path,
        "0001_broken.sql",
        "CREATE TABLE partial_table (id SERIAL PRIMARY KEY); THIS IS NOT VALID SQL;",
    )

    with pytest.raises(MigrationError):
        run_migrations(migrations_dir=tmp_path, database_url=clean_db)

    assert _table_exists(clean_db, "partial_table") is False
    assert _applied_filenames(clean_db) == []


def test_failure_stops_later_migrations_from_being_applied(tmp_path, clean_db):
    _write(tmp_path, "0001_ok.sql", "CREATE TABLE widgets (id SERIAL PRIMARY KEY);")
    _write(tmp_path, "0002_broken.sql", "THIS IS NOT VALID SQL;")
    _write(tmp_path, "0003_also_ok.sql", "CREATE TABLE gadgets (id SERIAL PRIMARY KEY);")

    with pytest.raises(MigrationError):
        run_migrations(migrations_dir=tmp_path, database_url=clean_db)

    # 0001 committed before 0002 failed; 0003 was never attempted.
    assert _table_exists(clean_db, "widgets") is True
    assert _table_exists(clean_db, "gadgets") is False
    assert _applied_filenames(clean_db) == ["0001_ok.sql"]


# --- idempotency: rerun after success is a no-op -----------------------------

def test_rerunning_after_success_is_a_noop(tmp_path, clean_db):
    _write(tmp_path, "0001_create_widgets.sql", "CREATE TABLE widgets (id SERIAL PRIMARY KEY);")

    first_run = run_migrations(migrations_dir=tmp_path, database_url=clean_db)
    second_run = run_migrations(migrations_dir=tmp_path, database_url=clean_db)

    assert first_run == ["0001_create_widgets.sql"]
    assert second_run == []
    assert _applied_filenames(clean_db) == ["0001_create_widgets.sql"]  # not duplicated


def test_rerun_after_success_applies_only_new_migrations(tmp_path, clean_db):
    _write(tmp_path, "0001_create_widgets.sql", "CREATE TABLE widgets (id SERIAL PRIMARY KEY);")
    run_migrations(migrations_dir=tmp_path, database_url=clean_db)

    # A new migration shows up later, alongside the already-applied one.
    _write(tmp_path, "0002_create_gadgets.sql", "CREATE TABLE gadgets (id SERIAL PRIMARY KEY);")
    second_run = run_migrations(migrations_dir=tmp_path, database_url=clean_db)

    assert second_run == ["0002_create_gadgets.sql"]
    assert _applied_filenames(clean_db) == ["0001_create_widgets.sql", "0002_create_gadgets.sql"]


# --- the real migration file -------------------------------------------------

def test_0001_initial_schema_applies_cleanly(clean_db):
    """The actual frozen-schema migration, applied against a real
    database, exactly as run_migrations would apply it in production."""
    applied = run_migrations(migrations_dir=MIGRATIONS_DIR, database_url=clean_db)

    assert applied == ["0001_initial_schema.sql"]
    for table in (
        "businesses", "contacts", "social_profiles", "evidence",
        "candidates", "crawl_runs", "crawl_pages", "page_cache",
    ):
        assert _table_exists(clean_db, table) is True

    # Rerunning against the now-migrated database is a no-op, same as
    # any other migration.
    assert run_migrations(migrations_dir=MIGRATIONS_DIR, database_url=clean_db) == []
