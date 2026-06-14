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
    assert neg["rohertragsmarge_umsatz"] == 0.7
    assert neg["betriebsergebnis_marge_umsatz"] == 0.5
    assert neg["metrics_version"] == 2


def test_metrics_nutzt_pdf_jue_anker():
    cons = _cons("neg")
    cons["pdf_jue_per_column"] = {0: 480.0}
    m = compute_company_metrics(cons, 0)
    assert m["jue"] == 480.0       # Anker statt computed 500
    assert m["jue_marge_umsatz"] == 0.48


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
    assert m["betriebsergebnis_marge_umsatz"] is None       # Umsatz 0
    assert m["betriebsergebnis_marge_gesamtleistung"] is not None  # Gesamtleistung-Basis da
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


def test_metrics_v2_ebitda_und_analytisch():
    """v2: EBITDA = Betriebsergebnis + Abschreibungen; ebit_analytisch =
    JÜ + Steuern + Zinsaufwand (Banker-EBIT)."""
    cons = {"columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
            "groups": [
                {"name": "1. Umsatz", "gkv_section": "umsatzerloese", "type": "ertrag",
                 "column_sums": {0: 1000.0}, "accounts": [{"konto_nr": "8400", "values": {0: 1000.0}}]},
                {"name": "7. Abschreibungen", "gkv_section": "abschreibungen", "type": "aufwand",
                 "column_sums": {0: -100.0}, "accounts": [{"konto_nr": "4830", "values": {0: -100.0}}]},
                {"name": "13. Zinsen", "gkv_section": "zinsaufwand", "type": "aufwand",
                 "column_sums": {0: -50.0}, "accounts": [{"konto_nr": "7300", "values": {0: -50.0}}]},
                {"name": "18. Steuern", "gkv_section": "ee_steuern", "type": "steuer",
                 "column_sums": {0: -30.0}, "accounts": [{"konto_nr": "7600", "values": {0: -30.0}}]},
            ]}
    m = compute_company_metrics(cons, 0)
    # Betriebsergebnis = 1000 - 100 = 900 ; EBITDA = 900 + 100 = 1000
    assert m["betriebsergebnis"] == 900.0
    assert m["ebitda"] == 1000.0
    # JÜ computed = 900 (EBIT) - 50 (Finanz) - 30 (Steuer) = 820
    assert m["jue"] == 820.0
    # ebit_analytisch = 820 + 30 + 50 = 900
    assert m["ebit_analytisch"] == 900.0
    assert m["zinsdeckung"] == round(900.0 / 50.0, 4)     # 18.0
    assert m["materialquote_umsatz"] == 0.0
    assert m["verfahren"] is None                          # keine GKV-Marker
    assert m["metrics_version"] == 2


def test_metrics_v2_verfahren_gkv_und_aktivierungsquote():
    """v2: GKV-Marker (Bestandsv./Material) → verfahren='gkv'; aktivierungsquote."""
    cons = {"columns": [{"label": "2024", "kind": "ja", "doc_type": "ja", "year": 2024}],
            "groups": [
                {"name": "1. Umsatz", "gkv_section": "umsatzerloese", "type": "ertrag",
                 "column_sums": {0: 800.0}, "accounts": [{"konto_nr": "8400", "values": {0: 800.0}}]},
                {"name": "2. Bestandserhöhung", "gkv_section": "bestandsveraenderung", "type": "ertrag",
                 "column_sums": {0: 200.0}, "accounts": [{"konto_nr": "8990", "values": {0: 200.0}}]},
                {"name": "5. Material", "gkv_section": "materialaufwand_rhb", "type": "aufwand",
                 "column_sums": {0: -300.0}, "accounts": [{"konto_nr": "5100", "values": {0: -300.0}}]},
            ]}
    m = compute_company_metrics(cons, 0)
    assert m["verfahren"] == "gkv"
    assert m["gesamtleistung"] == 1000.0
    assert m["aktivierungsquote"] == 0.2                   # 200 / 1000
    assert m["materialquote_umsatz"] == round(300.0 / 800.0, 4)
    assert m["materialquote_gesamtleistung"] == 0.3


def test_metrics_bestandsveraenderung_verzerrt_completeness_nicht():
    """Real-Befund (Prisma): Bestandsveränderung ist vorzeichen-normalisiert
    (gedruckt +X, Detail −X). Das darf den completeness_score NICHT drücken
    (sonst Fake-Lücke 2×|X|). Ausgeschlossen wie beim Builder-Restposten."""
    cons = {"columns": [{"label": "2023", "kind": "ja", "doc_type": "ja", "year": 2023}],
            "groups": [
                {"name": "1. Umsatz", "gkv_section": "umsatzerloese", "type": "ertrag",
                 "column_sums": {0: 1000.0}, "accounts": [{"konto_nr": "8400", "values": {0: 1000.0}}]},
                {"name": "2. Verminderung des Bestandes", "gkv_section": "bestandsveraenderung",
                 "type": "ertrag", "column_sums": {0: 1641810.23},   # roher Anker positiv
                 "accounts": [{"konto_nr": "", "values": {0: -1641810.23}}]},  # normalisiert negativ
            ]}
    m = compute_company_metrics(cons, 0)
    assert m["restposten_anteil"] == 0.0      # Umsatz voll, Bestandsv. nicht gezählt
    assert m["completeness_score"] == 1.0
