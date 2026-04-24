"""Background-Task-Orchestrator für die Extraction und Finalization Pipeline.

Idempotent + claim-based:
- Doppelte Aufrufe gleicher Job-ID machen kein Doppel-Request an Claude (Fix 1D)
- Konkurrierende Worker (z.B. nach Railway-Deploy-Restart) treten sich nicht
  auf die Füße — nur der erste try_claim gewinnt (Fix 1A)
"""
import logging
import os
import socket

from app.db import JobsRepo
from app.excel.builder import build_excel
from app.models import JobStatus
from app.storage import StorageClient
from app.worker.claude_client import ClaudeClient, ExtractionError
from app.worker.consolidate import merge_extractions
from app.worker.pdf_detect import PdfKind, classify_pdf, extract_text, pdf_to_images

log = logging.getLogger(__name__)

# Eindeutige Node-ID pro Prozess (Fix 1A)
NODE_ID = f"{socket.gethostname()}-{os.getpid()}"

TERMINAL_STATES = {JobStatus.READY, JobStatus.REVIEW_NEEDED,
                   JobStatus.FAILED, JobStatus.EXPIRED}


def extract_job(job_id: str) -> None:
    """Extract all input PDFs for a job via Claude. Idempotent + claim-safe."""
    repo = JobsRepo()
    storage = StorageClient()
    claude = ClaudeClient()

    # Fix 1D: Already processed?
    existing = repo.get(job_id)
    if existing is None:
        log.warning("extract_job(%s): job not found", job_id)
        return
    if existing.status in TERMINAL_STATES:
        log.info("extract_job(%s): already in %s, skipping", job_id, existing.status)
        return

    # Fix 1A: Claim — only one worker at a time
    if not repo.try_claim(job_id, NODE_ID):
        log.info("extract_job(%s): claimed by another worker, skipping", job_id)
        return

    try:
        repo.set_status(job_id, JobStatus.EXTRACTING)
        job = repo.get(job_id)
        if job is None:
            return

        extractions: list[dict] = []
        for input_file in job.input_files:
            data = storage.download_input(input_file.storage_path)
            kind = classify_pdf(data)
            sample = extract_text(data)[:5000] if kind == PdfKind.TEXT else "SCAN-BILDDATEN"
            doc_type = claude.classify_document(sample)
            is_bwa = (doc_type == "bwa")

            if kind == PdfKind.TEXT:
                full_text = extract_text(data)
                extraction = claude.extract_text_pdf(full_text, is_bwa=is_bwa)
            else:
                pages = pdf_to_images(data)
                extraction = claude.extract_scan_pdf(pages, is_bwa=is_bwa)

            extraction["file"] = input_file.name
            extractions.append(extraction)

        consolidated = merge_extractions(extractions)
        payload = {
            "documents": extractions,
            "consolidated": consolidated,
            "open_questions": [q for doc in extractions for q in doc.get("open_questions", [])],
        }
        repo.set_extraction(job_id, payload)
    except ExtractionError as e:
        log.exception("extract_job(%s) ExtractionError", job_id)
        repo.set_status(job_id, JobStatus.FAILED, error=f"Extraktion fehlgeschlagen: {e}")
    except Exception as e:
        log.exception("extract_job(%s) unexpected error", job_id)
        repo.set_status(job_id, JobStatus.FAILED, error=f"Unerwarteter Fehler: {e}")


def finalize_job(job_id: str, review_answers: dict) -> None:
    """Build the Excel file from extraction + review answers. Idempotent + claim-safe."""
    repo = JobsRepo()
    storage = StorageClient()

    existing = repo.get(job_id)
    if existing is None:
        log.warning("finalize_job(%s): job not found", job_id)
        return
    if existing.status in {JobStatus.READY, JobStatus.EXPIRED, JobStatus.FAILED}:
        return
    if not repo.try_claim(job_id, NODE_ID):
        return

    try:
        repo.set_status(job_id, JobStatus.FINALIZING)
        job = repo.get(job_id)
        if job is None or job.extraction is None:
            return
        consolidated = job.extraction["consolidated"]
        xlsx = build_excel(consolidated, review_answers=review_answers)
        path = storage.upload_output(job_id, xlsx)
        repo.set_output(job_id, path, review_answers)
    except Exception as e:
        log.exception("finalize_job(%s) error", job_id)
        repo.set_status(job_id, JobStatus.FAILED,
                        error=f"Excel-Erstellung fehlgeschlagen: {e}")
        repo.release_claim(job_id)


def resume_stuck_jobs() -> None:
    """Fix 1A: at app-start, find jobs stuck in 'extracting' or 'finalizing'
    whose processing_node is stale (worker was killed by Railway deploy)
    and re-run them."""
    repo = JobsRepo()
    try:
        resumable = repo.list_resumable()
    except Exception:
        log.exception("resume_stuck_jobs: list_resumable failed")
        return

    for job in resumable:
        try:
            repo.release_claim(job.id)
            if job.status == JobStatus.EXTRACTING:
                log.info("Resuming extract_job for %s", job.id)
                extract_job(job.id)
            elif job.status == JobStatus.FINALIZING and job.review_answers:
                log.info("Resuming finalize_job for %s", job.id)
                finalize_job(job.id, job.review_answers)
        except Exception:
            log.exception("resume_stuck_jobs: failed to resume %s", job.id)
