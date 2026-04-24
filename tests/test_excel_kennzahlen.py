from app.excel.kennzahlen import build_kennzahlen_rows


def test_kennzahlen_full_anchors_produce_formulas():
    anchors = {"umsatz_row": 5, "material_row": 12, "personal_row": 18,
               "jue_row": 35, "ebitda_row": 40}
    rows = build_kennzahlen_rows(anchors, col_idx=2)
    labels = [r["label"] for r in rows]
    assert "Materialquote" in labels
    assert "Umsatzrendite" in labels
    assert "EBITDA-Marge" in labels
    for r in rows:
        assert r["formula"].startswith("="), f"{r['label']}: {r['formula']}"
        assert r["number_format"] == "0.0%"


def test_kennzahlen_missing_anchor_yields_empty_formula():
    # No EBITDA — row should still exist but formula empty
    anchors = {"umsatz_row": 5, "material_row": 12, "personal_row": 18,
               "jue_row": 35, "ebitda_row": None}
    rows = build_kennzahlen_rows(anchors, col_idx=2)
    by_label = {r["label"]: r for r in rows}
    assert by_label["EBITDA-Marge"]["formula"] == ""
    # Others still have formulas
    assert by_label["Materialquote"]["formula"].startswith("=")


def test_kennzahlen_rohertrag_uses_one_minus_material():
    anchors = {"umsatz_row": 5, "material_row": 12, "personal_row": 18,
               "jue_row": 35, "ebitda_row": 40}
    rows = build_kennzahlen_rows(anchors, col_idx=2)
    by_label = {r["label"]: r for r in rows}
    assert "1-" in by_label["Rohertragsquote"]["formula"]
