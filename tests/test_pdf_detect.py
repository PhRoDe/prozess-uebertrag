import io
import fitz
from app.worker.pdf_detect import classify_pdf, pdf_to_images, extract_text, PdfKind


def make_text_pdf(lines: list[str] | None = None):
    lines = lines or ["Bilanz zum 31.12.2024", "Umsatzerloese 1.234.567,89"]
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 100 + i * 20), line)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_blank_pdf():
    doc = fitz.open()
    for _ in range(2):
        doc.new_page()
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_classify_text_pdf():
    # Text muss > 100 Zeichen Threshold liegen. Mehrere Zeilen verwenden weil
    # pymupdf insert_text nicht automatisch umbricht.
    lines = [f"Zeile {i}: Umsatzerloese 1.234.567,89 EUR" for i in range(10)]
    data = make_text_pdf(lines)
    assert classify_pdf(data) == PdfKind.TEXT


def test_classify_blank_pdf_is_scan():
    assert classify_pdf(make_blank_pdf()) == PdfKind.SCAN


def test_pdf_to_images_returns_png_bytes():
    images = pdf_to_images(make_blank_pdf())
    assert len(images) == 2
    assert all(img.startswith(b"\x89PNG") for img in images)


def test_extract_text_includes_page_markers():
    data = make_text_pdf(["Hello World"])
    text = extract_text(data)
    assert "=== Seite 1 ===" in text
    assert "=== Seite 2 ===" in text
    assert "Hello World" in text
