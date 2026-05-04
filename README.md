# Adam Dryden — AI-Native OT / Ignition SCADA Portfolio

**Principal Industrial Software Engineer | SCADA · UNS · AI-Driven OT**

This portfolio demonstrates end-to-end capability across the modern industrial software
stack: from PLC edge networks and Ignition SCADA configuration, through distributed
MQTT/UNS architectures, to AI-native anomaly detection that bridges the OT/IT divide.

Each project is production-representative, targeting real-world use cases in
manufacturing, oil & gas, and hyperscale data center operations.

---

## Author Positioning

I design and deliver industrial automation systems where **operational technology (OT)
meets artificial intelligence**. My work sits at the intersection of:

- **Ignition SCADA** (Inductive Automation) — Perspective screens, UDTs, Transaction
  Groups, Named Queries, Tag Change scripts, Gateway scripting
- **Unified Namespace (UNS)** — MQTT Sparkplug B, ISA-95 equipment hierarchy, broker
  topology, Cirrus Link MQTT Engine
- **AI-Native OT** — FastAPI anomaly agents, LLM operator interfaces, MCP-over-HTTP
  patterns for safe AI-to-field-device integration
- **Industrial time-series databases** — PostgreSQL, TimescaleDB, OEE analytics,
  continuous aggregates, hypertables

This stack positions me for **remote-first Systems Integrator (SI) roles** at companies
building Ignition-based solutions for manufacturing, oil & gas, utilities, and data
centers — as well as industrial AI startups adding intelligence layers to existing OT
infrastructure.

---

## Project Overview

| # | Project | Domain | Key Technologies | Core Concepts |
|---|---------|--------|-----------------|---------------|
| **1** | [Manufacturing OEE Analytics](#project-1--manufacturing-oee-analytics) | Discrete Manufacturing | Ignition 8.1, PostgreSQL, ISA-95 | OEE = A × P × Q, UDT design, Transaction Groups, Named Queries, downtime categorization |
| **2** | [Oil & Gas UNS / Sparkplug B](#project-2--oil--gas-uns--sparkplug-b) | O&G Field Operations | MQTT, Sparkplug B, Eclipse Mosquitto, Cirrus Link | UNS hierarchy, edge telemetry simulation, NBIRTH/NDEATH lifecycle, compression ratio tags |
| **3** | [AI Data Center Capstone](#project-3--ai-native-data-center-capstone) | Data Center / Hyperscale | FastAPI, TimescaleDB, scikit-learn, MQTT, Docker | PUE time-series, Isolation Forest anomaly detection, MCP tool pattern, AI-native OT architecture |

---

## Architecture Progression

The three projects tell a deliberate story of increasing architectural sophistication:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Project 1 — Core SCADA (Ignition Fundamentals)                            │
│                                                                             │
│  PLC/Sensor → OPC-UA → Ignition Tag Engine → UDT → Transaction Group       │
│              → PostgreSQL → Named Query → Perspective OEE Dashboard         │
│                                                                             │
│  Focus: ISA-95 data modeling, OEE SQL analytics, Ignition Designer basics   │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Project 2 — Distributed MQTT / Unified Namespace                          │
│                                                                             │
│  Field RTU → Sparkplug B MQTT → Mosquitto Broker → Cirrus Link MQTT Engine  │
│           → Ignition Tag Browser (auto-populated) → Derived Tags            │
│                                                                             │
│  Focus: Cellular-resilient edge telemetry, UNS hierarchy design,            │
│         NBIRTH/NDEATH lifecycle, report-by-exception bandwidth savings       │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Project 3 — AI-Native OT Capstone                                         │
│                                                                             │
│  Edge Sensors → MQTT → FastAPI Anomaly Agent (Isolation Forest)             │
│                      → TimescaleDB (PUE hypertable + continuous aggregates) │
│                      → Ignition (HTTP polling tag change script)             │
│                      → LLM Operator Interface (MCP-over-HTTP)               │
│                                                                             │
│  Focus: Production Python, AI in OT, safe LLM-to-field-device patterns,    │
│         time-series analytics, containerized microservice architecture       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Project 1 — Manufacturing OEE Analytics

**Directory:** `01-manufacturing-oee/`

**The Problem:** A bottling line operator needs real-time Overall Equipment
Effectiveness (OEE) visibility and a structured way to log downtime so that
recurring failure modes can be identified and addressed.

**The Solution:**

- **PostgreSQL schema** aligned to the ISA-95 equipment hierarchy
  (Enterprise → Site → Area → Work Center → Work Unit), with tables for
  `production_runs`, `downtime_events`, and `equipment_hierarchy`
- **Advanced SQL** using CTEs and window functions to calculate OEE components
  (Availability, Performance, Quality), a rolling 7-day trend, downtime
  categorized to ISA-95 codes, and a bottleneck analysis query
- **Ignition UDT** (`BottlingLine_Unit`) with standard OEE tags wired to
  a Transaction Group that writes production counts to PostgreSQL every scan
- **Perspective downtime entry UI** — a modal form with ISA-95 category
  dropdown that calls a Named Query on submit

**Key Concepts:** OEE = A × P × Q, ISA-95 equipment model, Ignition Transaction
Groups, Named Queries, Perspective modal forms, PostgreSQL CTE/window functions

---

## Project 2 — Oil & Gas UNS / Sparkplug B

**Directory:** `02-oil-gas-uns/`

**The Problem:** An oil & gas operator has compressor stations at remote field
sites connected only via cellular. Modbus polling over cellular is unreliable
and expensive. A modern UNS architecture is needed.

**The Solution:**

- **Eclipse Mosquitto** MQTT broker via Docker Compose with correct port
  bindings (1883 MQTT, 9001 WebSocket) and persistence configuration
- **Sparkplug B edge simulator** (`edge_compressor_sim.py`) that publishes
  realistic compressor telemetry (suction/discharge pressure, vibration,
  motor temperature, RPM, run hours, status) with Gaussian noise, proper
  NBIRTH/NDEATH lifecycle, and Last Will and Testament
- **Ignition MQTT Engine** integration guide covering Server Set configuration,
  Sparkplug B tag auto-population, and derived tag creation for compression ratio
- **UNS hierarchy** mapped to the ISA-95 model for this O&G use case

**Key Concepts:** Unified Namespace, Sparkplug B, NBIRTH/NDEATH, report-by-exception,
cellular resilience, store-and-forward, Cirrus Link MQTT Engine, compression ratio

---

## Project 3 — AI-Native Data Center Capstone

**Directory:** `03-data-center-capstone/`

**The Problem:** A data center operator needs sub-minute thermal anomaly detection
across hundreds of server racks, with a natural-language interface for operators
and integration into existing Ignition SCADA infrastructure.

**The Solution:**

- **TimescaleDB** hypertable schema for facility power data, with PUE calculation
  via `time_bucket()`, a continuous aggregate materialized view for hourly PUE,
  and an anomaly query for PUE > 1.5 events
- **FastAPI anomaly agent** that subscribes to MQTT rack telemetry, maintains a
  circular buffer per rack, runs both threshold detection (ASHRAE limits) and
  Isolation Forest statistical detection, and exposes REST endpoints
- **MCP tool pattern** — each REST endpoint is documented as an MCP tool so an
  LLM operator interface can ask "are there anomalies in rack 3?" and get a
  structured, safe answer without direct PLC access
- **Ignition integration** via `system.net.httpClient()` tag change script that
  polls the FastAPI `/status` endpoint and writes results into memory tags
- **Full Docker Compose stack**: TimescaleDB + Mosquitto + FastAPI service

**Key Concepts:** PUE (Power Usage Effectiveness), Isolation Forest, MCP-over-HTTP
safety pattern, TimescaleDB continuous aggregates, AI-native OT, CRAC/rack UDTs

---

## Skills Demonstrated

**Ignition Designer & Gateway**
- UDT (User Defined Type) authoring with standard OEE and thermal tags
- Tag Change Scripts and Gateway Event Scripts (Jython / Python 3)
- Transaction Groups for historian writes
- Named Queries with parameterized inputs
- Perspective component binding and modal form design
- `system.net.httpClient()` for REST integration

**Industrial SQL & Time-Series**
- ISA-95-aligned schema design
- OEE calculation via CTEs and window functions
- TimescaleDB hypertables, `time_bucket()`, continuous aggregates
- Anomaly detection queries, retention policies

**MQTT / Sparkplug B / UNS**
- Edge node simulation (NBIRTH, NDATA, NDEATH lifecycle)
- Last Will and Testament configuration
- Eclipse Mosquitto broker setup
- Cirrus Link MQTT Engine module configuration
- Report-by-exception and QoS selection rationale

**Production Python**
- PEP 8, comprehensive docstrings, type annotations
- FastAPI with Pydantic models
- paho-mqtt (background thread subscriber)
- scikit-learn Isolation Forest
- asyncpg / TimescaleDB integration
- Threading and in-memory state management

**Docker & Infrastructure**
- Multi-service Docker Compose
- Named volumes, healthchecks, environment variables
- Dockerfile for Python microservices

**AI-Native OT Architecture**
- MCP tool design for LLM-to-OT interfaces
- Safe AI integration patterns (read-only by default, explicit acknowledgement writes)
- Isolation Forest for unsupervised anomaly detection
- Operator-facing natural language query patterns

---

## Target Roles

This portfolio is designed for evaluation by:

- **Systems Integrators (SIs)** building Ignition-based solutions for
  manufacturing, oil & gas, water/wastewater, and utilities — companies
  like Grantek, Woolpert, DMC, Vertech, Burns & McDonnell, and others
  in the Inductive Automation integrator network
- **Industrial AI startups** adding AI layers to existing OT infrastructure
  (anomaly detection, predictive maintenance, operator copilots)
- **End-user companies** seeking a full-stack OT engineer who can own the
  entire pipeline from PLC edge network through SCADA to AI-enhanced dashboards

---

## Repository Structure

```
adam-ignition-portfolio/
├── README.md                                   ← this file
│
├── 01-manufacturing-oee/
│   ├── README.md                               ← project overview, ISA-95 mapping, integration notes
│   ├── downtime_queries.sql                    ← PostgreSQL OEE CTEs, window functions, bottleneck analysis
│   └── Designer_Tutorial_OEE.md               ← step-by-step Ignition Designer click paths
│
├── 02-oil-gas-uns/
│   ├── README.md                               ← UNS hierarchy, Sparkplug B rationale, resilience
│   ├── docker-compose.yml                      ← Eclipse Mosquitto broker
│   ├── mosquitto/
│   │   └── mosquitto.conf                      ← broker configuration (listeners, persistence, auth)
│   ├── edge_compressor_sim.py                  ← Sparkplug B simulator (paho-mqtt, Gaussian noise)
│   └── Designer_Tutorial_UNS.md               ← MQTT Engine setup, Sparkplug B tag population
│
└── 03-data-center-capstone/
    ├── README.md                               ← architecture overview, MCP pattern rationale
    ├── docker-compose.yml                      ← TimescaleDB + Mosquitto + FastAPI stack
    ├── Dockerfile                              ← FastAPI anomaly agent container
    ├── PUE_metrics.sql                         ← TimescaleDB hypertable, PUE aggregates, anomaly queries
    ├── ai_anomaly_agent.py                     ← FastAPI + MQTT + Isolation Forest service
    └── Designer_Tutorial_DataCenter.md         ← CRAC/rack UDTs, HTTP polling script, heat map dashboard
```

---

*Contact: Adam Dryden — adryden508@gmail.com*
