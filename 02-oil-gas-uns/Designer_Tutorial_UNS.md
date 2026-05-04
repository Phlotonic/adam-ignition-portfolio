# Ignition Designer Tutorial — UNS / MQTT Sparkplug B

**Ignition Version:** 8.1.x with Cirrus Link MQTT Engine module
**Estimated Time:** 30–45 minutes

This tutorial configures Ignition to consume Sparkplug B telemetry from the
Eclipse Mosquitto broker started with `docker compose up -d`, and to auto-populate
tags from the `edge_compressor_sim.py` simulator.

**Prerequisites:**
- Ignition 8.1 Gateway running (default: `http://localhost:8088`)
- Docker Compose broker started: `docker compose up -d`
- `edge_compressor_sim.py` running: `python edge_compressor_sim.py`

---

## Part 1 — Install the Cirrus Link MQTT Engine Module

The MQTT Engine module is a licensed Cirrus Link product that bridges Sparkplug B
topics into the Ignition tag tree. It is **not** included in the base Ignition license.

> **Development note:** Cirrus Link offers a free trial module for Ignition 8.1.
> Download from: https://www.cirrus-link.com/mqtt-modules/

### 1.1 Download the Module

1. Download `MQTT-Engine-signed.modl` from the Cirrus Link website (requires account)
2. Save to a local directory — do not unzip; `.modl` is the correct extension

### 1.2 Install via Gateway

1. Navigate to `http://localhost:8088` → **Log In**
2. Left nav: **Config** → **System** → **Modules**
3. Scroll to the bottom of the module list
4. Click **Install or Upgrade a Module**
5. Click **Choose File** → select `MQTT-Engine-signed.modl`
6. Click **Install** → agree to the license agreement
7. Wait for the module status to show **Running** (green dot)

> **Verify:** After installation, you should see **MQTT Engine** in the modules
> list with status "Running". A new section **MQTT Engine** will appear in the
> Gateway Config left nav.

---

## Part 2 — Configure the MQTT Broker Connection (Server Set)

In Cirrus Link MQTT Engine, broker connections are organized as **Server Sets**.
A Server Set defines one or more MQTT brokers (for redundancy) that the Engine
connects to and subscribes from.

### 2.1 Navigate to MQTT Engine Settings

1. Gateway left nav: **Config** → **MQTT Engine** → **Settings**
2. Confirm the Engine is **Enabled** (toggle switch at top)

### 2.2 Create a New Server Set

1. Left nav: **Config** → **MQTT Engine** → **Servers** → **Server Sets**
2. Click **Create new Server Set**
3. Fill in:

   | Field | Value |
   |-------|-------|
   | **Name** | `LocalMosquitto` |
   | **Enabled** | ✓ (checked) |

4. Click **Save**

### 2.3 Add the Mosquitto Broker to the Server Set

1. With `LocalMosquitto` selected, click **Edit Servers** (or **New Server** button)
2. Click **Create new Server**
3. Fill in:

   | Field | Value |
   |-------|-------|
   | **Name** | `Mosquitto_Local` |
   | **URL** | `tcp://localhost:1883` |
   | **Server Set** | `LocalMosquitto` |
   | **Client ID** | `ignition-mqtt-engine` |
   | **Username** | *(leave blank for anonymous)* |
   | **Password** | *(leave blank)* |
   | **Keep Alive** | `60` |

4. Click **Save**

> **Production note:** For TLS, change URL to `ssl://your-broker:8883` and add
> certificate paths in the **Security** section (CA cert, client cert, client key).

### 2.4 Verify Broker Connection

1. Left nav: **Config** → **MQTT Engine** → **Servers** → **Server Sets**
2. The `LocalMosquitto` row should show **Connected** status (green)
3. If not connected, check: broker is running (`docker ps`), port 1883 is accessible,
   and the simulator is running (`python edge_compressor_sim.py`)

---

## Part 3 — Configure Namespace (Subscribe to Sparkplug B Topics)

The Namespace configuration tells MQTT Engine which Sparkplug B groups to subscribe
to and where to place the resulting tags in the Tag Browser.

### 3.1 Open Namespace Configuration

1. Left nav: **Config** → **MQTT Engine** → **Namespaces** → **Sparkplug**
2. Click **Create new Namespace**

### 3.2 Configure the Sparkplug B Namespace

| Field | Value | Notes |
|-------|-------|-------|
| **Name** | `OilGas_UNS` | Logical name for this namespace group |
| **Server Set** | `LocalMosquitto` | The broker connection defined in Part 2 |
| **Group Filter** | `OilGas_Field_A` | Subscribe only to this Sparkplug B group |
| **Edge Node Filter** | *(leave blank for all)* | Or enter `Compressor_Station_01` |
| **Tag Provider** | `default` | Which Ignition tag provider hosts the tags |
| **Tag Path** | `MQTT_Devices` | Root folder in Tag Browser for MQTT tags |

3. Click **Save**

---

## Part 4 — Verify Tags Auto-Populate in Tag Browser

When MQTT Engine receives an NBIRTH message, it automatically creates the full
tag hierarchy from the Sparkplug B metric descriptors.

### 4.1 Open Ignition Designer

1. Launch Ignition Designer via Designer Launcher or `http://localhost:8088/designer`
2. Connect to your Gateway

### 4.2 Navigate to MQTT Tags in Tag Browser

1. In Designer: open the **Tag Browser** (wrench icon, left panel)
2. Expand the `default` tag provider
3. Expand `MQTT_Devices` (the Tag Path configured in Part 3)
4. Expand `OilGas_Field_A` (Group ID)
5. Expand `Compressor_Station_01` (Edge Node ID)

You should see all 8 metrics auto-created as tags:

```
[default]
  └── MQTT_Devices
        └── OilGas_Field_A
              └── Compressor_Station_01
                    ├── SuctionPressure_PSI      [Float4, GOOD]
                    ├── DischargePressure_PSI    [Float4, GOOD]
                    ├── VibrationX_mms           [Float4, GOOD]
                    ├── VibrationY_mms           [Float4, GOOD]
                    ├── MotorTemp_C              [Float4, GOOD]
                    ├── RPM                      [Float4, GOOD]
                    ├── RunHours                 [Float4, GOOD]
                    └── Status                   [Int4,   GOOD]
```

> **If tags don't appear:** Confirm the simulator is running (`python edge_compressor_sim.py`)
> and that it successfully published NBIRTH (look for `→ NBIRTH published` in console output).
> The MQTT Engine only creates tags after processing NBIRTH — NDATA alone is not enough.

### 4.3 Enable Watch Mode to Verify Updates

1. In the Tag Browser, right-click `Compressor_Station_01`
2. Select **Watch Mode** (or press `W`)
3. A Watch window opens showing live tag values
4. Observe values updating every 5 seconds (the simulator's publish interval)
5. Values should show realistic fluctuation (Gaussian noise around the normal range)

---

## Part 5 — Create a Derived Tag for Compression Ratio

The compression ratio is not a raw sensor value — it's calculated from two tags.
In Ignition, derived/calculated tags are created as **Expression Tags** or
**Derived Tags** in the Tag Browser.

### 5.1 Create an Expression Tag

1. In the Tag Browser, navigate to the folder where you want the derived tag
   (e.g., right-click `MQTT_Devices/OilGas_Field_A/Compressor_Station_01`)
2. Right-click → **New Tag** → **New Expression Tag**
3. Fill in:

   | Property | Value |
   |----------|-------|
   | **Name** | `CompressionRatio` |
   | **Data Type** | `Float4` |
   | **Expression** | (see below) |
   | **Tag Group** | `Default` |
   | **Units** | `:1 ratio` |

4. In the **Expression** field, enter:

```
{[default]MQTT_Devices/OilGas_Field_A/Compressor_Station_01/DischargePressure_PSI}
/
{[default]MQTT_Devices/OilGas_Field_A/Compressor_Station_01/SuctionPressure_PSI}
```

> **Note on the path:** In Ignition expressions, tag paths use curly braces `{}`.
> If either source tag has BAD quality, the Expression Tag automatically inherits
> BAD quality — no special null-check needed.

5. Click **OK**

### 5.2 Verify the Compression Ratio

1. In Watch Mode, observe `CompressionRatio`
2. With normal pressure ranges (suction 85-95 PSI, discharge 250-280 PSI),
   the ratio should be approximately **2.7 to 3.3**
3. Values outside this range would warrant investigation (valve wear, inlet blockage)

### 5.3 Simulate NDEATH (Optional — Testing)

1. Stop the simulator: press **Ctrl+C** in the simulator terminal
2. The simulator publishes NDEATH gracefully
3. In the Tag Browser Watch window, all tags from `Compressor_Station_01`
   will change quality to **STALE** (shown as gray/strikethrough in Perspective)
4. Restart the simulator — tags return to **GOOD** quality after NBIRTH is received

---

## Part 6 — Quick Reference: Click Path Summary

```
# Install MQTT Engine module
Gateway → Config → System → Modules → Install or Upgrade → [select .modl file]

# Configure broker connection
Gateway → Config → MQTT Engine → Settings → Enabled: ON
Gateway → Config → MQTT Engine → Servers → Server Sets → Create new Server Set
Gateway → Config → MQTT Engine → Servers → [set name] → Edit Servers → Create new Server

# Configure namespace subscription
Gateway → Config → MQTT Engine → Namespaces → Sparkplug → Create new Namespace

# Verify tags in Designer
Designer → Tag Browser → default → MQTT_Devices → OilGas_Field_A → Compressor_Station_01

# Watch mode
Tag Browser → right-click folder → Watch Mode (or press W)

# Create derived tag
Tag Browser → right-click folder → New Tag → New Expression Tag → [enter expression]
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Server Set shows "Disconnected" | Broker not running | `docker compose up -d` |
| Tags don't appear after broker connects | Simulator not running or hasn't sent NBIRTH | Run `python edge_compressor_sim.py`, check console |
| Tags appear but show BAD quality | Broker connection interrupted | Check `docker ps` and network |
| CompressionRatio shows 0.0 | Source tags have 0 values | Verify simulator output; check NBIRTH was received |
| MQTT Engine module not in left nav | Module not installed | Repeat Part 1 |
