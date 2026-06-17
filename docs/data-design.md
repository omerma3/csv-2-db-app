# Data Design

This document describes the **shape of the data**, the **cleaning/validation
rules**, and the **quality-flag model** that the ingestion pipeline applies.

## Source files

Each recording session arrives as **two files**:

### 1. `metadata_session_*.json` — session metadata
```json
{
  "session_id": "field_session_042",
  "vehicle_id": "V-PROTO-09",
  "driver_id": "D-8832",
  "test_location": "Handling Track A, Proving Grounds",
  "recording_date": "2026-06-09",
  "start_time_utc": "2026-06-09T10:00:00Z",
  "end_time_utc": "2026-06-09T10:01:39Z",
  "sample_rate_hz": 1,
  "hardware_version": "v2.1.4",
  "firmware_version": "fw_beta_0.9",
  "sensors_active": ["steering_angle_sensor", "obd2_speed", "reverse_state"],
  "notes": "..."
}
```

### 2. `field_session_*.csv` — time-series telemetry
```
timestamp,wheel_angle,speed,reverse_state
09/06/2026 10:00,-2.83,51.02,0
```

| Column | Type | Notes |
|--------|------|-------|
| `timestamp` | datetime | format `DD/MM/YYYY HH:MM` |
| `wheel_angle` | float (deg) | steering angle; can be negative |
| `speed` | float (km/h) | vehicle speed |
| `reverse_state` | bool | `1` = in reverse, `0` = forward |

## The central problem: dirty data

Real sensor exports are messy. The sample file deliberately contains every
failure mode the pipeline must survive:

| Defect | Example (row in sample) | Cause |
|--------|-------------------------|-------|
| Empty cell | `speed` rows 14–16, `wheel_angle` rows 57–59 | dropped reading |
| Error token | `ERROR_TIMEOUT` (row 67) | sensor/bus timeout |
| `NaN` literal | `wheel_angle` (row 90) | upstream float NaN serialized as text |
| Sentinel code | `-999` (row 80) | "no data" magic number |
| Outlier | `speed` 125 (row 30), 450 (row 44) | glitch / spike vs ~50–70 cluster |

## Guiding principle: **never drop a row**

Dropping rows silently destroys evidence and breaks time alignment. Instead,
every defect is recorded:

- **Missing / unparseable / sentinel** → column value becomes `NULL` **and** a
  flag is attached explaining why.
- **Outlier** → the **raw value is kept** and a flag is attached. (Reversible:
  consumers can hide/null flagged values at read time, but a value nulled at
  write time is lost forever.)

This makes the entire transformation **auditable** — you can always ask "why is
this NULL?" and get an answer from the flag.

## Cleaning rules (implemented in `app/ingestion/cleaning.py`)

| Rule | Input | Output value | Flag |
|------|-------|--------------|------|
| valid number | `"51.02"` | `51.02` | — |
| empty | `""`, `None`, `na`, `null` | `NULL` | `missing` |
| error token | `ERROR_TIMEOUT`, `NaN`, `inf` | `NULL` | `parse_error` |
| sentinel | `-999`, `-9999`, `9999` | `NULL` | `sentinel` |
| outlier | far outside IQR fence | **raw value kept** | `suspect_outlier` |
| timestamp parse fail | `"not-a-date"` | `NULL` ts | `parse_error` (on `timestamp`) |

### Outlier detection — regime-aware IQR, not fixed bounds

Fixed thresholds (e.g. "speed > 200") are arbitrary and brittle across vehicles
and tracks. Instead we use the **interquartile-range fence**:

```
outlier  ⇔  v < Q1 − k·IQR   or   v > Q3 + k·IQR        (k = 3.0)
```

- `k = 3.0` is conservative ("far out" points only) so normal driving variation
  is not flagged.
- Needs ≥ 4 present values and non-zero IQR, else it is skipped.
- Runs in a **second pass** after per-field parsing, so `NULL`/flagged cells are
  excluded from the quartile estimate (a `-999` can't skew the fence).

**Computed per driving regime, not per whole session.** Telemetry is *bimodal*
by `reverse_state`: in reverse the vehicle creeps (~10–12 km/h) while forward
speeds are far higher (~45–70). A single global fence would flag the entire
legitimate reverse segment as outliers. So the fence is computed **separately
within each `reverse_state` group** — keeping normal slow-reverse data unflagged
while still catching genuine spikes within each regime (e.g. `125` in reverse,
`450` in forward). On the sample file this is the difference between 16 false
positives and the 2 true outliers.

## Quality-flag vocabulary

Flags are stored per-field in `samples.quality_flags` as JSON, e.g.:

```json
{ "speed": "parse_error", "wheel_angle": "suspect_outlier" }
```

| Flag | Meaning | Value stored |
|------|---------|--------------|
| `missing` | cell was empty | `NULL` |
| `parse_error` | non-numeric / NaN / unparseable | `NULL` |
| `sentinel` | instrument "no-data" code | `NULL` |
| `suspect_outlier` | statistically implausible | **raw value** |

## Quality report

`GET /sessions/{id}/quality` aggregates flags into:

- `total_samples`, `flagged_samples`
- `flag_counts` — totals per flag type
- `field_flag_counts` — breakdown per field per flag

This gives a fast health read on any ingested session.

## Future data-design work

- Per-session configurable cleaning (sentinels, IQR `k`, physical bounds).
- Cross-field validation (e.g. `reverse_state=1` should imply low/negative speed).
- Gap detection vs. declared `sample_rate_hz`.
- DB + API integration tests asserting flag counts against the sample file.
