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


def test_summary_only_position_missing_in_newest_ja_not_dropped():
    """Regression Prisma 2026-06 / Fehler 2: Der jüngste JA (2024) hat keine
    'Zinsen und ähnliche Aufwendungen'-Position (war 0 → von Claude weggelassen),
    ein älterer JA (2023) liefert sie als Summen-only-Position (accs=0, nur
    pdf_sum). Da das Template aus dem jüngsten JA gebaut wird und _ingest_ja eine
    neue Gruppe bisher NUR bei unrouted-Konten anlegt, fiel der Wert (763,69 / VJ
    430,40) still raus → fehlte in der JÜ-Formel. Muss erhalten bleiben.
    """
    doc23 = _ja(2023, 2022, [
        _grp("1. Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 900000, 800000)],
             gkv_section="umsatzerloese"),
        _grp("9. Zinsen und ähnliche Aufwendungen", "aufwand", [],
             pdf_sum_gj=763.69, pdf_sum_vj=430.40, gkv_section="zinsaufwand"),
    ])
    doc24 = _ja(2024, 2023, [
        _grp("1. Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 1000000, 900000)],
             gkv_section="umsatzerloese"),
    ])
    r = merge_extractions([doc23, doc24])
    col = {r["columns"][i]["year"]: i for i in range(len(r["columns"]))}
    zins = [g for g in r["groups"] if g.get("gkv_section") == "zinsaufwand"]
    assert zins, "Zinsaufwand aus dem älteren JA darf nicht gedroppt werden"
    cs = zins[0]["column_sums"]
    assert cs.get(col[2023]) == 763.69, "GJ-Wert von JA2023 fehlt"
    assert cs.get(col[2022]) == 430.40, "VJ-Wert (2022) von JA2023 fehlt"


def test_vj_accounts_not_duplicated_when_year_has_own_ja():
    """Regression Prisma 2026-06 / Doppelzählung: Hat ein Jahr ein eigenes JA
    (Eigenjahr = authoritativ), dürfen die ANDERS benannten Vorjahres-Konten des
    Folge-JA nicht als Duplikate in dieselbe Spalte wandern. Sonst summiert sich
    die Spalte doppelt und der negative Restposten hebt es auf ('addiert-dann-
    abgezogen'). Real: JA2024-VJ 'Umsatzerlöse' vs JA2023-Eigenjahr 'Erlöse
    umsatzsteuerpflichtig' — beide für 2023.
    """
    doc23 = _ja(2023, 2022, [
        _grp("1. Umsatzerlöse", "ertrag",
             [_acc(None, "Erlöse umsatzsteuerpflichtig", 5155130.41, 2356170.92)],
             pdf_sum_gj=5155130.41, gkv_section="umsatzerloese"),
    ])
    doc24 = _ja(2024, 2023, [
        _grp("1. Umsatzerlöse", "ertrag",
             [_acc(None, "Umsatzerlöse", 4295856.96, 5155130.41)],  # VJ 2023, anderer Name
             pdf_sum_gj=4295856.96, gkv_section="umsatzerloese"),
    ])
    r = merge_extractions([doc23, doc24])
    col = {r["columns"][i]["year"]: i for i in range(len(r["columns"]))}
    umsatz = [g for g in r["groups"] if "Umsatzerl" in g["name"]][0]
    c2023 = col[2023]
    vals_2023 = [a["values"][c2023] for a in umsatz["accounts"] if c2023 in a.get("values", {})]
    assert vals_2023 == [5155130.41], \
        f"2023-Spalte doppelt belegt (VJ-Duplikat aus JA2024): {vals_2023}"


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


def test_previous_year_mismatch_resolved_silently():
    """Eigenjahres-Wert ist authoritativ. Mismatch zur VJ-Spalte eines anderen
    JAs wird stillschweigend aufgelöst — kein Eintrag im Fragen-Sheet."""
    doc23 = _ja(2023, 2022, [
        _grp("Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse 19%", 900000, 800000)]),
    ])
    doc24 = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse 19%", 1000000, 950000)]),
    ])
    r = merge_extractions([doc23, doc24])
    assert not [q for q in r["questions"] if q["type"] == "previous_year_mismatch"]
    # Eigenjahr 2023 (=900000) gewinnt gegen VJ-Wert 950000 aus JA2024
    umsatz = next(g for g in r["groups"] if g["name"] == "Umsatzerlöse")
    cols_2023 = next(i for i, c in enumerate(r["columns"]) if c["year"] == 2023)
    assert umsatz["accounts"][0]["values"][cols_2023] == 900000


def test_group_sum_mismatch_resolved_silently():
    """Konten-Summe ist authoritativ. Wenn die PDF-Summe von Claude erfunden ist
    (typisch: Übertrag-Doppelzählung), wird das stillschweigend ignoriert."""
    doc = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse 19%", 1000000, 900000)],
             pdf_sum_gj=1500000,
             pdf_sum_vj=900000),
    ])
    r = merge_extractions([doc])
    assert not [q for q in r["questions"] if q["type"] == "group_sum_mismatch"]


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


def test_column_traegt_doc_type_zur_unterscheidung_bwa_susa():
    """Codex P2: BWA- und Susa-Spalten haben beide kind='bwa'. Damit die
    relationale Konten-Schicht (source_type) Susa von BWA unterscheiden kann,
    trägt jede Spalte zusätzlich doc_type (ja|bwa|susa)."""
    ja = _ja(2024, 2023, [_grp("Umsatzerlöse", "ertrag",
                               [_acc("8400", "Erlöse", 1000000, 900000)])])
    bwa = {"type": "bwa", "year": 2025, "period_label": "BWA 2025",
           "sign_convention": "expenses_negative",
           "positions": [{"name": "Umsatzerlöse", "type": "ertrag", "betrag": 500000}]}
    susa = {"type": "susa", "year": 2025, "period_label": "Susa Dez 2025",
            "sign_convention": "expenses_negative",
            "positions": [{"name": "Klasse 4", "type": "aufwand", "betrag": 200000}]}
    r = merge_extractions([ja, bwa, susa])
    by_label = {c["label"]: c.get("doc_type") for c in r["columns"]}
    assert by_label["2024"] == "ja"
    assert by_label["BWA 2025"] == "bwa"
    assert by_label["Susa Dez 2025"] == "susa"


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
    # HGB-Renummerierung (Issue 2): einzige GKV-Position → "1. Materialaufwand"
    target = by_name.get("1. Materialaufwand")
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
    # HGB-Renummerierung: "Umsatzerlöse" → "1. Umsatzerlöse"; das neutrale
    # "Was-Auch-Immer" (keine gkv_section) bleibt unverändert.
    assert by_name["1. Umsatzerlöse"]["gkv_section"] == "umsatzerloese"
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


def test_bestandsveraenderung_verminderung_with_negative_value():
    """Wenn Claude (mit neuem Prompt) bereits ein Minus liefert: Wert wird
    nicht doppelt negiert. Konvention: -abs(value) für Verminderung."""
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
                            "betrag_gj": -614000.00, "betrag_vj": -2398000.00,
                            "confidence": "high"}]},
        ],
    }
    r = merge_extractions([doc])
    bestand = next(g for g in r["groups"] if "Bestand" in g["name"])
    acc = bestand["accounts"][0]
    # Beide Werte sind bereits negativ — die Konvention ist -abs(...),
    # darf also nicht zu +614000 / +2398000 werden
    assert acc["values"][1] == -614000.00
    assert acc["values"][0] == -2398000.00


# ---------------------------------------------------------------------------
# EÜR (§4 Abs 3 EStG) — Karstens-Pattern
# ---------------------------------------------------------------------------

def test_eur_endwert_label_durchgereicht():
    """Bei einer EÜR setzt Claude `endwert_label` (z.B. 'Steuerlicher Gewinn
    nach §4 Abs 3 EStG'). Die Konsolidierung muss das Top-Level-Label
    durchreichen, damit der Builder die Excel passend beschriften kann."""
    doc = {
        "type": "jahresabschluss", "year": 2024, "previous_year": 2023,
        "sign_convention": "expenses_positive",
        "endwert_label": "Steuerlicher Gewinn nach §4 Abs. 3 EStG",
        "groups": [
            _grp("A. 1. Einnahmen", "ertrag",
                 [_acc("8400", "Erlöse 19% USt", 181038.78, 496640.77)],
                 sub_of="A. BETRIEBSEINNAHMEN", gkv_section="umsatzerloese"),
        ],
        "open_questions": [],
    }
    r = merge_extractions([doc])
    assert r["endwert_label"] == "Steuerlicher Gewinn nach §4 Abs. 3 EStG"


def test_eur_hgb_default_endwert_label_none():
    """Bei klassischem HGB-JA ohne explicit endwert_label liefert
    consolidate.py None — der Builder fällt dann auf 'Jahresergebnis' /
    'Jahresüberschuss' zurück (Backwards-Kompat)."""
    doc = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse 19%", 1000000, 900000)]),
    ])
    r = merge_extractions([doc])
    assert r["endwert_label"] is None


def test_eur_hinzurechnungen_und_kuerzungen_als_eigene_gruppen():
    """EÜR-spezifische Korrektur-Gruppen ohne gkv_section: die Konsolidierung
    muss sie anhand von type + sub_group_of korrekt durchreichen."""
    doc = {
        "type": "jahresabschluss", "year": 2024, "previous_year": 2023,
        "sign_convention": "expenses_positive",
        "endwert_label": "Steuerlicher Gewinn nach §4 Abs. 3 EStG",
        "groups": [
            _grp("A. 1. Einnahmen", "ertrag",
                 [_acc("8400", "Erlöse", 372474.75, 551721.35)],
                 sub_of="A. BETRIEBSEINNAHMEN"),
            _grp("B. 1. Materialausgaben", "aufwand",
                 [_acc("1600", "Verbindlichkeiten L+L", 722.71, -722.71)],
                 sub_of="B. BETRIEBSAUSGABEN",
                 gkv_section="materialaufwand_rhb"),
            _grp("D. 1. Hinzurechnungen", "ertrag",
                 [_acc("4654", "Bewirtungskosten", 543.10, 374.24),
                  _acc("4320", "Gewerbesteuer", -6832.00, 11660.00)],
                 sub_of="D. STEUERLICHE KORREKTUREN"),
            _grp("D. Kürzungen", "aufwand",
                 [_acc("9971", "IAB §7g (1) EStG", 69483.50, 0.00)],
                 sub_of="D. STEUERLICHE KORREKTUREN"),
        ],
        "open_questions": [],
    }
    r = merge_extractions([doc])
    names = [g["name"] for g in r["groups"]]
    # alle Sub-Gruppen sind drin, Parent-Sektionen wurden synthetisch eingefügt
    assert "D. 1. Hinzurechnungen" in names
    assert "D. Kürzungen" in names
    assert "D. STEUERLICHE KORREKTUREN" in names  # synthetic parent
    # type-Klassifikation bleibt erhalten — kritisch für die JÜ-Formel
    hinzu = next(g for g in r["groups"] if g["name"] == "D. 1. Hinzurechnungen")
    kuerz = next(g for g in r["groups"] if g["name"] == "D. Kürzungen")
    assert hinzu["type"] == "ertrag"   # → wird im Builder addiert
    assert kuerz["type"] == "aufwand"  # → wird im Builder subtrahiert


# ---------------------------------------------------------------------------
# Susa (Summen- und Saldenliste) — wird wie BWA behandelt
# ---------------------------------------------------------------------------

def test_susa_creates_bwa_like_column():
    """Susa-Doc bekommt eine eigene Spalte (kind='bwa'), Konten landen wie
    bei einer BWA mit Einzelkonten. Gruppen-Routing per Kontonummer in
    bestehende JA-Gruppen, BWA-only-Gruppen werden angehaengt."""
    ja = _ja(2024, 2023, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse 19%", 1000000, 900000)]),
    ])
    susa = {
        "type": "susa", "year": 2025, "period_label": "Susa Dez 2025",
        "sign_convention": "expenses_positive",
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese", "sub_group_of": None,
             "accounts": [
                 {"konto_nr": "8400", "bezeichnung": "Erlöse 19% USt",
                  "betrag_gj": 491908.37, "confidence": "high"},
             ]},
            {"name": "Löhne und Gehälter", "type": "aufwand",
             "gkv_section": "personalaufwand_loehne", "sub_group_of": None,
             "accounts": [
                 {"konto_nr": "4110", "bezeichnung": "Löhne",
                  "betrag_gj": 556.00, "confidence": "high"},
             ]},
        ],
        "open_questions": [],
    }
    r = merge_extractions([ja, susa])
    # 3 Spalten: 2023 (ja), 2024 (ja), Susa Dez 2025 (bwa-kind)
    kinds = [c["kind"] for c in r["columns"]]
    assert kinds == ["ja", "ja", "bwa"]
    labels = [c["label"] for c in r["columns"]]
    assert labels[2] == "Susa Dez 2025"
    # 8400 hat in Susa-Spalte einen Wert
    susa_idx = 2
    erloese = next(g for g in r["groups"] if g["name"] == "Umsatzerlöse")
    konto_8400 = erloese["accounts"][0]
    assert konto_8400["values"][susa_idx] == 491908.37
    # Löhne-Gruppe wurde als neue BWA-only-Gruppe angehaengt
    loehne = next(g for g in r["groups"] if g["name"] == "Löhne und Gehälter")
    assert loehne["accounts"][0]["values"][susa_idx] == 556.00


def test_flat_old_ja_routes_into_sub_when_template_is_nested():
    """Tasteone-Bug-2 2026-05: Aeltere JAs liefern flache Hierarchie ('Aufwen-
    dungen für RHB' als Top-Level), juengstes JA liefert hierarchisch ('4. a)
    Aufwendungen für RHB' als Sub von '4. Materialaufwand'). Beim Template-
    Aufbau wird '4. Materialaufwand' als Top-Level synthetisch ergaenzt.

    Erwartung: Konten der flachen JAs landen in der realen Sub, NICHT im
    synthetischen Parent — sonst gibt es Doppelzaehlung wenn eine JA im
    Eigenjahr die Sub und im VJ ebenfalls die Sub bedient (Werte landen
    dann zusaetzlich auch im Parent via section-Match).
    """
    # 2020 (alt, flach): "Aufwendungen für RHB" als Top-Level
    doc_2020 = _ja(2020, 2019, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse", 5_000_000, 4_000_000)],
             gkv_section="umsatzerloese"),
        _grp("Aufwendungen für RHB", "aufwand",
             [_acc("5400", "Wareneingang", 3_000_000, 2_500_000)],
             gkv_section="materialaufwand_rhb"),
    ])
    # 2022 (neuestes, hierarchisch): "4. a) ..." als Sub von "4. Materialaufwand"
    doc_2022 = _ja(2022, 2021, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse", 5_500_000, 5_200_000)],
             gkv_section="umsatzerloese"),
        _grp("4. a) Aufwendungen für RHB", "aufwand",
             [_acc("5400", "Wareneingang", 3_300_000, 3_100_000)],
             gkv_section="materialaufwand_rhb",
             sub_of="4. Materialaufwand"),
    ])
    r = merge_extractions([doc_2020, doc_2022])

    # Konten landen in der realen Sub-Group, NICHT im synthetic Parent.
    # HGB-Renummerierung: "4. Materialaufwand" → "2." (nach "1. Umsatzerlöse").
    parent = next((g for g in r["groups"] if g["name"] == "2. Materialaufwand"), None)
    sub = next((g for g in r["groups"]
                 if g["name"] == "2. a) Aufwendungen für RHB"), None)
    assert parent is not None and sub is not None

    # Der synthetic Parent darf KEINE eigenen Konten haben
    assert len(parent.get("accounts", [])) == 0, \
        f"Parent '2. Materialaufwand' sollte synthetisch leer sein, hat aber " \
        f"{len(parent['accounts'])} Konten"

    # Sub hat alle Wareneingang-Werte aus beiden JAs (2020, 2021 als VJ aus 2022, 2022)
    waren = next((a for a in sub["accounts"] if a["konto_nr"] == "5400"), None)
    assert waren is not None, "Wareneingang-Konto sollte in Sub sein"
    cols = r["columns"]
    col_2020 = next(i for i, c in enumerate(cols) if c["year"] == 2020)
    col_2022 = next(i for i, c in enumerate(cols) if c["year"] == 2022)
    assert waren["values"][col_2020] == 3_000_000
    assert waren["values"][col_2022] == 3_300_000


def test_outlier_column_with_inverted_signs_is_normalized():
    """Tasteone-Bug 2026-05: Claude hat fuer 2022 die ganze Spalte vorzeichen-
    invertiert extrahiert (Aufwand negativ statt positiv, Skonti positiv statt
    negativ). Andere Jahre (2020, 2021, 2023, 2024) sind korrekt mit positivem
    Aufwand-Vorzeichen.

    Erwartung: Outlier-Spalte wird beim Konsolidieren erkannt (Mehrheits-Vote)
    und die Werte werden invertiert. Resultat: alle Spalten haben einheitlich
    positiv-konventionierten Aufwand. Die JÜ-Mathematik bleibt korrekt, weil
    der Builder die sign_convention aus den normalisierten Werten ableitet.
    """
    docs = []
    for year in (2020, 2021, 2023, 2024):
        docs.append(_ja(year, year - 1, [
            _grp("Umsatzerlöse", "ertrag",
                 [_acc("8400", "Erlöse", 5_000_000, 4_000_000)],
                 gkv_section="umsatzerloese"),
            _grp("Materialaufwand", "aufwand",
                 [_acc("5400", "Wareneingang", 3_000_000, 2_500_000),
                  _acc("5736", "Skonti", -50_000, -45_000)],
                 gkv_section="materialaufwand_rhb"),
        ]))
    # 2022: alle Vorzeichen invertiert (Wareneingang negativ, Skonti positiv)
    docs.append(_ja(2022, 2021, [
        _grp("Umsatzerlöse", "ertrag",
             [_acc("8400", "Erlöse", 5_500_000, 5_000_000)],
             gkv_section="umsatzerloese"),
        _grp("Materialaufwand", "aufwand",
             [_acc("5400", "Wareneingang", -3_200_000, -3_000_000),
              _acc("5736", "Skonti", 60_000, 50_000)],
             gkv_section="materialaufwand_rhb"),
    ]))
    r = merge_extractions(docs)

    # Spalte 2022 finden
    cols = r["columns"]
    col_2022 = next(i for i, c in enumerate(cols) if c["year"] == 2022)
    materialaufwand = next(g for g in r["groups"] if g["name"] == "2. Materialaufwand")
    waren = next(a for a in materialaufwand["accounts"] if a["konto_nr"] == "5400")
    skonti = next(a for a in materialaufwand["accounts"] if a["konto_nr"] == "5736")
    # Nach Normalisierung: 2022er Werte invertiert auf Mehrheits-Konvention
    assert waren["values"][col_2022] == 3_200_000, \
        f"Wareneingang 2022 sollte +3.2M sein, ist {waren['values'][col_2022]}"
    assert skonti["values"][col_2022] == -60_000, \
        f"Skonti 2022 sollte -60k sein, ist {skonti['values'][col_2022]}"


def test_no_normalization_when_majority_unclear():
    """Wenn nur 2 Spalten existieren und beide unterschiedlich konventioniert
    sind, gibt es keine Mehrheit → keine Inversion (Status quo)."""
    docs = [
        _ja(2024, 2023, [
            _grp("Materialaufwand", "aufwand",
                 [_acc("5400", "Wareneingang", 1_000_000, None)],
                 gkv_section="materialaufwand_rhb"),
        ]),
        _ja(2025, 2024, [
            _grp("Materialaufwand", "aufwand",
                 [_acc("5400", "Wareneingang", -1_100_000, None)],
                 gkv_section="materialaufwand_rhb"),
        ]),
    ]
    r = merge_extractions(docs)
    cols = r["columns"]
    col_2024 = next(i for i, c in enumerate(cols) if c["year"] == 2024)
    col_2025 = next(i for i, c in enumerate(cols) if c["year"] == 2025)
    materialaufwand = next(g for g in r["groups"] if g["name"] == "1. Materialaufwand")
    waren = next(a for a in materialaufwand["accounts"] if a["konto_nr"] == "5400")
    # Werte unverändert (keine Mehrheit)
    assert waren["values"][col_2024] == 1_000_000
    assert waren["values"][col_2025] == -1_100_000


def test_susa_default_label_when_period_label_missing():
    """Wenn Claude period_label vergisst, fällt das Spalten-Label auf
    'Susa {year}' zurück (NICHT 'BWA {year}' — das wäre verwirrend)."""
    susa = {
        "type": "susa", "year": 2025, "sign_convention": "expenses_positive",
        "groups": [
            {"name": "Umsatzerlöse", "type": "ertrag",
             "gkv_section": "umsatzerloese", "sub_group_of": None,
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Erlöse",
                            "betrag_gj": 100.0, "confidence": "high"}]},
        ],
        "open_questions": [],
    }
    r = merge_extractions([susa])
    assert r["columns"][0]["label"] == "Susa 2025"


# --- Issue 2: HGB-Positions-Renummerierung bei Multi-Jahr-Merge -------------

def test_hgb_multiyear_renumber_no_duplicate_positions():
    """Prisma-Bug 06/2026: 2024 nummeriert Steuern=9 (Zinsaufwand=0, fehlt),
    2022 nummeriert Zinsaufwand=9/Steuern=10. Naiver Merge → zwei "9." + Zins-
    aufwand am Ende. Erwartet: GKV-Reihenfolge, durchgehend neu nummeriert,
    Zinsaufwand VOR Steuern, keine Dubletten."""
    ja2024 = _ja(2024, 2023, [
        _grp("1. Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 1000, 900)],
             gkv_section="umsatzerloese"),
        _grp("8. sonstige Zinsen und ähnliche Erträge", "ertrag",
             [_acc("2650", "Zinsertrag", 10, 5)], gkv_section="sonstige_zins_ertraege"),
        _grp("9. Steuern vom Einkommen und vom Ertrag", "steuer",
             [_acc("2200", "KSt", 200, 180)], gkv_section="ee_steuern"),
        _grp("11. sonstige Steuern", "steuer",
             [_acc("4510", "Kfz-Steuer", 5, 4)], gkv_section="sonst_steuern"),
    ])
    ja2022 = _ja(2022, 2021, [
        _grp("1. Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 800, 700)],
             gkv_section="umsatzerloese"),
        _grp("8. sonstige Zinsen und ähnliche Erträge", "ertrag",
             [_acc("2650", "Zinsertrag", 3, 2)], gkv_section="sonstige_zins_ertraege"),
        _grp("9. Zinsen und ähnliche Aufwendungen", "aufwand",
             [_acc("2120", "Zinsaufwand", 7, 6)], gkv_section="zinsaufwand"),
        _grp("10. Steuern vom Einkommen und vom Ertrag", "steuer",
             [_acc("2200", "KSt", 70, 60)], gkv_section="ee_steuern"),
    ])
    r = merge_extractions([ja2024, ja2022])
    names = [g["name"] for g in r["groups"]]
    assert names == [
        "1. Umsatzerlöse",
        "2. sonstige Zinsen und ähnliche Erträge",
        "3. Zinsen und ähnliche Aufwendungen",
        "4. Steuern vom Einkommen und vom Ertrag",
        "5. sonstige Steuern",
    ], names
    # keine doppelten Nummern
    nums = [n.split(".")[0] for n in names]
    assert len(nums) == len(set(nums))


def test_hgb_renumber_keeps_subgroup_lettering_and_pointers():
    """Sub-Gruppen (a/b) behalten Buchstaben, sub_group_of zeigt auf den
    neuen Parent-Namen."""
    ja = _ja(2024, 2023, [
        _grp("1. Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 1000, 900)],
             gkv_section="umsatzerloese"),
        _grp("4. Materialaufwand", "aufwand", [], gkv_section="materialaufwand_rhb"),
        _grp("4. a) Aufwendungen für RHB", "aufwand",
             [_acc("5100", "Wareneinkauf", 300, 280)],
             sub_of="4. Materialaufwand", gkv_section="materialaufwand_rhb"),
        _grp("4. b) Aufwendungen für bezogene Leistungen", "aufwand",
             [_acc("5900", "Fremdleistungen", 100, 90)],
             sub_of="4. Materialaufwand", gkv_section="materialaufwand_bez_leistungen"),
    ])
    r = merge_extractions([ja])
    by_name = {g["name"]: g for g in r["groups"]}
    assert "2. Materialaufwand" in by_name
    subs = [g for g in r["groups"] if g.get("sub_group_of") == "2. Materialaufwand"]
    assert [s["name"] for s in subs] == [
        "2. a) Aufwendungen für RHB",
        "2. b) Aufwendungen für bezogene Leistungen",
    ]


def test_euer_structure_is_not_renumbered():
    """EÜR (A./B./D.-Struktur, Endwert 'Steuerlicher Gewinn') darf NICHT in
    1..N umnummeriert werden — die Hauptsektionen-Buchstaben bleiben."""
    euer = {
        "type": "jahresabschluss", "year": 2024, "previous_year": 2023,
        "sign_convention": "expenses_positive",
        "endwert_label": "Steuerlicher Gewinn nach §4 Abs. 3 EStG",
        "pdf_jahresueberschuss_gj": 100.0, "pdf_jahresueberschuss_vj": 90.0,
        "groups": [
            {"name": "A. 1. Einnahmen", "type": "ertrag", "gkv_section": "umsatzerloese",
             "sub_group_of": "A. BETRIEBSEINNAHMEN",
             "accounts": [{"konto_nr": "8400", "bezeichnung": "Erlöse",
                            "betrag_gj": 100.0, "betrag_vj": 90.0, "confidence": "high"}]},
        ],
        "open_questions": [],
    }
    r = merge_extractions([euer])
    names = [g["name"] for g in r["groups"]]
    assert any(n.startswith("A.") for n in names), names
    assert not any(n.startswith("1. ") for n in names), names


def test_hgb_renumber_keeps_steuern_before_bwa_block():
    """Ordering-Regression (Prisma 06/2026): spät angehängte GuV-Position
    (Zinsaufwand aus älterem JA) darf nachfolgende BWA-Aggregat-Gruppen NICHT
    vor die Steuern ziehen. Erwartet: alle GuV-§275-Positionen (inkl. Steuern)
    zuerst in GKV-Reihenfolge, BWA-Gruppen danach."""
    ja2024 = _ja(2024, 2023, [
        _grp("1. Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 1000, 900)],
             gkv_section="umsatzerloese"),
        _grp("9. Steuern vom Einkommen und vom Ertrag", "steuer",
             [_acc("2200", "KSt", 200, 180)], gkv_section="ee_steuern"),
        _grp("11. sonstige Steuern", "steuer",
             [_acc("4510", "Kfz-Steuer", 5, 4)], gkv_section="sonst_steuern"),
    ])
    ja2022 = _ja(2022, 2021, [
        _grp("1. Umsatzerlöse", "ertrag", [_acc("8400", "Erlöse", 800, 700)],
             gkv_section="umsatzerloese"),
        _grp("9. Zinsen und ähnliche Aufwendungen", "aufwand",
             [_acc("2120", "Zinsaufwand", 7, 6)], gkv_section="zinsaufwand"),
    ])
    bwa = {
        "type": "bwa", "year": 2025, "period_label": "BWA 2025",
        "sign_convention": "expenses_positive",
        "groups": [
            {"name": "Gesamtleistung", "type": "ertrag", "gkv_section": "neutral",
             "sub_group_of": None, "pdf_sum_gj": 50000.0, "accounts": []},
        ],
        "open_questions": [],
    }
    r = merge_extractions([ja2024, ja2022, bwa])
    names = [g["name"] for g in r["groups"]]
    # GuV-Positionen sequentiell, Zinsaufwand vor Steuern
    assert names[:4] == [
        "1. Umsatzerlöse",
        "2. Zinsen und ähnliche Aufwendungen",
        "3. Steuern vom Einkommen und vom Ertrag",
        "4. sonstige Steuern",
    ], names
    # BWA-Gruppe NACH allen GuV-Positionen
    assert names.index("Gesamtleistung") > names.index("4. sonstige Steuern")
    # keine doppelten Nummern
    nums = [n.split(".")[0] for n in names if n[:1].isdigit()]
    assert len(nums) == len(set(nums))
