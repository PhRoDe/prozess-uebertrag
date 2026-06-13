"""Phase 2: Projektion der consolidated-Struktur → line_items / line_item_groups.

project_line_items ist rein (kein DB-Zugriff): aus columns + groups + accounts
werden flache Zeilen für die relationale Konten-Schicht abgeleitet.
"""
from app.db import project_line_items


def _cons():
    return {
        "columns": [
            {"label": "2024", "kind": "ja", "year": 2024},
            {"label": "Susa Dez 2025", "kind": "bwa", "year": 2025},
        ],
        "groups": [
            {"name": "1. Umsatzerlöse", "gkv_section": "umsatzerloese",
             "column_sums": {0: 1000.0},
             "accounts": [
                 {"konto_nr": "8400", "bezeichnung": "Erlöse",
                  "values": {0: 900.0, 1: 500.0}, "confidence": "high"},
             ]},
        ],
    }


def test_project_line_items_one_row_per_account_and_column_with_value():
    li, grp = project_line_items("job-1", _cons())
    # Konto 8400 hat Werte in Spalte 0 UND 1 → zwei line_items
    assert len(li) == 2
    by_col = {r["col_idx"]: r for r in li}
    assert by_col[0]["konto_nr"] == "8400"
    assert by_col[0]["betrag"] == 900.0
    assert by_col[0]["source_type"] == "ja"
    assert by_col[0]["column_label"] == "2024"
    assert by_col[0]["group_name"] == "1. Umsatzerlöse"
    assert by_col[0]["gkv_section"] == "umsatzerloese"
    assert by_col[0]["is_restposten"] is False
    assert by_col[1]["betrag"] == 500.0
    assert by_col[1]["source_type"] == "bwa"


def test_project_group_rows_printed_vs_acc_sum():
    _li, grp = project_line_items("job-1", _cons())
    # Spalte 0: gedruckt 1000, erfasst 900 → group row mit Diff-Basis
    g0 = next(g for g in grp if g["col_idx"] == 0)
    assert g0["printed_sum"] == 1000.0
    assert g0["acc_sum"] == 900.0
    assert g0["group_name"] == "1. Umsatzerlöse"
    assert g0["job_id"] == "job-1"
    # Spalte 1: kein column_sum, aber ein Konto-Wert → group row mit acc_sum 500
    g1 = next(g for g in grp if g["col_idx"] == 1)
    assert g1["printed_sum"] is None
    assert g1["acc_sum"] == 500.0


def test_project_skips_columns_without_data():
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "year": 2024},
                    {"label": "2023", "kind": "ja", "year": 2023}],
        "groups": [
            {"name": "X", "gkv_section": "umsatzerloese", "column_sums": {0: 100.0},
             "accounts": [{"konto_nr": "8400", "bezeichnung": "E",
                           "values": {0: 100.0}, "confidence": "high"}]},
        ],
    }
    li, grp = project_line_items("job-1", cons)
    # Spalte 1 (2023) hat weder column_sum noch Konto-Wert → keine Zeilen
    assert all(r["col_idx"] == 0 for r in li)
    assert all(g["col_idx"] == 0 for g in grp)


def test_project_marks_restposten():
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "year": 2024}],
        "groups": [
            {"name": "X", "gkv_section": "sonst_betr_aufw", "column_sums": {0: 200.0},
             "accounts": [
                 {"konto_nr": "", "bezeichnung": "Restposten — nicht aufgeschlüsselt im PDF",
                  "values": {0: 50.0}, "confidence": "synthetic"}]},
        ],
    }
    li, _grp = project_line_items("job-1", cons)
    assert li[0]["is_restposten"] is True


def test_materialize_deletes_then_inserts():
    """LineItemsRepo.materialize ist idempotent: erst eq-job_id-Delete, dann
    Insert der projizierten Zeilen in beide Tabellen."""
    from unittest.mock import MagicMock
    from app.db import LineItemsRepo
    client = MagicMock()
    tables: dict = {}
    client.table.side_effect = lambda name: tables.setdefault(name, MagicMock())
    LineItemsRepo(client=client).materialize("job-1", _cons())
    # Idempotenz: delete().eq("job_id","job-1") auf beiden Tabellen
    tables["line_items"].delete.return_value.eq.assert_called_with("job_id", "job-1")
    tables["line_item_groups"].delete.return_value.eq.assert_called_with("job_id", "job-1")
    # Insert mit den projizierten Zeilen (2 line_items aus _cons())
    assert tables["line_items"].insert.call_args.args[0].__len__() == 2
    assert tables["line_item_groups"].insert.called


def test_project_group_acc_sum_includes_children():
    """Code-Review #3: ein Parent (Konten in den Subs) darf in der group-row
    nicht acc_sum=0 zeigen → sonst Falsch-Lücke in v_job_completeness. acc_sum
    muss die Kinder einrechnen (wie verify.py / Builder)."""
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "year": 2024}],
        "groups": [
            {"name": "4. Materialaufwand", "gkv_section": "materialaufwand_rhb",
             "column_sums": {0: 300.0}, "accounts": [], "sub_group_of": None},
            {"name": "4. a) RHB", "column_sums": {0: 200.0},
             "sub_group_of": "4. Materialaufwand",
             "accounts": [{"konto_nr": "5100", "bezeichnung": "x", "values": {0: 200.0}}]},
            {"name": "4. b) Bezogene", "column_sums": {0: 100.0},
             "sub_group_of": "4. Materialaufwand",
             "accounts": [{"konto_nr": "5900", "bezeichnung": "y", "values": {0: 100.0}}]},
        ],
    }
    _li, grp = project_line_items("job-1", cons)
    parent = next(g for g in grp if g["group_name"] == "4. Materialaufwand")
    assert parent["acc_sum"] == 300.0  # 200 + 100 (Kinder) → kein Falsch-Diff


def test_project_group_acc_sum_includes_summary_only_children():
    """Codex P2: Kinder, die NUR eine gedruckte column_sum tragen (keine eigenen
    Konten, DATEV-Summary-only), müssen trotzdem in die Parent-acc_sum fließen —
    sonst meldet v_job_completeness eine Falsch-Lücke am Parent (analog
    verify._group_acc_sum)."""
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "year": 2024}],
        "groups": [
            {"name": "4. Materialaufwand", "gkv_section": "materialaufwand_rhb",
             "column_sums": {0: 300.0}, "accounts": [], "sub_group_of": None},
            # summary-only: gedruckte Summe, aber keine Einzelkonten
            {"name": "4. a) RHB", "column_sums": {0: 200.0},
             "sub_group_of": "4. Materialaufwand", "accounts": []},
            {"name": "4. b) Bezogene", "column_sums": {0: 100.0},
             "sub_group_of": "4. Materialaufwand", "accounts": []},
        ],
    }
    _li, grp = project_line_items("job-1", cons)
    parent = next(g for g in grp if g["group_name"] == "4. Materialaufwand")
    assert parent["acc_sum"] == 300.0  # 200 + 100 aus summary-only Kindern


def test_project_source_type_uses_doc_type_for_susa():
    """Codex P2: Susa-Spalten tragen kind='bwa' (consolidate-intern), aber das
    Audit-Schema unterscheidet ja|bwa|susa. source_type muss doc_type bevorzugen,
    damit Susa-Konten nicht als BWA gespeichert werden."""
    cons = {
        "columns": [{"label": "Susa Dez 2025", "kind": "bwa",
                     "doc_type": "susa", "year": 2025}],
        "groups": [
            {"name": "Klasse 4", "gkv_section": None, "column_sums": {0: 50.0},
             "accounts": [{"konto_nr": "4100", "bezeichnung": "x",
                           "values": {0: 50.0}, "confidence": "high"}]},
        ],
    }
    li, _grp = project_line_items("job-1", cons)
    assert li[0]["source_type"] == "susa"


# --- Phase 3a: Vollständigkeits-Panel (read-only View-Model) ---

def test_completeness_summary_zaehlt_luecken_und_vollstaendige():
    from app.db import completeness_summary
    cons = {
        "groups": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
        "questions": [
            {"type": "completeness_gap", "group": "A", "year": 2024,
             "diff": -100.0, "printed_sum": 1000.0, "acc_sum": 900.0,
             "document": "x.pdf"},
            {"type": "unmatched_account", "year": 2024},  # anderer Typ → ignoriert
        ],
    }
    s = completeness_summary(cons)
    assert s["has_gaps"] is True
    assert len(s["gaps"]) == 1
    assert s["gaps"][0]["group"] == "A"
    assert s["total_groups"] == 3
    assert s["complete_groups"] == 2  # B + C ohne Lücke


def test_completeness_summary_keine_luecken():
    from app.db import completeness_summary
    s = completeness_summary({"groups": [{"name": "A"}], "questions": []})
    assert s["has_gaps"] is False
    assert s["complete_groups"] == 1
    assert s["gaps"] == []


def test_completeness_summary_robust_gegen_none():
    from app.db import completeness_summary
    s = completeness_summary(None)
    assert s["has_gaps"] is False
    assert s["total_groups"] == 0


def test_completeness_summary_kein_widerspruch_bei_umnummerierung():
    """Codex P2: gap.group trägt den Roh-Extraktionsnamen, consolidated.groups
    den umnummerierten (HGB-Renumber). complete_groups darf NICHT per Namens-
    Match berechnet werden — sonst meldet das Panel 'alle vollständig' UND
    listet gleichzeitig eine Lücke (Widerspruch)."""
    from app.db import completeness_summary
    cons = {
        "groups": [{"name": "8. Abschreibungen"}, {"name": "9. Sonstige"}],
        "questions": [{"type": "completeness_gap", "group": "Abschreibungen",
                       "diff": 100.0}],
    }
    s = completeness_summary(cons)
    assert s["has_gaps"] is True
    assert s["complete_groups"] < s["total_groups"]


def test_completeness_summary_reichert_target_group_und_col_an():
    """Phase 3b: jede Lücke bekommt target_group (Name-Match gegen consolidated)
    + target_col (Jahr→Spalten-Index) als Vorbelegung fürs Korrektur-Dropdown.
    Plus columns/all_groups-Listen für die Selects."""
    from app.db import completeness_summary
    cons = {
        "columns": [{"label": "2023", "year": 2023}, {"label": "2024", "year": 2024}],
        "groups": [{"name": "8. Abschreibungen"}, {"name": "1. Umsatzerlöse"}],
        "questions": [{"type": "completeness_gap", "group": "Abschreibungen",
                       "year": 2024, "diff": 100.0}],
    }
    s = completeness_summary(cons)
    g = s["gaps"][0]
    assert g["target_group"] == "8. Abschreibungen"  # Substring-Match
    assert g["target_col"] == 1                        # Jahr 2024 → idx 1
    assert [c["idx"] for c in s["columns"]] == [0, 1]
    assert s["all_groups"] == ["8. Abschreibungen", "1. Umsatzerlöse"]


def test_completeness_summary_target_group_none_wenn_kein_match():
    from app.db import completeness_summary
    cons = {
        "columns": [{"label": "2024", "year": 2024}],
        "groups": [{"name": "1. Umsatzerlöse"}],
        "questions": [{"type": "completeness_gap", "group": "Völlig anders",
                       "year": 2024, "diff": 5.0}],
    }
    s = completeness_summary(cons)
    assert s["gaps"][0]["target_group"] is None


def test_completeness_summary_dropdown_nur_ja_spalten_und_leaf_gruppen():
    """Codex P2: Korrektur-Dropdown darf nur JA-Spalten (completeness_gap kommt
    aus JA-Gruppensummen) und nur Leaf-Gruppen anbieten — BWA/Susa-Spalten und
    Parent-Gruppen würden vom Builder verworfen/falsch angewandt."""
    from app.db import completeness_summary
    cons = {
        "columns": [
            {"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024},
            {"label": "BWA 2025", "kind": "bwa", "doc_type": "bwa", "year": 2025},
            {"label": "Susa 2025", "kind": "bwa", "doc_type": "susa", "year": 2025},
        ],
        "groups": [
            {"name": "4. Materialaufwand"},  # Parent
            {"name": "4. a) RHB", "sub_group_of": "4. Materialaufwand"},  # Leaf
            {"name": "1. Umsatz"},  # Leaf
        ],
        "questions": [{"type": "completeness_gap", "group": "4. a) RHB",
                       "year": 2024, "diff": 10.0}],
    }
    s = completeness_summary(cons)
    assert [c["label"] for c in s["columns"]] == ["2024"]   # nur JA-Spalte
    assert [c["idx"] for c in s["columns"]] == [0]          # globaler Index erhalten
    assert "4. Materialaufwand" not in s["all_groups"]      # Parent raus
    assert s["all_groups"] == ["4. a) RHB", "1. Umsatz"]
    assert s["gaps"][0]["target_col"] == 0
    assert s["gaps"][0]["target_group"] == "4. a) RHB"


def test_completeness_summary_target_group_parent_wird_none():
    """Eine Lücke auf einer Parent-Gruppe darf nicht auf den Parent vorbelegt
    werden (der wird verworfen) → target_group None → leerer Platzhalter."""
    from app.db import completeness_summary
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [{"name": "4. Materialaufwand"},
                   {"name": "4. a) RHB", "sub_group_of": "4. Materialaufwand"}],
        "questions": [{"type": "completeness_gap", "group": "4. Materialaufwand",
                       "year": 2024, "diff": 10.0}],
    }
    s = completeness_summary(cons)
    assert s["gaps"][0]["target_group"] is None


def test_completeness_summary_diff_aus_konsolidierter_spalte():
    """Codex P2: Anzeige/Prefill-Betrag aus der KONSOLIDIERTEN Ziel-Spalte
    (post-Normalisierung, JSONB-String-Keys), nicht aus dem pre-merge verify-diff.
    Konsolidiert bereits geschlossene Lücken werden nicht mehr gelistet."""
    from app.db import completeness_summary
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [
            {"name": "8. Abschreibungen", "column_sums": {"0": 1000.0},
             "accounts": [{"konto_nr": "4830", "values": {"0": 900.0}}]},
            {"name": "9. Schon voll", "column_sums": {"0": 500.0},
             "accounts": [{"konto_nr": "x", "values": {"0": 500.0}}]},
        ],
        "questions": [
            {"type": "completeness_gap", "group": "8. Abschreibungen", "year": 2024,
             "printed_sum": 1000.0, "acc_sum": 111.0, "diff": 889.0},  # stale verify
            {"type": "completeness_gap", "group": "9. Schon voll", "year": 2024,
             "printed_sum": 500.0, "acc_sum": 0.0, "diff": 500.0},  # konsolidiert 0
        ],
    }
    s = completeness_summary(cons)
    g1 = next(g for g in s["gaps"] if g["target_group"] == "8. Abschreibungen")
    assert g1["diff"] == 100.0       # 1000 - 900 (konsolidiert), nicht 889
    assert g1["acc_sum"] == 900.0
    assert all(g["target_group"] != "9. Schon voll" for g in s["gaps"])  # gedroppt


# --- Phase 4: PdfCacheRepo + created_by ---

def test_pdf_cache_repo_get_und_put():
    from unittest.mock import MagicMock
    from app.db import PdfCacheRepo
    client = MagicMock()
    tbl = MagicMock()
    client.table.return_value = tbl
    # get: Treffer
    tbl.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"extractions": [{"type": "jahresabschluss"}]}]
    repo = PdfCacheRepo(client=client)
    assert repo.get("hash1", "claude-x") == [{"type": "jahresabschluss"}]
    # get: kein Treffer
    tbl.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    assert repo.get("hash1", "claude-x") is None
    # put: upsert mit on_conflict
    repo.put("hash1", "claude-x", [{"type": "jahresabschluss"}])
    args, kwargs = tbl.upsert.call_args
    assert args[0]["pdf_hash"] == "hash1" and args[0]["model"] == "claude-x"
    assert kwargs["on_conflict"] == "pdf_hash,model"


def test_jobs_repo_create_setzt_created_by():
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from app.db import JobsRepo
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "j1", "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded", "input_files": [],
        "expires_at": datetime.now(timezone.utc).isoformat(), "created_by": "alice"}]
    JobsRepo(client=client).create([], created_by="alice")
    row = client.table.return_value.insert.call_args.args[0]
    assert row["created_by"] == "alice"


def test_completeness_gaps_dedupes_parent_wenn_kind_gap():
    """Codex P2: bei verschachtelten Gruppen (Parent + Kind beide mit Lücke für
    dasselbe fehlende Konto) darf nicht beide Warnungen zeigen — der Parent-Gap
    ist das Aggregat des Kind-Gaps. Kind bleibt (actionable), Parent dedupliziert."""
    from app.completeness import completeness_gaps
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [
            {"name": "4. Materialaufwand", "column_sums": {0: 1000.0}, "accounts": []},
            {"name": "4a) RHB", "sub_group_of": "4. Materialaufwand",
             "column_sums": {0: 1000.0},
             "accounts": [{"konto_nr": "5100", "values": {0: 900.0}}]},
        ],
        "questions": [
            {"type": "completeness_gap", "group": "4. Materialaufwand", "year": 2024,
             "printed_sum": 1000.0, "acc_sum": 900.0, "diff": 100.0},
            {"type": "completeness_gap", "group": "4a) RHB", "year": 2024,
             "printed_sum": 1000.0, "acc_sum": 900.0, "diff": 100.0},
        ],
    }
    names = [g["group"] for g in completeness_gaps(cons)]
    assert "4. Materialaufwand" not in names   # Parent dedupliziert
    assert "4a) RHB" in names                   # Kind bleibt


def test_completeness_gaps_behaelt_standalone_parent_gap():
    """Ein Parent-Gap OHNE Kind-Gap (Kinder vollständig, Parent eigene Lücke)
    bleibt sichtbar — nicht fälschlich dedupliziert."""
    from app.completeness import completeness_gaps
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [
            {"name": "4. Materialaufwand", "column_sums": {0: 1000.0}, "accounts": []},
            {"name": "4a) RHB", "sub_group_of": "4. Materialaufwand",
             "column_sums": {0: 500.0},
             "accounts": [{"konto_nr": "5100", "values": {0: 500.0}}]},  # Kind vollständig
        ],
        "questions": [
            {"type": "completeness_gap", "group": "4. Materialaufwand", "year": 2024,
             "printed_sum": 1000.0, "acc_sum": 500.0, "diff": 500.0},
        ],
    }
    names = [g["group"] for g in completeness_gaps(cons)]
    assert "4. Materialaufwand" in names


def test_completeness_gaps_dedup_nur_im_gleichen_jahr():
    """Codex P2: Parent-Dedup nur fürs SELBE Jahr — ein Kind-Gap 2023 darf einen
    eigenständigen Parent-Gap 2024 nicht verdecken."""
    from app.completeness import completeness_gaps
    cons = {
        "columns": [{"label": "2023", "kind": "ja", "doc_type": "ja", "year": 2023},
                    {"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [
            {"name": "4. Material", "column_sums": {}, "accounts": []},
            {"name": "4a) RHB", "sub_group_of": "4. Material", "column_sums": {}, "accounts": []},
        ],
        "questions": [
            {"type": "completeness_gap", "group": "4a) RHB", "year": 2023,
             "printed_sum": 500.0, "acc_sum": 400.0, "diff": 100.0},
            {"type": "completeness_gap", "group": "4. Material", "year": 2024,
             "printed_sum": 1000.0, "acc_sum": 700.0, "diff": 300.0},
        ],
    }
    keyed = {(g["group"], g["year"]) for g in completeness_gaps(cons)}
    assert ("4a) RHB", 2023) in keyed
    assert ("4. Material", 2024) in keyed   # nicht durch Kind-2023 verdeckt


def test_completeness_gaps_behaelt_parent_mit_residuum():
    """Codex P2: Parent-Gap nur dedupen, wenn er EXAKT das Kind-Aggregat ist.
    Ist der Parent-Diff größer (zusätzlicher Parent-Shortfall), bleibt er sichtbar."""
    from app.completeness import completeness_gaps
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [
            {"name": "4. Material", "column_sums": {}, "accounts": []},
            {"name": "4a) RHB", "sub_group_of": "4. Material", "column_sums": {}, "accounts": []},
        ],
        "questions": [
            {"type": "completeness_gap", "group": "4a) RHB", "year": 2024,
             "printed_sum": 500.0, "acc_sum": 400.0, "diff": 100.0},
            {"type": "completeness_gap", "group": "4. Material", "year": 2024,
             "printed_sum": 1000.0, "acc_sum": 700.0, "diff": 300.0},  # 200 mehr als Kind
        ],
    }
    keyed = {(g["group"], g["year"]) for g in completeness_gaps(cons)}
    assert ("4a) RHB", 2024) in keyed
    assert ("4. Material", 2024) in keyed   # Residuum 200 → bleibt


def test_completeness_gaps_dedupes_parent_wenn_diff_exakt_kind():
    """Gegenprobe: Parent-Diff == Σ Kind-Diff → dedupen."""
    from app.completeness import completeness_gaps
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [
            {"name": "4. Material", "column_sums": {}, "accounts": []},
            {"name": "4a) RHB", "sub_group_of": "4. Material", "column_sums": {}, "accounts": []},
        ],
        "questions": [
            {"type": "completeness_gap", "group": "4a) RHB", "year": 2024,
             "printed_sum": 500.0, "acc_sum": 400.0, "diff": 100.0},
            {"type": "completeness_gap", "group": "4. Material", "year": 2024,
             "printed_sum": 1000.0, "acc_sum": 900.0, "diff": 100.0},  # == Kind
        ],
    }
    keyed = {(g["group"], g["year"]) for g in completeness_gaps(cons)}
    assert ("4. Material", 2024) not in keyed   # exaktes Aggregat → dedup
    assert ("4a) RHB", 2024) in keyed


def test_completeness_gaps_dedup_robust_bei_negativen_werten():
    """Codex P2: Parent-Diff kinder-bewusst aus konsolidierten Werten — Dedup
    greift auch bei (sign-normalisierten) negativen Aufwands-Werten."""
    from app.completeness import completeness_gaps
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [
            {"name": "4. Aufwand", "column_sums": {0: -1000.0}, "accounts": []},
            {"name": "4a) RHB", "sub_group_of": "4. Aufwand", "column_sums": {0: -1000.0},
             "accounts": [{"konto_nr": "5100", "values": {0: -900.0}}]},
        ],
        "questions": [
            {"type": "completeness_gap", "group": "4a) RHB", "year": 2024,
             "printed_sum": -1000.0, "acc_sum": -900.0, "diff": -100.0},
            {"type": "completeness_gap", "group": "4. Aufwand", "year": 2024,
             "printed_sum": -1000.0, "acc_sum": -900.0, "diff": -100.0},
        ],
    }
    names = [g["group"] for g in completeness_gaps(cons)]
    assert "4. Aufwand" not in names   # Parent dedupliziert (−1000 −(−900) = −100 == Kind)
    assert "4a) RHB" in names


def test_completeness_gaps_parent_zaehlt_summary_only_kinder():
    """Codex P2: ein summary-only Kind (nur column_sums, keine Konten) zählt für
    die Parent-Vollständigkeit mit (wie verify._group_acc_sum/project_line_items).
    Sonst rechnet der Parent-Diff zu groß → bogus Residuum, Dedup greift nicht."""
    from app.completeness import completeness_gaps
    cons = {
        "columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [
            {"name": "4. Mat", "column_sums": {0: 1000.0}, "accounts": []},
            # summary-only Kind: 200 gedruckt, keine Konten
            {"name": "4a) Summe", "sub_group_of": "4. Mat", "column_sums": {0: 200.0},
             "accounts": []},
            # Detail-Kind: 800 gedruckt, 750 erfasst (fehlend 50)
            {"name": "4b) Detail", "sub_group_of": "4. Mat", "column_sums": {0: 800.0},
             "accounts": [{"konto_nr": "5100", "values": {0: 750.0}}]},
        ],
        "questions": [
            {"type": "completeness_gap", "group": "4b) Detail", "year": 2024,
             "printed_sum": 800.0, "acc_sum": 750.0, "diff": 50.0},
            {"type": "completeness_gap", "group": "4. Mat", "year": 2024,
             "printed_sum": 1000.0, "acc_sum": 950.0, "diff": 50.0},
        ],
    }
    names = [g["group"] for g in completeness_gaps(cons)]
    # Parent-Diff = 1000 - (200 summary + 750 detail) = 50 == Kind-Aggregat → dedup
    assert "4. Mat" not in names
    assert "4b) Detail" in names
