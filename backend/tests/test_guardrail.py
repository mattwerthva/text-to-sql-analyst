"""Guardrail tests — the security-critical, deterministic layer.

Unlike the eval (evals/run_evals.py), these are real tests: no model, no
network, fast, and repeatable. They lock down the two guardrail layers so a
future prompt/model change can't silently weaken them.

  - Unit tests exercise sanitize_sql() (a pure function).
  - Integration tests exercise the read-only Postgres transaction (needs the
    sandbox DB; skipped automatically if it isn't reachable).
"""

import psycopg
import pytest

from app.config import get_settings
from app.db import UnsafeSQLError, run_select, sanitize_sql

# --------------------------------------------------------------------------
# Unit — sanitize_sql (no DB, no API)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "DELETE FROM assets",
        "UPDATE assets SET make = 'x'",
        "DROP TABLE assets",
        "INSERT INTO assets (id) VALUES (1)",
        "TRUNCATE assets",
        "ALTER TABLE assets ADD COLUMN x int",
        "GRANT ALL ON assets TO dev",
        "SELECT 1; DROP TABLE assets",  # multi-statement
        "SELECT * INTO evil FROM assets",  # SELECT INTO writes a table
        "",  # empty
        "   ",  # whitespace only
    ],
)
def test_sanitize_blocks_unsafe(bad):
    with pytest.raises(UnsafeSQLError):
        sanitize_sql(bad)


@pytest.mark.parametrize(
    "good",
    [
        "SELECT 1",
        "select region from communities",
        "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
    ],
)
def test_sanitize_allows_reads(good):
    out = sanitize_sql(good)
    # Every safe query is wrapped so the outer LIMIT is guaranteed.
    assert out.lower().startswith("select * from (")


def test_sanitize_forces_row_limit():
    out = sanitize_sql("SELECT 1", row_limit=7)
    assert out.rstrip().endswith("LIMIT 7")


def test_sanitize_allows_single_trailing_semicolon():
    # One statement ending in ';' is fine; a second statement is not.
    out = sanitize_sql("SELECT 1;")
    assert "select 1" in out.lower()


# --------------------------------------------------------------------------
# Integration — read-only transaction (needs the Postgres sandbox)
# --------------------------------------------------------------------------


def _db_reachable() -> bool:
    try:
        with psycopg.connect(get_settings().database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres sandbox not reachable at DATABASE_URL"
)


@requires_db
def test_run_select_returns_rows():
    columns, rows = run_select(
        sanitize_sql("SELECT region FROM communities ORDER BY region")
    )
    assert columns == ["region"]
    assert ["NYC"] in rows


@requires_db
def test_read_only_txn_refuses_write():
    # Bypass sanitize_sql on purpose: prove the DATABASE layer refuses a write
    # even if a mutation somehow reached run_select().
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        run_select("DELETE FROM assets WHERE id = -1")
