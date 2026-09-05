"""Shaping domain objects for templates.

Templates stay declarative: everything they need is computed here.
"""

from __future__ import annotations

from ..domain.entities import AccessGraph, Principal, PrincipalType, RoleDefinition, ScopeNode
from ..domain.hierarchy import ancestors, descendants, scope_breadcrumb, scope_chain
from ..domain.ids import ScopeKind
from ..findings import Finding
from ..resolver.access import (
    AccessPath,
    access_paths,
    assignees_of_role,
    distinct_grants,
    explain,
    explain_inheritance,
    is_inherited_at,
    path_steps,
    principals_with_access_to,
)
from ..resolver.membership import direct_group_ids, membership_paths, transitive_members

PRINCIPAL_ICON = {
    PrincipalType.USER: "person",
    PrincipalType.GROUP: "group",
    PrincipalType.SERVICE_PRINCIPAL: "app",
    PrincipalType.MANAGED_IDENTITY: "identity",
}

SCOPE_ICON = {
    ScopeKind.TENANT: "tenant",
    ScopeKind.MANAGEMENT_GROUP: "mg",
    ScopeKind.SUBSCRIPTION: "subscription",
    ScopeKind.RESOURCE_GROUP: "rg",
    ScopeKind.RESOURCE: "resource",
}


def search_principals(graph: AccessGraph, query: str, ptype: str = "", limit: int = 200) -> list[Principal]:
    """Case-insensitive match on name, UPN, email, department or id."""
    q = (query or "").strip().lower()
    results = []
    for principal in graph.principals.values():
        if ptype and principal.type.value != ptype:
            continue
        if q:
            haystack = " ".join(
                filter(
                    None,
                    (
                        principal.display_name,
                        principal.upn,
                        principal.email,
                        principal.department,
                        principal.app_id,
                        principal.id,
                    ),
                )
            ).lower()
            if q not in haystack:
                continue
        results.append(principal)
    results.sort(key=lambda p: (p.type.value, p.display_name.lower()))
    return results[:limit]


def principal_row(graph: AccessGraph, principal: Principal, findings: list[Finding]) -> dict:
    paths = access_paths(graph, principal.id)
    grants = distinct_grants(paths)
    return {
        "principal": principal,
        "icon": PRINCIPAL_ICON[principal.type],
        "grant_count": len(grants),
        "group_count": len({p.group_id for p in membership_paths(graph, principal.id)}),
        "top_scope": _broadest_scope_label(graph, paths),
        "finding_count": sum(1 for f in findings if f.principal and f.principal.id == principal.id),
        "high_findings": sum(
            1 for f in findings if f.principal and f.principal.id == principal.id and f.severity == "high"
        ),
    }


def _broadest_scope_label(graph: AccessGraph, paths: list[AccessPath]) -> str:
    if not paths:
        return "No access"
    broadest = min(paths, key=lambda p: (p.scope_node.depth if p.scope_node else 9))
    return broadest.scope_display


def identity_detail(graph: AccessGraph, principal: Principal, findings: list[Finding]) -> dict:
    paths = access_paths(graph, principal.id)
    grants = distinct_grants(paths)
    grant_rows = []
    for key, group in grants.items():
        first = group[0]
        grant_rows.append(
            {
                "role": first.role,
                "scope": first.scope,
                "scope_node": first.scope_node,
                "scope_display": first.scope_display,
                "scope_kind": first.scope_node.kind_label if first.scope_node else "Scope",
                "breadcrumb": scope_breadcrumb(graph, first.scope),
                "is_direct": any(p.is_direct for p in group),
                "route_count": len(group),
                "paths": group,
                "explanation": explain(graph, first),
                "assignment": first.assignment,
                "reach": len(descendants(graph, first.scope, include_self=True)),
            }
        )
    grant_rows.sort(key=lambda r: (r["scope_node"].depth if r["scope_node"] else 9, r["role"].name.lower()))

    memberships = membership_paths(graph, principal.id)
    direct = set(direct_group_ids(graph, principal.id))
    group_rows = []
    for group_id in sorted({m.group_id for m in memberships}):
        group = graph.principals.get(group_id)
        chains = [m.chain for m in memberships if m.group_id == group_id]
        group_rows.append(
            {
                "group": group,
                "id": group_id,
                "name": group.display_name if group else group_id,
                "is_direct": group_id in direct,
                "chains": chains,
                "chain_labels": [
                    " > ".join(
                        (graph.principals[g].display_name if g in graph.principals else g) for g in chain
                    )
                    for chain in chains
                ],
            }
        )

    members = []
    if principal.type is PrincipalType.GROUP:
        for member_id in sorted(transitive_members(graph, principal.id)):
            member = graph.principals.get(member_id)
            members.append(
                {
                    "id": member_id,
                    "principal": member,
                    "name": member.display_name if member else member_id,
                    "is_direct": member_id in graph.members_by_group.get(principal.id, ()),
                }
            )

    return {
        "principal": principal,
        "icon": PRINCIPAL_ICON[principal.type],
        "grants": grant_rows,
        "groups": group_rows,
        "members": members,
        "paths": paths,
        "findings": [f for f in findings if f.principal and f.principal.id == principal.id],
        "reach": _reach_summary(graph, paths),
    }


def _reach_summary(graph: AccessGraph, paths: list[AccessPath]) -> dict[str, int]:
    reached: set[str] = set()
    for path in paths:
        reached.update(descendants(graph, path.scope, include_self=True))
    counts = {kind.value: 0 for kind in ScopeKind}
    for scope in reached:
        node = graph.scopes.get(scope)
        if node:
            counts[node.kind.value] += 1
    counts["total"] = len(reached)
    return counts


def access_path_view(graph: AccessGraph, path: AccessPath, target_scope: str | None = None) -> dict:
    target = target_scope or path.scope
    inherited_chain = []
    if target != path.scope:
        chain = scope_chain(graph, target)
        started = False
        for node in chain:
            if node.scope == path.scope:
                started = True
            if started:
                inherited_chain.append(node)
    return {
        "path": path,
        "steps": path_steps(graph, path),
        "explanation": explain(graph, path),
        "inheritance": explain_inheritance(graph, path, target),
        "inherited_chain": inherited_chain,
        "is_inherited": is_inherited_at(path, target),
        "target_scope": target,
        "target_node": graph.scopes.get(target),
        "role": path.role,
        "permissions": role_permissions(path.role),
        "reach": _scope_reach_rows(graph, path.scope),
    }


def _scope_reach_rows(graph: AccessGraph, scope: str, limit: int = 60) -> list[ScopeNode]:
    nodes = [graph.scopes[s] for s in descendants(graph, scope, include_self=True) if s in graph.scopes]
    nodes.sort(key=lambda n: (n.depth, n.display_name.lower()))
    return nodes[:limit]


def role_permissions(role: RoleDefinition) -> dict[str, list[str]]:
    return {
        "actions": list(role.all_actions),
        "notActions": list(role.all_not_actions),
        "dataActions": list(role.all_data_actions),
        "notDataActions": [a for p in role.permissions for a in p.not_data_actions],
    }


def role_rows(graph: AccessGraph, query: str = "", role_type: str = "") -> list[dict]:
    q = (query or "").strip().lower()
    rows = []
    assignment_counts: dict[str, int] = {}
    for assignment in graph.role_assignments:
        assignment_counts[assignment.role_definition_id] = (
            assignment_counts.get(assignment.role_definition_id, 0) + 1
        )
    for role in graph.role_definitions.values():
        if role_type == "custom" and not role.is_custom:
            continue
        if role_type == "builtin" and role.is_custom:
            continue
        if q and q not in f"{role.name} {role.description or ''} {role.id}".lower():
            continue
        rows.append(
            {
                "role": role,
                "assignment_count": assignment_counts.get(role.id, 0),
                "action_count": len(role.all_actions),
                "wildcards": len(role.wildcard_actions),
                "can_assign_roles": role.can_assign_roles,
            }
        )
    rows.sort(key=lambda r: (not r["role"].is_custom, r["role"].name.lower()))
    return rows


def role_detail(graph: AccessGraph, role: RoleDefinition, findings: list[Finding]) -> dict:
    paths = assignees_of_role(graph, role.id)
    holders: dict[str, dict] = {}
    for path in paths:
        entry = holders.setdefault(
            path.principal.id,
            {"principal": path.principal, "scopes": set(), "direct": False, "via": set()},
        )
        entry["scopes"].add(path.scope_display)
        if path.is_direct:
            entry["direct"] = True
        else:
            holder = graph.principals.get(path.granted_via_id)
            entry["via"].add(holder.display_name if holder else path.granted_via_id)
    holder_rows = sorted(
        (
            {
                "principal": v["principal"],
                "scopes": sorted(v["scopes"]),
                "direct": v["direct"],
                "via": sorted(v["via"]),
            }
            for v in holders.values()
        ),
        key=lambda r: (r["principal"].type.value, r["principal"].display_name.lower()),
    )
    assignments = []
    for assignment in graph.role_assignments:
        if assignment.role_definition_id != role.id:
            continue
        holder = graph.principals.get(assignment.principal_id)
        assignments.append(
            {
                "assignment": assignment,
                "principal": holder,
                "principal_name": holder.display_name if holder else assignment.principal_id,
                "scope_node": graph.scopes.get(assignment.scope),
                "breadcrumb": scope_breadcrumb(graph, assignment.scope),
            }
        )
    return {
        "role": role,
        "permissions": role_permissions(role),
        "holders": holder_rows,
        "assignments": assignments,
        "findings": [f for f in findings if f.role and f.role.id == role.id],
    }


def scope_detail(graph: AccessGraph, node: ScopeNode, findings: list[Finding]) -> dict:
    paths = principals_with_access_to(graph, node.scope)
    rows: dict[tuple[str, str], dict] = {}
    for path in paths:
        key = (path.principal.id, path.role.id)
        entry = rows.setdefault(
            key,
            {
                "principal": path.principal,
                "role": path.role,
                "inherited": True,
                "assigned_at": path.scope_display,
                "assigned_scope": path.scope,
                "direct": path.is_direct,
                "path": path,
            },
        )
        if not is_inherited_at(path, node.scope):
            entry["inherited"] = False
    ordered = sorted(
        rows.values(),
        key=lambda r: (r["principal"].type.value, r["principal"].display_name.lower(), r["role"].name),
    )
    children = [graph.scopes[s] for s in graph.children_by_scope.get(node.scope, ()) if s in graph.scopes]
    children.sort(key=lambda n: (n.kind.value, n.display_name.lower()))
    return {
        "node": node,
        "icon": SCOPE_ICON[node.kind],
        "breadcrumb": scope_chain(graph, node.scope),
        "ancestors": ancestors(graph, node.scope),
        "children": children,
        "access": ordered,
        "descendant_count": len(descendants(graph, node.scope)),
        "findings": [f for f in findings if f.scope == node.scope],
    }


def overview(graph: AccessGraph, findings: list[Finding], snapshot) -> dict:
    by_type = {t: len(graph.principals_of_type(t)) for t in PrincipalType}
    privileged = [f for f in findings if f.severity == "high"]
    return {
        "tenant": graph.tenant,
        "snapshot": snapshot,
        "identity_counts": by_type,
        "identity_total": len(graph.principals),
        "assignment_count": len(graph.role_assignments),
        "role_count": len(graph.role_definitions),
        "custom_role_count": len(graph.custom_roles),
        "subscription_count": len(graph.scopes_of_kind(ScopeKind.SUBSCRIPTION)),
        "management_group_count": len(graph.scopes_of_kind(ScopeKind.MANAGEMENT_GROUP)),
        "resource_group_count": len(graph.scopes_of_kind(ScopeKind.RESOURCE_GROUP)),
        "resource_count": len(graph.scopes_of_kind(ScopeKind.RESOURCE)),
        "membership_count": len(graph.memberships),
        "findings": findings,
        "high_findings": privileged,
        "top_findings": findings[:6],
    }
