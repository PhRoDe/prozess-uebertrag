from unittest.mock import MagicMock
from app.ratelimit import allow, reset, client_ip


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


def _req(headers=None, host=None):
    req = MagicMock()
    req.headers = headers or {}
    req.client = MagicMock(host=host) if host else None
    return req


def test_client_ip_prefers_x_forwarded_for():
    req = _req(headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1"}, host="10.0.0.1")
    assert client_ip(req) == "1.2.3.4"


def test_client_ip_falls_back_to_request_client():
    req = _req(host="127.0.0.1")
    assert client_ip(req) == "127.0.0.1"


def test_client_ip_global_fallback_when_no_info():
    req = _req()
    assert client_ip(req) == "global"
