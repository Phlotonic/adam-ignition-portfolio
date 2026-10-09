# Project 3 — Triple-Bottom-Line Regenerative Data Center Capstone

**Domain:** Hyperscale Regenerative Ecosystems (Power, Compute, Water Harvesting, & SCADA)  
**Stack:** FastAPI, TimescaleDB, scikit-learn, Eclipse Mosquitto (MQTT / Sparkplug B), Docker Compose, Ignition 8.3 / 8.1

---

## 🌍 The Vision: Beyond Air Cooling
Traditional data centers are environmental liabilities, consuming massive amounts of municipal water and electricity while outputting nothing but waste heat. 

This capstone fundamentally re-engineers the data center into a **net-positive, structurally regenerative ecosystem**. 

By applying Reticular Chemistry (Metal-Organic Frameworks or MOFs) directly to server hardware, we overcome thermal limits while simultaneously harvesting water from the atmosphere and dynamically rewarding network participants.

### Core Architecture Highlights:
1. **HKUST-1 Two-Phase Immersion Cooling:** Superhydrophilic HKUST-1 MOF films coated on copper meshes elevate the Critical Heat Flux (CHF) limit from ~55 W/cm² to ~170 W/cm², preventing vapor dry-out and enabling extreme compute densities.
2. **MOF-303 Atmospheric Water Generation (AWG):** 60°C dielectric liquid waste heat thermally desorbs water vapor trapped by MOF-303 desiccants, producing pure distilled water without municipal hookups.
3. **Triple-Bottom-Line Economics:** Compute value ($/FLOP) is tracked in real-time alongside physical thermal flux, with automated smart-contract payout tracking and SLA anomaly alerting.
4. **Ignition 8.3 Configuration-as-Code:** SCADA configurations (UDTs, Event Streams, and Perspective Native SVG Views) are version-controlled as human-readable JSON files in `ignition-config/`.

---

## 🏗️ Full Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        DATA CENTER EDGE LAYER                               │
│                                                                              │
│  Immersion Pod A01   Immersion Pod A02   MOF-303 AWG 1    Dielectric Pump 1  │
│  [HeatFlux_W_cm2]    [HeatFlux_W_cm2]    [WaterHarvested] [FlowRate_LPM]     │
│  [FluidTemp_C]       [FluidTemp_C]       [DesorptionTemp] [Speed_Hz]         │
│       │                    │                 │                │              │
│       └──────┬─────────────┘                 └────────────────┘              │
│              │  Sparkplug B / MQTT Telemetry (5s interval)                   │
│              │  Topic: spBv1.0/DataCenter/NDATA/{pod_id}                     │
└──────────────┼───────────────────────────────────────────────────────────────┘
               │
               ▼ MQTT (port 1883)
┌──────────────────────────────────────────────────────────────────────────────┐
│                     Eclipse Mosquitto MQTT Broker                            │
│                     (Docker container: mosquitto)                            │
└──────────────┬───────────────────────────────────────────────────────────────┘
               │
        ┌──────┴────────────────────────────────────────────────┐
        │                                                       │
        ▼                                                       ▼
┌──────────────────────────────────┐   ┌───────────────────────────────────────────────┐
│  Ignition 8.3 SCADA Gateway      │   │  FastAPI Triple-Bottom-Line Agent             │
│  (Config-as-Code in Git)         │   │  (Python microservice: anomaly-agent)         │
│                                  │   │                                               │
│  1. Reference Tag UDTs           │   │  ┌──────────────────────────────────────────┐ │
│     - Immersion_Tank             │   │  │ MQTT Subscriber (Background Thread)      │ │
│     - Dielectric_Pump            │   │  │  - Circular buffer: 100 samples/pod      │ │
│     - Decouples alarms/history   │   │  │  - Auto-reconnect with backoff           │ │
│       from transient MQTT tags   │   │  └──────────────────┬───────────────────────┘ │
│                                  │   │                     │                         │
│  2. Event Streams Pipeline       │   │  ┌──────────────────▼───────────────────────┐ │
│     - Ingests alerts via Webhook │   │  │ Ensemble Anomaly Engine                  │ │
│     - Routes to TimescaleDB      │   │  │  - Thresholds: CHF > 165, SLA > $1000   │ │
│       Alarm Journal              │   │  │  - Physical Isolation Forest (4D)        │ │
│                                  │   │  │  - Economic Isolation Forest (2D)        │ │
│  3. Perspective Native Graphics  │   │  └──────────────────┬───────────────────────┘ │
│     - Native SVG shapes          │   │                     │                         │
│     - String tagPath param       │   │  ┌──────────────────▼───────────────────────┐ │
│     - Dynamic boiling animations │   │  │ Asyncpg TimescaleDB Ingest Worker        │ │
│                                  │   │  │  - Non-blocking asyncio.Queue            │ │
│                                  │   │  │  - Bulk writes to ecosystem_telemetry    │ │
│                                  │   │  └──────────────────┬───────────────────────┘ │
│                                  │   │                     │                         │
│                                  │   │  ┌──────────────────▼───────────────────────┐ │
│                                  │   │  │ REST API (MCP-Over-HTTP Tools)           │ │
│                                  │   │  │  GET /health     GET /status             │ │
│                                  │   │  │  GET /anomalies  POST /acknowledge       │ │
│                                  │   │  └──────────────────────────────────────────┘ │
└──────────────────────────────────┘   └───────────────────────┬───────────────────────┘
                                                               │
                                                               ▼
                                       ┌───────────────────────────────────────────────┐
                                       │ TimescaleDB (PostgreSQL 15 + Extension)       │
                                       │  - Auto-provisioned via ./init-scripts/       │
                                       │  - Hypertable: ecosystem_telemetry (1d chunk) │
                                       │  - Continuous Aggregate: hourly yield         │
                                       │  - Retention Policy: 90 days automatic prune  │
                                       └───────────────────────────────────────────────┘
```

---

## 🧠 Ensemble Machine Learning: Dual Isolation Forests

To accurately detect anomalies in this hybrid ecosystem, we resolve the **Temporal Impedance Mismatch**:
- **Thermodynamic Physics:** Continuous, microsecond-to-second boiling and fluid dynamics.
- **Economic Settlements:** Discrete, step-wise blockchain / smart-contract settlement events occurring over hours.

Feeding both into a single unsupervised model causes financial steps to drown out subtle physical degradation (such as MOF desiccant fouling). 

We separate detection into two dedicated Isolation Forests:
1. **Physical AI:** Features: `[heat_flux_w_cm2, fluid_temp_c, vapor_pressure_kpa, water_harvested_liters]`.
2. **Economic AI:** Features: `[compute_yield_usd, unsettled_balance_usd]`.

---

## 📁 Repository & Directory Layout

| Path | Purpose |
|------|---------|
| `docker-compose.yml` | Full 4-service stack: TimescaleDB, Mosquitto, Anomaly Agent, Simulator |
| `Dockerfile` | Multi-stage production container for `ai_anomaly_agent.py` |
| `Dockerfile.simulator` | Container for continuous multi-pod telemetry simulation |
| `ai_anomaly_agent.py` | Complete FastAPI service with asyncpg ingest, dual ML models, and MCP tools |
| `mqtt_pod_simulator.py` | Synthetic telemetry generator with simulated CHF and SLA failure modes |
| `PUE_metrics.sql` | TimescaleDB hypertable, continuous aggregates, and retention policy (manual psql) |
| `init-scripts/01_ecosystem_telemetry.sql` | Auto-executed SQL script for initial container database provisioning |
| `requirements.txt` | Pinned Python dependencies (FastAPI, scikit-learn, asyncpg, paho-mqtt) |
| `ignition-config/tags/` | Ignition 8.3 UDTs (`Immersion_Tank`, `Dielectric_Pump`) with Reference tags |
| `ignition-config/event-streams/` | Ignition 8.3 Event Streams webhook anomaly ingestion pipeline |
| `ignition-config/perspective/views/` | Ignition 8.3 Native SVG Perspective View with string path parameter |
| `Designer_Tutorial_DataCenter.md` | Comprehensive step-by-step Ignition setup guide (8.3 native + 8.1 fallback) |

---

## 🚀 Quick Start

### 1. Launch the Stack
```bash
docker compose up -d
```
Docker Compose automatically brings up all four services:
- `timescaledb`: Automatically mounts `./init-scripts/` and provisions the hypertable.
- `mosquitto`: Starts the MQTT broker on ports 1883 and 9001.
- `pod-simulator`: Begins broadcasting multi-pod telemetry every 5 seconds.
- `anomaly-agent`: Ingests telemetry, fits Isolation Forests, writes to TimescaleDB, and exposes MCP endpoints.

### 2. Verify Services
```bash
docker compose ps
```

### 3. Query Real-Time Telemetry and Anomalies
```bash
# Check service health
curl http://localhost:8000/health

# View complete facility status
curl http://localhost:8000/status

# View active unacknowledged anomalies
curl http://localhost:8000/anomalies
```

### 4. Interactive OpenAPI Docs
Open `http://localhost:8000/docs` in your browser to inspect and interact with the MCP tool endpoints.
