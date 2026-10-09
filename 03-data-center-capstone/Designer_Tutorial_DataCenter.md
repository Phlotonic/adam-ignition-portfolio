# Ignition Designer Tutorial — Regenerative Data Center Capstone

**Ignition Version:** 8.3 (Native SVG, Event Streams, Configuration-as-Code) with 8.1 Legacy Fallback  
**Estimated Time:** 45–60 minutes

This tutorial configures the Ignition SCADA frontend for the **Triple-Bottom-Line Liquid-Cooled Data Center**:
1. **Configuration-as-Code (Ignition 8.3):** Deploying Immersion Pod & Dielectric Pump UDTs using Reference tags.
2. **Event Streams:** Ingesting real-time anomaly alerts from the FastAPI agent via Webhook.
3. **Perspective Native Graphics:** Rendering dynamic two-phase boiling physics using native SVG drawing elements and string tag path parameterization.
4. **Legacy 8.1 Fallback:** Gateway Timer Script polling pattern.

---

## Part 1 — Configuration-as-Code & Reference Tag Pattern (Ignition 8.3)

In Ignition 8.3, configurations are stored directly in human-readable JSON files under the gateway's `data/config` filesystem, making them version-controllable via Git.

All ready-to-deploy JSON definitions are pre-built in the `ignition-config/` directory:

```
03-data-center-capstone/ignition-config/
├── tags/
│   ├── udt_immersion_tank.json      ← Immersion Pod UDT with Reference tags & CHF alarms
│   └── udt_dielectric_pump.json     ← Coolant pump UDT with flow/speed tags
├── event-streams/
│   └── anomaly_pipeline.json       ← Event Streams webhook & alarm routing pipeline
└── perspective/
    └── views/
        └── CoolingLoop/
            └── view.json            ← Native SVG Perspective view with string parameterization
```

### 1.1 The Reference Tag Best Practice

When using Cirrus Link MQTT Engine with Sparkplug B, telemetry metrics appear dynamically under `[MQTT Engine]Edge Nodes/DataCenter/...`. 

> [!IMPORTANT]
> **Why Reference Tags are required:**  
> Configuring alarms or historian logging directly on auto-generated MQTT Engine tags is dangerous. If an edge node drops offline or the broker restarts, transient Sparkplug tags can be recreated, wiping out attached alarms.  
> Our UDTs live safely in the standard `[default]` provider and use **Reference Tags** that point to `[MQTT Engine]`. Alarms and TimescaleDB history remain permanent and decoupled from network volatility.

### 1.2 Deploying the UDTs

In Ignition Designer:
1. Navigate to **Tag Browser** → **User Defined Types**.
2. Right-click → **Import Tags** (or copy `ignition-config/tags/*.json` into `data/config/tags`).
3. Verify that `Immersion_Tank` and `Dielectric_Pump` are loaded.
4. In `[default]DataCenter/Pods`, instantiate:
   - `Pod_A01` (parameter `MqttEdgeNode` = `Pod_A01`)
   - `Pod_A02` (parameter `MqttEdgeNode` = `Pod_A02`)
   - `Pod_A03` (parameter `MqttEdgeNode` = `Pod_A03`)

---

## Part 2 — Event Streams Anomaly Ingestion (Ignition 8.3)

Rather than polling an API with periodic scripts, Ignition 8.3 introduces **Event Streams** for event-driven data flow.

### 2.1 The Event Streams Pipeline

The pipeline defined in `ignition-config/event-streams/anomaly_pipeline.json` configures:
1. **Trigger:** Webhook listening on `/api/v1/event-streams/anomalies` (POST).
2. **Parser:** Unpacks the anomaly JSON payload (`pod_id`, `metric`, `value`, `severity`, `method`).
3. **Router:**
   - Critical anomalies (e.g. Critical Heat Flux > 165 W/cm² or SLA breach > $1,000) are routed directly into the `TimescaleDB_Journal` Alarm Journal.
   - Writes the status directly to `[default]DataCenter/Pods/{pod_id}/AnomalyStatus`.

### 2.2 Enabling the Pipeline

1. Open Gateway Web UI → **Event Streams**.
2. Import `anomaly_pipeline.json`.
3. Set `IGNITION_WEBHOOK_URL=http://<ignition-host>:8088/api/v1/event-streams/anomalies` in the `anomaly-agent` environment to enable automated dispatch.

---

## Part 3 — Perspective Native SVG Graphic & Performance Optimization

Modern Perspective dashboards in 8.3 leverage native SVG drawing elements (`ia.shapes.svg`), eliminating the need for heavy external image assets.

### 3.1 String Parameterization Best Practice

> [!TIP]
> **Performance Architecture:**  
> Never pass an entire UDT Object into a Perspective View as a parameter. Doing so forces the Gateway to serialize the entire object over WebSocket to the browser on every tick.  
> The `CoolingLoop` view accepts a single string parameter: `tankPath` (e.g. `"[default]DataCenter/Pods/Pod_A01"`). All component properties use indirect bindings to `{view.params.tankPath}/...`, keeping WebSocket bandwidth minimal.

### 3.2 Visualizing the Boiling Dynamics

In `ignition-config/perspective/views/CoolingLoop/view.json`:
- **Vessel:** SVG `rect` styled with metallic enclosure borders.
- **Fluid Fill:** Bound to `{view.params.tankPath}/HeatFlux_W_cm2`:
  - Normal nucleate boiling (< 150 W/cm²): `#3498DB` (Dielectric Blue)
  - Approaching limit (150–165 W/cm²): `#F39C12` (Warning Amber)
  - Dry-out / Critical Heat Flux (≥ 165 W/cm²): `#E74C3C` (Critical Red)
- **Vapor Phase:** Dynamic SVG `path` with bubble stroke animation responsive to vapor generation rates.

---

## Part 4 — Legacy 8.1 Fallback (HTTP Polling Script)

For systems running Ignition 8.1.x where Event Streams are unavailable, use this Gateway Timer Script fallback:

1. In Gateway / Designer: **Project** → **Gateway Event Scripts** → **Timer**.
2. **Name:** `PollAnomalyAgent`
3. **Rate:** `10000` ms (10 seconds)
4. Script:

```python
ANOMALY_AGENT_URL = "http://localhost:8000/status"
TAG_BASE_PATH = "[default]DataCenter/Pods"

def poll_anomaly_agent():
    try:
        client = system.net.httpClient()
        response = client.get(
            url=ANOMALY_AGENT_URL,
            headers={"Accept": "application/json"},
            connectTimeout=4,
            readTimeout=6,
        )

        if response.statusCode != 200:
            return

        data = system.util.jsonDecode(response.body)
        pods = data.get("pods", [])

        for pod in pods:
            pod_id      = pod.get("pod_id", "")
            status      = pod.get("status", "normal")
            anomaly_cnt = len(pod.get("active_anomalies", []))

            system.tag.writeBlocking(
                ["%s/%s/AnomalyStatus" % (TAG_BASE_PATH, pod_id),
                 "%s/%s/AnomalyCount"  % (TAG_BASE_PATH, pod_id)],
                [status, anomaly_cnt]
            )
    except Exception as e:
        system.util.getLogger("AnomalyPoll").warning("Polling failed: " + str(e))

poll_anomaly_agent()
```

---

## Part 5 — End-to-End Quickstart

1. **Launch the backend ecosystem:**
   ```bash
   docker compose up -d
   ```
2. **Verify running containers:**
   ```bash
   docker compose ps
   ```
   Containers: `timescaledb`, `mosquitto`, `anomaly-agent`, `pod-simulator`.
3. **Inspect live MCP agent status:**
   ```bash
   curl http://localhost:8000/status
   curl http://localhost:8000/health
   ```
4. **Interactive OpenAPI Documentation:**
   Open `http://localhost:8000/docs` in your browser.
