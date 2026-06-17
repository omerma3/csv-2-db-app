# Backend Design

The backend is a **FastAPI** service that ingests vehicle-telemetry files, cleans
and validates them, persists them to **PostgreSQL** (via SQLAlchemy), and serves
them over a JSON API.

## Layout

```
backend/
├── requirements.txt
├── .env.example            # DATABASE_URL
└── app/
    ├── main.py             # FastAPI app + lifespan (init_db) + /health
    ├── config.py           # Settings (pydantic-settings, reads .env)
    ├── database.py         # SQLAlchemy engine, SessionLocal, Base, get_db, init_db
    ├── models.py           # ORM: Session, Sample
    ├── schemas.py          # Pydantic request/response models
    ├── ingestion/
    │   ├── cleaning.py     # cleaning rules + IQR outlier detection (pure, testable)
    │   └── loader.py       # parse CSV + metadata JSON, persist to DB
    └── routers/
        ├── ingest.py       # POST /ingest
        └── sessions.py     # GET /sessions, /{id}, /{id}/samples, /{id}/quality
```

## Layered responsibilities

| Layer | Module | Responsibility |
|-------|--------|----------------|
| Transport | `routers/*` | HTTP shape, status codes, validation, pagination |
| Orchestration | `ingestion/loader.py` | parse files, call cleaning, write rows in a transaction |
| Domain logic | `ingestion/cleaning.py` | **pure functions** — no DB, no HTTP; fully unit-tested |
| Persistence | `models.py`, `database.py` | ORM + engine/session lifecycle |
| Contracts | `schemas.py` | serialization boundary; ORM never leaks directly |

`cleaning.py` is deliberately dependency-free so the data-quality rules can be
tested in milliseconds (`tests/test_cleaning.py`) without a database.

## API surface

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/health` | liveness probe |
| `POST` | `/ingest` | upload a `metadata` JSON + `csv` file pair (multipart) |
| `GET`  | `/sessions` | list ingested sessions |
| `GET`  | `/sessions/{session_id}` | full session metadata |
| `GET`  | `/sessions/{session_id}/samples` | paginated samples (`limit`, `offset`, `flagged_only`) |
| `GET`  | `/sessions/{session_id}/quality` | data-quality flag summary |

Interactive docs are auto-generated at `/docs` (Swagger) and `/redoc`.

## Configuration

All config comes from environment / `.env` via `app/config.py`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+psycopg2://csv2db:csv2db@localhost:5432/csv2db` | SQLAlchemy DSN |

## Running locally

```bash
docker compose up -d                 # start Postgres
cd backend
python -m venv .venv && .venv/Scripts/activate   # (Windows)
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload        # http://localhost:8000/docs
```

Tables are created on startup by `init_db()`; no migration tool yet (see
[db-and-storage.md](db-and-storage.md) for the migration roadmap).

## Testing

```bash
cd backend && python -m pytest -q
```

`test_cleaning.py` covers every cleaning rule (missing / parse_error / sentinel /
outlier) and the no-row-dropped invariant. DB/API integration tests are a
follow-up (see [data-design.md](data-design.md)).

## Error handling

- Bad input (missing `session_id`, malformed files) → `400` with a message.
- Unknown session → `404`.
- Ingestion is **idempotent**: re-ingesting an existing `session_id` replaces its
  samples inside a single transaction (`replace_existing=True`).
