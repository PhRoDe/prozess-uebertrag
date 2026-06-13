"""Vollständigkeits-Check pro extrahiertem Dokument (Phase 1a).

Gleicht je Gruppe die Summe der extrahierten Konten gegen die im PDF gedruckte
Gruppensumme ab. Eine Abweichung > 1 ct bedeutet: Konten fehlen oder wurden
falsch gelesen. Diese Lücke wird im Excel-Builder sonst still vom Restposten-
Mechanismus gefüllt (`_inject_restposten_accounts`) — hier machen wir sie
sichtbar, damit die Selbstheilung (1b) und das Review (Phase 3) darauf reagieren
können.

Reines Modul ohne I/O — keine HTTP-/Netzwerk-/Excel-Imports (app/worker-Regel).
"""
from typing import Any, Callable

TOLERANCE = 0.01  # 1 cent


def _is_bestandsveraenderung(group: dict) -> bool:
    """Bestandsveränderung (GKV Pos. 2) ausnehmen: das Detail wird beim
    Konsolidieren normalisiert, der Anker bleibt roh — ein Diff ist hier
    erwartbar und KEINE echte Lücke (Spiegel zu builder._is_bestandsveraenderung)."""
    if group.get("gkv_section") == "bestandsveraenderung":
        return True
    name = (group.get("name") or "").lower()
    return "bestand" in name and (
        "verminderung" in name or "erhöhung" in name or "bestandsveränderung" in name
    )


def _acc_sum(accounts: list[dict], field: str) -> float:
    total = 0.0
    for acc in accounts or []:
        if not isinstance(acc, dict):  # defensiv: kaputte Items überspringen
            continue
        v = acc.get(field)
        if isinstance(v, (int, float)):
            total += v
    return total


def _valid_accounts(accounts: object) -> bool:
    """Akzeptiere nur eine Liste von Dicts, deren Beträge Zahl oder None sind.
    Eine Re-Extraktion mit kaputter accounts-Struktur (dict statt Liste, Strings,
    None-Items) darf nicht ins Staging — sonst crasht _acc_sum und der Job failt
    statt graceful auf die Erst-Extraktion zurückzufallen (Codex Round-5)."""
    if not isinstance(accounts, list):
        return False
    for acc in accounts:
        if not isinstance(acc, dict):
            return False
        for f in ("betrag_gj", "betrag_vj"):
            v = acc.get(f)
            if v is not None and not isinstance(v, (int, float)):
                return False
    return True


def _group_acc_sum(group: dict, all_groups: list[dict], field: str) -> float:
    """Effektive Summe einer Gruppe INKLUSIVE ihrer Sub-Gruppen — spiegelt die
    Parent=Summe-der-Kinder-Logik des Builders. Verhindert, dass ein Parent
    (gedruckte Summe am Parent, Detail in den Subs, z.B. '4. Materialaufwand'
    mit 4a/4b) fälschlich als unvollständig gemeldet wird (Codex P2-1).

    Ein Sub OHNE eigene Konten aber MIT gedruckter Summe (summary-only, DATEV-
    Rohergebnis/Bilanzbericht) trägt seine pdf_sum bei — genau das summiert der
    Builder für den Parent. Sonst Parent-Falschmeldung (Codex Round-4)."""
    anchor = "pdf_sum_gj" if field == "betrag_gj" else "pdf_sum_vj"
    total = _acc_sum(group.get("accounts"), field)
    name = group.get("name")
    for g in all_groups:
        if g.get("sub_group_of") != name:
            continue
        if g.get("accounts"):
            total += _acc_sum(g.get("accounts"), field)
        else:
            cp = g.get(anchor)
            if isinstance(cp, (int, float)):
                total += cp
    return total


def _group_period_gaps(group: dict, all_groups: list[dict]) -> dict[str, float]:
    """Lücke |gedruckte Summe − Konten-Summe (inkl. Subs)| PRO Periode (gj/vj),
    nur für Perioden mit Anker. Dient dem Heal-Staging: ein Kandidat wird nur
    übernommen, wenn er KEINE Periode verschlechtert und mindestens eine
    verbessert (Codex-Findings P2-2/P2-3) — sonst würde das Schließen einer GJ-
    Lücke korrekte VJ-Daten überschreiben."""
    if _is_bestandsveraenderung(group):
        return {}
    out: dict[str, float] = {}
    for period, field, anchor_key in (("gj", "betrag_gj", "pdf_sum_gj"),
                                       ("vj", "betrag_vj", "pdf_sum_vj")):
        printed = group.get(anchor_key)
        if isinstance(printed, (int, float)):
            out[period] = abs(printed - _group_acc_sum(group, all_groups, field))
    return out


def document_completeness(extraction: dict[str, Any],
                          tolerance: float = TOLERANCE) -> list[dict]:
    """Liefert die Lücken eines Dokuments: Gruppen, bei denen die Konten-Summe
    von der gedruckten Gruppensumme abweicht.

    Jede Lücke: {group, period ('gj'|'vj'), year, printed_sum, acc_sum, diff}.
    """
    year_gj = extraction.get("year")
    year_vj = extraction.get("previous_year")
    gaps: list[dict] = []

    all_groups = extraction.get("groups", [])
    for group in all_groups:
        if _is_bestandsveraenderung(group):
            continue
        for period, field, anchor_key, year in (
            ("gj", "betrag_gj", "pdf_sum_gj", year_gj),
            ("vj", "betrag_vj", "pdf_sum_vj", year_vj),
        ):
            printed = group.get(anchor_key)
            if not isinstance(printed, (int, float)):
                continue
            acc_sum = _group_acc_sum(group, all_groups, field)
            diff = printed - acc_sum
            if abs(diff) > tolerance:
                gaps.append({
                    "group": group.get("name"),
                    "period": period,
                    "year": year,
                    "printed_sum": float(printed),
                    "acc_sum": round(acc_sum, 2),
                    "diff": round(diff, 2),
                })
    return gaps


def heal_extraction(
    extraction: dict[str, Any],
    reextract_fn: Callable[[dict, list[dict]], dict[str, list[dict]]],
    max_rounds: int = 2,
) -> tuple[dict, list[dict]]:
    """Selbstheilung: solange Lücken existieren, gezielt nachextrahieren.

    `reextract_fn(extraction, gaps)` liefert pro Gruppen-Name die (vollständige)
    Konten-Liste zurück. Ein Kandidat wird nur übernommen, wenn er die Lücke der
    Gruppe VERKLEINERT (Staging) — eine andere, aber gleich/schlechter
    unvollständige Liste darf die erste Extraktion nicht verschlechtern
    (Codex-Finding P2-2). Abbruch bei: keine Lücken mehr, `max_rounds` erreicht,
    leere Antwort, oder kein Fortschritt — Schutz gegen Endlosschleifen.

    Returns: (geheilte_extraction, verbleibende_lücken).
    """
    all_groups = extraction.get("groups", [])
    groups_by_name = {g.get("name"): g for g in all_groups}
    for _ in range(max_rounds):
        gaps = document_completeness(extraction)
        if not gaps:
            break
        filled = reextract_fn(extraction, gaps) or {}
        progressed = False
        for gname, accounts in filled.items():
            group = groups_by_name.get(gname)
            if (group is None or not accounts or not _valid_accounts(accounts)
                    or accounts == group.get("accounts")):
                continue
            old = group.get("accounts") or []
            # Unanchored Perioden (kein pdf_sum_{gj,vj} an der Gruppe) bleiben
            # UNANGETASTET: ohne Anker können wir nicht beurteilen, ob ein
            # geänderter Wert besser ist — also darf die Heilung die Summe dieser
            # Periode nicht verändern. Schützt z.B. VJ-Werte ohne pdf_sum_vj gegen
            # Überschreiben (Codex Round-6/7, Altitude-Fix statt Einzel-Guards).
            anchors = {"betrag_gj": "pdf_sum_gj", "betrag_vj": "pdf_sum_vj"}
            if any(not isinstance(group.get(anchor), (int, float))
                   and abs(_acc_sum(accounts, field) - _acc_sum(old, field)) > TOLERANCE
                   for field, anchor in anchors.items()):
                continue
            before = _group_period_gaps(group, all_groups)
            if not any(g > TOLERANCE for g in before.values()):
                continue  # keine geankerte Lücke an dieser Gruppe → nichts zu heilen
            group["accounts"] = accounts  # tentativ
            after = _group_period_gaps(group, all_groups)
            # NUR übernehmen, wenn der Kandidat ALLE geankerten Lücken VOLL
            # schließt. Eine nur teilweise bessere Liste, die bekannte Konten
            # weglässt, würde Detail verlieren — dann lieber Original behalten
            # (der Builder ergänzt den Restposten). (Codex Round-8A)
            if all(g <= TOLERANCE for g in after.values()):
                progressed = True
            else:
                group["accounts"] = old    # nicht voll geschlossen → zurücksetzen
        if not progressed:
            break
    return extraction, document_completeness(extraction)
