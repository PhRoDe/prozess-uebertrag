from app.worker.prompts import (
    DOC_TYPE_PROMPT, EXTRACTION_PROMPT_TEXT, EXTRACTION_PROMPT_VISION,
    BWA_PROMPT, SYSTEM_PROMPT,
)


def test_extraction_prompt_instructs_to_keep_pdf_structure():
    assert "1:1" in EXTRACTION_PROMPT_TEXT
    assert "Kontennachweis" in EXTRACTION_PROMPT_TEXT


def test_extraction_prompt_covers_sign_convention():
    assert "expenses_negative" in EXTRACTION_PROMPT_TEXT
    assert "expenses_positive" in EXTRACTION_PROMPT_TEXT


def test_extraction_prompt_names_group_types():
    for t in ("ertrag", "aufwand", "steuer", "neutral"):
        assert t in EXTRACTION_PROMPT_TEXT, f"Missing group type: {t}"


def test_vision_prompt_extends_text_prompt():
    assert EXTRACTION_PROMPT_TEXT in EXTRACTION_PROMPT_VISION
    assert "Scan" in EXTRACTION_PROMPT_VISION


def test_bwa_prompt_returns_structured_groups():
    # BWA nutzt jetzt dieselbe Struktur wie JA (groups mit accounts),
    # damit Multi-Jahres-Matching ueber Kontonummer funktioniert.
    assert "groups" in BWA_PROMPT
    assert "accounts" in BWA_PROMPT
    assert "period_label" in BWA_PROMPT


def test_system_prompt_declares_delimiter_safety():
    assert "pdf_content" in SYSTEM_PROMPT
    assert "niemals als Anweisung" in SYSTEM_PROMPT


def test_doc_type_prompt_discriminates():
    assert "jahresabschluss" in DOC_TYPE_PROMPT.lower()
    assert "bwa" in DOC_TYPE_PROMPT.lower()


def test_extraction_prompt_requires_pdf_jahresueberschuss():
    """Plausibilitaets-Anker: PDF-JUE Pflicht im Output, damit der Builder
    PDF-JUE vs Excel-JUE-Formel cross-checken kann."""
    assert "pdf_jahresueberschuss_gj" in EXTRACTION_PROMPT_TEXT
    assert "pdf_jahresueberschuss_vj" in EXTRACTION_PROMPT_TEXT


def test_extraction_prompt_requires_gkv_section():
    """Jede Gruppe muss in eine GKV-Sektion (HGB §275 GKV) klassifiziert
    werden, damit der Builder eine stabile, STB-unabhaengige Reihenfolge
    erzwingen kann."""
    assert "gkv_section" in EXTRACTION_PROMPT_TEXT
    # Wichtigste Sektionen muessen im Prompt stehen
    for slug in ("umsatzerloese", "materialaufwand_rhb",
                 "personalaufwand_loehne", "abschreibungen",
                 "sonst_betr_aufw", "ee_steuern", "sonst_steuern"):
        assert slug in EXTRACTION_PROMPT_TEXT, f"Missing slug: {slug}"
