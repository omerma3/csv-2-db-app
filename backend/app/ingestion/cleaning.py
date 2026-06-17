"""Cleaning and validation rules for raw telemetry rows.

Guiding principle: never drop a row. Missing, unparseable, and sentinel
values become NULL with an explanatory flag; outliers keep their raw value
with a flag. Every transformation is therefore auditable downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# --- Quality flag vocabulary -------------------------------------------------

FLAG_MISSING = "missing"  # cell was empty
FLAG_PARSE_ERROR = "parse_error"  # cell held a non-numeric token (ERROR_TIMEOUT, NaN)
FLAG_SENTINEL = "sentinel"  # cell held a sentinel "no data" code (-999)
FLAG_SUSPECT_OUTLIER = "suspect_outlier"  # value kept, but statistically implausible

# Tokens (compared case-insensitively) that mean "no usable value".
_MISSING_TOKENS = {"", "na", "n/a", "null", "none"}
_PARSE_ERROR_TOKENS = {"nan", "error", "error_timeout", "inf", "-inf", "#err"}

# Sentinel numeric codes that instruments emit to mean "no reading".
_SENTINELS = {-999.0, -9999.0, 9999.0}

# Timestamp formats accepted, tried in order. Source data uses DD/MM/YYYY HH:MM.
_TS_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S")


@dataclass
class FieldResult:
    """Outcome of cleaning a single field: a value (or None) plus an optional flag."""

    value: float | None
    flag: str | None = None


def clean_numeric(raw: str | None) -> FieldResult:
    """Clean a raw numeric cell into a float value or a flagged NULL."""
    if raw is None:
        return FieldResult(None, FLAG_MISSING)

    token = str(raw).strip()
    low = token.lower()

    if low in _MISSING_TOKENS:
        return FieldResult(None, FLAG_MISSING)
    if low in _PARSE_ERROR_TOKENS:
        return FieldResult(None, FLAG_PARSE_ERROR)

    try:
        value = float(token)
    except (ValueError, TypeError):
        return FieldResult(None, FLAG_PARSE_ERROR)

    # NaN/inf can slip through float() depending on the token.
    if value != value or value in (float("inf"), float("-inf")):
        return FieldResult(None, FLAG_PARSE_ERROR)
    if value in _SENTINELS:
        return FieldResult(None, FLAG_SENTINEL)

    return FieldResult(value, None)


def clean_timestamp(raw: str | None) -> datetime | None:
    """Parse a timestamp cell; return None if empty/unparseable."""
    if raw is None:
        return None
    token = str(raw).strip()
    if not token:
        return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(token, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def clean_reverse_state(raw: str | None) -> bool | None:
    """Parse the reverse_state flag (0/1, true/false) into a bool or None."""
    if raw is None:
        return None
    token = str(raw).strip().lower()
    if token in ("1", "true", "t", "yes", "y"):
        return True
    if token in ("0", "false", "f", "no", "n"):
        return False
    return None


def detect_outliers_iqr(values: list[float | None], k: float = 3.0) -> list[bool]:
    """Flag statistical outliers via the IQR rule (data-driven, no fixed bounds).

    A value is an outlier if it falls outside [Q1 - k*IQR, Q3 + k*IQR].
    None entries are never outliers. k=3.0 is conservative (only "far out"
    points), so normal driving variation is not flagged.
    """
    present = sorted(v for v in values if v is not None)
    out = [False] * len(values)
    if len(present) < 4:
        return out  # too few points for a meaningful quartile estimate

    q1 = _percentile(present, 25)
    q3 = _percentile(present, 75)
    iqr = q3 - q1
    if iqr == 0:
        return out  # no spread -> IQR rule is undefined / degenerate

    lo, hi = q1 - k * iqr, q3 + k * iqr
    for i, v in enumerate(values):
        if v is not None and (v < lo or v > hi):
            out[i] = True
    return out


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolation percentile over an already-sorted list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


# Numeric sensor columns subjected to outlier detection.
_OUTLIER_COLUMNS = ("wheel_angle", "speed")


@dataclass
class CleanedSample:
    row_index: int
    timestamp: datetime | None
    wheel_angle: float | None
    speed: float | None
    reverse_state: bool | None
    quality_flags: dict[str, str] = field(default_factory=dict)


def clean_rows(raw_rows: list[dict[str, str]]) -> list[CleanedSample]:
    """Clean a list of raw CSV row dicts into validated CleanedSample objects.

    Two passes: (1) per-field parsing of each row, (2) per-column IQR outlier
    detection across the session, flagging suspect values without nulling them.
    """
    cleaned: list[CleanedSample] = []

    for i, row in enumerate(raw_rows):
        angle = clean_numeric(row.get("wheel_angle"))
        speed = clean_numeric(row.get("speed"))
        sample = CleanedSample(
            row_index=i,
            timestamp=clean_timestamp(row.get("timestamp")),
            wheel_angle=angle.value,
            speed=speed.value,
            reverse_state=clean_reverse_state(row.get("reverse_state")),
        )
        if angle.flag:
            sample.quality_flags["wheel_angle"] = angle.flag
        if speed.flag:
            sample.quality_flags["speed"] = speed.flag
        if row.get("timestamp") and sample.timestamp is None:
            sample.quality_flags["timestamp"] = FLAG_PARSE_ERROR
        cleaned.append(sample)

    # Pass 2: outlier detection per numeric column, *within each driving regime*.
    #
    # Telemetry is bimodal by reverse_state: in reverse the vehicle creeps
    # (~10-12 km/h) while forward speeds are far higher (~45-70). A single
    # global IQR fence would flag the entire legitimate reverse segment as
    # outliers. Computing the fence separately per reverse_state group keeps
    # normal low-speed reverse data unflagged while still catching genuine
    # spikes within each regime (e.g. 125 in reverse, 450 in forward).
    for col in _OUTLIER_COLUMNS:
        for indices in _group_indices_by_regime(cleaned):
            group_values = [getattr(cleaned[i], col) for i in indices]
            for local_i, is_outlier in enumerate(detect_outliers_iqr(group_values)):
                sample = cleaned[indices[local_i]]
                # Don't override an existing flag (e.g. a parse_error stays NULL).
                if is_outlier and col not in sample.quality_flags:
                    sample.quality_flags[col] = FLAG_SUSPECT_OUTLIER

    return cleaned


def _group_indices_by_regime(samples: list[CleanedSample]) -> list[list[int]]:
    """Group sample indices by driving regime (reverse_state: True/False/None)."""
    groups: dict[bool | None, list[int]] = {}
    for i, s in enumerate(samples):
        groups.setdefault(s.reverse_state, []).append(i)
    return list(groups.values())
