from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from app.config import get_settings
from app.db import JobsRepo
from app.models import InputFile
from app.routes.pages import require_auth
from app.storage import StorageClient
from app.worker.tasks import extract_job

router = APIRouter()


@router.post("/upload")
async def upload(
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
):
    if not require_auth(request):
        raise HTTPException(status_code=401)

    s = get_settings()
    if len(files) == 0 or len(files) > s.max_files_per_job:
        raise HTTPException(
            status_code=400,
            detail=f"1..{s.max_files_per_job} Dateien erwartet, erhalten: {len(files)}",
        )

    for f in files:
        if f.content_type != "application/pdf" and not f.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"Nur PDFs erlaubt: {f.filename}")

    repo = JobsRepo()
    storage = StorageClient()

    # Initialer Create mit leeren Dateien — wir brauchen die Job-ID für den Storage-Pfad
    job = repo.create([])

    input_files: list[InputFile] = []
    try:
        for f in files:
            data = await f.read()
            if len(data) > s.max_file_size_mb * 1024 * 1024:
                raise HTTPException(
                    status_code=400,
                    detail=f"{f.filename} > {s.max_file_size_mb} MB",
                )
            path = storage.upload_input(job.id, f.filename, data)
            input_files.append(InputFile(name=f.filename, size=len(data), storage_path=path))

        # Fix 2A: saubere API statt direktem repo.client-Zugriff
        repo.set_input_files(job.id, input_files)
    except HTTPException:
        # Rollback: job + storage aufräumen
        storage.delete_job(job.id)
        repo.client.table("jobs").delete().eq("id", job.id).execute()
        raise

    background.add_task(extract_job, job.id)
    return Response(headers={"HX-Redirect": f"/job/{job.id}"}, status_code=200)
