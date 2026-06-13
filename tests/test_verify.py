"""Phase 1a: Vollständigkeits-Check pro Dokument.

document_completeness vergleicht je Gruppe die Summe der extrahierten Konten
(acc_sum) mit der im PDF gedruckten Gruppensumme (pdf_sum_gj/_vj). Ein Diff > 1 ct
bedeutet: Konten fehlen oder wurden falsch gelesen — genau die Lücke, die der
Restposten-Mechanismus im Excel heute still füllt.
"""
from app.worker.verify import document_completeness, heal_extraction


def _doc(groups, doc_type="jahresabschluss", year=2024, prev=2023):
    return {"type": doc_type, "year": year, "previous_year": prev, "groups": groups}


def _grp(name, accounts, pdf_sum_gj=None, pdf_sum_vj=None, gkv_section=None):
    g = {"name": name, "accounts": accounts}
    if pdf_sum_gj is not None:
        g["pdf_sum_gj"] = pdf_sum_gj
    if pdf_sum_vj is not None:
        g["pdf_sum_vj"] = pdf_sum_vj
    if gkv_section is not None:
        g["gkv_section"] = gkv_section
    return g


def _acc(gj=None, vj=None, nr=None, bez=""):
    return {"konto_nr": nr, "bezeichnung": bez, "betrag_gj": gj, "betrag_vj": vj}


def test_detects_gap_when_accounts_less_than_printed_sum():
    doc = _doc([
        _grp("7. sonstige betr. Aufwendungen",
             [_acc(gj=100.0), _acc(gj=50.0)],   # acc_sum = 150
             pdf_sum_gj=200.0),                  # gedruckt 200 → Lücke 50
    ])
    gaps = document_completeness(doc)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["group"] == "7. sonstige betr. Aufwendungen"
    assert g["period"] == "gj"
    assert g["year"] == 2024
    assert g["printed_sum"] == 200.0
    assert g["acc_sum"] == 150.0
    assert abs(g["diff"] - 50.0) < 0.01


def test_no_gap_when_accounts_match_printed_sum():
    doc = _doc([
        _grp("1. Umsatzerlöse", [_acc(gj=100.0), _acc(gj=100.0)], pdf_sum_gj=200.0),
    ])
    assert document_completeness(doc) == []


def test_vj_gap_detected_independently():
    doc = _doc([
        _grp("7. sonstige betr. Aufwendungen",
             [_acc(gj=200.0, vj=80.0)],
             pdf_sum_gj=200.0,    # gj matcht
             pdf_sum_vj=150.0),   # vj: 150 gedruckt, nur 80 erfasst → Lücke 70
    ])
    gaps = document_completeness(doc)
    assert len(gaps) == 1
    assert gaps[0]["period"] == "vj"
    assert gaps[0]["year"] == 2023
    assert abs(gaps[0]["diff"] - 70.0) < 0.01


def test_bestandsveraenderung_is_excluded():
    """Bestandsveränderung: Detail wird normalisiert, Anker bleibt roh — ein
    Diff ist hier erwartbar und KEINE echte Lücke (wie _inject_restposten_accounts)."""
    doc = _doc([
        _grp("2. Verminderung des Bestandes", [_acc(gj=-500.0)],
             pdf_sum_gj=500.0, gkv_section="bestandsveraenderung"),
    ])
    assert document_completeness(doc) == []


def test_summary_only_position_is_reported_as_gap():
    """Gruppe mit gedruckter Summe aber NULL Konten (Bilanzbericht-Format ODER
    von Claude gedroppt) → als Lücke melden; der User/Heal entscheidet."""
    doc = _doc([
        _grp("9. Steuern vom Einkommen", [], pdf_sum_gj=226572.84),
    ])
    gaps = document_completeness(doc)
    assert len(gaps) == 1
    assert gaps[0]["acc_sum"] == 0.0
    assert abs(gaps[0]["diff"] - 226572.84) < 0.01


def test_no_anchor_no_gap():
    """Ohne gedruckte Gruppensumme kann nichts geprüft werden → keine Lücke."""
    doc = _doc([
        _grp("Sonstiges", [_acc(gj=10.0)]),  # kein pdf_sum
    ])
    assert document_completeness(doc) == []


# --- Phase 1b: Selbstheilung (reine Orchestrierung, reextract_fn injiziert) ---

def test_heal_fills_gap_via_reextract():
    """Lücke in einer Gruppe → reextract_fn liefert die fehlenden Konten →
    nach dem Heilen ist die Lücke geschlossen."""
    doc = _doc([
        _grp("7. sonstige betr. Aufwendungen", [_acc(gj=150.0)], pdf_sum_gj=200.0),
    ])

    def fake_reextract(extraction, gaps):
        assert gaps and gaps[0]["group"] == "7. sonstige betr. Aufwendungen"
        # Claude liefert die VOLLE Kontenliste der Position zurück
        return {"7. sonstige betr. Aufwendungen": [_acc(gj=150.0), _acc(gj=50.0)]}

    healed, remaining = heal_extraction(doc, fake_reextract, max_rounds=2)
    assert remaining == []
    accs = healed["groups"][0]["accounts"]
    assert len(accs) == 2
    assert sum(a["betrag_gj"] for a in accs) == 200.0


def test_heal_noop_when_no_gaps():
    """Keine Lücke → reextract_fn wird NIE aufgerufen, Extraktion unverändert."""
    doc = _doc([_grp("1. Umsatzerlöse", [_acc(gj=200.0)], pdf_sum_gj=200.0)])
    calls = []

    def fake_reextract(extraction, gaps):
        calls.append(gaps)
        return {}

    healed, remaining = heal_extraction(doc, fake_reextract)
    assert remaining == []
    assert calls == []  # nie aufgerufen


def test_heal_stops_after_max_rounds_when_unresolved():
    """reextract_fn schließt die Lücke nicht → Abbruch nach max_rounds,
    Rest-Lücke wird zurückgegeben (keine Endlosschleife)."""
    doc = _doc([_grp("7. sonstige", [_acc(gj=150.0)], pdf_sum_gj=200.0)])
    calls = []

    def stubborn_reextract(extraction, gaps):
        calls.append(1)
        return {"7. sonstige": [_acc(gj=150.0)]}  # selbe unvollständige Liste

    healed, remaining = heal_extraction(doc, stubborn_reextract, max_rounds=2)
    assert len(remaining) == 1            # Lücke bleibt
    assert len(calls) == 1                # kein Fortschritt → sofortiger Abbruch


# --- Codex-Review-Findings (P2): Parent-Hierarchie + Heal nur bei Verbesserung ---

def test_parent_with_child_accounts_is_not_false_gap():
    """P2-1: '4. Materialaufwand' (gedruckte Summe am Parent, Konten in den
    Sub-Gruppen 4a/4b) darf NICHT als unvollständig gemeldet werden — acc_sum
    muss die Konten der Sub-Gruppen einrechnen (wie der Builder)."""
    doc = _doc([
        {"name": "4. Materialaufwand", "accounts": [], "pdf_sum_gj": 300.0,
         "gkv_section": "materialaufwand_rhb", "sub_group_of": None},
        {"name": "4. a) RHB", "accounts": [_acc(gj=200.0, nr="5100")],
         "pdf_sum_gj": 200.0, "sub_group_of": "4. Materialaufwand"},
        {"name": "4. b) Bezogene", "accounts": [_acc(gj=100.0, nr="5900")],
         "pdf_sum_gj": 100.0, "sub_group_of": "4. Materialaufwand"},
    ])
    gaps = document_completeness(doc)
    assert not any(g["group"] == "4. Materialaufwand" for g in gaps), \
        f"Parent fälschlich als Lücke gemeldet: {gaps}"


def test_heal_rejects_non_improving_reextract():
    """P2-2: liefert die Re-Extraktion eine andere, aber NICHT bessere (oder
    schlechtere) Kontenliste, bleibt die Original-Extraktion erhalten."""
    doc = _doc([_grp("7. sonstige", [_acc(gj=150.0)], pdf_sum_gj=200.0)])  # Lücke 50

    def reextract_worse(ext, gaps):
        return {"7. sonstige": [_acc(gj=100.0)]}  # anders, aber Lücke jetzt 100 (schlechter)

    healed, remaining = heal_extraction(doc, reextract_worse, max_rounds=2)
    accs = healed["groups"][0]["accounts"]
    assert sum(a["betrag_gj"] for a in accs) == 150.0, "Original hätte erhalten bleiben müssen"
    assert len(remaining) == 1


def test_heal_rejects_candidate_that_breaks_other_period():
    """Codex P2-3: ein Kandidat, der GJ schließt aber VJ (vorher korrekt)
    verschlechtert, darf NICHT übernommen werden — pro Periode prüfen."""
    doc = _doc([_grp("7. sonstige",
                     [_acc(gj=150.0, vj=100.0)],
                     pdf_sum_gj=200.0, pdf_sum_vj=100.0)])  # GJ-Lücke 50, VJ ok
    def reextract_breaks_vj(ext, gaps):
        # GJ korrekt (200), aber VJ kaputt (60 statt 100) → VJ-Lücke 40 neu
        return {"7. sonstige": [_acc(gj=200.0, vj=60.0)]}
    healed, remaining = heal_extraction(doc, reextract_breaks_vj, max_rounds=2)
    acc = healed["groups"][0]["accounts"][0]
    assert acc["betrag_vj"] == 100.0, "korrektes VJ hätte erhalten bleiben müssen"


def test_parent_with_summary_only_children_not_false_gap():
    """Codex Round-4: Parent mit gedruckter Summe, Kinder sind summary-only
    (eigene pdf_sum, KEINE Konten — DATEV-Rohergebnis/Bilanzbericht). Der Builder
    summiert die Kind-Summen → der Parent reconciled; er darf NICHT als Lücke
    gemeldet werden (Kinder selbst dürfen, sie haben kein Detail)."""
    doc = _doc([
        {"name": "4. Materialaufwand", "accounts": [], "pdf_sum_gj": 300.0,
         "gkv_section": "materialaufwand_rhb", "sub_group_of": None},
        {"name": "4. a) RHB", "accounts": [], "pdf_sum_gj": 200.0,
         "sub_group_of": "4. Materialaufwand"},
        {"name": "4. b) Bezogene", "accounts": [], "pdf_sum_gj": 100.0,
         "sub_group_of": "4. Materialaufwand"},
    ])
    gaps = document_completeness(doc)
    assert not any(g["group"] == "4. Materialaufwand" for g in gaps), \
        f"Parent fälschlich als Lücke: {gaps}"


def test_heal_ignores_malformed_reextract_accounts():
    """Codex Round-5: liefert die Re-Extraktion gültiges JSON aber kaputte
    accounts (dict statt Liste, oder Liste mit Strings/None), darf heal_extraction
    NICHT crashen — Kandidat wird verworfen, Original bleibt (graceful)."""
    doc = _doc([_grp("7. sonstige", [_acc(gj=150.0)], pdf_sum_gj=200.0)])

    for bad in ({"7. sonstige": {"not": "a list"}},
                {"7. sonstige": ["string", None, 123]},
                {"7. sonstige": [{"betrag_gj": "nicht-zahl"}]}):
        healed, remaining = heal_extraction(doc, lambda e, g, b=bad: b, max_rounds=2)
        accs = healed["groups"][0]["accounts"]
        assert len(accs) == 1 and accs[0]["betrag_gj"] == 150.0, \
            f"Original hätte erhalten bleiben müssen, war: {accs}"


def test_heal_preserves_unanchored_vj_when_candidate_drops_it():
    """Codex Round-6: Gruppe hat VJ-Konten-Werte, aber KEINEN pdf_sum_vj-Anker.
    Ein Kandidat, der GJ schließt aber VJ nullt, darf nicht übernommen werden —
    sonst gehen die unanchored VJ-Werte verloren."""
    doc = _doc([_grp("7. sonstige", [_acc(gj=150.0, vj=90.0)], pdf_sum_gj=200.0)])  # kein pdf_sum_vj
    def reextract_drops_vj(ext, gaps):
        return {"7. sonstige": [_acc(gj=200.0, vj=None)]}  # GJ ok, VJ weg
    healed, _ = heal_extraction(doc, reextract_drops_vj, max_rounds=2)
    accs = healed["groups"][0]["accounts"]
    assert any(a.get("betrag_vj") == 90.0 for a in accs), \
        f"unanchored VJ wurde überschrieben: {accs}"


def test_heal_preserves_unanchored_vj_total_even_same_count():
    """Codex Round-7 (Altitude): GJ-Lücke, VJ vorhanden aber OHNE pdf_sum_vj-Anker.
    Ein Kandidat, der GJ schließt aber den VJ-Wert ÄNDERT (gleiche Anzahl), darf
    NICHT übernommen werden — unanchored Perioden-Summen bleiben unangetastet."""
    doc = _doc([_grp("7. sonstige", [_acc(gj=150.0, vj=90.0)], pdf_sum_gj=200.0)])
    def reextract_changes_vj(ext, gaps):
        return {"7. sonstige": [_acc(gj=200.0, vj=70.0)]}  # GJ ok, VJ 90→70 (gleiche Anzahl)
    healed, _ = heal_extraction(doc, reextract_changes_vj, max_rounds=2)
    accs = healed["groups"][0]["accounts"]
    assert sum((a.get("betrag_vj") or 0) for a in accs) == 90.0, \
        f"unanchored VJ-Summe verändert: {accs}"


def test_heal_keeps_original_on_partial_improvement():
    """Codex Round-8A: eine nur TEILWEISE bessere Re-Extraktion, die bekannte
    Konten weglässt, darf das Original nicht ersetzen — nur volle Schließung der
    Lücke wird übernommen (sonst Detail-Verlust; Builder hat den Restposten)."""
    doc = _doc([_grp("7. sonstige",
                     [_acc(gj=100.0, nr="A"), _acc(gj=50.0, nr="B")],
                     pdf_sum_gj=200.0)])  # Lücke 50
    def reextract_partial(ext, gaps):
        return {"7. sonstige": [_acc(gj=180.0, nr="C")]}  # näher (Lücke 20) aber A/B weg
    healed, remaining = heal_extraction(doc, reextract_partial, max_rounds=2)
    nrs = {a["konto_nr"] for a in healed["groups"][0]["accounts"]}
    assert nrs == {"A", "B"}, f"bekannte Konten verloren: {nrs}"
    assert len(remaining) == 1
