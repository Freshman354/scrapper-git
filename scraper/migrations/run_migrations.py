"""Scraper database migration runner.

Applies numbered .sql files in this directory to the scraper's own
Postgres database, tracking completed migrations in a schema_migrations
table. Deliberately scoped to the scraper database only:

  - Reads exactly one connection string, SCRAPER_DATABASE_URL. No
    fallback to DATABASE_URL or any other Lumvi connection string.
  - Knows nothing about Lumvi's schema, knowledge_base, or migration
    history. It only ever touches whatever database that URL points to.

Not Alembic, by design — this project avoids extra dependencies where a
small bespoke tool covers the need (see the RQ-over-Celery and
sweep-job-over-rq-scheduler decisions elsewhere in this project). This
runner is intentionally simple: discover .sql files, apply the ones not
yet recorded, in filename order, one transaction each.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

import psycopg2

MIGRATIONS_DIR = Path(__file__).parent
ENV_VAR = "SCRAPER_DATABASE_URL"

_CREATE_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class MigrationError(RuntimeError):
    """Raised when a migration fails to apply or is misconfigured.

    Always raised, never swallowed — a failed migration must be visible
    to whoever/whatever ran this, not silently skipped.
    """


def get_database_url() -> str:
    """Read the scraper's own database URL. No fallback to any other
    env var — this is what keeps the migration scoped to the scraper
    database and out of Lumvi's connection configuration entirely."""
    url = os.environ.get(ENV_VAR)
    if not url:
        raise MigrationError(
            f"{ENV_VAR} is not set. The migration runner reads only this "
            "variable and will not fall back to DATABASE_URL or any other "
            "connection string."
        )
    return url


def discover_migration_files(migrations_dir: Path) -> List[Path]:
    """Return .sql files under migrations_dir sorted by filename.
    Zero-padded numeric prefixes (0001_, 0002_, ...) sort correctly as
    plain strings, so no numeric parsing is needed."""
    return sorted(migrations_dir.glob("*.sql"))


def ensure_tracking_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_CREATE_TRACKING_TABLE_SQL)
    conn.commit()


def get_applied_migrations(conn) -> set:
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM schema_migrations;")
        return {row[0] for row in cur.fetchall()}


def apply_migration(conn, path: Path) -> None:
    """Apply one migration file inside a single transaction.

    The migration's own SQL and the schema_migrations insert commit
    together — either both land, or neither does. On any failure the
    transaction is rolled back and the error re-raised; this migration
    is never recorded as applied in that case.
    """
    sql = path.read_text()
    with conn.cursor() as cur:
        try:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s);",
                (path.name,),
            )
        except Exception as exc:
            conn.rollback()
            raise MigrationError(
                f"Migration {path.name} failed and was rolled back: {exc}"
            ) from exc
        else:
            conn.commit()


def run_migrations(
    migrations_dir: Path = MIGRATIONS_DIR,
    database_url: Optional[str] = None,
) -> List[str]:
    """Apply all not-yet-applied migrations in filename order.

    Returns the filenames actually applied this run (empty if the
    database is already up to date — running this twice in a row is a
    no-op). Raises MigrationError on the first failure; migrations
    after the failed one are not attempted.

    `database_url` defaults to reading SCRAPER_DATABASE_URL via
    get_database_url() — the override parameter exists only so tests
    can point at a specific database without mutating process env vars.
    """
    url = database_url or get_database_url()
    conn = psycopg2.connect(url)
    applied_this_run: List[str] = []
    try:
        ensure_tracking_table(conn)
        already_applied = get_applied_migrations(conn)
        for path in discover_migration_files(migrations_dir):
            if path.name in already_applied:
                continue
            apply_migration(conn, path)
            applied_this_run.append(path.name)
        return applied_this_run
    finally:
        conn.close()


def main() -> int:
    try:
        applied = run_migrations()
    except MigrationError as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("No pending migrations. Database is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
