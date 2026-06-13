"""Vollständigkeits-Lücken: EINE reine Quelle der Wahrheit für Review-Panel
(db.completeness_summary) UND Excel-Build (builder).

Wichtig: KEIN Netzwerk/IO — damit app/excel/builder.py das hier importieren darf
(Layer-Regel: Output ohne Netzwerk). Beide Seiten leiten die Gap-Liste aus
genau dieser Funktion ab, sonst divergieren Review-Anzeige und Fragen-Sheet
(gleiche Reihenfolge → gap_index bleibt gültig).
"""
from typing import Any


def _col_get(d: dict, ci: int):
    """Spalten-Wert robust holen — int-Key (in-memory) ODER String-Key
    (JSONB-Roundtrip)."""
    d = d or {}
    if ci in d:
        return d[ci]
    return d.get(str(ci))


def _match_group_name(raw, names: list) -> Any:
    """Beste Ziel-(Leaf-)Gruppe für einen Roh-Lückennamen. Exakt gewinnt immer;
    sonst bidirektionaler Substring-Match (case-insensitiv). Bei MEHREREN
    Substring-Treffern → None (nicht raten — sonst bindet die Lücke an die
    falsche Gruppe und kann beim Recompute still gedroppt werden). Kein/uneindeutiger
    Match → None, der User wählt im Dropdown selbst."""
    if not raw:
        return None
    if raw in names:
        return raw
    raw_lc = str(raw).lower()
    matches = [n for n in names if raw_lc in str(n).lower() or str(n).lower() in raw_lc]
    return matches[0] if len(matches) == 1 else None


def _leaf_col_diff(group: dict | None, col_idx: int):
    """(printed, acc_sum, diff) der EIGENEN (nicht-synthetischen) Konten einer
    Gruppe in einer Spalte, aus der konsolidierten Struktur. diff = printed −
    acc_sum = exakt der Restposten, der gesetzt würde. None ohne gedruckte
    Summe."""
    if not group:
        return None
    printed = _col_get(group.get("column_sums") or {}, col_idx)
    if not isinstance(printed, (int, float)):
        return None
    acc = 0.0
    for a in group.get("accounts") or []:
        if a.get("confidence") == "synthetic":
            continue
        v = _col_get(a.get("values") or {}, col_idx)
        if isinstance(v, (int, float)):
            acc += v
    return round(float(printed), 2), round(acc, 2), round(float(printed) - acc, 2)


def leaf_group_names(groups: list[dict]) -> list:
    """Gruppen-Namen ohne Parent-Gruppen (Gruppen, die Sub-Gruppen unter sich
    haben). Manuelle Korrekturen dürfen nur in Leaf-Gruppen — ein Konto am
    Parent würde dessen Kinder-Kaskade doppelzählen."""
    parent_names = {g.get("sub_group_of") for g in groups if g.get("sub_group_of")}
    return [g.get("name") for g in groups if g.get("name") not in parent_names]


def ja_columns(columns: list[dict]) -> list[dict]:
    """JA-Spalten als [{idx, label}] mit erhaltenem GLOBALEM Index.
    completeness_gap kommt aus JA-Gruppensummen → BWA/Susa-Spalten passen nicht
    als Korrektur-Ziel."""
    return [{"idx": i, "label": c.get("label")} for i, c in enumerate(columns)
            if (c.get("doc_type") or c.get("kind")) not in ("bwa", "susa")]


def completeness_gaps(consolidated: dict | None) -> list[dict]:
    """Geordnete, gefilterte Lücken-Liste — die kanonische Quelle für Review +
    Build. Pro completeness_gap-Frage: Ziel-Leaf-Gruppe (Name-Match) + Ziel-
    Spalte (Jahr→JA-Spalte) anreichern; wenn beides auflösbar, Anzeige/Prefill
    aus der KONSOLIDIERTEN Spalte neu rechnen (richtiges Vorzeichen nach
    Normalisierung) und konsolidiert geschlossene Lücken (≤1 ct) weglassen.
    """
    consolidated = consolidated or {}
    groups = consolidated.get("groups") or []
    columns = consolidated.get("columns") or []
    questions = consolidated.get("questions") or []
    leaf = leaf_group_names(groups)
    jcols = ja_columns(columns)
    year_to_col: dict = {}
    for c in jcols:
        y = columns[c["idx"]].get("year")
        if y is not None:
            year_to_col.setdefault(y, c["idx"])
    by_name = {g.get("name"): g for g in groups}
    out: list[dict] = []
    for q in questions:
        if q.get("type") != "completeness_gap":
            continue
        tg = _match_group_name(q.get("group"), leaf)
        tc = year_to_col.get(q.get("year"))
        gap = {**q, "target_group": tg, "target_col": tc}
        if tg is not None and tc is not None:
            rec = _leaf_col_diff(by_name.get(tg), tc)
            if rec is not None:
                printed, acc, diff = rec
                if abs(diff) <= 0.01:
                    continue  # konsolidiert geschlossen → nicht zeigen
                gap.update(printed_sum=printed, acc_sum=acc, diff=diff)
        out.append(gap)
    return out
