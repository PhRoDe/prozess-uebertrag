import io
from openpyxl import load_workbook
from app.excel.builder import build_excel


def _sample_consolidated():
    return {
        "years": [2023, 2024],
        "rows": [
            {"konto_nr": "8400", "bezeichnung": "Erlöse 19%",
             "gruppe": "1. Umsatzerlöse",
             "values": {2023: 1000000, 2024: 1100000}, "confidence": "high"},
            {"konto_nr": "5100", "bezeichnung": "Wareneingang 19%",
             "gruppe": "4a. Materialaufwand RHB",  # Claude-Variante, soll fuzzy matchen
             "values": {2023: 400000, 2024: 450000}, "confidence": "high"},
            {"konto_nr": "6000", "bezeichnung": "Löhne",
             "gruppe": "5a. Löhne und Gehälter",
             "values": {2023: 150000, 2024: 160000}, "confidence": "high"},
            {"konto_nr": "4980", "bezeichnung": "Unsicher",
             "gruppe": "7g. Verschiedene betriebliche Kosten",
             "values": {2023: 5000, 2024: 6000}, "confidence": "low"},
        ],
        "questions": [],
    }


def _ws(xlsx: bytes):
    return load_workbook(io.BytesIO(xlsx))["Übertrag"]


def _find_row(ws, col_b_value: str) -> int | None:
    for row in ws.iter_rows():
        if row[1].value == col_b_value:
            return row[0].row
    return None


def test_builder_produces_sum_formulas():
    xlsx = build_excel(_sample_consolidated())
    ws = _ws(xlsx)
    sum_cells = [c for row in ws.iter_rows() for c in row
                 if c.value and isinstance(c.value, str) and c.value.startswith("=SUM")]
    assert len(sum_cells) >= 3, f"Erwarte min 3 SUM-Formeln, gefunden {len(sum_cells)}"


def test_builder_creates_fragen_sheet():
    xlsx = build_excel(_sample_consolidated())
    wb = load_workbook(io.BytesIO(xlsx))
    assert "Fragen" in wb.sheetnames


def test_jahresueberschuss_formula_references_real_cells_not_header():
    """Fix 2B: JÜ-Formel darf nicht auf Row 1 (Header) zeigen — sonst #VALUE!."""
    import re
    xlsx = build_excel(_sample_consolidated())
    ws = _ws(xlsx)
    jue_row = _find_row(ws, "14. Jahresüberschuss")
    assert jue_row is not None
    formula = ws.cell(row=jue_row, column=3).value  # erste Jahres-Spalte
    assert isinstance(formula, str) and formula.startswith("="), formula
    # Extrahiere alle Zell-Referenzen (z.B. C3, D14) und prüfe dass keine auf Row 1 zeigt
    refs = re.findall(r"[A-Z]+(\d+)", formula)
    assert "1" not in refs, f"Formel referenziert Header-Row: {formula}"


def test_missing_category_falls_back_to_zero_not_row_one():
    """Fix 2B: Wenn Kategorie fehlt, muss die Formel '0' verwenden, nicht B1."""
    consolidated = {
        "years": [2024],
        "rows": [
            {"konto_nr": "8400", "bezeichnung": "Erlöse",
             "gruppe": "1. Umsatzerlöse",
             "values": {2024: 1000000}, "confidence": "high"},
        ],
        "questions": [],
    }
    xlsx = build_excel(consolidated)
    ws = _ws(xlsx)
    # JÜ-Formel muss existieren und darf keine Row-1-Refs enthalten
    jue_row = _find_row(ws, "14. Jahresüberschuss")
    formula = ws.cell(row=jue_row, column=3).value
    assert formula is not None
    # Keine Referenz auf Row 1 in der Formel
    import re
    refs = re.findall(r"[A-Z]+(\d+)", formula)
    assert "1" not in refs, f"Formel referenziert Header-Row: {formula}"


def test_fuzzy_matching_places_claude_variant_into_correct_group():
    """Claude sagt '4a. Materialaufwand RHB' — muss unter '4a. Aufwendungen für RHB und Waren'
    landen."""
    xlsx = build_excel(_sample_consolidated())
    ws = _ws(xlsx)
    # Finde die Zeile für '4a. Aufwendungen für RHB und Waren' (Summe)
    sum_row = _find_row(ws, "4a. Aufwendungen für RHB und Waren")
    assert sum_row is not None, "Summe-Zeile für 4a fehlt"
    # Wareneingang muss vor dieser Summenzeile stehen
    wareneingang_found_before_sum = False
    for row in ws.iter_rows(max_row=sum_row - 1):
        for c in row:
            if c.value == "  Wareneingang 19%":
                wareneingang_found_before_sum = True
    assert wareneingang_found_before_sum, "Wareneingang wurde nicht in 4a einsortiert"


def test_review_answer_overrides_group():
    """Wenn der User im Review-UI eine Gruppe zuordnet, muss die Excel die nutzen."""
    consolidated = _sample_consolidated()
    # Änder Unsicher von 7g zu 7a
    xlsx = build_excel(consolidated,
                       review_answers={"4980": "7a. Raumkosten"})
    ws = _ws(xlsx)
    # Finde die Zeile für "Unsicher" in der Excel
    unsicher_row = _find_row(ws, "  Unsicher")
    assert unsicher_row is not None
    # Vor "Unsicher" muss 7a kommen, nach ihr erst der Rest
    raumkosten_sum = _find_row(ws, "7a. Raumkosten")
    # "Unsicher" muss VOR der 7a-Summe stehen
    assert unsicher_row < raumkosten_sum, \
        f"Unsicher ist in Zeile {unsicher_row}, 7a-Summe in {raumkosten_sum}"


def test_low_confidence_rows_are_highlighted_yellow():
    xlsx = build_excel(_sample_consolidated())
    ws = _ws(xlsx)
    unsicher_row = _find_row(ws, "  Unsicher")
    assert unsicher_row is not None
    # Die Werte-Zelle (Spalte 3) muss gelb sein
    c = ws.cell(row=unsicher_row, column=3)
    fill_color = c.fill.start_color.rgb if c.fill.start_color else None
    # Yellow fill
    assert fill_color and "FFF3CD" in str(fill_color).upper(), \
        f"Erwarte gelben Hintergrund, bekomme {fill_color}"


def test_kennzahlen_section_has_percent_formulas():
    xlsx = build_excel(_sample_consolidated())
    ws = _ws(xlsx)
    mq_row = _find_row(ws, "Materialquote")
    assert mq_row is not None
    formula = ws.cell(row=mq_row, column=3).value
    assert isinstance(formula, str) and formula.startswith("=IFERROR")
    assert ws.cell(row=mq_row, column=3).number_format == "0.0%"
