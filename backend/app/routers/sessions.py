from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app import models
from app.database import get_db
from app.schemas import (
    QualityReport,
    SampleOut,
    SamplePage,
    SessionDetail,
    SessionSummary,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _get_session_or_404(db: DbSession, session_id: str) -> models.Session:
    session = db.execute(
        select(models.Session).where(models.Session.session_id == session_id)
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail=f"session '{session_id}' not found")
    return session


@router.get("", response_model=list[SessionSummary])
def list_sessions(db: DbSession = Depends(get_db)) -> list[models.Session]:
    """List all ingested sessions."""
    return list(
        db.execute(select(models.Session).order_by(models.Session.id)).scalars()
    )


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: str, db: DbSession = Depends(get_db)) -> models.Session:
    """Get full metadata for one session."""
    return _get_session_or_404(db, session_id)


@router.get("/{session_id}/samples", response_model=SamplePage)
def get_samples(
    session_id: str,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    flagged_only: bool = Query(False, description="only return rows with quality flags"),
    db: DbSession = Depends(get_db),
) -> SamplePage:
    """Get paginated telemetry samples for a session."""
    session = _get_session_or_404(db, session_id)

    base = select(models.Sample).where(models.Sample.session_id == session.id)
    if flagged_only:
        base = base.where(models.Sample.quality_flags.isnot(None))

    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    rows = db.execute(
        base.order_by(models.Sample.row_index).limit(limit).offset(offset)
    ).scalars()

    return SamplePage(
        total=total,
        limit=limit,
        offset=offset,
        items=[SampleOut.model_validate(r) for r in rows],
    )


@router.get("/{session_id}/quality", response_model=QualityReport)
def get_quality(session_id: str, db: DbSession = Depends(get_db)) -> QualityReport:
    """Summarize data-quality flags for a session."""
    session = _get_session_or_404(db, session_id)

    samples = list(
        db.execute(
            select(models.Sample).where(models.Sample.session_id == session.id)
        ).scalars()
    )

    flag_counts: dict[str, int] = {}
    field_flag_counts: dict[str, dict[str, int]] = {}
    flagged_samples = 0

    for s in samples:
        if not s.quality_flags:
            continue
        flagged_samples += 1
        for field_name, flag in s.quality_flags.items():
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
            field_flag_counts.setdefault(field_name, {})
            field_flag_counts[field_name][flag] = (
                field_flag_counts[field_name].get(flag, 0) + 1
            )

    return QualityReport(
        session_id=session.session_id,
        total_samples=len(samples),
        flagged_samples=flagged_samples,
        flag_counts=flag_counts,
        field_flag_counts=field_flag_counts,
    )
