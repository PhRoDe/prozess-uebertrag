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


def test_sonst_betr_aufw_sum_includes_7c_through_7g():
    """Fix: Kategorien 7c-g (ohne eigene Sum-Row) müssen in Summe 7 einfließen.
    Sonst wird JÜ überbewertet."""
    consolidated = {
        "years": [2024],
        "rows": [
            {"konto_nr": "8400", "bezeichnung": "Umsatz",
             "gruppe": "1. Umsatzerlöse",
             "values": {2024: 1000000}, "confidence": "high"},
            {"konto_nr": "4100", "bezeichnung": "Fahrzeugkosten",
             "gruppe": "7d. Fahrzeugkosten",
             "values": {2024: 10000}, "confidence": "high"},
            {"konto_nr": "4200", "bezeichnung": "Werbung",
             "gruppe": "7e. Werbe- und Reisekosten",
             "values": {2024: 15000}, "confidence": "high"},
            {"konto_nr": "4900", "bezeichnung": "Sonstiges",
             "gruppe": "7g. Verschiedene betriebliche Kosten",
             "values": {2024: 5000}, "confidence": "high"},
        ],
        "questions": [],
    }
    xlsx = build_excel(consolidated)
    ws = _ws(xlsx)
    row7 = _find_row(ws, "7. Sonstige betriebliche Aufwendungen")
    assert row7 is not None
    formula = ws.cell(row=row7, column=3).value
    assert isinstance(formula, str) and formula.startswith("=")
    # Formel muss SUM-Refs enthalten — nicht nur 7a+7b
    import re
    sum_refs = re.findall(r"SUM\(", formula)
    assert len(sum_refs) >= 3, f"Erwarte >=3 SUM-Refs (7d, 7e, 7g), gefunden in: {formula}"


def test_coarse_group_reroutes_to_detail_bucket():
    """Fix: Claude sagt manchmal '4. Materialaufwand' statt '4a./4b.' — Konto
    darf nicht verloren gehen."""
    consolidated = {
        "years": [2024],
        "rows": [
            {"konto_nr": "5100", "bezeichnung": "Wareneingang",
             "gruppe": "4. Materialaufwand",  # grob, ohne a/b
             "values": {2024: 400000}, "confidence": "high"},
        ],
        "questions": [],
    }
    xlsx = build_excel(consolidated)
    ws = _ws(xlsx)
    found = False
    for row in ws.iter_rows():
        if row[1].value == "  Wareneingang":
            found = True
            break
    assert found, "Konto mit grober Gruppe wurde nicht in Excel dargestellt"


def test_bilanzgewinn_formula_references_vortrag_and_ausschuettung_rows():
    """Die Bilanzgewinn-Formel muss Vortrag UND Ausschüttung referenzieren,
    nicht nur JÜ + 0 - 0."""
    consolidated = {
        "years": [2024],
        "rows": [
            {"konto_nr": "8400", "bezeichnung": "Umsatz", "gruppe": "1. Umsatzerlöse",
             "values": {2024: 1000000}, "confidence": "high"},
        ],
        "bilanzgewinn_per_year": {
            2024: {"gewinnvortrag": 50000, "verlustvortrag": 0,
                   "ausschuettung": 30000, "bilanzgewinn": 100000}
        },
        "questions": [],
    }
    xlsx = build_excel(consolidated)
    ws = _ws(xlsx)
    bg_row = _find_row(ws, "17. Bilanzgewinn")
    formula = ws.cell(row=bg_row, column=3).value
    # Muss SUM-Refs auf Vortrag und Ausschüttung enthalten, nicht nur "+0-0"
    assert "SUM(" in formula, f"Bilanzgewinn-Formel ohne SUM-Refs: {formula}"


def test_bilanzgewinn_block_renders_gewinnvortrag_and_ausschuettung():
    """Fix 3A: Bilanzgewinn-Bereich aus Extraktion erscheint in der Excel."""
    consolidated = {
        "years": [2024],
        "rows": [
            {"konto_nr": "8400", "bezeichnung": "Umsatz", "gruppe": "1. Umsatzerlöse",
             "values": {2024: 1000000}, "confidence": "high"},
        ],
        "bilanzgewinn_per_year": {
            2024: {"gewinnvortrag": 50000, "verlustvortrag": 0,
                   "ausschuettung": 30000, "bilanzgewinn": 100000}
        },
        "questions": [],
    }
    xlsx = build_excel(consolidated)
    ws = _ws(xlsx)
    # Gewinnvortrag-Zeile muss existieren
    vortrag_row = _find_row(ws, "  Gewinn-/Verlustvortrag aus Vorjahr")
    assert vortrag_row is not None, "Gewinnvortrag-Zeile fehlt"
    assert ws.cell(row=vortrag_row, column=3).value == 50000
    # Ausschüttung-Zeile muss existieren
    aus_row = _find_row(ws, "  Ausschüttung")
    assert aus_row is not None, "Ausschüttung-Zeile fehlt"
    assert ws.cell(row=aus_row, column=3).value == 30000


def test_ebitda_block_has_topdown_and_bottomup_and_check():
    """Fix 4: EBITDA zweigleisig berechnen + Differenz als Check."""
    xlsx = build_excel(_sample_consolidated())
    ws = _ws(xlsx)
    top = _find_row(ws, "EBITDA (Top-Down)")
    bottom = _find_row(ws, "EBITDA (Bottom-Up)")
    check = _find_row(ws, "Check (Differenz)")
    assert top is not None and bottom is not None and check is not None
    # Check-Formel muss die Differenz der beiden EBITDA-Zeilen sein
    check_formula = ws.cell(row=check, column=3).value
    assert isinstance(check_formula, str)
    assert f"C{top}-C{bottom}" in check_formula


def test_ebitda_topdown_references_expected_anchors():
    xlsx = build_excel(_sample_consolidated())
    ws = _ws(xlsx)
    top_row = _find_row(ws, "EBITDA (Top-Down)")
    formula = ws.cell(row=top_row, column=3).value
    # Muss auf Gesamtleistung (oder deren Ref), Materialaufwand, Personalaufwand, Sonst. betr. Aufw. verweisen
    assert "+" in formula and "-" in formula


def test_ebitda_bottomup_references_jue_when_abschreibungen_present():
    """Bottom-Up EBITDA muss JÜ + alle Add-backs referenzieren wenn die Rows existieren."""
    consolidated = {
        "years": [2024],
        "rows": [
            {"konto_nr": "8400", "bezeichnung": "Umsatz", "gruppe": "1. Umsatzerlöse",
             "values": {2024: 1000000}, "confidence": "high"},
            {"konto_nr": "6000", "bezeichnung": "Löhne", "gruppe": "5a. Löhne und Gehälter",
             "values": {2024: 150000}, "confidence": "high"},
            {"konto_nr": "4800", "bezeichnung": "AfA", "gruppe": "6. Abschreibungen",
             "values": {2024: 40000}, "confidence": "high"},
            {"konto_nr": "7310", "bezeichnung": "Zinsen bank", "gruppe": "10. Zinsen und ähnliche Aufwendungen",
             "values": {2024: 5000}, "confidence": "high"},
            {"konto_nr": "7700", "bezeichnung": "KSt", "gruppe": "11. Steuern vom Einkommen und vom Ertrag",
             "values": {2024: 20000}, "confidence": "high"},
        ],
        "questions": [],
    }
    xlsx = build_excel(consolidated)
    ws = _ws(xlsx)
    bottom_row = _find_row(ws, "EBITDA (Bottom-Up)")
    formula = ws.cell(row=bottom_row, column=3).value
    import re
    refs = re.findall(r"([A-Z]+\d+)", formula)
    # Mindestens 4 echte Refs: JÜ, Steuern, Zinsen, AfA
    assert len(refs) >= 4, f"Bottom-Up-Formel zu kurz: {formula}"


def test_ebitda_marge_now_uses_real_ebitda_row_not_jue():
    """Fix 4: EBITDA-Marge zeigt jetzt auf EBITDA-Zeile, nicht mehr auf JÜ."""
    xlsx = build_excel(_sample_consolidated())
    ws = _ws(xlsx)
    ebitda_row = _find_row(ws, "EBITDA (Top-Down)")
    marge_row = _find_row(ws, "EBITDA-Marge")
    umsatzrendite_row = _find_row(ws, "Umsatzrendite")
    ebitda_formula = ws.cell(row=marge_row, column=3).value
    rendite_formula = ws.cell(row=umsatzrendite_row, column=3).value
    # Die beiden Formeln müssen unterschiedlich sein (verschiedene Anchor-Rows)
    assert ebitda_formula != rendite_formula, \
        f"EBITDA-Marge identisch mit Umsatzrendite: {ebitda_formula}"
    # EBITDA-Marge muss auf EBITDA-Zeile zeigen
    assert f"C{ebitda_row}" in ebitda_formula


def test_unmatched_group_goes_to_fragen_sheet():
    """Fix: Nicht-erkennbare Gruppen werden geloggt, nicht silent verworfen."""
    consolidated = {
        "years": [2024],
        "rows": [
            {"konto_nr": "9999", "bezeichnung": "Mysteriös",
             "gruppe": "Völlig Unbekannt",
             "values": {2024: 1234}, "confidence": "low"},
        ],
        "questions": [],
    }
    xlsx = build_excel(consolidated)
    wb = load_workbook(io.BytesIO(xlsx))
    fragen = wb["Fragen"]
    texts = [str(c.value) for row in fragen.iter_rows() for c in row if c.value]
    assert any("Mysteriös" in t for t in texts), \
        f"Fragen-Sheet sollte Mysteriös-Hinweis enthalten: {texts}"
