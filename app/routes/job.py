from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import JobsRepo, CompaniesRepo, completeness_summary
from app.industries import industry_choices, is_valid_industry
from app.models import JobStatus
from app.routes.pages import require_auth, job_owner_ok, current_user
from app.worker.tasks import finalize_job
import logging

log = logging.getLogger(__name__)

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


@router.get("/uebertraege", response_class=HTMLResponse)
def my_jobs(request: Request):
    """Übersicht „Meine Überträge" (Phase A3): Firma · Branche · Datum · Quelle ·
    Status · Download — so ist erkennbar, welcher Übertrag zu welcher Firma gehört."""
    if not require_auth(request):
        raise HTTPException(status_code=401)
    user = current_user(request)["username"]
    jobs = JobsRepo().list_for_user(user)
    labels = {i["code"]: i["label"] for i in industry_choices()}
    for j in jobs:
        j["branche_label"] = labels.get(j.get("branche_code"))
    return templates.TemplateResponse(request, "jobs_list.html",
                                      {"jobs": jobs, "user": user})


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    if not require_auth(request):
        raise HTTPException(status_code=401)
    job = JobsRepo().get(job_id)
    if not job:
        raise HTTPException(status_code=404)
    if not job_owner_ok(request, job):
        raise HTTPException(status_code=403)
    return templates.TemplateResponse(request, "job.html", {"job": job})


@router.get("/job/{job_id}/status", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    if not require_auth(request):
        raise HTTPException(status_code=401)
    job = JobsRepo().get(job_id)
    if not job:
        raise HTTPException(status_code=404)
    if not job_owner_ok(request, job):
        raise HTTPException(status_code=403)
    partial = STATUS_TO_PARTIAL[job.status]
    # Gruppennamen für den Review-Dropdown kommen jetzt aus der konsolidierten
    # Struktur (dynamisch aus den PDFs), nicht mehr aus fester Hierarchie.
    all_groups: list[str] = []
    consolidated = None
    if job.extraction and job.extraction.get("consolidated"):
        consolidated = job.extraction["consolidated"]
        all_groups = [g["name"] for g in consolidated.get("groups", [])]
    company_suggestion = ""
    if job.extraction:
        company_suggestion = job.extraction.get("company_suggestion") or ""
    return templates.TemplateResponse(request, partial, {
        "job": job,
        "status_label": STATUS_LABELS.get(job.status, ""),
        "all_detail_groups": all_groups,
        "completeness": completeness_summary(consolidated),
        "industries": industry_choices(),
        "company_suggestion": company_suggestion,
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


def parse_company_form(items) -> dict:
    """Firmen-Felder aus dem Finalize-Formular (Phase A). Rein.
    Leerer/fehlender Name → {} (keine Verknüpfung). Unbekannte branche_code →
    verworfen (FK-Schutz)."""
    f = {k: v for k, v in items if k.startswith("company_")}
    name = (f.get("company_name") or "").strip()
    if not name:
        return {}
    branche_code = (f.get("company_branche_code") or "").strip() or None
    if not is_valid_industry(branche_code):
        branche_code = None
    out: dict = {"name": name}
    if branche_code:
        out["branche_code"] = branche_code
    for key, col in (("company_branche_label", "branche_label"),
                     ("company_rechtsform", "rechtsform")):
        val = (f.get(key) or "").strip()
        if val:
            out[col] = val
    return out


def _link_company(job_id: str, company_fields: dict, username: str | None) -> None:
    """Firma find-or-create (per Name+Owner) und an den Job hängen. Best-effort:
    ein Fehler darf den Übertrag NICHT blockieren (Excel wird trotzdem gebaut)."""
    if not company_fields:
        return
    try:
        repo = CompaniesRepo()
        extra = {k: v for k, v in company_fields.items() if k != "name"}
        existing = repo.find_by_name(company_fields["name"], username)
        if existing:
            # Folge-Upload: vom User neu/zusätzlich angegebene Felder übernehmen
            # (z.B. Branche, die beim ersten Mal fehlte).
            if extra:
                repo.update(existing.id, **extra)
            company = existing
        else:
            company = repo.create(company_fields["name"], created_by=username, **extra)
        JobsRepo().set_company(job_id, company.id)
    except Exception:
        log.exception("link_company failed (non-critical) for %s", job_id)


@router.post("/job/{job_id}/finalize", response_class=HTMLResponse)
async def job_finalize(request: Request, job_id: str, background: BackgroundTasks):
    if not require_auth(request):
        raise HTTPException(status_code=401)

    form = await request.form()
    items = form.multi_items()
    review = parse_finalize_form(items)

    job = JobsRepo().get(job_id)
    if not job:
        raise HTTPException(status_code=404)
    if not job_owner_ok(request, job):
        raise HTTPException(status_code=403)

    # Firma erfassen/verknüpfen (Phase A) — vor dem Build, best-effort.
    _link_company(job_id, parse_company_form(items), current_user(request)["username"])

    background.add_task(finalize_job, job_id, review)
    # Update status immediately so the partial shows the spinner on next poll
    JobsRepo().set_status(job_id, JobStatus.FINALIZING)
    job = JobsRepo().get(job_id)
    return templates.TemplateResponse(request, "partials/status.html", {
        "job": job,
        "status_label": STATUS_LABELS[JobStatus.FINALIZING],
    })
