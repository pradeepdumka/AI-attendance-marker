"""HTML pages for sign-in, registration, and the public home page.

The files live in the repository `frontend` directory. API routes stay
on their own paths, so these pages do not replace `/auth` or `/docs`.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

_FRONTEND = Path(__file__).resolve().parents[3] / "frontend"
ASSETS_DIR = _FRONTEND / "assets"

router = APIRouter(include_in_schema=False)

_PAGES = {
    "/": "index.html",
    "/login": "login.html",
    "/register": "register.html",
    "/account": "account.html",
    "/dashboard": "dashboard.html",
}


def _page(filename: str) -> FileResponse:
    path = _FRONTEND / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(path)


for _route, _filename in _PAGES.items():
    router.add_api_route(_route, lambda filename=_filename: _page(filename), methods=["GET"])


@router.get("/dashboard/{rest:path}")
def dashboard_section(rest: str) -> FileResponse:
    """Serve the dashboard shell for list, detail, and form addresses."""
    return _page("dashboard.html")
