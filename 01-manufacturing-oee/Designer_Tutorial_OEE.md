# Ignition Designer Tutorial — Manufacturing OEE

**Ignition Version:** 8.1.x (Perspective, Tag Browser, Designer)
**Estimated Time:** 60–90 minutes for full setup

This tutorial walks through the exact click paths to implement the OEE project
inside Ignition Designer and Gateway. It assumes a running Ignition 8.1 gateway
and a reachable PostgreSQL 15 instance with the schema from `downtime_queries.sql`
already applied.

---

## Part 1 — Configure the PostgreSQL Database Connection

### 1.1 Open Gateway Config

1. Open a browser and navigate to `http://localhost:8088` (default Gateway address)
2. Click **Log In** in the top right → enter your Gateway admin credentials
3. In the left nav, click **Config**
4. Under the **Databases** section in the left nav, click **Connections**

### 1.2 Create a New Database Connection

1. Click **Create new Database Connection**
2. **Translator:** Select `PostgreSQL`
3. Fill in the connection form:

   | Field | Value |
   |-------|-------|
   | **Name** | `oee_postgres` |
   | **Connect URL** | `jdbc:postgresql://localhost:5432/oee_db` |
   | **Username** | *(your PostgreSQL user)* |
   | **Password** | *(your PostgreSQL password)* |
   | **Extra Connection Properties** | `currentSchema=public` |

4. Click **Test Connection** — confirm the status badge turns green
5. Click **Save Changes**

> **Note:** The connection name `oee_postgres` is referenced in Transaction
> Groups and Named Queries by this exact name. If you use a different name,
> update all references accordingly.

---

## Part 2 — Create the Bottling Line UDT (User Defined Type)

UDTs (called "User Defined Types" in Ignition) allow you to define a reusable
tag template. Every instance of the `BottlingLine_Unit` UDT automatically gets
all the standard OEE tags — you define it once, instantiate it for each line.

### 2.1 Open the Tag Browser

1. In Ignition Designer (open via **Designer Launcher** or `http://localhost:8088/designer`)
2. In the left panel, find the **Tag Browser** (wrench icon)
3. In the Tag Browser header, click the **Edit Tags** button (pencil icon)

### 2.2 Create the UDT Definition

1. In the Tag Browser, right-click on **User Defined Types** → **New Data Type**
2. Name the new type: `BottlingLine_Unit`
3. Click **OK**

### 2.3 Add Member Tags to the UDT

With `BottlingLine_Unit` selected in the UDT editor, add the following tags.
For each tag: right-click the UDT name → **New Tag** → **New Memory Tag** (for
calculated/aggregated values) or **New OPC Tag** (for PLC-sourced values).

| Tag Name | Tag Type | Data Type | Initial Value | Notes |
|----------|----------|-----------|---------------|-------|
| `TotalCount` | OPC Tag | Int4 | 0 | PLC register: production counter |
| `GoodCount` | OPC Tag | Int4 | 0 | PLC register: QC-passed counter |
| `RunState` | OPC Tag | Boolean | false | PLC coil: line running = TRUE |
| `CurrentShiftTarget` | Memory Tag | Int4 | 0 | Written by start-of-shift script |
| `IdealCycleTime_sec` | Memory Tag | Float4 | 2.4 | Nameplate rate (e.g., 25 units/min = 2.4s) |
| `Availability` | Expression Tag | Float4 | — | Expression: see below |
| `Performance` | Expression Tag | Float4 | — | Expression: see below |
| `Quality` | Expression Tag | Float4 | — | Expression: see below |
| `OEE` | Expression Tag | Float4 | — | Expression: see below |
| `PlannedTime_min` | Memory Tag | Int4 | 480 | Minutes per shift (e.g., 8-hour shift = 480) |
| `DowntimeAccum_min` | Memory Tag | Float4 | 0 | Accumulated by Tag Change Script on RunState |
| `CurrentState` | Memory Tag | String | "Running" | Updated by Tag Change Script |
| `ShiftDate` | Memory Tag | String | — | Set by start-of-shift script |

### 2.4 Add Expression Tag Formulas

For the calculated OEE tags, click the tag → **Edit** → **Value** section →
set **Tag Type** to `Expression Tag` and enter these expressions:

**Availability:**
```
( {PlannedTime_min} - {DowntimeAccum_min} ) / {PlannedTime_min}
```

**Performance:**
```
({IdealCycleTime_sec} * {TotalCount}) / (({PlannedTime_min} - {DowntimeAccum_min}) * 60.0)
```

**Quality:**
```
{GoodCount} / coalesce( {TotalCount}, 1 )
```

**OEE:**
```
{Availability} * {Performance} * {Quality}
```

> **Tip:** In real deployments, you would protect against division by zero more
> explicitly. The `coalesce( {TotalCount}, 1 )` pattern prevents NaN at shift
> start when TotalCount is 0.

### 2.5 Create a UDT Instance

1. In the Tag Browser, navigate to a logical folder (e.g., right-click root → **New Folder** → `BottlingLines`)
2. Right-click `BottlingLines` → **New Tag** → **New User Defined Type Instance**
3. Select `BottlingLine_Unit` from the dropdown
4. Name it: `Line3_330ml`
5. Click **OK**

You now have a fully structured tag set at `[default]BottlingLines/Line3_330ml/`
with all OEE tags ready for PLC binding and expression evaluation.

---

## Part 3 — Configure a Transaction Group (Production Count Writer)

Transaction Groups are Ignition's built-in mechanism for writing tag values to a
database table on a configurable trigger.

### 3.1 Open Transaction Group Configuration

1. In the Gateway web UI: **Config** → **OPC-UA** (or **Devices**) → **Transaction Groups**
   > In Ignition 8.1 with the **SQL Bridge** module installed, Transaction Groups
   > appear under **Config → SQL Bridge → Transaction Groups**
2. Click **Create new Transaction Group**

### 3.2 Configure the Production Run Update Group

Fill in the group properties:

| Property | Value |
|----------|-------|
| **Name** | `Line3_ProductionRun_Writer` |
| **Database** | `oee_postgres` |
| **Execution Mode** | `Triggered` |
| **Trigger Tag** | `[default]BottlingLines/Line3_330ml/RunState` |
| **Trigger Type** | `Value change (TRUE edge)` |
| **Table Action** | `Update` |
| **Table Name** | `production_runs` |

### 3.3 Add Table Items (Tag → Column Mappings)

Click **Add Items** and map each tag to a column:

| Tag Path | Column Name | Data Direction |
|----------|-------------|----------------|
| `[default]BottlingLines/Line3_330ml/TotalCount` | `total_count` | Tag → DB |
| `[default]BottlingLines/Line3_330ml/GoodCount` | `good_count` | Tag → DB |
| `[default]BottlingLines/Line3_330ml/RunState` | *(not stored)* | — |
| *(timestamp)* | `ended_at` | System timestamp |

Add a **WHERE clause** expression:
```sql
equipment_id = 4 AND shift_date = CURRENT_DATE AND shift_number = :ShiftNum
```

Where `:ShiftNum` is a scripted tag or a Gateway Tag mapped to the current shift number.

> **Note:** For INSERT on shift start, create a second Transaction Group triggered
> on the start-of-shift event with `Table Action = Insert`. Set `run_time_min`,
> `planned_production_time_min`, and `ideal_cycle_time_sec` from UDT memory tags.

---

## Part 4 — Create Named Queries

Named Queries in Ignition are stored, parameterized SQL queries that Perspective
components can call directly without writing scripting code.

### 4.1 Open Named Query Editor

1. In Designer: **Project** menu → **Named Queries** (or the Named Queries panel in left nav)
2. Right-click the root → **New Folder** → `OEE`

### 4.2 Create the OEE Dashboard Query

1. Right-click `OEE` folder → **New Named Query**
2. Name: `GetShiftOEE`
3. **Database:** `oee_postgres`
4. **Query Type:** `Query` (returns rows)
5. Paste the Section 3 query from `downtime_queries.sql` into the SQL editor
6. In the **Parameters** panel, add:
   - `equipment_id` — Type: `Integer`, Default: `4`
   - `shift_date` — Type: `Date`, Default: `now()`
   - `shift_number` — Type: `Integer`, Default: `1`
7. Click **Run** to test — confirm it returns OEE data
8. Click **Save**

### 4.3 Create the Downtime Insert Query

1. Right-click `OEE` folder → **New Named Query**
2. Name: `InsertDowntimeEvent`
3. **Query Type:** `Update` (no return rows)
4. SQL:

```sql
INSERT INTO downtime_events (
    equipment_id, run_id, started_at, category_code,
    category_detail, entered_by
) VALUES (
    :equipment_id,
    (SELECT run_id FROM production_runs
     WHERE equipment_id = :equipment_id
       AND shift_date = CURRENT_DATE
       AND ended_at IS NULL
     LIMIT 1),
    :started_at,
    :category_code,
    :category_detail,
    :entered_by
)
```

5. Add parameters: `equipment_id` (Integer), `started_at` (DateTime),
   `category_code` (String), `category_detail` (String), `entered_by` (String)
6. Save

---

## Part 5 — Build the Perspective Downtime Entry UI

This creates a modal form that operators use to log a downtime event with its
ISA-95 category.

### 5.1 Create a New Perspective View

1. In Designer: **Project Browser** (left nav) → right-click `views` → **New View**
2. Name: `components/DowntimeEntryModal`
3. **View Type:** `Popup` (modal dialog)
4. Set view size: Width `480`, Height `380`

### 5.2 Add Form Components

In the Perspective view, drag and drop these components from the component palette:

**1. Dropdown — Downtime Category**
- Component: `Dropdown`
- Property `props.options`: click binding → enter this as a Static value:

```json
[
  {"label": "Equipment Failure",      "value": "EQP_FAIL"},
  {"label": "Changeover / Setup",     "value": "CHANGEOVER"},
  {"label": "Planned Maintenance",    "value": "PLANNED_MAINT"},
  {"label": "Unplanned Stop",         "value": "UNPLANNED_STOP"},
  {"label": "Material Starvation",    "value": "MATERIAL_WAIT"},
  {"label": "Quality Hold",           "value": "QUALITY_HOLD"}
]
```

- Property `props.placeholder`: `"Select downtime category..."`
- Bind the selected value to a custom view parameter: `{view.custom.selectedCategory}`

**2. Text Area — Notes**
- Component: `TextArea`
- Label: `"Notes (optional)"`
- Bind value to: `{view.custom.downtimeNotes}`
- Set `props.placeholder`: `"Describe the issue..."`

**3. Label — Equipment Display**
- Component: `Label`
- Property `props.text`: bind to `{view.params.equipmentName}` (passed when modal opens)

**4. Submit Button**
- Component: `Button`
- Property `props.text`: `"Log Downtime"`
- Style: set background color to `#D32F2F` (red) to signal an exception-logging action

### 5.3 Add the Submit Button Click Script

1. Select the Submit button
2. In the Property Editor: **Events** → **onClick** → **Edit Script**
3. Enter this Jython script:

```python
# Retrieve form values from the view's custom properties
category_code   = self.view.custom.selectedCategory
category_detail = self.view.custom.downtimeNotes
equipment_id    = self.view.params.equipmentId     # passed when modal is opened
entered_by      = system.security.getUsername()     # current logged-in operator

# Basic validation: require a category to be selected
if not category_code:
    system.perspective.openPopup(
        "ValidationError",
        "components/ValidationErrorModal",
        params={"message": "Please select a downtime category before submitting."}
    )
    return

# Calculate the downtime start time
# In production, the start time should come from when RunState went FALSE,
# stored in a memory tag. Here we use the form submission time as a fallback.
import system.date
started_at = system.date.now()

# Call the Named Query to insert the downtime event
try:
    system.db.runNamedQuery(
        "OEE/InsertDowntimeEvent",
        {
            "equipment_id":    equipment_id,
            "started_at":      started_at,
            "category_code":   category_code,
            "category_detail": category_detail,
            "entered_by":      entered_by
        }
    )
    # Close this modal on success
    system.perspective.closePopup("DowntimeEntryModal")

    # Optionally refresh the OEE dashboard component
    # self.session.props.custom.oeeRefreshTrigger += 1

except Exception as e:
    system.perspective.openPopup(
        "SubmitError",
        "components/ValidationErrorModal",
        params={"message": "Failed to save downtime event: " + str(e)}
    )
```

### 5.4 Open the Modal from a Dashboard Button

On the main OEE Dashboard view, add a **Button** component with this onClick script:

```python
# Pass the current equipment ID and name as parameters to the modal
system.perspective.openPopup(
    "DowntimeEntryModal",            # popup ID (must be unique per session)
    "components/DowntimeEntryModal", # view path
    params={
        "equipmentId":   4,          # replace with tag-bound equipment ID
        "equipmentName": "Line 3 (330ml PET)"
    },
    overlayDismiss=False  # prevent accidental close by clicking outside
)
```

---

## Part 6 — OEE Dashboard Binding (Quick Reference)

To wire the Named Query results to Perspective display components:

1. Select a **Label** component showing OEE %
2. In Property Editor: click the binding icon next to `props.text`
3. **Binding Type:** `Named Query`
4. **Named Query:** `OEE/GetShiftOEE`
5. **Parameters:**
   - `equipment_id`: `4`
   - `shift_date`: `{now}`
   - `shift_number`: `1`
6. **Return Format:** `First Row` → `Column: oee_pct`
7. Add a **Transform** → **Expression**: `format(value, '#0.0') + "%"`

Repeat for `availability_pct`, `performance_pct`, `quality_pct`.

For the rolling trend chart, use the Section 4 query (`rolling 7-day`) as the
data source for a **Time Series Chart** component with `shift_date + shift_number`
on the X axis and `oee_pct` on the Y axis.

---

## Summary of Click Paths

```
Gateway → Config → Databases → Connections → Create new → PostgreSQL
Gateway → Config → SQL Bridge → Transaction Groups → Create new → Triggered
Designer → Project → Named Queries → New Folder → New Named Query
Designer → Project Browser → views → New View → Popup → [add components]
Designer → [Button] → Events → onClick → Edit Script → [Jython]
```
