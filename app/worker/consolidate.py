from typing import Any

MISMATCH_TOLERANCE = 0.01  # 1 cent


def merge_extractions(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multi-year jahresabschluss extractions into a consolidated structure.

    Returns {years, rows, questions}.
    - rows: union of accounts across all years, with per-year values dict
    - questions: detected mismatches (e.g. previous-year value in newer PDF
      disagrees with own-year value in older PDF)
    """
    jahresabschluss = [e for e in extractions if e.get("type") == "jahresabschluss"]
    jahresabschluss.sort(key=lambda e: e["year"])

    all_years: set[int] = set()
    for e in jahresabschluss:
        all_years.add(e["year"])
        if e.get("previous_year") is not None:
            all_years.add(e["previous_year"])
    years = sorted(all_years)

    rows: dict[str, dict[str, Any]] = {}
    questions: list[dict[str, Any]] = []

    for e in jahresabschluss:
        gj = e["year"]
        vj = e.get("previous_year")
        for acc in e["accounts"]:
            # When konto_nr is missing, include gruppe + bezeichnung to reduce
            # false merges between similarly-named entries in different groups.
            key = acc["konto_nr"] or f"__nrless__{acc.get('gruppe', '')}::{acc['bezeichnung']}"
            if key not in rows:
                rows[key] = {
                    "konto_nr": acc["konto_nr"],
                    "bezeichnung": acc["bezeichnung"],
                    "gruppe": acc["gruppe"],
                    "values": {},
                    "confidence": acc.get("confidence", "high"),
                }

            existing_gj = rows[key]["values"].get(gj)
            if existing_gj is not None and abs(existing_gj - acc["betrag_gj"]) > MISMATCH_TOLERANCE:
                questions.append({
                    "type": "duplicate_gj_mismatch",
                    "konto_nr": acc["konto_nr"],
                    "year": gj,
                    "values": [existing_gj, acc["betrag_gj"]],
                })
            rows[key]["values"][gj] = acc["betrag_gj"]

            if vj is not None:
                existing_vj = rows[key]["values"].get(vj)
                if (existing_vj is not None
                        and abs(existing_vj - acc["betrag_vj"]) > MISMATCH_TOLERANCE):
                    questions.append({
                        "type": "previous_year_mismatch",
                        "konto_nr": acc["konto_nr"],
                        "year": vj,
                        "pdf_says": acc["betrag_vj"],
                        "own_value": existing_vj,
                    })
                rows[key]["values"][vj] = acc["betrag_vj"]

    return {
        "years": years,
        "rows": list(rows.values()),
        "questions": questions,
    }
