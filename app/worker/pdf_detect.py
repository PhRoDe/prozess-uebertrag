from enum import Enum
import fitz


class PdfKind(str, Enum):
    TEXT = "text"
    SCAN = "scan"


def classify_pdf(data: bytes, threshold: int = 100) -> PdfKind:
    """Return TEXT if any page has > threshold extractable chars; SCAN otherwise."""
    doc = fitz.open(stream=data, filetype="pdf")
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
    doc = fitz.open(stream=data, filetype="pdf")
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    try:
        return [page.get_pixmap(matrix=matrix).tobytes("png") for page in doc]
    finally:
        doc.close()


def extract_text(data: bytes) -> str:
    """Extract all text from a text-PDF, page-numbered."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        parts = []
        for i, page in enumerate(doc, start=1):
            parts.append(f"=== Seite {i} ===\n{page.get_text('text')}")
        return "\n\n".join(parts)
    finally:
        doc.close()
