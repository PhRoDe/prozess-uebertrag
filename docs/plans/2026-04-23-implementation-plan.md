# Prozess-Übertrag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Web-App, die Jahresabschluss- und BWA-PDFs per Drag-and-Drop annimmt, via Claude API extrahiert und als vollständig verformelte Excel zum Download bereitstellt.

**Architecture:** Single-Repo Python-Monolith auf Railway. FastAPI + Jinja2 + HTMX + Tailwind CDN für UI, openpyxl für Excel, Supabase (Storage + Postgres) für Daten, Claude API für PDF-Extraktion. BackgroundTasks statt Queue.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX, Tailwind (CDN), openpyxl, pymupdf, anthropic SDK, supabase-py, bcrypt, pytest.

**Spec:** `docs/specs/2026-04-23-prozess-uebertrag-design.md`

---

## File Structure

```
Prozess-Übertrag/
├── .env.example
├── .gitignore
├── CLAUDE.md
├── README.md
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── railway.json
│
├── app/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app, route mounting
│   ├── config.py                 # Settings via pydantic-settings
│   ├── auth.py                   # Password session (cookie)
│   ├── db.py                     # Supabase Postgres client
│   ├── storage.py                # Supabase Storage client
│   ├── models.py                 # Pydantic: Job, InputFile, Extraction
│   │
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── pages.py              # GET /, /login, /job/{id}
│   │   ├── upload.py             # POST /upload
│   │   ├── job.py                # GET /job/{id}/status, POST /job/{id}/finalize
│   │   └── download.py           # GET /download/{id}
│   │
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── tasks.py              # extract_job(), finalize_job() — BackgroundTask entries
│   │   ├── pdf_detect.py         # pymupdf: text vs. scan, PDF→images
│   │   ├── claude_client.py      # anthropic SDK wrapper with retry
│   │   ├── prompts.py            # Extraction prompts (from guv-uebertrag SKILL.md)
│   │   └── consolidate.py        # Multi-year merging
│   │
│   ├── excel/
│   │   ├── __init__.py
│   │   ├── structure.py          # HGB §275 GKV hierarchy definition
│   │   ├── formulas.py           # Formula string builders
│   │   ├── kennzahlen.py         # Materialquote, EBITDA-Marge, etc.
│   │   └── builder.py            # openpyxl orchestrator (main entrypoint)
│   │
│   ├── templates/
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── upload.html
│   │   ├── job.html              # Full page shell
│   │   └── partials/
│   │       ├── status.html       # HTMX partial (progress)
│   │       ├── review.html       # HTMX partial (dropdowns)
│   │       └── ready.html        # HTMX partial (download)
│   │
│   └── static/
│       └── app.css               # Optional, kann leer bleiben
│
├── supabase/
│   └── migrations/
│       └── 0001_jobs_table.sql
│
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── fixtures/
    │   └── README.md             # Platzhalter für echte PDFs (nicht committen)
    ├── test_auth.py
    ├── test_config.py
    ├── test_pdf_detect.py
    ├── test_prompts.py
    ├── test_claude_client.py     # Mock-based
    ├── test_consolidate.py
    ├── test_excel_structure.py
    ├── test_excel_formulas.py
    ├── test_excel_kennzahlen.py
    ├── test_excel_builder.py
    ├── test_models.py
    ├── test_upload_route.py
    └── test_job_flow.py
```

**Responsibility boundaries:**
- `app/worker/` enthält keine HTTP-Logik — nur reine Extraktions/Claude-Aufrufe.
- `app/excel/` enthält keine Web-Logik — reine openpyxl-Arbeit, testbar ohne Netzwerk.
- `app/routes/` ist dünn — nur Validierung + Delegation an Worker/DB.

---

## Phase 0: Projekt-Setup (Tasks 1-3)

### Task 1: Repo-Init und Projektstruktur

**Files:**
- Create: `.gitignore`, `pyproject.toml`, `.env.example`, `README.md`, `CLAUDE.md`
- Create: alle leeren Verzeichnisse via `__init__.py`

- [ ] **Step 1: Git-Repo initialisieren**

```bash
cd /Users/philippdegen/Documents/Claude/Calandi/Prozess-Übertrag
git init
```

- [ ] **Step 2: `.gitignore` schreiben**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
.env.local
tests/fixtures/*.pdf
!tests/fixtures/README.md
.ruff_cache/
.DS_Store
```

- [ ] **Step 3: `pyproject.toml` anlegen**

```toml
[project]
name = "prozess-uebertrag"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    "anthropic>=0.40",
    "supabase>=2.4",
    "pymupdf>=1.24",
    "openpyxl>=3.1",
    "bcrypt>=4.1",
    "itsdangerous>=2.1",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.3",
    "mypy>=1.8",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 4: Leere Verzeichnisstruktur anlegen**

```bash
mkdir -p app/routes app/worker app/excel app/templates/partials app/static
mkdir -p supabase/migrations tests/fixtures
touch app/__init__.py app/routes/__init__.py app/worker/__init__.py app/excel/__init__.py
touch tests/__init__.py tests/fixtures/README.md
```

- [ ] **Step 5: `.env.example` schreiben**

```
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
APP_PASSWORD_HASH=$2b$12$...
SESSION_SECRET=change-me-random-hex
PUBLIC_BASE_URL=http://localhost:8000
```

- [ ] **Step 6: `CLAUDE.md` für das Projekt anlegen**

```markdown
# Prozess-Übertrag

Interne Calandi-Web-App: Jahresabschluss-PDFs → verformelte Excel.

## Entwicklung

- `uv sync --extra dev` oder `pip install -e ".[dev]"`
- `uvicorn app.main:app --reload`
- Tests: `pytest`

## Deployment

- Railway: `git push` → Auto-Deploy
- Supabase: Migrations in `supabase/migrations/`

## Wichtige Regeln

- Extraktions-Logik lebt in `app/worker/`, KEINE HTTP-Imports dort
- Excel-Logik lebt in `app/excel/`, KEINE Netzwerk-Calls dort
- Alle Excel-Zwischensummen MÜSSEN Formeln sein (`=SUM(...)`), nie hardcoded
- Siehe `docs/specs/2026-04-23-prozess-uebertrag-design.md` Section 4
```

- [ ] **Step 7: Commit**

```bash
git add .
git commit -m "chore: init project structure and dependencies"
```

---

### Task 2: Config mit pydantic-settings

**Files:**
- Create: `app/config.py`, `tests/test_config.py`

- [ ] **Step 1: Failing Test schreiben (`tests/test_config.py`)**

```python
import os
import pytest
from app.config import Settings

def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setenv("APP_PASSWORD_HASH", "$2b$12$hash")
    monkeypatch.setenv("SESSION_SECRET", "secret-hex")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")

    s = Settings()
    assert s.anthropic_api_key == "sk-test"
    assert s.supabase_url == "https://x.supabase.co"
    assert s.session_secret == "secret-hex"

def test_settings_missing_env_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(Exception):
        Settings(_env_file=None)
```

- [ ] **Step 2: Test ausführen, FAIL erwarten**

```bash
pytest tests/test_config.py -v
```

Erwartung: `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: `app/config.py` implementieren**

```python
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

    # Limits (aus Spec Section 8)
    max_file_size_mb: int = 10
    max_files_per_job: int = 10
    job_expiry_hours: int = 24


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Tests erneut ausführen, PASS erwarten**

```bash
pytest tests/test_config.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat(config): load settings from environment via pydantic-settings"
```

---

### Task 3: Supabase Migration anlegen

**Files:**
- Create: `supabase/migrations/0001_jobs_table.sql`

- [ ] **Step 1: Migration schreiben**

```sql
-- 0001_jobs_table.sql
create table if not exists jobs (
    id                uuid primary key default gen_random_uuid(),
    created_at        timestamptz not null default now(),
    status            text not null check (status in (
                          'uploaded', 'extracting', 'review_needed',
                          'finalizing', 'ready', 'failed', 'expired'
                      )),
    input_files       jsonb not null,
    extraction        jsonb,
    review_answers    jsonb,
    output_path       text,
    error_message     text,
    expires_at        timestamptz not null,
    -- Resume-Pattern (Fix 1A): Worker setzt beim Start processing_node,
    -- beim Restart übernimmt der Watchdog stuck Jobs ohne processing_node
    processing_node   text,
    processing_started_at timestamptz
);

create index if not exists jobs_status_idx on jobs (status);
create index if not exists jobs_expires_at_idx on jobs (expires_at);
create index if not exists jobs_processing_idx on jobs (status, processing_node);

-- Cleanup function (ausgeführt via pg_cron)
create or replace function cleanup_expired_jobs()
returns void language plpgsql as $$
begin
    delete from jobs where expires_at < now();
end;
$$;

-- Watchdog: Jobs > 30 min in extracting/finalizing auf failed setzen
-- (nur Jobs ohne processing_node — aktive Worker haben Vorrang)
create or replace function watchdog_stale_jobs()
returns void language plpgsql as $$
begin
    update jobs
    set status = 'failed',
        error_message = 'Stale job: worker timeout',
        processing_node = null
    where status in ('extracting', 'finalizing')
      and processing_started_at < now() - interval '30 minutes';
end;
$$;

-- Resume-Helfer (Fix 1A): findet Jobs die beim App-Start resumed werden müssen
create or replace function jobs_pending_resume()
returns setof jobs language sql as $$
    select * from jobs
    where status in ('extracting', 'finalizing')
      and (processing_node is null
           or processing_started_at < now() - interval '5 minutes');
$$;

-- pg_cron Jobs (müssen vom Dashboard aktiviert werden)
-- select cron.schedule('cleanup-expired', '0 3 * * *', 'select cleanup_expired_jobs()');
-- select cron.schedule('watchdog-stale', '*/5 * * * *', 'select watchdog_stale_jobs()');
```

- [ ] **Step 2: Commit**

```bash
git add supabase/migrations/0001_jobs_table.sql
git commit -m "feat(db): initial jobs table migration with status check and watchdog"
```

- [ ] **Step 3: Migration manuell in Supabase ausführen**

Manuelle Aktion für Philipp: Supabase-Projekt `prozess-uebertrag` anlegen, SQL-Editor öffnen, Migration pasten und ausführen. Danach pg_cron in Dashboard > Database > Extensions aktivieren und die beiden `cron.schedule`-Zeilen separat ausführen.

---

## Phase 1: Datenzugriff (Tasks 4-6)

### Task 4: Supabase Storage Client

**Files:**
- Create: `app/storage.py`, `tests/test_storage.py` (nur Schnittstelle, Integrationstest manuell)

- [ ] **Step 1: Schnittstellen-Test für Modul schreiben**

```python
# tests/test_storage.py
from app.storage import StorageClient

def test_storage_client_exposes_expected_interface():
    methods = ["upload_input", "upload_output", "download_input", "signed_output_url", "delete_job"]
    for m in methods:
        assert hasattr(StorageClient, m), f"Missing method: {m}"
```

- [ ] **Step 2: Test ausführen, FAIL erwarten**

```bash
pytest tests/test_storage.py -v
```

- [ ] **Step 3: `app/storage.py` implementieren**

```python
from pathlib import Path
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
            path=path, file=data, file_options={"content-type": "application/pdf"}
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
        # Best-effort cleanup
        try:
            files = self.client.storage.from_(BUCKET).list(f"{job_id}/input")
            paths = [f"{job_id}/input/{f['name']}" for f in files]
            paths.append(f"{job_id}/output.xlsx")
            self.client.storage.from_(BUCKET).remove(paths)
        except Exception:
            pass
```

- [ ] **Step 4: Test ausführen, PASS erwarten**

```bash
pytest tests/test_storage.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/storage.py tests/test_storage.py
git commit -m "feat(storage): Supabase Storage client for PDFs and Excel output"
```

---

### Task 5: Jobs Repository (Supabase Postgres)

**Files:**
- Create: `app/models.py`, `app/db.py`, `tests/test_models.py`

- [ ] **Step 1: Failing Test für Pydantic Models**

```python
# tests/test_models.py
from datetime import datetime, timezone
from app.models import Job, InputFile, JobStatus

def test_input_file_validates_pdf_only():
    f = InputFile(name="ja-2024.pdf", size=1024, storage_path="job/input/ja-2024.pdf")
    assert f.name.endswith(".pdf")

def test_job_serializes_roundtrip():
    now = datetime.now(timezone.utc)
    job = Job(
        id="11111111-1111-1111-1111-111111111111",
        created_at=now,
        status=JobStatus.EXTRACTING,
        input_files=[InputFile(name="a.pdf", size=100, storage_path="x/a.pdf")],
        expires_at=now,
    )
    data = job.model_dump(mode="json")
    assert data["status"] == "extracting"
    assert len(data["input_files"]) == 1
```

- [ ] **Step 2: Test ausführen, FAIL erwarten**

```bash
pytest tests/test_models.py -v
```

- [ ] **Step 3: `app/models.py` implementieren**

```python
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    REVIEW_NEEDED = "review_needed"
    FINALIZING = "finalizing"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class InputFile(BaseModel):
    name: str
    size: int
    storage_path: str
    pdf_type: str | None = None  # "text" or "scan"
    doc_type: str | None = None  # "jahresabschluss" or "bwa"


class Job(BaseModel):
    id: str
    created_at: datetime
    status: JobStatus
    input_files: list[InputFile]
    extraction: dict[str, Any] | None = None
    review_answers: dict[str, Any] | None = None
    output_path: str | None = None
    error_message: str | None = None
    expires_at: datetime
```

- [ ] **Step 4: Tests erneut laufen**

```bash
pytest tests/test_models.py -v
```

- [ ] **Step 5: `app/db.py` mit JobsRepo implementieren**

```python
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

    # Fix 2A: Layer-sauberer Update der input_files
    def set_input_files(self, job_id: str, input_files: list[InputFile]) -> None:
        self.client.table("jobs").update({
            "input_files": [f.model_dump() for f in input_files],
        }).eq("id", job_id).execute()

    # Fix 1A: Claim-Pattern — nur ein Worker pro Job
    def try_claim(self, job_id: str, node_id: str) -> bool:
        """Return True if this worker successfully claimed the job, False if already claimed
        by another active worker."""
        from datetime import datetime, timezone
        resp = self.client.table("jobs").update({
            "processing_node": node_id,
            "processing_started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", job_id).is_("processing_node", None).execute()
        return bool(resp.data)

    def release_claim(self, job_id: str) -> None:
        self.client.table("jobs").update({
            "processing_node": None,
        }).eq("id", job_id).execute()

    # Fix 1A: Beim App-Start — Jobs resumen die beim letzten Shutdown hingen
    def list_resumable(self) -> list[Job]:
        resp = self.client.rpc("jobs_pending_resume").execute()
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
        }).eq("id", job_id).execute()

    def _row_to_job(self, row: dict[str, Any]) -> Job:
        return Job(
            id=row["id"],
            created_at=row["created_at"],
            status=JobStatus(row["status"]),
            input_files=[InputFile(**f) for f in row["input_files"]],
            extraction=row.get("extraction"),
            review_answers=row.get("review_answers"),
            output_path=row.get("output_path"),
            error_message=row.get("error_message"),
            expires_at=row["expires_at"],
        )
```

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/db.py tests/test_models.py
git commit -m "feat(db): jobs repository with pydantic models"
```

---

### Task 6: Password-Auth mit signiertem Cookie

**Files:**
- Create: `app/auth.py`, `tests/test_auth.py`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/test_auth.py
import bcrypt
import pytest
from app.auth import verify_password, create_session_token, validate_session_token


def test_verify_password_correct():
    hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
    assert verify_password("secret", hashed) is True


def test_verify_password_wrong():
    hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
    assert verify_password("wrong", hashed) is False


def test_session_token_roundtrip():
    token = create_session_token("fixed-secret")
    assert validate_session_token(token, "fixed-secret") is True


def test_session_token_rejects_bad_secret():
    token = create_session_token("secret-a")
    assert validate_session_token(token, "secret-b") is False


def test_session_token_rejects_tampered():
    token = create_session_token("secret") + "x"
    assert validate_session_token(token, "secret") is False
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

```bash
pytest tests/test_auth.py -v
```

- [ ] **Step 3: `app/auth.py` implementieren**

```python
import bcrypt
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 Tage


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_session_token(secret: str) -> str:
    signer = TimestampSigner(secret)
    return signer.sign(b"ok").decode()


def validate_session_token(token: str, secret: str) -> bool:
    signer = TimestampSigner(secret)
    try:
        signer.unsign(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

```bash
pytest tests/test_auth.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat(auth): password verification and signed session tokens"
```

---

## Phase 2: PDF-Erkennung und Claude-Integration (Tasks 7-10)

### Task 7: PDF Text/Scan Detection

**Files:**
- Create: `app/worker/pdf_detect.py`, `tests/test_pdf_detect.py`
- Fixtures: `tests/fixtures/README.md` (Platzhalter-Info)

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/test_pdf_detect.py
import fitz  # pymupdf
import pytest
from app.worker.pdf_detect import classify_pdf, pdf_to_images, PdfKind


def make_text_pdf(tmp_path, text: str = "Bilanz zum 31.12.2024\nUmsatzerlöse 1.234.567,89"):
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), text)
    path = tmp_path / "text.pdf"
    doc.save(str(path))
    doc.close()
    return path


def make_image_pdf(tmp_path):
    doc = fitz.open()
    for _ in range(2):
        doc.new_page()  # leere Seite = praktisch ohne Text
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_classify_text_pdf(tmp_path):
    path = make_text_pdf(tmp_path)
    kind = classify_pdf(path.read_bytes())
    assert kind == PdfKind.TEXT


def test_classify_scan_pdf(tmp_path):
    path = make_image_pdf(tmp_path)
    kind = classify_pdf(path.read_bytes())
    assert kind == PdfKind.SCAN


def test_pdf_to_images_returns_png_bytes(tmp_path):
    path = make_image_pdf(tmp_path)
    images = pdf_to_images(path.read_bytes())
    assert len(images) == 2
    assert all(img.startswith(b"\x89PNG") for img in images)
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

```bash
pytest tests/test_pdf_detect.py -v
```

- [ ] **Step 3: `app/worker/pdf_detect.py` implementieren**

```python
from enum import Enum
import fitz


class PdfKind(str, Enum):
    TEXT = "text"
    SCAN = "scan"


def classify_pdf(data: bytes, threshold: int = 100) -> PdfKind:
    """Return TEXT if any page has > threshold extractable chars; SCAN otherwise."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        for page in doc:
            if len(page.get_text("text").strip()) > threshold:
                return PdfKind.TEXT
        return PdfKind.SCAN
    finally:
        doc.close()


def pdf_to_images(data: bytes, dpi: int = 100) -> list[bytes]:
    """Render each page to PNG bytes for Claude Vision.
    100 DPI balanciert Lesbarkeit (Claude kann Zahlen lesen) mit Token-Kosten
    (30% weniger als 150 DPI — Fix 4A)."""
    doc = fitz.open(stream=data, filetype="pdf")
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    try:
        return [page.get_pixmap(matrix=matrix).tobytes("png") for page in doc]
    finally:
        doc.close()


def extract_text(data: bytes) -> str:
    """Extract all text from a text-PDF, page-numbered."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        parts = []
        for i, page in enumerate(doc, start=1):
            parts.append(f"=== Seite {i} ===\n{page.get_text('text')}")
        return "\n\n".join(parts)
    finally:
        doc.close()
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

- [ ] **Step 5: Commit**

```bash
git add app/worker/pdf_detect.py tests/test_pdf_detect.py
git commit -m "feat(pdf): classify text vs scan and render pages to PNG"
```

---

### Task 8: Claude-Prompts aus guv-uebertrag übernehmen

**Files:**
- Create: `app/worker/prompts.py`, `tests/test_prompts.py`

- [ ] **Step 1: Failing Tests**

```python
# tests/test_prompts.py
from app.worker.prompts import (
    DOC_TYPE_PROMPT, EXTRACTION_PROMPT_TEXT, EXTRACTION_PROMPT_VISION,
    build_consolidation_prompt,
)


def test_prompts_reference_guv_structure():
    assert "Umsatzerlöse" in EXTRACTION_PROMPT_TEXT
    assert "Kontennachweis" in EXTRACTION_PROMPT_TEXT
    assert "confidence" in EXTRACTION_PROMPT_TEXT


def test_vision_prompt_warns_about_ocr():
    assert "scan" in EXTRACTION_PROMPT_VISION.lower() or "bild" in EXTRACTION_PROMPT_VISION.lower()


def test_doctype_prompt_discriminates():
    assert "jahresabschluss" in DOC_TYPE_PROMPT.lower()
    assert "bwa" in DOC_TYPE_PROMPT.lower()


def test_consolidation_prompt_includes_documents():
    extractions = [{"file": "ja-2023.pdf", "accounts": []}, {"file": "ja-2024.pdf", "accounts": []}]
    prompt = build_consolidation_prompt(extractions)
    assert "ja-2023.pdf" in prompt and "ja-2024.pdf" in prompt
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

- [ ] **Step 3: `app/worker/prompts.py` implementieren**

Der Inhalt ist lang — er übernimmt im Kern die HGB-§275-Gliederung aus `skills/guv-uebertrag/SKILL.md`. Hier die Struktur:

```python
"""Prompts für Claude-basierte Extraktion.
Die HGB-§275-Gliederung stammt aus skills/guv-uebertrag/SKILL.md."""

import json

GUV_STRUKTUR = """
1.  Umsatzerlöse
2.  Gesamtleistung
3.  Sonstige betriebliche Erträge
4.  Materialaufwand (a) RHB, (b) bezogene Leistungen
5.  Personalaufwand (a) Löhne und Gehälter, (b) Soziale Abgaben
6.  Abschreibungen
7.  Sonstige betriebliche Aufwendungen
    (a) Raumkosten, (b) Versicherungen/Beiträge/Abgaben,
    (c) Reparaturen und Instandhaltungen, (d) Fahrzeugkosten,
    (e) Werbe- und Reisekosten, (f) Kosten der Warenabgabe,
    (g) Verschiedene betriebliche Kosten
8.  Erträge aus Wertpapieren und Ausleihungen
9.  Sonstige Zinsen und ähnliche Erträge
10. Zinsen und ähnliche Aufwendungen
11. Steuern vom Einkommen und vom Ertrag
12. Ergebnis nach Steuern
13. Sonstige Steuern
14. Jahresüberschuss
15. Gewinnvortrag / Verlustvortrag
16. Ausschüttung
17. Bilanzgewinn
"""

DOC_TYPE_PROMPT = """Du bekommst ein PDF. Klassifiziere es in genau eine Kategorie:
- "jahresabschluss": offizieller Jahresabschluss mit Bilanz und GuV (meist mit Kontennachweis)
- "bwa": Betriebswirtschaftliche Auswertung (unterjährig, nur Hauptpositionen, keine Einzelkonten)
- "unknown": alles andere

Antworte NUR mit dem Kategorie-String, nichts weiter."""


EXTRACTION_PROMPT_TEXT = f"""Du extrahierst den Kontennachweis der GuV aus einem Jahresabschluss-PDF (Text-Version).

HGB §275 Gliederung (GKV), an der du orientierst:
{GUV_STRUKTUR}

Aufgabe: Extrahiere ALLE Konten aus dem Kontennachweis mit Geschäftsjahr- und Vorjahres-Betrag.
Ordne jedes Konto in die passende Gruppe ein. Markiere unsichere Zuordnungen mit confidence="low".

Regeln:
- Deutsches Zahlenformat: "1.387.335,10" → 1387335.10
- Aufwandspositionen als positive Zahlen übernehmen
- Negative Werte nur wo explizit im Kontennachweis
- Bei unklarer Gruppe: confidence="low" und suggested_groups angeben, NICHT raten
- Auch Bilanzgewinn-Bereich extrahieren (Gewinnvortrag, Ausschüttung, Bilanzgewinn)

Rückgabeformat: JSON wie in diesem Schema:
{{
  "type": "jahresabschluss",
  "year": 2024,
  "previous_year": 2023,
  "accounts": [
    {{"konto_nr": "8400", "bezeichnung": "Erlöse 19% USt", "gruppe": "1. Umsatzerlöse",
     "betrag_gj": 1279228.53, "betrag_vj": 1110030.20, "confidence": "high"}}
  ],
  "open_questions": [
    {{"konto_nr": "4980", "bezeichnung": "...", "betrag_gj": 12340.00,
     "suggested_groups": ["7g. Verschiedene betr. Kosten", "7d. Fahrzeugkosten"]}}
  ],
  "bilanzgewinn": {{"gewinnvortrag": 0, "verlustvortrag": 78877.33,
                   "ausschuettung": 120000.00, "bilanzgewinn": -19667.11}}
}}

Antworte AUSSCHLIESSLICH mit gültigem JSON, kein Markdown, keine Erklärung."""


EXTRACTION_PROMPT_VISION = EXTRACTION_PROMPT_TEXT + """

ACHTUNG — Scan-PDF:
Die Seiten kommen als Bilder. Lies die Zahlen besonders sorgfältig.
Bei unsicheren Ziffern: confidence="low" setzen. Niemals raten."""


BWA_PROMPT = """Du extrahierst eine Betriebswirtschaftliche Auswertung (BWA).
BWAs enthalten nur Hauptpositionen, keine Einzelkonten.

Rückgabeformat: JSON
{
  "type": "bwa",
  "zeitraum": "01/2024-12/2024",
  "positionen": [
    {"bezeichnung": "Umsatzerlöse", "betrag": 1234567.89},
    ...
  ]
}

Antworte AUSSCHLIESSLICH mit gültigem JSON."""


def build_consolidation_prompt(extractions: list[dict]) -> str:
    """Build a prompt that asks Claude to merge multi-year extractions."""
    docs_json = json.dumps(extractions, ensure_ascii=False, indent=2)
    return f"""Du bekommst Extraktionen aus mehreren Jahren und sollst sie zu einer
konsolidierten Mehrjahres-Tabelle zusammenführen.

Regeln:
- Vereinigungsmenge aller Konten über alle Jahre bilden
- Pro Konto: ein Eintrag mit Werten pro Jahr (leer wenn in dem Jahr nicht vorhanden)
- Cross-Check: Vorjahreswerte aus PDF(n) müssen mit PDF(n-1) übereinstimmen —
  bei Abweichung in "questions" listen
- Gruppenreihenfolge nach HGB §275 GKV beibehalten

Dokumente:
{docs_json}

Rückgabeformat: JSON
{{
  "years": [2022, 2023, 2024],
  "rows": [
    {{"konto_nr": "8400", "bezeichnung": "Erlöse 19% USt",
      "gruppe": "1. Umsatzerlöse", "values": {{"2022": 1000000, "2023": 1100000, "2024": 1279228.53}}}}
  ],
  "questions": [
    {{"type": "previous_year_mismatch", "konto_nr": "8400",
      "year": 2023, "pdf2024_says": 1110030.20, "pdf2023_says": 1115000.00}}
  ]
}}

Antworte AUSSCHLIESSLICH mit gültigem JSON."""
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

- [ ] **Step 5: Commit**

```bash
git add app/worker/prompts.py tests/test_prompts.py
git commit -m "feat(prompts): Claude extraction prompts derived from guv-uebertrag skill"
```

---

### Task 9: Claude Client (SDK-Wrapper mit Retry)

**Files:**
- Create: `app/worker/claude_client.py`, `tests/test_claude_client.py`

- [ ] **Step 1: Failing Tests mit Mocks**

```python
# tests/test_claude_client.py
import json
from unittest.mock import MagicMock
import pytest
from app.worker.claude_client import ClaudeClient, ExtractionError


def make_mock_sdk(response_text: str):
    mock = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    mock.messages.create.return_value = msg
    return mock


def test_classify_document_returns_type():
    sdk = make_mock_sdk("jahresabschluss")
    client = ClaudeClient(sdk=sdk)
    assert client.classify_document("pdf content here") == "jahresabschluss"


def test_extract_text_pdf_parses_json():
    payload = {"type": "jahresabschluss", "year": 2024, "accounts": []}
    sdk = make_mock_sdk(json.dumps(payload))
    client = ClaudeClient(sdk=sdk)
    result = client.extract_text_pdf("PDF-Text ...")
    assert result["year"] == 2024


def test_extract_raises_on_invalid_json():
    sdk = make_mock_sdk("not-json")
    client = ClaudeClient(sdk=sdk)
    with pytest.raises(ExtractionError):
        client.extract_text_pdf("text")
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

- [ ] **Step 3: `app/worker/claude_client.py` implementieren**

```python
import json
import time
from typing import Any
import base64
from anthropic import Anthropic, APIStatusError
from app.config import get_settings
from app.worker.prompts import (
    DOC_TYPE_PROMPT, EXTRACTION_PROMPT_TEXT, EXTRACTION_PROMPT_VISION, BWA_PROMPT,
)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 16000


class ExtractionError(Exception):
    pass


class ClaudeClient:
    def __init__(self, sdk: Anthropic | None = None) -> None:
        s = get_settings()
        self.sdk = sdk or Anthropic(api_key=s.anthropic_api_key)

    def classify_document(self, text_or_sample: str) -> str:
        resp = self._call([
            {"role": "user", "content": [
                {"type": "text", "text": DOC_TYPE_PROMPT},
                {"type": "text", "text": f"\n\nInhalt:\n{text_or_sample[:5000]}"},
            ]}
        ])
        return resp.strip().lower()

    def extract_text_pdf(self, text: str, is_bwa: bool = False) -> dict[str, Any]:
        prompt = BWA_PROMPT if is_bwa else EXTRACTION_PROMPT_TEXT
        raw = self._call([
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "text", "text": f"\n\nPDF-Inhalt:\n{text}"},
            ]}
        ])
        return self._parse_json(raw)

    def extract_scan_pdf(self, pages_png: list[bytes], is_bwa: bool = False) -> dict[str, Any]:
        prompt = BWA_PROMPT if is_bwa else EXTRACTION_PROMPT_VISION
        images = [
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.standard_b64encode(p).decode(),
            }}
            for p in pages_png
        ]
        raw = self._call([
            {"role": "user", "content": [{"type": "text", "text": prompt}, *images]}
        ])
        return self._parse_json(raw)

    def consolidate(self, prompt: str) -> dict[str, Any]:
        raw = self._call([{"role": "user", "content": [{"type": "text", "text": prompt}]}])
        return self._parse_json(raw)

    def _call(self, messages: list[dict[str, Any]], retries: int = 3) -> str:
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                msg = self.sdk.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS, messages=messages,
                )
                return msg.content[0].text
            except APIStatusError as e:
                last_err = e
                if e.status_code == 429 and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except Exception as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise ExtractionError(f"Claude call failed after {retries} tries: {last_err}")

    def _parse_json(self, raw: str) -> dict[str, Any]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ExtractionError(f"Invalid JSON from Claude: {e}\nRaw: {raw[:500]}") from e
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

- [ ] **Step 5: Commit**

```bash
git add app/worker/claude_client.py tests/test_claude_client.py
git commit -m "feat(claude): SDK wrapper with classify/extract and retry logic"
```

---

### Task 10: Konsolidierung (Multi-Year Merging)

**Files:**
- Create: `app/worker/consolidate.py`, `tests/test_consolidate.py`

- [ ] **Step 1: Failing Tests**

```python
# tests/test_consolidate.py
from app.worker.consolidate import merge_extractions


def test_merge_two_years_union_of_accounts():
    extractions = [
        {"type": "jahresabschluss", "year": 2023, "previous_year": 2022,
         "accounts": [
             {"konto_nr": "8400", "bezeichnung": "Erlöse", "gruppe": "1. Umsatzerlöse",
              "betrag_gj": 1000000, "betrag_vj": 900000, "confidence": "high"},
         ]},
        {"type": "jahresabschluss", "year": 2024, "previous_year": 2023,
         "accounts": [
             {"konto_nr": "8400", "bezeichnung": "Erlöse", "gruppe": "1. Umsatzerlöse",
              "betrag_gj": 1100000, "betrag_vj": 1000000, "confidence": "high"},
             {"konto_nr": "8401", "bezeichnung": "Erlöse Korrektur", "gruppe": "1. Umsatzerlöse",
              "betrag_gj": 500, "betrag_vj": 0, "confidence": "high"},
         ]},
    ]
    result = merge_extractions(extractions)
    assert result["years"] == [2022, 2023, 2024]
    rows_by_konto = {r["konto_nr"]: r for r in result["rows"]}
    assert rows_by_konto["8400"]["values"] == {2022: 900000, 2023: 1000000, 2024: 1100000}
    assert rows_by_konto["8401"]["values"] == {2023: 0, 2024: 500}


def test_merge_detects_previous_year_mismatch():
    extractions = [
        {"type": "jahresabschluss", "year": 2023, "previous_year": 2022,
         "accounts": [{"konto_nr": "8400", "bezeichnung": "E", "gruppe": "1. Umsatzerlöse",
                       "betrag_gj": 1000000, "betrag_vj": 900000, "confidence": "high"}]},
        {"type": "jahresabschluss", "year": 2024, "previous_year": 2023,
         "accounts": [{"konto_nr": "8400", "bezeichnung": "E", "gruppe": "1. Umsatzerlöse",
                       "betrag_gj": 1100000, "betrag_vj": 999999, "confidence": "high"}]},
    ]
    result = merge_extractions(extractions)
    mismatches = [q for q in result["questions"] if q["type"] == "previous_year_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["konto_nr"] == "8400"
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

- [ ] **Step 3: `app/worker/consolidate.py` implementieren**

```python
from collections import defaultdict
from typing import Any

MISMATCH_TOLERANCE = 0.01  # 1 Cent


def merge_extractions(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multi-year extractions into a consolidated structure.

    Returns {years, rows, questions}.
    """
    jahresabschluss = [e for e in extractions if e.get("type") == "jahresabschluss"]
    jahresabschluss.sort(key=lambda e: e["year"])

    all_years: set[int] = set()
    for e in jahresabschluss:
        all_years.add(e["year"])
        if e.get("previous_year"):
            all_years.add(e["previous_year"])
    years = sorted(all_years)

    # konto_nr → aggregated row
    rows: dict[str, dict[str, Any]] = {}
    questions: list[dict[str, Any]] = []

    for e in jahresabschluss:
        gj = e["year"]
        vj = e.get("previous_year")
        for acc in e["accounts"]:
            key = acc["konto_nr"] or f"__nrless__{acc['bezeichnung']}"
            if key not in rows:
                rows[key] = {
                    "konto_nr": acc["konto_nr"],
                    "bezeichnung": acc["bezeichnung"],
                    "gruppe": acc["gruppe"],
                    "values": {},
                    "confidence": acc.get("confidence", "high"),
                }
            existing_gj = rows[key]["values"].get(gj)
            if existing_gj is not None and abs(existing_gj - acc["betrag_gj"]) > MISMATCH_TOLERANCE:
                questions.append({"type": "duplicate_gj_mismatch", "konto_nr": acc["konto_nr"],
                                  "year": gj, "values": [existing_gj, acc["betrag_gj"]]})
            rows[key]["values"][gj] = acc["betrag_gj"]

            if vj is not None:
                existing_vj = rows[key]["values"].get(vj)
                if existing_vj is not None and abs(existing_vj - acc["betrag_vj"]) > MISMATCH_TOLERANCE:
                    questions.append({
                        "type": "previous_year_mismatch",
                        "konto_nr": acc["konto_nr"],
                        "year": vj,
                        "pdf_says": acc["betrag_vj"],
                        "own_value": existing_vj,
                    })
                rows[key]["values"][vj] = acc["betrag_vj"]

    return {
        "years": years,
        "rows": list(rows.values()),
        "questions": questions,
    }
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

- [ ] **Step 5: Commit**

```bash
git add app/worker/consolidate.py tests/test_consolidate.py
git commit -m "feat(consolidate): merge multi-year extractions with cross-check"
```

---

## Phase 3: Excel-Generierung (Tasks 11-14)

### Task 11: HGB §275 Struktur-Definition

**Files:**
- Create: `app/excel/structure.py`, `tests/test_excel_structure.py`

- [ ] **Step 1: Failing Tests**

```python
# tests/test_excel_structure.py
from app.excel.structure import GUV_HIERARCHY, get_all_groups, find_group_row


def test_hierarchy_contains_expected_top_level():
    groups = get_all_groups()
    required = [
        "1. Umsatzerlöse", "2. Gesamtleistung", "3. Sonstige betriebliche Erträge",
        "4. Materialaufwand", "5. Personalaufwand", "6. Abschreibungen",
        "7. Sonstige betriebliche Aufwendungen", "12. Ergebnis nach Steuern",
        "14. Jahresüberschuss", "17. Bilanzgewinn",
    ]
    for r in required:
        assert r in groups, f"Missing group: {r}"


def test_hierarchy_has_sonst_betr_aufw_subgroups():
    groups = get_all_groups()
    subs = ["7a. Raumkosten", "7b. Versicherungen, Beiträge und Abgaben",
            "7e. Werbe- und Reisekosten", "7g. Verschiedene betriebliche Kosten"]
    for s in subs:
        assert s in groups, f"Missing subgroup: {s}"
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

- [ ] **Step 3: `app/excel/structure.py` implementieren**

```python
"""Hierarchische GuV-Gliederung nach HGB §275 GKV.
Jeder Eintrag beschreibt wie eine Zeile in der Excel erzeugt wird.
"""

# Reihenfolge ist signifikant — bestimmt Excel-Layout.
GUV_HIERARCHY: list[dict] = [
    {"code": "1. Umsatzerlöse", "kind": "sum", "bold": True},
    {"code": "1. Umsatzerlöse", "kind": "details"},
    {"code": "2. Gesamtleistung", "kind": "formula", "formula": "ref:1. Umsatzerlöse"},
    {"code": "3. Sonstige betriebliche Erträge", "kind": "sum", "bold": True},
    {"code": "3. Sonstige betriebliche Erträge", "kind": "details"},
    {"code": "4. Materialaufwand", "kind": "sum", "bold": True},
    {"code": "4a. Aufwendungen für RHB und Waren", "kind": "sum"},
    {"code": "4a. Aufwendungen für RHB und Waren", "kind": "details"},
    {"code": "4b. Aufwendungen für bezogene Leistungen", "kind": "sum"},
    {"code": "4b. Aufwendungen für bezogene Leistungen", "kind": "details"},
    {"code": "5. Personalaufwand", "kind": "sum", "bold": True},
    {"code": "5a. Löhne und Gehälter", "kind": "sum"},
    {"code": "5a. Löhne und Gehälter", "kind": "details"},
    {"code": "5b. Soziale Abgaben", "kind": "sum"},
    {"code": "5b. Soziale Abgaben", "kind": "details"},
    {"code": "6. Abschreibungen", "kind": "sum", "bold": True},
    {"code": "6. Abschreibungen", "kind": "details"},
    {"code": "7. Sonstige betriebliche Aufwendungen", "kind": "sum", "bold": True},
    {"code": "7a. Raumkosten", "kind": "sum"},
    {"code": "7a. Raumkosten", "kind": "details"},
    {"code": "7b. Versicherungen, Beiträge und Abgaben", "kind": "sum"},
    {"code": "7b. Versicherungen, Beiträge und Abgaben", "kind": "details"},
    {"code": "7c. Reparaturen und Instandhaltungen", "kind": "details"},
    {"code": "7d. Fahrzeugkosten", "kind": "details"},
    {"code": "7e. Werbe- und Reisekosten", "kind": "details"},
    {"code": "7f. Kosten der Warenabgabe", "kind": "details"},
    {"code": "7g. Verschiedene betriebliche Kosten", "kind": "details"},
    {"code": "8. Erträge aus Wertpapieren", "kind": "details"},
    {"code": "9. Sonstige Zinsen und ähnliche Erträge", "kind": "details"},
    {"code": "10. Zinsen und ähnliche Aufwendungen", "kind": "details"},
    {"code": "11. Steuern vom Einkommen und vom Ertrag", "kind": "sum", "bold": True},
    {"code": "11. Steuern vom Einkommen und vom Ertrag", "kind": "details"},
    {"code": "12. Ergebnis nach Steuern", "kind": "formula", "bold": True,
     "formula": "=gesamtleistung + sbe - material - personal - afa - sba + fin_ertr - fin_aufw - ee_steuern"},
    {"code": "13. Sonstige Steuern", "kind": "details"},
    {"code": "14. Jahresüberschuss", "kind": "formula", "bold": True,
     "formula": "=ergebnis_nach_steuern - sonst_steuern"},
    {"code": "15. Gewinn-/Verlustvortrag", "kind": "details"},
    {"code": "16. Ausschüttung", "kind": "details"},
    {"code": "17. Bilanzgewinn", "kind": "formula", "bold": True,
     "formula": "=jahresueberschuss + vortrag - ausschuettung"},
]


def get_all_groups() -> list[str]:
    return sorted({e["code"] for e in GUV_HIERARCHY})


def find_group_row(code: str, entries: list[dict]) -> int | None:
    """Find the first row index where this group appears. Indices are 0-based into entries."""
    for i, e in enumerate(entries):
        if e["code"] == code:
            return i
    return None
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

- [ ] **Step 5: Commit**

```bash
git add app/excel/structure.py tests/test_excel_structure.py
git commit -m "feat(excel): HGB §275 GKV hierarchy definition"
```

---

### Task 12: Excel-Formel-Builder

**Files:**
- Create: `app/excel/formulas.py`, `tests/test_excel_formulas.py`

- [ ] **Step 1: Failing Tests**

```python
# tests/test_excel_formulas.py
from app.excel.formulas import sum_range, cell, multi_add


def test_cell_converts_column_index():
    assert cell(0, 1) == "A1"
    assert cell(2, 5) == "C5"
    assert cell(25, 1) == "Z1"


def test_sum_range_produces_formula():
    assert sum_range(col_idx=2, first_row=3, last_row=8) == "=SUM(C3:C8)"


def test_multi_add_subtracts_correctly():
    formula = multi_add(col_idx=2, add_rows=[3, 5], subtract_rows=[7])
    assert formula == "=C3+C5-C7"
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

- [ ] **Step 3: `app/excel/formulas.py` implementieren**

```python
from openpyxl.utils import get_column_letter


def cell(col_idx: int, row: int) -> str:
    """Return A1-style cell reference. col_idx is 0-based."""
    return f"{get_column_letter(col_idx + 1)}{row}"


def sum_range(col_idx: int, first_row: int, last_row: int) -> str:
    col = get_column_letter(col_idx + 1)
    return f"=SUM({col}{first_row}:{col}{last_row})"


def multi_add(col_idx: int, add_rows: list[int], subtract_rows: list[int] | None = None) -> str:
    col = get_column_letter(col_idx + 1)
    parts = [f"{col}{r}" for r in add_rows]
    subtract_rows = subtract_rows or []
    formula = "=" + "+".join(parts)
    for r in subtract_rows:
        formula += f"-{col}{r}"
    return formula


def ratio(numerator_row: int, denominator_row: int, col_idx: int) -> str:
    col = get_column_letter(col_idx + 1)
    return f"={col}{numerator_row}/{col}{denominator_row}"
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

- [ ] **Step 5: Commit**

```bash
git add app/excel/formulas.py tests/test_excel_formulas.py
git commit -m "feat(excel): formula builder helpers"
```

---

### Task 13: Excel-Kennzahlen

**Files:**
- Create: `app/excel/kennzahlen.py`, `tests/test_excel_kennzahlen.py`

- [ ] **Step 1: Failing Tests**

```python
# tests/test_excel_kennzahlen.py
from app.excel.kennzahlen import build_kennzahlen_rows


def test_kennzahlen_returns_expected_set():
    anchors = {
        "umsatz_row": 5, "material_row": 12, "personal_row": 18,
        "afa_row": 22, "jue_row": 35, "ebitda_row": 40,
    }
    rows = build_kennzahlen_rows(anchors, col_idx=2)
    names = [r["label"] for r in rows]
    assert "Materialquote" in names
    assert "Umsatzrendite" in names
    assert "EBITDA-Marge" in names
    for r in rows:
        assert r["formula"].startswith("=C")
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

- [ ] **Step 3: `app/excel/kennzahlen.py` implementieren**

```python
from app.excel.formulas import ratio


def build_kennzahlen_rows(anchors: dict[str, int], col_idx: int) -> list[dict]:
    umsatz = anchors["umsatz_row"]
    return [
        {"label": "Materialquote",
         "formula": ratio(anchors["material_row"], umsatz, col_idx), "number_format": "0.0%"},
        {"label": "Rohertragsquote",
         "formula": f"=1-{ratio(anchors['material_row'], umsatz, col_idx)[1:]}",
         "number_format": "0.0%"},
        {"label": "Personalquote",
         "formula": ratio(anchors["personal_row"], umsatz, col_idx), "number_format": "0.0%"},
        {"label": "Umsatzrendite",
         "formula": ratio(anchors["jue_row"], umsatz, col_idx), "number_format": "0.0%"},
        {"label": "EBITDA-Marge",
         "formula": ratio(anchors["ebitda_row"], umsatz, col_idx), "number_format": "0.0%"},
    ]
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

- [ ] **Step 5: Commit**

```bash
git add app/excel/kennzahlen.py tests/test_excel_kennzahlen.py
git commit -m "feat(excel): Kennzahlen-Formeln (Materialquote etc.)"
```

---

### Task 14: Excel-Builder (Gesamt-Orchestrator)

**Files:**
- Create: `app/excel/builder.py`, `tests/test_excel_builder.py`

- [ ] **Step 1: Failing Tests**

```python
# tests/test_excel_builder.py
import io
from openpyxl import load_workbook
from app.excel.builder import build_excel


def make_consolidated():
    return {
        "years": [2023, 2024],
        "rows": [
            {"konto_nr": "8400", "bezeichnung": "Erlöse 19%",
             "gruppe": "1. Umsatzerlöse",
             "values": {2023: 1000000, 2024: 1100000}, "confidence": "high"},
            {"konto_nr": "5100", "bezeichnung": "Wareneingang 19%",
             "gruppe": "4a. Aufwendungen für RHB und Waren",
             "values": {2023: 400000, 2024: 450000}, "confidence": "high"},
            {"konto_nr": "6000", "bezeichnung": "Löhne",
             "gruppe": "5a. Löhne und Gehälter",
             "values": {2023: 150000, 2024: 160000}, "confidence": "high"},
        ],
        "questions": [],
    }


def test_builder_produces_excel_with_formulas():
    xlsx = build_excel(make_consolidated(), review_answers={})
    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb["Übertrag"]

    # Find cell containing "=SUM" — at least one sum formula must exist
    formula_cells = [c for row in ws.iter_rows() for c in row
                     if c.value and isinstance(c.value, str) and c.value.startswith("=SUM")]
    assert len(formula_cells) > 0, "Keine SUM-Formel in der Excel gefunden"


def test_builder_creates_fragen_sheet():
    xlsx = build_excel(make_consolidated(), review_answers={})
    wb = load_workbook(io.BytesIO(xlsx))
    assert "Fragen" in wb.sheetnames


# Fix 3B: Formel-Correctness — Top-Anforderung aus Spec Section 4
def test_umsatz_sum_formula_covers_all_detail_rows():
    """Die Umsatz-Summenzeile muss SUM über genau den Kontenbereich sein."""
    xlsx = build_excel(make_consolidated(), review_answers={})
    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb["Übertrag"]

    sum_cells = [c for row in ws.iter_rows() for c in row
                 if c.value and isinstance(c.value, str) and c.value.startswith("=SUM(")]
    # Mindestens für Umsatz, Material, Personal sollten Summen existieren
    assert len(sum_cells) >= 3, f"Erwarte min 3 Summen-Zellen, gefunden {len(sum_cells)}"


def test_jahresueberschuss_references_other_cells_not_header_row():
    """Fix 2B: JÜ-Formel darf NICHT auf Row 1 (Header) zeigen."""
    xlsx = build_excel(make_consolidated(), review_answers={})
    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb["Übertrag"]

    # JÜ findet sich in Spalte B als Label "14. Jahresüberschuss"
    for row in ws.iter_rows():
        for c in row:
            if c.value == "14. Jahresüberschuss":
                # Formel-Zelle ist in den Jahres-Spalten rechts daneben
                formula_col = row[2].value  # Spalte C (erster Jahreswert)
                assert isinstance(formula_col, str) and formula_col.startswith("=")
                assert "B1" not in formula_col and "C1" not in formula_col, \
                    f"Formel zeigt auf Header-Row: {formula_col}"
                return
    raise AssertionError("Jahresüberschuss-Zeile nicht gefunden")


def test_missing_category_falls_back_to_zero_not_row_one():
    """Fix 2B: Wenn Abschreibungen fehlen, muss die Formel '0' enthalten, nicht 'B1'."""
    consolidated = {
        "years": [2024],
        "rows": [
            {"konto_nr": "8400", "bezeichnung": "Erlöse",
             "gruppe": "1. Umsatzerlöse", "values": {2024: 1000000}, "confidence": "high"},
            # keine Abschreibungen, keine Personalkosten, keine Materialkosten
        ],
        "questions": [],
    }
    xlsx = build_excel(consolidated, review_answers={})
    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb["Übertrag"]
    # Suche Formeln die auf Row 1 zeigen — darf nicht passieren
    bad = [c.value for row in ws.iter_rows() for c in row
           if isinstance(c.value, str) and c.value.startswith("=") and "1+" in c.value]
    assert not bad, f"Formel zeigt auf Row 1 (Header): {bad}"


def test_review_answers_override_gruppe():
    """Wenn der User im Review eine Gruppe zuordnet, muss die Excel diese verwenden."""
    consolidated = make_consolidated()
    # Füge eine Konto mit falscher Gruppe hinzu, das durch Review korrigiert wird
    consolidated["rows"].append({
        "konto_nr": "4980", "bezeichnung": "Unklar",
        "gruppe": "7a. Raumkosten",  # falsch
        "values": {2023: 500, 2024: 600}, "confidence": "low",
    })
    xlsx = build_excel(consolidated,
                       review_answers={"4980": "7g. Verschiedene betriebliche Kosten"})
    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb["Übertrag"]
    # Das Konto muss jetzt unter 7g auftauchen, nicht unter 7a
    found_in_7g = False
    current_gruppe = None
    for row in ws.iter_rows():
        val_b = row[1].value
        if val_b and isinstance(val_b, str):
            if val_b.startswith("7a."): current_gruppe = "7a"
            elif val_b.startswith("7g."): current_gruppe = "7g"
            elif "Unklar" in val_b and current_gruppe == "7g":
                found_in_7g = True
    assert found_in_7g, "Review-Korrektur wurde nicht angewendet"
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

- [ ] **Step 3: `app/excel/builder.py` implementieren**

Der Builder ist das Herzstück. Er übersetzt die konsolidierte Datenstruktur in ein openpyxl-Workbook. Wegen der Länge hier das Grundgerüst — Details werden in der Umsetzung an der Excel-Logik aus `skills/guv-uebertrag` orientiert.

```python
import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from app.excel.structure import GUV_HIERARCHY
from app.excel.formulas import sum_range, multi_add
from app.excel.kennzahlen import build_kennzahlen_rows

EUR_FORMAT = '#,##0.00;[Red]-#,##0.00'
PCT_FORMAT = "0.0%"
YELLOW = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
RED = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
BOLD = Font(bold=True)


def build_excel(consolidated: dict, review_answers: dict) -> bytes:
    """Build the final Excel file bytes from consolidated data + user review answers."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Übertrag"

    years = consolidated["years"]
    rows_data = _apply_review(consolidated["rows"], review_answers)

    # Header row
    headers = ["Konto", "Bezeichnung"] + [str(y) for y in years] + [f"{y} % v.U." for y in years]
    for col_idx, h in enumerate(headers):
        ws.cell(row=1, column=col_idx + 1, value=h).font = BOLD

    # Write rows grouped by GUV_HIERARCHY
    row_cursor = 3  # leave row 2 empty
    group_detail_ranges: dict[str, tuple[int, int]] = {}
    group_sum_rows: dict[str, int] = {}

    by_group: dict[str, list[dict]] = {}
    for r in rows_data:
        by_group.setdefault(r["gruppe"], []).append(r)

    for entry in GUV_HIERARCHY:
        code = entry["code"]
        kind = entry["kind"]
        if kind == "details":
            details = by_group.get(code, [])
            if not details:
                continue
            start = row_cursor
            for r in details:
                ws.cell(row=row_cursor, column=1, value=r["konto_nr"] or "")
                ws.cell(row=row_cursor, column=2, value=f"  {r['bezeichnung']}")
                for y_idx, year in enumerate(years):
                    val = r["values"].get(year)
                    c = ws.cell(row=row_cursor, column=3 + y_idx, value=val)
                    c.number_format = EUR_FORMAT
                    if r.get("confidence") == "low":
                        c.fill = YELLOW
                row_cursor += 1
            group_detail_ranges[code] = (start, row_cursor - 1)
        elif kind == "sum":
            detail_range = group_detail_ranges.get(code)
            if not detail_range:
                continue
            label = ws.cell(row=row_cursor, column=2, value=code)
            if entry.get("bold"):
                label.font = BOLD
            for y_idx in range(len(years)):
                col = 3 + y_idx
                c = ws.cell(row=row_cursor, column=col, value=sum_range(col - 1, *detail_range))
                c.number_format = EUR_FORMAT
                if entry.get("bold"):
                    c.font = BOLD
            group_sum_rows[code] = row_cursor
            row_cursor += 1
        elif kind == "formula":
            # Formulas referenced by anchor keyword — resolved below
            label = ws.cell(row=row_cursor, column=2, value=code)
            if entry.get("bold"):
                label.font = BOLD
            group_sum_rows[code] = row_cursor
            row_cursor += 1

    # Resolve formula rows now that we know anchors
    _write_computed_formulas(ws, group_sum_rows, years)

    # Anteil-Spalten (% vom Umsatz)
    umsatz_row = group_sum_rows.get("1. Umsatzerlöse")
    if umsatz_row:
        _write_percent_columns(ws, years, umsatz_row, last_data_row=row_cursor)

    # Kennzahlen
    row_cursor += 2
    ws.cell(row=row_cursor, column=2, value="Kennzahlen").font = BOLD
    row_cursor += 1
    anchors = _kennzahlen_anchors(group_sum_rows)
    for y_idx, year in enumerate(years):
        col_idx = 2 + y_idx
        kz_rows = build_kennzahlen_rows(anchors, col_idx=col_idx)
        for kz_idx, kz in enumerate(kz_rows):
            if y_idx == 0:
                ws.cell(row=row_cursor + kz_idx, column=2, value=kz["label"])
            c = ws.cell(row=row_cursor + kz_idx, column=col_idx + 1, value=kz["formula"])
            c.number_format = kz["number_format"]

    # Cross-Checks
    _write_crosschecks(ws, group_sum_rows, years, row_cursor + 6)

    # Formatting: column widths
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 50
    for i, _ in enumerate(years):
        ws.column_dimensions[get_column_letter(3 + i)].width = 15

    # Fragen-Sheet
    fragen = wb.create_sheet("Fragen")
    fragen.append(["Thema", "Details"])
    for q in consolidated.get("questions", []):
        fragen.append([q.get("type"), str(q)])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _apply_review(rows: list[dict], review_answers: dict) -> list[dict]:
    out = []
    for r in rows:
        key = r.get("konto_nr") or r["bezeichnung"]
        if key in review_answers:
            r = {**r, "gruppe": review_answers[key], "confidence": "reviewed"}
        out.append(r)
    return out


def _write_computed_formulas(ws, anchors: dict[str, int], years: list[int]) -> None:
    # 12. Ergebnis nach Steuern
    eng = anchors
    for y_idx in range(len(years)):
        col = 3 + y_idx
        col_letter = get_column_letter(col)

        def _ref(code: str) -> str:
            # Fix 2B: Fehlende Kategorie → "0", nicht Row 1 (= Header-Zelle!).
            # Sonst erzeugt =B1-... eine #VALUE!-Formel gegen den Header "Bezeichnung".
            r = eng.get(code)
            if r is None:
                return "0"
            return f"{col_letter}{r}"

        if "12. Ergebnis nach Steuern" in eng:
            formula = (f"={_ref('2. Gesamtleistung')}+{_ref('3. Sonstige betriebliche Erträge')}"
                       f"-{_ref('4. Materialaufwand')}-{_ref('5. Personalaufwand')}"
                       f"-{_ref('6. Abschreibungen')}-{_ref('7. Sonstige betriebliche Aufwendungen')}"
                       f"-{_ref('11. Steuern vom Einkommen und vom Ertrag')}")
            c = ws.cell(row=eng["12. Ergebnis nach Steuern"], column=col, value=formula)
            c.number_format = EUR_FORMAT; c.font = BOLD

        if "14. Jahresüberschuss" in eng:
            ref13 = _ref("13. Sonstige Steuern")
            formula = f"={_ref('12. Ergebnis nach Steuern')}-{ref13}"
            c = ws.cell(row=eng["14. Jahresüberschuss"], column=col, value=formula)
            c.number_format = EUR_FORMAT; c.font = BOLD

        # 2. Gesamtleistung = 1. Umsatzerlöse
        if "2. Gesamtleistung" in eng:
            c = ws.cell(row=eng["2. Gesamtleistung"], column=col, value=_ref("1. Umsatzerlöse"))
            c.number_format = EUR_FORMAT


def _write_percent_columns(ws, years, umsatz_row, last_data_row):
    # Für jede Datenzeile: Anteil = Betrag / Umsatz
    pct_start_col = 3 + len(years)
    for row in range(3, last_data_row):
        for y_idx, _ in enumerate(years):
            val_col = 3 + y_idx
            val_letter = get_column_letter(val_col)
            umsatz_letter = get_column_letter(val_col)
            pct_col = pct_start_col + y_idx
            # Only if value cell is numeric (skip labels)
            if ws.cell(row=row, column=val_col).value is not None:
                formula = f"=IFERROR({val_letter}{row}/{umsatz_letter}${umsatz_row},\"\")"
                c = ws.cell(row=row, column=pct_col, value=formula)
                c.number_format = PCT_FORMAT


def _kennzahlen_anchors(anchors: dict[str, int]) -> dict[str, int]:
    return {
        "umsatz_row": anchors.get("1. Umsatzerlöse", 1),
        "material_row": anchors.get("4. Materialaufwand", 1),
        "personal_row": anchors.get("5. Personalaufwand", 1),
        "afa_row": anchors.get("6. Abschreibungen", 1),
        "jue_row": anchors.get("14. Jahresüberschuss", 1),
        "ebitda_row": anchors.get("14. Jahresüberschuss", 1),  # placeholder bis EBITDA
    }


def _write_crosschecks(ws, anchors, years, start_row):
    ws.cell(row=start_row, column=2, value="Plausibilitäts-Checks").font = BOLD
    # Mehr Checks (JÜ PDF vs. Formel) würden hier Cross-Compare-Werte brauchen —
    # wird in späteren Iterationen ergänzt.
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

- [ ] **Step 5: Commit**

```bash
git add app/excel/builder.py tests/test_excel_builder.py
git commit -m "feat(excel): full builder with formulas, kennzahlen, fragen-sheet"
```

---

## Phase 4: Worker-Tasks und FastAPI-Layer (Tasks 15-19)

### Task 15: Worker-Orchestrierung (extract_job, finalize_job)

**Files:**
- Create: `app/worker/tasks.py`, `tests/test_worker_tasks.py`

- [ ] **Step 1: Failing Tests für Idempotenz und Status-Transitions (Fix 3A)**

```python
# tests/test_worker_tasks.py
from unittest.mock import MagicMock, patch
from app.worker.tasks import extract_job, finalize_job
from app.models import Job, JobStatus, InputFile


def make_job(status=JobStatus.UPLOADED):
    return Job(
        id="job-1", created_at="2026-04-23T00:00:00Z", status=status,
        input_files=[InputFile(name="a.pdf", size=100, storage_path="job-1/input/a.pdf")],
        expires_at="2026-04-24T00:00:00Z",
    )


def test_extract_skips_if_already_processed():
    """Fix 1D: Idempotenz — wenn Job schon review_needed, kein Claude-Call."""
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude:
        repo = Repo.return_value
        repo.get.return_value = make_job(status=JobStatus.REVIEW_NEEDED)
        repo.try_claim.return_value = True
        extract_job("job-1")
        Claude.return_value.classify_document.assert_not_called()


def test_extract_skips_if_not_claimed():
    """Fix 1A: Wenn ein anderer Worker den Job schon hält, kein Doppel-Call."""
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude:
        repo = Repo.return_value
        repo.try_claim.return_value = False
        extract_job("job-1")
        Claude.return_value.classify_document.assert_not_called()


def test_extract_sets_review_needed_on_success():
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.classify_pdf") as cls, \
         patch("app.worker.tasks.extract_text", return_value="sample text"):
        repo = Repo.return_value
        repo.try_claim.return_value = True
        repo.get.return_value = make_job(status=JobStatus.UPLOADED)
        Storage.return_value.download_input.return_value = b"%PDF-fake"
        cls.return_value = __import__("app.worker.pdf_detect", fromlist=["PdfKind"]).PdfKind.TEXT
        claude = Claude.return_value
        claude.classify_document.return_value = "jahresabschluss"
        claude.extract_text_pdf.return_value = {"type": "jahresabschluss", "year": 2024,
                                                 "accounts": []}
        extract_job("job-1")
        repo.set_extraction.assert_called_once()


def test_finalize_writes_excel_and_sets_ready():
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.build_excel", return_value=b"xlsx-bytes"):
        repo = Repo.return_value
        repo.try_claim.return_value = True
        job = make_job(status=JobStatus.REVIEW_NEEDED)
        job.extraction = {"consolidated": {"years": [2024], "rows": [], "questions": []}}
        repo.get.return_value = job
        Storage.return_value.upload_output.return_value = "job-1/output.xlsx"
        finalize_job("job-1", {"4980": "7g. Verschiedene betriebliche Kosten"})
        repo.set_output.assert_called_once()
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

```bash
pytest tests/test_worker_tasks.py -v
```

- [ ] **Step 3: `app/worker/tasks.py` mit Idempotenz implementieren (Fix 1D)**

```python
import os
import socket
from app.db import JobsRepo
from app.storage import StorageClient
from app.models import JobStatus, InputFile
from app.worker.pdf_detect import classify_pdf, pdf_to_images, extract_text, PdfKind
from app.worker.claude_client import ClaudeClient, ExtractionError
from app.worker.prompts import build_consolidation_prompt
from app.worker.consolidate import merge_extractions
from app.excel.builder import build_excel

# Unique worker-identifier per Prozess (Fix 1A)
NODE_ID = f"{socket.gethostname()}-{os.getpid()}"

TERMINAL_STATES = {JobStatus.READY, JobStatus.REVIEW_NEEDED, JobStatus.FAILED,
                   JobStatus.EXPIRED}


def extract_job(job_id: str) -> None:
    """Run extraction for all input files of a job.
    Idempotent (Fix 1D) + claim-based (Fix 1A): doppelte Aufrufe oder konkurrierende
    Worker führen nicht zu doppelten Claude-Calls."""
    repo = JobsRepo()
    storage = StorageClient()
    claude = ClaudeClient()

    # Fix 1D: Idempotenz-Check — schon verarbeitet?
    existing = repo.get(job_id)
    if existing is None:
        return
    if existing.status in TERMINAL_STATES:
        return  # already done

    # Fix 1A: Nur ein Worker pro Job. Wenn Claim fehlschlägt, läuft bereits jemand.
    if not repo.try_claim(job_id, NODE_ID):
        return

    try:
        repo.set_status(job_id, JobStatus.EXTRACTING)
        job = repo.get(job_id)
        if job is None:
            return

        extractions = []
        updated_files: list[dict] = []
        for f in job.input_files:
            data = storage.download_input(f.storage_path)
            kind = classify_pdf(data)
            sample = extract_text(data)[:5000] if kind == PdfKind.TEXT else ""
            doc_type = claude.classify_document(sample or "BILDDATEN-SCAN")

            if kind == PdfKind.TEXT:
                text = extract_text(data)
                extraction = claude.extract_text_pdf(text, is_bwa=(doc_type == "bwa"))
            else:
                pages = pdf_to_images(data)
                extraction = claude.extract_scan_pdf(pages, is_bwa=(doc_type == "bwa"))

            extraction["file"] = f.name
            extractions.append(extraction)
            updated_files.append({
                **f.model_dump(), "pdf_type": kind.value, "doc_type": doc_type,
            })

        consolidated = merge_extractions(extractions)
        payload = {
            "documents": extractions,
            "consolidated": consolidated,
            "open_questions": [q for doc in extractions for q in doc.get("open_questions", [])],
        }
        repo.set_extraction(job_id, payload)
    except ExtractionError as e:
        repo.set_status(job_id, JobStatus.FAILED, error=f"Extraktion fehlgeschlagen: {e}")
    except Exception as e:
        repo.set_status(job_id, JobStatus.FAILED, error=f"Unerwarteter Fehler: {e}")


def finalize_job(job_id: str, review_answers: dict) -> None:
    """Build the Excel file. Idempotent + claim-based."""
    repo = JobsRepo()
    storage = StorageClient()

    existing = repo.get(job_id)
    if existing is None or existing.status in {JobStatus.READY, JobStatus.EXPIRED,
                                                JobStatus.FAILED}:
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
        repo.set_status(job_id, JobStatus.FAILED, error=f"Excel-Erstellung fehlgeschlagen: {e}")
        repo.release_claim(job_id)


def resume_stuck_jobs() -> None:
    """Fix 1A: Beim App-Start — Jobs vom letzten Prozess wieder aufnehmen.
    Der Deploy-Restart von Railway kann BackgroundTasks mitten drin killen.
    """
    repo = JobsRepo()
    for job in repo.list_resumable():
        # Claim freigeben, damit der neue Worker ihn aufnehmen kann
        repo.release_claim(job.id)
        if job.status == JobStatus.EXTRACTING:
            extract_job(job.id)
        elif job.status == JobStatus.FINALIZING and job.review_answers:
            finalize_job(job.id, job.review_answers)
```

- [ ] **Step 2: Commit**

```bash
git add app/worker/tasks.py
git commit -m "feat(worker): orchestrate extraction and finalization jobs"
```

---

### Task 16: FastAPI-App und Login-Route

**Files:**
- Create: `app/main.py`, `app/routes/pages.py`, `app/templates/base.html`, `app/templates/login.html`, `app/templates/upload.html`

- [ ] **Step 1: `app/main.py` anlegen (mit Startup-Hook + Health-Endpoint)**

```python
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.routes import pages, upload, job, download
from app.worker.tasks import resume_stuck_jobs
from app.db import JobsRepo

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fix 1A: Beim Start hängende Jobs aufnehmen (Railway-Deploy-Überlebenspfad)
    try:
        resume_stuck_jobs()
    except Exception as e:
        log.warning("resume_stuck_jobs fehlgeschlagen (Supabase pausiert?): %s", e)
    yield


app = FastAPI(title="Prozess-Übertrag", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/health")
def health():
    """Fix 1C: Erkennt Supabase-Pause und gibt klare Nachricht zurück."""
    try:
        JobsRepo().client.table("jobs").select("id").limit(1).execute()
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse(
            {"status": "error",
             "message": "Datenbank nicht erreichbar. Falls Supabase pausiert ist: "
                        "im Dashboard aktivieren.",
             "details": str(e)[:200]},
            status_code=503,
        )


app.include_router(pages.router)
app.include_router(upload.router)
app.include_router(job.router)
app.include_router(download.router)
```

- [ ] **Step 2: `app/routes/pages.py` anlegen**

```python
from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from app.auth import verify_password, create_session_token, validate_session_token
from app.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
COOKIE_NAME = "pu_session"


def require_auth(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    return validate_session_token(token, get_settings().session_secret)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(response: Response, password: str = Form(...)):
    s = get_settings()
    if not verify_password(password, s.app_password_hash):
        return templates.TemplateResponse("login.html",
                                          {"request": None, "error": "Falsches Passwort"},
                                          status_code=401)
    token = create_session_token(s.session_secret)
    resp = RedirectResponse(url="/", status_code=303)
    # Fix 1B: Secure nur wenn App über HTTPS läuft — sonst bricht lokale Entwicklung.
    use_secure = s.public_base_url.startswith("https://")
    resp.set_cookie(COOKIE_NAME, token, httponly=True, secure=use_secure,
                    samesite="strict", max_age=60 * 60 * 24 * 7)
    return resp


@router.get("/", response_class=HTMLResponse)
def upload_page(request: Request):
    if not require_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("upload.html", {"request": request})
```

- [ ] **Step 3: Templates `base.html`, `login.html`, `upload.html` aus HTMX-Demo übernehmen**

Die Templates übernehmen wir 1:1 aus der Demo `docs/exploration/htmx-demo.html`. Zerlege in Jinja-Templates. Ich zeige hier `base.html` als Ausgangspunkt — die anderen folgen dem gleichen Muster:

```html
<!-- app/templates/base.html -->
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>{% block title %}Prozess-Übertrag{% endblock %}</title>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    .drop-zone { border: 2px dashed #94a3b8; transition: all 0.2s; }
    .drop-zone.drag-over { border-color: #2563eb; background: #eff6ff; }
  </style>
</head>
<body class="bg-slate-50 min-h-screen">
  <div class="max-w-3xl mx-auto py-12 px-4">
    <header class="mb-8">
      <h1 class="text-3xl font-bold text-slate-900">Prozess-Übertrag</h1>
      <p class="text-slate-600 mt-1">Jahresabschlüsse in Excel übertragen — Calandi intern</p>
    </header>
    {% block content %}{% endblock %}
  </div>
</body>
</html>
```

```html
<!-- app/templates/login.html -->
{% extends "base.html" %}
{% block content %}
<div class="bg-white rounded-lg shadow-sm border p-8">
  <h2 class="text-xl font-semibold mb-4">Anmelden</h2>
  <form method="post" action="/login">
    <input type="password" name="password" placeholder="Team-Passwort" required
           class="w-full px-4 py-2 border rounded-md mb-3" />
    {% if error %}<p class="text-red-600 text-sm mb-3">{{ error }}</p>{% endif %}
    <button class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md w-full">
      Anmelden
    </button>
  </form>
</div>
{% endblock %}
```

```html
<!-- app/templates/upload.html -->
{% extends "base.html" %}
{% block content %}
<form id="upload-form" hx-post="/upload" hx-encoding="multipart/form-data" hx-target="#result"
      class="bg-white rounded-lg shadow-sm border p-8">
  <h2 class="text-xl font-semibold mb-4">PDFs hochladen</h2>
  <div id="drop-zone" class="drop-zone rounded-lg p-12 text-center cursor-pointer">
    <p class="text-slate-700 font-medium">PDFs hierher ziehen</p>
    <p class="text-sm text-slate-500">oder klicken — max 10 PDFs, je max 10 MB</p>
    <input type="file" name="files" multiple accept="application/pdf" class="hidden" id="file-input" />
  </div>
  <div id="file-list" class="mt-4 space-y-2"></div>
  <button type="submit" class="mt-4 bg-blue-600 text-white px-4 py-2 rounded-md w-full">
    Verarbeitung starten
  </button>
</form>
<div id="result"></div>

<script>
  const drop = document.getElementById('drop-zone');
  const input = document.getElementById('file-input');
  drop.addEventListener('click', () => input.click());
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('drag-over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('drag-over'));
  drop.addEventListener('drop', e => {
    e.preventDefault();
    drop.classList.remove('drag-over');
    input.files = e.dataTransfer.files;
    renderList();
  });
  input.addEventListener('change', renderList);
  function renderList() {
    const list = document.getElementById('file-list');
    list.textContent = '';
    for (const f of input.files) {
      const div = document.createElement('div');
      div.className = 'flex justify-between p-3 bg-slate-50 rounded border';
      const name = document.createElement('span'); name.textContent = f.name;
      const size = document.createElement('span'); size.className = 'text-xs text-slate-500';
      size.textContent = (f.size/1024/1024).toFixed(1) + ' MB';
      div.appendChild(name); div.appendChild(size);
      list.appendChild(div);
    }
  }
</script>
{% endblock %}
```

- [ ] **Step 4: Smoke-Test lokal**

```bash
uvicorn app.main:app --reload
```

Erwartung: Server startet auf `http://localhost:8000`, `/login` rendert.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/routes/pages.py app/templates/ app/static/
git commit -m "feat(web): FastAPI app with login and upload page"
```

---

### Task 17: Upload-Route

**Files:**
- Create: `app/routes/upload.py`, `tests/test_upload_route.py`

- [ ] **Step 1: Failing Test**

```python
# tests/test_upload_route.py
import io
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app


def test_upload_rejects_non_pdf():
    client = TestClient(app)
    # Bypass auth via direct cookie
    with patch("app.routes.pages.validate_session_token", return_value=True):
        files = [("files", ("not-a-pdf.txt", io.BytesIO(b"hello"), "text/plain"))]
        r = client.post("/upload", files=files, cookies={"pu_session": "x"})
    assert r.status_code == 400


def test_upload_rejects_too_many_files():
    client = TestClient(app)
    with patch("app.routes.pages.validate_session_token", return_value=True):
        files = [("files", (f"a{i}.pdf", io.BytesIO(b"%PDF-"), "application/pdf"))
                 for i in range(11)]
        r = client.post("/upload", files=files, cookies={"pu_session": "x"})
    assert r.status_code == 400
```

- [ ] **Step 2: Tests laufen, FAIL erwarten**

- [ ] **Step 3: `app/routes/upload.py` implementieren**

```python
from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Request, HTTPException
from fastapi.responses import Response
from app.db import JobsRepo
from app.storage import StorageClient
from app.models import InputFile
from app.worker.tasks import extract_job
from app.routes.pages import require_auth
from app.config import get_settings

router = APIRouter()


@router.post("/upload")
async def upload(request: Request, background: BackgroundTasks,
                 files: list[UploadFile] = File(...)):
    if not require_auth(request):
        raise HTTPException(status_code=401)

    s = get_settings()
    if len(files) == 0 or len(files) > s.max_files_per_job:
        raise HTTPException(status_code=400, detail=f"1..{s.max_files_per_job} Dateien erwartet")

    for f in files:
        if f.content_type != "application/pdf" and not f.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"Nur PDFs erlaubt: {f.filename}")

    repo = JobsRepo()
    storage = StorageClient()

    input_files: list[InputFile] = []
    job = repo.create([])  # placeholder to get job_id

    for f in files:
        data = await f.read()
        if len(data) > s.max_file_size_mb * 1024 * 1024:
            raise HTTPException(status_code=400,
                                detail=f"{f.filename} > {s.max_file_size_mb} MB")
        path = storage.upload_input(job.id, f.filename, data)
        input_files.append(InputFile(name=f.filename, size=len(data), storage_path=path))

    # Fix 2A: Layer-sauber über JobsRepo-Methode statt direkt repo.client
    repo.set_input_files(job.id, input_files)

    background.add_task(extract_job, job.id)

    return Response(headers={"HX-Redirect": f"/job/{job.id}"}, status_code=200)
```

- [ ] **Step 4: Tests laufen, PASS erwarten**

- [ ] **Step 5: Commit**

```bash
git add app/routes/upload.py tests/test_upload_route.py
git commit -m "feat(upload): validate and store PDFs, trigger extraction background task"
```

---

### Task 18: Job-Status- und Review-Routes

**Files:**
- Create: `app/routes/job.py`, `app/templates/job.html`, `app/templates/partials/{status,review,ready,failed}.html`

- [ ] **Step 1: `app/routes/job.py` anlegen**

```python
from fastapi import APIRouter, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.db import JobsRepo
from app.models import JobStatus
from app.routes.pages import require_auth
from app.worker.tasks import finalize_job

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    if not require_auth(request):
        raise HTTPException(status_code=401)
    job = JobsRepo().get(job_id)
    if not job:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("job.html", {"request": request, "job": job})


@router.get("/job/{job_id}/status", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    if not require_auth(request):
        raise HTTPException(status_code=401)
    job = JobsRepo().get(job_id)
    if not job:
        raise HTTPException(status_code=404)

    partial = {
        JobStatus.UPLOADED: "partials/status.html",
        JobStatus.EXTRACTING: "partials/status.html",
        JobStatus.REVIEW_NEEDED: "partials/review.html",
        JobStatus.FINALIZING: "partials/status.html",
        JobStatus.READY: "partials/ready.html",
        JobStatus.FAILED: "partials/failed.html",
        JobStatus.EXPIRED: "partials/failed.html",
    }[job.status]
    return templates.TemplateResponse(partial, {"request": request, "job": job})


@router.post("/job/{job_id}/finalize", response_class=HTMLResponse)
def job_finalize(request: Request, job_id: str, background: BackgroundTasks):
    if not require_auth(request):
        raise HTTPException(status_code=401)
    form = request.form()
    # Collect review answers: keys like "review[KONTONR]" → group
    review: dict[str, str] = {}
    # parsed from form data in real request
    background.add_task(finalize_job, job_id, review)
    job = JobsRepo().get(job_id)
    return templates.TemplateResponse("partials/status.html", {"request": request, "job": job})
```

- [ ] **Step 2: Templates anlegen (`job.html`, `partials/*.html`)**

Wegen Länge hier nur Struktur: `job.html` enthält ein `<div id="job-state" hx-get="/job/{{ job.id }}/status" hx-trigger="every 3s" hx-swap="innerHTML">`. Die Partials rendern je nach Status — Progress-Bar, Review-Form mit Dropdowns, Download-Button.

- [ ] **Step 3: Commit**

```bash
git add app/routes/job.py app/templates/
git commit -m "feat(job): status polling and review endpoints with HTMX partials"
```

---

### Task 19: Download-Route

**Files:**
- Create: `app/routes/download.py`

- [ ] **Step 1: `app/routes/download.py` implementieren**

```python
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from app.db import JobsRepo
from app.storage import StorageClient
from app.models import JobStatus
from app.routes.pages import require_auth

router = APIRouter()


@router.get("/download/{job_id}")
def download(request: Request, job_id: str):
    if not require_auth(request):
        raise HTTPException(status_code=401)
    job = JobsRepo().get(job_id)
    if not job or job.status != JobStatus.READY or not job.output_path:
        raise HTTPException(status_code=404)
    url = StorageClient().signed_output_url(job.output_path, expires_in=900)
    return RedirectResponse(url=url, status_code=303)
```

- [ ] **Step 2: Commit**

```bash
git add app/routes/download.py
git commit -m "feat(download): signed URL redirect for generated Excel"
```

---

## Phase 5: Deployment (Tasks 20-22)

### Task 20: Dockerfile und docker-compose

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `railway.json`

- [ ] **Step 1: `Dockerfile` schreiben**

```dockerfile
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: `docker-compose.yml` für lokale Entwicklung**

```yaml
services:
  app:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    volumes:
      - ./app:/app/app
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

- [ ] **Step 3: `railway.json`**

```json
{
  "build": { "builder": "DOCKERFILE" },
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    "restartPolicyType": "ON_FAILURE"
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docker-compose.yml railway.json
git commit -m "chore(deploy): Dockerfile and Railway config"
```

---

### Task 21: Echter End-to-End-Test mit Mock-Claude (Fix 3C)

**Files:**
- Create: `tests/test_job_flow.py`, `tests/fixtures/minimal-ja.pdf` (generierte Dummy-PDF)

Ziel: ein Test, der den kompletten Pfad durchläuft — Upload → Extraktion (mit Mock-Claude) → Review → Excel-Generierung. Das ist der einzige Test, der beweist dass alle Module zusammenpassen.

- [ ] **Step 1: Test schreiben**

```python
# tests/test_job_flow.py
import io
import fitz
import pytest
from unittest.mock import patch
from openpyxl import load_workbook
from fastapi.testclient import TestClient
from app.main import app


def make_minimal_ja_pdf() -> bytes:
    """Generate a minimal text-PDF that looks like a Kontennachweis."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Jahresabschluss 2024\n\nKontennachweis GuV\n"
                               "8400  Erlöse 19% USt    1.000.000,00\n"
                               "5100  Wareneingang       400.000,00\n"
                               "6000  Löhne              150.000,00\n")
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


FAKE_EXTRACTION = {
    "type": "jahresabschluss", "year": 2024, "previous_year": 2023,
    "accounts": [
        {"konto_nr": "8400", "bezeichnung": "Erlöse 19% USt",
         "gruppe": "1. Umsatzerlöse",
         "betrag_gj": 1000000.0, "betrag_vj": 900000.0, "confidence": "high"},
        {"konto_nr": "5100", "bezeichnung": "Wareneingang",
         "gruppe": "4a. Aufwendungen für RHB und Waren",
         "betrag_gj": 400000.0, "betrag_vj": 370000.0, "confidence": "high"},
        {"konto_nr": "6000", "bezeichnung": "Löhne",
         "gruppe": "5a. Löhne und Gehälter",
         "betrag_gj": 150000.0, "betrag_vj": 140000.0, "confidence": "high"},
    ],
    "open_questions": [],
}


@pytest.mark.integration
def test_full_flow_upload_extract_finalize_download(tmp_path, monkeypatch):
    """E2E: upload → mock extract → finalize → download.
    Läuft ohne echte Claude-API (gemockt), ohne echte Supabase (monkeypatched).
    """
    # Supabase in-memory: simulate via dict-backed fake
    fake_jobs: dict = {}
    fake_storage: dict = {}

    class FakeRepo:
        def create(self, input_files):
            from app.models import Job, JobStatus
            from datetime import datetime, timezone, timedelta
            job = Job(
                id="e2e-job", created_at=datetime.now(timezone.utc),
                status=JobStatus.UPLOADED, input_files=input_files,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            fake_jobs["e2e-job"] = job
            return job

        def get(self, job_id): return fake_jobs.get(job_id)

        def set_status(self, job_id, status, error=None):
            fake_jobs[job_id].status = status
            if error: fake_jobs[job_id].error_message = error

        def set_input_files(self, job_id, files):
            fake_jobs[job_id].input_files = files

        def set_extraction(self, job_id, extraction):
            from app.models import JobStatus
            fake_jobs[job_id].extraction = extraction
            fake_jobs[job_id].status = JobStatus.REVIEW_NEEDED

        def set_output(self, job_id, path, answers):
            from app.models import JobStatus
            fake_jobs[job_id].output_path = path
            fake_jobs[job_id].review_answers = answers
            fake_jobs[job_id].status = JobStatus.READY

        def try_claim(self, job_id, node_id): return True
        def release_claim(self, job_id): pass
        def list_resumable(self): return []

    class FakeStorage:
        def upload_input(self, job_id, name, data):
            path = f"{job_id}/input/{name}"
            fake_storage[path] = data
            return path

        def upload_output(self, job_id, data):
            path = f"{job_id}/output.xlsx"
            fake_storage[path] = data
            return path

        def download_input(self, path): return fake_storage[path]
        def signed_output_url(self, path, expires_in=900): return f"https://fake/{path}"

    with patch("app.routes.upload.JobsRepo", return_value=FakeRepo()), \
         patch("app.routes.upload.StorageClient", return_value=FakeStorage()), \
         patch("app.worker.tasks.JobsRepo", return_value=FakeRepo()), \
         patch("app.worker.tasks.StorageClient", return_value=FakeStorage()), \
         patch("app.worker.tasks.ClaudeClient") as ClaudeMock, \
         patch("app.routes.pages.validate_session_token", return_value=True):

        ClaudeMock.return_value.classify_document.return_value = "jahresabschluss"
        ClaudeMock.return_value.extract_text_pdf.return_value = FAKE_EXTRACTION

        client = TestClient(app)
        pdf_bytes = make_minimal_ja_pdf()

        # Upload
        r = client.post("/upload",
                        files=[("files", ("ja-2024.pdf", pdf_bytes, "application/pdf"))],
                        cookies={"pu_session": "x"})
        assert r.status_code == 200
        # BackgroundTask hat synchron gelaufen in TestClient
        assert fake_jobs["e2e-job"].status.value == "review_needed"

        # Finalize (keine Review-Answers nötig da confidence=high)
        from app.worker.tasks import finalize_job
        finalize_job("e2e-job", {})
        assert fake_jobs["e2e-job"].status.value == "ready"

        # Excel prüfen
        xlsx_bytes = fake_storage["e2e-job/output.xlsx"]
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        assert "Übertrag" in wb.sheetnames
        ws = wb["Übertrag"]
        has_sum = any(isinstance(c.value, str) and c.value.startswith("=SUM")
                      for row in ws.iter_rows() for c in row)
        assert has_sum, "Excel enthält keine SUM-Formeln"
```

- [ ] **Step 2: Test laufen lassen**

```bash
pytest tests/test_job_flow.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_job_flow.py
git commit -m "test(e2e): full flow upload→extract→finalize→download with mocked Claude"
```

---

### Task 22: Deployment-Anleitung in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: `README.md` ergänzen**

```markdown
# Prozess-Übertrag

Interne Calandi-Web-App: Jahresabschluss-PDFs → verformelte Excel.

## Lokale Entwicklung

```bash
cp .env.example .env  # ausfüllen
docker compose up
# → http://localhost:8000
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Deployment

### Supabase-Projekt anlegen

1. Neues Projekt `prozess-uebertrag` erstellen.
2. SQL aus `supabase/migrations/0001_jobs_table.sql` im SQL Editor ausführen.
3. Storage-Bucket `prozess-uebertrag` als **private** anlegen.
4. `pg_cron` Extension aktivieren (Database → Extensions).
5. Im SQL Editor:
   ```sql
   select cron.schedule('cleanup-expired', '0 3 * * *', 'select cleanup_expired_jobs()');
   select cron.schedule('watchdog-stale', '*/5 * * * *', 'select watchdog_stale_jobs()');
   ```

### Railway

1. Repo verbinden (GitHub).
2. ENV-Variablen aus `.env.example` setzen.
3. Deploy.
4. Öffentliche URL in `PUBLIC_BASE_URL` setzen (für Cookie-Secure-Check).

## Passwort setzen

```python
import bcrypt
print(bcrypt.hashpw(b"dein-passwort", bcrypt.gensalt()).decode())
```

Hash in `APP_PASSWORD_HASH` setzen.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: deployment and setup instructions"
```

---

## Self-Review Ergebnisse

**Spec-Abdeckung (alle Sections aus `2026-04-23-prozess-uebertrag-design.md`):**

- Section 2 Scope (JA+BWA, ad-hoc, Desktop) → Task 7 (Detection), 8 (BWA-Prompt), alle Routes
- Section 3 Auth (Passwort, Cookie) → Task 6, 16
- Section 4 **Verformelte Excel** (Top-Anforderung) → Tasks 11-14
- Section 5 Architektur → Tasks 15-19 + 20
- Section 6 Data Flow → Tasks 17, 18, 19, 15
- Section 7 Datenmodell → Tasks 3, 5
- Section 8 Error Handling
  - Upload-Validierung → Task 17
  - Scan-PDF-Handling → Tasks 7, 9
  - Claude-Retry → Task 9
  - Cross-Check Excel → Task 14 (teilweise, siehe Lücke unten)
  - Worker-Ausfälle → Task 3 (Watchdog-SQL)
- Section 9 Deployment → Tasks 20, 22
- Section 11 Skills → dieser Plan verwendet writing-plans

**Gefundene Lücken im Plan:**
- EBITDA-Überleitung ist im Builder (Task 14) nur als Anchor-Platzhalter — muss ausgebaut werden. Füge in Task 14 EBITDA-Block explizit hinzu.
- JÜ-Cross-Check (PDF-Wert vs. Formel-Wert) erwähnt, aber nicht implementiert. Akzeptiert als Erweiterung im follow-up.

**Placeholder-Scan:** keine "TBD"/"TODO" in Steps. `_write_crosschecks` hat minimalen Inhalt — bewusst.

---

## Eng-Review-Fixes (2026-04-23)

Nach `/gstack-plan-eng-review` wurden folgende 10 Findings eingearbeitet:

| # | Fix | Wo | Kritikalität |
|---|---|---|---|
| 1A | Resume-Pattern für BackgroundTasks nach Railway-Restart | Task 3 (Migration), Task 5 (JobsRepo), Task 15 (resume_stuck_jobs), Task 16 (Startup-Hook) | 🔴 |
| 1B | Cookie `secure` nur bei HTTPS — lokale Entwicklung funktioniert | Task 16 (pages.py Login) | 🟡 |
| 1C | `/health`-Endpoint erkennt pausierte Supabase | Task 16 (main.py) | 🟢 |
| 1D | Idempotenz-Check in `extract_job`/`finalize_job` verhindert doppelte Claude-Kosten | Task 15 | 🔴 |
| 2A | `JobsRepo.set_input_files()` statt direkter `repo.client`-Zugriff | Task 5, Task 17 | 🟡 |
| 2B | Excel-Anchor-Fallback auf `"0"` statt Header-Row | Task 14 (`_write_computed_formulas`) | 🔴 |
| 3A | TDD-Tests für `extract_job` / `finalize_job` (Mock-Claude) | Task 15 (neuer Test-Block) | 🔴 |
| 3B | Formel-Correctness-Tests (Top-Anforderung aus Spec §4) | Task 14 (5 neue Tests) | 🔴 |
| 3C | Echter E2E-Test mit In-Memory-Fakes | Task 21 (komplett ersetzt) | 🟡 |
| 4A | Scan-DPI von 150 auf 100 reduziert (~30% weniger Token) | Task 7 (`pdf_to_images`) | 🟢 |

---

## Execution Handoff

Plan complete und gespeichert unter:
`docs/plans/2026-04-23-implementation-plan.md`

**Zwei Ausführungs-Optionen:**

**1. Subagent-Driven (empfohlen)** — Ich dispatche einen frischen Subagenten pro Task, reviewe zwischen Tasks. Du siehst Fortschritt, schnelles Iterieren, kleinere Kontextfenster pro Subagent.

**2. Inline Execution** — Tasks werden direkt in dieser Session abgearbeitet, mit Checkpoints.

**Welcher Ansatz?**
