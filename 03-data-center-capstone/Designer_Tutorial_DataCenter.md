# Ignition Designer Tutorial — AI Data Center Capstone

**Ignition Version:** 8.1.x with MQTT Engine module (from Project 2)
**Estimated Time:** 60–90 minutes

This tutorial configures Ignition Designer to:
1. Create the CRAC Unit UDT and Server Rack UDT with appropriate tags
2. Write a tag change script that polls the FastAPI anomaly agent via HTTP
3. Store the JSON response in Ignition memory tags
4. Build a Perspective heat map dashboard showing rack temperatures

**Prerequisites:**
- Docker stack running: `docker compose up -d` (TimescaleDB, Mosquitto, anomaly-agent)
- FastAPI agent accessible at `http://localhost:8000`
- Verify: `curl http://localhost:8000/health` returns `{"status":"ok",...}`

---

## Part 1 — Create the CRAC Unit UDT

A **CRAC (Computer Room Air Conditioning)** unit is the primary cooling asset in
a data hall. This UDT represents one CRAC unit's key operational parameters.

### 1.1 Open the UDT Editor

1. Open Ignition Designer → **Tag Browser** (left panel)
2. Right-click **User Defined Types** → **New Data Type**
3. Name: `CRAC_Unit`
4. Click **OK**

### 1.2 Add Member Tags

With `CRAC_Unit` selected, right-click → **New Tag** for each:

| Tag Name | Tag Type | Data Type | Default | Notes |
|----------|----------|-----------|---------|-------|
| `SupplyTemp_C` | OPC Tag | Float4 | 16.0 | CRAC supply air temperature (should be ≤ 18°C) |
| `ReturnTemp_C` | OPC Tag | Float4 | 28.0 | Return air temperature from hot aisle |
| `SetPoint_C` | OPC Tag | Float4 | 18.0 | Temperature setpoint (controlled by BMS) |
| `FanSpeed_pct` | OPC Tag | Float4 | 75.0 | Fan VFD speed as percentage (0–100%) |
| `Status` | OPC Tag | Int4 | 0 | 0=Running, 1=Fault, 2=Standby, 3=Maintenance |
| `DeltaT_C` | Expression Tag | Float4 | — | Expression: `{ReturnTemp_C} - {SupplyTemp_C}` |
| `Efficiency` | Expression Tag | Float4 | — | Expression: see below |

**DeltaT_C expression** (temperature rise across the room):
```
{ReturnTemp_C} - {SupplyTemp_C}
```
Normal range: 8–15°C. Low delta-T (<6°C) suggests short-cycling or excess cooling.

**Efficiency expression** (simple proxy metric):
```
({SetPoint_C} - {SupplyTemp_C}) / {FanSpeed_pct} * 100
```

### 1.3 Create UDT Instances for All CRAC Units

1. In Tag Browser, create a folder: right-click root → **New Folder** → `HVAC`
2. Inside `HVAC`, create a subfolder `CRAC_Units`
3. Right-click `CRAC_Units` → **New Tag** → **New User Defined Type Instance**
4. Select `CRAC_Unit`, name: `CRAC_01`
5. Repeat for `CRAC_02`, `CRAC_03` (as needed for your hall)

---

## Part 2 — Create the Server Rack UDT

The Server Rack UDT represents one instrumented server rack in the data hall.

### 2.1 Create the UDT Definition

1. Right-click **User Defined Types** → **New Data Type**
2. Name: `Server_Rack`
3. Click **OK**

### 2.2 Add Member Tags

| Tag Name | Tag Type | Data Type | Default | Notes |
|----------|----------|-----------|---------|-------|
| `InletTemp_C` | OPC Tag | Float4 | 20.0 | Cold aisle inlet temperature; ASHRAE A1 limit: 27°C |
| `ExitTemp_C` | OPC Tag | Float4 | 35.0 | Hot aisle exit temperature; typical limit: 45°C |
| `PowerDraw_kW` | OPC Tag | Float4 | 5.0 | Rack PDU measured power consumption |
| `AirflowStatus` | Memory Tag | Boolean | true | TRUE = adequate airflow; updated by anomaly agent |
| `AnomalyCount` | Memory Tag | Int4 | 0 | Count of active anomalies; written by HTTP polling script |
| `AnomalyStatus` | Memory Tag | String | "normal" | "normal" / "warning" / "critical" from API |
| `DeltaT_C` | Expression Tag | Float4 | — | `{ExitTemp_C} - {InletTemp_C}` (normal: 10–20°C) |
| `LastUpdated` | Memory Tag | String | — | ISO timestamp of last API poll result |

**DeltaT_C** is the thermal load indicator:
- < 8°C: abnormally low (possible bypass airflow issue)
- 10–20°C: normal operating range for typical rack densities
- > 25°C: high thermal load; verify cooling adequacy

### 2.3 Create UDT Instances

1. Create folder structure: `DataCenter/Racks`
2. Create instances: `Rack_A01`, `Rack_A02`, `Rack_A03`, `Rack_B01`, etc.
3. In production, these OPC tags would be wired to actual IPMI/iDRAC BMC endpoints
   or SNMP-enabled PDUs. For this demo, they receive values from the MQTT simulator.

---

## Part 3 — Write the HTTP Polling Tag Change Script

Ignition's `system.net.httpClient()` function (Ignition 8.1+) enables Gateway
scripts to make HTTP requests. This script polls the FastAPI anomaly agent and
writes the results into the rack UDT memory tags.

### 3.1 Create a Gateway Tag Change Script

1. In Gateway web UI: **Config** → **Events** → **Tag Change Scripts**
   > In Designer: **Project** → **Gateway Event Scripts** → **Tag Change**
2. Click **Create new Tag Change Script**
3. Name: `PollAnomalyAgent`
4. **Trigger Tag Path:** `[System]Client/PollTrigger`
   (This is a system tag that changes every second — we'll filter by time inside the script)

> **Alternative approach:** Use a **Gateway Timer Script** instead:
> **Config** → **Events** → **Gateway Event Scripts** → **Timer**
> Set interval to 60 seconds. This is cleaner than a tag change trigger for periodic polling.

### 3.2 Timer Script — Poll FastAPI and Write to Tags

Create a **Gateway Timer Script** (**Config** → **Gateway Event Scripts** → **Timer**):

- **Name:** `PollAnomalyAgent`
- **Execution Rate:** `60000` ms (60 seconds)

Enter this script:

```python
# =============================================================================
# Gateway Timer Script: Poll FastAPI Anomaly Agent
# Runs every 60 seconds. Reads facility status from the anomaly agent REST API
# and writes results into Ignition memory tags on the Server Rack UDTs.
#
# API endpoint: GET http://localhost:8000/status
# Returns: FacilityStatus JSON with per-rack status including anomaly counts
# =============================================================================

ANOMALY_AGENT_URL = "http://localhost:8000/status"
TAG_BASE_PATH = "[default]DataCenter/Racks"

def poll_anomaly_agent():
    """
    Fetch facility status from the FastAPI anomaly agent and update rack tags.
    Uses system.net.httpClient() (Ignition 8.1+).
    """
    try:
        # --- Build the HTTP client ---
        # system.net.httpClient() returns an HttpClient object.
        # Parameters:
        #   connectTimeout: seconds to wait for TCP connection
        #   readTimeout:    seconds to wait for response body
        client = system.net.httpClient()

        # --- Make the GET request ---
        response = client.get(
            url=ANOMALY_AGENT_URL,
            headers={"Accept": "application/json"},
            connectTimeout=5,   # seconds
            readTimeout=10,     # seconds
        )

        # --- Check HTTP status ---
        if response.statusCode != 200:
            system.util.getLogger("AnomalyPoll").warning(
                "Anomaly agent returned HTTP %d" % response.statusCode
            )
            return

        # --- Parse JSON response ---
        # response.body is a string; system.util.jsonDecode parses it to a dict
        facility_data = system.util.jsonDecode(response.body)

        # --- Get current timestamp for the LastUpdated tag ---
        import system.date
        now_str = system.date.format(system.date.now(), "yyyy-MM-dd'T'HH:mm:ss'Z'")

        # --- Iterate over racks in the response ---
        racks = facility_data.get("racks", [])

        for rack in racks:
            rack_id     = rack.get("rack_id", "")
            status      = rack.get("status", "normal")
            anomaly_ids = rack.get("active_anomalies", [])
            anomaly_cnt = len(anomaly_ids)

            # Build tag paths for this rack's UDT memory tags
            # The rack_id from the API must match the UDT instance name in Ignition
            anomaly_status_tag = "%s/%s/AnomalyStatus" % (TAG_BASE_PATH, rack_id)
            anomaly_count_tag  = "%s/%s/AnomalyCount"  % (TAG_BASE_PATH, rack_id)
            last_updated_tag   = "%s/%s/LastUpdated"   % (TAG_BASE_PATH, rack_id)

            # Write the values to the memory tags
            # system.tag.writeBlocking() writes synchronously and waits for quality
            system.tag.writeBlocking(
                [anomaly_status_tag, anomaly_count_tag, last_updated_tag],
                [status,             anomaly_cnt,       now_str]
            )

            # Log any active anomalies at warning level for the Gateway logs
            if anomaly_cnt > 0:
                system.util.getLogger("AnomalyPoll").warning(
                    "Rack %s has %d active anomaly(ies): %s"
                    % (rack_id, anomaly_cnt, str(status).upper())
                )

        system.util.getLogger("AnomalyPoll").info(
            "Anomaly agent poll complete: %d racks updated" % len(racks)
        )

    except Exception as e:
        # Log but don't raise — a polling failure should not crash the script
        system.util.getLogger("AnomalyPoll").error(
            "Failed to poll anomaly agent: %s" % str(e)
        )


# Execute the polling function
poll_anomaly_agent()
```

### 3.3 Verify Tag Updates

1. In Designer Tag Browser: navigate to `DataCenter/Racks/Rack_A01`
2. Right-click → **Watch Mode**
3. Wait up to 60 seconds for the timer script to fire
4. `AnomalyStatus`, `AnomalyCount`, and `LastUpdated` should update

> **Debugging:** In Gateway web UI → **Diagnostics** → **Logs** → filter by
> `AnomalyPoll` to see the script's log output.

---

## Part 4 — Build the Perspective Heat Map Dashboard

A heat map visualizes rack temperatures as a color-coded grid, making it
instantly obvious which racks are running hot.

### 4.1 Create a New Perspective View

1. In Designer: **Project Browser** → right-click `views` → **New View**
2. Name: `dashboards/RackThermalHeatmap`
3. **View Type:** `Fixed` or `Coordinate`
4. Set dimensions: Width `1200`, Height `800`

### 4.2 Add a Background Grid Using a Flex Container

1. Drag a **Flex Container** onto the view canvas
2. Set `props.direction` to `row` (horizontal)
3. Set `props.wrap` to `wrap` (allows racks to wrap to next row)
4. Size the container to fill the view

### 4.3 Create a Rack Temperature Display Component (Template Repeater)

The **Template Repeater** component is ideal for this — it renders one instance
of a template view per item in a data source.

**Step 1: Create the rack tile template view:**
1. New View: `components/RackTile`
2. Set size: 100 × 120 px
3. Add a `Label` for rack ID: bind to `{view.params.rack_id}`
4. Add a `Label` for temperature: bind to `{view.params.inlet_temp_c}` with format `#0.0°C`
5. Add a `Rectangle` shape:
   - Background color: **Expression binding** on `props.style.backgroundColor`:

```
if({view.params.inlet_temp_c} >= 27, '#d32f2f',      // critical red: ≥ 27°C
   if({view.params.inlet_temp_c} >= 25, '#f57c00',   // warning orange: 25–27°C
      if({view.params.inlet_temp_c} >= 22, '#fbc02d', // warm yellow: 22–25°C
         '#388e3c')))                                   // normal green: < 22°C
```

6. Add a `Label` overlay showing anomaly status: bind to `{view.params.status}`

**Step 2: Wire the Template Repeater:**
1. On the main heatmap view, drag a **Template Repeater** component
2. Set `props.instanceTemplate` to `components/RackTile`
3. Set `props.instances` with a **Tag Binding** or **Named Query Binding**

For a Tag Binding (simplest approach):
- Use an **Expression** or **Script** to build the instances array from UDT tags

In a Perspective **Script Transform** binding on `props.instances`:

```python
# Script Transform: Build rack tile data from UDT tags
# This runs in the browser context; use a light transform

def transform(self, value, quality, timestamp):
    """
    Reads AnomalyStatus and InletTemp_C from each rack UDT instance.
    Returns a list of dicts for the Template Repeater.
    """
    rack_ids = ["Rack_A01", "Rack_A02", "Rack_A03",
                "Rack_B01", "Rack_B02", "Rack_B03"]
    base = "[default]DataCenter/Racks"

    instances = []
    for rack_id in rack_ids:
        tags_to_read = [
            "%s/%s/InletTemp_C" % (base, rack_id),
            "%s/%s/ExitTemp_C" % (base, rack_id),
            "%s/%s/AnomalyStatus" % (base, rack_id),
            "%s/%s/AnomalyCount" % (base, rack_id),
        ]
        values = system.tag.readBlocking(tags_to_read)

        instances.append({
            "rack_id":      rack_id,
            "inlet_temp_c": values[0].value if values[0] else 0.0,
            "exit_temp_c":  values[1].value if values[1] else 0.0,
            "status":       values[2].value if values[2] else "unknown",
            "anomaly_count": values[3].value if values[3] else 0,
        })

    return instances
```

> **Note on `system.tag.readBlocking()`:** This Jython function reads all tags
> in a single gateway round-trip (synchronous). It is appropriate for a timed
> refresh (e.g., component refresh every 30 seconds) but not for use in
> hot-path script transforms that fire on every tag change.

### 4.4 Add a PUE Trend Chart (Time Series)

1. Drag a **Time Series Chart** component onto the dashboard
2. **Data Source:** Named Query → create a new Named Query `GetPUEHourly`:

```sql
-- Reads from the pue_hourly continuous aggregate in TimescaleDB
SELECT
    hour                            AS ts,
    ROUND(pue::NUMERIC, 3)          AS pue,
    ROUND(it_power_kw::NUMERIC, 2)  AS it_kw,
    ROUND(overhead_power_kw::NUMERIC, 2) AS overhead_kw
FROM pue_hourly
WHERE hour >= NOW() - INTERVAL '24 hours'
ORDER BY hour ASC;
```

3. Bind `Time Series Chart` pen:
   - X axis: `ts` column
   - Y axis: `pue` column
4. Add a **reference line** at Y=1.5 (anomaly threshold — PUE > 1.5 triggers investigation)
5. Add a second pen for `it_kw` on a secondary Y axis

### 4.5 Add an Active Anomalies Table

1. Drag a **Table** component onto the dashboard
2. Data source: **Named Query** or a **Script Transform** that calls the FastAPI:

```python
# In a Perspective Binding (Script transform or Gateway Tag script)
# Fetches active anomalies for the dashboard table

def get_anomaly_table():
    client = system.net.httpClient()
    response = client.get("http://localhost:8000/anomalies",
                         connectTimeout=3, readTimeout=5)
    if response.statusCode == 200:
        return system.util.jsonDecode(response.body)
    return []
```

3. Configure Table columns: `rack_id`, `metric`, `value`, `severity`, `detected_at`, `method`
4. Add **row styling**: use an **Expression** on row background:
   - `if({severity} == "critical", '#ffebee', if({severity} == "warning", '#fff8e1', '#f1f8e9'))`

---

## Part 5 — Summary Click Path Reference

```
# Create CRAC UDT
Designer → Tag Browser → User Defined Types → New Data Type → CRAC_Unit
  → New Tag × 7 tags (OPC + Expression types)

# Create Server Rack UDT
Designer → Tag Browser → User Defined Types → New Data Type → Server_Rack
  → New Tag × 8 tags (OPC + Memory + Expression types)

# Create UDT instances
Designer → Tag Browser → DataCenter/Racks → New Tag → New UDT Instance

# Create Gateway Timer Script
Gateway → Config → Gateway Event Scripts → Timer → Create new → [paste script]
  → Set execution rate: 60000 ms

# Verify polling
Gateway → Diagnostics → Logs → filter: AnomalyPoll

# Build heatmap view
Designer → Project Browser → views → New View → dashboards/RackThermalHeatmap
  → Template Repeater → components/RackTile → [bind instances]

# Add PUE chart
Designer → Named Queries → New → GetPUEHourly → [paste SQL]
  → Drag Time Series Chart → bind Named Query

# View FastAPI Swagger UI (for manual testing)
http://localhost:8000/docs
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Timer script shows `Failed to poll` | Anomaly agent not running | `docker compose up -d`; verify `http://localhost:8000/health` |
| `system.net.httpClient()` not found | Ignition version < 8.1 | Upgrade to 8.1+; alternatively use `system.net.httpGet()` (deprecated) |
| Rack tags not updating | Tag path mismatch (rack_id vs UDT instance name) | Confirm rack_id from API matches exactly the UDT instance name in Tag Browser |
| Heat map shows no color | Expression binding error | Check Designer console for expression errors; test expression in Expression editor |
| PUE chart empty | TimescaleDB not populated | Run `PUE_metrics.sql` sample data INSERT; verify `docker compose ps` |
| `system.tag.readBlocking()` slow | Too many tags in one call | Batch into groups of ≤ 50 tags; or use asynchronous tag reads |
