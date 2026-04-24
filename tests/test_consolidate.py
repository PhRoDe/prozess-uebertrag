from app.worker.consolidate import merge_extractions


def _acc(konto, bez, gruppe, gj, vj, conf="high"):
    return {"konto_nr": konto, "bezeichnung": bez, "gruppe": gruppe,
            "betrag_gj": gj, "betrag_vj": vj, "confidence": conf}


def test_merge_two_years_union_of_accounts():
    extractions = [
        {"type": "jahresabschluss", "year": 2023, "previous_year": 2022,
         "accounts": [_acc("8400", "Erloese", "1. Umsatzerlöse", 1000000, 900000)]},
        {"type": "jahresabschluss", "year": 2024, "previous_year": 2023,
         "accounts": [
             _acc("8400", "Erloese", "1. Umsatzerlöse", 1100000, 1000000),
             _acc("8401", "Erloese Korrektur", "1. Umsatzerlöse", 500, 0),
         ]},
    ]
    result = merge_extractions(extractions)
    assert result["years"] == [2022, 2023, 2024]
    rows_by_konto = {r["konto_nr"]: r for r in result["rows"]}
    assert rows_by_konto["8400"]["values"] == {2022: 900000, 2023: 1000000, 2024: 1100000}
    assert rows_by_konto["8401"]["values"] == {2023: 0, 2024: 500}


def test_merge_detects_previous_year_mismatch():
    extractions = [
        {"type": "jahresabschluss", "year": 2023, "previous_year": 2022,
         "accounts": [_acc("8400", "E", "1. Umsatzerlöse", 1000000, 900000)]},
        {"type": "jahresabschluss", "year": 2024, "previous_year": 2023,
         "accounts": [_acc("8400", "E", "1. Umsatzerlöse", 1100000, 999999)]},
    ]
    result = merge_extractions(extractions)
    mismatches = [q for q in result["questions"] if q["type"] == "previous_year_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["konto_nr"] == "8400"


def test_merge_ignores_bwa_type():
    extractions = [
        {"type": "bwa", "zeitraum": "01-12/2024", "positionen": []},
        {"type": "jahresabschluss", "year": 2024, "previous_year": 2023,
         "accounts": [_acc("8400", "E", "1. Umsatzerlöse", 1000, 900)]},
    ]
    result = merge_extractions(extractions)
    assert result["years"] == [2023, 2024]
    assert len(result["rows"]) == 1


def test_merge_single_year_no_questions():
    extractions = [
        {"type": "jahresabschluss", "year": 2024, "previous_year": 2023,
         "accounts": [_acc("8400", "E", "1. Umsatzerlöse", 1000, 900)]},
    ]
    result = merge_extractions(extractions)
    assert result["questions"] == []
