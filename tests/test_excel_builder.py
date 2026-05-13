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


def test_pdf_jue_mismatch_creates_fragen_entry():
    """JÜ-Formel weicht vom PDF-Wert ab → Excel wird trotzdem gebaut, aber
    die Diff erscheint im Fragen-Sheet (sichtbar für den User) und in der
    'Differenz Excel ↔ PDF'-Zeile pro Spalte. Vorher: ValueError → keine
    Excel → User stuck. Jetzt: User bekommt die Excel, sieht die Diff
    prominent in der Anker-Zeile und im Fragen-Sheet, kann manuell prüfen
    (Live-Bug 2026-05-12 Job df31b6cd: Bilanzbericht-Mandat mit
    unvollständig extrahierten Konten — hard-fail blockierte den User
    obwohl die Excel grundsätzlich nutzbar gewesen wäre)."""
    cons = _sample()
    cons["pdf_jue_per_column"] = {0: 530000.00, 1: 700000.00}
    xlsx = build_excel(cons)
    wb = load_workbook(io.BytesIO(xlsx))
    assert "Fragen" in wb.sheetnames, "Fragen-Sheet muss bei JÜ-Mismatch angelegt werden"
    fragen = wb["Fragen"]
    fragen_rows = list(fragen.iter_rows(values_only=True))
    jue_mismatches = [r for r in fragen_rows if r[0] == "jue_excel_vs_pdf_mismatch"]
    assert len(jue_mismatches) >= 1, \
        f"Mindestens ein jue_excel_vs_pdf_mismatch-Eintrag erwartet, gefunden: {fragen_rows}"


def test_no_fragen_sheet_when_clean():
    """Bei sauberem Lauf (keine echten User-Entscheidungen offen) wird das
    Fragen-Sheet gar nicht erst angelegt."""
    cons = _sample()
    cons["questions"] = []
    xlsx = build_excel(cons)
    wb = load_workbook(io.BytesIO(xlsx))
    assert "Fragen" not in wb.sheetnames


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


def test_jue_formel_addiert_bestandsveraenderung():
    """JÜ-Formel addiert die Bestandsveränderungs-Gruppe (Werte sind im
    consolidated bereits signed: + Erhöhung / − Verminderung). Sie darf
    NICHT als Aufwand subtrahiert werden, sonst würde sie doppelt wirken."""
    cons = {
        "columns": [
            {"label": "2024", "kind": "ja", "year": 2024,
             "sign_convention": "expenses_negative"},
        ],
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese", "sub_group_of": None,
             "column_sums": {},
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Erlöse",
                            "values": {0: 1000000}, "confidence": "high"}]},
            {"name": "Verminderung des Bestandes an fertigen und unfertigen Erzeugnissen",
             "type": "ertrag", "gkv_section": "bestandsveraenderung",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "4815", "bezeichnung": "Bestandsv.",
                            "values": {0: -614000}, "confidence": "high"}]},
        ],
        "questions": [],
    }
    xlsx = build_excel(cons)
    ws = _ws(xlsx)
    je_row = _find_row(ws, "Jahresergebnis")
    bestand_row = _find_row(ws, "Verminderung des Bestandes an fertigen und unfertigen Erzeugnissen")
    formula = ws.cell(je_row, 3).value  # Spalte 2024 = C
    assert isinstance(formula, str)
    # Bestand-Zeile muss in der Formel addiert (+), nicht subtrahiert (−) sein
    assert f"+C{bestand_row}" in formula or formula.startswith(f"=C{bestand_row}") \
        or f"=C{bestand_row}+" in formula
    assert f"-C{bestand_row}" not in formula


def test_jue_ignoriert_redundante_aggregat_gruppen():
    """BWA-Aggregat-Gruppen ohne eigene Konten (z.B. 'Personalkosten' = Sum
    von 'Löhne' + 'Soz. Abg.') duerfen die JÜ-Formel nicht doppelt belasten,
    wenn die JA-Gruppen die Konten bereits enthalten."""
    cons = {
        "columns": [
            {"label": "BWA 2025", "kind": "bwa", "year": 2025,
             "sign_convention": "expenses_positive"},
        ],
        "groups": [
            # JA-Gruppe mit Konten: Löhne 1000, Soz. Abg. 200
            {"name": "Löhne und Gehälter", "type": "aufwand",
             "gkv_section": "personalaufwand_loehne",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "6020", "bezeichnung": "Gehälter",
                            "values": {0: 1000}, "confidence": "high"}]},
            {"name": "Soz. Abgaben", "type": "aufwand",
             "gkv_section": "personalaufwand_sozial",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "6110", "bezeichnung": "Sozialabgaben",
                            "values": {0: 200}, "confidence": "high"}]},
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese",
             "sub_group_of": None, "column_sums": {},
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Umsatz",
                            "values": {0: 5000}, "confidence": "high"}]},
            # BWA-Aggregat ohne eigene Konten — Sum schon in JA-Gruppen
            {"name": "Personalkosten", "type": "aufwand",
             "gkv_section": "neutral", "sub_group_of": None,
             "column_sums": {0: 1200},  # = 1000 + 200
             "accounts": []},
        ],
        "questions": [],
    }
    xlsx = build_excel(cons)
    ws = _ws(xlsx)
    je_row = _find_row(ws, "Jahresergebnis")
    personalkosten_row = _find_row(ws, "Personalkosten")
    formula = ws.cell(je_row, 3).value
    # Personalkosten-Zeile darf NICHT in der JE-Formel auftauchen
    assert f"C{personalkosten_row}" not in formula, \
        f"Aggregat-Gruppe 'Personalkosten' wurde doppelt gezaehlt: {formula}"
    # JÜ = Umsatz - Löhne - Soz.Abg. = 5000 - 1000 - 200 = 3800 (NICHT 2600 mit Doppelzählung)
    loehne_row = _find_row(ws, "Löhne und Gehälter")
    soz_row = _find_row(ws, "Soz. Abgaben")
    umsatz_row = _find_row(ws, "Umsatzerlöse")
    assert f"C{loehne_row}" in formula
    assert f"C{soz_row}" in formula
    assert f"C{umsatz_row}" in formula


def test_jue_nutzt_aggregate_wenn_keine_konten_in_spalte():
    """Wenn eine Spalte (typisch reine BWA ohne JA) keine Konten-Daten hat,
    werden die Aggregat-Gruppen normal in JÜ einbezogen."""
    cons = {
        "columns": [
            {"label": "BWA 2025", "kind": "bwa", "year": 2025,
             "sign_convention": "expenses_positive"},
        ],
        "groups": [
            # Keine JA-Gruppen, nur BWA-Aggregate
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese", "sub_group_of": None,
             "column_sums": {0: 5000}, "accounts": []},
            {"name": "Personalkosten", "type": "aufwand",
             "gkv_section": "neutral", "sub_group_of": None,
             "column_sums": {0: 1200}, "accounts": []},
        ],
        "questions": [],
    }
    xlsx = build_excel(cons)
    ws = _ws(xlsx)
    je_row = _find_row(ws, "Jahresergebnis")
    personalkosten_row = _find_row(ws, "Personalkosten")
    formula = ws.cell(je_row, 3).value
    # Hier MUSS Personalkosten in der Formel sein (nichts anderes da)
    assert f"C{personalkosten_row}" in formula


# ---------------------------------------------------------------------------
# EÜR (§4 Abs 3 EStG) — Karstens-Pattern
# ---------------------------------------------------------------------------

def _eur_karstens_2024():
    """Vereinfachter EÜR-Datensatz nach Karstens-Layout:
       A. Betriebseinnahmen → ertrag
       B. Betriebsausgaben → aufwand
       D.1 Hinzurechnungen → ertrag (addiert sich zum Gewinn)
       D. Kürzungen → aufwand (mindert Gewinn)

    Erwartete Excel-Formel (expenses_positive):
       Steuerlicher Gewinn = (Einnahmen + Hinzurechnungen)
                             - (Materialausgaben + Kürzungen)
       = (372474.75 + (543.10 + (-6832.00))) - (722.71 + 69483.50)
       = 366185.85 - 70206.21 = 295979.64
    """
    cols = [{"label": "2024", "kind": "ja", "year": 2024,
             "sign_convention": "expenses_positive"}]
    groups = [
        {"name": "A. 1. Einnahmen", "type": "ertrag", "sub_group_of": None,
         "gkv_section": "umsatzerloese", "column_sums": {},
         "accounts": [
             {"konto_nr": "8400", "bezeichnung": "Erlöse 19% USt",
              "values": {0: 372474.75}, "confidence": "high"},
         ]},
        {"name": "B. 1. Materialausgaben", "type": "aufwand",
         "sub_group_of": None, "gkv_section": "materialaufwand_rhb",
         "column_sums": {},
         "accounts": [
             {"konto_nr": "1600", "bezeichnung": "Verbindlichkeiten L+L",
              "values": {0: 722.71}, "confidence": "high"},
         ]},
        {"name": "D. 1. Hinzurechnungen", "type": "ertrag",
         "sub_group_of": None, "gkv_section": None, "column_sums": {},
         "accounts": [
             {"konto_nr": "4654", "bezeichnung": "Bewirtungskosten",
              "values": {0: 543.10}, "confidence": "high"},
             {"konto_nr": "4320", "bezeichnung": "Gewerbesteuer",
              "values": {0: -6832.00}, "confidence": "high"},
         ]},
        {"name": "D. Kürzungen", "type": "aufwand",
         "sub_group_of": None, "gkv_section": None, "column_sums": {},
         "accounts": [
             {"konto_nr": "9971", "bezeichnung": "IAB §7g (1) EStG",
              "values": {0: 69483.50}, "confidence": "high"},
         ]},
    ]
    return {"columns": cols, "groups": groups, "questions": [],
            "endwert_label": "Steuerlicher Gewinn nach §4 Abs. 3 EStG"}


def test_eur_endwert_label_in_excel():
    """Excel-Formel-Zeile beschriftet sich mit dem EÜR-Endwert. HGB-Default
    bleibt 'Jahresergebnis' (Backwards-Kompat)."""
    xlsx = build_excel(_eur_karstens_2024())
    ws = _ws(xlsx)
    assert _find_row(ws, "Steuerlicher Gewinn nach §4 Abs. 3 EStG") is not None
    xlsx_hgb = build_excel(_sample())
    ws_hgb = _ws(xlsx_hgb)
    assert _find_row(ws_hgb, "Jahresergebnis") is not None


def test_eur_hinzurechnungen_addiert_kuerzungen_subtrahiert():
    """Kern-Logik der EÜR: Hinzurechnungen werden im Endwert ADDIERT
    (type=ertrag), Kürzungen SUBTRAHIERT (type=aufwand). Damit kommt bei
    expenses_positive die richtige steuerliche Gewinn-Formel raus."""
    xlsx = build_excel(_eur_karstens_2024())
    ws = _ws(xlsx)
    je_row = _find_row(ws, "Steuerlicher Gewinn nach §4 Abs. 3 EStG")
    formula = ws.cell(je_row, 3).value
    einnahmen_row = _find_row(ws, "A. 1. Einnahmen")
    material_row = _find_row(ws, "B. 1. Materialausgaben")
    hinzu_row = _find_row(ws, "D. 1. Hinzurechnungen")
    kuerz_row = _find_row(ws, "D. Kürzungen")
    # Einnahmen + Hinzurechnungen sind Ertrag → in Plus-Teil
    assert f"C{einnahmen_row}+C{hinzu_row}" in formula \
        or f"C{hinzu_row}+C{einnahmen_row}" in formula
    # Material + Kürzungen sind Aufwand → in Minus-Teil (mit '-' Prefix)
    assert f"-C{material_row}" in formula
    assert f"-C{kuerz_row}" in formula


def test_eur_pdf_anker_label_dynamisch():
    """Wenn pdf_jue_per_column gesetzt ist, beschriftet die Anker-Zeile
    sich mit 'PDF-{endwert_label}' (statt hartcoded 'PDF-Jahresüberschuss')."""
    data = _eur_karstens_2024()
    # Steuerlicher Gewinn = (372474.75 - 6288.90) - (722.71 + 69483.50)
    #                     = 295979.64
    data["pdf_jue_per_column"] = {0: 295979.64}
    xlsx = build_excel(data)
    ws = _ws(xlsx)
    label_row = _find_row(ws, "PDF-Steuerlicher Gewinn nach §4 Abs. 3 EStG")
    assert label_row is not None
    assert abs(ws.cell(label_row, 3).value - 295979.64) < 0.01


def test_rohergebnis_format_uses_column_sums_when_no_accounts():
    """Live-Bug 2026-05-12 (Job df31b6cd): DATEV-Rohergebnis-Format-JAs
    liefern fast alle GuV-Top-Levels OHNE einzelne Konten — nur pdf_sum_gj
    pro Gruppe. Beispiel:
      - "1. Rohergebnis" type=ertrag, accounts=0, pdf_sum_gj=1.190.321,13
      - "2. Personalaufwand a) Löhne" type=aufwand, accounts=0,
        pdf_sum_gj=560.694,45, sub_of="2. Personalaufwand"
      - "4. sonstige betriebliche Aufwendungen" type=aufwand, accounts>0

    Vorher: Sub-Zelle wird auf 0 gesetzt (else-Branch im Pass 1), JÜ-Formel
    überspringt Rohergebnis-Top-Level (is_bare_top_level-Heuristik) → JÜ-Excel
    extrem falsch, Build-Time-Cross-Check fail. User sieht "Verarbeitung
    fehlgeschlagen".

    Fix: Wenn accounts=0 aber column_sums[col_idx] gesetzt ist, den Wert
    in die Zelle schreiben + in die JÜ-Formel einbeziehen.
    """
    cols = [{"label": "2024", "kind": "ja", "year": 2024,
             "sign_convention": "expenses_positive"}]
    groups = [
        {"name": "1. Rohergebnis", "type": "ertrag", "sub_group_of": None,
         "gkv_section": "umsatzerloese",
         "column_sums": {0: 1190321.13}, "accounts": []},
        {"name": "2. Personalaufwand", "type": "aufwand", "sub_group_of": None,
         "gkv_section": "personalaufwand_loehne",
         "column_sums": {}, "accounts": []},
        {"name": "2. Personalaufwand a) Löhne", "type": "aufwand",
         "sub_group_of": "2. Personalaufwand",
         "gkv_section": "personalaufwand_loehne",
         "column_sums": {0: 560694.45}, "accounts": []},
        {"name": "2. Personalaufwand b) Sozial", "type": "aufwand",
         "sub_group_of": "2. Personalaufwand",
         "gkv_section": "personalaufwand_sozial",
         "column_sums": {0: 65357.11}, "accounts": []},
        {"name": "4. sonstige betriebliche Aufwendungen", "type": "aufwand",
         "sub_group_of": None, "gkv_section": "sonst_betr_aufw",
         "column_sums": {0: 214284.17},
         "accounts": [
             {"konto_nr": "6300", "bezeichnung": "div. Aufw.",
              "values": {0: 214284.17}, "confidence": "high"},
         ]},
    ]
    # Erwarteter Endwert: 1190321.13 - 560694.45 - 65357.11 - 214284.17
    #                   = 349985.40
    data = {"columns": cols, "groups": groups, "questions": [],
            "pdf_jue_per_column": {0: 349985.40},
            "endwert_label": "Jahresüberschuss"}
    # Darf nicht mit Cross-Check-ValueError fehlschlagen
    xlsx = build_excel(data)
    ws = _ws(xlsx)
    # Rohergebnis-Zelle: column_sum-Wert, nicht 0
    rohe_row = _find_row(ws, "1. Rohergebnis")
    assert ws.cell(rohe_row, 3).value == 1190321.13, \
        f"Rohergebnis-Zelle sollte 1.190.321,13 sein, ist {ws.cell(rohe_row, 3).value}"
    # 2.a Löhne-Zelle: column_sum-Wert, nicht 0
    loehne_row = _find_row(ws, "  2. Personalaufwand a) Löhne")
    assert ws.cell(loehne_row, 3).value == 560694.45, \
        f"Löhne-Zelle sollte 560.694,45 sein, ist {ws.cell(loehne_row, 3).value}"
