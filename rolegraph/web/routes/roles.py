from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from ..viewmodels import role_detail, role_rows
from ._shared import dataset, is_htmx, render

router = APIRouter()


@router.get("/roles", response_class=HTMLResponse)
async def roles(
    request: Request,
    q: str = Query(""),
    type: str = Query("", alias="type"),
) -> HTMLResponse:
    data = dataset()
    rows = role_rows(data.graph, q, type)
    context = {
        "nav": "roles",
        "rows": rows,
        "query": q,
        "type_filter": type,
        "total": len(data.graph.role_definitions),
        "custom_total": len(data.graph.custom_roles),
    }
    template = "partials/role_rows.html" if is_htmx(request) else "roles.html"
    return render(request, template, context)


@router.get("/roles/{role_id:path}", response_class=HTMLResponse)
async def role(request: Request, role_id: str) -> HTMLResponse:
    data = dataset()
    definition = data.graph.role_definitions.get(unquote(role_id))
    if definition is None:
        raise HTTPException(status_code=404, detail="Role definition not found in the current snapshot.")
    context = role_detail(data.graph, definition, data.findings)
    context["nav"] = "roles"
    return render(request, "role_detail.html", context)
