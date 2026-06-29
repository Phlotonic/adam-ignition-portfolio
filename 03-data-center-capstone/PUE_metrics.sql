-- =============================================================================
-- Ecosystem_Metrics.sql (Formerly PUE_metrics.sql)
-- TimescaleDB Schema for the Regenerative Triple-Bottom-Line Data Center
--
-- WHY WE CHANGED THIS:
-- Traditional data centers only track PUE (Power Usage Effectiveness). 
-- That is a 1-dimensional metric. We are tracking a Triple-Bottom-Line:
--   1. Thermodynamics (HKUST-1 Immersion Cooling, CHF, HTC)
--   2. Hydrology (Atmospheric Water Generation via MOF desiccants)
--   3. Economics (Compute Yield, Network Participant Profit Distribution)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- =============================================================================
-- SECTION 1: THE CORE TELEMETRY HYPERTABLE
-- 
-- WHY A HYPERTABLE?
-- TimescaleDB hypertables partition data into time-based "chunks". When we are
-- ingesting high-frequency telemetry from thousands of immersion pods and AWG 
-- units, traditional SQL tables slow down during inserts. Hypertables keep 
-- inserts at O(1) time complexity by writing to the newest chunk.
-- =============================================================================

CREATE TABLE IF NOT EXISTS ecosystem_telemetry (
    time                      TIMESTAMPTZ         NOT NULL,
    
    -- The physical hardware unit (e.g., 'ImmersionPod_A01', 'AWG_RoofUnit_1')
    node_id                   TEXT                NOT NULL,
    
    -- The participant or wallet address providing the compute or IP
    participant_id            TEXT                NOT NULL,

    -- ==========================================
    -- THERMODYNAMICS (MILESTONE 1)
    -- ==========================================
    -- Traditional air cooling tracks generic 'Rack Temp'. 
    -- Two-phase immersion boils a dielectric fluid directly on the silicon.
    -- We track the heat flux to ensure we don't hit the Critical Heat Flux (CHF).
    -- HKUST-1 MOF coatings on copper meshes allow us to push this to ~170 W/cm2.
    heat_flux_w_cm2           DOUBLE PRECISION    NULL,
    fluid_temp_c              DOUBLE PRECISION    NULL,
    vapor_pressure_kpa        DOUBLE PRECISION    NULL,

    -- ==========================================
    -- HYDROLOGY (MILESTONE 3 - AWG)
    -- ==========================================
    -- Water produced by the MOF-303 / COF-309 Atmospheric Water Generators,
    -- driven by the waste heat from the immersion pods above.
    water_harvested_liters    DOUBLE PRECISION    NULL,

    -- ==========================================
    -- ECONOMICS (MILESTONE 6 - TRIPLE BOTTOM LINE)
    -- ==========================================
    -- Compute yield is the actual valuable work done (e.g., AI model training).
    -- We measure the value of this compute in USD (or token equivalent)
    -- and distribute profit directly to the participant.
    compute_yield_usd         DOUBLE PRECISION    NULL,
    distributed_profit_usd    DOUBLE PRECISION    NULL,
    
    PRIMARY KEY (time, node_id)
);

COMMENT ON TABLE ecosystem_telemetry IS
    'Unified hypertable tracking thermodynamics, hydrology, and decentralized economics.';

-- Convert the standard Postgres table into a TimescaleDB Hypertable partitioned by time.
SELECT create_hypertable(
    'ecosystem_telemetry',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists       => TRUE
);

-- =============================================================================
-- SECTION 2: CONTINUOUS AGGREGATE — THE ECOSYSTEM DASHBOARD
--
-- WHY A CONTINUOUS AGGREGATE?
-- We need to know the total network profit, total water harvested, and average 
-- heat flux every hour to run our economic smart contracts. Querying millions 
-- of raw rows every time the dashboard refreshes is too slow. 
-- Continuous aggregates compute the math in the background incrementally.
-- =============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_ecosystem_yield
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time)                                 AS hour,
    participant_id,
    
    -- THERMODYNAMIC SAFETY
    -- If max_heat_flux exceeds 170 W/cm2, the HKUST-1 wick might fail (dry-out).
    MAX(heat_flux_w_cm2)                                        AS max_heat_flux,
    AVG(fluid_temp_c)                                           AS avg_fluid_temp,
    
    -- HYDROLOGICAL YIELD
    -- Total liters of pure water pulled from the atmosphere by this participant's heat.
    SUM(water_harvested_liters)                                 AS total_water_liters,
    
    -- ECONOMIC YIELD (TRIPLE BOTTOM LINE)
    SUM(compute_yield_usd)                                      AS total_compute_value,
    SUM(distributed_profit_usd)                                 AS total_profit_paid

FROM ecosystem_telemetry
GROUP BY hour, participant_id
WITH NO DATA;

-- Schedule the continuous aggregate to refresh every 30 minutes in the background
SELECT add_continuous_aggregate_policy(
    'hourly_ecosystem_yield',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists     => TRUE
);

-- =============================================================================
-- SECTION 3: ANOMALY DETECTION QUERY (ECONOMIC & THERMAL)
--
-- WHY THIS QUERY IS IMPORTANT:
-- This query acts as the safety check for both the physics and the economics.
-- It flags an anomaly if:
--   1. Thermodynamics fail: Heat flux is too close to the 170 W/cm2 CHF limit.
--   2. Economics fail: Compute value is generated, but profit wasn't distributed.
-- =============================================================================

WITH 
recent_data AS (
    SELECT
        node_id,
        participant_id,
        heat_flux_w_cm2,
        compute_yield_usd,
        distributed_profit_usd
    FROM ecosystem_telemetry
    WHERE time >= NOW() - INTERVAL '1 hour'
)
SELECT
    node_id,
    participant_id,
    heat_flux_w_cm2,
    compute_yield_usd,
    distributed_profit_usd,
    CASE
        -- Physics failure: We are approaching HKUST-1 dry-out limits!
        WHEN heat_flux_w_cm2 > 165.0 THEN 'CRITICAL: CHF IMMINENT'
        
        -- Economic failure: Work was done, but the smart contract didn't pay out!
        WHEN compute_yield_usd > 0 AND distributed_profit_usd = 0 THEN 'CRITICAL: PROFIT DISTRIBUTION FAILURE'
        
        ELSE 'NORMAL'
    END AS ecosystem_status
FROM recent_data
WHERE heat_flux_w_cm2 > 165.0 OR (compute_yield_usd > 0 AND distributed_profit_usd = 0);

-- =============================================================================
-- SECTION 4: SAMPLE DATA INSERTION (PROOF OF CONCEPT)
-- Let's insert a fake row to prove the database schema works.
-- Note the heat flux is safe (140.5 W/cm2) and profit is distributed.
-- =============================================================================
INSERT INTO ecosystem_telemetry (
    time, node_id, participant_id, heat_flux_w_cm2, fluid_temp_c, vapor_pressure_kpa, 
    water_harvested_liters, compute_yield_usd, distributed_profit_usd
) VALUES (
    NOW(), 'ImmersionPod_A01', 'Wallet_0x123', 140.5, 55.0, 101.3, 
    5.2, 120.00, 15.50
) ON CONFLICT DO NOTHING;
