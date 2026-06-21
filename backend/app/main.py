from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.routers import ingest, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Field Test Ingestion & Analytics",
    description="Ingest, clean, and serve field-test telemetry sessions.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(ingest.router)
app.include_router(sessions.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
