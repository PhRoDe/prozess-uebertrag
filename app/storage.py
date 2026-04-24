from supabase import create_client, Client
from app.config import get_settings

BUCKET = "prozess-uebertrag"


class StorageClient:
    def __init__(self, client: Client | None = None) -> None:
        s = get_settings()
        self.client = client or create_client(s.supabase_url, s.supabase_service_key)

    def upload_input(self, job_id: str, filename: str, data: bytes) -> str:
        path = f"{job_id}/input/{filename}"
        self.client.storage.from_(BUCKET).upload(
            path=path, file=data,
            file_options={"content-type": "application/pdf"},
        )
        return path

    def upload_output(self, job_id: str, data: bytes) -> str:
        path = f"{job_id}/output.xlsx"
        self.client.storage.from_(BUCKET).upload(
            path=path, file=data,
            file_options={
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "upsert": "true",
            },
        )
        return path

    def download_input(self, path: str) -> bytes:
        return self.client.storage.from_(BUCKET).download(path)

    def signed_output_url(self, path: str, expires_in: int = 900) -> str:
        resp = self.client.storage.from_(BUCKET).create_signed_url(path, expires_in)
        return resp["signedURL"]

    def delete_job(self, job_id: str) -> None:
        try:
            files = self.client.storage.from_(BUCKET).list(f"{job_id}/input")
            paths = [f"{job_id}/input/{f['name']}" for f in files]
            paths.append(f"{job_id}/output.xlsx")
            self.client.storage.from_(BUCKET).remove(paths)
        except Exception:
            pass
