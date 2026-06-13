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
