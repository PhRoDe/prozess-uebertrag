import bcrypt
from app.auth import verify_password, create_session_token, validate_session_token


def test_verify_password_correct():
    hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
    assert verify_password("secret", hashed) is True


def test_verify_password_wrong():
    hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
    assert verify_password("wrong", hashed) is False


def test_verify_password_invalid_hash():
    assert verify_password("secret", "not-a-bcrypt-hash") is False


def test_session_token_roundtrip():
    token = create_session_token("fixed-secret")
    assert validate_session_token(token, "fixed-secret") is True


def test_session_token_rejects_bad_secret():
    token = create_session_token("secret-a")
    assert validate_session_token(token, "secret-b") is False


def test_session_token_rejects_tampered():
    token = create_session_token("secret") + "x"
    assert validate_session_token(token, "secret") is False
