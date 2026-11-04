"""Schema description + few-shot examples fed to the model for NL→SQL.

Text-to-SQL quality lives and dies on the context you give the model: an
accurate schema and a handful of examples that demonstrate the *idioms* this
dataset rewards (anti-joins, date math, GROUPING SETS, DISTINCT ON, cents→usd).

IMPORTANT: the model generates every query ON THE FLY from the schema — these
examples teach style, not answers. They are deliberately DIFFERENT questions
from the six eval questions in `evals/run_evals.py`, so the eval
remains a real test on unseen questions (not a lookup of what we showed it).
"""

SCHEMA_DESCRIPTION = """\
PostgreSQL 17. Domain: property / asset-lifecycle management.
Hierarchy: portfolio -> community -> building -> unit -> asset.

TABLES
  portfolios(id, name)
  communities(id, portfolio_id -> portfolios.id, name, region)
      region is a text like 'NYC' | 'Boston' | 'Austin'
  buildings(id, community_id -> communities.id, name, address)
  units(id, building_id -> buildings.id, unit_number)
  equipment_types(id, name, expected_lifespan_years, replacement_cost_cents)
      the CapEx pricing/reference table. Costs are in CENTS.
  assets(id, unit_id -> units.id, equipment_type_id -> equipment_types.id,
         make, model, serial, install_date DATE)
  inspections(id, asset_id -> assets.id, inspection_date DATE, passed BOOLEAN)
      ~108k rows.
  work_orders(id, asset_id -> assets.id, type, status, scheduled_date DATE,
              cost_cents, created_at TIMESTAMPTZ)
      type   in ('replacement', 'maintenance')
      status in ('scheduled', 'completed', 'cancelled')

USEFUL FACTS
  - "Past expected life / end-of-life" means:
        install_date + make_interval(years => et.expected_lifespan_years) < CURRENT_DATE
  - Dollar amounts: divide *_cents by 100.0 and alias as *_usd.
  - "No scheduled replacement" / "missing inspection" are ANTI-JOINs
    (LEFT JOIN … WHERE … IS NULL, or NOT EXISTS).
  - "Latest per asset" -> DISTINCT ON (asset_id) … ORDER BY asset_id, <date> DESC.
"""

# (natural-language question, illustrative SQL) pairs. These teach idioms and
# are intentionally *unlike* the eval questions — see the module docstring.
FEW_SHOT: list[tuple[str, str]] = [
    # DISTINCT ON — latest record per entity (here: work orders, not inspections).
    (
        "What is the most recent work order for each asset?",
        "SELECT DISTINCT ON (w.asset_id) w.asset_id, w.type, w.status, w.scheduled_date\n"
        "FROM work_orders w\n"
        "ORDER BY w.asset_id, w.scheduled_date DESC",
    ),
    # Money (cents→usd) + full hierarchy join + SUM + GROUP BY.
    (
        "Total completed maintenance spend by region.",
        "SELECT c.region, SUM(w.cost_cents) / 100.0 AS spend_usd\n"
        "FROM work_orders w\n"
        "JOIN assets a      ON a.id = w.asset_id\n"
        "JOIN units u       ON u.id = a.unit_id\n"
        "JOIN buildings b   ON b.id = u.building_id\n"
        "JOIN communities c ON c.id = b.community_id\n"
        "WHERE w.type = 'maintenance' AND w.status = 'completed'\n"
        "GROUP BY c.region\n"
        "ORDER BY spend_usd DESC",
    ),
    # NOT EXISTS anti-join (here: assets with no work order at all).
    (
        "Which assets have never had any work order?",
        "SELECT a.id AS asset_id, et.name AS equipment\n"
        "FROM assets a\n"
        "JOIN equipment_types et ON et.id = a.equipment_type_id\n"
        "WHERE NOT EXISTS (SELECT 1 FROM work_orders w WHERE w.asset_id = a.id)\n"
        "ORDER BY a.id",
    ),
    # make_interval date math + GROUP BY + HAVING (bucketing near end-of-life).
    (
        "How many assets of each equipment type reach end-of-life within the next 2 years?",
        "SELECT et.name AS equipment, COUNT(*) AS approaching_eol\n"
        "FROM assets a\n"
        "JOIN equipment_types et ON et.id = a.equipment_type_id\n"
        "WHERE a.install_date + make_interval(years => et.expected_lifespan_years)\n"
        "        BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '2 years'\n"
        "GROUP BY et.name\n"
        "HAVING COUNT(*) > 0\n"
        "ORDER BY approaching_eol DESC",
    ),
    # GROUPING SETS — multi-level rollup in one pass (here: asset counts).
    (
        "Count assets at building, community, and portfolio levels in one result.",
        "SELECT p.name AS portfolio, c.name AS community, b.name AS building,\n"
        "       COUNT(*) AS asset_count\n"
        "FROM assets a\n"
        "JOIN units u       ON u.id = a.unit_id\n"
        "JOIN buildings b   ON b.id = u.building_id\n"
        "JOIN communities c ON c.id = b.community_id\n"
        "JOIN portfolios p  ON p.id = c.portfolio_id\n"
        "GROUP BY GROUPING SETS ((p.name, c.name, b.name), (p.name, c.name), (p.name))\n"
        "ORDER BY portfolio, community NULLS FIRST, building NULLS FIRST",
    ),
]


def build_system_prompt() -> str:
    """Assemble the NL→SQL system prompt: schema + rules + few-shot examples."""
    examples = "\n\n".join(
        f"-- Q: {q}\n{sql}" for q, sql in FEW_SHOT
    )
    return f"""You are a senior analytics engineer for a property-maintenance company.
Translate the user's question into ONE PostgreSQL query against the schema below.

{SCHEMA_DESCRIPTION}

RULES
  - Emit exactly one SELECT (a leading WITH … SELECT is fine). Never write to the DB.
  - Only reference tables and columns that exist above.
  - Money: divide *_cents by 100.0 and alias the result *_usd.
  - Prefer clear column aliases; ORDER BY the most decision-relevant column.
  - If the question is ambiguous, make the most useful reasonable assumption.

Here are golden examples of the query patterns this dataset rewards:

{examples}

Call the emit_sql tool with your query and a one-sentence rationale."""
