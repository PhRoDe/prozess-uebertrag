from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from app.models import Job, InputFile, JobStatus


def make_job(status=JobStatus.UPLOADED, extraction=None):
    now = datetime.now(timezone.utc)
    return Job(
        id="job-1", created_at=now, status=status,
        input_files=[InputFile(name="a.pdf", size=100, storage_path="job-1/input/a.pdf")],
        expires_at=now + timedelta(hours=24),
        extraction=extraction,
    )


def test_extract_skips_if_terminal_status():
    """Fix 1D: already in review_needed → no Claude call."""
    from app.worker.tasks import extract_job
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude:
        repo = Repo.return_value
        repo.get.return_value = make_job(status=JobStatus.REVIEW_NEEDED)
        extract_job("job-1")
        # try_claim darf nie aufgerufen werden, da frühzeitig abgebrochen
        repo.try_claim.assert_not_called()
        Claude.return_value.classify_document.assert_not_called()


def test_extract_skips_when_claim_fails():
    """Fix 1A: Wenn ein anderer Worker den Job hält, brechen wir ab."""
    from app.worker.tasks import extract_job
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude:
        repo = Repo.return_value
        repo.get.return_value = make_job(status=JobStatus.UPLOADED)
        repo.try_claim.return_value = False  # claim-failed
        extract_job("job-1")
        Claude.return_value.classify_document.assert_not_called()


def test_extract_sets_review_needed_on_success():
    from app.worker.tasks import extract_job
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.classify_pdf") as cls, \
         patch("app.worker.tasks.extract_text", return_value="a lot of text " * 20), \
         patch("app.worker.tasks.extract_guv_section", return_value="GuV text"):
        repo = Repo.return_value
        repo.try_claim.return_value = True
        repo.get.return_value = make_job(status=JobStatus.UPLOADED)
        Storage.return_value.download_input.return_value = b"%PDF-fake"
        from app.worker.pdf_detect import PdfKind
        cls.return_value = PdfKind.TEXT
        claude = Claude.return_value
        claude.classify_document.return_value = "jahresabschluss"
        claude.extract_text_pdf.return_value = {
            "type": "jahresabschluss", "year": 2024, "previous_year": 2023,
            "groups": [], "open_questions": [],
        }
        extract_job("job-1")
        repo.set_extraction.assert_called_once()


def test_finalize_writes_excel_and_sets_ready():
    from app.worker.tasks import finalize_job
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.build_excel", return_value=b"xlsx-bytes"):
        repo = Repo.return_value
        repo.try_claim.return_value = True
        job = make_job(status=JobStatus.REVIEW_NEEDED,
                       extraction={"consolidated": {"years": [2024], "rows": [], "questions": []}})
        repo.get.return_value = job
        Storage.return_value.upload_output.return_value = "job-1/output.xlsx"
        finalize_job("job-1", {"4980": "7g. Verschiedene betriebliche Kosten"})
        repo.set_output.assert_called_once()


def test_resume_stuck_jobs_retries_extracting():
    """Fix 1A: nach Deploy-Restart müssen extracting-Jobs wieder aufgenommen werden."""
    from app.worker import tasks as tasks_mod
    stuck = make_job(status=JobStatus.EXTRACTING)
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.extract_job") as extract_mock:
        repo = Repo.return_value
        repo.list_resumable.return_value = [stuck]
        tasks_mod.resume_stuck_jobs()
        repo.release_claim.assert_called_once_with("job-1")
        extract_mock.assert_called_once_with("job-1")
