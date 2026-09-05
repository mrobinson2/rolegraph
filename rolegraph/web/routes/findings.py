from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ...config import get_settings
from ...domain.entities import PrincipalType
from ...findings import group_by_rule, summarise
from ...resolver.access import access_paths
from ._shared import dataset, is_htmx, render

router = APIRouter()


@router.get("/findings", response_class=HTMLResponse)
async def findings(
    request: Request,
    severity: str = Query(""),
    rule: str = Query(""),
) -> HTMLResponse:
    data = dataset()
    items = data.findings
    if severity:
        items = [f for f in items if f.severity == severity]
    if rule:
        items = [f for f in items if f.rule_id == rule]
    context = {
        "nav": "findings",
        "findings": items,
        "summary": summarise(data.findings),
        "rules": sorted(group_by_rule(data.findings)),
        "severity_filter": severity,
        "rule_filter": rule,
    }
    template = "partials/finding_cards.html" if is_htmx(request) else "findings.html"
    return render(request, template, context)


@router.get("/privileged", response_class=HTMLResponse)
async def privileged(request: Request) -> HTMLResponse:
    data = dataset()
    settings = get_settings()
    rows = []
    for principal_id, principal in data.graph.principals.items():
        if principal.type is PrincipalType.GROUP:
            continue
        for path in access_paths(data.graph, principal_id):
            if not settings.is_privileged(path.role.name):
                continue
            rows.append(
                {
                    "principal": principal,
                    "role": path.role,
                    "scope_display": path.scope_display,
                    "scope_kind": path.scope_node.kind_label if path.scope_node else "Scope",
                    "is_direct": path.is_direct,
                    "path": path,
                    "via": path.membership_chain,
                }
            )
    rows.sort(
        key=lambda r: (
            r["role"].name.lower(),
            r["principal"].type.value,
            r["principal"].display_name.lower(),
        )
    )
    group_rows = []
    for principal_id, principal in data.graph.principals.items():
        if principal.type is not PrincipalType.GROUP:
            continue
        for assignment in data.graph.assignments_by_principal.get(principal_id, ()):
            role = data.graph.role_definitions.get(assignment.role_definition_id)
            if role is None or not settings.is_privileged(role.name):
                continue
            node = data.graph.scopes.get(assignment.scope)
            group_rows.append(
                {
                    "principal": principal,
                    "role": role,
                    "scope_display": node.display_name if node else assignment.raw_scope,
                    "scope_kind": node.kind_label if node else "Scope",
                }
            )
    context = {
        "nav": "privileged",
        "rows": rows,
        "group_rows": group_rows,
        "privileged_roles": settings.privileged_roles,
        "role_notes": settings.privileged_role_notes,
        "config_file": settings.privileged_roles_file.name,
    }
    return render(request, "privileged.html", context)
