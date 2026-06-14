"""Background-Task-Orchestrator für die Extraction und Finalization Pipeline.

Idempotent + claim-based:
- Doppelte Aufrufe gleicher Job-ID machen kein Doppel-Request an Claude (Fix 1D)
- Konkurrierende Worker (z.B. nach einem Deploy-Restart) treten sich nicht
  auf die Füße — nur der erste try_claim gewinnt (Fix 1A)
"""
import copy
import hashlib
import logging
import os
import socket

from app.db import JobsRepo, LineItemsRepo, PdfCacheRepo
from app.excel.builder import build_excel
from app.models import JobStatus
from app.storage import StorageClient
from app.worker.claude_client import ClaudeClient, ExtractionError
from app.worker.consolidate import merge_extractions
from app.worker.verify import heal_extraction
from app.worker.pdf_detect import (
    PdfKind, classify_pdf, extract_guv_section, extract_susa_section,
    extract_text, pdf_to_images, _has_susa_section,
)

log = logging.getLogger(__name__)

# Eindeutige Node-ID pro Prozess (Fix 1A)
NODE_ID = f"{socket.gethostname()}-{os.getpid()}"

# Pipeline-Cache-Version (Phase 4): bei Änderungen an der Extraktions-/Heilungs-
# /Prompt-Logik bumpen, um gecachte Extraktionen zu invalidieren (Cache-Key =
# pdf_hash × f"{model}:v{_CACHE_VERSION}").
_CACHE_VERSION = "2"  # v2: company_name im JA-Prompt ergänzt (Phase A2)

TERMINAL_STATES = {JobStatus.READY, JobStatus.REVIEW_NEEDED,
                   JobStatus.FAILED, JobStatus.EXPIRED}


def _extract_pdf(claude: ClaudeClient, data: bytes,
                 cache: PdfCacheRepo | None = None) -> list[dict]:
    """Extrahiere ein PDF → eine oder ZWEI Extraktionen, mit optionalem Cache.

    Phase 4: inhalts-adressierter Cache je (sha256(data) × Modell). Bei Treffer
    werden ALLE Claude-Calls (Klassifikation + Extraktion + Selbstheilung)
    übersprungen. Best-effort: ein Cache-Fehler darf den Job NICHT scheitern
    lassen — Fallback auf frische Extraktion.
    """
    if cache is None:
        return _extract_pdf_uncached(claude, data)
    pdf_hash = hashlib.sha256(data).hexdigest()
    # Modell-Key trägt eine Pipeline-Cache-Version: ändert sich die
    # Extraktions-/Heilungs-/Prompt-Logik (bei gleichem Modell-String), wird der
    # Cache durch Bumpen von _CACHE_VERSION invalidiert (sonst 30 Tage stale).
    model_key = f"{claude.model}:v{_CACHE_VERSION}"
    try:
        hit = cache.get(pdf_hash, model_key)
    except Exception:
        log.warning("pdf cache get failed (non-critical)", exc_info=True)
        hit = None
    if hit is not None:
        log.info("pdf cache hit %s…", pdf_hash[:12])
        return copy.deepcopy(hit)  # Kopie: extract_job mutiert ['file']
    result = _extract_pdf_uncached(claude, data)
    # Nur SAUBERE Extraktionen cachen: bleibt eine Lücke offen
    # (_unresolved_gaps, z.B. transienter Heilungs-Fehler), nicht einfrieren —
    # ein späterer Re-Run soll erneut heilen können (Code-Review).
    if not any(e.get("_unresolved_gaps") for e in result):
        try:
            cache.put(pdf_hash, model_key, result)
        except Exception:
            log.warning("pdf cache put failed (non-critical)", exc_info=True)
    return result


def _extract_pdf_uncached(claude: ClaudeClient, data: bytes) -> list[dict]:
    """Extrahiere ein PDF → eine oder ZWEI Extraktionen.

    Kombiniertes DATEV-Bundle (BWA-Aggregat + 'Summen und Salden'-Susa in einer
    PDF): liefert ZWEI Extraktionen — das BWA-Aggregat (mit Vorläufigem Ergebnis
    als Endwert) UND die Susa-Einzelkonten (Detail-Spalte). Sonst genau eine.
    Susa-Detail wird nur aus den Susa-Seiten gezogen (extract_susa_section),
    damit OPOS-/USt-Seiten nicht ins Detail wandern.
    """
    kind = classify_pdf(data)
    if kind != PdfKind.TEXT:
        doc_type = claude.classify_document("SCAN-BILDDATEN")
        pages = pdf_to_images(data)
        return [claude.extract_scan_pdf(pages, doc_type=doc_type)]

    # Klassifikation auf Basis der ersten 5000 Zeichen (Titelseite)
    full_text = extract_text(data)
    doc_type = claude.classify_document(full_text[:5000])

    if doc_type == "bwa":
        out = [claude.extract_text_pdf(full_text, doc_type="bwa")]
        # Bundle: enthält die PDF zusätzlich eine Susa → Einzelkonten als
        # eigene Detail-Spalte mitnehmen (User-Wunsch: BWA-Aggregat + Detail).
        if _has_susa_section(full_text):
            out.append(claude.extract_text_pdf(
                extract_susa_section(data), doc_type="susa"))
        return out
    if doc_type == "susa":
        # Reine Susa (oder als susa klassifiziertes Bundle): nur die Susa-Seiten.
        return [claude.extract_text_pdf(
            extract_susa_section(data), doc_type="susa")]
    # JAs koennen 60+ Seiten haben → nur GuV-Kontennachweis an Claude.
    guv_text = extract_guv_section(data)
    extraction = claude.extract_text_pdf(guv_text, doc_type="jahresabschluss")
    # Selbstheilung: fehlen Konten (Konten-Summe < gedruckte Gruppensumme),
    # gezielt nachextrahieren (max. 2 Runden). Verbleibende Lücken zur Anzeige
    # im Review (Phase 3) am Dokument vermerken.
    # Wichtig: die Re-Extraktion ist eine OPTIONALE Verbesserung. Schlägt sie
    # fehl (API-Fehler, kaputtes JSON), darf das NICHT den Job killen — wir
    # fallen auf die bereits erfolgreiche Erst-Extraktion zurück (graceful
    # degradation), die Lücken werden dann nur angezeigt statt geheilt.
    def _safe_reextract(_ext, gaps):
        try:
            return claude.reextract_groups(guv_text, gaps)
        except Exception:
            log.warning("self-heal reextract failed, keeping original extraction",
                        exc_info=True)
            return {}
    healed, unresolved = heal_extraction(extraction, _safe_reextract)
    if unresolved:
        healed["_unresolved_gaps"] = unresolved
    return [healed]


def _collect_completeness_questions(extractions: list[dict]) -> list[dict]:
    """Verbleibende Vollständigkeits-Lücken (nach der Selbstheilung) als
    completeness_gap fürs Fragen-Sheet sammeln.

    Filtert VJ-Lücken heraus, deren Jahr ein eigenes JA hat: die Konsolidierung
    nimmt für ein Jahr mit eigenem JA dessen Werte (setdefault) und ignoriert die
    VJ-Spalte des jüngeren Docs — eine VJ-Lücke dort betrifft Daten, die das
    Excel gar nicht verwendet, und wäre ein Fehlalarm (Codex P2).
    """
    own_years = {d.get("year") for d in extractions
                 if d.get("type") == "jahresabschluss" and d.get("year") is not None}
    questions: list[dict] = []
    for doc in extractions:
        for gap in doc.get("_unresolved_gaps", []):
            if gap.get("period") == "vj" and gap.get("year") in own_years:
                continue
            questions.append({
                "type": "completeness_gap",
                "group": gap.get("group"),
                "year": gap.get("year"),
                "printed_sum": gap.get("printed_sum"),
                "acc_sum": gap.get("acc_sum"),
                "diff": gap.get("diff"),
                "document": doc.get("file"),
            })
    return questions


def extract_job(job_id: str) -> None:
    """Extract all input PDFs for a job via Claude. Idempotent + claim-safe."""
    repo = JobsRepo()
    storage = StorageClient()
    claude = ClaudeClient()
    cache = PdfCacheRepo()  # Phase 4: PDF-Extraktions-Cache (best-effort)

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
            for extraction in _extract_pdf(claude, data, cache=cache):
                extraction["file"] = input_file.name
                extractions.append(extraction)

        consolidated = merge_extractions(extractions)
        # Verbleibende Vollständigkeits-Lücken (nach der Selbstheilung) sichtbar
        # machen: als completeness_gap ins Fragen-Sheet (consolidated.questions).
        # Sonst füllt der Excel-Builder still einen Restposten und der User sieht
        # die fehlenden Konten nie (Codex-Finding P2-6). Ignorierte VJ-Lücken
        # werden in _collect_completeness_questions herausgefiltert.
        gap_questions = _collect_completeness_questions(extractions)
        if gap_questions:
            consolidated.setdefault("questions", []).extend(gap_questions)
        # open_questions für Review-Screen: nur aus der jüngsten JA (die ist am relevantesten).
        # Claude liefert open_questions manchmal als String statt Dict (z.B. "Diese PDF ist
        # eine EÜR ohne erkennbare GuV-Gruppen"). String-Hinweise als hint-only-Eintraege
        # wrappen — konsistent zu consolidate.py:_ingest_ja. Sonst crasht {**oq, ...} mit
        # TypeError: 'str' object is not a mapping (Live-Bug 2026-05-12, Job f55c031b).
        open_questions: list = []
        for doc in extractions:
            if doc.get("type") != "jahresabschluss":
                continue
            file_name = doc.get("file")
            for oq in doc.get("open_questions", []):
                if isinstance(oq, str):
                    open_questions.append({
                        "konto_nr": None, "bezeichnung": None,
                        "betrag_gj": None, "hint": oq,
                        "document": file_name,
                    })
                else:
                    open_questions.append({**oq, "document": file_name})
        # Firmenname-Vorschlag (Phase A): erster JA mit company_name → Prefill
        # im Review (User bestätigt + ergänzt Branche).
        company_suggestion = next(
            (d.get("company_name") for d in extractions
             if d.get("type") == "jahresabschluss" and d.get("company_name")),
            None,
        ) or next((d.get("company_name") for d in extractions if d.get("company_name")), None)
        payload = {
            "documents": extractions,
            "consolidated": consolidated,
            "open_questions": open_questions,
            "company_suggestion": company_suggestion,
        }
        repo.set_extraction(job_id, payload)
        # Phase 2: relationale Konten-Schicht (line_items) materialisieren.
        # Auxiliär/Audit — die ausgelieferte Excel kommt weiter aus dem
        # consolidated-JSONB. Ein Fehler hier darf den Job NICHT scheitern
        # lassen (graceful degradation), darum eigenes try/except.
        try:
            LineItemsRepo().materialize(job_id, consolidated)
        except Exception:
            log.exception("materialize line_items failed (non-critical) for %s", job_id)
    except ExtractionError as e:
        log.exception("extract_job(%s) ExtractionError", job_id)
        repo.set_status(job_id, JobStatus.FAILED, error=f"Extraktion fehlgeschlagen: {e}")
    except Exception as e:
        log.exception("extract_job(%s) unexpected error", job_id)
        repo.set_status(job_id, JobStatus.FAILED, error=f"Unerwarteter Fehler: {e}")


def _materialize_metrics(job, consolidated: dict) -> None:
    """Phase B2: Benchmarking-Kennzahlen pro JA-Spalte (=Firma-Jahr) berechnen +
    upserten. Best-effort — ein Fehler darf den Übertrag NICHT scheitern lassen.
    Nur wenn der Job einer Firma zugeordnet ist."""
    company_id = getattr(job, "company_id", None)
    if not company_id:
        return
    try:
        from app.metrics import compute_company_metrics
        from app.db import MetricsRepo
        repo = MetricsRepo()
        for i, col in enumerate(consolidated.get("columns") or []):
            if (col.get("doc_type") or col.get("kind")) != "ja":
                continue
            year = col.get("year")
            if year is None:
                continue
            m = compute_company_metrics(consolidated, i)
            if m is None:
                continue
            repo.upsert_company_year(company_id, year, job.id, "ja", m)
    except Exception:
        log.exception("metrics materialize failed (non-critical) for %s", job.id)


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
        _materialize_metrics(job, consolidated)
    except Exception as e:
        log.exception("finalize_job(%s) error", job_id)
        repo.set_status(job_id, JobStatus.FAILED,
                        error=f"Excel-Erstellung fehlgeschlagen: {e}")
        repo.release_claim(job_id)


def resume_stuck_jobs() -> None:
    """Fix 1A: at app-start, find jobs stuck in 'extracting' or 'finalizing'
    whose processing_node is stale (worker was killed by a deploy restart)
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
