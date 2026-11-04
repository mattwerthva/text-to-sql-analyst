# Preventive-Maintenance & CapEx Analyst

**Ask your property portfolio anything in plain English.** Claude writes a SQL
query, runs it read-only against a Postgres sandbox (108k inspection records),
and streams back an analyst summary with a prioritized recommendation.

This is an applied-AI **text-to-SQL** demo over a realistic property /
asset-lifecycle dataset — the questions a preventive-maintenance or
CapEx-planning team actually asks. All data is fictitious.

![pipeline](https://img.shields.io/badge/pipeline-NL%20%E2%86%92%20SQL%20%E2%86%92%20execute%20%E2%86%92%20narrative-1f2937)

https://github.com/user-attachments/assets/1ae12621-0726-4295-b340-d5a313d72cce

---

## What it does (the pipeline)

Every question flows through four steps, streamed to the browser over
Server-Sent Events (SSE):

1. **NL → SQL.** Claude turns the question into one PostgreSQL query. The model
   is given the schema + a few idiom examples and **forced to return the query
   as a structured tool call** (`emit_sql{sql, rationale}`) — no prose to parse.
   The SQL is generated on the fly from schema understanding, not looked up.
2. **Guardrail gate.** The query must be a single `SELECT` / `WITH … SELECT` —
   no multi-statements, no DDL/DML — and it's wrapped in a forced `LIMIT`.
3. **Execute read-only.** It runs in a **read-only Postgres transaction** with a
   `statement_timeout`. Even a write that slipped past the keyword gate is
   refused by the database itself.
4. **Narrative.** Claude turns the result rows into a short summary + a single
   prioritized recommendation, **streamed token-by-token** to the UI.

The UI surfaces all three artifacts: the **generated SQL** (collapsed toggle —
transparency is the trust signal for a DB owner), the **result grid**, and the
**streaming analysis**.

## Why the guardrails matter

The person who owns the database cares less about the model and more about
"what exactly did it run against my data?" Two independent layers answer that:

| Layer | Mechanism | File |
|-------|-----------|------|
| Static gate | single-statement, `SELECT`-only, keyword block, forced `LIMIT` | `backend/app/db.py` → `sanitize_sql()` |
| Runtime gate | read-only transaction + `statement_timeout` | `backend/app/db.py` → `run_select()` |

The static gate shows intent; the read-only transaction is the one that
genuinely can't be talked around. Both are covered by tests (below).

## The data

A Postgres 17 + pgvector sandbox, hierarchy
`portfolio → community → building → unit → asset`, with `inspections` (~108k
rows) and `work_orders`. Schema + seed live in
[`db/`](./db). Six
analytical patterns are exercised: latest-per-entity, end-of-life anti-join,
CapEx rollup, compliance gap, hierarchical `GROUPING SETS`, and query
optimization.

---

## Run it

**Prerequisites:** Node 22 (`.nvmrc`), [uv](https://docs.astral.sh/uv/), and the
Docker, and an Anthropic API key.

**Database** — Postgres on localhost:5432, seeded on first boot

```bash
cd db
cp .env.example .env
docker compose -p text_to_sql_analyst up -d   # -p: a unique project name, so the
                                              # "db" folder name can't collide
```

**Backend** — http://localhost:8000

```bash
cd backend
cp .env.example .env          # then set ANTHROPIC_API_KEY
uv sync
uv run fastapi dev app/main.py
```

**Frontend** — http://localhost:5173

```bash
cd frontend
nvm use 22
npm install
npm run dev
```

Open http://localhost:5173, click an example question, and watch the SQL,
grid, and analysis appear.

## The eval

Most demos stop at "it worked when I tried it." This one measures it. The eval
harness asks the model 7 questions (one per analytical pattern, plus the UI's
"failing early" example), runs **both**
the generated SQL and a hand-written *golden* query, and checks the result sets
match — order-insensitive.

```bash
cd backend
uv run python evals/run_evals.py
```

```
NL→SQL eval · model=claude-opus-4-8
────────────────────────────────────────────────────────────────────
✓ PASS  P1 latest-per-entity                     90 rows vs golden 90
✓ PASS  P2 end-of-life anti-join                 12 rows vs golden 12
✓ PASS  P3 CapEx rollup                          14 rows vs golden 14
✓ PASS  P4 compliance gap                        0 rows vs golden 0
✓ PASS  P5 hierarchical rollup                   10 rows vs golden 10
✓ PASS  P1b latest-per-entity + region filter    30 rows vs golden 30
✓ PASS  P7 early-failure by make/model           7 rows vs golden 7
────────────────────────────────────────────────────────────────────
7/7 questions matched golden result sets
```

The eval questions are **deliberately different** from the few-shot examples the
model is shown — so this tests schema understanding, not recall.

## Tests vs. evals — a deliberate split

- **`evals/run_evals.py`** is a *model benchmark*: it hits the live API, is
  non-deterministic, and reports a quality score. You run it on demand, not in
  CI.
- **`tests/test_guardrail.py`** are *real tests*: deterministic, no API, fast.
  They lock down the security-critical guardrail so a future prompt/model change
  can't silently weaken it.

```bash
cd backend
uv run pytest -q          # 23 passed (needs the db running)
```

---


## Layout

```
text-to-sql-analyst/
├── backend/
│   ├── app/
│   │   ├── config.py          # settings (keys, DB URL, guardrail knobs)
│   │   ├── db.py              # sanitize_sql() + read-only run_select()
│   │   ├── schema_context.py  # schema description + few-shot idioms
│   │   ├── nl2sql.py          # emit_sql tool + prompt (shared by app & eval)
│   │   └── routers/ask.py     # the SSE pipeline
│   ├── evals/run_evals.py     # golden-query eval (model benchmark)
│   └── tests/test_guardrail.py# deterministic guardrail tests
├── frontend/
│   └── src/
│       ├── lib/api.ts         # SSE client for /api/ask
│       └── App.tsx            # question box, SQL toggle, grid, streaming
└── db/                        # portable docker-compose + seed
```
