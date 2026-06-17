from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SessionSummary(BaseModel):
    """Lightweight session view for list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    vehicle_id: str | None = None
    driver_id: str | None = None
    test_location: str | None = None
    recording_date: str | None = None
    sample_rate_hz: float | None = None


class SessionDetail(SessionSummary):
    """Full session metadata."""

    start_time_utc: datetime | None = None
    end_time_utc: datetime | None = None
    hardware_version: str | None = None
    firmware_version: str | None = None
    sensors_active: list[str] | None = None
    notes: str | None = None
    ingested_at: datetime | None = None


class SampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_index: int
    timestamp: datetime | None = None
    wheel_angle: float | None = None
    speed: float | None = None
    reverse_state: bool | None = None
    quality_flags: dict[str, str] | None = None


class SamplePage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[SampleOut]


class QualityReport(BaseModel):
    session_id: str
    total_samples: int
    flagged_samples: int
    flag_counts: dict[str, int]
    field_flag_counts: dict[str, dict[str, int]]


class IngestResponse(BaseModel):
    session_id: str
    sample_count: int
    flag_counts: dict[str, int]
