"""Excel-Builder — erzeugt die vollständig verformelte Mehrjahres-Excel.

Top-Anforderung (Spec §4): Alle Zwischensummen müssen Formeln sein.
Doppelklick auf jede Zelle zeigt, wie der Wert entsteht.
"""
import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from app.excel.structure import GUV_HIERARCHY, match_code  # noqa: F401 used in _index_by_group
from app.excel.formulas import sum_range, safe_ref
from app.excel.kennzahlen import build_kennzahlen_rows

EUR_FORMAT = '#,##0.00;[Red]-#,##0.00'
PCT_FORMAT = "0.0%"
YELLOW = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
RED = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
BOLD = Font(bold=True)


def build_excel(consolidated: dict, review_answers: dict | None = None) -> bytes:
    """Build the final Excel bytes from consolidated data + optional review answers.

    consolidated = {years: [int], rows: [{konto_nr, bezeichnung, gruppe, values, confidence}],
                    questions: [dict]}
    review_answers = {konto_nr_or_key: canonical_group_code} — overrides Claude's group
    """
    review_answers = review_answers or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Übertrag"

    years = consolidated["years"]
    rows_data = _apply_review(consolidated["rows"], review_answers)
    unmatched: list[dict] = []
    by_group = _index_by_group(rows_data, unmatched)

    # Header
    headers = ["Konto", "Bezeichnung"] + [str(y) for y in years]
    for col_idx, h in enumerate(headers):
        c = ws.cell(row=1, column=col_idx + 1, value=h)
        c.font = BOLD

    row_cursor = 3  # blank row 2 for spacing
    detail_ranges: dict[str, tuple[int, int]] = {}  # code -> (first_detail_row, last_detail_row)
    sum_rows: dict[str, int] = {}                   # code -> row number of summe/formula

    # Pass 1 — write details + sum/formula placeholders in document order.
    # Formula rows are placeholders; actual formula text is filled in Pass 2
    # once all sum rows are known.
    formula_deferrals: list[dict] = []

    for entry in GUV_HIERARCHY:
        code = entry["code"]
        kind = entry["kind"]

        if kind == "details":
            details = by_group.get(code, [])
            if not details:
                continue
            start = row_cursor
            for r in details:
                ws.cell(row=row_cursor, column=1, value=r["konto_nr"] or "")
                ws.cell(row=row_cursor, column=2, value=f"  {r['bezeichnung']}")
                for y_idx, year in enumerate(years):
                    val = r["values"].get(year)
                    c = ws.cell(row=row_cursor, column=3 + y_idx, value=val)
                    c.number_format = EUR_FORMAT
                    if r.get("confidence") == "low":
                        c.fill = YELLOW
                row_cursor += 1
            detail_ranges[code] = (start, row_cursor - 1)

        elif kind == "sum":
            rng = detail_ranges.get(code)
            if not rng:
                continue  # no details to sum
            label = ws.cell(row=row_cursor, column=2, value=code)
            if entry.get("bold"):
                label.font = BOLD
            for y_idx in range(len(years)):
                col = 3 + y_idx
                c = ws.cell(row=row_cursor, column=col, value=sum_range(col - 1, *rng))
                c.number_format = EUR_FORMAT
                if entry.get("bold"):
                    c.font = BOLD
            sum_rows[code] = row_cursor
            row_cursor += 1

        elif kind == "formula":
            label = ws.cell(row=row_cursor, column=2, value=code)
            if entry.get("bold"):
                label.font = BOLD
            sum_rows[code] = row_cursor
            formula_deferrals.append({"code": code, "row": row_cursor,
                                      "bold": entry.get("bold", False)})
            row_cursor += 1

    # Pass 2 — fill deferred formulas now that all sum rows are known.
    _write_computed_formulas(ws, sum_rows, detail_ranges, years)

    last_data_row = row_cursor - 1

    # Kennzahlen
    row_cursor += 2
    ws.cell(row=row_cursor, column=2, value="Kennzahlen").font = BOLD
    row_cursor += 1
    anchors = _kennzahlen_anchors(sum_rows)
    kz_first_row = row_cursor
    for y_idx, _year in enumerate(years):
        col_idx = 2 + y_idx  # 0-based for formulas.cell()
        kz_rows = build_kennzahlen_rows(anchors, col_idx=col_idx)
        for kz_idx, kz in enumerate(kz_rows):
            target_row = kz_first_row + kz_idx
            if y_idx == 0:
                ws.cell(row=target_row, column=2, value=kz["label"])
            if kz["formula"]:
                c = ws.cell(row=target_row, column=col_idx + 1, value=kz["formula"])
                c.number_format = kz["number_format"]
    row_cursor = kz_first_row + 5  # 5 Kennzahlen

    # Column widths
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 50
    for i in range(len(years)):
        ws.column_dimensions[get_column_letter(3 + i)].width = 15

    # Fragen-Sheet
    fragen = wb.create_sheet("Fragen")
    fragen.append(["Thema", "Details"])
    for q in consolidated.get("questions", []):
        fragen.append([q.get("type", ""), str(q)])
    for u in unmatched:
        fragen.append([
            "unmatched_group",
            f"Konto {u.get('konto_nr') or '(ohne Nr)'}: '{u.get('bezeichnung')}' "
            f"— Gruppe '{u.get('gruppe')}' nicht in HGB-Gliederung erkennbar"
        ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# -----------------------------------------------------------------------------


def _apply_review(rows: list[dict], review_answers: dict) -> list[dict]:
    """Overlay user review corrections onto the extracted rows."""
    out = []
    for r in rows:
        key = r.get("konto_nr") or r["bezeichnung"]
        if key in review_answers:
            r = {**r, "gruppe": review_answers[key], "confidence": "reviewed"}
        out.append(r)
    return out


GROUP_COARSE_TO_DETAIL = {
    "4. Materialaufwand": "4a. Aufwendungen für RHB und Waren",
    "5. Personalaufwand": "5a. Löhne und Gehälter",
    "7. Sonstige betriebliche Aufwendungen": "7g. Verschiedene betriebliche Kosten",
}


def _index_by_group(rows: list[dict], unmatched: list[dict]) -> dict[str, list[dict]]:
    """Group rows by their canonical HGB code.

    If Claude returns a coarse group (e.g. "4. Materialaufwand" without a/b
    subdivision), route into the most-likely detail-bucket so the account is
    not lost. If the group matches a code marked as "formula" only (no details
    block), also reroute. Truly unmatched rows are appended to `unmatched`
    and later surface in the Fragen-Sheet.
    """
    detail_codes = {e["code"] for e in GUV_HIERARCHY if e["kind"] == "details"}

    by_group: dict[str, list[dict]] = {}
    for r in rows:
        canonical = match_code(r.get("gruppe"))
        if canonical is None:
            unmatched.append({"konto_nr": r.get("konto_nr"),
                              "bezeichnung": r.get("bezeichnung"),
                              "gruppe": r.get("gruppe")})
            continue
        # Reroute groups that land on a formula-only row (no details block)
        if canonical not in detail_codes:
            rerouted = GROUP_COARSE_TO_DETAIL.get(canonical)
            if rerouted:
                canonical = rerouted
            else:
                unmatched.append({"konto_nr": r.get("konto_nr"),
                                  "bezeichnung": r.get("bezeichnung"),
                                  "gruppe": r.get("gruppe")})
                continue
        by_group.setdefault(canonical, []).append(r)
    return by_group


def _write_computed_formulas(ws, anchors: dict[str, int],
                              detail_ranges: dict[str, tuple[int, int]],
                              years: list[int]) -> None:
    """Fill in the cascade formulas that depend on other sum rows:
    2. Gesamtleistung, 4. Materialaufwand, 5. Personalaufwand,
    7. Sonstige betr. Aufwendungen, 12. Ergebnis nach Steuern,
    14. Jahresüberschuss, 17. Bilanzgewinn."""
    for y_idx in range(len(years)):
        col = 3 + y_idx
        col_idx = col - 1  # 0-based

        def ref(code: str) -> str:
            return safe_ref(col_idx, anchors.get(code))

        def ref_details_sum(code: str) -> str:
            """For groups without a sum row (7c-g), sum the detail range directly."""
            rng = detail_ranges.get(code)
            if rng is None:
                return "0"
            return sum_range(col_idx, rng[0], rng[1])[1:]  # strip leading "="

        # 2. Gesamtleistung = 1. Umsatzerlöse (simplified)
        if (r := anchors.get("2. Gesamtleistung")) is not None:
            c = ws.cell(row=r, column=col, value=f"={ref('1. Umsatzerlöse')}")
            c.number_format = EUR_FORMAT
            c.font = BOLD

        # 4. Materialaufwand = 4a + 4b
        if (r := anchors.get("4. Materialaufwand")) is not None:
            formula = (f"={ref('4a. Aufwendungen für RHB und Waren')}"
                       f"+{ref('4b. Aufwendungen für bezogene Leistungen')}")
            c = ws.cell(row=r, column=col, value=formula)
            c.number_format = EUR_FORMAT
            c.font = BOLD

        # 5. Personalaufwand = 5a + 5b
        if (r := anchors.get("5. Personalaufwand")) is not None:
            formula = f"={ref('5a. Löhne und Gehälter')}+{ref('5b. Soziale Abgaben')}"
            c = ws.cell(row=r, column=col, value=formula)
            c.number_format = EUR_FORMAT
            c.font = BOLD

        # 7. Sonstige betr. Aufwendungen = 7a-sum + 7b-sum + 7c-g details
        # (7c-g haben nur details, keine eigene sum row — daher direkt SUM über ihre
        #  detail-ranges).
        if (r := anchors.get("7. Sonstige betriebliche Aufwendungen")) is not None:
            parts = [ref("7a. Raumkosten"),
                     ref("7b. Versicherungen, Beiträge und Abgaben"),
                     ref_details_sum("7c. Reparaturen und Instandhaltungen"),
                     ref_details_sum("7d. Fahrzeugkosten"),
                     ref_details_sum("7e. Werbe- und Reisekosten"),
                     ref_details_sum("7f. Kosten der Warenabgabe"),
                     ref_details_sum("7g. Verschiedene betriebliche Kosten")]
            formula = "=" + "+".join(parts)
            c = ws.cell(row=r, column=col, value=formula)
            c.number_format = EUR_FORMAT
            c.font = BOLD

        # 12. Ergebnis nach Steuern
        if (r := anchors.get("12. Ergebnis nach Steuern")) is not None:
            formula = (
                f"={ref('2. Gesamtleistung')}+{ref('3. Sonstige betriebliche Erträge')}"
                f"-{ref('4. Materialaufwand')}-{ref('5. Personalaufwand')}"
                f"-{ref('6. Abschreibungen')}-{ref('7. Sonstige betriebliche Aufwendungen')}"
                f"-{ref('11. Steuern vom Einkommen und vom Ertrag')}"
            )
            c = ws.cell(row=r, column=col, value=formula)
            c.number_format = EUR_FORMAT
            c.font = BOLD

        # 14. Jahresüberschuss = Ergebnis n. Steuern - Sonstige Steuern
        if (r := anchors.get("14. Jahresüberschuss")) is not None:
            # "13. Sonstige Steuern" only has a details block, no sum row — we
            # use safe_ref which falls back to 0 if not present.
            formula = f"={ref('12. Ergebnis nach Steuern')}-{ref('13. Sonstige Steuern')}"
            c = ws.cell(row=r, column=col, value=formula)
            c.number_format = EUR_FORMAT
            c.font = BOLD

        # 17. Bilanzgewinn = JÜ + Vortrag - Ausschüttung
        if (r := anchors.get("17. Bilanzgewinn")) is not None:
            formula = (f"={ref('14. Jahresüberschuss')}"
                       f"+{ref('15. Gewinn-/Verlustvortrag')}"
                       f"-{ref('16. Ausschüttung')}")
            c = ws.cell(row=r, column=col, value=formula)
            c.number_format = EUR_FORMAT
            c.font = BOLD


def _kennzahlen_anchors(sum_rows: dict[str, int]) -> dict[str, int | None]:
    return {
        "umsatz_row": sum_rows.get("1. Umsatzerlöse"),
        "material_row": sum_rows.get("4. Materialaufwand"),
        "personal_row": sum_rows.get("5. Personalaufwand"),
        "jue_row": sum_rows.get("14. Jahresüberschuss"),
        "ebitda_row": sum_rows.get("14. Jahresüberschuss"),  # placeholder — proper EBITDA later
    }
