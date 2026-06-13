from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
import pytest
from app.models import Job, InputFile, JobStatus


@pytest.fixture(autouse=True)
def _stub_line_items_repo():
    """extract_job materialisiert line_items über LineItemsRepo(). In Unit-Tests
    NIE die echte (network-)Repo konstruieren — sonst Live-DB-Zugriff. Tests, die
    materialize prüfen, patchen zusätzlich selbst."""
    with patch("app.worker.tasks.LineItemsRepo"):
        yield


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


def test_extract_handles_string_open_questions():
    """Live-Bug 2026-05-12 (Job f55c031b): Claude liefert open_questions
    manchmal als String statt Dict (z.B. "Diese PDF ist eine EÜR ohne
    erkennbare GuV-Gruppen"). `{**oq, ...}` crasht dann mit TypeError:
    'str' object is not a mapping. Konsistent zu consolidate.py:
    String-Hinweise als hint-only-Eintraege behandeln, nicht den Job killen.
    """
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
            "groups": [],
            # Mix: ein String-Hinweis + ein normales Dict
            "open_questions": [
                "Diese PDF ist eine EÜR ohne erkennbare GuV-Gruppen",
                {"konto_nr": "5400", "bezeichnung": "Wareneinkauf", "betrag_gj": 1000},
            ],
        }
        # darf NICHT crashen
        extract_job("job-1")
        # set_extraction muss aufgerufen worden sein → Job auf review_needed
        repo.set_extraction.assert_called_once()
        # Failure-Pfad darf NICHT durchlaufen sein
        failed_calls = [c for c in repo.set_status.call_args_list
                        if len(c.args) >= 2 and c.args[1] == JobStatus.FAILED]
        assert not failed_calls, f"Job sollte nicht failen, aber: {failed_calls}"


def test_extract_bundle_bwa_plus_susa_emits_two_extractions():
    """Prisma 06/2026: kombiniertes DATEV-Bundle (BWA-Aggregat + 'Summen und
    Salden'-Susa in EINER PDF) muss ZWEI Extraktionen liefern — BWA + Susa-
    Detail — damit die Einzelkonten als eigene Spalte erscheinen."""
    from app.worker.tasks import extract_job
    bundle_text = "Bezeichnung Dez/2025 BWA ... Summen und Salden (pro Monat) ..."
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.classify_pdf") as cls, \
         patch("app.worker.tasks.extract_text", return_value=bundle_text), \
         patch("app.worker.tasks.extract_susa_section", return_value="susa pages"):
        repo = Repo.return_value
        repo.try_claim.return_value = True
        repo.get.return_value = make_job(status=JobStatus.UPLOADED)
        Storage.return_value.download_input.return_value = b"%PDF-fake"
        from app.worker.pdf_detect import PdfKind
        cls.return_value = PdfKind.TEXT
        claude = Claude.return_value
        claude.classify_document.return_value = "bwa"

        def fake_extract(text, doc_type=None, **kw):
            return {"type": doc_type, "year": 2025,
                    "groups": [], "open_questions": []}
        claude.extract_text_pdf.side_effect = fake_extract

        extract_job("job-1")

        repo.set_extraction.assert_called_once()
        payload = repo.set_extraction.call_args.args[1]
        types = sorted(d["type"] for d in payload["documents"])
        assert types == ["bwa", "susa"], f"erwartet BWA+Susa, war {types}"
        # Susa-Extraktion lief über extract_susa_section, nicht über vollen Text
        susa_calls = [c for c in claude.extract_text_pdf.call_args_list
                      if c.kwargs.get("doc_type") == "susa"]
        assert susa_calls and susa_calls[0].args[0] == "susa pages"


def test_extract_pure_bwa_without_susa_stays_single():
    """Reine BWA ohne Susa-Abschnitt → genau EINE Extraktion (kein Susa-Doppel)."""
    from app.worker.tasks import extract_job
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.classify_pdf") as cls, \
         patch("app.worker.tasks.extract_text", return_value="BWA Kurzform nur Aggregat-Positionen"):
        repo = Repo.return_value
        repo.try_claim.return_value = True
        repo.get.return_value = make_job(status=JobStatus.UPLOADED)
        Storage.return_value.download_input.return_value = b"%PDF-fake"
        from app.worker.pdf_detect import PdfKind
        cls.return_value = PdfKind.TEXT
        claude = Claude.return_value
        claude.classify_document.return_value = "bwa"
        claude.extract_text_pdf.return_value = {
            "type": "bwa", "year": 2025, "groups": [], "open_questions": []}
        extract_job("job-1")
        payload = repo.set_extraction.call_args.args[1]
        assert len(payload["documents"]) == 1


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


def test_extract_ja_self_heals_missing_accounts():
    """Phase 1b: hat ein JA eine Lücke (Konten-Summe < gedruckte Gruppensumme),
    wird gezielt nachextrahiert und die fehlenden Konten gemergt."""
    from app.worker.tasks import extract_job
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.classify_pdf") as cls, \
         patch("app.worker.tasks.extract_text", return_value="JA text " * 20), \
         patch("app.worker.tasks.extract_guv_section", return_value="GuV text"):
        repo = Repo.return_value
        repo.try_claim.return_value = True
        repo.get.return_value = make_job(status=JobStatus.UPLOADED)
        Storage.return_value.download_input.return_value = b"%PDF-fake"
        from app.worker.pdf_detect import PdfKind
        cls.return_value = PdfKind.TEXT
        claude = Claude.return_value
        claude.classify_document.return_value = "jahresabschluss"
        # Erst-Extraktion: Lücke (gedruckt 200, nur 150 erfasst)
        claude.extract_text_pdf.return_value = {
            "type": "jahresabschluss", "year": 2024, "previous_year": 2023,
            "groups": [
                {"name": "7. sonstige", "gkv_section": "sonst_betr_aufw",
                 "pdf_sum_gj": 200.0,
                 "accounts": [{"konto_nr": "4900", "bezeichnung": "A",
                               "betrag_gj": 150.0}]},
            ],
            "open_questions": [],
        }
        # Selbstheilung liefert die VOLLE Kontenliste zurück
        claude.reextract_groups.return_value = {
            "7. sonstige": [
                {"konto_nr": "4900", "bezeichnung": "A", "betrag_gj": 150.0},
                {"konto_nr": "4901", "bezeichnung": "B", "betrag_gj": 50.0},
            ]
        }
        extract_job("job-1")
        claude.reextract_groups.assert_called_once()
        payload = repo.set_extraction.call_args.args[1]
        grp = payload["documents"][0]["groups"][0]
        assert len(grp["accounts"]) == 2
        assert sum(a["betrag_gj"] for a in grp["accounts"]) == 200.0


def test_extract_ja_survives_self_heal_error():
    """Review-Finding (1b): schlägt die Selbstheilung fehl (API-Fehler / kaputtes
    JSON bei der Re-Extraktion), darf das NICHT den ganzen Job killen — die
    erste, erfolgreiche Extraktion muss erhalten bleiben (graceful degradation)."""
    from app.worker.tasks import extract_job
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.classify_pdf") as cls, \
         patch("app.worker.tasks.extract_text", return_value="JA text " * 20), \
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
            "groups": [
                {"name": "7. sonstige", "gkv_section": "sonst_betr_aufw",
                 "pdf_sum_gj": 200.0,
                 "accounts": [{"konto_nr": "4900", "bezeichnung": "A", "betrag_gj": 150.0}]},
            ],
            "open_questions": [],
        }
        # Selbstheilung wirft (z.B. 429 nach Retries / kaputtes JSON)
        claude.reextract_groups.side_effect = RuntimeError("API down")

        extract_job("job-1")

        # Job darf NICHT failen — Original-Extraktion bleibt erhalten
        repo.set_extraction.assert_called_once()
        failed = [c for c in repo.set_status.call_args_list
                  if len(c.args) >= 2 and c.args[1] == JobStatus.FAILED]
        assert not failed, f"Job sollte nicht failen: {failed}"
        # Die Original-Gruppe ist noch da
        payload = repo.set_extraction.call_args.args[1]
        assert payload["documents"][0]["groups"][0]["name"] == "7. sonstige"


def test_unresolved_gaps_surface_in_consolidated_questions():
    """Codex P2-6: bleibt nach der Selbstheilung eine Lücke offen, muss sie im
    Fragen-Sheet sichtbar sein (consolidated.questions), nicht still verschwinden."""
    from app.worker.tasks import extract_job
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.classify_pdf") as cls, \
         patch("app.worker.tasks.extract_text", return_value="JA " * 20), \
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
            "groups": [{"name": "7. sonstige", "gkv_section": "sonst_betr_aufw",
                        "pdf_sum_gj": 200.0,
                        "accounts": [{"konto_nr": "4900", "bezeichnung": "A", "betrag_gj": 150.0}]}],
            "open_questions": [],
        }
        claude.reextract_groups.return_value = {}  # Heal kann nicht schließen
        extract_job("job-1")
        payload = repo.set_extraction.call_args.args[1]
        qs = payload["consolidated"].get("questions", [])
        assert any(q.get("type") == "completeness_gap" for q in qs), \
            f"offene Lücke fehlt im Fragen-Sheet: {qs}"


def test_extract_materializes_line_items_and_survives_its_failure():
    """Phase 2: line_items werden materialisiert; schlägt das fehl (DB-Problem),
    darf der Job NICHT scheitern (non-critical, graceful)."""
    from app.worker.tasks import extract_job
    with patch("app.worker.tasks.JobsRepo") as Repo, \
         patch("app.worker.tasks.ClaudeClient") as Claude, \
         patch("app.worker.tasks.StorageClient") as Storage, \
         patch("app.worker.tasks.LineItemsRepo") as LIRepo, \
         patch("app.worker.tasks.classify_pdf") as cls, \
         patch("app.worker.tasks.extract_text", return_value="JA " * 20), \
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
            "groups": [], "open_questions": []}
        LIRepo.return_value.materialize.side_effect = RuntimeError("DB down")

        extract_job("job-1")

        # materialize wurde versucht …
        LIRepo.return_value.materialize.assert_called_once()
        # … und der Job ist trotzdem nicht gescheitert
        repo.set_extraction.assert_called_once()
        failed = [c for c in repo.set_status.call_args_list
                  if len(c.args) >= 2 and c.args[1] == JobStatus.FAILED]
        assert not failed, f"Job sollte nicht failen: {failed}"


def test_collect_completeness_questions_filtert_ignorierte_vj_luecken():
    """Codex P2: Multi-Jahr — eine VJ-Lücke aus dem jüngeren JA für ein Jahr,
    das ein eigenes JA hat, ist KEIN echtes Problem (Konsolidierung ignoriert
    diese VJ-Werte). Darf nicht als completeness_gap erscheinen."""
    from app.worker.tasks import _collect_completeness_questions
    extractions = [
        {"type": "jahresabschluss", "year": 2024, "file": "ja2024.pdf",
         "_unresolved_gaps": [
             {"group": "X", "period": "vj", "year": 2023, "diff": 100.0},
             {"group": "Y", "period": "gj", "year": 2024, "diff": 50.0},
         ]},
        {"type": "jahresabschluss", "year": 2023, "file": "ja2023.pdf"},
    ]
    qs = _collect_completeness_questions(extractions)
    groups = {q["group"] for q in qs}
    assert "X" not in groups  # eigenes JA 2023 vorhanden → VJ-Lücke unterdrückt
    assert "Y" in groups      # GJ-Lücke bleibt sichtbar
    assert all(q["type"] == "completeness_gap" for q in qs)


def test_collect_completeness_questions_behaelt_vj_ohne_eigenes_ja():
    """VJ-Lücke für ein Jahr OHNE eigenes JA (existiert nur als Vorjahr) bleibt
    sichtbar — dort nutzt die Konsolidierung die VJ-Werte tatsächlich."""
    from app.worker.tasks import _collect_completeness_questions
    extractions = [
        {"type": "jahresabschluss", "year": 2024, "file": "ja2024.pdf",
         "_unresolved_gaps": [
             {"group": "X", "period": "vj", "year": 2023, "diff": 100.0},
         ]},
    ]
    qs = _collect_completeness_questions(extractions)
    assert {q["group"] for q in qs} == {"X"}
