from app.excel.formulas import ratio, cell


def build_kennzahlen_rows(anchors: dict[str, int | None], col_idx: int) -> list[dict]:
    """Produce a list of Kennzahlen rows to write into the Excel.

    anchors: map of anchor name → row number (or None if missing in this data).
             Required keys: umsatz_row, material_row, personal_row, jue_row, ebitda_row.
    """
    umsatz = anchors.get("umsatz_row")
    material = anchors.get("material_row")
    personal = anchors.get("personal_row")
    jue = anchors.get("jue_row")
    ebitda = anchors.get("ebitda_row")

    def _ratio_or_blank(num: int | None, den: int | None) -> str:
        if num is None or den is None:
            return ""
        return ratio(num, den, col_idx)

    return [
        {"label": "Materialquote",
         "formula": _ratio_or_blank(material, umsatz), "number_format": "0.0%"},
        {"label": "Rohertragsquote",
         "formula": (f'=IFERROR(1-{cell(col_idx, material)}/{cell(col_idx, umsatz)},"")'
                     if material is not None and umsatz is not None else ""),
         "number_format": "0.0%"},
        {"label": "Personalquote",
         "formula": _ratio_or_blank(personal, umsatz), "number_format": "0.0%"},
        {"label": "Umsatzrendite",
         "formula": _ratio_or_blank(jue, umsatz), "number_format": "0.0%"},
        {"label": "EBITDA-Marge",
         "formula": _ratio_or_blank(ebitda, umsatz), "number_format": "0.0%"},
    ]
