# Example prompts

A curated set of natural-language questions for the **Preventive-Maintenance &
CapEx Analyst** demo. Type any of these (or a variation) and Claude writes a
read-only SQL query, runs it against the seeded Postgres, and explains the
answer.

The data models a small residential portfolio:

```
portfolio → community (region) → building → unit → asset
asset ─┬─ inspections (history + pass/fail)
       ├─ work_orders (maintenance | replacement, scheduled | completed,
       │              replacements carry a reason: end_of_life | early_failure)
       └─ equipment_type (expected lifespan + replacement cost = CapEx)
```

Two derived ideas used throughout:

- **End of life (EOL)** = `install_date + expected_lifespan_years`. `install_date`
  reflects the *currently installed* equipment, so a completed replacement resets it.
- **Early failure vs. end of life** — every completed replacement records *why* it
  happened, so "which make/models fail early" is a direct query, not a guess.

---

## ⭐ Featured progression — one thread, three levels of insight

These three build on each other and are wired into the app's example chips:

1. **How much did we spend on completed maintenance versus replacements in 2025?**
   _Baseline: a single year-over-type spend rollup._
2. **Which makes and models are failing early instead of lasting to end of life?**
   _Diagnosis: ranks equipment reliability using the recorded replacement reason._
3. **Which assets are past their expected life with no scheduled replacement, and what's the projected CapEx by community?**
   _Action: turns the risk into a dollar figure and points at where to spend._

---

## Spend & budget history

- How much did we spend on completed maintenance versus replacements in 2025?
- Show total completed work-order spend by year and type — is spend trending up?
- What was our biggest single replacement cost last year, and on which asset?
- Total maintenance spend by community for 2025.

## Reliability — early failure vs. end of life

- Which makes and models are failing early instead of lasting to end of life?
- What share of each make's replacements were early failures?
- List the models with the most early-failure replacements on record.
- Are our early-failure replacements concentrated in any one equipment type?

## CapEx forecast (end of life)

- Project our replacement CapEx by year as each asset reaches the end of its expected life.
- How many assets reach end of life in the next 5 years, and what will they cost to replace?
- Which assets are past their expected life with no scheduled replacement?
- What's our total replacement exposure this year and next, including both
  scheduled replacements and past-life assets not yet on the schedule?

## Maintenance burden

- Which make and model has cost us the most in completed maintenance?
- Rank equipment types by total maintenance spend and number of work orders.
- For our highest-maintenance make/model, how many of those units are already past expected life?
- Which assets have the most maintenance work orders on record?

## Portfolio inventory & rollups

- How many assets do we have of each make and model, ordered by make and model?
- Summarize asset count and total replacement value by community and building.
- How many assets and open work orders does each community have?
- What's the average age of installed equipment by equipment type?

## Inspection compliance

- Which assets have no inspection recorded this month?
- Show the latest inspection per asset in the NYC region.
- How many assets failed their most recent inspection, by community?

---

## How the data is modeled (worth knowing)

- **`install_date` = the current unit's install**, reset by completed replacements.
  So "equipment age" means time since the *current* unit went in, and a replaced
  asset's clock restarts — the same way a real asset register works.
- **Early failure is a recorded fact, not reconstructed.** Each completed
  replacement stores `reason` (`early_failure` | `end_of_life`), so reliability
  questions are answered from data rather than inferred from dates.
