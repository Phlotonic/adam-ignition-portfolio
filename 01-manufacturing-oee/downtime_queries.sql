-- =============================================================================
-- downtime_queries.sql
-- Manufacturing OEE Analytics — PostgreSQL Schema and Queries
--
-- Purpose:
--   Complete schema definition and analytical queries for OEE (Overall Equipment
--   Effectiveness) calculation, downtime categorization to ISA-95 codes, rolling
--   7-day trend analysis, and bottleneck identification.
--
-- ISA-95 Reference:
--   The equipment_hierarchy table models the ISA-95 / IEC 62264 equipment levels:
--   Enterprise (1) → Site (2) → Area (3) → Work Center (4) → Work Unit (5)
--   This enables OEE aggregation at any level of the equipment tree.
--
-- Usage with Ignition:
--   These queries are intended to be used as Ignition Named Queries.
--   Parameterized inputs (e.g., :equipment_id, :shift_date) map to Named Query
--   parameters in the Ignition Designer Named Query editor.
--
-- Author: Adam Dryden
-- =============================================================================


-- =============================================================================
-- SECTION 1: SCHEMA DEFINITIONS
-- =============================================================================

-- -----------------------------------------------------------------------------
-- equipment_hierarchy
--
-- Represents the ISA-95 physical asset tree. Every production run and downtime
-- event links here, enabling rollup analysis from Work Unit up to Enterprise.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equipment_hierarchy (
    equipment_id    SERIAL          PRIMARY KEY,
    equipment_name  VARCHAR(100)    NOT NULL,
    -- ISA-95 type labels: Enterprise | Site | Area | WorkCenter | WorkUnit
    equipment_type  VARCHAR(50)     NOT NULL,
    -- Self-referencing FK: NULL for root (Enterprise) node
    parent_id       INTEGER         REFERENCES equipment_hierarchy(equipment_id),
    -- Numeric level mirrors ISA-95: 1=Enterprise, 2=Site, 3=Area, 4=WorkCenter, 5=WorkUnit
    isa95_level     SMALLINT        NOT NULL CHECK (isa95_level BETWEEN 1 AND 5),
    -- Arbitrary key-value metadata (e.g., asset tag number, PLC address)
    properties      JSONB           DEFAULT '{}',
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW()
);

-- Index to efficiently walk the hierarchy tree (find all children of a node)
CREATE INDEX IF NOT EXISTS idx_equip_parent ON equipment_hierarchy(parent_id);
CREATE INDEX IF NOT EXISTS idx_equip_level  ON equipment_hierarchy(isa95_level);

-- -----------------------------------------------------------------------------
-- production_runs
--
-- One row per shift × equipment pair. Written by the Ignition Transaction Group
-- that fires at shift start (INSERT) and is updated throughout the shift.
-- At shift end, a Gateway Event Script finalizes the row.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS production_runs (
    run_id                      SERIAL          PRIMARY KEY,
    -- FK to the Work Center or Work Unit producing this run
    equipment_id                INTEGER         NOT NULL
                                    REFERENCES equipment_hierarchy(equipment_id),
    shift_date                  DATE            NOT NULL,
    -- 1 = Day shift, 2 = Afternoon, 3 = Night
    shift_number                SMALLINT        NOT NULL CHECK (shift_number IN (1, 2, 3)),
    -- Total minutes this equipment was SCHEDULED to run this shift
    -- (excludes planned breaks, shift handover, etc.)
    planned_production_time_min INTEGER         NOT NULL CHECK (planned_production_time_min > 0),
    -- Actual run time = planned_production_time_min minus all downtime durations
    -- Updated by Transaction Group or end-of-shift script
    run_time_min                NUMERIC(8, 2),
    -- Total parts/units produced (good + reject)
    total_count                 INTEGER         DEFAULT 0 CHECK (total_count >= 0),
    -- Units that passed quality inspection
    good_count                  INTEGER         DEFAULT 0 CHECK (good_count >= 0),
    -- Engineered ideal cycle time in seconds per unit (from time study or nameplate)
    -- Used in Performance = (ideal_cycle_time_sec × total_count) / (run_time_min × 60)
    ideal_cycle_time_sec        NUMERIC(8, 3)   NOT NULL,
    started_at                  TIMESTAMPTZ,
    ended_at                    TIMESTAMPTZ,
    -- Shift supervisor or auto-close flag
    closed_by                   VARCHAR(100),
    CONSTRAINT chk_good_lte_total CHECK (good_count <= total_count)
);

CREATE INDEX IF NOT EXISTS idx_runs_equipment_date
    ON production_runs(equipment_id, shift_date DESC);
CREATE INDEX IF NOT EXISTS idx_runs_shift_date
    ON production_runs(shift_date DESC);

-- -----------------------------------------------------------------------------
-- downtime_events
--
-- One row per downtime incident. A row is inserted when RunState goes FALSE
-- (via Transaction Group or Tag Change Script). The row is closed (ended_at set)
-- when RunState returns TRUE or when an operator submits the Perspective form.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS downtime_events (
    event_id        SERIAL          PRIMARY KEY,
    equipment_id    INTEGER         NOT NULL
                        REFERENCES equipment_hierarchy(equipment_id),
    -- FK back to the active production run (may be NULL if no active run)
    run_id          INTEGER         REFERENCES production_runs(run_id),
    started_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    -- NULL while event is still open (machine still down)
    ended_at        TIMESTAMPTZ,
    -- Computed duration in minutes; NULL while open
    duration_min    NUMERIC(8, 2)
                        GENERATED ALWAYS AS (
                            EXTRACT(EPOCH FROM (ended_at - started_at)) / 60.0
                        ) STORED,
    -- ISA-95 / SEMI E10 category code. Operator selects from a dropdown.
    -- Valid codes: EQP_FAIL | CHANGEOVER | PLANNED_MAINT |
    --              UNPLANNED_STOP | MATERIAL_WAIT | QUALITY_HOLD
    category_code   VARCHAR(20)     NOT NULL,
    -- Free-text notes entered by operator in Perspective downtime form
    category_detail VARCHAR(500),
    -- Operator who entered/closed the event
    entered_by      VARCHAR(100),
    -- TRUE once the supervisor has reviewed this downtime event
    acknowledged    BOOLEAN         DEFAULT FALSE,
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_downtime_equipment_start
    ON downtime_events(equipment_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_downtime_run
    ON downtime_events(run_id);
CREATE INDEX IF NOT EXISTS idx_downtime_open
    ON downtime_events(equipment_id) WHERE ended_at IS NULL;


-- =============================================================================
-- SECTION 2: SEED DATA (Sample ISA-95 Equipment Tree)
-- =============================================================================

-- This tree represents a single bottling line within a beverage plant.
-- Adjust to match the real asset structure before deploying.

INSERT INTO equipment_hierarchy (equipment_id, equipment_name, equipment_type, parent_id, isa95_level) VALUES
    (1, 'Acme Beverage Co.',     'Enterprise',   NULL, 1),
    (2, 'Denver Plant',          'Site',         1,    2),
    (3, 'Bottling Hall A',       'Area',         2,    3),
    (4, 'Line 3 (330ml PET)',    'WorkCenter',   3,    4),
    (5, 'Filler Head Assembly',  'WorkUnit',     4,    5),
    (6, 'Labeller Unit',         'WorkUnit',     4,    5),
    (7, 'Case Packer',           'WorkUnit',     4,    5)
ON CONFLICT (equipment_id) DO NOTHING;


-- =============================================================================
-- SECTION 3: OEE CALCULATION QUERY
--
-- Calculates OEE and its three components for a specific shift.
-- Intended as a Named Query in Ignition Designer.
--
-- Parameters (Ignition Named Query syntax):
--   :equipment_id  INTEGER  — the Work Center equipment_id
--   :shift_date    DATE     — e.g., CURRENT_DATE
--   :shift_number  INTEGER  — 1, 2, or 3
-- =============================================================================

WITH

-- ─────────────────────────────────────────────────────────────────────────────
-- run_data CTE
-- Retrieves the single production run row for the requested shift.
-- All OEE calculations derive from this base row.
-- ─────────────────────────────────────────────────────────────────────────────
run_data AS (
    SELECT
        r.run_id,
        r.equipment_id,
        r.planned_production_time_min,
        r.run_time_min,
        r.total_count,
        r.good_count,
        r.ideal_cycle_time_sec,
        e.equipment_name,
        e.isa95_level
    FROM production_runs r
    JOIN equipment_hierarchy e USING (equipment_id)
    WHERE r.equipment_id  = :equipment_id
      AND r.shift_date    = :shift_date
      AND r.shift_number  = :shift_number
),

-- ─────────────────────────────────────────────────────────────────────────────
-- downtime_by_run CTE
-- Sums all CLOSED downtime events for this run.
-- We only count closed events (ended_at IS NOT NULL) because open events
-- have no duration yet and could double-count if the machine is still down.
-- ─────────────────────────────────────────────────────────────────────────────
downtime_by_run AS (
    SELECT
        run_id,
        SUM(duration_min)  AS total_downtime_min,
        COUNT(*)           AS downtime_event_count
    FROM downtime_events
    WHERE run_id = (SELECT run_id FROM run_data)
      AND ended_at IS NOT NULL          -- only closed events have valid duration
    GROUP BY run_id
),

-- ─────────────────────────────────────────────────────────────────────────────
-- downtime_categorization CTE
-- Maps each ISA-95 category code to a human-readable label and sums minutes.
-- This is what feeds the Pareto chart on the Perspective dashboard.
--
-- ISA-95 Part 3 defines Work Center activity states; SEMI E10 provides more
-- granular sub-states. We use a pragmatic subset here.
-- ─────────────────────────────────────────────────────────────────────────────
downtime_categorization AS (
    SELECT
        d.category_code,
        -- Map raw codes to display labels per ISA-95 / SEMI E10
        CASE d.category_code
            WHEN 'EQP_FAIL'      THEN 'Equipment Failure'          -- ISA-95: Unscheduled Downtime
            WHEN 'CHANGEOVER'    THEN 'Changeover / Setup'         -- ISA-95: Scheduled Downtime
            WHEN 'PLANNED_MAINT' THEN 'Planned Maintenance'        -- ISA-95: Scheduled Downtime
            WHEN 'UNPLANNED_STOP' THEN 'Unplanned Stop (Unknown)'  -- ISA-95: Unscheduled Downtime
            WHEN 'MATERIAL_WAIT' THEN 'Material Starvation'        -- ISA-95: Scheduled (starve)
            WHEN 'QUALITY_HOLD'  THEN 'Quality Hold'               -- ISA-95: Non-Scheduled
            ELSE                      'Other / Uncategorized'
        END                                             AS category_label,
        -- Which OEE loss bucket does this code affect?
        -- Availability losses: unplanned stops that reduce run time
        -- Note: CHANGEOVER and PLANNED_MAINT are often excluded from Availability
        -- per the plant's OEE contract — adjust per client preference
        CASE d.category_code
            WHEN 'EQP_FAIL'       THEN 'Availability'
            WHEN 'UNPLANNED_STOP' THEN 'Availability'
            WHEN 'MATERIAL_WAIT'  THEN 'Availability'
            WHEN 'CHANGEOVER'     THEN 'Scheduled'   -- excluded from OEE denominator if desired
            WHEN 'PLANNED_MAINT'  THEN 'Scheduled'
            WHEN 'QUALITY_HOLD'   THEN 'Quality'
            ELSE                       'Other'
        END                                             AS oee_loss_bucket,
        SUM(d.duration_min)                             AS total_minutes,
        COUNT(*)                                        AS event_count
    FROM downtime_events d
    WHERE d.run_id = (SELECT run_id FROM run_data)
      AND d.ended_at IS NOT NULL
    GROUP BY d.category_code
),

-- ─────────────────────────────────────────────────────────────────────────────
-- oee_components CTE
-- Computes the three OEE factors:
--
--   Availability = (Planned Production Time − Total Downtime) / Planned Production Time
--   Performance  = (Ideal Cycle Time [sec] × Total Count) / (Run Time [sec])
--   Quality      = Good Count / Total Count
--
-- NULLIF guards prevent division-by-zero on fresh/empty runs.
-- LEAST(..., 1.0) prevents Performance from exceeding 100% due to data entry errors
-- (e.g., actual cycle time faster than the assumed ideal — review ideal_cycle_time_sec).
-- ─────────────────────────────────────────────────────────────────────────────
oee_components AS (
    SELECT
        r.run_id,
        r.equipment_name,
        r.planned_production_time_min                       AS planned_min,
        COALESCE(dt.total_downtime_min, 0)                  AS downtime_min,
        -- Availability
        ROUND(
            (r.planned_production_time_min - COALESCE(dt.total_downtime_min, 0))
            / NULLIF(r.planned_production_time_min::NUMERIC, 0),
            4
        )                                                   AS availability,
        -- Performance: convert run_time to seconds for unit consistency
        ROUND(
            LEAST(
                (r.ideal_cycle_time_sec * r.total_count)
                / NULLIF((r.run_time_min * 60.0)::NUMERIC, 0),
                1.0     -- cap at 100%; investigate if > 1.0 (ideal_cycle_time may be wrong)
            ),
            4
        )                                                   AS performance,
        -- Quality
        ROUND(
            r.good_count::NUMERIC
            / NULLIF(r.total_count::NUMERIC, 0),
            4
        )                                                   AS quality,
        r.total_count,
        r.good_count,
        r.total_count - r.good_count                        AS reject_count,
        COALESCE(dt.downtime_event_count, 0)                AS downtime_events
    FROM run_data r
    LEFT JOIN downtime_by_run dt USING (run_id)
)

-- ─────────────────────────────────────────────────────────────────────────────
-- Final SELECT: combine components into OEE score + downtime Pareto
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    c.run_id,
    c.equipment_name,
    c.planned_min,
    c.downtime_min,
    c.availability,
    c.performance,
    c.quality,
    -- OEE = A × P × Q
    ROUND(c.availability * c.performance * c.quality, 4)    AS oee,
    -- Percentage representation for dashboards
    ROUND(c.availability * 100, 2)                          AS availability_pct,
    ROUND(c.performance  * 100, 2)                          AS performance_pct,
    ROUND(c.quality      * 100, 2)                          AS quality_pct,
    ROUND(c.availability * c.performance * c.quality * 100, 2) AS oee_pct,
    c.total_count,
    c.good_count,
    c.reject_count,
    c.downtime_events,
    -- Downtime breakdown as JSON array (for Perspective table component)
    COALESCE(
        (
            SELECT json_agg(
                json_build_object(
                    'category_code',  dc.category_code,
                    'category_label', dc.category_label,
                    'oee_loss_bucket', dc.oee_loss_bucket,
                    'total_minutes',  dc.total_minutes,
                    'event_count',    dc.event_count
                )
                ORDER BY dc.total_minutes DESC
            )
            FROM downtime_categorization dc
        ),
        '[]'::JSON
    )                                                       AS downtime_pareto
FROM oee_components c;


-- =============================================================================
-- SECTION 4: ROLLING 7-DAY OEE TREND (WINDOW FUNCTIONS)
--
-- Returns one row per shift for the last 7 days, for a given Work Center.
-- Uses LAG() to show shift-over-shift OEE delta, helping operators spot
-- degradation trends before they become crisis events.
--
-- Parameters:
--   :equipment_id  INTEGER
-- =============================================================================

WITH

daily_shift_oee AS (
    -- Compute OEE for every shift in the last 7 days
    SELECT
        r.shift_date,
        r.shift_number,
        -- Availability
        ROUND(
            (r.planned_production_time_min
             - COALESCE(SUM(d.duration_min) FILTER (WHERE d.ended_at IS NOT NULL), 0))
            / NULLIF(r.planned_production_time_min::NUMERIC, 0),
            4
        )                               AS availability,
        -- Performance
        ROUND(
            LEAST(
                (r.ideal_cycle_time_sec * r.total_count)
                / NULLIF((r.run_time_min * 60.0)::NUMERIC, 0),
                1.0
            ),
            4
        )                               AS performance,
        -- Quality
        ROUND(
            r.good_count::NUMERIC / NULLIF(r.total_count::NUMERIC, 0),
            4
        )                               AS quality,
        SUM(COALESCE(d.duration_min, 0)) AS total_downtime_min
    FROM production_runs r
    LEFT JOIN downtime_events d
           ON d.run_id = r.run_id
    WHERE r.equipment_id = :equipment_id
      AND r.shift_date  >= CURRENT_DATE - INTERVAL '7 days'
    GROUP BY
        r.run_id,
        r.shift_date,
        r.shift_number,
        r.planned_production_time_min,
        r.run_time_min,
        r.total_count,
        r.good_count,
        r.ideal_cycle_time_sec
),

oee_with_score AS (
    SELECT
        shift_date,
        shift_number,
        availability,
        performance,
        quality,
        ROUND(availability * performance * quality, 4)      AS oee,
        total_downtime_min
    FROM daily_shift_oee
),

-- ─────────────────────────────────────────────────────────────────────────────
-- Window function: LAG() retrieves the OEE value from the previous shift
-- so we can calculate the delta. PARTITION BY nothing here — we want the
-- global ordering across all shifts, not reset per day.
-- ─────────────────────────────────────────────────────────────────────────────
oee_trend AS (
    SELECT
        shift_date,
        shift_number,
        availability,
        performance,
        quality,
        oee,
        total_downtime_min,
        -- Previous shift's OEE (NULL for the first row in the window)
        LAG(oee) OVER (
            ORDER BY shift_date ASC, shift_number ASC
        )                                                   AS prev_shift_oee,
        -- Rolling 7-shift average OEE (smooths single-shift anomalies)
        ROUND(
            AVG(oee) OVER (
                ORDER BY shift_date ASC, shift_number ASC
                ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
            ),
            4
        )                                                   AS rolling_7shift_avg_oee,
        -- Cumulative downtime this week
        SUM(total_downtime_min) OVER (
            ORDER BY shift_date ASC, shift_number ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                                   AS cumulative_downtime_min,
        -- Row number to identify the most recent shift
        ROW_NUMBER() OVER (
            ORDER BY shift_date DESC, shift_number DESC
        )                                                   AS recency_rank
    FROM oee_with_score
)

SELECT
    shift_date,
    shift_number,
    ROUND(availability * 100, 2)                        AS availability_pct,
    ROUND(performance  * 100, 2)                        AS performance_pct,
    ROUND(quality      * 100, 2)                        AS quality_pct,
    ROUND(oee          * 100, 2)                        AS oee_pct,
    ROUND(rolling_7shift_avg_oee * 100, 2)              AS rolling_7shift_avg_pct,
    -- OEE delta vs previous shift (positive = improvement)
    ROUND((oee - COALESCE(prev_shift_oee, oee)) * 100, 2) AS shift_oee_delta_pct,
    ROUND(total_downtime_min, 1)                        AS downtime_min,
    ROUND(cumulative_downtime_min, 1)                   AS cumulative_downtime_min,
    -- Flag shifts that fell below 85% (world-class threshold)
    CASE WHEN oee < 0.85 THEN TRUE ELSE FALSE END       AS below_world_class
FROM oee_trend
ORDER BY shift_date ASC, shift_number ASC;


-- =============================================================================
-- SECTION 5: BOTTLENECK ANALYSIS
--
-- Identifies the equipment unit with the HIGHEST total downtime contribution
-- within a given Work Center and date range.
--
-- This is the query that answers: "Which machine is killing our line OEE?"
-- It aggregates downtime by equipment, by category, and computes each unit's
-- share of total line downtime — the first step in a root-cause analysis.
--
-- Parameters:
--   :work_center_id  INTEGER  — the Work Center equipment_id (ISA-95 Level 4)
--   :start_date      DATE
--   :end_date        DATE
-- =============================================================================

WITH

-- ─────────────────────────────────────────────────────────────────────────────
-- work_units: find all Work Units (level 5) beneath the given Work Center
-- Uses a recursive CTE to walk the hierarchy tree, which handles arbitrary
-- depth (useful when Work Centers have sub-cells, e.g. a filler with multiple
-- heads each represented as a separate Work Unit).
-- ─────────────────────────────────────────────────────────────────────────────
work_units AS (
    -- Anchor: the Work Center itself
    SELECT equipment_id, equipment_name, isa95_level
    FROM equipment_hierarchy
    WHERE equipment_id = :work_center_id

    UNION ALL

    -- Recursive: all descendants
    SELECT e.equipment_id, e.equipment_name, e.isa95_level
    FROM equipment_hierarchy e
    JOIN work_units wu ON e.parent_id = wu.equipment_id
),

-- ─────────────────────────────────────────────────────────────────────────────
-- unit_downtime: total downtime minutes per Work Unit in the date range
-- ─────────────────────────────────────────────────────────────────────────────
unit_downtime AS (
    SELECT
        d.equipment_id,
        wu.equipment_name,
        d.category_code,
        SUM(d.duration_min)     AS downtime_min,
        COUNT(*)                AS event_count
    FROM downtime_events d
    JOIN production_runs r  ON r.run_id      = d.run_id
    JOIN work_units wu      ON wu.equipment_id = d.equipment_id
    WHERE r.shift_date BETWEEN :start_date AND :end_date
      AND d.ended_at IS NOT NULL
    GROUP BY d.equipment_id, wu.equipment_name, d.category_code
),

-- ─────────────────────────────────────────────────────────────────────────────
-- total_line_downtime: sum across ALL units — used to compute percentage share
-- ─────────────────────────────────────────────────────────────────────────────
total_line_downtime AS (
    SELECT SUM(downtime_min) AS total_min
    FROM unit_downtime
)

SELECT
    ud.equipment_id,
    ud.equipment_name,
    ud.category_code,
    CASE ud.category_code
        WHEN 'EQP_FAIL'       THEN 'Equipment Failure'
        WHEN 'CHANGEOVER'     THEN 'Changeover / Setup'
        WHEN 'PLANNED_MAINT'  THEN 'Planned Maintenance'
        WHEN 'UNPLANNED_STOP' THEN 'Unplanned Stop'
        WHEN 'MATERIAL_WAIT'  THEN 'Material Starvation'
        WHEN 'QUALITY_HOLD'   THEN 'Quality Hold'
        ELSE                       'Other'
    END                                                 AS category_label,
    ROUND(ud.downtime_min, 1)                           AS downtime_min,
    ud.event_count,
    -- Percentage of TOTAL LINE downtime attributable to this unit+category
    ROUND(ud.downtime_min / NULLIF(tl.total_min, 0) * 100, 2) AS pct_of_total_downtime,
    -- Rank the unit+category combinations: rank 1 = biggest bottleneck
    RANK() OVER (
        ORDER BY ud.downtime_min DESC
    )                                                   AS bottleneck_rank,
    -- Cumulative percentage (for Pareto analysis — typically 20% of causes = 80% of time)
    ROUND(
        SUM(ud.downtime_min) OVER (
            ORDER BY ud.downtime_min DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) / NULLIF(tl.total_min, 0) * 100,
        2
    )                                                   AS cumulative_pct
FROM unit_downtime ud
CROSS JOIN total_line_downtime tl
ORDER BY ud.downtime_min DESC;
