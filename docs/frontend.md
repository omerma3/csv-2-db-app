# Frontend Design

> **Status: planned / not yet implemented.** The `frontend/` directory is empty.
> This document is the design we'll build against; it will be updated as code lands.

## Goal

A lightweight web UI to **browse ingested sessions**, **inspect telemetry**, and —
crucially — **see and trust the data quality** produced by the cleaning pipeline.

## Proposed stack

| Concern | Choice | Why |
|---------|--------|-----|
| Framework | **React + Vite + TypeScript** | fast dev server, typed API contracts |
| Data fetching | TanStack Query | caching, loading/error states for the REST API |
| Charts | Recharts (or uPlot for large series) | line charts with gap/flag overlays |
| Styling | Tailwind CSS | quick, consistent UI |

The frontend is a pure client of the backend REST API
([backend.md](backend.md)); no shared server state.

## Screens

### 1. Sessions list — `/`
- Table from `GET /sessions`: `session_id`, vehicle, driver, location, date.
- Each row links to the session detail.
- (Later) an "Upload" action posting to `POST /ingest`.

### 2. Session detail — `/sessions/:id`
Pulls `GET /sessions/{id}`, `/samples`, and `/quality`.
- **Metadata panel** — vehicle/driver/hardware/firmware, sensors, notes.
- **Quality banner** — from `/quality`: `flagged_samples / total_samples`, with a
  per-flag breakdown (`missing`, `parse_error`, `sentinel`, `suspect_outlier`).
- **Telemetry charts** — `wheel_angle` and `speed` vs `timestamp`:
  - `NULL` values render as **gaps** (not zeros).
  - `suspect_outlier` points are **highlighted** (e.g. red marker) since the raw
    value is preserved — the UI decides whether to show or hide them.
- **Samples table** — paginated (`limit`/`offset`), with a `flagged_only` toggle
  mapping to the API param; flagged cells are visually marked with their flag.

## Handling NULL & flags (the important part)

Because the pipeline never drops rows and nulls bad values (see
[data-design.md](data-design.md)), the frontend must treat `NULL` as
"no reading":

- charts: break the line at `NULL` rather than plotting `0`;
- tables: show a muted "—" plus the flag from `quality_flags`;
- outliers: keep the value visible but styled, with a toggle to exclude them from
  any client-side aggregates (avg speed, etc.).

## API contract (consumed)

| Call | Used by |
|------|---------|
| `GET /sessions` | sessions list |
| `GET /sessions/{id}` | metadata panel |
| `GET /sessions/{id}/samples?limit&offset&flagged_only` | charts + table |
| `GET /sessions/{id}/quality` | quality banner |
| `POST /ingest` | upload action (later) |

CORS must be enabled on the backend for the Vite dev origin
(`http://localhost:5173`) — a `CORSMiddleware` entry is a prerequisite task.

## Proposed layout (when built)

```
frontend/
├── index.html
├── package.json
├── vite.config.ts
└── src/
    ├── main.tsx
    ├── api/client.ts          # typed fetch wrappers
    ├── pages/SessionsList.tsx
    ├── pages/SessionDetail.tsx
    └── components/
        ├── TelemetryChart.tsx
        ├── QualityBanner.tsx
        └── SamplesTable.tsx
```

## Build order

1. Scaffold Vite + Tailwind + TanStack Query; typed API client.
2. Sessions list → session detail (metadata + samples table).
3. Telemetry charts with NULL gaps + outlier highlighting.
4. Quality banner.
5. Upload flow (`POST /ingest`).
