"""Phase 3b: reine Form-Parsing-Logik des Finalize-Endpunkts.

parse_finalize_form baut aus den Form-Items das review-Dict: bestehende
review[KONTO]→Gruppe-Zuordnungen PLUS manuell nachgetragene Konten aus dem
Vollständigkeits-Panel (_manual_accounts), aber nur für Lücken mit Aktion
'correct' und gültigem Betrag.
"""
from app.routes.job import parse_finalize_form


def test_parse_finalize_form_manuelles_konto():
    items = [
        ("review[8400]", "1. Umsatzerlöse"),
        ("gap_action[0]", "correct"),
        ("gap_group[0]", "8. Abschreibungen"),
        ("gap_col[0]", "0"),
        ("gap_bez[0]", "AfA GWG"),
        ("gap_betrag[0]", "100.00"),
        ("gap_uid[0]", "0"),
        ("gap_action[1]", "accept"),     # accept → kein manuelles Konto
        ("gap_group[1]", "X"),
        ("gap_betrag[1]", "5"),
    ]
    review = parse_finalize_form(items)
    assert review["8400"] == "1. Umsatzerlöse"
    manual = review["_manual_accounts"]
    assert len(manual) == 1
    assert manual[0]["group"] == "8. Abschreibungen"
    assert manual[0]["col_idx"] == 0
    assert manual[0]["bezeichnung"] == "AfA GWG"
    assert manual[0]["betrag"] == 100.0
    assert manual[0]["gap_index"] == 0  # eindeutige Lücken-Position


def test_parse_finalize_form_keine_manual_ohne_correct():
    review = parse_finalize_form([("review[1]", "G")])
    assert review == {"1": "G"}
    assert "_manual_accounts" not in review


def test_parse_finalize_form_betrag_mit_komma():
    items = [("gap_action[0]", "correct"), ("gap_group[0]", "X"),
             ("gap_col[0]", "0"), ("gap_betrag[0]", "1234,56")]
    review = parse_finalize_form(items)
    assert review["_manual_accounts"][0]["betrag"] == 1234.56


def test_parse_finalize_form_correct_ohne_gruppe_oder_betrag_uebersprungen():
    items = [
        ("gap_action[0]", "correct"), ("gap_group[0]", ""), ("gap_col[0]", "0"),
        ("gap_betrag[0]", "10"),
        ("gap_action[1]", "correct"), ("gap_group[1]", "Y"), ("gap_col[1]", "0"),
        ("gap_betrag[1]", ""),
    ]
    review = parse_finalize_form(items)
    assert "_manual_accounts" not in review  # beide unvollständig


def test_parse_betrag_deutsches_tausender_format():
    """Code-Review R5: deutsches Format mit Tausenderpunkt darf nicht still als
    None gedroppt werden."""
    from app.routes.job import _parse_betrag
    assert _parse_betrag("1.234,56") == 1234.56
    assert _parse_betrag("1234,56") == 1234.56
    assert _parse_betrag("1234.56") == 1234.56
    assert _parse_betrag("1.000.000,00") == 1000000.00
    assert _parse_betrag("") is None
    assert _parse_betrag("abc") is None
