# CLAUDE.md — text-to-sql-analyst

Context for assistant sessions working in this repo. [README.md](README.md) covers
what the app does and how to run it.

## The schema exists twice — change both

The model never sees the database. It sees `SCHEMA_DESCRIPTION` in
[`backend/app/schema_context.py`](backend/app/schema_context.py), a hand-written
description of [`db/seed.sql`](db/seed.sql). A column the description leaves out is
a column the model cannot use; it will improvise from the columns it can see and
write plausible, wrong SQL.

So any schema change to `db/seed.sql` (add, rename or drop a column or table) must
also update `SCHEMA_DESCRIPTION`:

- **Coverage** is enforced: `backend/tests/test_schema_context.py` fails when the
  database and the description disagree about which columns exist. It needs the
  Postgres sandbox running; a *skipped* run has not checked anything.
- **Meaning** is not: say what a column's values mean and add a `USEFUL FACTS` line
  when a column changes how a question should be answered (e.g. "failing early"
  means `work_orders.reason = 'early_failure'`). If it does, add or update a
  golden query in [`backend/evals/run_evals.py`](backend/evals/run_evals.py) and
  run the eval.

Goal: every example question in the UI (`frontend/src/App.tsx`) has a golden
query in the eval, so the demo's own suggestions are measured. Today about half
do (failing early → P7, NYC latest inspection → P1b, past-life CapEx → P2/P3);
the spend, CapEx-by-year and maintenance-by-model examples are not yet covered.

## Checks

```bash
cd db && docker compose -p text_to_sql_analyst up -d   # tests need the sandbox
cd backend && uv run pytest -q                         # deterministic, no API
cd backend && uv run python evals/run_evals.py         # live model, on demand
```

## Seed data is deterministic

`db/seed.sql` uses no `random()`, so results are reproducible. Dates are relative
to `CURRENT_DATE`, though, so row counts in the README's eval output drift over
time. Re-seed with `docker compose -p text_to_sql_analyst down -v` and then `up -d`.
