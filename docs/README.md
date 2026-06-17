# csv-2-db-app — Documentation

Ingest vehicle-telemetry CSV files + their metadata JSON sidecar, clean and
validate the messy sensor data, store it in PostgreSQL, and serve it via a
FastAPI API (with a planned web frontend).

## Design docs

| Doc | Covers |
|-----|--------|
| [data-design.md](data-design.md) | Source file shapes, the dirty-data problem, cleaning rules, quality-flag model |
| [backend.md](backend.md) | FastAPI service layout, layers, API surface, config, running & testing |
| [db-and-storage.md](db-and-storage.md) | Postgres schema, constraints/indexes, ingestion semantics, storage roadmap |
| [frontend.md](frontend.md) | Planned React UI: screens, NULL/flag handling, API contract |

## Core principle

**Never drop a row.** Missing, unparseable, and sentinel values become `NULL`
with an explanatory quality flag; outliers keep their raw value plus a flag.
Every transformation is auditable. See [data-design.md](data-design.md).

## Quick start

```bash
docker compose up -d                       # Postgres
cd backend
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload              # API + Swagger at /docs

python -m pytest -q                        # run tests
```
