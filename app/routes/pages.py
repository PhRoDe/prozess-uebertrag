from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import create_session_token, validate_session_token, verify_password
from app.config import get_settings
from app.ratelimit import allow as rate_allow, client_ip

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

COOKIE_NAME = "pu_session"


def require_auth(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    return validate_session_token(token, get_settings().session_secret)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, password: str = Form(...)) -> Response:
    # Fix: Brute-Force-Schutz, 10 Versuche pro 15 min pro IP
    # Railway proxies requests through Fastly — X-Forwarded-For is the real client
    ip = client_ip(request)
    print(f"LOGIN_ATTEMPT xff={request.headers.get('x-forwarded-for')!r} "
          f"client={request.client.host if request.client else None!r} "
          f"resolved={ip!r}", flush=True)
    if not rate_allow(f"login:{ip}", max_hits=10, window_seconds=900):
        raise HTTPException(status_code=429,
                            detail="Zu viele Login-Versuche. Bitte 15 min warten.")
    s = get_settings()
    if not verify_password(password, s.app_password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Falsches Passwort"},
            status_code=401,
        )
    token = create_session_token(s.session_secret)
    resp = RedirectResponse(url="/", status_code=303)
    # Fix 1B: secure=True bricht HTTP-Dev — environmentsensitiv
    use_secure = s.public_base_url.startswith("https://")
    resp.set_cookie(
        COOKIE_NAME, token,
        httponly=True, secure=use_secure, samesite="strict",
        max_age=60 * 60 * 24 * 7,
    )
    return resp


@router.post("/logout")
def logout() -> Response:
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not require_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "upload.html", {})
