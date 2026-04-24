from app.routes.upload import _safe_filename


def test_safe_filename_strips_directory_components():
    assert _safe_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert _safe_filename("/tmp/injected.pdf") == "injected.pdf"
    assert _safe_filename("normal-name.pdf") == "normal-name.pdf"


def test_safe_filename_strips_dangerous_chars():
    # Control chars, slashes, etc.
    result = _safe_filename("a/b\\c*d?e.pdf")
    assert "/" not in result
    assert "\\" not in result
    assert "*" not in result


def test_safe_filename_fallback_for_empty():
    assert _safe_filename("") == "file.pdf"
    assert _safe_filename(None) == "file.pdf"


def test_safe_filename_preserves_spaces_and_dashes():
    assert _safe_filename("My Jahresabschluss 2024.pdf") == "My Jahresabschluss 2024.pdf"
