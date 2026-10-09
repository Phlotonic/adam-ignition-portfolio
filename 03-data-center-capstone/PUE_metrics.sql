-- =============================================================================
-- PUE_metrics.sql (Ecosystem Telemetry Schema)
-- TimescaleDB Schema for the Regenerative Triple-Bottom-Line Data Center
--
-- Note: When running with Docker Compose, this schema is automatically applied
-- on startup from ./init-scripts/01_ecosystem_telemetry.sql.
-- This file is maintained for standalone manual execution via psql:
--   docker exec -i timescaledb psql -U postgres -d datacenter < PUE_metrics.sql
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- =============================================================================
-- SECTION 1: HYPERTABLE DEFINITION
-- =============================================================================
CREATE TABLE IF NOT EXISTS ecosystem_telemetry (
    time                      TIMESTAMPTZ         NOT NULL,
    node_id                   TEXT                NOT NULL,
    participant_id            TEXT                NOT NULL DEFAULT 'default_operator',
    
    -- Thermodynamics (Two-Phase Immersion with HKUST-1 MOF)
    heat_flux_w_cm2           DOUBLE PRECISION    NULL,
    fluid_temp_c              DOUBLE PRECISION    NULL,
    vapor_pressure_kpa        DOUBLE PRECISION    NULL,

    -- Hydrology (MOF-303 Atmospheric Water Generation)
    water_harvested_liters    DOUBLE PRECISION    NULL,

    -- Economics (Triple-Bottom-Line Compute & Payout)
    compute_yield_usd         DOUBLE PRECISION    NULL,
    distributed_profit_usd    DOUBLE PRECISION    NULL,
    unsettled_balance_usd     DOUBLE PRECISION    NULL,
    
    PRIMARY KEY (time, node_id)
);

COMMENT ON TABLE ecosystem_telemetry IS
    'Unified time-series hypertable tracking thermodynamics, hydrology, and decentralized economics.';

-- Convert to TimescaleDB Hypertable partitioned by 1-day chunks
SELECT create_hypertable(
    'ecosystem_telemetry',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists       => TRUE
);

-- Compound index for rapid per-node historical lookups across chunks
CREATE INDEX IF NOT EXISTS idx_ecosystem_telemetry_node_time
    ON ecosystem_telemetry (node_id, time DESC);

-- =============================================================================
-- SECTION 2: CONTINUOUS AGGREGATE — HOURLY ECOSYSTEM YIELD
-- =============================================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_ecosystem_yield
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time)                                 AS hour,
    participant_id,
    MAX(heat_flux_w_cm2)                                        AS max_heat_flux,
    AVG(fluid_temp_c)                                           AS avg_fluid_temp,
    SUM(water_harvested_liters)                                 AS total_water_liters,
    SUM(compute_yield_usd)                                      AS total_compute_value,
    SUM(distributed_profit_usd)                                 AS total_profit_paid
FROM ecosystem_telemetry
GROUP BY hour, participant_id
WITH NO DATA;

-- Background refresh policy for continuous aggregate (every 30 mins)
SELECT add_continuous_aggregate_policy(
    'hourly_ecosystem_yield',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists     => TRUE
);

-- =============================================================================
-- SECTION 3: DATA RETENTION POLICY
-- Automatically prune raw telemetry chunks older than 90 days
-- =============================================================================
SELECT add_retention_policy(
    'ecosystem_telemetry',
    drop_after    => INTERVAL '90 days',
    if_not_exists => TRUE
);

-- =============================================================================
-- SECTION 4: INITIAL SEED SAMPLE DATA
-- =============================================================================
INSERT INTO ecosystem_telemetry (
    time, node_id, participant_id, heat_flux_w_cm2, fluid_temp_c, vapor_pressure_kpa, 
    water_harvested_liters, compute_yield_usd, distributed_profit_usd, unsettled_balance_usd
) VALUES 
    (NOW() - INTERVAL '15 minutes', 'Pod_A01', 'Wallet_0x123', 135.2, 54.8, 101.4, 6.7, 108.0, 108.0, 0.0),
    (NOW() - INTERVAL '10 minutes', 'Pod_A02', 'Wallet_0x456', 138.1, 55.2, 101.8, 6.9, 110.5, 110.5, 0.0),
    (NOW() - INTERVAL '5 minutes',  'Pod_A03', 'Wallet_0x789', 142.0, 56.1, 102.1, 7.1, 113.6, 0.0, 113.6)
ON CONFLICT DO NOTHING;
