"""Benchmarking-Kennzahlen (Phase B). Rein, kein Netzwerk/IO.

Berechnet pro JA-Spalte kanonische Kennzahlen (Absolutwerte + Quoten) aus der
consolidated-Struktur. Nutzt die GETEILTE Vorzeichen-/Aggregations-Logik aus
app.excel.builder (SECTION_ROLE / _resolve_role / group_column_value /
_endwert_groups) — KEINE Duplizierung (Council #1: sonst Vorzeichen-Bug).

v1-Formeln (METRICS_VERSION=1) — bewusst Standard-HGB-GKV, via metrics_version
später änderbar (Council: Reproduzierbarkeit):
  gesamtleistung = umsatz + bestandsveraenderung + aktivierte_eigenleistungen
  rohertrag      = gesamtleistung − materialaufwand
  betriebsergebnis (EBIT) = rohertrag + sonst_betr_ertraege
                            − personalaufwand − abschreibungen − sonst_betr_aufw
  finanzergebnis = finanzertraege − zinsaufwand
  ergebnis_vor_steuern = betriebsergebnis + finanzergebnis
  jue = PDF-Anker (pdf_jue_per_column) falls vorhanden, sonst
        ergebnis_vor_steuern − steuern
Quoten (NULL bei Nenner ~0):
  personalaufwandsquote = personalaufwand / gesamtleistung
  rohertragsmarge       = rohertrag / gesamtleistung
  ebit_marge            = betriebsergebnis / umsatz
  jue_marge             = jue / umsatz
"""
from app.excel.builder import (
    _coerce_int_keys, _endwert_groups, _resolve_role, group_column_value,
    _infer_sign_conventions, BILANZGEWINN_SECTIONS, SECTION_ROLE,
)

METRICS_VERSION = 2  # v2 (Council 2026-06-14): Umsatz-Default-Margen, EBITDA,
                     # ebit_analytisch, Material-/Abschr.-/Aktivierungsquote,
                     # Zinsdeckung, Steuerquote, verfahren-Heuristik (gkv/None)

# gkv_section → Kennzahl-Bucket
_SECTION_BUCKET: dict[str, str] = {
    "umsatzerloese": "umsatz",
    "bestandsveraenderung": "bestandsveraenderung",
    "aktivierte_eigenleistungen": "aktivierte_eigenleistungen",
    "sonst_betr_ertraege": "sonst_betr_ertraege",
    "materialaufwand_rhb": "materialaufwand",
    "materialaufwand_bez_leistungen": "materialaufwand",
    "personalaufwand_loehne": "personalaufwand",
    "personalaufwand_sozial": "personalaufwand",
    "abschreibungen": "abschreibungen",
    "sonst_betr_aufw": "sonst_betr_aufw",
    "ertraege_wertpapiere": "finanzertraege",
    "ertraege_beteiligungen": "finanzertraege",
    "sonstige_zins_ertraege": "finanzertraege",
    "zinsaufwand": "zinsaufwand",
    "ee_steuern": "steuern",
    "sonst_steuern": "steuern",
}

_BUCKETS = set(_SECTION_BUCKET.values())


def _div(num: float, den: float):
    """Quote oder None bei Nenner ~0."""
    if not isinstance(den, (int, float)) or abs(den) < 0.01:
        return None
    return round(num / den, 4)


def _own_account_sum(g: dict, col_idx: int) -> float:
    total = 0.0
    for acc in g.get("accounts") or []:
        v = (acc.get("values") or {}).get(col_idx)
        if isinstance(v, (int, float)):
            total += v
    return total


def _acc_incl_children(g: dict, groups: list[dict], col_idx: int) -> float:
    """Eigene + (bei Top-Level-Parent) Kind-Konten — OHNE column_sum-Fallback.
    Für die Restposten-/Quality-Schätzung bei verschachtelten Strukturen."""
    total = _own_account_sum(g, col_idx)
    if g.get("sub_group_of") is None:
        for sub in groups:
            if sub.get("sub_group_of") == g.get("name"):
                total += _own_account_sum(sub, col_idx)
    return total


def compute_company_metrics(consolidated: dict, col_idx: int) -> dict | None:
    """Kennzahlen für EINE JA-Spalte (col_idx). None wenn nichts Verwertbares.

    Liefert Absolutwerte (sign-korrigiert: Erträge +, Kosten + als Magnitude),
    Quoten und eine Datenqualitäts-Schätzung. Ohne company_id/Jahr/data_source —
    die setzt der Aufrufer (Persistenz-Schicht)."""
    consolidated = consolidated or {}
    groups = _coerce_int_keys(consolidated.get("groups") or [])
    columns = consolidated.get("columns") or []
    if col_idx < 0 or col_idx >= len(columns):
        return None
    # JA-only: BWA/Susa haben andere Endwert-Semantik (Council: data_source nie
    # mischen). Diese Funktion ist für JA-Spalten — nicht-JA → None (Guard).
    if (columns[col_idx].get("doc_type") or columns[col_idx].get("kind")) in ("bwa", "susa"):
        return None
    columns = _infer_sign_conventions(columns, groups)
    conv = columns[col_idx].get("sign_convention", "expenses_negative")

    # Sub-Gruppen tragen die gkv_section nicht immer (Konten im Kind, Sektion am
    # Parent) — effektive Sektion: eigene ODER die des Parents erben.
    section_by_name = {g.get("name"): g.get("gkv_section") for g in groups}

    def _eff_section(g: dict):
        sec = g.get("gkv_section")
        if sec:
            return sec
        parent = g.get("sub_group_of")
        return section_by_name.get(parent) if parent else None

    b: dict[str, float] = {k: 0.0 for k in _BUCKETS}
    saw_value = False
    for g in _endwert_groups(groups):
        eff_sec = _eff_section(g)
        if eff_sec in BILANZGEWINN_SECTIONS:
            continue
        bucket = _SECTION_BUCKET.get(eff_sec)
        if bucket is None:
            continue
        raw = group_column_value(g, groups, col_idx)
        if raw:
            saw_value = True
        role = SECTION_ROLE.get(eff_sec) or _resolve_role(g)
        # kanonische Magnitude: Erträge behalten ihr (positives) Vorzeichen
        # (Bestandsveränderung darf negativ bleiben); Aufwand/Steuer werden zur
        # positiven Kosten-Magnitude (in expenses_negative: raw×−1).
        mag = raw if role == "ertrag" else (raw if conv == "expenses_positive" else -raw)
        b[bucket] += mag
    if not saw_value:
        return None

    gesamtleistung = b["umsatz"] + b["bestandsveraenderung"] + b["aktivierte_eigenleistungen"]
    rohertrag = gesamtleistung - b["materialaufwand"]
    betriebsergebnis = (rohertrag + b["sonst_betr_ertraege"]
                        - b["personalaufwand"] - b["abschreibungen"] - b["sonst_betr_aufw"])
    finanzergebnis = b["finanzertraege"] - b["zinsaufwand"]
    ergebnis_vor_steuern = betriebsergebnis + finanzergebnis
    computed_jue = ergebnis_vor_steuern - b["steuern"]

    # PDF-JÜ-Anker bevorzugen (authoritativ, deckt sich mit dem Excel-Cross-Check)
    pdf_jue_map = consolidated.get("pdf_jue_per_column") or {}
    pdf_jue = pdf_jue_map.get(col_idx, pdf_jue_map.get(str(col_idx)))
    jue = pdf_jue if isinstance(pdf_jue, (int, float)) else computed_jue

    # Residuum: alles, was die gemappten GKV-Sektionen NICHT erklären (neutrale/
    # außerordentliche/nicht klassifizierte Posten). Macht das Modell konsistent
    # zur authoritativen JÜ: jue = betriebsergebnis + finanzergebnis
    # + neutrales_ergebnis − steuern. Bei computed jue (kein PDF-Anker) = 0.
    neutrales_ergebnis = jue + b["steuern"] - betriebsergebnis - finanzergebnis

    # v2-Kennzahlen (Council 2026-06-14):
    ebitda = betriebsergebnis + b["abschreibungen"]
    # ebit_analytisch = Banker-EBIT (Ergebnis vor Zinsen + Steuern), aus der
    # authoritativen JÜ zurückgerechnet — robust auch bei neutralen Posten.
    ebit_analytisch = jue + b["steuern"] + b["zinsaufwand"]
    aktivierungsgrad = b["bestandsveraenderung"] + b["aktivierte_eigenleistungen"]

    # Datenqualität: Anteil der Gruppensummen, der NICHT durch Detail-Konten
    # gedeckt ist (Restposten-Lücke). consolidated enthält keine Restposten.
    total_printed = 0.0
    total_gap = 0.0
    for g in groups:
        # Nur Top-Level (Kinder via _acc_incl_children) → keine Doppelzählung,
        # erfasst verschachtelte Lücken (Parent trägt Summe, Konten in Kindern).
        if g.get("sub_group_of") is not None:
            continue
        if g.get("gkv_section") in BILANZGEWINN_SECTIONS:
            continue
        printed = (g.get("column_sums") or {}).get(col_idx)
        if not isinstance(printed, (int, float)):
            continue
        total_printed += abs(printed)
        total_gap += abs(printed - _acc_incl_children(g, groups, col_idx))
    restposten_anteil = round(total_gap / total_printed, 4) if total_printed > 0.01 else 0.0
    completeness_score = round(max(0.0, 1.0 - restposten_anteil), 4)
    has_open_questions = any(q.get("type") == "unmatched_account"
                             for q in (consolidated.get("questions") or []))

    # GuV-Verfahren (Council): UKV-Aufsteller sind bei Rohertrag/Material nicht
    # mit GKV vergleichbar. Heuristik: GKV-typische Sektionen vorhanden → 'gkv',
    # sonst unbekannt (Benchmark filtert default auf gkv).
    gkv_marker = {"bestandsveraenderung", "aktivierte_eigenleistungen",
                  "materialaufwand_rhb", "materialaufwand_bez_leistungen"}
    seen_sections = {g.get("gkv_section") for g in groups}
    verfahren = "gkv" if (seen_sections & gkv_marker) else None

    u = b["umsatz"]
    gl = gesamtleistung
    return {
        # Absolutwerte
        "umsatz": round(u, 2),
        "gesamtleistung": round(gl, 2),
        "materialaufwand": round(b["materialaufwand"], 2),
        "rohertrag": round(rohertrag, 2),
        "personalaufwand": round(b["personalaufwand"], 2),
        "abschreibungen": round(b["abschreibungen"], 2),
        "sonst_betr_aufw": round(b["sonst_betr_aufw"], 2),
        "sonst_betr_ertraege": round(b["sonst_betr_ertraege"], 2),
        "finanzertraege": round(b["finanzertraege"], 2),
        "zinsaufwand": round(b["zinsaufwand"], 2),
        "betriebsergebnis": round(betriebsergebnis, 2),
        "ebitda": round(ebitda, 2),
        "finanzergebnis": round(finanzergebnis, 2),
        "ebit_analytisch": round(ebit_analytisch, 2),
        "neutrales_ergebnis": round(neutrales_ergebnis, 2),
        "steuern": round(b["steuern"], 2),
        "jue": round(jue, 2),
        # Margen DUAL (Umsatz = Default für externe Vergleiche; Gesamtleistung
        # als GKV-Sekundärsicht — Council: Bestandsv./aktiv. Eigenl. verzerren).
        "rohertragsmarge_umsatz": _div(rohertrag, u),
        "rohertragsmarge_gesamtleistung": _div(rohertrag, gl),
        "betriebsergebnis_marge_umsatz": _div(betriebsergebnis, u),
        "betriebsergebnis_marge_gesamtleistung": _div(betriebsergebnis, gl),
        "ebitda_marge_umsatz": _div(ebitda, u),
        "ebitda_marge_gesamtleistung": _div(ebitda, gl),
        "jue_marge_umsatz": _div(jue, u),
        "jue_marge_gesamtleistung": _div(jue, gl),
        "materialquote_umsatz": _div(b["materialaufwand"], u),
        "materialquote_gesamtleistung": _div(b["materialaufwand"], gl),
        # Quoten (Einzelbasis)
        "personalaufwandsquote": _div(b["personalaufwand"], gl),
        "abschreibungsquote_umsatz": _div(b["abschreibungen"], u),
        "aktivierungsquote": _div(aktivierungsgrad, gl),
        "zinsdeckung": _div(betriebsergebnis, b["zinsaufwand"]),
        "steuerquote": _div(b["steuern"], ergebnis_vor_steuern),
        # Qualität + Meta
        "completeness_score": completeness_score,
        "restposten_anteil": restposten_anteil,
        "has_open_questions": has_open_questions,
        "verfahren": verfahren,
        "metrics_version": METRICS_VERSION,
    }
