from enum import Enum
import fitz


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

    Fallback: wenn nichts passt, vollen Text zurueckgeben.
    """
    import re
    kontennachweis_marker = re.compile(
        r"Kontennachweis\s+zur?\s+(Gewinn|G\.?u\.?V)", re.IGNORECASE)
    overview_marker = re.compile(
        r"Gewinn-?\s*und\s*Verlustrechnung|"
        r"Gewinnermittlung\s+nach\s+§\s*4\s+Abs|"
        r"\bBETRIEBSEINNAHMEN\b|\bBETRIEBSAUSGABEN\b",
        re.IGNORECASE)
    exclude_marker = re.compile(
        r"\bAKTIVA\b|\bPASSIVA\b|"  # Bilanz
        r"Anlagenspiegel|Entwicklung\s+des\s+Anlageverm|"  # Anlagenspiegel
        r"Allgemeine\s+Auftragsbedingungen",  # AGBs
        re.IGNORECASE)

    doc = _open_pdf(data)
    try:
        matched_pages: list[int] = []
        for i, page in enumerate(doc):
            text = page.get_text("text")
            if exclude_marker.search(text):
                continue
            if (kontennachweis_marker.search(text)
                    or overview_marker.search(text)):
                matched_pages.append(i)

        if not matched_pages:
            # Keine spezifische Seite erkannt → vollen Text zurueckgeben
            parts = []
            for i, page in enumerate(doc, start=1):
                parts.append(f"=== Seite {i} ===\n{page.get_text('text')}")
            return "\n\n".join(parts)

        # Eine Puffer-Seite hinter dem letzten Treffer mitnehmen
        # (falls Fortsetzung ohne Header). Aber nur wenn Puffer-Seite kein
        # Excluded-Inhalt hat.
        last = matched_pages[-1]
        if last + 1 < doc.page_count:
            buffer_text = doc[last + 1].get_text("text")
            if not exclude_marker.search(buffer_text):
                matched_pages.append(last + 1)

        parts = []
        for i in matched_pages:
            parts.append(f"=== Seite {i+1} ===\n{doc[i].get_text('text')}")
        return "\n\n".join(parts)
    finally:
        doc.close()
