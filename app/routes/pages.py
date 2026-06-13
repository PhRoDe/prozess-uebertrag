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


def job_owner_ok(request: Request, job) -> bool:
    """Darf der aktuelle User auf den Job zugreifen? (Phase 4 Owner-Scoping)

    Legacy-Jobs ohne created_by (vor der Migration angelegt) bleiben für alle
    authentifizierten User zugänglich — kein Lockout. RLS ist mit dem
    service_role-Key wirkungslos, daher passiert das Scoping hier in der App.
    """
    owner = getattr(job, "created_by", None)
    # NUR NULL (Legacy vor Migration) ist offen. Ein leerer String würde sonst
    # alle Jobs öffnen (IDOR) — daher strikt `is None` (Code-Review).
    if owner is None:
        return True
    return owner == current_user(request)["username"]


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    # Authentik already gates this route; just render the upload screen.
    return templates.TemplateResponse(request, "upload.html", {})
