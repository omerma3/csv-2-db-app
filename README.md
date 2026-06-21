# Field Test Ingestion & Analytics

A local prototype that **ingests** raw field-test telemetry (CSV + metadata JSON),
**cleans** the real-world data-quality issues in it, **stores** it in PostgreSQL,
and **visualizes** it with an analytics dashboard.

- **Backend** — FastAPI service: parse → clean/validate → persist → serve (REST).
- **Storage** — PostgreSQL (via SQLAlchemy), run with Docker Compose.
- **Frontend** — Streamlit dashboard: charts, key statistics, and a data-quality view.

```
CSV + metadata ──▶ FastAPI /ingest ──▶ cleaning pipeline ──▶ PostgreSQL ──▶ REST API ──▶ Streamlit dashboard
```

---

## Quick start

**Only Docker is required.** One command brings up all three services (Postgres,
API, dashboard):

```bash
docker compose up --build
```

Then open the dashboard at **http://localhost:8501**, and **upload
`sample_data/field_session_042.csv` + `metadata_session_042.json`** from the
sidebar to ingest the sample session.

- Dashboard: http://localhost:8501
- API + interactive docs: http://localhost:8000/docs

```bash
# Optional: ingest the sample from the CLI instead of the UI
curl -X POST http://localhost:8000/ingest \
  -F "metadata=@sample_data/metadata_session_042.json;type=application/json" \
  -F "csv=@sample_data/field_session_042.csv;type=text/csv"

# Run the tests (inside the backend container)
docker compose exec backend pytest -q
```

<details>
<summary>Run without Docker (local dev)</summary>

Requires Python 3.12 and a reachable Postgres. With the DB container running
(`docker compose up -d db`):

```bash
cd backend && pip install -r requirements.txt && cp .env.example .env
uvicorn app.main:app --port 8000                 # API on :8000

cd frontend && pip install -r requirements.txt
streamlit run app.py                             # dashboard on :8501
```
</details>

---

## How the data is handled

The sample file is intentionally messy. Each defect is **detected, not dropped** —
the value becomes `NULL` (or is kept, for outliers) with an explanatory
**quality flag**, so every transformation is auditable.

| Defect (in the sample) | Handling | Flag |
|---|---|---|
| Empty cell | → `NULL` | `missing` |
| Bad string (`ERROR_TIMEOUT`, `NaN`) | → `NULL` | `parse_error` |
| Sentinel `-999` | → `NULL` | `sentinel` |
| Outlier (speed `125`, `450`) | **keep raw value** | `suspect_outlier` |

**Guiding principle: never drop a row.** Outliers keep their value because that's
reversible (a consumer can hide them on read); a value nulled on write is gone.

Outliers are found with a **regime-aware IQR fence** — computed *separately per
`reverse_state` group*, because the data is bimodal (slow reverse ~10–12 km/h vs.
forward ~45–70). A global fence would wrongly flag all legitimate reverse driving.

See **[docs/DESIGN.md](docs/DESIGN.md)** for the full rationale, schema, and rules.

---

## Architecture & key choices

| Choice | Why |
|---|---|
| **FastAPI** | Async, typed, auto-generated OpenAPI docs; clean router/service split |
| **PostgreSQL** | Relational fits the session→samples model; native JSON for flags/metadata; real concurrency; clear path to production |
| **SQLAlchemy** | ORM for clarity now, with an escape hatch to bulk `COPY` for scale |
| **Streamlit** | Fastest path to an analytics UI with rich charts + stats — best speed/insight balance for a prototype |
| **Pure cleaning module** | `ingestion/cleaning.py` has no DB/HTTP deps, so the data-quality rules are unit-tested in milliseconds |

Ingestion is **idempotent** per `session_id` (re-ingesting replaces, in one
transaction), so re-runs and retries are safe.

---

## Scaling to thousands of files & much longer recordings

The prototype is correct but deliberately simple; here is how it would scale.

**Ingestion throughput (1 → thousands of files)**
- Replace the synchronous HTTP ingest with an **object-storage landing zone**
  (e.g. S3) + **event/queue-driven workers** (the API just enqueues).
- Idempotency is already keyed on `session_id` (unique constraint) + the
  `(session_id, row_index)` unique key, so retries and duplicates are safe.
- Add a **dead-letter path** for malformed files instead of failing a batch.

**Long files (memory & speed)**
- **Stream-parse** the CSV instead of reading it fully into memory.
- Insert with **`COPY` / bulk insert** rather than row-by-row ORM (orders of
  magnitude faster).
- The outlier pass currently needs the whole column in memory. At scale, compute
  quartiles **in the database** (window functions / approximate quantiles) or in a
  chunked streaming pass, so memory stays bounded.

**Storage & query at volume**
- **Partition** `samples` by session/time; consider a time-series extension
  (TimescaleDB) for very long recordings.
- **Downsample** server-side for charts (a million points can't go to the browser);
  pre-compute per-session aggregates into a summary table / materialized view.

**Serving**
- Pagination already exists; add caching of quality/stat aggregates and
  appropriate indexes (some are already in place).

---

## What I'd do next (timeboxed exercise)

- **Automated DB/API integration test** that ingests `sample_data/` and asserts
  flag counts (the cleaning logic is unit-tested; the persistence path is verified
  manually).
- **Alembic migrations** (schema is currently `create_all` on startup).
- **CORS / auth** and a proper config for non-local deployment.
- A first slice of the scaling work above (bulk `COPY`, streaming parse).

---

## Repo layout

```
backend/          FastAPI service
  app/
    ingestion/    cleaning rules (pure) + loader (parse + persist)
    routers/      /ingest, /sessions
    models.py     ORM: Session, Sample
  tests/          cleaning unit tests
  Dockerfile
frontend/         Streamlit dashboard (app.py)
  Dockerfile
sample_data/      provided sample session
docs/DESIGN.md    detailed design, schema, and data-quality rationale
docker-compose.yml   orchestrates all 3 services (db + backend + frontend)
```
