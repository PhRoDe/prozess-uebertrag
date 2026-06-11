"""Authentik forward-auth: identity comes from nginx-injected X-Authentik-* headers.

Behind Authentik, anyone who reaches the app is already authenticated; the
presence of X-Authentik-Username is the proof. There is no app-side login.
"""
from starlette.requests import Request

from app.routes.pages import require_auth, current_user


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw})


def test_require_auth_true_with_authentik_username():
    assert require_auth(_request({"X-Authentik-Username": "philipp"})) is True


def test_require_auth_false_without_header():
    assert require_auth(_request({})) is False


def test_require_auth_false_with_empty_username():
    assert require_auth(_request({"X-Authentik-Username": ""})) is False


def test_current_user_reads_all_headers():
    user = current_user(_request({
        "X-Authentik-Username": "philipp",
        "X-Authentik-Email": "philipp@calandi.de",
        "X-Authentik-Groups": "admins",
    }))
    assert user == {
        "username": "philipp",
        "email": "philipp@calandi.de",
        "groups": "admins",
    }


def test_current_user_defaults_to_empty_strings():
    assert current_user(_request({})) == {"username": "", "email": "", "groups": ""}
