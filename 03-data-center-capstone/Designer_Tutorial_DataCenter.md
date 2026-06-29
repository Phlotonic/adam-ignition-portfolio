# Ignition Designer Tutorial — Regenerative Data Center Capstone

**Ignition Version:** 8.1.x with MQTT Engine module
**Estimated Time:** 60–90 minutes

This tutorial configures Ignition Designer for our Triple-Bottom-Line facility:
1. Create the Immersion Pod UDT and MOF AWG Unit UDT.
2. Write a tag change script that polls the FastAPI anomaly agent via HTTP.
3. Build a Perspective heat map dashboard showing HKUST-1 heat fluxes.

---

## Part 1 — Create the Immersion Pod UDT

The Immersion Pod UDT represents a high-density, two-phase liquid cooled compute unit utilizing HKUST-1 MOF coatings.

### 1.1 Open the UDT Editor

1. Open Ignition Designer → **Tag Browser** (left panel)
2. Right-click **User Defined Types** → **New Data Type**
3. Name: `Immersion_Pod`
4. Click **OK**

### 1.2 Add Member Tags

With `Immersion_Pod` selected, right-click → **New Tag** for each:

| Tag Name | Tag Type | Data Type | Default | Notes |
|----------|----------|-----------|---------|-------|
| `HeatFlux_W_cm2` | OPC Tag | Float4 | 130.0 | Heat flux across the HKUST-1 wick (Limit: 170 W/cm²) |
| `FluidTemp_C` | OPC Tag | Float4 | 55.0 | Bulk temperature of dielectric fluid |
| `VaporPressure_kPa` | OPC Tag | Float4 | 101.3 | Pressure of the boiling fluid |
| `UnsettledBalance_USD` | OPC Tag | Float4 | 0.0 | Accrued economic payout pending smart contract execution |
| `AnomalyCount` | Memory Tag | Int4 | 0 | Count of active anomalies from FastAPI agent |
| `AnomalyStatus` | Memory Tag | String | "normal" | "normal" / "warning" / "critical" |

### 1.3 Create UDT Instances

1. Create folder structure: `DataCenter/Pods`
2. Create instances: `Pod_A01`, `Pod_A02`, `Pod_A03`.
3. These OPC tags will receive values from our `mqtt_pod_simulator.py`.

---

## Part 2 — Create the MOF AWG UDT

We no longer use traditional CRAC (air conditioning) units. Instead, we route the waste heat into Atmospheric Water Generators (AWGs).

### 2.1 Add Member Tags to `AWG_Unit` UDT

| Tag Name | Tag Type | Data Type | Default | Notes |
|----------|----------|-----------|---------|-------|
| `WaterHarvested_Liters` | OPC Tag | Float4 | 0.0 | Yield from the MOF-303 desiccant |
| `DesorptionTemp_C` | OPC Tag | Float4 | 58.0 | Waste heat temperature driving the AWG |
| `Status` | OPC Tag | Int4 | 0 | 0=Running, 1=Fouling, 2=Standby |

---

## Part 3 — Write the HTTP Polling Tag Change Script

This script polls the FastAPI anomaly agent and writes the results into the pod UDT memory tags.

1. In Gateway web UI: **Config** → **Events** → **Gateway Event Scripts** → **Timer**
2. Name: `PollAnomalyAgent`
3. Execution Rate: `60000` ms (60 seconds)

Enter this script:

```python
ANOMALY_AGENT_URL = "http://localhost:8000/status"
TAG_BASE_PATH = "[default]DataCenter/Pods"

def poll_anomaly_agent():
    try:
        client = system.net.httpClient()
        response = client.get(
            url=ANOMALY_AGENT_URL,
            headers={"Accept": "application/json"},
            connectTimeout=5,
            readTimeout=10,
        )

        if response.statusCode != 200:
            return

        facility_data = system.util.jsonDecode(response.body)
        pods = facility_data.get("pods", [])

        for pod in pods:
            pod_id      = pod.get("pod_id", "")
            status      = pod.get("status", "normal")
            anomaly_cnt = len(pod.get("active_anomalies", []))

            status_tag = "%s/%s/AnomalyStatus" % (TAG_BASE_PATH, pod_id)
            count_tag  = "%s/%s/AnomalyCount"  % (TAG_BASE_PATH, pod_id)

            system.tag.writeBlocking(
                [status_tag, count_tag],
                [status,     anomaly_cnt]
            )
            
    except Exception as e:
        system.util.getLogger("AnomalyPoll").error("Failed: " + str(e))

poll_anomaly_agent()
```

---

## Part 4 — Build the Perspective Heat Flux Dashboard

Instead of a traditional air temperature heatmap, we visualize the boiling dynamics (Heat Flux).

1. In Designer: New View → `dashboards/PodThermalHeatmap`
2. Create a Template Repeater (`components/PodTile`) binding to the `HeatFlux_W_cm2` tag.
3. Configure the color expression to highlight CHF risks:
```
if({view.params.heat_flux} >= 165, '#d32f2f',      // Critical dry-out: ≥ 165
   if({view.params.heat_flux} >= 150, '#f57c00',   // Warning: 150–165
      '#388e3c'))                                  // Normal nucleate boiling
```

## Quick Start
1. Run `docker compose up -d`
2. Start the simulator: `python mqtt_pod_simulator.py`
3. Start the AI Agent: `python ai_anomaly_agent.py`
