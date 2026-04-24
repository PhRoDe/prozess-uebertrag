from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str
    supabase_url: str
    supabase_service_key: str
    app_password_hash: str
    session_secret: str
    public_base_url: str = "http://localhost:8000"

    # Limits (spec section 8)
    max_file_size_mb: int = 10
    max_files_per_job: int = 10
    job_expiry_hours: int = 24
    max_extract_chars: int = 120_000  # ~30k tokens input — guard against runaway cost

    # Claude
    claude_model: str = "claude-opus-4-7"
    claude_max_tokens: int = 16_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
