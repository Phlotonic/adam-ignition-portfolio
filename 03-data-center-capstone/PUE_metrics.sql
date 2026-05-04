-- =============================================================================
-- PUE_metrics.sql
-- TimescaleDB Time-Series Schema for Data Center Power & PUE Analytics
--
-- PUE (Power Usage Effectiveness) is the universal data center efficiency metric:
--   PUE = Total Facility Power (kW) / IT Equipment Power (kW)
--
-- Defined by The Green Grid (https://www.thegreengrid.org), PUE quantifies how
-- much overhead power (cooling, lighting, UPS losses, power distribution) is
-- consumed per unit of IT work performed.
--
-- Rating benchmarks (Green Grid, 2021):
--   PUE 1.0:       Theoretical perfect (physically impossible — some overhead exists)
--   PUE 1.1–1.2:   World class (Google ~1.10, Meta ~1.09)
--   PUE 1.2–1.4:   Efficient (modern enterprise with cold-aisle containment)
--   PUE 1.4–1.6:   Average (typical enterprise data center)
--   PUE > 1.6:     Poor (legacy air-cooled, old transformer/UPS infrastructure)
--
-- TimescaleDB Features Used:
--   - Hypertables:          Automatic time-based partitioning into 'chunks'
--   - time_bucket():        Aggregate to arbitrary time intervals (15min, 1hr, etc.)
--   - Continuous aggregates: Incrementally refreshed materialized views
--   - Retention policies:   Automatic deletion of old raw data chunks
--
-- Author: Adam Dryden
-- =============================================================================


-- =============================================================================
-- SECTION 1: PREREQUISITES
-- =============================================================================

-- TimescaleDB must be installed as a PostgreSQL extension.
-- On a fresh TimescaleDB Docker container, this extension is pre-loaded.
-- On a standard PostgreSQL instance, run: CREATE EXTENSION timescaledb CASCADE;
-- (requires superuser; do this before any other DDL in this file)
-- CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;


-- =============================================================================
-- SECTION 2: SCHEMA DEFINITION
-- =============================================================================

-- -----------------------------------------------------------------------------
-- facility_power: raw time-series power readings
--
-- Each row is a single power measurement from one zone at one point in time.
-- Zones separate the power draw by category, enabling PUE decomposition.
--
-- Zone naming convention:
--   'IT_Load'      — aggregate power of all IT equipment (servers, storage, networking)
--   'CRAC_N'       — Computer Room Air Conditioning unit N
--   'CRAH_N'       — Computer Room Air Handler unit N (chilled water systems)
--   'UPS_Loss'     — power lost in UPS conversion (typically 3–8% of IT load)
--   'PDU_Loss'     — power distribution unit transformer losses
--   'Lighting'     — data hall and mechanical room lighting
--   'Other'        — miscellaneous (security systems, monitoring equipment)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS facility_power (
    -- Timestamp: always UTC. TimescaleDB requires a NOT NULL timestamptz
    -- column as the partitioning dimension for hypertables.
    time        TIMESTAMPTZ         NOT NULL,
    -- Zone identifier: which subsystem produced this power reading
    zone        TEXT                NOT NULL,
    -- Instantaneous power in kilowatts at the time of this reading.
    -- Data source: smart PDU SNMP poll, BMS (Building Management System) feed,
    -- or power meter MQTT telemetry via Ignition Tag History
    power_kw    DOUBLE PRECISION    NOT NULL,

    PRIMARY KEY (time, zone)
);

-- Comment the table for documentation visibility in pg_catalog
COMMENT ON TABLE facility_power IS
    'Raw power readings by zone for PUE calculation. '
    'One row per zone per measurement interval (typically 1 minute). '
    'Partitioned as a TimescaleDB hypertable by time.';

COMMENT ON COLUMN facility_power.zone IS
    'Power zone: IT_Load | CRAC_N | CRAH_N | UPS_Loss | PDU_Loss | Lighting | Other';
COMMENT ON COLUMN facility_power.power_kw IS
    'Instantaneous power consumption in kilowatts for this zone at this timestamp';


-- -----------------------------------------------------------------------------
-- Convert to a TimescaleDB hypertable
--
-- A hypertable is a TimescaleDB-managed table that automatically partitions
-- rows into time-based 'chunks'. Each chunk is a regular PostgreSQL table
-- covering a specific time interval (chunk_time_interval).
--
-- Benefits:
--   1. Efficient time-range queries: only relevant chunks are scanned
--   2. Fast bulk deletes: drop a chunk (O(1)) instead of DELETE rows (O(n))
--   3. Parallel query execution: each chunk can be queried on a separate worker
--   4. Better compression per chunk (data is correlated within a time window)
--
-- chunk_time_interval = 1 day: each chunk covers 24 hours of data.
-- With ~60 zones × 60 readings/hour × 24 hours = ~86,400 rows/chunk.
-- At 1 Hz polling this would be ~5.18M rows/chunk — adjust interval accordingly.
-- -----------------------------------------------------------------------------
SELECT create_hypertable(
    'facility_power',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists       => TRUE  -- idempotent: safe to re-run this script
);

-- Secondary index on (zone, time) for per-zone range queries.
-- Without this, a query like "SELECT * FROM facility_power WHERE zone = 'IT_Load'
-- AND time > NOW() - INTERVAL '1 hour'" would scan all chunks.
CREATE INDEX IF NOT EXISTS idx_facility_power_zone_time
    ON facility_power (zone, time DESC);

COMMENT ON INDEX idx_facility_power_zone_time IS
    'Supports per-zone time-range queries efficiently across chunks';


-- =============================================================================
-- SECTION 3: PUE CALCULATION — 15-MINUTE BUCKETS
--
-- Named Query: "GetPUE_15min"
-- Use for: real-time PUE dashboard in Ignition Perspective
-- Binds to: Perspective Time Series Chart component (time on X, PUE on Y)
--
-- time_bucket(interval, time_column) is a TimescaleDB function that truncates
-- a timestamp to a regular interval boundary, similar to date_trunc() but:
--   - Supports arbitrary intervals (e.g., '15 minutes', '6 hours', '1 week')
--   - Aligns to the epoch (2000-01-01) by default, ensuring consistent buckets
--     across days regardless of when the database started
-- =============================================================================
SELECT
    time_bucket('15 minutes', time)                             AS bucket,

    -- IT load: total power consumed by all servers, storage, and networking.
    -- This is the 'useful work' denominator of the PUE fraction.
    -- FILTER (WHERE ...) is a PostgreSQL aggregate extension that applies a
    -- condition to each row before summing, equivalent to:
    --   SUM(CASE WHEN zone = 'IT_Load' THEN power_kw ELSE 0 END)
    -- but cleaner and slightly faster.
    SUM(power_kw) FILTER (WHERE zone = 'IT_Load')               AS it_power_kw,

    -- Overhead power: everything except IT load.
    -- Includes cooling (CRAC/CRAH), UPS losses, PDU losses, lighting, other.
    SUM(power_kw) FILTER (WHERE zone != 'IT_Load')              AS overhead_power_kw,

    -- Total facility power: IT + all overhead subsystems
    SUM(power_kw)                                               AS total_facility_kw,

    -- PUE = Total / IT
    -- NULLIF(expr, 0) returns NULL instead of 0 when IT load is zero, which
    -- prevents a division-by-zero error during off-hours when servers are down.
    -- The ::NUMERIC cast + ROUND ensures consistent decimal precision in the output.
    ROUND(
        (SUM(power_kw) / NULLIF(
            SUM(power_kw) FILTER (WHERE zone = 'IT_Load'),
            0
        ))::NUMERIC,
        4
    )                                                           AS pue,

    -- Wasted overhead power: the additional kW beyond perfect efficiency (PUE=1.0)
    -- Formula: (PUE - 1.0) × IT_Load = overhead_power + losses
    -- Useful for quantifying the cost of inefficiency in dollar terms:
    --   wasted_kw × $/kWh × 8760 h/yr = annual overhead cost above PUE 1.0
    ROUND(
        (
            SUM(power_kw) - COALESCE(
                SUM(power_kw) FILTER (WHERE zone = 'IT_Load'), 0
            )
        )::NUMERIC,
        2
    )                                                           AS overhead_kw

FROM facility_power
WHERE time >= NOW() - INTERVAL '24 hours'
GROUP BY bucket
ORDER BY bucket DESC;


-- =============================================================================
-- SECTION 4: CONTINUOUS AGGREGATE — HOURLY PUE
--
-- A continuous aggregate is a TimescaleDB materialized view that refreshes
-- incrementally. Unlike a standard PostgreSQL materialized view (which requires
-- a full re-scan on REFRESH), TimescaleDB only processes NEW data since the
-- last refresh.
--
-- Benefits for a PUE dashboard:
--   - Dashboard queries at hourly resolution don't touch raw data (fast)
--   - Background refresh job runs every 30 minutes
--   - Historical trend queries (7-day, 30-day PUE) scan pre-computed data
-- =============================================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS pue_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time)                                 AS hour,

    SUM(power_kw) FILTER (WHERE zone = 'IT_Load')               AS it_power_kw,
    SUM(power_kw) FILTER (WHERE zone != 'IT_Load')              AS overhead_power_kw,
    SUM(power_kw)                                               AS total_facility_kw,

    -- PUE with 4 decimal precision for trend analysis
    ROUND(
        (SUM(power_kw) / NULLIF(
            SUM(power_kw) FILTER (WHERE zone = 'IT_Load'), 0
        ))::NUMERIC,
        4
    )                                                           AS pue,

    -- Quality metric: number of IT_Load readings in this hour.
    -- If < expected reading count, the hour has data gaps.
    -- Expected = readings_per_hour × IT_Load zones (e.g., 60 × 1 = 60 for 1/min)
    COUNT(*) FILTER (WHERE zone = 'IT_Load')                    AS it_sample_count,

    -- Track which zones contributed data (for data completeness audit)
    COUNT(DISTINCT zone)                                        AS zone_count

FROM facility_power
GROUP BY hour
WITH NO DATA;  -- Don't backfill history on CREATE; only materialize future data.
               -- To backfill: call refresh_continuous_aggregate() manually.

COMMENT ON MATERIALIZED VIEW pue_hourly IS
    'Hourly PUE aggregates, materialized by TimescaleDB continuous aggregate. '
    'Used for trend dashboards and historical analysis. '
    'Refreshed every 30 minutes by the refresh policy below.';

-- -----------------------------------------------------------------------------
-- Refresh policy: automatically update pue_hourly in the background.
-- start_offset: how far back to look for new data (2h covers late-arriving data)
-- end_offset:   do not include the most recent 1h (data may still be arriving)
-- schedule_interval: how often the refresh job runs
-- -----------------------------------------------------------------------------
SELECT add_continuous_aggregate_policy(
    'pue_hourly',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists     => TRUE
);


-- =============================================================================
-- SECTION 5: ANOMALY DETECTION — PUE > 1.5 IN LAST 24 HOURS
--
-- Surfaces time buckets where PUE exceeded the 'average/poor' threshold.
-- In a well-managed modern data center, PUE > 1.4 warrants investigation;
-- PUE > 1.5 indicates likely cooling system issues or measurement problems.
--
-- Named Query: "GetPUE_Anomalies_24h"
-- Use for: anomaly alert dashboard, operator notification
-- Returns: one row per anomalous 15-minute bucket, ordered by severity
-- =============================================================================
WITH

-- Compute PUE for every 15-minute bucket in the past 24 hours
pue_buckets AS (
    SELECT
        time_bucket('15 minutes', time)                         AS bucket,
        SUM(power_kw)                                           AS total_kw,
        COALESCE(
            SUM(power_kw) FILTER (WHERE zone = 'IT_Load'), 0
        )                                                       AS it_kw,
        SUM(power_kw) / NULLIF(
            SUM(power_kw) FILTER (WHERE zone = 'IT_Load'), 0
        )                                                       AS pue
    FROM facility_power
    WHERE time >= NOW() - INTERVAL '24 hours'
    GROUP BY bucket
),

-- Filter to only anomalous buckets and compute overhead metrics
anomaly_buckets AS (
    SELECT
        bucket,
        total_kw,
        it_kw,
        (total_kw - it_kw)  AS overhead_kw,
        pue,
        -- Classify by severity based on PUE magnitude
        -- These thresholds align with The Green Grid's PUE rating categories
        CASE
            WHEN pue > 2.0 THEN 'critical'  -- Extreme: possible measurement fault
                                            -- or catastrophic cooling failure
            WHEN pue > 1.7 THEN 'high'      -- Cooling unit likely offline or overloaded
            WHEN pue > 1.5 THEN 'warning'   -- Investigate CRAC setpoints, filter condition
            ELSE                 'normal'   -- Should not appear (filtered below)
        END                                                     AS severity
    FROM pue_buckets
    WHERE pue > 1.5  -- Only return anomalous buckets
      AND pue IS NOT NULL
)

SELECT
    bucket                                  AS anomaly_time,
    ROUND(pue::NUMERIC, 4)                  AS pue_value,

    -- Power in rounded kW for display
    ROUND(total_kw::NUMERIC, 2)             AS total_facility_kw,
    ROUND(it_kw::NUMERIC, 2)               AS it_equipment_kw,
    ROUND(overhead_kw::NUMERIC, 2)         AS overhead_kw,

    severity,

    -- Wasted cooling kW: power consumed above theoretical PUE=1.0
    -- At $0.10/kWh, this column × hours × 0.10 = dollar cost of inefficiency
    ROUND(((pue - 1.0) * it_kw)::NUMERIC, 2)   AS wasted_cooling_kw,

    -- Carbon impact estimate (assumes 0.386 kg CO2/kWh — US average grid, EPA 2023)
    ROUND(((pue - 1.0) * it_kw * 0.386 / 1000.0)::NUMERIC, 4) AS wasted_co2_tonnes_per_hour

FROM anomaly_buckets
ORDER BY bucket DESC;


-- =============================================================================
-- SECTION 6: DATA RETENTION POLICY
--
-- Keep raw data for 90 days (sufficient for root-cause analysis lookback).
-- The pue_hourly continuous aggregate retains hourly summaries indefinitely
-- (or until its own retention policy is applied separately).
--
-- When a chunk's data exceeds the retention window, TimescaleDB drops the
-- entire chunk file (O(1) operation) rather than issuing row-level DELETEs,
-- which would create dead tuples requiring VACUUM.
-- =============================================================================
SELECT add_retention_policy(
    'facility_power',
    drop_after    => INTERVAL '90 days',
    if_not_exists => TRUE
);


-- =============================================================================
-- SECTION 7: SAMPLE DATA INSERTION
-- Test data for verifying the schema and queries.
-- =============================================================================
INSERT INTO facility_power (time, zone, power_kw) VALUES
    -- Normal period: PUE ≈ 1.35 (good)
    (NOW() - INTERVAL '2 hours', 'IT_Load',  100.0),
    (NOW() - INTERVAL '2 hours', 'CRAC_1',    20.0),
    (NOW() - INTERVAL '2 hours', 'CRAC_2',    10.0),
    (NOW() - INTERVAL '2 hours', 'UPS_Loss',   3.5),
    (NOW() - INTERVAL '2 hours', 'PDU_Loss',   1.5),
    (NOW() - INTERVAL '2 hours', 'Lighting',   0.5),
    -- Anomalous period: PUE ≈ 1.65 (CRAC_1 overcooling or stuck on max)
    (NOW() - INTERVAL '30 minutes', 'IT_Load',  100.0),
    (NOW() - INTERVAL '30 minutes', 'CRAC_1',    42.0),  -- abnormally high
    (NOW() - INTERVAL '30 minutes', 'CRAC_2',    10.0),
    (NOW() - INTERVAL '30 minutes', 'UPS_Loss',   3.5),
    (NOW() - INTERVAL '30 minutes', 'PDU_Loss',   1.5),
    (NOW() - INTERVAL '30 minutes', 'Lighting',   0.5),
    -- Recent normal
    (NOW() - INTERVAL '5 minutes',  'IT_Load',  100.0),
    (NOW() - INTERVAL '5 minutes',  'CRAC_1',    18.0),
    (NOW() - INTERVAL '5 minutes',  'CRAC_2',    10.5),
    (NOW() - INTERVAL '5 minutes',  'UPS_Loss',   3.5),
    (NOW() - INTERVAL '5 minutes',  'PDU_Loss',   1.5),
    (NOW() - INTERVAL '5 minutes',  'Lighting',   0.5)
ON CONFLICT DO NOTHING;
