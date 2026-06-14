"""Phase A: CompaniesRepo + JobsRepo.set_company (mock supabase-client)."""
from unittest.mock import MagicMock
from datetime import datetime, timezone
from app.db import CompaniesRepo, JobsRepo


def _company_row(**kw):
    base = {"id": "c1", "name": "Acme GmbH",
            "created_at": datetime.now(timezone.utc).isoformat()}
    base.update(kw)
    return base


def test_create_setzt_felder():
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.return_value.data = [
        _company_row(rechtsform="GmbH", branche_code="it_software", created_by="alice")]
    CompaniesRepo(client=client).create(
        "Acme GmbH", created_by="alice", rechtsform="GmbH",
        branche_code="it_software", branche_label="B2B SaaS")
    row = client.table.return_value.insert.call_args.args[0]
    assert row["name"] == "Acme GmbH"
    assert row["created_by"] == "alice"
    assert row["branche_code"] == "it_software"
    assert row["branche_label"] == "B2B SaaS"


def test_get_none_wenn_leer():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    assert CompaniesRepo(client=client).get("x") is None


def test_find_by_name_created_by_null_nutzt_is_null():
    client = MagicMock()
    tbl = client.table.return_value
    tbl.select.return_value.eq.return_value.is_.return_value.execute.return_value.data = []
    CompaniesRepo(client=client).find_by_name("Acme GmbH", None)
    tbl.select.return_value.eq.return_value.is_.assert_called_with("created_by", "null")


def test_list_for_user_filtert_legacy_und_eigene():
    client = MagicMock()
    client.table.return_value.select.return_value.order.return_value.execute.return_value.data = [
        _company_row(id="c1", name="A", created_by="alice"),
        _company_row(id="c2", name="B", created_by="bob"),
        _company_row(id="c3", name="C", created_by=None),   # legacy → für alle
    ]
    out = CompaniesRepo(client=client).list_for_user("alice")
    ids = {c.id for c in out}
    assert ids == {"c1", "c3"}   # bob's c2 raus, legacy c3 drin


def test_set_company_updated_job():
    client = MagicMock()
    JobsRepo(client=client).set_company("job-1", "c1",
                                        metadata={"source_type": "ja", "coverage_months": 12})
    upd = client.table.return_value.update.call_args.args[0]
    assert upd["company_id"] == "c1"
    assert upd["source_type"] == "ja"
    assert upd["coverage_months"] == 12


def test_jobs_list_for_user_parametrisiert_und_merged():
    """Code-Review: KEINE String-Interpolation des Usernames in den Filter
    (Injection). Parametrisierte .eq (eigene) + .is_ (legacy NULL), gemerged,
    neueste zuerst."""
    client = MagicMock()
    sel = client.table.return_value.select.return_value
    sel.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"id": "j1", "status": "ready", "created_at": "2026-06-14T10:00:00Z",
         "output_path": "j1/output.xlsx", "source_type": "ja",
         "input_files": [{"name": "JA2024.pdf"}],
         "companies": {"name": "Acme GmbH", "branche_code": "it_software"}},
    ]
    sel.is_.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"id": "j2", "status": "extracting", "created_at": "2026-06-13T09:00:00Z",
         "output_path": None, "source_type": None, "input_files": [], "companies": None},
    ]
    out = JobsRepo(client=client).list_for_user("alice")
    sel.eq.assert_called_with("created_by", "alice")
    sel.is_.assert_called_with("created_by", "null")
    assert not sel.or_.called                  # keine Filter-Interpolation
    assert [r["id"] for r in out] == ["j1", "j2"]   # neueste zuerst (Merge+Sort)
    assert out[0]["company_name"] == "Acme GmbH"
    assert out[0]["file_names"] == ["JA2024.pdf"]
    assert out[0]["has_output"] is True
    assert out[1]["company_name"] is None
    assert out[1]["has_output"] is False


def test_jobs_list_for_user_leer_nutzt_nur_legacy():
    """Leerer/None-Username darf NICHT alle Jobs liefern — nur Legacy-NULL."""
    client = MagicMock()
    sel = client.table.return_value.select.return_value
    sel.is_.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    JobsRepo(client=client).list_for_user("")
    assert not sel.eq.called
    sel.is_.assert_called_with("created_by", "null")


def test_companies_update_setzt_nur_bekannte_felder():
    client = MagicMock()
    CompaniesRepo(client=client).update("c1", branche_code="it_software",
                                        rechtsform="GmbH", unknown="x")
    upd = client.table.return_value.update.call_args.args[0]
    assert upd == {"branche_code": "it_software", "rechtsform": "GmbH"}


def test_link_company_aktualisiert_bestehende():
    from unittest.mock import patch
    from datetime import datetime, timezone
    from app.routes import job as jobmod
    from app.models import Company
    with patch.object(jobmod, "CompaniesRepo") as CRepo, \
         patch.object(jobmod, "JobsRepo") as JRepo:
        existing = Company(id="c1", name="Acme", created_at=datetime.now(timezone.utc))
        CRepo.return_value.find_by_name.return_value = existing
        jobmod._link_company("job-1", {"name": "Acme", "branche_code": "it_software"}, "alice")
        CRepo.return_value.update.assert_called_once_with(  # neu angegebene Branche übernehmen
            "c1", branche_code="it_software")
        CRepo.return_value.create.assert_not_called()
        JRepo.return_value.set_company.assert_called_with("job-1", "c1")


def test_metrics_repo_upsert():
    from app.db import MetricsRepo
    client = MagicMock()
    MetricsRepo(client=client).upsert_company_year(
        "c1", 2024, "j1", "ja", {"umsatz": 1000.0, "jue": 50.0, "metrics_version": 1})
    args, kw = client.table.return_value.upsert.call_args
    row = args[0]
    assert row["company_id"] == "c1" and row["fiscal_year"] == 2024
    assert row["data_source"] == "ja" and row["source_job_id"] == "j1"
    assert row["umsatz"] == 1000.0 and row["metrics_version"] == 1
    assert kw["on_conflict"] == "company_id,fiscal_year,data_source"
