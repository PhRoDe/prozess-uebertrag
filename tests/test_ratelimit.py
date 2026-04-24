from app.ratelimit import allow, reset


def setup_function(func):
    reset()


def test_allow_within_budget():
    for _ in range(5):
        assert allow("key1", max_hits=5, window_seconds=60) is True


def test_reject_over_budget():
    for _ in range(3):
        allow("key2", max_hits=3, window_seconds=60)
    assert allow("key2", max_hits=3, window_seconds=60) is False


def test_different_keys_have_independent_budgets():
    for _ in range(3):
        allow("a", max_hits=3, window_seconds=60)
    assert allow("a", max_hits=3, window_seconds=60) is False
    # b wurde noch nicht benutzt
    assert allow("b", max_hits=3, window_seconds=60) is True


def test_reset_clears_budget():
    for _ in range(3):
        allow("r", max_hits=3, window_seconds=60)
    assert allow("r", max_hits=3, window_seconds=60) is False
    reset("r")
    assert allow("r", max_hits=3, window_seconds=60) is True
