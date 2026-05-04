# Project 1 — Manufacturing OEE Analytics

**Domain:** Discrete Manufacturing (Bottling / Packaging Line)
**Stack:** Ignition 8.1, PostgreSQL 15, ISA-95, Perspective

---

## What Is OEE and Why Does It Matter for SI Work?

Overall Equipment Effectiveness (OEE) is the single most widely cited KPI in
discrete manufacturing. It measures how effectively a piece of equipment (or
a production line) is utilized relative to its theoretical maximum.

```
OEE = Availability × Performance × Quality

Where:
  Availability  = (Planned Production Time − Downtime) / Planned Production Time
  Performance   = (Ideal Cycle Time × Total Count) / Run Time
  Quality       = Good Count / Total Count
```

**World-class OEE is generally considered ≥ 85%.** Most plants run between 40–60%,
meaning there is enormous value-add available to an SI who can help them instrument,
visualize, and act on this metric.

**Why it matters for SI work:**

- OEE is usually the *first* deliverable a manufacturing client asks for when
  modernizing their SCADA system
- It provides a clear, quantifiable ROI story (e.g., "moving from 52% to 61% OEE
  on a line producing $2M/day recovers $3.3M/year")
- It forces a disciplined conversation about data quality, sensor placement,
  PLC signal mapping, and shift scheduling — all core SI competencies
- Ignition's Transaction Groups + Named Queries are purpose-built for this pattern

---

## ISA-95 Equipment Model Mapping

ISA-95 (ANSI/ISA-95, also IEC 62264) defines a hierarchical equipment model for
manufacturing enterprises. Every table and column in this schema maps to a level
in that hierarchy:

```
ISA-95 Level    │ Level # │ Schema Representation
────────────────┼─────────┼──────────────────────────────────────────────────
Enterprise      │    1    │ equipment_hierarchy WHERE isa95_level = 1
                │         │ e.g., "Acme Beverage Co."
────────────────┼─────────┼──────────────────────────────────────────────────
Site            │    2    │ equipment_hierarchy WHERE isa95_level = 2
                │         │ e.g., "Denver Plant"
────────────────┼─────────┼──────────────────────────────────────────────────
Area            │    3    │ equipment_hierarchy WHERE isa95_level = 3
                │         │ e.g., "Bottling Hall A"
────────────────┼─────────┼──────────────────────────────────────────────────
Work Center     │    4    │ equipment_hierarchy WHERE isa95_level = 4
                │         │ e.g., "Line 3 (330ml PET)"
────────────────┼─────────┼──────────────────────────────────────────────────
Work Unit       │    5    │ equipment_hierarchy WHERE isa95_level = 5
                │         │ e.g., "Filler Head Assembly"
```

The `production_runs` and `downtime_events` tables link to `equipment_hierarchy`
via `equipment_id`, so every production event is precisely located in the ISA-95
tree. This enables aggregate OEE rollups at any level — from a single work unit
up to an entire site.

### Downtime Category Mapping to ISA-95 / SEMI E10

The `downtime_categorization` CTE in `downtime_queries.sql` maps downtime event
codes to a subset of ISA-95 / SEMI E10 categories:

| `category_code` | ISA-95 / SEMI E10 Category | Description |
|-----------------|---------------------------|-------------|
| `EQP_FAIL`      | Equipment Failure          | Unplanned mechanical or electrical breakdown |
| `CHANGEOVER`    | Scheduled Downtime         | Product or format changeover between runs |
| `PLANNED_MAINT` | Scheduled Downtime         | Preventive maintenance per schedule |
| `UNPLANNED_STOP`| Unscheduled Downtime       | Unexplained stop, requires investigation |
| `MATERIAL_WAIT` | Scheduled Downtime (starve)| Waiting for upstream material supply |
| `QUALITY_HOLD`  | Non-Scheduled Downtime     | Line stopped pending quality disposition |

These codes are what an operator enters in the Perspective downtime form and what
analysts use to build Pareto charts of downtime root causes.

---

## Integration with Ignition Database Transaction Groups

Ignition's **Database Transaction Groups** (under Legacy Devices or via Tag History
in Ignition 8.1+) are the primary mechanism for writing structured production data
to a relational database on a scan-cycle basis.

**How this SQL schema integrates:**

1. **Transaction Group — Production Counter Write**
   - Trigger: Time-based (every 60 seconds) or event-based (shift end)
   - Source tags: `[BottlingLine]Line3/TotalCount`, `GoodCount`, `RunState`
   - Target table: `production_runs`
   - Action: INSERT or UPDATE the active run row

2. **Transaction Group — Downtime Auto-Detection**
   - Trigger: Tag change on `RunState` (PLC bit goes FALSE)
   - Source tags: `[BottlingLine]Line3/RunState`, `EquipmentID`
   - Target table: `downtime_events` — inserts a new row with `started_at = NOW()`
   - On `RunState` returning TRUE: UPDATE `downtime_events SET ended_at = NOW(),
     duration_min = EXTRACT(EPOCH FROM (NOW() - started_at)) / 60`

3. **Named Queries — OEE Dashboard Reads**
   - Named queries in Ignition call the SQL in `downtime_queries.sql`
   - Perspective binds component data to named query results
   - The rolling 7-day trend feeds a time-series chart component
   - The bottleneck analysis feeds a bar chart of top contributors

4. **Named Query — Downtime Form Submit**
   - Operator fills the Perspective modal form (category, notes, equipment)
   - Submit button calls a Named Query (INSERT INTO downtime_events ...)
   - Named queries run with parameterized inputs — no SQL injection risk

---

## What a Prospective Client or Employer Should Take Away

**For a manufacturing client:**
> "This engineer understands ISA-95 — they won't build a flat OEE table that
> breaks when you expand to a second line. They know how to wire a PLC counter
> to a PostgreSQL row, and they can build the Pareto analysis that tells you
> which downtime category to address first."

**For an SI interviewer:**
> "This candidate knows the difference between Transaction Groups and Tag History,
> can write production-quality SQL (CTEs, window functions, proper indexing), and
> has thought through the operator workflow — not just the data model. They also
> know ISA-95, which means they can have a credible conversation with plant
> engineers and operations managers."

**Key differentiators this project demonstrates:**
- ISA-95 data modeling (not just flat tables)
- Advanced PostgreSQL (CTEs, window functions, `FILTER` clauses)
- Understanding of OEE as a *business* metric, not just a formula
- Ignition-specific patterns (Transaction Groups, Named Queries)
- Operator UX thinking (downtime entry form, category codes)

---

## Files in This Directory

| File | Purpose |
|------|---------|
| `downtime_queries.sql` | Full PostgreSQL schema + OEE CTEs + rolling trend + bottleneck analysis |
| `Designer_Tutorial_OEE.md` | Step-by-step Ignition Designer click-path instructions |
| `README.md` | This file |
