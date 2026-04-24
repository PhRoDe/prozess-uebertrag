"""Prompts für Claude-basierte Extraktion.
Die HGB-§275-Gliederung stammt aus skills/guv-uebertrag/SKILL.md."""

GUV_STRUKTUR = """
1.  Umsatzerlöse
2.  Gesamtleistung
3.  Sonstige betriebliche Erträge
4.  Materialaufwand (a) RHB, (b) bezogene Leistungen
5.  Personalaufwand (a) Löhne und Gehälter, (b) Soziale Abgaben
6.  Abschreibungen
7.  Sonstige betriebliche Aufwendungen
    (a) Raumkosten, (b) Versicherungen/Beiträge/Abgaben,
    (c) Reparaturen und Instandhaltungen, (d) Fahrzeugkosten,
    (e) Werbe- und Reisekosten, (f) Kosten der Warenabgabe,
    (g) Verschiedene betriebliche Kosten
8.  Erträge aus Wertpapieren und Ausleihungen
9.  Sonstige Zinsen und ähnliche Erträge
10. Zinsen und ähnliche Aufwendungen
11. Steuern vom Einkommen und vom Ertrag
12. Ergebnis nach Steuern
13. Sonstige Steuern
14. Jahresüberschuss
15. Gewinnvortrag / Verlustvortrag
16. Ausschüttung
17. Bilanzgewinn
"""

DOC_TYPE_PROMPT = """Du bekommst einen Ausschnitt eines PDF. Klassifiziere ihn in genau eine Kategorie:
- "jahresabschluss": offizieller Jahresabschluss mit Bilanz und GuV (meist mit Kontennachweis)
- "bwa": Betriebswirtschaftliche Auswertung (unterjährig, nur Hauptpositionen, keine Einzelkonten)
- "unknown": alles andere

Antworte NUR mit dem Kategorie-String, nichts weiter."""


EXTRACTION_PROMPT_TEXT = f"""Du extrahierst den Kontennachweis der GuV aus einem Jahresabschluss-PDF (Text-Version).

HGB §275 Gliederung (GKV), an der du orientierst:
{GUV_STRUKTUR}

Aufgabe: Extrahiere ALLE Konten aus dem Kontennachweis mit Geschäftsjahr- und Vorjahres-Betrag.
Ordne jedes Konto in die passende Gruppe ein. Markiere unsichere Zuordnungen mit confidence="low".

Regeln:
- Deutsches Zahlenformat: "1.387.335,10" → 1387335.10
- Aufwandspositionen als positive Zahlen übernehmen
- Negative Werte nur wo explizit im Kontennachweis
- Bei unklarer Gruppe: confidence="low" und suggested_groups angeben, NICHT raten
- Auch Bilanzgewinn-Bereich extrahieren (Gewinnvortrag, Ausschüttung, Bilanzgewinn)

Rückgabeformat: JSON wie in diesem Schema:
{{
  "type": "jahresabschluss",
  "year": 2024,
  "previous_year": 2023,
  "accounts": [
    {{"konto_nr": "8400", "bezeichnung": "Erlöse 19% USt", "gruppe": "1. Umsatzerlöse",
     "betrag_gj": 1279228.53, "betrag_vj": 1110030.20, "confidence": "high"}}
  ],
  "open_questions": [
    {{"konto_nr": "4980", "bezeichnung": "...", "betrag_gj": 12340.00,
     "suggested_groups": ["7g. Verschiedene betr. Kosten", "7d. Fahrzeugkosten"]}}
  ],
  "bilanzgewinn": {{"gewinnvortrag": 0, "verlustvortrag": 78877.33,
                   "ausschuettung": 120000.00, "bilanzgewinn": -19667.11}}
}}

Antworte AUSSCHLIESSLICH mit gültigem JSON, kein Markdown, keine Erklärung."""


EXTRACTION_PROMPT_VISION = EXTRACTION_PROMPT_TEXT + """

ACHTUNG — Scan-PDF:
Die Seiten kommen als Bilder. Lies die Zahlen besonders sorgfältig.
Bei unsicheren Ziffern: confidence="low" setzen. Niemals raten."""


BWA_PROMPT = """Du extrahierst eine Betriebswirtschaftliche Auswertung (BWA).
BWAs enthalten nur Hauptpositionen, keine Einzelkonten.

Rückgabeformat: JSON
{
  "type": "bwa",
  "zeitraum": "01/2024-12/2024",
  "positionen": [
    {"bezeichnung": "Umsatzerlöse", "betrag": 1234567.89}
  ]
}

Antworte AUSSCHLIESSLICH mit gültigem JSON."""

