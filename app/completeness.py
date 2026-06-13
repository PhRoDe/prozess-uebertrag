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


def _own_acc_detail(group: dict, col_idx: int) -> tuple[float, bool]:
    """(Summe der eigenen nicht-synthetischen Konten, hat_eigenen_Wert) einer
    Gruppe in einer Spalte. Robust gegen int/Str-Spalten-Keys (JSONB)."""
    total = 0.0
    has_value = False
    for a in group.get("accounts") or []:
        if a.get("confidence") == "synthetic":
            continue
        v = _col_get(a.get("values") or {}, col_idx)
        if isinstance(v, (int, float)):
            total += v
            has_value = True
    return total, has_value


def _own_acc(group: dict, col_idx: int) -> float:
    return _own_acc_detail(group, col_idx)[0]


def _leaf_col_diff(group: dict | None, col_idx: int):
    """(printed, acc_sum, diff) der eigenen Konten einer (Leaf-)Gruppe in einer
    Spalte, aus der konsolidierten Struktur. diff = printed − acc_sum = exakt der
    Restposten, der gesetzt würde. None ohne gedruckte Summe."""
    if not group:
        return None
    printed = _col_get(group.get("column_sums") or {}, col_idx)
    if not isinstance(printed, (int, float)):
        return None
    acc = _own_acc(group, col_idx)
    return round(float(printed), 2), round(acc, 2), round(float(printed) - acc, 2)


def _parent_col_diff(parent: dict | None, groups: list, col_idx: int):
    """Wie _leaf_col_diff, aber kinder-bewusst: acc_sum = eigene Konten + Konten
    der direkten Sub-Gruppen (aus denselben KONSOLIDIERTEN, ggf. normalisierten
    Werten). So ist der Parent-Diff mit den Kind-Diffs vergleichbar — sonst
    schlägt die Dedup bei sign-normalisierten Spalten fehl (Codex P2)."""
    if not parent:
        return None
    printed = _col_get(parent.get("column_sums") or {}, col_idx)
    if not isinstance(printed, (int, float)):
        return None
    acc = _own_acc(parent, col_idx)
    for g in groups:
        if g.get("sub_group_of") != parent.get("name"):
            continue
        child_sum, has_acc = _own_acc_detail(g, col_idx)
        if has_acc:
            acc += child_sum
        else:
            # summary-only Kind (keine Konten): gedruckte Summe zählt mit
            # (wie verify._group_acc_sum / project_line_items).
            cv = _col_get(g.get("column_sums") or {}, col_idx)
            if isinstance(cv, (int, float)):
                acc += cv
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
    all_names = list(by_name.keys())
    parent_set = {g.get("sub_group_of") for g in groups if g.get("sub_group_of")}
    raw = [q for q in questions if q.get("type") == "completeness_gap"]

    # Phase 1: jede Lücke auflösen (Ziel-Leaf/Spalte) + aus der konsolidierten
    # Spalte rekalkulieren; konsolidiert geschlossene (≤1 ct) wegfiltern.
    resolved: list[tuple[dict, dict]] = []  # (raw_q, gefilterter gap)
    for q in raw:
        matched_full = _match_group_name(q.get("group"), all_names)
        tg = _match_group_name(q.get("group"), leaf)
        tc = year_to_col.get(q.get("year"))
        gap = {**q, "target_group": tg, "target_col": tc}
        # Leaf-Gap aus eigenen Konten, Parent-Gap kinder-bewusst — beide aus den
        # KONSOLIDIERTEN Werten, damit Parent/Kind-Diffs vergleichbar sind.
        rec = None
        if tc is not None and matched_full is not None:
            if matched_full in parent_set:
                rec = _parent_col_diff(by_name.get(matched_full), groups, tc)
            elif tg is not None:
                rec = _leaf_col_diff(by_name.get(tg), tc)
        if rec is not None:
            printed, acc, diff = rec
            if abs(diff) <= 0.01:
                continue  # konsolidiert geschlossen → nicht zeigen
            gap.update(printed_sum=printed, acc_sum=acc, diff=diff)
        resolved.append((q, gap))

    # Phase 2: Dedup verschachtelter Lücken (Codex P2). document_completeness
    # emittiert für dasselbe fehlende Konto Kind- UND Parent-Gap (Parent-acc_sum
    # zählt Kinder mit). Kind-Aggregat NUR aus den ÜBERLEBENDEN Kindern + deren
    # REKALKULIERTEN Diffs summieren (nicht aus rohen/stale Kind-Gaps) — sonst
    # unterdrückte ein bereits geschlossenes Kind fälschlich den Parent.
    child_gap_diff_sum: dict = {}
    for q, gap in resolved:
        node = by_name.get(_match_group_name(q.get("group"), all_names) or "")
        if node and node.get("sub_group_of"):
            key = (node.get("sub_group_of"), q.get("year"))
            child_gap_diff_sum[key] = child_gap_diff_sum.get(key, 0.0) + (gap.get("diff") or 0.0)

    # Phase 3: emittieren; Parent-Gap überspringen NUR wenn er exakt das Aggregat
    # der überlebenden Kinder DESSELBEN Jahres ist (Standalone- oder Residuum-
    # Parent-Gaps bleiben sichtbar).
    out: list[dict] = []
    for q, gap in resolved:
        matched_full = _match_group_name(q.get("group"), all_names)
        if matched_full is not None and matched_full in parent_set:
            csum = child_gap_diff_sum.get((matched_full, q.get("year")))
            if csum is not None and abs((gap.get("diff") or 0.0) - csum) <= 0.01:
                continue
        out.append(gap)
    return out
