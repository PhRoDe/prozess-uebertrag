from datetime import datetime, timedelta, timezone
from typing import Any
from supabase import create_client, Client
from app.config import get_settings
from app.completeness import completeness_gaps, ja_columns, leaf_group_names, _col_get
from app.models import Job, InputFile, JobStatus, Company


class JobsRepo:
    def __init__(self, client: Client | None = None) -> None:
        s = get_settings()
        self.client = client or create_client(s.supabase_url, s.supabase_service_key)
        self._expiry_hours = s.job_expiry_hours

    def create(self, input_files: list[InputFile],
               created_by: str | None = None) -> Job:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self._expiry_hours)
        row = {
            "status": JobStatus.UPLOADED.value,
            "input_files": [f.model_dump() for f in input_files],
            "expires_at": expires.isoformat(),
            "created_by": created_by,
        }
        resp = self.client.table("jobs").insert(row).execute()
        return self._row_to_job(resp.data[0])

    def get(self, job_id: str) -> Job | None:
        resp = self.client.table("jobs").select("*").eq("id", job_id).execute()
        if not resp.data:
            return None
        return self._row_to_job(resp.data[0])

    def set_status(self, job_id: str, status: JobStatus, error: str | None = None) -> None:
        updates: dict[str, Any] = {"status": status.value}
        if error:
            updates["error_message"] = error
        self.client.table("jobs").update(updates).eq("id", job_id).execute()

    # Fix 2A: Layer-sauberes Update der input_files
    def set_input_files(self, job_id: str, input_files: list[InputFile]) -> None:
        self.client.table("jobs").update({
            "input_files": [f.model_dump() for f in input_files],
        }).eq("id", job_id).execute()

    # Fix 1A: Claim-Pattern (nur ein Worker pro Job)
    def try_claim(self, job_id: str, node_id: str) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        resp = (self.client.table("jobs")
                .update({"processing_node": node_id, "processing_started_at": now_iso})
                .eq("id", job_id)
                .is_("processing_node", "null")
                .execute())
        return bool(resp.data)

    def release_claim(self, job_id: str) -> None:
        self.client.table("jobs").update({"processing_node": None}).eq("id", job_id).execute()

    # Fix 1A: Beim App-Start hängende Jobs aufnehmen
    def list_resumable(self) -> list[Job]:
        resp = self.client.rpc("jobs_pending_resume", {}).execute()
        return [self._row_to_job(r) for r in (resp.data or [])]

    def set_company(self, job_id: str, company_id: str,
                    metadata: dict[str, Any] | None = None) -> None:
        """Job mit einer Firma verknüpfen (+ optionale Perioden-/Quellen-
        Metadaten). Phase A."""
        updates: dict[str, Any] = {"company_id": company_id}
        for key in ("source_type", "period_start", "period_end",
                    "coverage_months", "is_consolidated"):
            if metadata and key in metadata:
                updates[key] = metadata[key]
        self.client.table("jobs").update(updates).eq("id", job_id).execute()

    def set_extraction(self, job_id: str, extraction: dict[str, Any]) -> None:
        self.client.table("jobs").update({
            "extraction": extraction,
            "status": JobStatus.REVIEW_NEEDED.value,
            "processing_node": None,
        }).eq("id", job_id).execute()

    def set_output(self, job_id: str, output_path: str, review_answers: dict[str, Any]) -> None:
        self.client.table("jobs").update({
            "output_path": output_path,
            "review_answers": review_answers,
            "status": JobStatus.READY.value,
            "processing_node": None,
        }).eq("id", job_id).execute()

    def _row_to_job(self, row: dict[str, Any]) -> Job:
        return Job.model_validate(row)


def project_line_items(job_id: str, consolidated: dict[str, Any]
                       ) -> tuple[list[dict], list[dict]]:
    """Projiziere die consolidated-Struktur in flache Zeilen für die relationale
    Konten-Schicht. Rein (kein I/O).

    Returns: (line_items_rows, line_item_group_rows).
    - line_items: eine Zeile pro Konto × Spalte mit Wert.
    - line_item_groups: eine Zeile pro Gruppe × Spalte mit gedruckter Summe
      (column_sums) und/oder Konten-Summe — Basis für v_job_completeness.
    """
    columns = consolidated.get("columns", [])
    groups = consolidated.get("groups", [])
    # Kinder pro Parent vorberechnen — die group-row eines Parents rechnet die
    # Konten der Sub-Gruppen mit ein (Parent=Summe-der-Kinder, wie Builder/
    # verify.py), sonst zeigt ein Parent mit Detail in den Subs eine Falsch-
    # Lücke in v_job_completeness (Code-Review #3).
    children_of: dict[str, list[dict]] = {}
    for g in groups:
        parent = g.get("sub_group_of")
        if parent:
            children_of.setdefault(parent, []).append(g)

    line_items: list[dict] = []
    group_rows: list[dict] = []
    for g in groups:
        gname = g.get("name")
        gsec = g.get("gkv_section")
        col_sums = g.get("column_sums") or {}
        accounts = g.get("accounts") or []
        for ci, col in enumerate(columns):
            label = col.get("label")
            # source_type: doc_type bevorzugen (ja|bwa|susa). Susa-Spalten tragen
            # consolidate-intern kind='bwa' — ohne doc_type würden Susa-Konten als
            # BWA gespeichert (Codex P2).
            src = col.get("doc_type") or col.get("kind")
            printed = _col_get(col_sums, ci)
            own_sum = 0.0
            has_own_value = False
            for acc in accounts:
                v = _col_get(acc.get("values") or {}, ci)
                if not isinstance(v, (int, float)):
                    continue
                has_own_value = True
                own_sum += v
                line_items.append({
                    "job_id": job_id, "source_type": src, "col_idx": ci,
                    "column_label": label, "group_name": gname,
                    "gkv_section": gsec, "konto_nr": acc.get("konto_nr"),
                    "bezeichnung": acc.get("bezeichnung"), "betrag": v,
                    "confidence": acc.get("confidence"),
                    "is_restposten": acc.get("confidence") == "synthetic",
                })
            # acc_sum der group-row inkl. Sub-Gruppen. Ein Kind mit eigenen
            # Konten steuert deren Summe bei; ein summary-only Kind (nur
            # gedruckte column_sum, keine Konten — DATEV-Kurzform) seine
            # column_sum (analog verify._group_acc_sum). Sonst Falsch-Lücke
            # am Parent in v_job_completeness (Codex P2).
            child_sum = 0.0
            has_child_value = False
            for child in children_of.get(gname, []):
                child_own = 0.0
                child_has_acc = False
                for acc in child.get("accounts") or []:
                    v = _col_get(acc.get("values") or {}, ci)
                    if isinstance(v, (int, float)):
                        child_own += v
                        child_has_acc = True
                if child_has_acc:
                    child_sum += child_own
                    has_child_value = True
                else:
                    cv = _col_get(child.get("column_sums") or {}, ci)
                    if isinstance(cv, (int, float)):
                        child_sum += cv
                        has_child_value = True
            if printed is not None or has_own_value or has_child_value:
                group_rows.append({
                    "job_id": job_id, "col_idx": ci, "column_label": label,
                    "group_name": gname, "gkv_section": gsec,
                    "printed_sum": printed if isinstance(printed, (int, float)) else None,
                    "acc_sum": round(own_sum + child_sum, 2),
                })
    return line_items, group_rows


def completeness_summary(consolidated: dict[str, Any] | None) -> dict[str, Any]:
    """View-Model fürs Vollständigkeits-Panel (Phase 3a/3b). Rein.

    Die Lücken-Liste kommt aus `app.completeness.completeness_gaps` — dieselbe
    Funktion nutzt der Excel-Builder beim Finalisieren, damit Review-Anzeige und
    Fragen-Sheet konsistent bleiben (gleiche Reihenfolge → gap_index gültig,
    Codex P2). Hier nur das View-Model drumherum (Zähler + Dropdown-Quellen).

    complete_groups NICHT per Namens-Match: gap.group ist roh, consolidated ggf.
    HGB-umnummeriert — Match würde "alle vollständig" neben Lücken melden. Daher
    distinkte Lücken-Gruppen zählen.
    """
    consolidated = consolidated or {}
    groups = consolidated.get("groups") or []
    columns = consolidated.get("columns") or []
    gaps = completeness_gaps(consolidated)
    total_groups = len(groups)
    gap_group_count = len({g.get("group") for g in gaps})
    return {
        "gaps": gaps,
        "total_groups": total_groups,
        "complete_groups": max(0, total_groups - gap_group_count),
        "has_gaps": bool(gaps),
        "columns": ja_columns(columns),
        "all_groups": leaf_group_names(groups),
    }


class LineItemsRepo:
    """Relationale Konten-Schicht (Phase 2). Schreibt die abgeleiteten
    line_items/line_item_groups eines Jobs. Idempotent: alte Zeilen des Jobs
    werden gezielt (eq job_id) gelöscht, dann neu eingefügt — kein breites
    Löschen, `keepalive` bleibt unberührt."""

    def __init__(self, client: Client | None = None) -> None:
        s = get_settings()
        self.client = client or create_client(s.supabase_url, s.supabase_service_key)

    def materialize(self, job_id: str, consolidated: dict[str, Any]) -> None:
        line_items, group_rows = project_line_items(job_id, consolidated)
        # Idempotenz: vorhandene Zeilen dieses Jobs entfernen (eq job_id).
        self.client.table("line_items").delete().eq("job_id", job_id).execute()
        self.client.table("line_item_groups").delete().eq("job_id", job_id).execute()
        if line_items:
            self.client.table("line_items").insert(line_items).execute()
        if group_rows:
            self.client.table("line_item_groups").insert(group_rows).execute()


class PdfCacheRepo:
    """Inhalts-adressierter Cache der rohen _extract_pdf-Ausgabe (Phase 4).
    Key = (pdf_hash, model). Spart teure Claude-Calls bei Re-Uploads/Re-Runs.
    Best-effort: Aufrufer fangen Fehler ab — ein Cache-Ausfall darf den Job
    NICHT scheitern lassen."""

    def __init__(self, client: Client | None = None) -> None:
        s = get_settings()
        self.client = client or create_client(s.supabase_url, s.supabase_service_key)

    def get(self, pdf_hash: str, model: str) -> list[dict] | None:
        resp = (self.client.table("pdf_extractions")
                .select("extractions")
                .eq("pdf_hash", pdf_hash).eq("model", model)
                .execute())
        if resp.data:
            return resp.data[0]["extractions"]
        return None

    def put(self, pdf_hash: str, model: str, extractions: list[dict]) -> None:
        # upsert (idempotent gegen konkurrierende Worker / Wiederholungen).
        self.client.table("pdf_extractions").upsert(
            {"pdf_hash": pdf_hash, "model": model, "extractions": extractions},
            on_conflict="pdf_hash,model",
        ).execute()


class CompaniesRepo:
    """Firmen-Entität (Phase A, Benchmarking-Fundament). Mehrere Jobs/Jahre pro
    Firma. branche_code referenziert die kontrollierte Liste (app/industries.py),
    branche_label ist die optionale Freitext-Notiz (Hybrid)."""

    _FIELDS = ("rechtsform", "branche_code", "branche_label",
               "revenue_band", "employee_band")

    def __init__(self, client: Client | None = None) -> None:
        s = get_settings()
        self.client = client or create_client(s.supabase_url, s.supabase_service_key)

    def create(self, name: str, created_by: str | None = None, **fields: Any) -> Company:
        row: dict[str, Any] = {"name": name, "created_by": created_by}
        for key in self._FIELDS:
            if key in fields:
                row[key] = fields[key]
        resp = self.client.table("companies").insert(row).execute()
        return Company.model_validate(resp.data[0])

    def get(self, company_id: str) -> Company | None:
        resp = (self.client.table("companies").select("*")
                .eq("id", company_id).execute())
        if not resp.data:
            return None
        return Company.model_validate(resp.data[0])

    def find_by_name(self, name: str, created_by: str | None) -> Company | None:
        """Firma per (Name, Owner) finden — für find-or-create beim Verknüpfen,
        damit mehrere Jahre derselben Firma EINER company_id zugeordnet werden."""
        q = self.client.table("companies").select("*").eq("name", name)
        q = q.is_("created_by", "null") if created_by is None else q.eq("created_by", created_by)
        resp = q.execute()
        if not resp.data:
            return None
        return Company.model_validate(resp.data[0])

    def list_for_user(self, username: str | None) -> list[Company]:
        """Firmen des Users (created_by). Legacy-Firmen ohne created_by sind für
        alle sichtbar (analog job_owner_ok)."""
        resp = self.client.table("companies").select("*").order("name").execute()
        out = [Company.model_validate(r) for r in (resp.data or [])]
        return [c for c in out if c.created_by is None or c.created_by == username]
