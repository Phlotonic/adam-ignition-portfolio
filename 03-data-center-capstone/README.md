# Project 3 — Triple-Bottom-Line Regenerative Data Center Capstone

**Domain:** Hyperscale Regenerative Ecosystems (Power, Compute, and Water Harvesting)
**Stack:** FastAPI, TimescaleDB, scikit-learn, MQTT, Sparkplug B, Docker

---

## 🌍 The Vision: Beyond Air Cooling
Traditional data centers are environmental liabilities, consuming massive amounts of municipal water and electricity while outputting nothing but waste heat. 

This capstone completely fundamentally re-engineers the data center into a **net-positive, structurally regenerative ecosystem**. 

By applying Reticular Chemistry (Metal-Organic Frameworks or MOFs) directly to the server hardware, we overcome thermal limits while simultaneously harvesting water from the atmosphere and capturing carbon. 

### Core Milestones:
1. **HKUST-1 Two-Phase Immersion Cooling:** We coat copper heat sinks with superhydrophilic HKUST-1 MOFs, pushing the Critical Heat Flux (CHF) limit to ~170 W/cm² to prevent vapor dry-out, enabling ultra-dense compute.
2. **MOF-303 Atmospheric Water Generation (AWG):** We route the 60°C liquid waste heat through MOF-303 desiccants to thermally desorb harvested atmospheric water. The facility acts as a net-positive water producer.
3. **Triple-Bottom-Line Economics:** The excess compute density generates profit, which is tracked and dynamically distributed to ecosystem participants, creating a network effect.

---

## 🏗️ Full Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        DATA CENTER EDGE LAYER                               │
│                                                                              │
│  Immersion Pod A01   Immersion Pod A02   MOF-303 AWG 1    Power / Plant     │
│  [HeatFlux_W_cm2]    [HeatFlux_W_cm2]    [WaterHarvested] [TotalLoad_kW]    │
│  [FluidTemp_C]       [FluidTemp_C]                                          │
│       │                    │                 │                │              │
│       └──────┬─────────────┘                 └────────────────┘             │
│              │  Sparkplug B MQTT edge nodes                                  │
│              │  Topic: spBv1.0/DataCenter/NDATA/Pod_A01                     │
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
│  Ignition MQTT     │   │          FastAPI Triple-Bottom-Line Agent         │
│  Engine module     │   │          (Python microservice, Docker)            │
│                    │   │                                                    │
│  Auto-creates tags │   │  ┌─────────────────────────────────────────────┐ │
│  in Tag Browser    │   │  │ MQTT Subscriber (background thread)         │ │
│  for all pods      │   │  │  - Circular buffer: 100 readings/pod        │ │
│                    │   │  │  - Physics thresholds (CHF, Fluid Temp)     │ │
│  HTTP polling via  │   │  │  - Economic SLAs (Unsettled balances)       │ │
│  tag change script │   │  │  - Dual Isolation Forests (Physics + Econ)  │ │
│  → writes JSON to  │   │  └──────────────────┬──────────────────────────┘ │
│    memory tags     │   │                     │                             │
│                    │   │  ┌──────────────────▼──────────────────────────┐ │
│                    │   │  │ REST API (MCP Tools for LLM interface)       │ │
│                    │   │  │  GET  /status          — facility overview   │ │
│                    │   │  │  GET  /anomalies        — active alerts      │ │
│                    │   │  │  POST /acknowledge/{id} — ack anomaly        │ │
│                    │   │  └──────────────────┬──────────────────────────┘ │
│                    │   │                     │                             │
│                    │   │  ┌──────────────────▼──────────────────────────┐ │
│                    │   │  │ TimescaleDB (ecosystem_telemetry hypertable) │ │
│                    │   │  │  - Profit distribution ledgers               │ │
│                    │   │  │  - Continuous aggregate: hourly yields       │ │
│                    │   │  │  - Anomaly queries                           │ │
│                    │   │  └──────────────────────────────────────────────┘ │
│                    │   └──────────────────────────────────────────────────┘
└────────────────────┘
```

---

## 🧠 Ensemble Machine Learning: Dual Isolation Forests

To accurately detect anomalies in a hybrid ecosystem, we must account for the **Temporal Impedance Mismatch**. 
Mixing continuous thermodynamic physics (which occur in seconds) with discrete economic smart contracts (which settle in hours) into a single machine learning model ruins the clustering geometry of an Isolation Forest.

We solved this by running an **Ensemble Model**:
1. **The Physical AI:** Ingests `[heat_flux_w_cm2, fluid_temp_c, vapor_pressure_kpa, water_harvested_liters]`. Exclusively monitors the physical health of the MOF and the HKUST-1 wick.
2. **The Economic AI:** Ingests `[compute_yield_usd, unsettled_balance_usd]`. Exclusively monitors the health of the smart contract and the profit distribution.

If the physical AI says everything is normal, but the water harvesting drops, the AI detects the decoupling of the ecosystem (e.g., MOF-303 desiccant fouling). 

---

## 🔒 Why MCP-Over-HTTP Is Safer Than Direct LLM-to-PLC

This is the most important architectural principle when combining Generative AI with Operational Technology (OT).

**The naive approach (dangerous):**
```
LLM → Modbus write → PLC → physical actuator
```
An LLM hallucinating a setpoint change could cause real equipment damage, specifically in highly volatile two-phase immersion tanks.

**The MCP-over-HTTP pattern (safe):**
```
LLM → HTTP REST API → validated agent → read-only OT data
                   ↘ POST /acknowledge → human-initiated, not autonomous
```

1. **Read-only by default.** The LLM can see all sensor data but cannot change setpoints.
2. **Validated, typed responses.** Pydantic models ensure the LLM receives well-structured JSON, not raw register dumps.

---

## 🚀 Quick Start

```bash
# Start the full stack
docker compose up -d

# Verify services
docker compose ps

# Run database schema
docker exec -i timescaledb psql -U postgres -d datacenter < PUE_metrics.sql

# Install Python dependencies (for local dev, outside Docker)
pip install fastapi uvicorn paho-mqtt scikit-learn numpy asyncpg python-dotenv

# Run the anomaly agent locally
python ai_anomaly_agent.py

# View API docs
open http://localhost:8000/docs
```
