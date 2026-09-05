from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from ...domain.hierarchy import roots
from ...domain.ids import ScopeParseError, normalize_scope
from ..viewmodels import scope_detail
from ._shared import dataset, render

router = APIRouter()


@router.get("/scopes", response_class=HTMLResponse)
async def scopes(request: Request, scope: str = Query("")) -> HTMLResponse:
    data = dataset()
    if not scope:
        context = {
            "nav": "scopes",
            "roots": roots(data.graph),
            "children_by_scope": data.graph.children_by_scope,
            "scopes": data.graph.scopes,
        }
        return render(request, "scopes.html", context)
    try:
        normalized = normalize_scope(scope)
    except ScopeParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    node = data.graph.scopes.get(normalized)
    if node is None:
        raise HTTPException(status_code=404, detail="Scope not found in the current snapshot.")
    context = scope_detail(data.graph, node, data.findings)
    context["nav"] = "scopes"
    return render(request, "scope_detail.html", context)
