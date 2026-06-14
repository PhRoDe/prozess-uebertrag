from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel


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
    processing_node: str | None = None
    processing_started_at: datetime | None = None
    created_by: str | None = None  # X-Authentik-Username (Phase 4 Owner-Scoping)
    # Phase A (Benchmarking): Firma + Perioden-/Quellen-Metadaten
    company_id: str | None = None
    source_type: str | None = None       # ja | bwa | euer | susa (dominant)
    period_start: str | None = None      # ISO-Datum
    period_end: str | None = None
    coverage_months: int | None = None
    is_consolidated: bool | None = None


class Company(BaseModel):
    id: str
    name: str
    rechtsform: str | None = None
    branche_code: str | None = None      # FK industry_categories (app/industries.py)
    branche_label: str | None = None     # Freitext-Notiz (Hybrid)
    revenue_band: str | None = None
    employee_band: str | None = None
    created_by: str | None = None
    created_at: datetime
