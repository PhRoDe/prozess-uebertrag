"""Mehrjahres-Konsolidierung: nimmt die Extraktionen aus N Jahresabschluss-
und M BWA-PDFs und baut die konsolidierte Struktur für den Excel-Builder.

Kernprinzipien:
- Gruppen-Reihenfolge und -Struktur aus der **jüngsten** JA-PDF
- Konten-Matching über `konto_nr` (stabil, sonst Fallback über Bezeichnung)
- JAs und BWAs bekommen **eigene Spalten**
- Vorzeichen-Konvention und Gruppen-Typen aus der jüngsten JA-PDF
"""
from typing import Any

MISMATCH_TOLERANCE = 0.01  # 1 cent


def merge_extractions(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge Jahresabschluss- + BWA-Extraktionen in die konsolidierte Struktur.

    Returns:
        {
            "columns": [{"label": "2024", "kind": "ja", "year": 2024,
                         "sign_convention": "expenses_negative"}, ...],
            "groups": [
                {"name": "1. Umsatzerlöse", "type": "ertrag",
                 "sub_group_of": None,
                 "column_sums": {column_idx: pdf_sum_value_or_None},
                 "accounts": [{"konto_nr": "8400", "bezeichnung": "...",
                               "values": {column_idx: value}}, ...]}
            ],
            "questions": [...]
        }
    """
    ja_docs = [e for e in extractions if e.get("type") == "jahresabschluss"]
    bwa_docs = [e for e in extractions if e.get("type") == "bwa"]

    # 1. Spalten-Reihenfolge aufbauen.
    # JAs sortiert nach year, BWAs dazwischen nach ihrem year.
    columns = _build_columns(ja_docs, bwa_docs)
    if not columns:
        return {"columns": [], "groups": [], "questions": []}

    # 2. Gruppen-Template: Reihenfolge + Struktur aus der JÜNGSTEN JA-PDF.
    if ja_docs:
        newest_ja = max(ja_docs, key=lambda e: e.get("year", 0))
        group_template = [
            {
                "name": g["name"],
                "type": g.get("type", "neutral"),
                "sub_group_of": g.get("sub_group_of"),
            }
            for g in newest_ja.get("groups", [])
        ]
        # Fix: Wenn eine Sub-Gruppe auf eine Haupt-Gruppe verweist, die NICHT im Template
        # vorkommt, lege die Haupt-Gruppe synthetisch direkt vor der ersten Sub an.
        group_template = _insert_missing_parents(group_template)
    else:
        # BWAs haben keine Hierarchie, nur flache Gruppen → nehme Positionen aus erster BWA
        first_bwa = bwa_docs[0] if bwa_docs else None
        group_template = [
            {"name": p["name"], "type": p.get("type", "neutral"), "sub_group_of": None}
            for p in (first_bwa.get("positions", []) if first_bwa else [])
        ]

    # 3. Daten pro Gruppe sammeln
    groups_by_name: dict[str, dict] = {
        g["name"]: {
            "name": g["name"],
            "type": g["type"],
            "sub_group_of": g["sub_group_of"],
            "column_sums": {},
            "accounts_by_key": {},  # key -> account-dict; später zu list konvertiert
            "account_order": [],  # stabile Reihenfolge der keys
        }
        for g in group_template
    }

    questions: list[dict] = []

    # 4. Jede JA-Extraktion einsortieren
    for col_idx, col in enumerate(columns):
        if col["kind"] != "ja":
            continue
        doc = _find_ja_for_year(ja_docs, col["year"])
        if doc is None:
            continue
        _ingest_ja(doc, col_idx, col["year"], groups_by_name, group_template,
                   questions, newest_ja_year=newest_ja["year"] if ja_docs else None)

    # 5. Jede BWA-Extraktion einsortieren (nur Gruppen-Summen, keine Konten)
    for col_idx, col in enumerate(columns):
        if col["kind"] != "bwa":
            continue
        doc = _find_bwa_for_col(bwa_docs, col)
        if doc is None:
            continue
        _ingest_bwa(doc, col_idx, groups_by_name)

    # 6. Vorjahreswerte aus JAs einsortieren (Cross-Year-Check)
    _apply_previous_year_values(ja_docs, columns, groups_by_name, questions)

    # 7. Gruppen finalisieren: accounts_by_key → accounts list
    groups_out = []
    for g_tpl in group_template:
        g = groups_by_name[g_tpl["name"]]
        accounts = [g["accounts_by_key"][k] for k in g["account_order"]]
        groups_out.append({
            "name": g["name"],
            "type": g["type"],
            "sub_group_of": g["sub_group_of"],
            "column_sums": g["column_sums"],
            "accounts": accounts,
        })

    return {"columns": columns, "groups": groups_out, "questions": questions}


# ---------------------------------------------------------------------------


def _build_columns(ja_docs: list[dict], bwa_docs: list[dict]) -> list[dict]:
    """Baue die Spalten in chronologischer Reihenfolge.

    Eine JA-Spalte wird angelegt für jedes Jahr, für das wir Daten haben:
    - explizit (ein JA mit year=Y), oder
    - implizit (ein JA n+1 mit previous_year=Y, auch wenn kein JA_n existiert).
    BWA-Spalten werden separat für ihr year angelegt.
    """
    ja_years: set[int] = set()
    for d in ja_docs:
        if d.get("year") is not None:
            ja_years.add(d["year"])
        if d.get("previous_year") is not None:
            ja_years.add(d["previous_year"])

    default_sign = (ja_docs[0].get("sign_convention", "expenses_negative")
                    if ja_docs else "expenses_negative")

    entries: list[tuple[int, int, dict]] = []
    for y in ja_years:
        # Sign convention bevorzugt aus dem Dokument, das year == y hat
        match = next((d for d in ja_docs if d.get("year") == y), None)
        sign = (match.get("sign_convention", default_sign) if match
                else default_sign)
        entries.append((y, 0, {
            "label": str(y), "kind": "ja", "year": y, "sign_convention": sign,
        }))
    for d in bwa_docs:
        y = d.get("year")
        if y is None:
            continue
        entries.append((y, 1, {
            "label": d.get("period_label") or f"BWA {y}",
            "kind": "bwa", "year": y,
            "sign_convention": d.get("sign_convention", default_sign),
        }))
    entries.sort(key=lambda t: (t[0], t[1]))
    return [e[2] for e in entries]


def _find_ja_for_year(ja_docs: list[dict], year: int) -> dict | None:
    for d in ja_docs:
        if d.get("year") == year:
            return d
    return None


def _find_bwa_for_col(bwa_docs: list[dict], col: dict) -> dict | None:
    for d in bwa_docs:
        if d.get("year") == col["year"] and (d.get("period_label") or
                                              f"BWA {d.get('year')}") == col["label"]:
            return d
    return None


def _ingest_ja(doc: dict, col_idx: int, year: int, groups_by_name: dict,
               group_template: list, questions: list, newest_ja_year: int | None):
    """Integriere ein JA-Dokument in die Konsolidierung."""
    seen_groups_in_doc = set()
    for g in doc.get("groups", []):
        gname = g["name"]
        seen_groups_in_doc.add(gname)
        target = groups_by_name.get(gname)
        if target is None:
            # Gruppe existiert in diesem älteren JA, aber nicht in der jüngsten
            # → wir ignorieren sie für jetzt (sonst würde die Reihenfolge bröckeln).
            # Alternative: anhängen. TODO falls in Praxis nötig.
            continue
        # Gruppen-Summe aus PDF (für Cross-Check)
        if g.get("pdf_sum_gj") is not None:
            target["column_sums"][col_idx] = g["pdf_sum_gj"]
        # Konten
        for acc in g.get("accounts", []):
            key = _acc_key(acc, gname)
            if key not in target["accounts_by_key"]:
                target["accounts_by_key"][key] = {
                    "konto_nr": acc.get("konto_nr"),
                    "bezeichnung": acc.get("bezeichnung", ""),
                    "values": {},
                    "confidence": acc.get("confidence", "high"),
                }
                target["account_order"].append(key)
            target["accounts_by_key"][key]["values"][col_idx] = acc.get("betrag_gj")

        # Cross-Check: SUM(Konten) vs pdf_sum_gj
        if g.get("pdf_sum_gj") is not None:
            real_sum = sum(a.get("betrag_gj") or 0 for a in g.get("accounts", []))
            if abs(real_sum - g["pdf_sum_gj"]) > MISMATCH_TOLERANCE:
                questions.append({
                    "type": "group_sum_mismatch",
                    "group": gname, "year": year,
                    "pdf_says": g["pdf_sum_gj"], "accounts_sum": real_sum,
                })

    # open_questions übertragen
    for oq in doc.get("open_questions", []):
        questions.append({
            "type": "unmatched_account", "year": year,
            "konto_nr": oq.get("konto_nr"),
            "bezeichnung": oq.get("bezeichnung"),
            "betrag_gj": oq.get("betrag_gj"),
            "hint": oq.get("hint", ""),
        })


def _ingest_bwa(doc: dict, col_idx: int, groups_by_name: dict):
    """BWA hat nur Summen pro Gruppe, keine Konten-Details."""
    for p in doc.get("positions", []):
        target = groups_by_name.get(p["name"])
        if target is None:
            continue
        target["column_sums"][col_idx] = p.get("betrag")


def _apply_previous_year_values(ja_docs: list[dict], columns: list[dict],
                                 groups_by_name: dict, questions: list):
    """Wenn JA_n Vorjahreswerte enthält, tragen wir die in die Spalte für year n-1 ein,
    soweit noch nichts drin ist. Mismatch mit dem eigenen Jahr der Vorjahres-PDF
    → Fragen-Sheet."""
    for doc in ja_docs:
        vj = doc.get("previous_year")
        if vj is None:
            continue
        # Finde Spalten-Index für Vorjahr (erste JA-Spalte mit diesem Jahr)
        vj_col_idx = None
        for idx, col in enumerate(columns):
            if col["kind"] == "ja" and col["year"] == vj:
                vj_col_idx = idx
                break
        if vj_col_idx is None:
            continue  # Vorjahr ist keine eigene Spalte — skippen

        for g in doc.get("groups", []):
            target = groups_by_name.get(g["name"])
            if target is None:
                continue
            for acc in g.get("accounts", []):
                if acc.get("betrag_vj") is None:
                    continue
                key = _acc_key(acc, g["name"])
                if key not in target["accounts_by_key"]:
                    # Konto kommt nur im Vorjahr vor — aufnehmen
                    target["accounts_by_key"][key] = {
                        "konto_nr": acc.get("konto_nr"),
                        "bezeichnung": acc.get("bezeichnung", ""),
                        "values": {},
                        "confidence": acc.get("confidence", "high"),
                    }
                    target["account_order"].append(key)
                existing = target["accounts_by_key"][key]["values"].get(vj_col_idx)
                vj_val = acc["betrag_vj"]
                if existing is not None and abs(existing - vj_val) > MISMATCH_TOLERANCE:
                    questions.append({
                        "type": "previous_year_mismatch",
                        "group": g["name"], "konto_nr": acc.get("konto_nr"),
                        "year": vj, "from_doc_year": doc.get("year"),
                        "own_value": existing, "pdf_says": vj_val,
                    })
                else:
                    target["accounts_by_key"][key]["values"].setdefault(vj_col_idx, vj_val)


def _insert_missing_parents(tpl: list[dict]) -> list[dict]:
    """Ensure every sub_group_of reference has a real parent group in the list.
    If a parent is missing, synthesize it directly before the first child."""
    existing = {g["name"] for g in tpl}
    out: list[dict] = []
    inserted: set[str] = set()
    for g in tpl:
        parent = g.get("sub_group_of")
        if parent and parent not in existing and parent not in inserted:
            # Inherit type from the first sub with that parent
            out.append({
                "name": parent,
                "type": g.get("type", "neutral"),
                "sub_group_of": None,
            })
            inserted.add(parent)
        out.append(g)
    return out


def _acc_key(acc: dict, group_name: str) -> str:
    """Stable key for account matching across documents."""
    nr = acc.get("konto_nr")
    if nr:
        return f"nr:{nr}"
    # Fallback: Gruppe + Bezeichnung (reduziert Kollisionen)
    return f"nrless:{group_name}::{acc.get('bezeichnung', '')}"
