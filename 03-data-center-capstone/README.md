# Project 3 — AI-Native Data Center Capstone

**Domain:** Hyperscale Data Center Operations
**Stack:** FastAPI, TimescaleDB, scikit-learn, MQTT, Sparkplug B, Docker, Ignition 8.1

---

## Full Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        DATA CENTER EDGE LAYER                               │
│                                                                              │
│  Server Rack A01     Server Rack A02     CRAC Unit 1    UPS / PDU           │
│  [InletTemp sensor]  [InletTemp sensor]  [SupplyTemp]   [TotalLoad_kW]      │
│       │                    │                 │                │              │
│       └──────┬─────────────┘                 └────────────────┘             │
│              │  Sparkplug B MQTT edge nodes                                  │
│              │  Topic: spBv1.0/DataCenter/NDATA/{rack_id}                   │
└──────────────┼───────────────────────────────────────────────────────────────┘
               │
               ▼ MQTT (port 1883)
┌──────────────────────────────────────────────────────────────────────────────┐
│                     Eclipse Mosquitto MQTT Broker                            │
│                     (Docker container)                                       │
└──────────────┬───────────────────────────────────────────────────────────────┘
               │
        ┌──────┴──────────────────────┐
        │                             │
        ▼                             ▼
┌────────────────────┐   ┌──────────────────────────────────────────────────┐
│  Ignition MQTT     │   │          FastAPI Anomaly Agent                    │
│  Engine module     │   │          (Python microservice, Docker)            │
│                    │   │                                                    │
│  Auto-creates tags │   │  ┌─────────────────────────────────────────────┐ │
│  in Tag Browser    │   │  │ MQTT Subscriber (background thread)         │ │
│  for all racks     │   │  │  - Circular buffer: 100 readings/rack       │ │
│                    │   │  │  - Threshold detection (ASHRAE limits)      │ │
│  HTTP polling via  │   │  │  - Isolation Forest (after 50 samples)      │ │
│  tag change script │   │  └──────────────────┬──────────────────────────┘ │
│  → writes JSON to  │   │                     │                             │
│    memory tags     │   │  ┌──────────────────▼──────────────────────────┐ │
│                    │   │  │ REST API (MCP Tools for LLM interface)       │ │
└────────────────────┘   │  │  GET  /status          — facility overview   │ │
        │                │  │  GET  /anomalies        — active alerts      │ │
        │                │  │  GET  /rack/{rack_id}   — rack detail        │ │
        │                │  │  POST /acknowledge/{id} — ack anomaly        │ │
        │                │  └──────────────────┬──────────────────────────┘ │
        │                │                     │                             │
        │                │  ┌──────────────────▼──────────────────────────┐ │
        │                │  │ TimescaleDB (facility_power hypertable)      │ │
        │                │  │  - PUE calculation via time_bucket()        │ │
        │                │  │  - Continuous aggregate: hourly PUE         │ │
        │                │  │  - Anomaly query: PUE > 1.5 last 24h       │ │
        │                │  └──────────────────────────────────────────────┘ │
        │                └──────────────────────────────────────────────────┘
        │                             │
        ▼                             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    OPERATOR INTERFACES                                       │
│                                                                              │
│  Ignition Perspective        LLM Operator Interface (MCP over HTTP)         │
│  ─────────────────────        ─────────────────────────────────────────     │
│  Heat map dashboard           "Are there any thermal anomalies?"             │
│  Rack temperature grid        "What's the status of rack A01?"               │
│  CRAC/UPS status              "Acknowledge anomaly [ID]"                     │
│  PUE trend chart                                                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Natural Language Operator Query Flow

An operator (or LLM acting on the operator's behalf) can ask natural language
questions that map directly to the FastAPI MCP tool endpoints:

**Q: "Are there any thermal anomalies in the data center right now?"**
```
LLM calls → GET /anomalies
Response:  [
  { rack_id: "Rack_A01", metric: "InletTemp_C", value: 28.3,
    severity: "critical", method: "threshold", detected_at: "..." },
  { rack_id: "Rack_B07", metric: "MultivariateThermal", value: 26.1,
    severity: "warning", method: "isolation_forest", detected_at: "..." }
]
LLM answers: "Yes — Rack A01 has a critical inlet temperature of 28.3°C
              (above the 27°C ASHRAE limit). Rack B07 has a statistical
              anomaly detected by the Isolation Forest model."
```

**Q: "What is the status of rack A03?"**
```
LLM calls → GET /rack/Rack_A03
Response:  {
  rack_id: "Rack_A03",
  latest_metrics: { inlet_temp_c: 23.1, exit_temp_c: 38.4, power_draw_kw: 12.3 },
  status: "normal",
  if_fitted: true,
  active_anomalies: []
}
LLM answers: "Rack A03 is normal. Inlet temperature 23.1°C, exit 38.4°C,
              drawing 12.3 kW. No active anomalies."
```

**Q: "Acknowledge the anomaly in rack A01."**
```
LLM extracts anomaly_id from earlier /anomalies call
LLM calls → POST /acknowledge/{anomaly_id}
Response:  { acknowledged: true, message: "Anomaly ... acknowledged." }
LLM answers: "Done. The rack A01 inlet temperature anomaly has been
              acknowledged. Monitoring continues."
```

---

## Why MCP-Over-HTTP Is Safer Than Direct LLM-to-PLC

This is the most important architectural principle in this project.

**The naive approach (dangerous):**
```
LLM → Modbus write → PLC → physical actuator
```
An LLM hallucinating a setpoint change or misinterpreting a query could
cause real equipment damage. Language models are not deterministic, not
auditable, and not safety-rated for direct field device control.

**The MCP-over-HTTP pattern (safe):**
```
LLM → HTTP REST API → validated agent → read-only OT data
                   ↘ POST /acknowledge → human-initiated, not autonomous
```

Safety properties of this pattern:
1. **Read-only by default.** The LLM can see all sensor data but cannot change
   setpoints, toggle relays, or send commands. Zero write surface area.
2. **Explicit acknowledgement only.** The single write endpoint (`/acknowledge`)
   merely marks an alarm as seen — it does not change any physical system state.
3. **No PLCs in scope.** The LLM talks to a Python microservice that aggregates
   data. PLC writes happen only through Ignition's traditional operator screens,
   protected by authentication and role-based access control.
4. **Validated, typed responses.** Pydantic models ensure the LLM receives
   well-structured JSON, not raw register dumps it might misinterpret.
5. **Auditability.** Every API call is logged with timestamp and parameters.
   Traditional PLC access logs don't capture LLM context; this pattern does.

---

## Isolation Forest: The Statistics Behind the Detection

**Why not just use more thresholds?**
Thresholds catch obvious overtemperature. But consider this scenario:
- InletTemp_C = 24°C (normal, below 27°C threshold — no alert)
- ExitTemp_C = 36°C (normal, below 45°C threshold — no alert)
- PowerDraw_kW = 18 kW (normal for this rack — no alert)

But: at this rack's typical load, these temperatures suggest the delta-T
(exit − inlet = 12°C) is higher than usual, indicating reduced airflow. A pure
threshold approach misses this. The Isolation Forest detects it because the
*combination* of three features is statistically anomalous relative to history.

**How Isolation Forest works (in 60 seconds):**
- Randomly selects a feature and a split point within its range
- Recursively partitions the feature space like a random decision tree
- An outlier (anomaly) is isolated in fewer partitions than a normal point
- Anomaly score = average depth across 100 trees; shallower = more anomalous
- sklearn's `contamination=0.05` sets the decision boundary at the 5th percentile
  of scores from the training set (assumes ~5% of historical data was anomalous)

**Feature vector:** `[InletTemp_C, ExitTemp_C, PowerDraw_kW]`
Multivariate: captures cross-metric patterns that threshold detection misses.

---

## PUE: The Standard Data Center Efficiency Metric

**PUE = Total Facility Power / IT Equipment Power**

The Green Grid (https://www.thegreengrid.org) defined this metric in 2007. It is
now the universal data center efficiency benchmark.

| PUE Value | Rating |
|-----------|--------|
| 1.0 | Theoretical perfect (impossible: some overhead always exists) |
| 1.1–1.2 | Excellent (hyperscale: Google ~1.10, Meta ~1.09) |
| 1.2–1.4 | Good (modern enterprise DC) |
| 1.4–1.6 | Average (typical enterprise) |
| > 1.6 | Poor (legacy air-cooled, old UPS) |

**What PUE > 1.5 means practically:** For every 1 kW of IT work, 0.5+ kW is spent
on cooling, power distribution losses, and lighting. An anomaly query that surfaces
these events enables operations to investigate cooling setpoints, check for CRAC
failures, and drive toward efficiency targets.

**TimescaleDB advantage:** The `time_bucket()` function and continuous aggregates
allow PUE to be computed over billions of raw power readings without full table
scans. The continuous aggregate materializes hourly PUE in background jobs,
so dashboard queries hit pre-computed results rather than raw time-series data.

---

## What This Demonstrates to an SI Interviewer

- **Production Python architecture:** FastAPI, Pydantic, background MQTT thread,
  Isolation Forest, in-memory state with thread locks — not just scripts
- **AI-native OT thinking:** You understand where AI belongs in the OT stack
  (at the informational layer, not the control layer) and why
- **Time-series expertise:** TimescaleDB hypertables, `time_bucket()`, continuous
  aggregates, retention policies — the tools used in real industrial data infrastructure
- **MCP design pattern:** Documenting REST endpoints as LLM tools with structured
  inputs/outputs — an emerging pattern in industrial AI integration
- **Data center domain:** PUE, ASHRAE thermal guidelines, rack power density,
  CRAC/CRAH systems — shows cross-domain fluency beyond traditional manufacturing

---

## Files in This Directory

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Full stack: TimescaleDB + Mosquitto + FastAPI service |
| `Dockerfile` | FastAPI anomaly agent container build |
| `PUE_metrics.sql` | TimescaleDB hypertable, PUE aggregation, anomaly queries |
| `ai_anomaly_agent.py` | FastAPI service: MQTT subscriber + Isolation Forest + REST API |
| `Designer_Tutorial_DataCenter.md` | Ignition step-by-step: CRAC UDT, rack UDT, HTTP polling script |
| `README.md` | This file |

## Quick Start

```bash
# Start the full stack
docker compose up -d

# Verify services
docker compose ps

# Run database schema
docker exec -i timescaledb psql -U postgres -d datacenter \
  < PUE_metrics.sql

# Install Python dependencies (for local dev, outside Docker)
pip install fastapi uvicorn paho-mqtt scikit-learn numpy asyncpg python-dotenv

# Run the anomaly agent locally (or use Docker Compose)
python ai_anomaly_agent.py

# View API docs
open http://localhost:8000/docs
```
