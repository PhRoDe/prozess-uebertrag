from app.worker.prompts import (
    DOC_TYPE_PROMPT, EXTRACTION_PROMPT_TEXT, EXTRACTION_PROMPT_VISION,
    BWA_PROMPT, SUSA_PROMPT, SYSTEM_PROMPT,
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
    """gkv_section ist optional aber im Prompt dokumentiert, damit der Builder
    HGB-§275-Slugs als STB-unabhaengigen Anker erzwingen kann."""
    assert "gkv_section" in EXTRACTION_PROMPT_TEXT
    for slug in ("umsatzerloese", "materialaufwand_rhb",
                 "personalaufwand_loehne", "abschreibungen",
                 "sonst_betr_aufw", "ee_steuern", "sonst_steuern"):
        assert slug in EXTRACTION_PROMPT_TEXT, f"Missing slug: {slug}"


def test_doc_type_prompt_includes_susa():
    """Susa als eigene Kategorie — sonst landet sie im 'unknown' und der
    Extraktor weiss nicht welchen Prompt er nehmen soll."""
    assert "susa" in DOC_TYPE_PROMPT.lower()
    assert "Summen und Salden" in DOC_TYPE_PROMPT


def test_susa_prompt_excludes_bilanz_klassen():
    """Susa-Prompt muss explizit Klassen 0/1/9 ausschliessen — sonst landen
    Bilanz-Konten (Pkw, Bank, Privatentnahmen) in der GuV-Excel."""
    assert "Klasse 0" in SUSA_PROMPT
    assert "Klasse 1" in SUSA_PROMPT
    assert "Klasse 9" in SUSA_PROMPT
    assert "ignoriert" in SUSA_PROMPT.lower() or "ignorieren" in SUSA_PROMPT.lower()
    # Ausgabe-Format: pdf_jahresueberschuss optional/null (Susa hat keinen Endwert)
    assert "pdf_jahresueberschuss_gj" in SUSA_PROMPT  # Feld erwähnt
    # type=susa als Output-Marker
    assert '"type": "susa"' in SUSA_PROMPT


def test_extraction_prompt_supports_eur_format():
    """Der Prompt muss explizit EÜR-Format (§4 Abs 3 EStG) abdecken — sonst
    bricht Claude bei Einzelunternehmer-PDFs ab oder liefert leeres JSON."""
    # Format-Hinweis im Prompt
    assert "§4 Abs. 3 EStG" in EXTRACTION_PROMPT_TEXT \
        or "§ 4 Abs. 3 EStG" in EXTRACTION_PROMPT_TEXT
    assert "Einnahmen-Überschuss" in EXTRACTION_PROMPT_TEXT \
        or "EÜR" in EXTRACTION_PROMPT_TEXT
    # Stop-Position-Logik fuer EÜR (Steuerlicher Gewinn statt Jahresüberschuss)
    assert "Steuerlicher Gewinn" in EXTRACTION_PROMPT_TEXT
    # Hinzurechnungen + Kürzungen müssen klassifiziert werden
    # (Hinzurechnungen=ertrag, Kürzungen=aufwand)
    assert "Hinzurechnungen" in EXTRACTION_PROMPT_TEXT
    assert "Kürzungen" in EXTRACTION_PROMPT_TEXT
    # endwert_label-Feld fuer dynamische Excel-Beschriftung
    assert "endwert_label" in EXTRACTION_PROMPT_TEXT
