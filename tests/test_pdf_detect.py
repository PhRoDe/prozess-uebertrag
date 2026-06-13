import io
import fitz
import pytest
from app.worker.pdf_detect import (
    classify_pdf, pdf_to_images, extract_text, PdfKind, PdfError,
    _select_guv_pages,
)


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


def test_encrypted_pdf_raises_pdferror():
    doc = fitz.open()
    doc.new_page()
    buf = io.BytesIO()
    doc.save(buf, encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw="owner", user_pw="user")
    doc.close()
    with pytest.raises(PdfError, match="passwortgeschützt"):
        classify_pdf(buf.getvalue())


def test_non_pdf_bytes_raise_pdferror():
    with pytest.raises(PdfError):
        classify_pdf(b"this is not a pdf")


# --- _select_guv_pages: Kontennachweis-Folgeseiten ---------------------------

def _amounts(n):
    """n deutsche Geldbeträge als Text-Zeilen (für Betrag-Dichte-Heuristik)."""
    return "\n".join(f"Position {i} {1000 + i}.{i:03d},{i % 100:02d}" for i in range(n))


def test_select_guv_pages_keeps_multipage_kontennachweis_continuation():
    """Regression: mehrseitiger Kontennachweis trägt den Header nur auf der
    ersten Seite. Folgeseiten (Positionen 6-12 mit Unterkonten) haben keinen
    Marker und dürfen NICHT gedroppt werden (Prisma JA 2022-2024, Bug 06/2026)."""
    pages = [
        "Inhaltsverzeichnis Gewinn- und Verlustrechnung 13",        # 0: Prosa, 0 Beträge
        "BILANZ zum 31.12. AKTIVA PASSIVA\n" + _amounts(40),         # 1: Bilanz → exclude
        "GEWINN- UND VERLUSTRECHNUNG vom 01.01 bis 31.12\n" + _amounts(30),  # 2: Summen-GuV
        "ANLAGENSPIEGEL zum 31.12.\n" + _amounts(20),                # 3: exclude → stoppt Run ab S2
        "Erläuterungen zu den Posten der Bilanz\n" + _amounts(10),   # 4: Anhang, kein GuV-Marker
        "Gewinn- und Verlustrechnung\n1. Umsatzerlöse\n" + _amounts(12),  # 5: Detail-Start
        "3. sonstige betriebliche Erträge\n" + _amounts(20),         # 6: Fortsetzung, KEIN Header
        "6. Abschreibungen\n" + _amounts(8),                          # 7: Fortsetzung
        "9. Steuern vom Einkommen\n" + _amounts(6),                   # 8: Fortsetzung
    ]
    selected = _select_guv_pages(pages)
    # Summen-GuV (2) + kompletter Detail-Block (5,6,7,8). NICHT: Prosa (0),
    # Bilanz (1), Anlagenspiegel (3), Anhang (4).
    assert selected == [2, 5, 6, 7, 8]


def test_select_guv_pages_prose_mention_does_not_start_run():
    """Eine reine Text-Erwähnung von 'Gewinn- und Verlustrechnung' ohne
    Betrags-Tabelle darf keinen Run starten (sonst Anhang-Prosa-Flut)."""
    pages = [
        "Die Gewinn- und Verlustrechnung wird nach §275 HGB gegliedert.",  # 0: Prosa
        "Lagebericht Text ohne Zahlen",                                     # 1
    ]
    assert _select_guv_pages(pages) == []


def test_select_guv_pages_fallback_returns_all_when_no_match():
    pages = ["irgendwas", "noch was"]
    # Kein Treffer → Fallback signalisiert "alle Seiten" (leere Auswahl =
    # extract_guv_section gibt vollen Text zurück; hier prüfen wir die Auswahl).
    assert _select_guv_pages(pages) == []
