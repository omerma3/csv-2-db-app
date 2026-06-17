from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session as DbSession

from app.database import get_db
from app.ingestion.loader import ingest
from app.schemas import IngestResponse

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_session(
    metadata: UploadFile = File(..., description="metadata_*.json sidecar"),
    csv: UploadFile = File(..., description="field_*.csv telemetry file"),
    db: DbSession = Depends(get_db),
) -> IngestResponse:
    """Ingest a telemetry session from a metadata JSON + CSV file pair."""
    metadata_raw = await metadata.read()
    csv_raw = await csv.read()
    try:
        result = ingest(db, metadata_raw=metadata_raw, csv_raw=csv_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return IngestResponse(
        session_id=result.session.session_id,
        sample_count=result.sample_count,
        flag_counts=result.flag_counts,
    )
