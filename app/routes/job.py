from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import JobsRepo
from app.models import JobStatus
from app.routes.pages import require_auth
from app.worker.tasks import finalize_job

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

STATUS_TO_PARTIAL = {
    JobStatus.UPLOADED: "partials/status.html",
    JobStatus.EXTRACTING: "partials/status.html",
    JobStatus.REVIEW_NEEDED: "partials/review.html",
    JobStatus.FINALIZING: "partials/status.html",
    JobStatus.READY: "partials/ready.html",
    JobStatus.FAILED: "partials/failed.html",
    JobStatus.EXPIRED: "partials/failed.html",
}

STATUS_LABELS = {
    JobStatus.UPLOADED: "PDFs hochgeladen, warte auf Verarbeitung …",
    JobStatus.EXTRACTING: "Claude liest die PDFs und extrahiert die Konten …",
    JobStatus.FINALIZING: "Excel wird gebaut …",
}


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    if not require_auth(request):
        raise HTTPException(status_code=401)
    job = JobsRepo().get(job_id)
    if not job:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "job.html", {"job": job})


@router.get("/job/{job_id}/status", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    if not require_auth(request):
        raise HTTPException(status_code=401)
    job = JobsRepo().get(job_id)
    if not job:
        raise HTTPException(status_code=404)
    partial = STATUS_TO_PARTIAL[job.status]
    return templates.TemplateResponse(request, partial, {
        "job": job,
        "status_label": STATUS_LABELS.get(job.status, ""),
    })


@router.post("/job/{job_id}/finalize", response_class=HTMLResponse)
async def job_finalize(request: Request, job_id: str, background: BackgroundTasks):
    if not require_auth(request):
        raise HTTPException(status_code=401)

    form = await request.form()
    # Review-Antworten: keys wie "review[KONTONR]" → gruppe
    review: dict[str, str] = {}
    for key, value in form.items():
        if key.startswith("review[") and key.endswith("]") and value:
            konto_nr = key[len("review["):-1]
            review[konto_nr] = str(value)

    job = JobsRepo().get(job_id)
    if not job:
        raise HTTPException(status_code=404)

    background.add_task(finalize_job, job_id, review)
    # Update status immediately so the partial shows the spinner on next poll
    JobsRepo().set_status(job_id, JobStatus.FINALIZING)
    job = JobsRepo().get(job_id)
    return templates.TemplateResponse(request, "partials/status.html", {
        "job": job,
        "status_label": STATUS_LABELS[JobStatus.FINALIZING],
    })
