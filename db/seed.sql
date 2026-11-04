-- Runs automatically on FIRST boot of an empty data volume.
-- To re-run after edits:  docker compose down -v && docker compose up -d
--
-- Domain: property / asset-lifecycle management. All data is fictitious.
-- Hierarchy:  portfolio -> community -> building -> unit -> asset
--             asset 1--* inspections
--             asset 1--* work_orders
--             asset *--1 equipment_type   (lifespan + replacement cost = CapEx)
--
-- The schema + data are shaped to exercise six analytical question patterns;
-- the golden SQL for each lives in backend/evals/run_evals.py.

-- pgvector is enabled but unused here; harmless, and ready for a RAG layer.
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------
CREATE TABLE portfolios (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE communities (
    id           SERIAL PRIMARY KEY,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    name         TEXT NOT NULL,
    region       TEXT NOT NULL           -- e.g. NYC / Boston / Austin
);

CREATE TABLE buildings (
    id           SERIAL PRIMARY KEY,
    community_id INTEGER NOT NULL REFERENCES communities(id),
    name         TEXT NOT NULL,
    address      TEXT
);

CREATE TABLE units (
    id          SERIAL PRIMARY KEY,
    building_id INTEGER NOT NULL REFERENCES buildings(id),
    unit_number TEXT NOT NULL
);

-- Reference / pricing table — the CapEx forecasting join target.
CREATE TABLE equipment_types (
    id                     SERIAL PRIMARY KEY,
    name                   TEXT NOT NULL,
    expected_lifespan_years INTEGER NOT NULL,
    replacement_cost_cents  INTEGER NOT NULL
);

CREATE TABLE assets (
    id                SERIAL PRIMARY KEY,
    unit_id           INTEGER NOT NULL REFERENCES units(id),
    equipment_type_id INTEGER NOT NULL REFERENCES equipment_types(id),
    make              TEXT,
    model             TEXT,
    serial            TEXT,
    install_date      DATE NOT NULL
);

CREATE TABLE inspections (
    id              SERIAL PRIMARY KEY,
    asset_id        INTEGER NOT NULL REFERENCES assets(id),
    inspection_date DATE NOT NULL,
    passed          BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE work_orders (
    id             SERIAL PRIMARY KEY,
    asset_id       INTEGER NOT NULL REFERENCES assets(id),
    type           TEXT NOT NULL,          -- 'replacement' | 'maintenance'
    status         TEXT NOT NULL,          -- 'scheduled' | 'completed' | 'cancelled'
    reason         TEXT,                   -- replacements only: 'end_of_life' | 'early_failure' (NULL for maintenance)
    scheduled_date DATE,
    cost_cents     INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NOTE: no index on inspections(asset_id, inspection_date) on purpose — a FK
-- does NOT create one in Postgres. Add it yourself during the EXPLAIN ANALYZE
-- exercise (pattern #6) and compare plans before/after.

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------
INSERT INTO equipment_types (name, expected_lifespan_years, replacement_cost_cents) VALUES
    ('Water Heater',    12, 120000),   -- id 1
    ('Dishwasher',      10,  65000),   -- id 2
    ('Icemaker',         7, 250000),   -- id 3
    ('HVAC Condenser',  15, 550000),   -- id 4
    ('Refrigerator',    13, 140000);   -- id 5

-- ---------------------------------------------------------------------------
-- Hierarchy  (1 portfolio -> 3 communities -> 6 buildings -> 30 units)
-- ---------------------------------------------------------------------------
INSERT INTO portfolios (name) VALUES ('Harborview Residential');

INSERT INTO communities (portfolio_id, name, region) VALUES
    (1, 'Harbor Point',   'NYC'),
    (1, 'Beacon Commons', 'Boston'),
    (1, 'Lakeline Flats', 'Austin');

INSERT INTO buildings (community_id, name, address)
SELECT c.id, c.name || ' Bldg ' || g, g || ' Main St'
FROM communities c CROSS JOIN generate_series(1, 2) g;   -- 2 buildings each -> 6

INSERT INTO units (building_id, unit_number)
SELECT b.id, 'U' || lpad(g::text, 2, '0')
FROM buildings b CROSS JOIN generate_series(1, 5) g;      -- 5 units each -> 30

-- ---------------------------------------------------------------------------
-- Assets: 3 per unit -> 90 assets. Deterministic (no random()) so results are
-- reproducible. install_date ages 0..~19yrs so some are past end-of-life.
--
-- make is tied to equipment type via the same %5 index (Rheem water heaters,
-- Bosch dishwashers, Scotsman icemakers, Carrier HVAC, GE fridges). Each make
-- has exactly 4 models, picked deterministically -> ~90 assets spread over 20
-- (make, model) pairs, so GROUP BY make/model returns meaningful counts.
-- ---------------------------------------------------------------------------
INSERT INTO assets (unit_id, equipment_type_id, make, model, serial, install_date)
SELECT
    unit_id,
    eq_idx                                          AS equipment_type_id,
    (ARRAY['Rheem','Bosch','Scotsman','Carrier','GE'])[eq_idx] AS make,
    (ARRAY[
        ARRAY['Performance','Prestige','Gladiator','ProTerra'],        -- Rheem    / Water Heater
        ARRAY['300 Series','500 Series','800 Series','Benchmark'],      -- Bosch    / Dishwasher
        ARRAY['CU50','CU100','Prodigy','Prodigy Plus'],                -- Scotsman / Icemaker
        ARRAY['Comfort 16','Performance 17','Infinity 19','Infinity 24'], -- Carrier  / HVAC Condenser
        ARRAY['GTS18','Profile','Cafe','Monogram']                     -- GE       / Refrigerator
    ])[eq_idx][model_idx]                           AS model,
    'SN' || lpad((unit_id * 1000 + slot)::text, 7, '0')             AS serial,
    install_date
FROM (
    SELECT
        u.id                                    AS unit_id,
        slot,
        ((u.id + slot) % 5) + 1                 AS eq_idx,       -- 1..5 -> make + equipment type
        ((u.id * 7 + slot * 3) % 4) + 1         AS model_idx,    -- 1..4 -> which model for that make
        -- Age off the asset's serial ordinal (not u.id): a *37 %20 spread keeps
        -- old, never-replaced assets in EVERY community, so "past EOL by
        -- community" isn't degenerate. (u.id-based aging clustered them in one.)
        (CURRENT_DATE - make_interval(years => ((((u.id - 1) * 3 + slot) * 37) % 20)))::date AS install_date
    FROM units u CROSS JOIN generate_series(1, 3) slot
) s;

-- ---------------------------------------------------------------------------
-- Inspections: 3 historical per asset (n=0 is the most recent).
-- ---------------------------------------------------------------------------
INSERT INTO inspections (asset_id, inspection_date, passed)
SELECT a.id,
       (CURRENT_DATE - make_interval(months => n * 4 + (a.id % 3)))::date,
       (a.id + n) % 4 <> 0
FROM assets a CROSS JOIN generate_series(0, 2) n;

-- Give ~half the assets an inspection THIS month, so the compliance-gap
-- anti-join (pattern #4) returns a real subset (the assets with none).
INSERT INTO inspections (asset_id, inspection_date, passed)
SELECT a.id, date_trunc('month', CURRENT_DATE)::date + 5, true
FROM assets a WHERE a.id % 2 = 0;

-- ---------------------------------------------------------------------------
-- Work orders
--   * future 'replacement' (scheduled)      -> anti-join target for pattern #2
--   * past completed, mixed maint/replace   -> spend rollups for pattern #3/#5
--
-- Completed rows mix BOTH types. Replacement cost_cents is joined from
-- equipment_types so it matches the asset's equipment (a real CapEx number);
-- maintenance cost is a smaller varied amount. Dates are offset by a varied
-- number of DAYS (not whole months, which would land every row on today's
-- day-of-month) so completed work is sprinkled across many distinct days.
-- ---------------------------------------------------------------------------

-- Future scheduled replacements: open CapEx pipeline. Planned ahead -> EOL-driven.
INSERT INTO work_orders (asset_id, type, status, reason, scheduled_date, cost_cents)
SELECT a.id, 'replacement', 'scheduled', 'end_of_life',
       (CURRENT_DATE + make_interval(months => (a.id % 12) + 1))::date,
       et.replacement_cost_cents
FROM assets a JOIN equipment_types et ON et.id = a.equipment_type_id
WHERE a.id % 4 = 0;

-- Completed work in the trailing ~12 months: even ids -> replacement (cost from
-- equipment_types), odd ids -> maintenance. Day offset 7..356 back for variety.
-- A completed replacement's reason is decided by the MODEL: certain "lemon"
-- models (see LEMON_MODELS below) fail early, the rest run to end of life. This
-- is deterministic on the model string (no a.id correlation), so a GROUP BY
-- make/model early-failure ranking is clean. Maintenance rows have no reason.
--
-- LEMON_MODELS (early-failure prone), skewed so makes rank differently:
--   Scotsman 3/4 (CU50, CU100, Prodigy) > Bosch 2/4 (300, 800) =
--   GE 2/4 (GTS18, Cafe) > Rheem 1/4 (Gladiator) = Carrier 1/4 (Comfort 16)
INSERT INTO work_orders (asset_id, type, status, reason, scheduled_date, cost_cents)
SELECT a.id,
       CASE WHEN a.id % 2 = 0 THEN 'replacement' ELSE 'maintenance' END,
       'completed',
       CASE WHEN a.id % 2 = 0 THEN
            CASE WHEN a.model = ANY (ARRAY['CU50','CU100','Prodigy','300 Series',
                                           '800 Series','GTS18','Cafe','Gladiator',
                                           'Comfort 16'])
                 THEN 'early_failure' ELSE 'end_of_life' END
       END,                                            -- NULL for maintenance
       (CURRENT_DATE - make_interval(days => ((a.id * 37) % 350) + 7))::date,
       CASE WHEN a.id % 2 = 0 THEN et.replacement_cost_cents
            ELSE (((a.id % 5) + 1) * 15000) END
FROM assets a JOIN equipment_types et ON et.id = a.equipment_type_id
WHERE a.id % 5 < 2;   -- community-neutral subset (a.id % 3 == community here!)

-- Historical completed work across calendar year 2025: 2 per asset, scattered
-- over the full year on varied days. ~1/3 replacement (cost from equipment_types),
-- ~2/3 maintenance. Gives a year-over-year spend history for rollups.
-- (a.id % 5 == 0 is community-neutral; a.id % 3 would alias to community here.)
INSERT INTO work_orders (asset_id, type, status, reason, scheduled_date, cost_cents)
SELECT a.id,
       CASE WHEN (a.id + g) % 5 = 0 THEN 'replacement' ELSE 'maintenance' END,
       'completed',
       CASE WHEN (a.id + g) % 5 = 0 THEN
            CASE WHEN a.model = ANY (ARRAY['CU50','CU100','Prodigy','300 Series',
                                           '800 Series','GTS18','Cafe','Gladiator',
                                           'Comfort 16'])
                 THEN 'early_failure' ELSE 'end_of_life' END
       END,                                            -- NULL for maintenance
       (DATE '2025-01-01' + make_interval(days => ((a.id * 53 + g * 181) % 365)))::date,
       CASE WHEN (a.id + g) % 5 = 0 THEN et.replacement_cost_cents
            ELSE (((a.id % 5) + 1) * 12000) END
FROM assets a JOIN equipment_types et ON et.id = a.equipment_type_id
CROSS JOIN generate_series(0, 1) g;

-- A COMPLETED replacement physically swapped the asset's equipment on that day,
-- so the currently-installed unit's install_date is the date of the most recent
-- completed replacement. (Scheduled/future replacements haven't happened yet, so
-- they don't move install_date.) Assets never replaced keep their original aged
-- install_date -> still a real past-end-of-life population for CapEx forecasting.
UPDATE assets a
SET install_date = r.last_replacement
FROM (
    SELECT asset_id, max(scheduled_date) AS last_replacement
    FROM work_orders
    WHERE type = 'replacement' AND status = 'completed'
    GROUP BY asset_id
) r
WHERE r.asset_id = a.id;

-- ---------------------------------------------------------------------------
-- Bulk inspections for the EXPLAIN ANALYZE / indexing exercise (pattern #6).
-- 90 assets x 1200 = ~108k extra rows. Big enough that a composite index on
-- (asset_id, inspection_date) visibly changes the plan for "latest per asset".
-- ---------------------------------------------------------------------------
INSERT INTO inspections (asset_id, inspection_date, passed)
SELECT a.id,
       (CURRENT_DATE - make_interval(days => (g % 2000)))::date,
       (g % 5) <> 0
FROM assets a CROSS JOIN generate_series(1, 1200) g;

ANALYZE;   -- refresh planner stats after the bulk load
