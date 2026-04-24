import bcrypt
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 Tage


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_session_token(secret: str) -> str:
    signer = TimestampSigner(secret)
    return signer.sign(b"ok").decode()


def validate_session_token(token: str, secret: str) -> bool:
    signer = TimestampSigner(secret)
    try:
        signer.unsign(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False
