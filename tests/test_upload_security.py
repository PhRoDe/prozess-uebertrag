"""CSO: Upload-Größenlimit greift, OHNE die ganze Datei in den RAM zu ziehen
(begrenztes f.read(limit+1))."""
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


def _client():
    with patch("app.main.resume_stuck_jobs"):
        from app.main import app
        return TestClient(app, raise_server_exceptions=False)


def test_upload_lehnt_zu_grosse_datei_ab():
    from app.config import Settings
    tiny = Settings(max_file_size_mb=0, max_files_per_job=10,
                    anthropic_api_key="x", supabase_url="http://x", supabase_service_key="x")
    with patch("app.routes.upload.get_settings", return_value=tiny), \
         patch("app.routes.upload.JobsRepo") as Repo, \
         patch("app.routes.upload.StorageClient") as Storage, \
         patch("app.routes.upload.extract_job"), \
         patch("app.routes.upload.rate_allow", return_value=True):
        Repo.return_value.create.return_value = MagicMock(id="job-x")
        c = _client()
        r = c.post("/upload", headers={"X-Authentik-Username": "dev"},
                   files={"files": ("x.pdf", b"%PDF-1.4 content", "application/pdf")})
        assert r.status_code == 400
        assert "MB" in r.text
        # Rollback wurde versucht (Job + Storage aufgeräumt)
        Storage.return_value.delete_job.assert_called_once()


def test_upload_ohne_auth_401():
    with patch("app.routes.upload.JobsRepo"), patch("app.routes.upload.StorageClient"):
        c = _client()
        r = c.post("/upload", files={"files": ("x.pdf", b"%PDF", "application/pdf")})
        assert r.status_code == 401
