"""Phase B: kanonische Benchmarking-Kennzahlen (rein)."""
from app.metrics import compute_company_metrics


def _cons(sign):
    m = -1.0 if sign == "neg" else 1.0
    return {
        "columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
        "groups": [
            {"name": "1. Umsatzerlöse", "gkv_section": "umsatzerloese", "type": "ertrag",
             "column_sums": {0: 1000.0}, "accounts": [{"konto_nr": "8400", "values": {0: 1000.0}}]},
            {"name": "5. Materialaufwand", "gkv_section": "materialaufwand_rhb", "type": "aufwand",
             "column_sums": {0: 300.0 * m}, "accounts": [{"konto_nr": "5100", "values": {0: 300.0 * m}}]},
            {"name": "6. Personalaufwand", "gkv_section": "personalaufwand_loehne", "type": "aufwand",
             "column_sums": {0: 200.0 * m}, "accounts": [{"konto_nr": "6000", "values": {0: 200.0 * m}}]},
        ],
    }


def test_metrics_vorzeichen_konventionen_identisch():
    """Beide Konventionen (Aufwand negativ ODER positiv) → gleiche kanonische
    Magnituden (Council #1: Vorzeichen aus geteilter Quelle, kein Bug)."""
    neg = compute_company_metrics(_cons("neg"), 0)
    pos = compute_company_metrics(_cons("pos"), 0)
    for k in ("umsatz", "materialaufwand", "personalaufwand", "gesamtleistung",
              "rohertrag", "betriebsergebnis"):
        assert neg[k] == pos[k], (k, neg[k], pos[k])
    assert neg["umsatz"] == 1000.0
    assert neg["materialaufwand"] == 300.0     # positive Kosten-Magnitude
    assert neg["personalaufwand"] == 200.0
    assert neg["rohertrag"] == 700.0           # 1000 - 300
    assert neg["betriebsergebnis"] == 500.0    # 700 - 200
    assert neg["personalaufwandsquote"] == 0.2
    assert neg["rohertragsmarge"] == 0.7
    assert neg["ebit_marge"] == 0.5
    assert neg["metrics_version"] == 1


def test_metrics_nutzt_pdf_jue_anker():
    cons = _cons("neg")
    cons["pdf_jue_per_column"] = {0: 480.0}
    m = compute_company_metrics(cons, 0)
    assert m["jue"] == 480.0       # Anker statt computed 500
    assert m["jue_marge"] == 0.48


def test_metrics_restposten_anteil_und_completeness():
    cons = {"columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
            "groups": [{"name": "U", "gkv_section": "umsatzerloese", "type": "ertrag",
                        "column_sums": {0: 1000.0},
                        "accounts": [{"konto_nr": "8400", "values": {0: 900.0}}]}]}
    m = compute_company_metrics(cons, 0)
    assert m["restposten_anteil"] == 0.1      # gap 100 / printed 1000
    assert m["completeness_score"] == 0.9


def test_metrics_none_wenn_leer():
    assert compute_company_metrics(
        {"columns": [{"label": "x", "kind": "ja", "year": 2024}], "groups": []}, 0) is None


def test_metrics_quote_none_bei_nenner_null():
    cons = {"columns": [{"label": "2024", "kind": "ja", "year": 2024}],
            "groups": [{"name": "P", "gkv_section": "personalaufwand_loehne", "type": "aufwand",
                        "column_sums": {0: -50.0}, "accounts": [{"konto_nr": "6000", "values": {0: -50.0}}]}]}
    m = compute_company_metrics(cons, 0)
    assert m["personalaufwand"] == 50.0
    assert m["personalaufwandsquote"] is None   # Gesamtleistung 0


def test_metrics_neutrales_ergebnis_rekonsiliert_zur_pdf_jue():
    """Residuum macht das Modell konsistent zur authoritativen PDF-JÜ:
    jue = betriebsergebnis + finanzergebnis + neutrales_ergebnis − steuern."""
    cons = _cons("neg")
    cons["pdf_jue_per_column"] = {0: 1500.0}   # weit über EBIT 500 → großer neutraler Posten
    m = compute_company_metrics(cons, 0)
    lhs = m["jue"]
    rhs = round(m["betriebsergebnis"] + m["finanzergebnis"] + m["neutrales_ergebnis"] - m["steuern"], 2)
    assert lhs == rhs == 1500.0
    assert m["neutrales_ergebnis"] == 1000.0    # 1500 - 500 (EBIT)


def test_metrics_neutrales_ergebnis_null_ohne_anker():
    m = compute_company_metrics(_cons("neg"), 0)   # kein pdf_jue → computed
    assert m["neutrales_ergebnis"] == 0.0


def test_metrics_bwa_spalte_gibt_none():
    """JA-only Guard: BWA/Susa-Spalte → None (andere Endwert-Semantik)."""
    cons = {"columns": [{"label": "BWA 2025", "kind": "bwa", "doc_type": "bwa", "year": 2025}],
            "groups": [{"name": "U", "gkv_section": "umsatzerloese", "type": "ertrag",
                        "column_sums": {0: 500.0}, "accounts": [{"konto_nr": "8", "values": {0: 500.0}}]}]}
    assert compute_company_metrics(cons, 0) is None


def test_metrics_marge_auf_gesamtleistung_bei_umsatz_null():
    """Umsatz≈0 aber echte Gesamtleistung (Bestandsveränderung) → Margen NICHT
    None (Basis Gesamtleistung)."""
    cons = {"columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
            "groups": [
                {"name": "2. Bestandserhöhung", "gkv_section": "bestandsveraenderung", "type": "ertrag",
                 "column_sums": {0: 1000.0}, "accounts": [{"konto_nr": "8990", "values": {0: 1000.0}}]},
                {"name": "6. Personal", "gkv_section": "personalaufwand_loehne", "type": "aufwand",
                 "column_sums": {0: -200.0}, "accounts": [{"konto_nr": "6000", "values": {0: -200.0}}]},
            ]}
    m = compute_company_metrics(cons, 0)
    assert m["umsatz"] == 0.0
    assert m["gesamtleistung"] == 1000.0
    assert m["ebit_marge"] is not None        # vorher None (Basis Umsatz) → jetzt da
    assert m["personalaufwandsquote"] == 0.2


def test_metrics_restposten_verschachtelt():
    """Parent trägt gedruckte Summe, Konten in Kindern, unvollständig → Lücke
    wird erkannt (vorher fälschlich completeness 1.0)."""
    cons = {"columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
            "groups": [
                {"name": "4. Materialaufwand", "gkv_section": "materialaufwand_rhb", "type": "aufwand",
                 "column_sums": {0: -1000.0}, "accounts": []},
                {"name": "4a) RHB", "sub_group_of": "4. Materialaufwand", "type": "aufwand",
                 "column_sums": {}, "accounts": [{"konto_nr": "5100", "values": {0: -900.0}}]},
            ]}
    m = compute_company_metrics(cons, 0)
    assert m["restposten_anteil"] == 0.1      # |−1000 − (−900)| / 1000
    assert m["completeness_score"] == 0.9
