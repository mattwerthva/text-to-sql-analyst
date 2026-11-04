"""Schema-drift test: the prompt's schema description must match the database.

The model only knows the schema through the hand-written TABLES section of
SCHEMA_DESCRIPTION. If a column is added to db/seed.sql but not described
there, the model cannot use it — it improvises from the columns it can see
and produces plausible but wrong SQL. (That happened: work_orders.reason was
added to the seed, never described, and "which models fail early?" returned
different nonsense on every run.)

This test compares the two in both directions, so it also catches a column
that was renamed or dropped in the database but is still described.

It checks *coverage*, not *meaning*: whether a description leads the model to
correct SQL is the job of the golden-query eval (evals/run_evals.py).
Needs the Postgres sandbox; skipped automatically if it isn't reachable.
"""

import re

import psycopg
import pytest

from app.config import get_settings
from app.schema_context import SCHEMA_DESCRIPTION


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


def described_columns() -> set[tuple[str, str]]:
    """(table, column) pairs from the TABLES section of SCHEMA_DESCRIPTION.

    Each table is written as `name(col, col -> fk.ref, col TYPE, ...)`, possibly
    wrapped over several lines; the column name is the first word of each item.
    """
    tables_section = SCHEMA_DESCRIPTION.split("TABLES", 1)[1].split("USEFUL FACTS", 1)[0]
    pairs = set()
    for table, cols in re.findall(r"(\w+)\(([^)]*)\)", tables_section):
        for item in cols.split(","):
            pairs.add((table, item.split()[0]))
    return pairs


def database_columns() -> set[tuple[str, str]]:
    with psycopg.connect(get_settings().database_url) as conn:
        rows = conn.execute(
            """
            SELECT c.table_name, c.column_name
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
            """
        ).fetchall()
    return {(table, column) for table, column in rows}


def test_parser_reads_the_description():
    # Guard the guard: if the description's format changes so the parser finds
    # nothing, the drift test below would pass vacuously.
    pairs = described_columns()
    assert ("assets", "install_date") in pairs
    assert ("work_orders", "reason") in pairs


@requires_db
def test_every_database_column_is_described_to_the_model():
    missing = sorted(database_columns() - described_columns())
    assert not missing, (
        "These columns exist in the database but are not described to the model "
        f"in app/schema_context.py SCHEMA_DESCRIPTION: {missing}"
    )


@requires_db
def test_every_described_column_exists_in_the_database():
    stale = sorted(described_columns() - database_columns())
    assert not stale, (
        "SCHEMA_DESCRIPTION describes columns the database does not have "
        f"(renamed or dropped?): {stale}"
    )
