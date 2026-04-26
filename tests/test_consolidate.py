from app.worker.consolidate import merge_extractions


def _ja(year, prev_year, groups, sign="expenses_negative"):
    return {"type": "jahresabschluss", "year": year, "previous_year": prev_year,
            "sign_convention": sign, "groups": groups, "open_questions": []}


def _acc(nr, bez, gj, vj, conf="high"):
    return {"konto_nr": nr, "bezeichnung": bez,
            "betrag_gj": gj, "betrag_vj": vj, "confidence": conf}


def _grp(name, typ, accounts, pdf_sum_gj=None, pdf_sum_vj=None, sub_of=None,
         gkv_section=None):
    g = {"name": name, "type": typ, "accounts": accounts, "sub_group_of": sub_of}
    if pdf_sum_gj is not None: g["pdf_sum_gj"] = pdf_sum_gj
    if pdf_sum_vj is not None: g["pdf_sum_vj"] = pdf_sum_vj
    if gkv_section is not None: g["gkv_section"] = gkv_section
    return g


def test_single_ja_columns_and_groups():
    doc = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse 19%", 1000000, 900000)],
             pdf_sum_gj=1000000, pdf_sum_vj=900000),
        _grp("Materialaufwand", "aufwand",
             [_acc("5100", "Wareneingang", -400000, -370000)],
             pdf_sum_gj=-400000, pdf_sum_vj=-370000),
    ])
    r = merge_extractions([doc])
    # Spalte 0 = Vorjahr 2023, Spalte 1 = 2024
    assert len(r["columns"]) == 2
    assert r["columns"][0]["year"] == 2023
    assert r["columns"][1]["year"] == 2024
    # Beide Spalten sind ja
    assert all(c["kind"] == "ja" for c in r["columns"])
    # Gruppen-Struktur aus der einzigen PDF übernommen
    assert [g["name"] for g in r["groups"]] == ["Umsatzerlöse", "Materialaufwand"]


def test_ja_account_values_populated():
    doc = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse 19%", 1000000, 900000)]),
    ])
    r = merge_extractions([doc])
    g = r["groups"][0]
    assert len(g["accounts"]) == 1
    acc = g["accounts"][0]
    # Spalte 0 = 2023 (Vorjahr), Spalte 1 = 2024 (Eigenjahr)
    assert acc["values"][0] == 900000  # aus betrag_vj
    assert acc["values"][1] == 1000000  # aus betrag_gj


def test_multi_year_ja_matches_accounts_by_konto_nr():
    doc23 = _ja(2023, 2022, [
        _grp("Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse 19%", 900000, 800000)]),
    ])
    doc24 = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse 19%", 1000000, 900000)]),
    ])
    r = merge_extractions([doc23, doc24])
    years = sorted(set(c["year"] for c in r["columns"] if c["kind"] == "ja"))
    assert years == [2022, 2023, 2024]
    acc = r["groups"][0]["accounts"][0]
    # Das gleiche Konto hat Werte für alle drei Spalten
    vals_by_year = {r["columns"][i]["year"]: acc["values"].get(i)
                    for i in range(len(r["columns"]))}
    assert vals_by_year[2022] == 800000
    assert vals_by_year[2023] == 900000
    assert vals_by_year[2024] == 1000000


def test_previous_year_mismatch_logged_to_questions():
    doc23 = _ja(2023, 2022, [
        _grp("Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse 19%", 900000, 800000)]),
    ])
    doc24 = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse 19%", 1000000, 950000)]),
    ])
    r = merge_extractions([doc23, doc24])
    mismatches = [q for q in r["questions"] if q["type"] == "previous_year_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["konto_nr"] == "8400"
    assert mismatches[0]["year"] == 2023


def test_group_sum_mismatch_detected():
    doc = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse 19%", 1000000, 900000)],
             pdf_sum_gj=1500000,  # PDF-Summe weicht ab
             pdf_sum_vj=900000),
    ])
    r = merge_extractions([doc])
    mismatches = [q for q in r["questions"] if q["type"] == "group_sum_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["group"] == "Umsatzerlöse"


def test_bwa_creates_separate_column_with_group_sums_only():
    ja = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse 19%", 1000000, 900000)]),
    ])
    bwa = {
        "type": "bwa", "year": 2025, "period_label": "BWA 2025",
        "sign_convention": "expenses_negative",
        "positions": [
            {"name": "Umsatzerlöse", "type": "ertrag", "betrag": 500000},
        ],
    }
    r = merge_extractions([ja, bwa])
    # 3 Spalten: 2023 (ja), 2024 (ja), BWA 2025 (bwa)
    kinds = [c["kind"] for c in r["columns"]]
    assert kinds == ["ja", "ja", "bwa"]
    # Gruppen-Summe für BWA-Spalte wird direkt eingetragen
    g = r["groups"][0]
    bwa_col_idx = 2
    assert g["column_sums"][bwa_col_idx] == 500000


def test_group_order_from_newest_ja():
    old = _ja(2023, 2022, [_grp("B", "neutral", []), _grp("A", "neutral", [])])
    new = _ja(2024, 2023, [_grp("A", "neutral", []), _grp("B", "neutral", []),
                            _grp("C", "neutral", [])])
    r = merge_extractions([old, new])
    assert [g["name"] for g in r["groups"]] == ["A", "B", "C"]


def test_sign_convention_propagates_to_column():
    doc = _ja(2024, 2023, [_grp("X", "neutral", [])], sign="expenses_positive")
    r = merge_extractions([doc])
    for c in r["columns"]:
        assert c["sign_convention"] == "expenses_positive"


def test_bwa_groups_fuzzy_match_ja_groups_with_numbering():
    """BWA gibt 'Umsatzerlöse' zurück, JA hat '1. Umsatzerlöse' -- per
    Normalisierung (leading numbering strippen) sollen sie gemerged werden."""
    ja = _ja(2024, 2023, [
        _grp("1. Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse 19%", 1000000, 900000)]),
    ])
    bwa = {
        "type": "bwa", "year": 2025, "period_label": "BWA 2025",
        "sign_convention": "expenses_negative",
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "pdf_sum_gj": 500000, "sub_group_of": None,
             "accounts": [
                 {"konto_nr": "8401", "bezeichnung": "Erlöse 7%",
                  "betrag_gj": 500000, "confidence": "high"},
             ]},
        ],
    }
    r = merge_extractions([ja, bwa])
    # Account aus BWA muss in der '1. Umsatzerlöse'-Gruppe landen,
    # nicht in einer eigenen 'Umsatzerlöse'-Gruppe
    names = [g["name"] for g in r["groups"]]
    assert "1. Umsatzerlöse" in names
    assert "Umsatzerlöse" not in names
    umsatz = next(g for g in r["groups"] if g["name"] == "1. Umsatzerlöse")
    konto_nrs = [a["konto_nr"] for a in umsatz["accounts"]]
    assert "8401" in konto_nrs


def test_bwa_only_group_appended_when_no_ja_match():
    """BWA hat eine Gruppe ('Skonto-Erträge') die in der JA nicht vorkommt
    und deren Konto-Nr auch nirgendwo in der JA matched -- die Gruppe muss
    angehängt werden, damit der Wert nicht verloren geht."""
    ja = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse 19%", 1000000, 900000)]),
    ])
    bwa = {
        "type": "bwa", "year": 2025, "period_label": "BWA 2025",
        "sign_convention": "expenses_negative",
        "groups": [
            {"name": "Sonstige Zinserträge", "type": "ertrag",
             "pdf_sum_gj": 1234, "sub_group_of": None,
             "accounts": [
                 {"konto_nr": "2650", "bezeichnung": "Bankzinsen",
                  "betrag_gj": 1234, "confidence": "high"},
             ]},
        ],
    }
    r = merge_extractions([ja, bwa])
    names = [g["name"] for g in r["groups"]]
    assert "Sonstige Zinserträge" in names
    new_grp = next(g for g in r["groups"] if g["name"] == "Sonstige Zinserträge")
    assert len(new_grp["accounts"]) == 1
    assert new_grp["accounts"][0]["konto_nr"] == "2650"


def test_cross_year_group_match_via_normalized_name():
    """JA-2022 hat 'Materialaufwand', JA-2024 hat '4. Materialaufwand'.
    Cross-Year-Match per normalisiertem Namen muss greifen, sonst gehen die
    aelteren Werte verloren."""
    doc22 = _ja(2022, 2021, [
        _grp("Materialaufwand", "aufwand",
             [_acc("5100", "Wareneing.", 400000, 370000)],
             gkv_section="materialaufwand_rhb"),
    ])
    doc24 = _ja(2024, 2023, [
        _grp("4. Materialaufwand", "aufwand",
             [_acc("5100", "Wareneing.", 500000, 450000)],
             gkv_section="materialaufwand_rhb"),
    ])
    r = merge_extractions([doc22, doc24])
    by_name = {g["name"]: g for g in r["groups"]}
    target = by_name.get("4. Materialaufwand")
    assert target is not None
    konto5100 = next(a for a in target["accounts"] if a["konto_nr"] == "5100")
    by_year = {r["columns"][i]["year"]: konto5100["values"].get(i)
               for i in range(len(r["columns"]))}
    assert by_year[2022] == 400000  # aus doc22 own
    assert by_year[2024] == 500000  # aus doc24 own


def test_cross_year_group_match_via_gkv_section():
    """Wenn Gruppen-Namen komplett verschieden sind aber gkv_section gleich,
    matcht trotzdem. Beispiel: 'Steuern' (2022) vs '10. Steuern vom Einkommen
    und vom Ertrag' (2024)."""
    doc22 = _ja(2022, 2021, [
        _grp("Umsatz", "ertrag", [_acc("8400", "E", 100, 90)],
             gkv_section="umsatzerloese"),
        _grp("Steuern", "steuer", [_acc("7610", "GewSt", 5000, 4500)],
             gkv_section="ee_steuern"),
    ])
    doc24 = _ja(2024, 2023, [
        _grp("Umsatz", "ertrag", [_acc("8400", "E", 200, 150)],
             gkv_section="umsatzerloese"),
        _grp("10. Steuern vom Einkommen und vom Ertrag", "steuer",
             [_acc("7610", "GewSt", 6000, 5500)],
             gkv_section="ee_steuern"),
    ])
    r = merge_extractions([doc22, doc24])
    # Steuern-Konto sollte trotz Namens-Mismatch bei der einen Steuer-Gruppe landen
    all_accs = [a for g in r["groups"] for a in g["accounts"]
                if a["konto_nr"] == "7610"]
    # Genau ein Konto 7610, aber mit Werten fuer alle Spalten
    assert len(all_accs) == 1
    by_year = {r["columns"][i]["year"]: all_accs[0]["values"].get(i)
               for i in range(len(r["columns"]))}
    assert by_year[2022] == 5000
    assert by_year[2024] == 6000


def test_gkv_section_propagated_to_consolidated_groups():
    """Wenn die JA gkv_section pro Gruppe liefert, taucht der Slug im
    konsolidierten Output auf. Default 'neutral' wenn nicht gesetzt."""
    doc = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 1000, 900)],
             gkv_section="umsatzerloese"),
        _grp("Was-Auch-Immer", "neutral", []),  # ohne gkv_section
    ])
    r = merge_extractions([doc])
    by_name = {g["name"]: g for g in r["groups"]}
    assert by_name["Umsatzerlöse"]["gkv_section"] == "umsatzerloese"
    assert by_name["Was-Auch-Immer"]["gkv_section"] == "neutral"


def test_pdf_jue_collected_per_column_from_ja():
    """JA-Dokument liefert PDF-JUE pro Geschaeftsjahr und Vorjahr; die
    Konsolidierung mappt das auf die jeweiligen Spalten-Indizes."""
    doc23 = _ja(2023, 2022, [
        _grp("Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 900000, 800000)]),
    ])
    doc23["pdf_jahresueberschuss_gj"] = 50000.00
    doc23["pdf_jahresueberschuss_vj"] = 30000.00
    doc24 = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 1000000, 900000)]),
    ])
    doc24["pdf_jahresueberschuss_gj"] = 70000.00
    doc24["pdf_jahresueberschuss_vj"] = 50000.00
    r = merge_extractions([doc23, doc24])
    pdf_jue = r["pdf_jue_per_column"]
    by_year = {r["columns"][i]["year"]: pdf_jue.get(i)
               for i in range(len(r["columns"]))}
    assert by_year[2022] == 30000.00
    assert by_year[2023] == 50000.00
    assert by_year[2024] == 70000.00


def test_pdf_jue_mismatch_between_docs_logged():
    """JA-2024 sagt VJ=50k, JA-2023 sagt GJ=51k -> Mismatch in questions."""
    doc23 = _ja(2023, 2022, [_grp("X", "neutral", [])])
    doc23["pdf_jahresueberschuss_gj"] = 51000.00
    doc24 = _ja(2024, 2023, [_grp("X", "neutral", [])])
    doc24["pdf_jahresueberschuss_vj"] = 50000.00
    doc24["pdf_jahresueberschuss_gj"] = 70000.00
    r = merge_extractions([doc23, doc24])
    mismatches = [q for q in r["questions"]
                  if q["type"] == "pdf_jue_previous_year_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["year"] == 2023


def test_bwa_only_no_ja_uses_bwa_groups():
    """Wenn nur BWAs vorliegen (keine JA), bauen die BWAs die Struktur."""
    bwa = {
        "type": "bwa", "year": 2025, "period_label": "BWA 2025",
        "sign_convention": "expenses_negative",
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "pdf_sum_gj": 500000, "sub_group_of": None,
             "accounts": [
                 {"konto_nr": "8400", "bezeichnung": "Erlöse",
                  "betrag_gj": 500000, "confidence": "high"},
             ]},
        ],
    }
    r = merge_extractions([bwa])
    assert len(r["columns"]) == 1
    assert r["columns"][0]["kind"] == "bwa"
    names = [g["name"] for g in r["groups"]]
    assert "Umsatzerlöse" in names
    umsatz = next(g for g in r["groups"] if g["name"] == "Umsatzerlöse")
    assert len(umsatz["accounts"]) == 1
    assert umsatz["accounts"][0]["values"][0] == 500000


def test_bestandsveraenderung_verminderung_negates_values():
    """Doc-Gruppe heißt 'Verminderung des Bestandes' — Werte werden beim
    Konsolidieren negiert, damit positiv = Erhöhung als universelle
    Konvention im consolidated gilt."""
    doc = {
        "type": "jahresabschluss", "year": 2024, "previous_year": 2023,
        "sign_convention": "expenses_negative",
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese", "sub_group_of": None,
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Erlöse",
                            "betrag_gj": 1000000, "betrag_vj": 900000,
                            "confidence": "high"}]},
            {"name": "Verminderung des Bestandes an fertigen und unfertigen Erzeugnissen",
             "type": "ertrag",
             "gkv_section": "bestandsveraenderung", "sub_group_of": None,
             "accounts": [{"konto_nr": "4815",
                            "bezeichnung": "Bestandsveränderung",
                            "betrag_gj": 614000.00, "betrag_vj": 2398000.00,
                            "confidence": "high"}]},
        ],
    }
    r = merge_extractions([doc])
    bestand = next(g for g in r["groups"] if "Bestand" in g["name"])
    acc = bestand["accounts"][0]
    # Position heißt "Verminderung" → Werte negiert
    assert acc["values"][1] == -614000.00  # 2024 (gj)
    assert acc["values"][0] == -2398000.00  # 2023 (vj)


def test_bestandsveraenderung_erhoehung_keeps_values():
    """Doc-Gruppe heißt 'Erhöhung des Bestands' — Werte unverändert."""
    doc = {
        "type": "jahresabschluss", "year": 2020, "previous_year": 2019,
        "sign_convention": "expenses_positive",
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese", "sub_group_of": None,
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Erlöse",
                            "betrag_gj": 8000000, "betrag_vj": 7000000,
                            "confidence": "high"}]},
            {"name": "Erhöhung des Bestands an fertigen und unfertigen Erzeugnissen",
             "type": "ertrag",
             "gkv_section": "bestandsveraenderung", "sub_group_of": None,
             "accounts": [{"konto_nr": "4815",
                            "bezeichnung": "Bestandsveränderung",
                            "betrag_gj": 161100.00, "betrag_vj": 32500.00,
                            "confidence": "high"}]},
        ],
    }
    r = merge_extractions([doc])
    bestand = next(g for g in r["groups"] if "Bestand" in g["name"])
    acc = bestand["accounts"][0]
    # Position heißt "Erhöhung" → Werte unverändert
    assert acc["values"][1] == 161100.00  # 2020 (gj)
    assert acc["values"][0] == 32500.00   # 2019 (vj)


def test_bestandsveraenderung_cross_year_mix():
    """Mehrjahres-Mix: alte JAs 'Erhöhung', neue JAs 'Verminderung'.
    Konvention im consolidated muss konsistent sein: positiv = Erhöhung."""
    ja2020 = {
        "type": "jahresabschluss", "year": 2020, "previous_year": 2019,
        "sign_convention": "expenses_positive",
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese", "sub_group_of": None,
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Erlöse",
                            "betrag_gj": 8000000, "betrag_vj": 7000000,
                            "confidence": "high"}]},
            {"name": "Erhöhung des Bestands an fertigen und unfertigen Erzeugnissen",
             "type": "ertrag", "gkv_section": "bestandsveraenderung",
             "sub_group_of": None,
             "accounts": [{"konto_nr": "4815", "bezeichnung": "Bestandsv.",
                            "betrag_gj": 161100.00, "betrag_vj": 32500.00,
                            "confidence": "high"}]},
        ],
    }
    ja2024 = {
        "type": "jahresabschluss", "year": 2024, "previous_year": 2023,
        "sign_convention": "expenses_negative",
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese", "sub_group_of": None,
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Erlöse",
                            "betrag_gj": 21000000, "betrag_vj": 19000000,
                            "confidence": "high"}]},
            {"name": "Verminderung des Bestandes an fertigen und unfertigen Erzeugnissen",
             "type": "ertrag", "gkv_section": "bestandsveraenderung",
             "sub_group_of": None,
             "accounts": [{"konto_nr": "4815", "bezeichnung": "Bestandsv.",
                            "betrag_gj": 614000.00, "betrag_vj": 2398000.00,
                            "confidence": "high"}]},
        ],
    }
    r = merge_extractions([ja2020, ja2024])
    bestand = next(g for g in r["groups"] if "Bestand" in g["name"])
    acc = bestand["accounts"][0]
    # Spalten-Order: 2019, 2020, 2023, 2024
    cols = [c["year"] for c in r["columns"]]
    assert cols == [2019, 2020, 2023, 2024]
    # 2019 (vj von 2020 'Erhöhung'): +32500
    assert acc["values"][cols.index(2019)] == 32500.00
    # 2020 (own von 2020 'Erhöhung'): +161100
    assert acc["values"][cols.index(2020)] == 161100.00
    # 2023 (vj von 2024 'Verminderung'): negiert -> -2398000
    assert acc["values"][cols.index(2023)] == -2398000.00
    # 2024 (own von 2024 'Verminderung'): negiert -> -614000
    assert acc["values"][cols.index(2024)] == -614000.00
