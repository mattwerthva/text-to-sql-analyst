"""Read-only SQL execution with an app-level guardrail.

The trust story for a demo where a language model writes the SQL:

1. **sanitize_sql()** — a static gate. Reject anything that isn't a *single*
   `SELECT` / `WITH … SELECT`. No multi-statements, no DDL/DML keywords. Then
   force a row cap by wrapping the query in an outer `LIMIT`, so a model that
   forgets `LIMIT` (or writes `LIMIT 100000`) can't dump the table.

2. **run_select()** — a runtime gate. Every query runs in a *read-only*
   Postgres transaction (`default_transaction_read_only = on`) with a
   `statement_timeout`. Even if a write slipped past the static gate, the
   database itself refuses it, and a runaway scan is killed after N ms.

Two independent layers: the keyword gate shows intent; the read-only txn is the
one that actually can't be talked around. We surface both in the demo.
"""

import re

import psycopg

from app.config import get_settings

_settings = get_settings()


class UnsafeSQLError(ValueError):
    """Raised when generated SQL fails the static guardrail."""


# Whole-word matches only, so a column literally named "created_at" won't trip
# the CREATE check. These are the statement types a read-only analyst never needs.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|into|"
    r"merge|call|do|vacuum|analyze|reindex|comment|set|reset)\b",
    re.IGNORECASE,
)


def sanitize_sql(raw: str, row_limit: int | None = None) -> str:
    """Validate model-generated SQL and return an execution-safe version.

    Raises UnsafeSQLError if the query is anything other than a single read.
    """
    limit = row_limit if row_limit is not None else _settings.sql_row_limit

    sql = raw.strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQLError("Empty query.")

    # One statement only. A stray semicolon means a second statement was smuggled in.
    if ";" in sql:
        raise UnsafeSQLError("Only a single statement is allowed (found ';').")

    # Must be a read. Postgres CTEs let you write `WITH x AS (...) SELECT ...`,
    # so allow a leading WITH — but a data-modifying CTE would trip _FORBIDDEN below.
    lowered = sql.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise UnsafeSQLError("Only SELECT / WITH … SELECT queries are allowed.")

    if _FORBIDDEN.search(sql):
        raise UnsafeSQLError("Query contains a disallowed keyword (writes/DDL are blocked).")

    # Force a hard row cap regardless of what the model wrote. Wrapping is
    # bulletproof vs. regex-detecting an existing LIMIT: a SELECT or a
    # WITH … SELECT is always valid as a subquery.
    return f"SELECT * FROM (\n{sql}\n) AS _capped LIMIT {int(limit)}"


def run_select(safe_sql: str) -> tuple[list[str], list[list]]:
    """Execute a sanitized query read-only and return (columns, rows).

    Uses a fresh connection with a read-only transaction default and a
    statement timeout. Any write attempt errors out at the database.
    """
    timeout_ms = int(_settings.sql_statement_timeout_ms)
    with psycopg.connect(_settings.database_url, autocommit=True) as conn:
        # int-cast the timeout so there's no string interpolation surface here.
        conn.execute(f"SET statement_timeout = {timeout_ms}")
        conn.execute("SET default_transaction_read_only = on")
        cur = conn.execute(safe_sql)
        columns = [d.name for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
    return columns, rows
