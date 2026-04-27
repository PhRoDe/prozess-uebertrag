from datetime import datetime, timedelta, timezone
from typing import Any
from supabase import create_client, Client
from app.config import get_settings
from app.models import Job, InputFile, JobStatus


class JobsRepo:
    def __init__(self, client: Client | None = None) -> None:
        s = get_settings()
        self.client = client or create_client(s.supabase_url, s.supabase_service_key)
        self._expiry_hours = s.job_expiry_hours

    def create(self, input_files: list[InputFile]) -> Job:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self._expiry_hours)
        row = {
            "status": JobStatus.UPLOADED.value,
            "input_files": [f.model_dump() for f in input_files],
            "expires_at": expires.isoformat(),
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
