from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.db import JobsRepo
from app.models import JobStatus
from app.routes.pages import require_auth
from app.storage import StorageClient

router = APIRouter()


@router.get("/download/{job_id}")
def download(request: Request, job_id: str):
    if not require_auth(request):
        raise HTTPException(status_code=401)
    job = JobsRepo().get(job_id)
    if not job or job.status != JobStatus.READY or not job.output_path:
        raise HTTPException(status_code=404)
    url = StorageClient().signed_output_url(job.output_path, expires_in=900)
    return RedirectResponse(url=url, status_code=303)
