"""Parse CSV + metadata JSON into the database."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app import models
from app.ingestion.cleaning import clean_rows, clean_timestamp


def parse_metadata(raw_json: str | bytes) -> dict:
    """Parse a metadata_*.json payload into a plain dict."""
    return json.loads(raw_json)


def parse_csv(raw_csv: str | bytes) -> list[dict[str, str]]:
    """Parse a field_*.csv payload into a list of raw row dicts."""
    if isinstance(raw_csv, bytes):
        raw_csv = raw_csv.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw_csv))
    return [dict(row) for row in reader]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class IngestionResult:
    def __init__(self, session: models.Session, sample_count: int, flag_counts: dict):
        self.session = session
        self.sample_count = sample_count
        self.flag_counts = flag_counts


def ingest(
    db: DbSession,
    *,
    metadata_raw: str | bytes,
    csv_raw: str | bytes,
    replace_existing: bool = True,
) -> IngestionResult:
    """Ingest one session's metadata + CSV into the database.

    If a session with the same session_id already exists and replace_existing
    is True, its samples are replaced (idempotent re-ingestion).
    """
    meta = parse_metadata(metadata_raw)
    session_id = meta.get("session_id")
    if not session_id:
        raise ValueError("metadata is missing required field 'session_id'")

    existing = db.execute(
        select(models.Session).where(models.Session.session_id == session_id)
    ).scalar_one_or_none()

    if existing is not None:
        if not replace_existing:
            raise ValueError(f"session '{session_id}' already exists")
        db.delete(existing)
        db.flush()

    session = models.Session(
        session_id=session_id,
        vehicle_id=meta.get("vehicle_id"),
        driver_id=meta.get("driver_id"),
        test_location=meta.get("test_location"),
        recording_date=meta.get("recording_date"),
        start_time_utc=_parse_iso(meta.get("start_time_utc")),
        end_time_utc=_parse_iso(meta.get("end_time_utc")),
        sample_rate_hz=meta.get("sample_rate_hz"),
        hardware_version=meta.get("hardware_version"),
        firmware_version=meta.get("firmware_version"),
        sensors_active=meta.get("sensors_active"),
        notes=meta.get("notes"),
        ingested_at=clean_timestamp(None) or datetime.now(timezone.utc),
    )
    db.add(session)
    db.flush()  # assign session.id

    rows = parse_csv(csv_raw)
    cleaned = clean_rows(rows)

    flag_counts: dict[str, int] = {}
    for c in cleaned:
        db.add(
            models.Sample(
                session_id=session.id,
                row_index=c.row_index,
                timestamp=c.timestamp,
                wheel_angle=c.wheel_angle,
                speed=c.speed,
                reverse_state=c.reverse_state,
                quality_flags=c.quality_flags or None,
            )
        )
        for flag in c.quality_flags.values():
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    db.commit()
    db.refresh(session)
    return IngestionResult(session, len(cleaned), flag_counts)
