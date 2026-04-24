from app.worker.prompts import (
    DOC_TYPE_PROMPT, EXTRACTION_PROMPT_TEXT, EXTRACTION_PROMPT_VISION,
    BWA_PROMPT, build_consolidation_prompt,
)


def test_prompts_reference_guv_structure():
    assert "Umsatzerlöse" in EXTRACTION_PROMPT_TEXT
    assert "Kontennachweis" in EXTRACTION_PROMPT_TEXT
    assert "confidence" in EXTRACTION_PROMPT_TEXT


def test_vision_prompt_warns_about_scans():
    assert "Scan" in EXTRACTION_PROMPT_VISION or "scan" in EXTRACTION_PROMPT_VISION.lower()
    assert "Bilder" in EXTRACTION_PROMPT_VISION or "bild" in EXTRACTION_PROMPT_VISION.lower()


def test_doctype_prompt_discriminates():
    assert "jahresabschluss" in DOC_TYPE_PROMPT.lower()
    assert "bwa" in DOC_TYPE_PROMPT.lower()


def test_bwa_prompt_focuses_on_hauptpositionen():
    assert "BWA" in BWA_PROMPT
    assert "Hauptpositionen" in BWA_PROMPT


def test_consolidation_prompt_includes_documents():
    extractions = [
        {"file": "ja-2023.pdf", "accounts": []},
        {"file": "ja-2024.pdf", "accounts": []},
    ]
    prompt = build_consolidation_prompt(extractions)
    assert "ja-2023.pdf" in prompt and "ja-2024.pdf" in prompt
    assert "previous_year_mismatch" in prompt
