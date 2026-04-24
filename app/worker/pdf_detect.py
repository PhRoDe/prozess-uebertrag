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
    """Extract just the 'Kontennachweis zur GuV' pages (and any continuation).

    Bei grossen Jahresabschluss-PDFs (60+ Seiten) ist der Kontennachweis zur
    GuV nur ein kleiner Teil. Wenn wir den kompletten Text an Claude geben,
    verliert es den Fokus und extrahiert Kontennamen ohne Werte. Lieber
    gezielt den relevanten Abschnitt schicken.

    Heuristik (robust gegenueber verschiedenen Steuerberater-Formaten):
    - Seiten mit "Kontennachweis" + einer GuV-Referenz (Gewinn/G.u.V/GuV)
      im ersten Drittel des Texts
    - NICHT Bilanz-Kontennachweis (Aktiva/Passiva im Kopf)

    Fallback: wenn nichts passt, den vollen Text zurueckgeben (kleine PDFs).
    """
    import re
    guv_header = re.compile(r"Kontennachweis\s+zur?\s+(Gewinn|G\.?u\.?V)",
                            re.IGNORECASE)

    doc = _open_pdf(data)
    try:
        matched_pages: list[int] = []
        for i, page in enumerate(doc):
            text = page.get_text("text")
            head = text[:400]  # Header-Bereich
            if not guv_header.search(head):
                continue
            # Bilanz-Kontennachweis ausschliessen
            if re.search(r"\bAKTIVA\b|\bPASSIVA\b", head, re.IGNORECASE):
                continue
            matched_pages.append(i)

        if not matched_pages:
            # Keine spezifische GuV-Seite erkannt → vollen Text zurueckgeben
            parts = []
            for i, page in enumerate(doc, start=1):
                parts.append(f"=== Seite {i} ===\n{page.get_text('text')}")
            return "\n\n".join(parts)

        # Alle Treffer + eine Puffer-Seite hinten (falls Fortsetzung ohne Header)
        last = matched_pages[-1]
        if last + 1 < doc.page_count:
            matched_pages.append(last + 1)

        parts = []
        for i in matched_pages:
            parts.append(f"=== Seite {i+1} ===\n{doc[i].get_text('text')}")
        return "\n\n".join(parts)
    finally:
        doc.close()
