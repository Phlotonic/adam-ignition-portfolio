# Project 2 — Oil & Gas Unified Namespace / Sparkplug B

**Domain:** Oil & Gas Field Operations (Remote Compressor Stations)
**Stack:** MQTT, Sparkplug B, Eclipse Mosquitto, Cirrus Link MQTT Engine, Python

---

## The Problem: Remote Field Sites on Cellular

An oil & gas operator runs 40+ compressor stations across a field. Each station
has a local RTU monitoring suction/discharge pressure, vibration, temperature,
RPM, and run hours. The challenge:

- **Distance**: Stations are 5–50 km from the nearest office
- **Connectivity**: Only cellular (LTE/LTE-M) is available; link quality is variable
- **Legacy approach**: Modbus TCP polling over cellular VPN — every 10 seconds,
  the SCADA server attempts to read 12 registers from 40 stations = 480 poll
  attempts/minute, most of which time out or are queued, creating data latency
  and an expensive per-connection VPN

The modern answer is **MQTT + Sparkplug B** — a publish-subscribe architecture
where the RTU is the initiator and the broker fans out data to all consumers.

---

## UNS Hierarchy for This Use Case

The Unified Namespace (UNS) is a conceptual architecture where all data in an
enterprise is organized in a single, coherent namespace — typically a MQTT topic
tree structured by the ISA-95 equipment hierarchy.

```
ISA-95 Level    │ Sparkplug B Mapping          │ This Project
────────────────┼──────────────────────────────┼───────────────────────────────
Enterprise (1)  │ Group ID                     │ OilGas_Field_A
Site (2)        │ (within Group or Edge Node)  │ implied by Group ID
Area (3)        │ Edge Node ID prefix          │ Compressor_Station_01
Work Cell (4/5) │ Device ID (Sparkplug B)      │ (not used; single device node)
────────────────┴──────────────────────────────┴───────────────────────────────

Topic structure:
  spBv1.0 / {group_id} / {msg_type} / {edge_node_id}

  spBv1.0/OilGas_Field_A/NBIRTH/Compressor_Station_01   ← announce + full metric list
  spBv1.0/OilGas_Field_A/NDATA/Compressor_Station_01    ← periodic telemetry updates
  spBv1.0/OilGas_Field_A/NDEATH/Compressor_Station_01   ← node went offline (LWT)
```

If the operator had devices attached to the edge node (sub-devices), they would
add a Device ID level:
```
  spBv1.0/OilGas_Field_A/DBIRTH/Compressor_Station_01/VibrationSensor_A
  spBv1.0/OilGas_Field_A/DDATA/Compressor_Station_01/VibrationSensor_A
```

This topic structure is itself the UNS — any system subscribing to
`spBv1.0/OilGas_Field_A/#` gets all telemetry for the entire field.

---

## Why MQTT + Sparkplug B Over Modbus or OPC-UA for Remote Sites?

### 1. Publish-Subscribe vs. Poll-Response

| Aspect | Modbus TCP (polling) | OPC-UA (subscription) | MQTT Sparkplug B |
|--------|---------------------|----------------------|------------------|
| **Initiator** | SCADA server (master) | SCADA server (after subscribe) | RTU (edge node) |
| **Cellular suitability** | Poor — server needs persistent connection to each RTU | Better — but complex firewall traversal | Excellent — RTU initiates outbound connection |
| **Bandwidth** | High — polls even when values haven't changed | Good — subscription model | Excellent — report-by-exception |
| **Multi-consumer** | One master only | Multiple clients possible | Any subscriber gets all data |
| **Broker fault tolerance** | N/A — direct connection | N/A | Broker HA, QoS levels, store-and-forward |
| **Data type metadata** | None — register interpretation is manual | Rich (OPC-UA data types) | Rich via NBIRTH metric descriptors |

### 2. Cellular-Specific Advantages

**Outbound connections only:** Cellular carriers use carrier-grade NAT (CGNAT),
making inbound connections to field RTUs impossible without a VPN. MQTT RTUs
establish an outbound connection to the broker — no VPN required.

**Store-and-Forward:** If the cellular link drops, the RTU buffers messages
locally and retransmits when connectivity is restored. With Modbus polling,
dropped connections mean data gaps with no recovery.

**QoS levels:** Sparkplug B uses MQTT QoS 1 for NBIRTH/NDEATH (at-least-once
delivery, broker acknowledgement required) and QoS 0 for high-frequency NDATA
(fire-and-forget, reduces overhead on constrained links).

**Report-by-Exception (RBE):** NDATA messages only need to carry metrics that
changed beyond a deadband. A compressor running steadily at 1780 RPM for 8 hours
sends zero RPM updates — versus Modbus polling 1 register every 10 seconds (48×
per hour, all redundant). On a 10kbps LTE-M link this is the difference between
viable and unusable.

---

## Sparkplug B Lifecycle: NBIRTH / NDATA / NDEATH

Sparkplug B defines a strict lifecycle that solves the "stale data" problem that
plagues traditional MQTT industrial deployments:

```
1. RTU powers on and connects to broker
         │
         ▼
2. RTU publishes NBIRTH (Node Birth)
   ┌─────────────────────────────────────────────────┐
   │ Topic: spBv1.0/{group}/{NBIRTH}/{node}           │
   │ Payload: all metrics with full descriptors       │
   │   - name, alias (integer), dataType              │
   │   - current value                                │
   │   - properties (engineering units, description)  │
   │ QoS: 1 (acknowledged)                           │
   └─────────────────────────────────────────────────┘
         │  Ignition MQTT Engine receives NBIRTH →
         │  creates tags in Tag Browser with correct types
         ▼
3. RTU publishes NDATA every N seconds
   ┌─────────────────────────────────────────────────┐
   │ Topic: spBv1.0/{group}/{NDATA}/{node}            │
   │ Payload: changed metrics only (RBE)              │
   │   - alias (integer, not full name — saves bytes) │
   │   - current value + timestamp                    │
   │ QoS: 0 (fire-and-forget for telemetry speed)    │
   └─────────────────────────────────────────────────┘
         │  Ignition tags update in real time
         ▼
4. RTU disconnects (graceful) or crashes (ungraceful)
   ┌─────────────────────────────────────────────────┐
   │ Graceful: RTU publishes NDEATH before disconnect │
   │ Ungraceful: Broker delivers LWT (pre-registered │
   │             NDEATH) automatically               │
   │                                                 │
   │ Effect in Ignition: all tags go STALE quality   │
   │ Alarms triggered, operator sees gray/yellow tags │
   └─────────────────────────────────────────────────┘
```

**Why does this matter?** Without NBIRTH/NDEATH, a consumer receiving NDATA has
no way to know if a silent period means "values unchanged (RBE)" or "node is
dead." Sparkplug B solves this definitively.

---

## How Ignition's Cirrus Link MQTT Engine Module Consumes This

The **Cirrus Link MQTT Engine** module (licensed add-on for Ignition) is the
bridge between Sparkplug B topics and the Ignition tag tree.

**What it does automatically:**
1. Subscribes to `spBv1.0/#` (all groups and nodes)
2. Parses NBIRTH payloads to build tag definitions
3. Creates a tag folder structure in the Tag Browser:
   `[MQTT Engine] / Sparkplug B Devices / OilGas_Field_A / Compressor_Station_01 / SuctionPressure_PSI`
4. Updates tag values on each NDATA message using the alias-to-name mapping
   built from NBIRTH
5. Sets tag quality to BAD/STALE when NDEATH is received

**What the SI configures:**
- Server Set: connection parameters for the Mosquitto broker
- Security: TLS certificates and username/password (production)
- Namespace: which Sparkplug B groups to subscribe to

See `Designer_Tutorial_UNS.md` for step-by-step click paths.

---

## Simulated Metrics and Operating Ranges

The `edge_compressor_sim.py` simulator publishes these metrics:

| Metric | Unit | Normal Range | Noise σ | Alert Threshold |
|--------|------|-------------|---------|----------------|
| `SuctionPressure_PSI` | PSI | 85–95 | ±0.8 | < 75 or > 105 |
| `DischargePressure_PSI` | PSI | 250–280 | ±2.0 | < 220 or > 310 |
| `VibrationX_mms` | mm/s | 1.5–3.5 | ±0.15 | > 7.1 (ISO 10816 limit) |
| `VibrationY_mms` | mm/s | 1.5–3.5 | ±0.15 | > 7.1 |
| `MotorTemp_C` | °C | 65–75 | ±0.5 | > 95 (Class F insulation limit) |
| `RPM` | RPM | 1750–1800 | ±5.0 | < 1700 or > 1850 |
| `RunHours` | hours | incrementing | — | maintenance trigger |
| `Status` | enum | 0 (Running) | — | 1 = Fault |

**Compression Ratio** (derived in Ignition): `DischargePressure_PSI / SuctionPressure_PSI`
Normal operating range: ~2.7–3.3. Values outside this range indicate valve wear,
inlet blockage, or process upset.

---

## What This Demonstrates to an SI Interviewer

- **MQTT architecture knowledge**: You understand the broker pattern, QoS trade-offs,
  topic design, and the difference between at-least-once (QoS 1) and fire-and-forget (QoS 0)
- **Sparkplug B fluency**: NBIRTH/NDEATH lifecycle, alias-based NDATA, Last Will and
  Testament — the constructs that make Sparkplug B production-safe vs. plain MQTT
- **UNS thinking**: Mapping ISA-95 hierarchy to topic namespaces, designing for
  multi-consumer data sharing rather than point-to-point integration
- **Cirrus Link MQTT Engine**: The commercial module used in 80%+ of Ignition MQTT
  deployments — knowing how to configure it is a direct job skill
- **Cellular/edge constraints**: Awareness of CGNAT, report-by-exception, bandwidth
  budgeting — the practical realities of remote O&G deployments
- **Production Python**: PEP 8 compliant simulator with real Gaussian noise modeling,
  fault injection, and proper MQTT lifecycle management

---

## Files in This Directory

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Eclipse Mosquitto broker (MQTT 1883, WebSocket 9001) |
| `mosquitto/mosquitto.conf` | Broker configuration file |
| `edge_compressor_sim.py` | Sparkplug B compressor station simulator |
| `Designer_Tutorial_UNS.md` | Ignition Designer step-by-step click paths |
| `README.md` | This file |

## Quick Start

```bash
# Start the MQTT broker
docker compose up -d

# Install Python dependency
pip install paho-mqtt

# Run the simulator
python edge_compressor_sim.py

# Verify messages are publishing
# Install mosquitto-clients, then:
mosquitto_sub -h localhost -p 1883 -t "spBv1.0/#" -v
```
