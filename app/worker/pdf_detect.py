import re
from enum import Enum
import fitz

# Marker für GuV-/Kontennachweis-Erkennung (modul-weit, damit _select_guv_pages
# als reine, testbare Funktion ohne PDF arbeiten kann).
_KONTENNACHWEIS_MARKER = re.compile(
    r"Kontennachweis\s+zur?\s+(Gewinn|G\.?u\.?V)", re.IGNORECASE)
_OVERVIEW_MARKER = re.compile(
    r"Gewinn-?\s*und\s*Verlustrechnung|"
    r"Gewinnermittlung\s+nach\s+§\s*4\s+Abs|"
    r"\bBETRIEBSEINNAHMEN\b|\bBETRIEBSAUSGABEN\b",
    re.IGNORECASE)
_EXCLUDE_MARKER = re.compile(
    r"\bAKTIVA\b|\bPASSIVA\b|"  # Bilanz
    r"Anlagen?spiegel|Entwicklung\s+des\s+Anlageverm|"  # Anlagenspiegel
    r"Allgemeine\s+Auftragsbedingungen",  # AGBs
    re.IGNORECASE)
# Deutsches Geldbetrag-Muster (z.B. "1.387.335,10" oder "763,69").
_AMOUNT_RE = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
# Eine Seite startet nur dann einen GuV-Run, wenn sie ein echter Tabellen-
# Auszug ist (Marker + genug Beträge) — nicht eine reine Prosa-Erwähnung.
_MIN_AMOUNTS_TO_START = 4


def _select_guv_pages(pages_text: list[str]) -> list[int]:
    """Wähle die GuV-/Kontennachweis-relevanten Seiten-Indizes aus.

    Kernproblem (Bug 06/2026): Ein mehrseitiger Kontennachweis trägt seinen
    Header ("Gewinn- und Verlustrechnung" / "Kontennachweis zur GuV") nur auf
    der ERSTEN Seite. Die Folgeseiten (Positionen 5b/6/7/8/9… mit Unterkonten)
    haben keinen Marker. Die alte Logik nahm nur Marker-Seiten + 1 Pufferseite
    → Folgeseiten gingen verloren → Positionen ohne Unterkonten.

    Lösung: Ab einer echten GuV-Tabellenseite (Marker + Betrag-Dichte) vorwärts
    durch alle nicht-ausgeschlossenen Folgeseiten mit Beträgen ("Run") bis zur
    nächsten Exclude-Seite (Bilanz/Anlagenspiegel) oder EOF. Reine Prosa-
    Erwähnungen (0 Beträge) starten keinen Run.

    Returns: sortierte Liste der Seiten-Indizes. Leer = kein Treffer (Aufrufer
    soll dann den vollen Text verwenden).
    """
    def excluded(t: str) -> bool:
        return bool(_EXCLUDE_MARKER.search(t))

    def has_marker(t: str) -> bool:
        return bool(_OVERVIEW_MARKER.search(t) or _KONTENNACHWEIS_MARKER.search(t))

    def amount_count(t: str) -> int:
        return len(_AMOUNT_RE.findall(t))

    n = len(pages_text)
    selected: set[int] = set()
    i = 0
    while i < n:
        t = pages_text[i]
        starts_run = (
            not excluded(t) and has_marker(t)
            and amount_count(t) >= _MIN_AMOUNTS_TO_START
        )
        if not starts_run:
            i += 1
            continue
        # Run: diese Seite + alle Folge-(Fortsetzungs-)Seiten mit Beträgen,
        # bis eine Exclude-Seite kommt oder die Beträge ausgehen (Signatur-/
        # Prosa-Seite nach dem Kontennachweis).
        j = i
        while j < n and not excluded(pages_text[j]) and amount_count(pages_text[j]) >= 1:
            selected.add(j)
            j += 1
        i = max(j, i + 1)
    return sorted(selected)


class PdfKind(str, Enum):
    TEXT = "text"
    SCAN = "scan"


class PdfError(ValueError):
    """Raised when a PDF cannot be read (corrupted, encrypted, not actually PDF)."""


def _open_pdf(data: bytes) -> fitz.Document:
    """Open a PDF with clear error messages for common failure modes."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise PdfError(f"PDF kann nicht geöffnet werden: {e}") from e
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise PdfError("PDF ist passwortgeschützt — bitte vorher entsperren.")
    return doc


def classify_pdf(data: bytes, threshold: int = 100) -> PdfKind:
    """Return TEXT if any page has > threshold extractable chars; SCAN otherwise."""
    doc = _open_pdf(data)
    try:
        for page in doc:
            if len(page.get_text("text").strip()) > threshold:
                return PdfKind.TEXT
        return PdfKind.SCAN
    finally:
        doc.close()


def pdf_to_images(data: bytes, dpi: int = 100) -> list[bytes]:
    """Render each page to PNG bytes for Claude Vision.
    100 DPI balances readability with token cost (Fix 4A — 30% less than 150 DPI)."""
    doc = _open_pdf(data)
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    try:
        return [page.get_pixmap(matrix=matrix).tobytes("png") for page in doc]
    finally:
        doc.close()


def extract_text(data: bytes) -> str:
    """Extract all text from a text-PDF, page-numbered."""
    doc = _open_pdf(data)
    try:
        parts = []
        for i, page in enumerate(doc, start=1):
            parts.append(f"=== Seite {i} ===\n{page.get_text('text')}")
        return "\n\n".join(parts)
    finally:
        doc.close()


# Susa-Marker (DATEV "Summen und Salden" / "Alle bebuchten Konten" /
# Saldenliste). Der Header wiederholt sich auf JEDER Susa-Seite — daher reicht
# Marker-Match (kein Ride-Forward), das hält OPOS-/USt-Seiten ohne Marker raus.
_SUSA_MARKER = re.compile(
    r"Summen\s+und\s+Salden|bebuchten\s+Konten|Saldenliste", re.IGNORECASE)


def _has_susa_section(text: str) -> bool:
    """True wenn der (volle) PDF-Text einen Susa-Abschnitt enthält — auch wenn
    eine BWA davorsteht (kombiniertes DATEV-Bundle)."""
    return bool(_SUSA_MARKER.search(text or ""))


def _select_susa_pages(pages_text: list[str]) -> list[int]:
    """Seiten-Indizes der Susa ('Summen und Salden') im (ggf. kombinierten)
    PDF. Reiner Marker-Match: jede Susa-Seite trägt den Header, OPOS-/USt-/BWA-
    Seiten nicht → sauber abgegrenzt ohne Ride-Forward. Leer = kein Susa-Teil."""
    return [i for i, t in enumerate(pages_text) if _SUSA_MARKER.search(t or "")]


def extract_susa_section(data: bytes) -> str:
    """Nur die Susa-Seiten ('Summen und Salden') eines kombinierten DATEV-
    Bundles (BWA + Susa + OPOS/USt) extrahieren — für den SUSA-Prompt. So
    bekommt Claude die Einzelkonten ohne BWA-Aggregat-/OPOS-Rauschen.

    Fallback: kein Susa-Marker → voller Text."""
    doc = _open_pdf(data)
    try:
        pages_text = [page.get_text("text") for page in doc]
        selected = _select_susa_pages(pages_text)
        if not selected:
            selected = list(range(len(pages_text)))
        return "\n\n".join(
            f"=== Seite {i + 1} ===\n{pages_text[i]}" for i in selected)
    finally:
        doc.close()


def extract_guv_section(data: bytes) -> str:
    """Extract the GuV/EÜR overview + Kontennachweis pages from a JA-PDF.

    Bei grossen Jahresabschluss-PDFs (60+ Seiten) ist der relevante Teil nur
    ein kleiner Ausschnitt. Wenn wir den kompletten Text an Claude geben,
    verliert es den Fokus. Wir senden gezielt:
      1. Übersichts-Seiten (GuV nach §275 HGB ODER Gewinnermittlung §4 Abs 3
         EStG) — liefert die Hauptsektionen-Hierarchie
      2. Kontennachweis-Seiten — liefert die Einzelkonten

    Format-agnostisch:
    - HGB-GuV: "Gewinn- und Verlustrechnung", "Kontennachweis zur GuV"
    - EÜR:     "Gewinnermittlung nach §4 Abs 3 EStG", "BETRIEBSEINNAHMEN",
               "Kontennachweis zur Gewinnermittlung"

    Wir suchen die Marker in der ganzen Seite (nicht nur Header), weil pyMuPDF
    bei manchen Layouts den Header erst nach dem Tabellen-Body extrahiert.
    Bilanz-, Anlagenspiegel- und AGB-Seiten werden ausgeschlossen.

    Mehrseitige Kontennachweise: Der Header steht nur auf der ersten Seite —
    Folgeseiten werden über `_select_guv_pages` (Run-Vorwärts-Logik) mitgenommen,
    damit Positionen 6-12 ihre Unterkonten behalten (Bug 06/2026).

    Fallback: wenn nichts passt, vollen Text zurueckgeben.
    """
    doc = _open_pdf(data)
    try:
        pages_text = [page.get_text("text") for page in doc]
        selected = _select_guv_pages(pages_text)
        if not selected:
            # Keine spezifische Seite erkannt → vollen Text zurueckgeben
            selected = list(range(len(pages_text)))
        return "\n\n".join(
            f"=== Seite {i + 1} ===\n{pages_text[i]}" for i in selected)
    finally:
        doc.close()
