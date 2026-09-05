from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from ...resolver.access import access_paths
from .. import relationship_graph
from ..viewmodels import access_path_view, identity_detail, principal_row, search_principals
from ._shared import dataset, is_htmx, render

router = APIRouter()


@router.get("/identities", response_class=HTMLResponse)
async def identities(
    request: Request,
    q: str = Query("", alias="q"),
    type: str = Query("", alias="type"),
) -> HTMLResponse:
    data = dataset()
    matches = search_principals(data.graph, q, type)
    rows = [principal_row(data.graph, p, data.findings) for p in matches]
    context = {
        "nav": "identities",
        "rows": rows,
        "query": q,
        "type_filter": type,
        "total": len(data.graph.principals),
    }
    template = "partials/identity_rows.html" if is_htmx(request) else "identities.html"
    return render(request, template, context)


@router.get("/identities/{principal_id}", response_class=HTMLResponse)
async def identity(request: Request, principal_id: str) -> HTMLResponse:
    data = dataset()
    principal = data.graph.principals.get(principal_id)
    if principal is None:
        raise HTTPException(status_code=404, detail="Identity not found in the current snapshot.")
    context = identity_detail(data.graph, principal, data.findings)
    context["nav"] = "identities"
    context["diagram"] = relationship_graph.build(
        data.graph, principal, access_paths(data.graph, principal_id)
    )
    return render(request, "identity_detail.html", context)


@router.get("/identities/{principal_id}/path/{assignment_id}", response_class=HTMLResponse)
async def access_path(
    request: Request,
    principal_id: str,
    assignment_id: str,
    scope: str = Query(""),
    chain: str = Query(""),
) -> HTMLResponse:
    data = dataset()
    principal = data.graph.principals.get(principal_id)
    if principal is None:
        raise HTTPException(status_code=404, detail="Identity not found in the current snapshot.")
    wanted_chain = tuple(part for part in chain.split(">") if part) if chain else None
    candidates = [p for p in access_paths(data.graph, principal_id) if p.assignment.id == assignment_id]
    if not candidates:
        raise HTTPException(status_code=404, detail="That access path is not present in this snapshot.")
    path = next((p for p in candidates if wanted_chain and p.membership_chain == wanted_chain), candidates[0])
    context = access_path_view(data.graph, path, scope or None)
    context["nav"] = "identities"
    context["alternatives"] = [p for p in candidates if p is not path]
    return render(request, "access_path.html", context)
