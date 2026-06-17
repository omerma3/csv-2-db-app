# Database & Storage Design

## Engine

**PostgreSQL 16**, accessed via **SQLAlchemy 2.0** (ORM) with the
`psycopg2` driver. Run locally through Docker Compose:

```bash
docker compose up -d        # postgres on localhost:5432
```

| Setting | Value (dev) |
|---------|-------------|
| host / port | `localhost:5432` |
| db / user / password | `csv2db` / `csv2db` / `csv2db` |
| volume | `pgdata` (named volume — survives container restarts) |
| DSN | `postgresql+psycopg2://csv2db:csv2db@localhost:5432/csv2db` |

Why Postgres over SQLite: native `JSON`/`JSONB` columns for `quality_flags` and
`sensors_active`, real concurrency, and a clean path to production.

## Schema

Two tables in a one-to-many relationship (`sessions` 1 ─→ N `samples`).

### `sessions` — one row per recording (from metadata JSON)

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | surrogate key |
| `session_id` | text **unique**, indexed | natural key from metadata |
| `vehicle_id`, `driver_id`, `test_location`, `recording_date` | text | nullable |
| `start_time_utc`, `end_time_utc` | timestamptz | parsed from ISO-8601 |
| `sample_rate_hz` | float | |
| `hardware_version`, `firmware_version` | text | |
| `sensors_active` | JSON | array of sensor names |
| `notes` | text | |
| `ingested_at` | timestamptz | set at ingestion time |

### `samples` — one row per CSV line (cleaned telemetry)

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `session_id` | int FK → `sessions.id` `ON DELETE CASCADE`, indexed | |
| `row_index` | int | original 0-based position in the CSV |
| `timestamp` | timestamptz, indexed | nullable |
| `wheel_angle` | float | **nullable** (NULL = bad/missing, see flags) |
| `speed` | float | **nullable** |
| `reverse_state` | bool | nullable |
| `quality_flags` | JSON (`none_as_null=True`) | `{field: flag}` map; SQL `NULL` if the row is clean |

**Constraints / indexes**
- `UNIQUE (session_id, row_index)` — `uq_sample_session_row`; prevents duplicate
  rows and supports idempotent re-ingestion.
- Index on `samples.session_id` and `samples.timestamp` for the common
  "fetch a session's samples ordered by time" query.

### ER sketch

```
sessions (1) ───< samples (N)
  id (PK)            id (PK)
  session_id UQ      session_id (FK → sessions.id, CASCADE)
  ...metadata        row_index        ┐ UNIQUE(session_id, row_index)
                     timestamp        │
                     wheel_angle      │ nullable sensor columns
                     speed            │
                     reverse_state    ┘
                     quality_flags (JSON)
```

## Why sensor columns are nullable

Missing, unparseable, and sentinel readings are stored as `NULL` with a
`quality_flags` entry rather than dropped (see [data-design.md](data-design.md)).
Nullable columns are therefore a hard requirement, and any consumer must handle
`NULL` regardless of outlier policy.

### SQL `NULL` vs JSON `null` for `quality_flags`

A clean row has no flags. By default SQLAlchemy's `JSON` type writes Python
`None` as the JSON literal `null` (a non-NULL value), which breaks
`quality_flags IS NULL` / `IS NOT NULL` filtering — every row matches. We set
`JSON(none_as_null=True)` so a clean row stores a true SQL `NULL`, and the
`flagged_only` query (`quality_flags IS NOT NULL`) returns only flagged rows.

## Lifecycle & ingestion semantics

- **Table creation:** `init_db()` runs `Base.metadata.create_all()` on app
  startup — fine for dev. **No migrations yet.**
- **Idempotent ingestion:** re-ingesting an existing `session_id` deletes the old
  session (cascading to its samples) and re-inserts, all in **one transaction**
  (`loader.ingest`, `replace_existing=True`). A failure rolls back cleanly.

## File / object storage

Currently the raw uploaded CSV/JSON are **parsed in-memory and not retained** —
only the cleaned rows + metadata are persisted. If raw-file retention becomes a
requirement (re-processing, audit), the plan is:

- store originals in object storage (S3 / local `storage/` dir),
- add a `source_files` table with the storage key + checksum per session.

## Roadmap

- **Alembic migrations** to replace `create_all` before any shared/prod use.
- `JSONB` + GIN index on `quality_flags` if we start querying by flag at scale.
- Partition or time-bucket `samples` if sessions grow to millions of rows.
- Connection pooling tuning (currently defaults + `pool_pre_ping`).
