"""Hierarchische GuV-Gliederung nach HGB §275 GKV.
Jeder Eintrag beschreibt wie eine Zeile in der Excel erzeugt wird.

kind:
  - "details":     Einzelkonten-Bereich; Konten aus der Extraktion werden hier
                   eingesetzt
  - "sum":         Summenzeile für eine Gruppe — wird als =SUM(...) erzeugt
  - "formula":     Berechnete Zeile (JÜ, EBITDA usw.) — Formel-Aufbau im Builder
"""

# Reihenfolge ist signifikant — bestimmt das Excel-Layout.
GUV_HIERARCHY: list[dict] = [
    {"code": "1. Umsatzerlöse", "kind": "details"},
    {"code": "1. Umsatzerlöse", "kind": "sum", "bold": True},
    {"code": "2. Gesamtleistung", "kind": "formula", "bold": True},
    {"code": "3. Sonstige betriebliche Erträge", "kind": "details"},
    {"code": "3. Sonstige betriebliche Erträge", "kind": "sum", "bold": True},
    {"code": "4a. Aufwendungen für RHB und Waren", "kind": "details"},
    {"code": "4a. Aufwendungen für RHB und Waren", "kind": "sum"},
    {"code": "4b. Aufwendungen für bezogene Leistungen", "kind": "details"},
    {"code": "4b. Aufwendungen für bezogene Leistungen", "kind": "sum"},
    {"code": "4. Materialaufwand", "kind": "formula", "bold": True},
    {"code": "5a. Löhne und Gehälter", "kind": "details"},
    {"code": "5a. Löhne und Gehälter", "kind": "sum"},
    {"code": "5b. Soziale Abgaben", "kind": "details"},
    {"code": "5b. Soziale Abgaben", "kind": "sum"},
    {"code": "5. Personalaufwand", "kind": "formula", "bold": True},
    {"code": "6. Abschreibungen", "kind": "details"},
    {"code": "6. Abschreibungen", "kind": "sum", "bold": True},
    {"code": "7a. Raumkosten", "kind": "details"},
    {"code": "7a. Raumkosten", "kind": "sum"},
    {"code": "7b. Versicherungen, Beiträge und Abgaben", "kind": "details"},
    {"code": "7b. Versicherungen, Beiträge und Abgaben", "kind": "sum"},
    {"code": "7c. Reparaturen und Instandhaltungen", "kind": "details"},
    {"code": "7d. Fahrzeugkosten", "kind": "details"},
    {"code": "7e. Werbe- und Reisekosten", "kind": "details"},
    {"code": "7f. Kosten der Warenabgabe", "kind": "details"},
    {"code": "7g. Verschiedene betriebliche Kosten", "kind": "details"},
    {"code": "7. Sonstige betriebliche Aufwendungen", "kind": "formula", "bold": True},
    {"code": "8. Erträge aus Wertpapieren", "kind": "details"},
    {"code": "9. Sonstige Zinsen und ähnliche Erträge", "kind": "details"},
    {"code": "10. Zinsen und ähnliche Aufwendungen", "kind": "details"},
    {"code": "11. Steuern vom Einkommen und vom Ertrag", "kind": "details"},
    {"code": "11. Steuern vom Einkommen und vom Ertrag", "kind": "sum"},
    {"code": "12. Ergebnis nach Steuern", "kind": "formula", "bold": True},
    {"code": "13. Sonstige Steuern", "kind": "details"},
    {"code": "14. Jahresüberschuss", "kind": "formula", "bold": True},
    {"code": "15. Gewinn-/Verlustvortrag", "kind": "details"},
    {"code": "16. Ausschüttung", "kind": "details"},
    {"code": "17. Bilanzgewinn", "kind": "formula", "bold": True},
]


def get_all_codes() -> list[str]:
    return sorted({e["code"] for e in GUV_HIERARCHY})


def code_prefix(code: str) -> str:
    """Return the canonical prefix (e.g. '4a', '7g', '12') — used for fuzzy matching
    Claude's sometimes-abbreviated group names."""
    # "4a. Aufwendungen für RHB..." -> "4a"
    # "12. Ergebnis nach Steuern" -> "12"
    head = code.split(".", 1)[0].strip()
    return head.lower()


def match_code(claude_gruppe: str | None) -> str | None:
    """Given a group label from Claude (which may vary slightly), return the
    canonical HGB code from the hierarchy or None if no match.

    Examples:
        '4a. Materialaufwand RHB' -> '4a. Aufwendungen für RHB und Waren'
        '1. Umsatzerlöse' -> '1. Umsatzerlöse'
        'Xy unknown' -> None
    """
    if not claude_gruppe:
        return None
    claude_prefix = code_prefix(claude_gruppe)
    for entry in GUV_HIERARCHY:
        if code_prefix(entry["code"]) == claude_prefix:
            return entry["code"]
    return None
