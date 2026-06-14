"""Phase A: kontrollierte Branchenliste (Single Source of Truth)."""
from app.industries import (
    INDUSTRY_CATEGORIES, INDUSTRY_CODES, industry_choices, is_valid_industry,
)


def test_codes_eindeutig():
    codes = [c for c, _l, _w, _s in INDUSTRY_CATEGORIES]
    assert len(codes) == len(set(codes))


def test_sammelkategorie_vorhanden():
    assert "sonstige" in INDUSTRY_CODES


def test_choices_sortiert_und_vollstaendig():
    ch = industry_choices()
    assert len(ch) == len(INDUSTRY_CATEGORIES)
    sorts = [s for _c, _l, _w, s in sorted(INDUSTRY_CATEGORIES, key=lambda t: t[3])]
    assert sorts == sorted(sorts)
    assert all(c["code"] and c["label"] for c in ch)


def test_is_valid_industry():
    assert is_valid_industry("it_software") is True
    assert is_valid_industry(None) is True        # Branche optional
    assert is_valid_industry("gibtsnicht") is False
