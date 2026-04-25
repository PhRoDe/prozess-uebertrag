import io
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
