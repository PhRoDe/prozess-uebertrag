from datetime import datetime, timezone
from app.models import Job, InputFile, JobStatus


def test_input_file_basics():
    f = InputFile(name="ja-2024.pdf", size=1024, storage_path="job/input/ja-2024.pdf")
    assert f.name.endswith(".pdf")
    assert f.pdf_type is None
    assert f.doc_type is None


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
    assert data["processing_node"] is None


def test_job_status_enum_values():
    assert JobStatus.UPLOADED.value == "uploaded"
    assert JobStatus.REVIEW_NEEDED.value == "review_needed"
    assert JobStatus.READY.value == "ready"
