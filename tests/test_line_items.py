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
