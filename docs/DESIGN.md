# Design Notes

Deeper detail behind the [README](../README.md): the data model, the cleaning
rules, and the reasoning. The README is the quick tour; this is the "why".

## Source data

Each session is **two files**:

- `metadata_session_*.json` — session context (vehicle, driver, location, times,
  sample rate, hardware/firmware, active sensors, notes).
- `field_session_*.csv` — the time series: `timestamp` (`DD/MM/YYYY HH:MM`),
  `wheel_angle` (deg, can be negative), `speed` (km/h), `reverse_state` (0/1).

## Data model

One-to-many: a `sessions` row owns many `samples` rows.

**`sessions`** — one per recording; `session_id` is the unique natural key.
Metadata fields plus `sensors_active` (JSON) and `ingested_at`.

**`samples`** — one per CSV line:

| Column | Notes |
|---|---|
| `session_id` FK | `ON DELETE CASCADE` |
| `row_index` | original CSV position; `UNIQUE(session_id, row_index)` |
| `timestamp` | nullable, indexed |
| `wheel_angle`, `speed` | **nullable** floats (bad/missing → `NULL` + flag) |
| `reverse_state` | nullable bool |
| `quality_flags` | JSON `{field: flag}`, **SQL `NULL`** when the row is clean |

Two design points worth calling out:

- **Sensor columns are nullable by necessity.** Missing / unparseable / sentinel
  readings become `NULL`, so any consumer must handle `NULL` regardless.
- **`quality_flags` uses `JSON(none_as_null=True)`.** SQLAlchemy otherwise stores
  Python `None` as the JSON literal `null` (a non-NULL value), which would make
  `quality_flags IS NOT NULL` match every row and break the "flagged only" filter.

## Cleaning rules

Implemented as **pure functions** in `backend/app/ingestion/cleaning.py` (no DB /
HTTP), so they are fast to unit-test.

| Input | Result | Flag |
|---|---|---|
| valid number | value | — |
| `""`, `na`, `null`, `None` | `NULL` | `missing` |
| `ERROR_TIMEOUT`, `NaN`, `inf` | `NULL` | `parse_error` |
| `-999`, `-9999`, `9999` | `NULL` | `sentinel` |
| far outside the IQR fence | **value kept** | `suspect_outlier` |
| unparseable timestamp | `NULL` ts | `parse_error` on `timestamp` |

**Never drop a row.** A dropped row destroys evidence and breaks the time series.
Flags keep the data auditable: you can always ask "why is this NULL?".

**Why keep outliers instead of nulling them?** It's reversible — a consumer can
hide/exclude a flagged value at read time, but a value nulled on write is lost.

### Regime-aware outlier detection

Outliers use the **IQR fence** `v < Q1 − k·IQR` or `v > Q3 + k·IQR`, with `k = 3`
(conservative: only "far out" points). Two refinements matter:

1. It runs as a **second pass** after per-field parsing, so already-flagged
   `NULL`s never skew the quartiles (a `-999` can't move the fence).
2. It is computed **per `reverse_state` group**, not globally. The data is
   *bimodal*: reverse driving creeps at ~10–12 km/h while forward driving is
   ~45–70. A single global fence flags the entire legitimate reverse segment.
   Grouping by regime removes those false positives while still catching genuine
   spikes within each regime (e.g. `125` km/h in reverse, `450` going forward).

On the provided sample this is the difference between **16 false positives** and
the **2 true outliers**.

## Ingestion semantics

- `POST /ingest` takes the metadata + CSV as multipart files.
- **Idempotent per `session_id`**: an existing session is deleted (cascade) and
  re-inserted inside **one transaction**, so re-runs/retries are safe and a
  failure rolls back cleanly.
- Schema is created on startup via `Base.metadata.create_all()` (no migration tool
  yet — Alembic is a documented next step).

## Schema handling & extensibility

The expected columns (`timestamp`, `wheel_angle`, `speed`, `reverse_state`) are
**hard-coded** at every layer — the cleaning functions, the `Sample` ORM model,
the API schema, and the dashboard. This is a deliberate trade-off
(**schema-on-write**), not an oversight.

**Consequences for a CSV with different columns:**
- **Extra** columns (e.g. `gps_lat`) are silently **ignored** — never read or stored.
- A **renamed/missing** expected column (e.g. `velocity` instead of `speed`) is
  read as absent → flagged `missing`, stored `NULL`; the real data is dropped.

No crash either way, but unknown columns are lost silently.

**Why static is the right call for this prototype:** a known format lets us apply
strong typing, domain-specific cleaning (speed outliers, `reverse_state` as bool,
steering sentinels), a clear API contract, indexed columns, and efficient
relational queries. For a single documented sensor layout, explicit-and-validated
beats generic-and-loose.

**Where it breaks down:** fleets add sensors and change formats over time, so a
static schema means each new channel needs a code change + a DB migration, and
unknown columns vanish silently. To generalize (in rough order of effort):

1. **Metadata-driven schema** — drive expected columns/types/cleaning rules from
   per-session-type config (the metadata already declares `sensors_active`),
   instead of hard-coded literals.
2. **Hybrid storage** — keep common channels as typed columns, put any extra
   channels in a GIN-indexed **JSONB** column; absorbs new sensors without a
   migration.
3. **Narrow "measurements" table** — `(session_id, timestamp, channel, value,
   quality_flag)`, one row per reading per channel. Fully generic (any number of
   channels, zero schema change to add one) and the classic time-series model;
   trades some type-safety and needs pivoting for analysis.
4. **Explicit validation, no silent drops** — validate incoming columns against a
   registered schema and *flag* unknown/missing ones rather than ignoring them.

## API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | liveness |
| `POST`| `/ingest` | ingest a metadata + CSV pair |
| `GET` | `/sessions` | list sessions |
| `GET` | `/sessions/{id}` | full metadata |
| `GET` | `/sessions/{id}/samples` | paginated samples (`limit`, `offset`, `flagged_only`) |
| `GET` | `/sessions/{id}/quality` | data-quality flag summary |

## Visualization strategy

The dashboard (Streamlit + Plotly) consumes the REST API — it isn't coupled to the
database, which keeps the analytics layer a normal API client.

- **Charts** plot `speed` and `wheel_angle` vs time. `NULL` values render as
  **gaps** (`connectgaps=False`), never interpolated zeros. `suspect_outlier`
  points are kept but **highlighted** so the value is visible and obviously flagged.
- **Key statistics** (samples, flagged %, clean avg/max speed, time in reverse) are
  computed over *clean* values (outliers excluded from aggregates, not from view).
- **Data-quality panel** surfaces the `/quality` summary so a reviewer immediately
  sees how trustworthy a session is.

## Scaling

See the README's "Scaling" section for the full plan. The headline moves:
queue/worker + object-storage ingestion, streaming parse + bulk `COPY`,
in-database quantiles, table partitioning, and server-side downsampling for charts.
