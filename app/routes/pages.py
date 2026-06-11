from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def require_auth(request: Request) -> bool:
    # Behind Authentik (calandi-tools): nginx forward-auth injects X-Authentik-*.
    # Anyone who reaches the page is already authenticated -> the header is proof.
    return bool(request.headers.get("X-Authentik-Username"))


def current_user(request: Request) -> dict:
    return {
        "username": request.headers.get("X-Authentik-Username", ""),
        "email": request.headers.get("X-Authentik-Email", ""),
        "groups": request.headers.get("X-Authentik-Groups", ""),
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    # Authentik already gates this route; just render the upload screen.
    return templates.TemplateResponse(request, "upload.html", {})
