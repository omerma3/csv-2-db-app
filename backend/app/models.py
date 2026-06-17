from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Session(Base):
    """A recording session, sourced from a metadata_*.json sidecar file."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    vehicle_id: Mapped[str | None] = mapped_column(String, nullable=True)
    driver_id: Mapped[str | None] = mapped_column(String, nullable=True)
    test_location: Mapped[str | None] = mapped_column(String, nullable=True)
    recording_date: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_time_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sample_rate_hz: Mapped[float | None] = mapped_column(Float, nullable=True)
    hardware_version: Mapped[str | None] = mapped_column(String, nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String, nullable=True)
    sensors_active: Mapped[list | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    samples: Mapped[list["Sample"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class Sample(Base):
    """A single telemetry row from a field_*.csv file.

    Sensor columns are nullable: missing / unparseable / sentinel values
    are stored as NULL with an explanatory flag in `quality_flags`, so no
    row is ever dropped and every transformation is auditable.
    """

    __tablename__ = "samples"
    __table_args__ = (
        UniqueConstraint("session_id", "row_index", name="uq_sample_session_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    row_index: Mapped[int] = mapped_column(Integer)

    timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    wheel_angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    reverse_state: Mapped[bool | None] = mapped_column(nullable=True)

    # Per-field quality flags, e.g. {"speed": "parse_error", "wheel_angle": "sentinel"}.
    # none_as_null=True so a clean row stores SQL NULL (not JSON `null`), which
    # makes `quality_flags IS NULL` / IS NOT NULL filtering work correctly.
    quality_flags: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )

    session: Mapped["Session"] = relationship(back_populates="samples")
