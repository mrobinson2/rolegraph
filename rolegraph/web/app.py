"""FastAPI application factory.

Server-rendered Jinja2 with HTMX for partial updates. No build step, no client
framework, no outbound requests.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import get_settings
from .routes import findings as findings_routes
from .routes import identities as identity_routes
from .routes import imports as import_routes
from .routes import overview as overview_routes
from .routes import roles as role_routes
from .routes import scopes as scope_routes
from .state import NoDataset

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def _severity_class(severity: str) -> str:
    return {"high": "sev-high", "medium": "sev-medium", "low": "sev-low"}.get(severity, "sev-low")


def _short_scope(scope: str, length: int = 48) -> str:
    return scope if len(scope) <= length else f"{scope[: length - 1]}…"


TEMPLATES.env.filters["severity_class"] = _severity_class
TEMPLATES.env.filters["short_scope"] = _short_scope
TEMPLATES.env.globals["app_name"] = "RoleGraph"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="RoleGraph",
        description="Self-hosted Azure RBAC access intelligence.",
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.templates = TEMPLATES
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    app.include_router(overview_routes.router)
    app.include_router(identity_routes.router)
    app.include_router(role_routes.router)
    app.include_router(findings_routes.router)
    app.include_router(scope_routes.router)
    app.include_router(import_routes.router)

    @app.exception_handler(NoDataset)
    async def no_dataset(request: Request, _exc: NoDataset) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request, "no_data.html", {"nav": "import"}, status_code=200
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        # The app loads nothing from the network; the policy makes that explicit.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    return app


app = create_app()
