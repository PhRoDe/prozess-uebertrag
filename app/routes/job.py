from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import JobsRepo, completeness_summary
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
    # Gruppennamen für den Review-Dropdown kommen jetzt aus der konsolidierten
    # Struktur (dynamisch aus den PDFs), nicht mehr aus fester Hierarchie.
    all_groups: list[str] = []
    consolidated = None
    if job.extraction and job.extraction.get("consolidated"):
        consolidated = job.extraction["consolidated"]
        all_groups = [g["name"] for g in consolidated.get("groups", [])]
    return templates.TemplateResponse(request, partial, {
        "job": job,
        "status_label": STATUS_LABELS.get(job.status, ""),
        "all_detail_groups": all_groups,
        "completeness": completeness_summary(consolidated),
    })


def _parse_betrag(raw) -> float | None:
    """Betrag aus dem Formular: akzeptiert Punkt-Dezimal, Komma-Dezimal UND
    deutsches Tausenderformat (1.234,56 / 1.000.000,00) ohne stilles Droppen."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    candidates = [s]
    if "," in s:
        # Deutsches Format: Punkt = Tausender, Komma = Dezimal → Punkte raus,
        # Komma zu Punkt.
        candidates.append(s.replace(".", "").replace(",", "."))
    candidates.append(s.replace(",", "."))
    for candidate in candidates:
        try:
            return float(candidate)
        except ValueError:
            continue
    return None


def parse_finalize_form(items) -> dict:
    """Baut das review-Dict aus den Form-Items. Rein (kein Request-Objekt).

    - review[KONTONR] → Gruppen-Zuordnung (bestehender open_questions-Pfad)
    - gap_*[i] → manuell nachgetragenes Konto pro Lücke, aber nur wenn
      gap_action[i] == 'correct' UND Gruppe + Betrag gesetzt sind. Landet als
      review['_manual_accounts'] (build_excel injiziert es vor dem Restposten).
    """
    review: dict = {}
    raw_gaps: dict[str, dict] = {}
    for key, value in items:
        if key.startswith("review[") and key.endswith("]") and value:
            review[key[len("review["):-1]] = str(value)
        elif key.startswith("gap_") and key.endswith("]") and "[" in key:
            field, _, rest = key.partition("[")
            raw_gaps.setdefault(rest[:-1], {})[field] = value
    manual: list[dict] = []
    for idx_str, g in raw_gaps.items():
        if g.get("gap_action") != "correct":
            continue
        betrag = _parse_betrag(g.get("gap_betrag"))
        group = (g.get("gap_group") or "").strip()
        if betrag is None or not group:
            continue
        try:
            col_idx = int(g.get("gap_col"))
        except (TypeError, ValueError):
            continue
        # gap_index = Position der Lücke im Panel (Form-Reihenfolge). Eindeutig,
        # auch bei zwei gleichnamigen Lücken — fürs Reconcile des Fragen-Sheets.
        try:
            gap_index = int(g.get("gap_uid", idx_str))
        except (TypeError, ValueError):
            gap_index = None
        manual.append({
            "group": group,
            "col_idx": col_idx,
            "bezeichnung": (g.get("gap_bez") or "").strip() or "Manuell nachgetragen",
            "betrag": betrag,
            "konto_nr": "",
            "gap_index": gap_index,
        })
    if manual:
        review["_manual_accounts"] = manual
    return review


@router.post("/job/{job_id}/finalize", response_class=HTMLResponse)
async def job_finalize(request: Request, job_id: str, background: BackgroundTasks):
    if not require_auth(request):
        raise HTTPException(status_code=401)

    form = await request.form()
    review = parse_finalize_form(form.multi_items())

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
