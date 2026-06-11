"""Mehrjahres-Konsolidierung: nimmt die Extraktionen aus N Jahresabschluss-
und M BWA-PDFs und baut die konsolidierte Struktur für den Excel-Builder.

Kernprinzipien:
- Gruppen-Reihenfolge und -Struktur aus der **jüngsten** JA-PDF
- Konten-Matching über `konto_nr` (stabil, sonst Fallback über Bezeichnung)
- JAs und BWAs bekommen **eigene Spalten**
- Vorzeichen-Konvention und Gruppen-Typen aus der jüngsten JA-PDF
"""
import re
from typing import Any

MISMATCH_TOLERANCE = 0.01  # 1 cent


def _normalize_bestand_value(group: dict, value):
    """Bestandsveränderung universal vorzeichen-normalisieren.

    Convention im consolidated:
      positiv = Erhöhung des Bestandes (Ertrag, +JÜ)
      negativ = Verminderung des Bestandes (Aufwand, -JÜ)

    Der Position-Name im PDF gibt die JÜ-Wirkung vor — der Wert liefert nur
    den Betrag. Verschiedene STBs liefern den gleichen Sachverhalt mit
    unterschiedlichen Vorzeichen (Verminderung 614 vs. Verminderung -614),
    deshalb ignorieren wir das eingehende Vorzeichen und setzen es per
    Position-Namen.
    """
    if value is None:
        return None
    if group.get("gkv_section") != "bestandsveraenderung":
        return value
    name_lc = (group.get("name") or "").lower()
    if "bestand" not in name_lc:
        return value
    if "verminderung" in name_lc:
        return -abs(value)
    if "erhöhung" in name_lc:
        return abs(value)
    return value


def _norm_group_name(name: str) -> str:
    """Normalize a group name for fuzzy matching: strip leading
    numbering (1., 5.1, a)) and lowercase. Lets BWA-Group 'Umsatzerlöse'
    merge with JA-Group '1. Umsatzerlöse'."""
    s = (name or "").strip().lower()
    s = re.sub(r"^[\d\.\)\s]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


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
    # Susa wird wie BWA behandelt: eigene Spalte mit Konten-Salden, kein
    # eigener Endwert. Beide Typen tragen `period_label` und `year`.
    bwa_docs = [e for e in extractions if e.get("type") in ("bwa", "susa")]

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
                "gkv_section": g.get("gkv_section", "neutral"),
            }
            for g in newest_ja.get("groups", [])
        ]
        # Fix: Wenn eine Sub-Gruppe auf eine Haupt-Gruppe verweist, die NICHT im Template
        # vorkommt, lege die Haupt-Gruppe synthetisch direkt vor der ersten Sub an.
        group_template = _insert_missing_parents(group_template)
    else:
        # BWA-only: Struktur aus der ersten BWA übernehmen.
        # Neue BWA-Extraktionen liefern `groups`, Legacy-Extraktionen `positions`.
        first_bwa = bwa_docs[0] if bwa_docs else None
        if first_bwa and first_bwa.get("groups"):
            group_template = [
                {"name": g["name"], "type": g.get("type", "neutral"),
                 "sub_group_of": g.get("sub_group_of"),
                 "gkv_section": g.get("gkv_section", "neutral")}
                for g in first_bwa.get("groups", [])
            ]
            group_template = _insert_missing_parents(group_template)
        elif first_bwa:
            group_template = [
                {"name": p["name"], "type": p.get("type", "neutral"),
                 "sub_group_of": None, "gkv_section": "neutral"}
                for p in first_bwa.get("positions", [])
            ]
        else:
            group_template = []

    # 3. Daten pro Gruppe sammeln
    groups_by_name: dict[str, dict] = {
        g["name"]: {
            "name": g["name"],
            "type": g["type"],
            "sub_group_of": g["sub_group_of"],
            "gkv_section": g.get("gkv_section", "neutral"),
            "_synthetic_parent": g.get("_synthetic_parent", False),
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
        _ingest_bwa(doc, col_idx, groups_by_name, group_template)

    # 6. Vorjahreswerte aus JAs einsortieren (Cross-Year-Check)
    _apply_previous_year_values(ja_docs, columns, groups_by_name, questions)

    # 7. PDF-JUE pro Spalte sammeln (Plausibilitaets-Anker)
    pdf_jue_per_column = _collect_pdf_jue(ja_docs, columns, questions)

    # 8. Gruppen finalisieren: accounts_by_key → accounts list
    groups_out = []
    for g_tpl in group_template:
        g = groups_by_name[g_tpl["name"]]
        accounts = [g["accounts_by_key"][k] for k in g["account_order"]]
        groups_out.append({
            "name": g["name"],
            "type": g["type"],
            "sub_group_of": g["sub_group_of"],
            "gkv_section": g.get("gkv_section", "neutral"),
            "column_sums": g["column_sums"],
            "accounts": accounts,
        })

    # 8.5 Sign-Normalisierung: wenn eine Spalte vorzeichen-invertiert vs. der
    # Mehrheit ist (Claude-Halluzination, typisch durch Suffix-Minus an Übertrags-
    # Zwischensummen mehrseitiger Tabellen), Werte der Outlier-Spalte invertieren.
    # Mathematisch JÜ-neutral, visuell konsistent zwischen Spalten.
    _normalize_column_signs(columns, groups_out)

    # 9. Endwert-Label aus dem juengsten JA (z.B. "Steuerlicher Gewinn nach
    # §4 Abs 3 EStG" bei EÜR, default "Jahresüberschuss"). Steuert die
    # Beschriftung des Plausibilitaets-Ankers im Excel.
    endwert_label = None
    if ja_docs:
        endwert_label = newest_ja.get("endwert_label")

    return {"columns": columns, "groups": groups_out, "questions": questions,
            "pdf_jue_per_column": pdf_jue_per_column,
            "endwert_label": endwert_label}


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
        default_label = f"Susa {y}" if d.get("type") == "susa" else f"BWA {y}"
        entries.append((y, 1, {
            "label": d.get("period_label") or default_label,
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
        if d.get("year") != col["year"]:
            continue
        default_label = (f"Susa {d.get('year')}" if d.get("type") == "susa"
                         else f"BWA {d.get('year')}")
        if (d.get("period_label") or default_label) == col["label"]:
            return d
    return None


def _ingest_ja(doc: dict, col_idx: int, year: int, groups_by_name: dict,
               group_template: list, questions: list, newest_ja_year: int | None):
    """Integriere ein JA-Dokument in die Konsolidierung. Cross-Year-Matching:
    1. exakter Gruppen-Name
    2. fuzzy-normalized Name
    3. gkv_section (Top-Level-Gruppe der Sektion bevorzugt)
    4. konto_nr (Konto landet in der Gruppe wo seine Nr schon existiert)
    Nur wenn alle Heuristiken scheitern UND es Konten ohne bekannte Nr gibt,
    wird die Gruppe ans Ende angehaengt."""
    nr_to_group: dict[str, str] = {}
    for gname, g in groups_by_name.items():
        for acc in g["accounts_by_key"].values():
            nr = acc.get("konto_nr")
            if nr:
                nr_to_group[str(nr)] = gname

    norm_to_tpl: dict[str, str] = {
        _norm_group_name(gname): gname for gname in groups_by_name
    }

    # gkv_section -> Top-Level-Gruppe der Sektion (Sub-Gruppe nur als Fallback).
    # Synthetic Parents (von _insert_missing_parents erzeugt, kein eigener
    # Konten-Träger im Template-Doc) werden de-prioritisiert: ihre realen Subs
    # gewinnen, sonst gibt's Doppelzählung wenn ältere JAs flach geliefert
    # werden (z.B. "Aufwendungen für RHB" ohne Numerierung) und das Template
    # geschachtelt ist (z.B. "4. Materialaufwand" → "4. a) Aufwendungen für RHB").
    section_to_tpl: dict[str, str] = _build_section_to_tpl(groups_by_name)

    for g in doc.get("groups", []):
        gname = g["name"]
        gnorm = _norm_group_name(gname)
        gsection = g.get("gkv_section")
        target_name = (
            gname if gname in groups_by_name
            else norm_to_tpl.get(gnorm)
            or (section_to_tpl.get(gsection) if gsection else None)
        )
        if target_name is None:
            # Pruefen ob alle Konten via konto_nr schon woanders existieren
            accs = g.get("accounts", [])
            unrouted = [acc for acc in accs
                         if not (acc.get("konto_nr") and
                                 str(acc["konto_nr"]) in nr_to_group)]
            # Auch Summen-only-GuV-Positionen (accs=0, nur pdf_sum) brauchen eine
            # eigene Gruppe — sonst faellt ihr Wert still raus, wenn der juengste
            # JA (Template-Quelle) die Position nicht hat (z.B. Zinsaufwand=0 ->
            # von Claude weggelassen). Real: Prisma JA2022/2023 Zinsaufwand
            # 763,69 / VJ 430,40 -> JUE sonst um genau diesen Betrag zu hoch.
            has_guv_summary = (
                (g.get("pdf_sum_gj") is not None or g.get("pdf_sum_vj") is not None)
                and gsection and gsection != "neutral"
            )
            if unrouted or has_guv_summary:
                # Konten ohne Anker ODER GuV-Summen-Position -> Gruppe anlegen
                target_name = gname
                new_tpl = {
                    "name": gname,
                    "type": g.get("type", "neutral"),
                    "sub_group_of": g.get("sub_group_of"),
                    "gkv_section": gsection or "neutral",
                }
                group_template.append(new_tpl)
                groups_by_name[gname] = {
                    "name": gname,
                    "type": new_tpl["type"],
                    "sub_group_of": new_tpl["sub_group_of"],
                    "gkv_section": new_tpl["gkv_section"],
                    "column_sums": {},
                    "accounts_by_key": {},
                    "account_order": [],
                }
                norm_to_tpl[gnorm] = gname
                if gsection and gsection != "neutral" and gsection not in section_to_tpl:
                    section_to_tpl[gsection] = gname
            # else: target_name bleibt None und Konten werden per konto_nr geroutet

        # Gruppen-Summe (PDF) auf Ziel-Gruppe schreiben
        if target_name and g.get("pdf_sum_gj") is not None:
            groups_by_name[target_name]["column_sums"][col_idx] = g["pdf_sum_gj"]

        for acc in g.get("accounts", []):
            nr = acc.get("konto_nr")
            nr_key = str(nr) if nr else None
            # Gruppen-Routing ist authoritativ (Claude hat die Gruppe explizit
            # gewählt). konto_nr-Routing nur als Fallback wenn keine Zielgruppe
            # ermittelbar war.
            acc_target = target_name or (nr_to_group.get(nr_key) if nr_key else None)
            if acc_target is None:
                continue
            target = groups_by_name[acc_target]
            key = _acc_key(acc, acc_target)
            if key not in target["accounts_by_key"]:
                target["accounts_by_key"][key] = {
                    "konto_nr": acc.get("konto_nr"),
                    "bezeichnung": acc.get("bezeichnung", ""),
                    "values": {},
                    "confidence": acc.get("confidence", "high"),
                }
                target["account_order"].append(key)
                if nr_key:
                    nr_to_group[nr_key] = acc_target
            betrag = _normalize_bestand_value(g, acc.get("betrag_gj"))
            target["accounts_by_key"][key]["values"][col_idx] = betrag

        # Konten-Summe ist authoritativ — pdf_sum_gj kann von Claude erfunden
        # sein (z.B. Übertrag-Doppelzählung bei mehrseitigen Tabellen). Wir
        # vertrauen den einzeln nachvollziehbaren Konto-Werten und ignorieren
        # einen Mismatch zur PDF-Summe stillschweigend.

    # open_questions übertragen — Claude liefert manchmal Strings statt Dicts
    # (z.B. "Diese PDF ist eine EÜR ohne erkennbare GuV-Gruppen").
    # Solche Hinweise als hint-only-Eintraege behandeln, damit der Job nicht crasht.
    for oq in doc.get("open_questions", []):
        if isinstance(oq, str):
            questions.append({"type": "unmatched_account", "year": year,
                              "konto_nr": None, "bezeichnung": None,
                              "betrag_gj": None, "hint": oq})
            continue
        questions.append({
            "type": "unmatched_account", "year": year,
            "konto_nr": oq.get("konto_nr"),
            "bezeichnung": oq.get("bezeichnung"),
            "betrag_gj": oq.get("betrag_gj"),
            "hint": oq.get("hint", ""),
        })


def _ingest_bwa(doc: dict, col_idx: int, groups_by_name: dict,
                group_template: list):
    """BWA wird wie JA behandelt: groups mit accounts. Routing-Reihenfolge:
    1. Per Kontonummer in bestehendes JA-Konto mergen
    2. Per Gruppen-Name (exact oder fuzzy ohne Nummerierung) in bestehende JA-Gruppe
    3. Sonst: Gruppe wird als neue Top-Level-Gruppe ans Ende angehängt

    So geht kein Wert verloren -- weder Werte mit unbekannter Konto-Nr noch
    BWA-only-Gruppen wie 'Sonstige Zinserträge' die in der JA fehlen.
    """
    # Legacy-Struktur (flache positions) — behalten wir für Kompatibilität
    if not doc.get("groups") and doc.get("positions"):
        for p in doc["positions"]:
            target = groups_by_name.get(p["name"])
            if target is not None:
                target["column_sums"][col_idx] = p.get("betrag")
        return

    # Index 1: konto_nr → group_name (für JA-übergreifendes Konten-Matching)
    nr_to_group: dict[str, str] = {}
    for gname, g in groups_by_name.items():
        for acc in g["accounts_by_key"].values():
            nr = acc.get("konto_nr")
            if nr:
                nr_to_group[str(nr)] = gname

    # Index 2: normalized name → group_name (für fuzzy Gruppennamen-Matching)
    norm_to_group: dict[str, str] = {
        _norm_group_name(gname): gname for gname in groups_by_name
    }

    for g in doc.get("groups", []):
        bwa_gname = g["name"]
        bwa_norm = _norm_group_name(bwa_gname)

        # Resolve target group: exact name → fuzzy name → create new
        if bwa_gname in groups_by_name:
            matched_gname = bwa_gname
        elif bwa_norm in norm_to_group:
            matched_gname = norm_to_group[bwa_norm]
        else:
            # New BWA-only group — append to template + index
            matched_gname = bwa_gname
            new_tpl = {
                "name": bwa_gname,
                "type": g.get("type", "neutral"),
                "sub_group_of": g.get("sub_group_of"),
                "gkv_section": g.get("gkv_section", "neutral"),
            }
            group_template.append(new_tpl)
            groups_by_name[bwa_gname] = {
                "name": bwa_gname,
                "type": new_tpl["type"],
                "sub_group_of": new_tpl["sub_group_of"],
                "gkv_section": new_tpl["gkv_section"],
                "column_sums": {},
                "accounts_by_key": {},
                "account_order": [],
            }
            norm_to_group[bwa_norm] = bwa_gname

        # Gruppen-Summe auf die Zielgruppe schreiben
        if g.get("pdf_sum_gj") is not None:
            groups_by_name[matched_gname]["column_sums"][col_idx] = g["pdf_sum_gj"]

        for acc in g.get("accounts", []):
            nr = acc.get("konto_nr")
            nr_key = str(nr) if nr else None
            # Konto existiert schon irgendwo → in dessen Gruppe mergen,
            # sonst in die per Name geroutete Zielgruppe
            target_gname = nr_to_group.get(nr_key) if nr_key else None
            if target_gname is None:
                target_gname = matched_gname
            target = groups_by_name[target_gname]
            key = _acc_key(acc, target_gname)
            if key not in target["accounts_by_key"]:
                target["accounts_by_key"][key] = {
                    "konto_nr": nr,
                    "bezeichnung": acc.get("bezeichnung", ""),
                    "values": {},
                    "confidence": acc.get("confidence", "high"),
                }
                target["account_order"].append(key)
                if nr_key:
                    nr_to_group[nr_key] = target_gname
            target["accounts_by_key"][key]["values"][col_idx] = acc.get("betrag_gj")


def _apply_previous_year_values(ja_docs: list[dict], columns: list[dict],
                                 groups_by_name: dict, questions: list):
    """Trage VJ-Werte in die Spalte fuer year n-1 ein. Routing-Reihenfolge wie
    _ingest_ja: konto_nr (authoritativ) -> exact name -> normalized -> gkv_section.
    Werte werden nur eingetragen wenn die Spalte noch leer ist; bei Konflikt
    mit dem Eigenwert -> Fragen-Sheet."""
    nr_to_group: dict[str, str] = {}
    for gname, g in groups_by_name.items():
        for acc in g["accounts_by_key"].values():
            nr = acc.get("konto_nr")
            if nr:
                nr_to_group[str(nr)] = gname

    norm_to_tpl: dict[str, str] = {
        _norm_group_name(gname): gname for gname in groups_by_name
    }
    section_to_tpl: dict[str, str] = _build_section_to_tpl(groups_by_name)

    for doc in ja_docs:
        vj = doc.get("previous_year")
        if vj is None:
            continue
        vj_col_idx = None
        for idx, col in enumerate(columns):
            if col["kind"] == "ja" and col["year"] == vj:
                vj_col_idx = idx
                break
        if vj_col_idx is None:
            continue

        for g in doc.get("groups", []):
            gname = g["name"]
            gnorm = _norm_group_name(gname)
            gsection = g.get("gkv_section")
            grp_target_name = (
                gname if gname in groups_by_name
                else norm_to_tpl.get(gnorm)
                or (section_to_tpl.get(gsection) if gsection else None)
            )
            # Gruppen-Summe (PDF-VJ) auf Ziel-Gruppe schreiben — für DATEV-
            # Rohergebnis-Format-JAs ohne Einzelkonten ist das die einzige
            # Quelle für VJ-Werte in der VJ-Spalte. setdefault, damit ein
            # bereits gesetzter Eigenjahres-column_sum (aus _ingest_ja) nicht
            # überschrieben wird.
            if grp_target_name and g.get("pdf_sum_vj") is not None:
                groups_by_name[grp_target_name]["column_sums"].setdefault(
                    vj_col_idx, g["pdf_sum_vj"])
            for acc in g.get("accounts", []):
                if acc.get("betrag_vj") is None:
                    continue
                nr = acc.get("konto_nr")
                nr_key = str(nr) if nr else None
                acc_target = grp_target_name or (nr_to_group.get(nr_key) if nr_key else None)
                if acc_target is None:
                    continue
                target = groups_by_name[acc_target]
                key = _acc_key(acc, acc_target)
                if key not in target["accounts_by_key"]:
                    target["accounts_by_key"][key] = {
                        "konto_nr": acc.get("konto_nr"),
                        "bezeichnung": acc.get("bezeichnung", ""),
                        "values": {},
                        "confidence": acc.get("confidence", "high"),
                    }
                    target["account_order"].append(key)
                    if nr_key:
                        nr_to_group[nr_key] = acc_target
                # Eigenjahres-Wert ist authoritativ. Bei Mismatch zur VJ-Spalte
                # eines anderen JAs (klassische STB-Vorzeichen-Inversion) lassen
                # wir den existing-Wert stehen, ohne den User zu belästigen.
                vj_val = _normalize_bestand_value(g, acc["betrag_vj"])
                target["accounts_by_key"][key]["values"].setdefault(vj_col_idx, vj_val)


def _collect_pdf_jue(ja_docs: list[dict], columns: list[dict],
                      questions: list) -> dict[int, float]:
    """Sammle PDF-Jahresueberschuss pro Spalte aus den JA-Extraktionen.
    Eigenjahr-Wert ueberschreibt Vorjahres-Verweise; bei Mismatch zwischen
    den zwei Quellen wird ein Question-Eintrag erzeugt."""
    out: dict[int, float] = {}
    sources: dict[int, str] = {}  # column_idx → "own" | "previous"
    for doc in ja_docs:
        own_year = doc.get("year")
        prev_year = doc.get("previous_year")
        own_jue = doc.get("pdf_jahresueberschuss_gj")
        prev_jue = doc.get("pdf_jahresueberschuss_vj")
        for idx, col in enumerate(columns):
            if col["kind"] != "ja":
                continue
            # Eigenjahr-Quelle ist immer authoritativ
            if col["year"] == own_year and own_jue is not None:
                if idx in out and sources.get(idx) == "previous":
                    if abs(out[idx] - own_jue) > MISMATCH_TOLERANCE:
                        questions.append({
                            "type": "pdf_jue_previous_year_mismatch",
                            "year": col["year"],
                            "from_doc_year": own_year,
                            "own_value": own_jue,
                            "pdf_says": out[idx],
                        })
                out[idx] = own_jue
                sources[idx] = "own"
            elif col["year"] == prev_year and prev_jue is not None:
                # Cross-Check: Wenn die Vorjahres-Aussage von doc24 zur
                # Eigenjahres-Aussage von doc23 differiert -> Mismatch loggen.
                # Der Eigenjahres-Wert bleibt authoritativ, der VJ-Wert
                # dient nur dem Cross-Check.
                if idx in out and abs(out[idx] - prev_jue) > MISMATCH_TOLERANCE:
                    questions.append({
                        "type": "pdf_jue_previous_year_mismatch",
                        "year": col["year"],
                        "from_doc_year": own_year,
                        "own_value": out[idx],
                        "pdf_says": prev_jue,
                    })
                if sources.get(idx) != "own":
                    out[idx] = prev_jue
                    sources[idx] = "previous"
    return out


def _build_section_to_tpl(groups_by_name: dict[str, dict]) -> dict[str, str]:
    """Mapping gkv_section -> Gruppen-Name fuer section-basiertes Routing.

    Priorisierung pro Section:
    1. Reales Top-Level mit eigener Konten-Erwartung
    2. Sub-Group (spezifischer als ein synthetisches Parent)
    3. Synthetisches Top-Level (nur als letzter Ausweg)

    Damit landen Konten aus flach gelieferten JAs (z.B. "Aufwendungen für RHB"
    ohne Numerierung) konsistent in der Sub des hierarchischen Templates
    (z.B. "4. a) Aufwendungen für RHB" unter "4. Materialaufwand"), statt im
    synthetischen Parent — was sonst Doppelzählung produzieren würde wenn
    spätere JAs die Sub-Group exakt benennen.
    """
    out: dict[int, dict[str, str]] = {0: {}, 1: {}, 2: {}}
    # rank: 0 = real top, 1 = sub, 2 = synthetic top
    for gname, g in groups_by_name.items():
        sec = g.get("gkv_section")
        if not sec or sec == "neutral":
            continue
        is_top = g.get("sub_group_of") is None
        is_synthetic = g.get("_synthetic_parent", False)
        if is_top and not is_synthetic:
            rank = 0
        elif not is_top:
            rank = 1
        else:
            rank = 2
        out[rank].setdefault(sec, gname)
    final: dict[str, str] = {}
    for rank in (0, 1, 2):
        for sec, gname in out[rank].items():
            final.setdefault(sec, gname)
    return final


def _infer_column_polarity(col_idx: int, groups: list[dict]) -> str | None:
    """Polarität einer Spalte aus den Aufwand/Steuer-Werten ableiten.

    Returns 'pos' wenn Aufwand-Summe > 0 (= expenses_positive Konvention),
    'neg' wenn < 0 (= expenses_negative), None wenn keine Aufwand-Daten.
    Spiegelt die Heuristik aus builder._infer_sign_conventions, aber auf
    der konsolidierten Struktur (vor JSONB-Persistierung)."""
    samples: list[float] = []
    for g in groups:
        if g.get("type") not in ("aufwand", "steuer"):
            continue
        for acc in g.get("accounts", []):
            v = acc.get("values", {}).get(col_idx)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if abs(v) < 0.01:
                continue
            samples.append(v)
    if not samples:
        return None
    return "neg" if sum(samples) < 0 else "pos"


def _normalize_column_signs(columns: list[dict], groups: list[dict]) -> None:
    """Outlier-Spalten mit invertierter Vorzeichen-Konvention erkennen und
    in-place auf die Mehrheits-Konvention normalisieren.

    Hintergrund: Claude halluziniert in seltenen Fällen die Vorzeichen einer
    ganzen Spalte (z.B. Tasteone 2022, getriggert durch Suffix-Minus an
    Übertrags-Zwischensummen mehrseitiger Tabellen). Symptom: Aufwände
    negativ, Skonti/Boni positiv — also komplett gespiegelte Spalte.

    Strategie: pro Spalte die Polarität ermitteln. Wenn eine Mehrheit klar
    eine Konvention zeigt (>50% und >=2 Spalten Konsens) und einzelne Spalten
    abweichen, alle Werte der Outlier-Spalten ×−1. JÜ bleibt mathematisch
    korrekt; Sign-Inferenz im Builder sieht danach einheitliche Konvention.

    Greift NICHT bei zweideutigen Fällen (1 Spalte, 2 Spalten gleich verteilt)
    — dort bleibt Status quo.
    """
    polarities: dict[int, str] = {}
    for col_idx in range(len(columns)):
        p = _infer_column_polarity(col_idx, groups)
        if p is not None:
            polarities[col_idx] = p

    if len(polarities) < 2:
        return  # zu wenig Signal

    pos_cols = [i for i, p in polarities.items() if p == "pos"]
    neg_cols = [i for i, p in polarities.items() if p == "neg"]
    total = len(polarities)
    if len(pos_cols) > total / 2:
        majority, outliers = "pos", neg_cols
    elif len(neg_cols) > total / 2:
        majority, outliers = "neg", pos_cols
    else:
        return  # keine echte Mehrheit

    if not outliers:
        return

    for col_idx in outliers:
        for g in groups:
            for acc in g.get("accounts", []):
                v = acc.get("values", {}).get(col_idx)
                if isinstance(v, (int, float)):
                    acc["values"][col_idx] = -v
            cs = (g.get("column_sums") or {}).get(col_idx)
            if isinstance(cs, (int, float)):
                g["column_sums"][col_idx] = -cs
        # Spalten-Annotation aktualisieren, damit der Builder die richtige
        # Konvention sieht.
        columns[col_idx]["sign_convention"] = (
            "expenses_positive" if majority == "pos" else "expenses_negative"
        )


def _insert_missing_parents(tpl: list[dict]) -> list[dict]:
    """Ensure every sub_group_of reference has a real parent group in the list.
    If a parent is missing, synthesize it directly before the first child.
    Synthetic parents are marked with `_synthetic_parent=True` so the section-
    routing heuristics can de-prioritise them against their real subs."""
    existing = {g["name"] for g in tpl}
    out: list[dict] = []
    inserted: set[str] = set()
    for g in tpl:
        parent = g.get("sub_group_of")
        if parent and parent not in existing and parent not in inserted:
            # Inherit type + gkv_section from the first sub with that parent
            out.append({
                "name": parent,
                "type": g.get("type", "neutral"),
                "sub_group_of": None,
                "gkv_section": g.get("gkv_section", "neutral"),
                "_synthetic_parent": True,
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
