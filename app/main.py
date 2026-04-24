import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.db import JobsRepo
from app.routes import download, job, pages, upload
from app.worker.tasks import resume_stuck_jobs

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fix 1A: beim App-Start hängende Jobs aufnehmen (Railway-Deploy-Überlebenspfad)
    try:
        resume_stuck_jobs()
    except Exception as e:
        log.warning("resume_stuck_jobs failed at startup (Supabase paused?): %s", e)
    yield


app = FastAPI(title="Prozess-Übertrag", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/health")
def health():
    """Fix 1C: detect paused Supabase and return clear message instead of 500."""
    try:
        JobsRepo().client.table("jobs").select("id").limit(1).execute()
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse(
            {"status": "error",
             "message": "Datenbank nicht erreichbar. Falls Supabase pausiert ist: "
                        "im Dashboard aktivieren.",
             "details": str(e)[:200]},
            status_code=503,
        )


app.include_router(pages.router)
app.include_router(upload.router)
app.include_router(job.router)
app.include_router(download.router)
