"""Golden-query eval for the NL→SQL step.

For each question we (1) ask the model to write SQL — using the exact same
prompt + forced tool call as the live app — then (2) execute BOTH the generated
SQL and a hand-written *golden* query (defined below), and
(3) check the two result sets match, order-insensitive.

Most demos stop at "it works when I try it." An eval turns that into a
measurable claim: N/7 questions produce SQL whose results match ground truth.
The questions here are NOT the few-shot examples the model was shown — so this
is a real test of schema understanding, not recall.

Run from the backend directory (needs .env + the DB up):

    uv run python evals/run_evals.py
"""

import os
import re
import sys
from decimal import Decimal, InvalidOperation

# Make `app` importable whether run as `python evals/run_evals.py` or `-m`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anthropic import Anthropic  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import UnsafeSQLError, run_select, sanitize_sql  # noqa: E402
from app.nl2sql import EMIT_SQL_TOOL, SYSTEM_PROMPT, extract_sql  # noqa: E402

# (label, natural-language question, golden SQL from queries.sql).
# Pattern 6 (query optimization / EXPLAIN ANALYZE) is intentionally excluded —
# it's about query *plans*, not result sets, so there's nothing to diff.
EVAL_CASES: list[tuple[str, str, str]] = [
    (
        "P1 latest-per-entity",
        "For each asset, return exactly two columns — its asset id and its most recent "
        "inspection date. No other columns.",
        """
        SELECT DISTINCT ON (a.id) a.id AS asset_id, i.inspection_date
        FROM assets a
        JOIN inspections i ON i.asset_id = a.id
        ORDER BY a.id, i.inspection_date DESC
        """,
    ),
    (
        "P2 end-of-life anti-join",
        "Return only the asset id of every asset that is past its expected life and has "
        "no scheduled replacement work order. One column: asset_id.",
        """
        SELECT a.id AS asset_id
        FROM assets a
        JOIN equipment_types et ON et.id = a.equipment_type_id
        LEFT JOIN work_orders w
               ON w.asset_id = a.id AND w.type = 'replacement' AND w.scheduled_date > CURRENT_DATE
        WHERE a.install_date + make_interval(years => et.expected_lifespan_years) < CURRENT_DATE
          AND w.id IS NULL
        """,
    ),
    (
        "P3 CapEx rollup",
        "For assets already past end-of-life, what is the projected replacement cost by "
        "building and equipment type, with a count of units due?",
        """
        SELECT b.name AS building, et.name AS equipment,
               COUNT(*) AS units_due,
               SUM(et.replacement_cost_cents) / 100.0 AS projected_cost_usd
        FROM assets a
        JOIN equipment_types et ON et.id = a.equipment_type_id
        JOIN units u     ON u.id = a.unit_id
        JOIN buildings b ON b.id = u.building_id
        WHERE a.install_date + make_interval(years => et.expected_lifespan_years) < CURRENT_DATE
        GROUP BY b.name, et.name
        HAVING COUNT(*) >= 1
        """,
    ),
    (
        "P4 compliance gap",
        "Return only the asset id of every asset that has no inspection recorded in the "
        "current calendar month. One column: asset_id.",
        """
        SELECT a.id AS asset_id
        FROM assets a
        WHERE NOT EXISTS (
            SELECT 1 FROM inspections i
            WHERE i.asset_id = a.id
              AND i.inspection_date >= date_trunc('month', CURRENT_DATE)
        )
        """,
    ),
    (
        "P5 hierarchical rollup",
        "Using GROUPING SETS, return completed-maintenance spend at exactly three levels "
        "in one result: per (portfolio, community, building), per (portfolio, community), "
        "and per (portfolio). Do NOT include a grand total. Columns: portfolio, "
        "community, building, spend_usd (dollars).",
        """
        SELECT p.name AS portfolio, c.name AS community, b.name AS building,
               SUM(w.cost_cents) / 100.0 AS spend_usd
        FROM work_orders w
        JOIN assets a      ON a.id = w.asset_id
        JOIN units u       ON u.id = a.unit_id
        JOIN buildings b   ON b.id = u.building_id
        JOIN communities c ON c.id = b.community_id
        JOIN portfolios p  ON p.id = c.portfolio_id
        WHERE w.type = 'maintenance' AND w.status = 'completed'
        GROUP BY GROUPING SETS ((p.name, c.name, b.name), (p.name, c.name), (p.name))
        """,
    ),
    (
        "P1b latest-per-entity + region filter",
        "For assets in the NYC region only, return exactly two columns per asset — its "
        "asset id and its most recent inspection date. No other columns.",
        """
        SELECT DISTINCT ON (a.id) a.id AS asset_id, i.inspection_date
        FROM assets a
        JOIN units u       ON u.id = a.unit_id
        JOIN buildings b   ON b.id = u.building_id
        JOIN communities c ON c.id = b.community_id
        JOIN inspections i ON i.asset_id = a.id
        WHERE c.region = 'NYC'
        ORDER BY a.id, i.inspection_date DESC
        """,
    ),
    (
        # Mirrors the UI's "failing early" example. The answer depends on the
        # `reason` label; a model that instead derives age from install_date
        # gets meaningless (zero or negative) ages and a different result set.
        "P7 early-failure by make/model",
        "Which makes and models are failing early? For each (make, model) with at least "
        "one completed early-failure replacement, return exactly three columns: make, "
        "model, early_failures (the count).",
        """
        SELECT a.make, a.model, COUNT(*) AS early_failures
        FROM work_orders w
        JOIN assets a ON a.id = w.asset_id
        WHERE w.type = 'replacement' AND w.status = 'completed'
          AND w.reason = 'early_failure'
        GROUP BY a.make, a.model
        """,
    ),
]


def _canon(value: object) -> str:
    """Canonicalize a cell so 12700.00 == '12700' and dates/bools compare cleanly."""
    if value is None:
        return "∅"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        return f"{Decimal(str(value)).normalize():f}"
    if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value):
        try:
            return f"{Decimal(value).normalize():f}"
        except InvalidOperation:
            pass
    return str(value)


def _normalize(rows: list[list]) -> list[tuple]:
    """Order-insensitive on both rows and columns: sort values within each row,
    then sort the rows. Two queries that compute the same values 'match' even if
    they alias or order columns differently."""
    return sorted(tuple(sorted(_canon(c) for c in row)) for row in rows)


def _run(sql: str) -> list[list]:
    # High cap so the row limit never truncates a comparison (all sets are small).
    _, rows = run_select(sanitize_sql(sql, row_limit=1_000_000))
    return rows


def main() -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY not set (backend/.env). Aborting.")
        return 2
    client = Anthropic(api_key=settings.anthropic_api_key)

    print(f"\nNL→SQL eval · model={settings.anthropic_model}\n" + "─" * 68)
    passed = 0
    for label, question, golden in EVAL_CASES:
        try:
            message = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=[EMIT_SQL_TOOL],
                tool_choice={"type": "tool", "name": "emit_sql"},
                messages=[{"role": "user", "content": question}],
            )
            generated, _ = extract_sql(message)
            gen_rows = _run(generated)
            gold_rows = _run(golden)
            ok = _normalize(gen_rows) == _normalize(gold_rows)
            passed += ok
            status = "✓ PASS" if ok else "✗ FAIL"
            detail = f"{len(gen_rows)} rows vs golden {len(gold_rows)}"
            print(f"{status}  {label:<40} {detail}")
            if not ok:
                print(f"        generated SQL:\n        {generated.strip()[:400]}")
        except (UnsafeSQLError, Exception) as exc:  # noqa: BLE001
            print(f"✗ FAIL  {label:<40} error: {exc}")

    print("─" * 68)
    print(f"{passed}/{len(EVAL_CASES)} questions matched golden result sets\n")
    return 0 if passed == len(EVAL_CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
