"""Manueller Smoketest gegen Claude-API. Nicht Teil der Unit-Test-Suite.
Ausfuehren mit: .venv/bin/python tests/fixtures/smoketest_claude.py"""
import io
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

import fitz
from app.worker.claude_client import ClaudeClient
from app.worker.pdf_detect import classify_pdf, extract_text


def make_mini_ja_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        "Jahresabschluss 2024 - Muster GmbH",
        "",
        "Kontennachweis Gewinn- und Verlustrechnung",
        "",
        "1. Umsatzerloese",
        "   8400  Erloese 19% USt           1.000.000,00    900.000,00",
        "   8401  Erloese 7% USt                15.000,00     12.000,00",
        "                                    1.015.000,00    912.000,00",
        "",
        "4. Materialaufwand",
        "   5100  Wareneingang 19%            400.000,00    370.000,00",
        "                                      400.000,00    370.000,00",
        "",
        "5. Personalaufwand",
        "   6000  Loehne und Gehaelter        150.000,00    140.000,00",
        "   6010  Gesetzl. soz. Aufwand        30.000,00     28.000,00",
        "                                      180.000,00    168.000,00",
    ]
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + i * 14), line)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def main() -> None:
    pdf_bytes = make_mini_ja_pdf()
    kind = classify_pdf(pdf_bytes)
    print(f"1. PDF-Kind: {kind.value}")

    text = extract_text(pdf_bytes)
    print(f"2. Extracted text: {len(text)} chars")

    client = ClaudeClient()
    doc_type = client.classify_document(text[:2000])
    print(f"3. Doc type: {doc_type!r}")

    print("4. Calling Claude extract_text_pdf (may take 30-60s)...")
    result = client.extract_text_pdf(text, is_bwa=(doc_type == "bwa"))
    print(f"   type={result.get('type')}, year={result.get('year')}, prev={result.get('previous_year')}")
    accounts = result.get("accounts", [])
    print(f"   accounts: {len(accounts)}")
    for acc in accounts[:6]:
        print(f"   - nr={acc.get('konto_nr')!s:>6} | {acc.get('bezeichnung'):<30} "
              f"| {acc.get('gruppe'):<30} | gj={acc.get('betrag_gj'):>12} vj={acc.get('betrag_vj'):>12}")


if __name__ == "__main__":
    main()
