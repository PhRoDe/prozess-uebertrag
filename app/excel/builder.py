"""Excel-Builder — erzeugt die Übertrag-Excel basierend auf der
dynamischen Gruppenstruktur aus der Konsolidierung.

Layout:
- Header: Konto | Bezeichnung | Jahr-1 | Jahr-2 | BWA xyz | ...
- Pro Gruppe:
    * Summen-Zeile (fett) OBEN: Name + SUM-Formel über Detail-Zeilen (für JAs)
      bzw. direkter Wert (für BWAs)
    * Details: alle Konten darunter
- Nach allen Gruppen: `Jahresergebnis` als Formel
  (respektiert sign_convention + Gruppen-Typen)
- Zweites Sheet `Fragen`: Data-Quality-Issues
"""
import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

EUR_FORMAT = '#,##0.00;[Red]-#,##0.00'
YELLOW = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
BOLD = Font(bold=True)
BOLD_LIGHT = Font(bold=True, color="555555")


def build_excel(consolidated: dict, review_answers: dict | None = None) -> bytes:
    """Build the final xlsx bytes from the consolidated structure.

    review_answers: optional mapping {konto_nr -> target_group_name} for accounts
                    that landed in open_questions.
    """
    review_answers = review_answers or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Übertrag"

    columns = consolidated.get("columns", [])
    groups = consolidated.get("groups", [])

    # _apply_review routed open-question accounts into named groups
    groups = _apply_review_answers(groups, consolidated.get("questions", []),
                                    review_answers)

    # "Verminderung" wirkt semantisch mindernd im Jahresergebnis. Die Behandlung
    # passiert direkt in der JÜ-Formel (immer subtrahieren), nicht über type.
    groups = _reclassify_bestandsveraenderung(groups)

    # Sign-convention aus ECHTEN Aufwands-Werten ableiten, statt Claude zu vertrauen.
    columns = _infer_sign_conventions(columns, groups)

    # Header row
    ws.cell(row=1, column=1, value="Konto").font = BOLD
    ws.cell(row=1, column=2, value="Bezeichnung").font = BOLD
    for col_idx, col in enumerate(columns):
        c = ws.cell(row=1, column=3 + col_idx, value=col["label"])
        c.font = BOLD

    # Pre-compute: which groups are parents of sub-groups
    children_by_parent: dict[str, list[str]] = {}
    for g in groups:
        parent = g.get("sub_group_of")
        if parent:
            children_by_parent.setdefault(parent, []).append(g["name"])

    # Rows
    group_sum_rows: dict[str, int] = {}  # group_name -> row
    row_cursor = 2

    for g in groups:
        # Summen-Zeile zuerst
        sum_row = row_cursor
        group_sum_rows[g["name"]] = sum_row
        is_sub = g.get("sub_group_of") is not None
        label = ("  " if is_sub else "") + g["name"]
        cell_label = ws.cell(row=sum_row, column=2, value=label)
        cell_label.font = BOLD if not is_sub else BOLD_LIGHT

        has_children = g["name"] in children_by_parent and not g.get("accounts")

        # Accounts
        detail_start = row_cursor + 1
        detail_end = detail_start - 1
        for acc in g.get("accounts", []):
            row_cursor += 1
            ws.cell(row=row_cursor, column=1, value=acc.get("konto_nr") or "")
            ws.cell(row=row_cursor, column=2, value=f"  {acc.get('bezeichnung', '')}")
            for col_idx in range(len(columns)):
                v = acc.get("values", {}).get(col_idx)
                c = ws.cell(row=row_cursor, column=3 + col_idx, value=v)
                c.number_format = EUR_FORMAT
                if acc.get("confidence") == "low":
                    c.fill = YELLOW
            detail_end = row_cursor

        # Summen-Zeile befüllen
        for col_idx, col in enumerate(columns):
            target_col = 3 + col_idx
            col_letter = get_column_letter(target_col)
            has_details = detail_end >= detail_start
            if has_details:
                # Gruppe hat Konten — SUM-Formel (gilt für JA *und* BWA,
                # seit BWA auch Einzelkonten liefert)
                formula = f"=SUM({col_letter}{detail_start}:{col_letter}{detail_end})"
                c = ws.cell(row=sum_row, column=target_col, value=formula)
            elif col["kind"] == "bwa":
                # BWA ohne Konten für diese Gruppe → direkter Wert aus column_sums
                bwa_val = g.get("column_sums", {}).get(col_idx)
                c = ws.cell(row=sum_row, column=target_col, value=bwa_val)
            elif has_children:
                # Parent-Gruppe → SUM über Sub-Summen (Pass 2)
                c = ws.cell(row=sum_row, column=target_col, value=None)
            else:
                c = ws.cell(row=sum_row, column=target_col, value=0)
            c.number_format = EUR_FORMAT
            c.font = BOLD if not is_sub else BOLD_LIGHT

        row_cursor += 1

    # Pass 2: Parent-Gruppen-Summen, die über Sub-Summen gehen
    for parent_name, children_names in children_by_parent.items():
        parent_row = group_sum_rows.get(parent_name)
        if parent_row is None:
            continue
        for col_idx, col in enumerate(columns):
            target_col = 3 + col_idx
            col_letter = get_column_letter(target_col)
            # Wenn Zelle schon einen Wert hat (BWA-Fall), nicht überschreiben
            current = ws.cell(row=parent_row, column=target_col).value
            if current is not None:
                continue
            child_refs = [f"{col_letter}{group_sum_rows[cn]}"
                          for cn in children_names if cn in group_sum_rows]
            if not child_refs:
                formula = "=0"
            else:
                formula = "=" + "+".join(child_refs)
            c = ws.cell(row=parent_row, column=target_col, value=formula)
            c.number_format = EUR_FORMAT
            c.font = BOLD

    # Leerzeile
    row_cursor += 1

    # Jahresergebnis-Zeile: Summe aller Top-Level-Gruppen (sub_group_of is None)
    # unter Berücksichtigung von Gruppen-Typ und sign_convention der jeweiligen Spalte.
    je_row = row_cursor
    ws.cell(row=je_row, column=2, value="Jahresergebnis").font = BOLD
    for col_idx, col in enumerate(columns):
        target_col = 3 + col_idx
        col_letter = get_column_letter(target_col)
        conv = col.get("sign_convention", "expenses_negative")
        parts_plus: list[int] = []
        parts_minus: list[int] = []
        for g in groups:
            if g.get("sub_group_of") is not None:
                continue  # nur Top-Level summieren
            gtype = g.get("type", "neutral")
            if gtype == "bilanz":
                continue  # Gewinnvortrag/Bilanzgewinn sind NICHT Teil des JÜ
            sum_r = group_sum_rows.get(g["name"])
            if sum_r is None:
                continue
            # Spezialfall: Verminderung des Bestandes wirkt IMMER mindernd auf
            # den JÜ. Der Wert kann positiv (Bestand sank) oder negativ (Bestand
            # stieg) sein — in beiden Fällen ist JÜ-Beitrag = -Wert. Subtraktion
            # macht das semantisch korrekt unabhängig vom Vorzeichen.
            name_lc = (g.get("name") or "").lower()
            is_verminderung = "verminderung" in name_lc and "bestand" in name_lc
            if is_verminderung:
                parts_minus.append(sum_r)
                continue

            if conv == "expenses_negative":
                # Alles addieren — Aufwände sind eh negativ
                parts_plus.append(sum_r)
            else:
                # Erträge plus, Aufwände/Steuern minus
                if gtype == "ertrag":
                    parts_plus.append(sum_r)
                elif gtype in ("aufwand", "steuer"):
                    parts_minus.append(sum_r)
                else:
                    parts_plus.append(sum_r)
        if not parts_plus and not parts_minus:
            formula = "=0"
        else:
            plus = "+".join(f"{col_letter}{r}" for r in parts_plus) or "0"
            minus = ""
            if parts_minus:
                minus = "-" + "-".join(f"{col_letter}{r}" for r in parts_minus)
            formula = f"={plus}{minus}"
        c = ws.cell(row=je_row, column=target_col, value=formula)
        c.number_format = EUR_FORMAT
        c.font = BOLD

    # Spaltenbreiten
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 55
    for idx in range(len(columns)):
        ws.column_dimensions[get_column_letter(3 + idx)].width = 14

    # Fragen-Sheet
    fragen = wb.create_sheet("Fragen")
    fragen.append(["Thema", "Details"])
    for q in consolidated.get("questions", []):
        details = _format_question(q)
        fragen.append([q.get("type", ""), details])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _reclassify_bestandsveraenderung(groups: list[dict]) -> list[dict]:
    """Korrigiert semantische Missklassifikationen aus der Claude-Extraktion
    und filtert Nicht-GuV-Positionen aus.

    GKV §275 Pos 2: 'Erhöhung ODER Verminderung des Bestandes an fertigen und
    unfertigen Erzeugnissen'. PDFs labeln das oft als 'Verminderung' mit positivem
    Wert — dann ist die Position real ein Aufwand.

    'Gewinnvortrag', 'Verlustvortrag', 'Bilanzgewinn', 'Bilanzverlust',
    'Ausschüttung' sind Eigenkapital-Bewegungen (Bilanzgewinn-Rechnung), NICHT
    Teil der GuV. Der Uebertrag zeigt nur die GuV → diese Gruppen komplett
    ausfiltern.
    """
    out = []
    for g in groups:
        name_lc = (g.get("name") or "").lower()
        # Bilanz-Positionen komplett ausfiltern (nicht Teil der GuV)
        if any(k in name_lc for k in ["gewinnvortrag", "verlustvortrag",
                                       "bilanzgewinn", "bilanzverlust",
                                       "ausschüttung"]):
            continue
        if "verminderung" in name_lc and "bestand" in name_lc:
            out.append({**g, "type": "aufwand"})
        elif "erhöhung" in name_lc and "bestand" in name_lc:
            out.append({**g, "type": "ertrag"})
        else:
            out.append(g)
    return out


def _normalize_verminderung_signs(groups: list[dict],
                                    columns: list[dict]) -> list[dict]:
    """Verminderung-Bestand-Werte ans Vorzeichen-Schema der Spalte anpassen.

    Claude extrahiert Aufwände manchmal positiv (typisch GuV-Layout) und
    manchmal negativ (Layout mit Minus-Endung). Innerhalb einer Spalte sollte
    die Konvention konsistent sein, damit die JÜ-Formel funktioniert. Bei
    Verminderung-Bestand stimmt das Vorzeichen oft nicht mit den anderen
    Aufwänden derselben Spalte überein → wir flippen es an.
    """
    out = []
    for g in groups:
        nl = (g.get("name") or "").lower()
        if not ("verminderung" in nl and "bestand" in nl):
            out.append(g)
            continue
        new_accounts = []
        for acc in g.get("accounts", []):
            new_values = {}
            for col_idx, v in (acc.get("values") or {}).items():
                if not isinstance(v, (int, float)) or v == 0:
                    new_values[col_idx] = v
                    continue
                if col_idx >= len(columns):
                    new_values[col_idx] = v
                    continue
                conv = columns[col_idx].get("sign_convention", "expenses_negative")
                # expenses_negative: Aufwände sollten negativ sein → positiv flippen
                # expenses_positive: Aufwände sollten positiv sein → negativ flippen
                if (conv == "expenses_negative" and v > 0) or \
                   (conv == "expenses_positive" and v < 0):
                    new_values[col_idx] = -v
                else:
                    new_values[col_idx] = v
            new_accounts.append({**acc, "values": new_values})
        out.append({**g, "accounts": new_accounts})
    return out


def _infer_sign_conventions(columns: list[dict], groups: list[dict]) -> list[dict]:
    """Leite sign_convention pro Spalte aus den echten Aufwand/Steuer-Werten ab.
    Mehrheit negativ → expenses_negative (einfache Summen-Formel fürs Jahresergebnis).
    Mehrheit positiv → expenses_positive (Aufwände müssen subtrahiert werden)."""
    out = []
    for col_idx, col in enumerate(columns):
        new_col = dict(col)
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
        if samples:
            # Summe statt Anzahl: einzelne Skonto-Korrekturen (viele kleine
            # negative Werte) duerfen die grossen Hauptaufwand-Werte nicht
            # ueberstimmen.
            total = sum(samples)
            new_col["sign_convention"] = (
                "expenses_negative" if total < 0 else "expenses_positive"
            )
        out.append(new_col)
    return out


def _apply_review_answers(groups: list[dict], questions: list[dict],
                          review_answers: dict) -> list[dict]:
    """Route accounts from open_questions into the user-chosen group."""
    if not review_answers:
        return groups

    # Index für schnelles Gruppen-Lookup
    groups_by_name = {g["name"]: g for g in groups}

    for q in questions:
        if q.get("type") != "unmatched_account":
            continue
        konto_nr = q.get("konto_nr")
        if not konto_nr or konto_nr not in review_answers:
            continue
        target_name = review_answers[konto_nr]
        target = groups_by_name.get(target_name)
        if target is None:
            continue
        # Wir haben hier nur einen Einzelwert (betrag_gj). Column-Index 0 annehmen
        # wenn Spalten bekannt sind — aber hier fehlt der Kontext. Review-Accounts
        # werden aktuell nur mit betrag_gj aus dem Dokument der jüngsten JA geroutet.
        # TODO: falls Multi-Jahr-Review nötig, müsste q eine column-Liste mitbringen.
        acc_key = f"reviewed:{konto_nr}"
        acc = {
            "konto_nr": konto_nr,
            "bezeichnung": q.get("bezeichnung", ""),
            "values": {0: q.get("betrag_gj")},
            "confidence": "reviewed",
        }
        target.setdefault("accounts", []).append(acc)

    return groups


def _format_question(q: dict) -> str:
    t = q.get("type", "")
    if t == "previous_year_mismatch":
        return (f"Gruppe {q.get('group')} · Konto {q.get('konto_nr')} · "
                f"Jahr {q.get('year')}: "
                f"PDF {q.get('from_doc_year')} sagt {q.get('pdf_says')}, "
                f"eigene Extraktion {q.get('own_value')}")
    if t == "group_sum_mismatch":
        return (f"Gruppe {q.get('group')} · Jahr {q.get('year')}: "
                f"PDF-Summe {q.get('pdf_says')} ≠ Konten-Summe {q.get('accounts_sum')}")
    if t == "unmatched_account":
        return (f"Konto {q.get('konto_nr') or '(ohne Nr)'} · "
                f"'{q.get('bezeichnung')}' · Jahr {q.get('year')} · "
                f"Betrag {q.get('betrag_gj')} — {q.get('hint', '')}")
    return str(q)
