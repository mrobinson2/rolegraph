"""Helpers shared by the route modules."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from ...storage.repository import active_snapshot
from ..state import Dataset, current_dataset


def dataset() -> Dataset:
    return current_dataset()


def render(request: Request, template: str, context: dict) -> HTMLResponse:
    templates = request.app.state.templates
    context.setdefault("snapshot", active_snapshot())
    return templates.TemplateResponse(request, template, context)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"
