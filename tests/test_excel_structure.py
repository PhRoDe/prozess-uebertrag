from app.excel.structure import GUV_HIERARCHY, get_all_codes, code_prefix, match_code


def test_hierarchy_contains_expected_top_level():
    codes = get_all_codes()
    required = [
        "1. Umsatzerlöse", "2. Gesamtleistung", "3. Sonstige betriebliche Erträge",
        "4. Materialaufwand", "5. Personalaufwand", "6. Abschreibungen",
        "7. Sonstige betriebliche Aufwendungen", "12. Ergebnis nach Steuern",
        "14. Jahresüberschuss", "17. Bilanzgewinn",
    ]
    for r in required:
        assert r in codes, f"Missing code: {r}"


def test_hierarchy_has_sonst_betr_aufw_subgroups():
    codes = get_all_codes()
    subs = ["7a. Raumkosten", "7b. Versicherungen, Beiträge und Abgaben",
            "7e. Werbe- und Reisekosten", "7g. Verschiedene betriebliche Kosten"]
    for s in subs:
        assert s in codes, f"Missing subgroup: {s}"


def test_code_prefix_extracts_head():
    assert code_prefix("4a. Aufwendungen für RHB") == "4a"
    assert code_prefix("12. Ergebnis nach Steuern") == "12"
    assert code_prefix("1. Umsatzerlöse") == "1"


def test_match_code_accepts_claude_variations():
    # Claude's shortened form maps to canonical
    assert match_code("4a. Materialaufwand RHB") == "4a. Aufwendungen für RHB und Waren"
    assert match_code("1. Umsatzerlöse") == "1. Umsatzerlöse"
    assert match_code("5a. Löhne und Gehälter") == "5a. Löhne und Gehälter"


def test_match_code_returns_none_on_unknown():
    assert match_code("XY unbekannt") is None
    assert match_code("") is None
    assert match_code(None) is None


def test_details_then_sum_ordering():
    """For each 'sum' entry there must be a preceding 'details' entry
    with the same code, so the sum formula has data to aggregate."""
    prev_details: set[str] = set()
    for entry in GUV_HIERARCHY:
        if entry["kind"] == "details":
            prev_details.add(entry["code"])
        elif entry["kind"] == "sum":
            assert entry["code"] in prev_details, \
                f"Sum row for '{entry['code']}' has no preceding details block"
