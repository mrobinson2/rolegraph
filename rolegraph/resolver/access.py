"""Effective access resolution.

One access path answers the whole question the product promises:

    identity -> (group nesting) -> role assignment -> role definition
             -> scope the assignment was made at -> scopes it is inherited by

Everything the UI renders - the identity explorer, the access path screen, the
findings - is built from these objects.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.entities import AccessGraph, Principal, RoleAssignment, RoleDefinition, ScopeNode
from ..domain.hierarchy import covers, descendants, scope_breadcrumb, scope_chain
from ..domain.ids import SCOPE_DEPTH, ScopeKind
from .membership import describe_chain, membership_paths


@dataclass(frozen=True, slots=True)
class AccessPath:
    """A single, fully explained route from an identity to a permission."""

    principal: Principal
    assignment: RoleAssignment
    role: RoleDefinition
    scope_node: ScopeNode | None
    membership_chain: tuple[str, ...] = ()  # group ids, nearest first; empty = direct

    @property
    def is_direct(self) -> bool:
        return not self.membership_chain

    @property
    def is_nested_group(self) -> bool:
        return len(self.membership_chain) > 1

    @property
    def granted_via_id(self) -> str:
        """The principal the assignment is actually attached to."""
        return self.membership_chain[-1] if self.membership_chain else self.principal.id

    @property
    def scope(self) -> str:
        return self.assignment.scope

    @property
    def scope_kind(self) -> ScopeKind | None:
        return self.scope_node.kind if self.scope_node else None

    @property
    def scope_display(self) -> str:
        return self.scope_node.display_name if self.scope_node else self.assignment.raw_scope

    @property
    def key(self) -> tuple:
        """Identity of the grant, ignoring which path reached it."""
        return (self.principal.id, self.role.id, self.assignment.scope)


def access_paths(graph: AccessGraph, principal_id: str) -> list[AccessPath]:
    """Every role assignment that reaches ``principal_id``, direct or inherited via groups."""
    principal = graph.principals.get(principal_id)
    if principal is None:
        return []

    routes: list[tuple[str, tuple[str, ...]]] = [(principal_id, ())]
    for mp in membership_paths(graph, principal_id):
        routes.append((mp.group_id, mp.chain))

    out: list[AccessPath] = []
    for holder_id, chain in routes:
        for assignment in graph.assignments_by_principal.get(holder_id, ()):
            role = graph.role_definitions.get(assignment.role_definition_id)
            if role is None:
                continue  # dangling role reference; the importer already warned
            out.append(
                AccessPath(
                    principal=principal,
                    assignment=assignment,
                    role=role,
                    scope_node=graph.scopes.get(assignment.scope),
                    membership_chain=chain,
                )
            )
    out.sort(key=_path_sort_key)
    return out


def _path_sort_key(path: AccessPath) -> tuple:
    depth = SCOPE_DEPTH.get(path.scope_kind, 9) if path.scope_kind else 9
    return (depth, path.role.name.lower(), len(path.membership_chain))


def distinct_grants(paths: list[AccessPath]) -> dict[tuple, list[AccessPath]]:
    """Group paths by (identity, role, scope). More than one path = duplicate route."""
    grouped: dict[tuple, list[AccessPath]] = {}
    for p in paths:
        grouped.setdefault(p.key, []).append(p)
    return grouped


def reached_scopes(graph: AccessGraph, path: AccessPath) -> list[str]:
    """Scopes the assignment applies to: its own scope plus everything beneath it."""
    return descendants(graph, path.scope, include_self=True)


def access_at_scope(graph: AccessGraph, principal_id: str, target_scope: str) -> list[AccessPath]:
    """The subset of a principal's access that applies at ``target_scope``."""
    return [p for p in access_paths(graph, principal_id) if covers(graph, p.scope, target_scope)]


def is_inherited_at(path: AccessPath, target_scope: str) -> bool:
    return path.scope != target_scope


def principals_with_access_to(graph: AccessGraph, target_scope: str) -> list[AccessPath]:
    """Reverse lookup: everyone who can reach ``target_scope``, and how."""
    out: list[AccessPath] = []
    for principal_id in graph.principals:
        out.extend(access_at_scope(graph, principal_id, target_scope))
    return out


def assignees_of_role(graph: AccessGraph, role_definition_id: str) -> list[AccessPath]:
    """Every identity holding a role, including identities that get it via a group."""
    out: list[AccessPath] = []
    for principal_id, principal in graph.principals.items():
        for path in access_paths(graph, principal_id):
            if path.role.id == role_definition_id:
                out.append(path)
    return out


def direct_assignments_of_role(graph: AccessGraph, role_definition_id: str) -> list[RoleAssignment]:
    return [ra for ra in graph.role_assignments if ra.role_definition_id == role_definition_id]


# --- plain-English explanation -------------------------------------------------


def explain(graph: AccessGraph, path: AccessPath) -> str:
    """One sentence a non-engineer can read.

    "Jane Smith has Contributor on Production because she is a member of
    Azure-Platform-Admins, which has Contributor assigned at the Production
    management group."
    """
    who = path.principal.display_name
    role = path.role.name
    where = path.scope_display
    where_kind = path.scope_node.kind_label.lower() if path.scope_node else "scope"

    if path.is_direct:
        return f"{who} has {role} on {where} through a direct assignment at the {where_kind} {where}."

    chain_names = describe_chain(graph, path.membership_chain)
    holder = graph.principals.get(path.granted_via_id)
    holder_name = holder.display_name if holder else path.granted_via_id
    if path.is_nested_group:
        return (
            f"{who} has {role} on {where} because they are a member of {chain_names}, "
            f"and {holder_name} has {role} assigned at the {where_kind} {where}."
        )
    return (
        f"{who} has {role} on {where} because they are a member of {holder_name}, "
        f"which has {role} assigned at the {where_kind} {where}."
    )


def explain_inheritance(graph: AccessGraph, path: AccessPath, target_scope: str) -> str:
    """Why the access reaches a scope below where it was assigned."""
    if not is_inherited_at(path, target_scope):
        return f"Assigned directly at {path.scope_display}."
    chain = scope_chain(graph, target_scope)
    from_index = next((i for i, n in enumerate(chain) if n.scope == path.scope), None)
    if from_index is None:
        return f"Inherited from {path.scope_display}."
    trail = " > ".join(n.display_name for n in chain[from_index:])
    return f"Inherited from {path.scope_display}: {trail}."


def path_steps(graph: AccessGraph, path: AccessPath) -> list[dict]:
    """The access path rendered as ordered, displayable steps."""
    steps: list[dict] = [
        {
            "kind": "identity",
            "label": path.principal.display_name,
            "detail": path.principal.type_label,
            "id": path.principal.id,
        }
    ]
    for group_id in path.membership_chain:
        group = graph.principals.get(group_id)
        steps.append(
            {
                "kind": "group",
                "label": group.display_name if group else group_id,
                "detail": "Member of",
                "id": group_id,
            }
        )
    steps.append(
        {
            "kind": "role",
            "label": path.role.name,
            "detail": "Custom role" if path.role.is_custom else "Built-in role",
            "id": path.role.id,
        }
    )
    steps.append(
        {
            "kind": "scope",
            "label": path.scope_display,
            "detail": f"Assigned at {path.scope_node.kind_label.lower()}"
            if path.scope_node
            else "Assigned at scope",
            "id": path.scope,
            "breadcrumb": scope_breadcrumb(graph, path.scope),
        }
    )
    return steps
