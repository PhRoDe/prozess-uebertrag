import io
import json
import pytest
from openpyxl import load_workbook
from app.excel.builder import build_excel


def _sample(sign="expenses_negative", with_bwa=False):
    cols = [
        {"label": "2023", "kind": "ja", "year": 2023, "sign_convention": sign},
        {"label": "2024", "kind": "ja", "year": 2024, "sign_convention": sign},
    ]
    if with_bwa:
        cols.append({"label": "BWA 2025", "kind": "bwa", "year": 2025,
                     "sign_convention": sign})
    groups = [
        {"name": "Umsatzerlöse", "type": "ertrag", "sub_group_of": None,
         "column_sums": {},
         "accounts": [
             {"konto_nr": "8400", "bezeichnung": "Erlöse 19%",
              "values": {0: 900000, 1: 1000000}, "confidence": "high"},
         ]},
        {"name": "Materialaufwand", "type": "aufwand", "sub_group_of": None,
         "column_sums": {},
         "accounts": [
             {"konto_nr": "5100", "bezeichnung": "Wareneingang",
              "values": {0: -370000, 1: -400000} if sign == "expenses_negative"
                        else {0: 370000, 1: 400000},
              "confidence": "high"},
         ]},
    ]
    if with_bwa:
        groups[0]["column_sums"][2] = 500000
        groups[1]["column_sums"][2] = -200000 if sign == "expenses_negative" else 200000
    return {"columns": cols, "groups": groups, "questions": []}


def _ws(xlsx):
    return load_workbook(io.BytesIO(xlsx))["Übertrag"]


def _find_row(ws, label):
    for row in ws.iter_rows():
        if row[1].value == label:
            return row[0].row
    return None


def test_header_has_konto_bezeichnung_and_year_columns():
    xlsx = build_excel(_sample())
    ws = _ws(xlsx)
    assert ws.cell(1, 1).value == "Konto"
    assert ws.cell(1, 2).value == "Bezeichnung"
    assert ws.cell(1, 3).value == "2023"
    assert ws.cell(1, 4).value == "2024"


def test_group_sum_row_comes_before_details():
    xlsx = build_excel(_sample())
    ws = _ws(xlsx)
    umsatz_sum = _find_row(ws, "Umsatzerlöse")
    erloese_detail = _find_row(ws, "  Erlöse 19%")
    assert umsatz_sum is not None and erloese_detail is not None
    assert umsatz_sum < erloese_detail


def test_ja_sums_are_formulas():
    xlsx = build_excel(_sample())
    ws = _ws(xlsx)
    umsatz_row = _find_row(ws, "Umsatzerlöse")
    val = ws.cell(umsatz_row, 3).value  # 2023-Spalte
    assert isinstance(val, str) and val.startswith("=SUM")


def test_bwa_sum_direct_value_when_no_accounts():
    """BWA-Spalten bekommen nur direct value wenn die Gruppe keine Konten hat.
    Mit Konten: auch BWA wird per SUM-Formel summiert."""
    xlsx = build_excel(_sample(with_bwa=True))
    ws = _ws(xlsx)
    umsatz_row = _find_row(ws, "Umsatzerlöse")
    bwa_val = ws.cell(umsatz_row, 5).value  # BWA 2025 = Spalte 5
    # Hier hat Umsatzerlöse 1 Konto (8400) → SUM-Formel
    assert isinstance(bwa_val, str) and bwa_val.startswith("=SUM")


def test_jahresergebnis_expenses_negative_is_simple_sum():
    """expenses_negative: alle Gruppen addieren, weil Aufwände eh negativ sind."""
    xlsx = build_excel(_sample(sign="expenses_negative"))
    ws = _ws(xlsx)
    je_row = _find_row(ws, "Jahresergebnis")
    formula = ws.cell(je_row, 3).value
    assert isinstance(formula, str) and formula.startswith("=")
    # Nur Pluszeichen, keine Minus-Zeichen
    assert "-" not in formula.replace("=", "")


def test_jahresergebnis_expenses_positive_uses_minus_for_aufwand():
    """expenses_positive: Aufwände müssen explizit subtrahiert werden."""
    xlsx = build_excel(_sample(sign="expenses_positive"))
    ws = _ws(xlsx)
    je_row = _find_row(ws, "Jahresergebnis")
    formula = ws.cell(je_row, 3).value
    assert isinstance(formula, str) and formula.startswith("=")
    # Muss ein Minus enthalten (Materialaufwand wird abgezogen)
    assert "-" in formula.replace("=", "")


def test_fragen_sheet_lists_questions():
    cons = _sample()
    cons["questions"] = [
        {"type": "previous_year_mismatch", "group": "Umsatzerlöse",
         "konto_nr": "8400", "year": 2023, "from_doc_year": 2024,
         "pdf_says": 1000000, "own_value": 999000},
    ]
    xlsx = build_excel(cons)
    wb = load_workbook(io.BytesIO(xlsx))
    assert "Fragen" in wb.sheetnames
    fragen = wb["Fragen"]
    rows = list(fragen.iter_rows(values_only=True))
    assert rows[0] == ("Thema", "Details")
    assert rows[1][0] == "previous_year_mismatch"


def test_pdf_jue_row_rendered_when_pdf_value_present():
    """Wenn pdf_jue_per_column geliefert wird, rendert der Builder eine
    'PDF-Jahresüberschuss'-Zeile mit den Werten und eine
    'Differenz Excel ↔ PDF'-Zeile mit Subtraktions-Formel."""
    cons = _sample()
    cons["pdf_jue_per_column"] = {0: 530000.00, 1: 600000.00}
    xlsx = build_excel(cons)
    ws = _ws(xlsx)
    pdf_jue_row = _find_row(ws, "PDF-Jahresüberschuss")
    diff_row = _find_row(ws, "Differenz Excel ↔ PDF")
    assert pdf_jue_row is not None
    assert diff_row is not None
    assert ws.cell(pdf_jue_row, 3).value == 530000.00
    assert ws.cell(pdf_jue_row, 4).value == 600000.00
    diff_formula = ws.cell(diff_row, 3).value
    assert isinstance(diff_formula, str) and diff_formula.startswith("=")


def test_pdf_jue_mismatch_logged_to_questions():
    """JÜ-Formel weicht vom PDF-Wert ab → Eintrag im Fragen-Sheet."""
    cons = _sample()
    # Sample: Umsatzerlöse 1000000, Materialaufwand -400000 → JÜ = 600000.
    # PDF-JÜ (z.B. via Claude-Mistake) sagt 700000 → Diff 100000 → Frage.
    cons["pdf_jue_per_column"] = {0: 530000.00, 1: 700000.00}
    xlsx = build_excel(cons)
    wb = load_workbook(io.BytesIO(xlsx))
    fragen = wb["Fragen"]
    rows = list(fragen.iter_rows(values_only=True))
    themes = [r[0] for r in rows]
    assert "jue_excel_vs_pdf_mismatch" in themes


def test_bilanzgewinn_block_separate_from_jue():
    """Gewinnvortrag/Ausschüttung/Bilanzgewinn sind NICHT im JÜ enthalten,
    sondern werden als eigener Block nach dem JÜ gerendert. Der Bilanzgewinn
    ist eine Formel (= JÜ + Gewinnvortrag - Ausschüttung)."""
    cons = {
        "columns": [
            {"label": "2024", "kind": "ja", "year": 2024,
             "sign_convention": "expenses_negative"},
        ],
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Erlöse",
                           "values": {0: 1000000}, "confidence": "high"}]},
            {"name": "Materialaufwand", "type": "aufwand",
             "gkv_section": "materialaufwand_rhb",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "5100", "bezeichnung": "Wareneing.",
                           "values": {0: -400000}, "confidence": "high"}]},
            {"name": "Gewinnvortrag", "type": "neutral",
             "gkv_section": "gewinnvortrag",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "8990", "bezeichnung": "Vortrag",
                           "values": {0: 50000}, "confidence": "high"}]},
            {"name": "Ausschüttung", "type": "neutral",
             "gkv_section": "ausschuettung",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "8995", "bezeichnung": "Ausschüttung",
                           "values": {0: 100000}, "confidence": "high"}]},
        ],
        "questions": [],
    }
    xlsx = build_excel(cons)
    ws = _ws(xlsx)
    je_row = _find_row(ws, "Jahresergebnis")
    bilanzgewinn_row = _find_row(ws, "Bilanzgewinn (Formel)")
    gewinnvortrag_sum_row = _find_row(ws, "Gewinnvortrag")
    ausschuettung_sum_row = _find_row(ws, "Ausschüttung")
    # Bilanzgewinn-Block kommt NACH dem JÜ
    assert bilanzgewinn_row is not None
    assert bilanzgewinn_row > je_row
    assert gewinnvortrag_sum_row > je_row
    assert ausschuettung_sum_row > je_row
    # JÜ-Formel enthaelt nur die GuV-Gruppen, nicht Gewinnvortrag/Ausschuettung
    je_formula = ws.cell(je_row, 3).value
    assert isinstance(je_formula, str)
    # Zeilen-Refs in Formel duerfen Gewinnvortrag/Ausschuettung NICHT enthalten
    assert f"C{gewinnvortrag_sum_row}" not in je_formula
    assert f"C{ausschuettung_sum_row}" not in je_formula
    # Bilanzgewinn = JÜ + Gewinnvortrag - Ausschuettung
    bg_formula = ws.cell(bilanzgewinn_row, 3).value
    assert isinstance(bg_formula, str) and bg_formula.startswith("=")


def test_jue_uses_gkv_section_for_classification():
    """Wenn Claude eine Aufwand-Gruppe versehentlich als type='neutral'
    klassifiziert, aber gkv_section korrekt ist, wird sie trotzdem
    abgezogen (gkv_section ist authoritativ)."""
    cons = {
        "columns": [
            {"label": "2024", "kind": "ja", "year": 2024,
             "sign_convention": "expenses_positive"},
        ],
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Erlöse",
                           "values": {0: 1000000}, "confidence": "high"}]},
            # Personalaufwand mit type=neutral, aber gkv_section=...loehne
            {"name": "Personalaufwand", "type": "neutral",
             "gkv_section": "personalaufwand_loehne",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "6000", "bezeichnung": "Löhne",
                           "values": {0: 300000}, "confidence": "high"}]},
        ],
        "questions": [],
    }
    xlsx = build_excel(cons)
    ws = _ws(xlsx)
    je_row = _find_row(ws, "Jahresergebnis")
    formula = ws.cell(je_row, 3).value
    # Personalaufwand muss subtrahiert werden -> Minus in Formel
    assert "-" in formula.replace("=", "")


def test_pdf_jue_row_skipped_when_no_pdf_value():
    """Ohne pdf_jue_per_column wird keine PDF-JÜ-Zeile gerendert."""
    cons = _sample()
    xlsx = build_excel(cons)
    ws = _ws(xlsx)
    assert _find_row(ws, "PDF-Jahresüberschuss") is None


def test_top_level_with_own_accounts_and_subgroups_sums_both():
    """Wenn eine Top-Level-Gruppe sowohl eigene Konten als auch Sub-Gruppen hat
    (zB 'Sonst. betr. Aufw.' mit direktem Konto 'Forderungsverlust' + den
    Sub-Gruppen 'Raumkosten', 'Versicherungen'), muss die Top-Level-Summe
    BEIDES enthalten."""
    cons = {
        "columns": [
            {"label": "2024", "kind": "ja", "year": 2024,
             "sign_convention": "expenses_negative"},
        ],
        "groups": [
            {"name": "Sonst. betr. Aufw.", "type": "aufwand",
             "gkv_section": "sonst_betr_aufw",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "6960", "bezeichnung": "Forderungsverlust",
                           "values": {0: -100}, "confidence": "high"}]},
            {"name": "Raumkosten", "type": "aufwand",
             "gkv_section": "sonst_betr_aufw",
             "sub_group_of": "Sonst. betr. Aufw.", "column_sums": {},
             "accounts": [{"konto_nr": "4210", "bezeichnung": "Miete",
                           "values": {0: -1000}, "confidence": "high"}]},
            {"name": "Versicherungen", "type": "aufwand",
             "gkv_section": "sonst_betr_aufw",
             "sub_group_of": "Sonst. betr. Aufw.", "column_sums": {},
             "accounts": [{"konto_nr": "4360", "bezeichnung": "Beitr.",
                           "values": {0: -200}, "confidence": "high"}]},
        ],
        "questions": [],
    }
    xlsx = build_excel(cons)
    ws = _ws(xlsx)
    parent_row = _find_row(ws, "Sonst. betr. Aufw.")
    formula = ws.cell(parent_row, 3).value
    # Formel muss SUM(eigene Konten) UND Sub-Gruppen-Refs enthalten
    assert isinstance(formula, str) and formula.startswith("=")
    raumkosten_row = _find_row(ws, "  Raumkosten")
    versicherungen_row = _find_row(ws, "  Versicherungen")
    assert raumkosten_row is not None and versicherungen_row is not None
    # Beide Sub-Refs muessen in der Top-Level-Formel auftauchen
    assert f"C{raumkosten_row}" in formula
    assert f"C{versicherungen_row}" in formula


def test_subgroup_sum_indented():
    """Sub-Gruppen bekommen Einrückung durch zwei führende Leerzeichen."""
    cons = {
        "columns": [
            {"label": "2024", "kind": "ja", "year": 2024,
             "sign_convention": "expenses_negative"},
        ],
        "groups": [
            {"name": "5. Sonstige betriebliche Aufwendungen", "type": "aufwand",
             "sub_group_of": None, "column_sums": {}, "accounts": []},
            {"name": "5.1 Versicherungen", "type": "aufwand",
             "sub_group_of": "5. Sonstige betriebliche Aufwendungen",
             "column_sums": {}, "accounts": [
                {"konto_nr": "4380", "bezeichnung": "Beiträge",
                 "values": {0: -500}, "confidence": "high"},
             ]},
        ],
        "questions": [],
    }
    xlsx = build_excel(cons)
    ws = _ws(xlsx)
    sub_row = _find_row(ws, "  5.1 Versicherungen")  # mit Einrückung
    assert sub_row is not None


def test_builder_survives_json_roundtrip():
    """Postgres JSONB turns int keys into strings. The builder must still
    produce non-empty value cells after a json.dumps -> json.loads cycle."""
    cons = _sample()
    cons_after_db = json.loads(json.dumps(cons))
    xlsx = build_excel(cons_after_db)
    ws = _ws(xlsx)
    erloese_row = _find_row(ws, "  Erlöse 19%")
    assert erloese_row is not None
    assert ws.cell(erloese_row, 3).value == 900000
    assert ws.cell(erloese_row, 4).value == 1000000


def test_builder_raises_when_all_account_values_empty():
    """Sanity guard: if every account value is None/empty after the build,
    something is structurally wrong (most likely a roundtrip bug). Fail loud
    instead of silently producing a useless Excel."""
    cons = _sample()
    for g in cons["groups"]:
        for acc in g["accounts"]:
            acc["values"] = {}
    with pytest.raises(ValueError, match="empty"):
        build_excel(cons)
