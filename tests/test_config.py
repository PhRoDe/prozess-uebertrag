import pytest
from app.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")

    s = Settings(_env_file=None)
    assert s.anthropic_api_key == "sk-test"
    assert s.supabase_url == "https://x.supabase.co"
    assert s.supabase_service_key == "svc"
    assert s.max_file_size_mb == 10
    assert s.max_files_per_job == 10
    assert s.job_expiry_hours == 24


def test_settings_missing_env_raises(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(Exception):
        Settings(_env_file=None)
