from app.worker.consolidate import merge_extractions


def _ja(year, prev_year, groups, sign="expenses_negative"):
    return {"type": "jahresabschluss", "year": year, "previous_year": prev_year,
            "sign_convention": sign, "groups": groups, "open_questions": []}


def _acc(nr, bez, gj, vj, conf="high"):
    return {"konto_nr": nr, "bezeichnung": bez,
            "betrag_gj": gj, "betrag_vj": vj, "confidence": conf}


def _grp(name, typ, accounts, pdf_sum_gj=None, pdf_sum_vj=None, sub_of=None):
    g = {"name": name, "type": typ, "accounts": accounts, "sub_group_of": sub_of}
    if pdf_sum_gj is not None: g["pdf_sum_gj"] = pdf_sum_gj
    if pdf_sum_vj is not None: g["pdf_sum_vj"] = pdf_sum_vj
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
