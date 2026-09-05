"""Scope-tree navigation: ancestry, descendants, and inheritance checks.

An Azure role assignment applies at its scope *and everywhere below it*. All
inheritance questions in RoleGraph reduce to walking this tree.
"""

from __future__ import annotations

from .entities import AccessGraph, ScopeNode
from .ids import ScopeKind


def ancestors(graph: AccessGraph, scope: str) -> list[ScopeNode]:
    """Ancestors of ``scope``, nearest parent first. Cycle-safe."""
    out: list[ScopeNode] = []
    seen = {scope}
    node = graph.scopes.get(scope)
    while node is not None and node.parent_scope:
        if node.parent_scope in seen:
            break  # malformed data: refuse to loop forever
        seen.add(node.parent_scope)
        parent = graph.scopes.get(node.parent_scope)
        if parent is None:
            break
        out.append(parent)
        node = parent
    return out


def scope_chain(graph: AccessGraph, scope: str) -> list[ScopeNode]:
    """Root-down path to ``scope``, inclusive. Empty if the scope is unknown."""
    node = graph.scopes.get(scope)
    if node is None:
        return []
    return list(reversed(ancestors(graph, scope))) + [node]


def descendants(graph: AccessGraph, scope: str, include_self: bool = False) -> list[str]:
    """Every scope at or below ``scope``, breadth-first. Cycle-safe."""
    out: list[str] = [scope] if include_self else []
    seen = {scope}
    queue = list(graph.children_by_scope.get(scope, ()))
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        out.append(current)
        queue.extend(graph.children_by_scope.get(current, ()))
    return out


def is_ancestor_of(graph: AccessGraph, ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        return False
    return any(node.scope == ancestor for node in ancestors(graph, descendant))


def covers(graph: AccessGraph, assignment_scope: str, target_scope: str) -> bool:
    """True if an assignment made at ``assignment_scope`` reaches ``target_scope``."""
    return assignment_scope == target_scope or is_ancestor_of(graph, assignment_scope, target_scope)


def roots(graph: AccessGraph) -> list[ScopeNode]:
    return [n for n in graph.scopes.values() if n.parent_scope is None]


def scope_breadcrumb(graph: AccessGraph, scope: str) -> str:
    """Human-readable ancestry, e.g. ``Contoso > Production > Payments-Prod``."""
    chain = scope_chain(graph, scope)
    if not chain:
        return scope
    return " > ".join(node.display_name for node in chain)


def orphan_scopes(graph: AccessGraph) -> list[ScopeNode]:
    """Scopes whose declared parent was never imported."""
    return [
        n
        for n in graph.scopes.values()
        if n.parent_scope is not None and n.parent_scope not in graph.scopes
    ]


def subtree_size(graph: AccessGraph, scope: str) -> int:
    return len(descendants(graph, scope))


def kind_of(graph: AccessGraph, scope: str) -> ScopeKind | None:
    node = graph.scopes.get(scope)
    return node.kind if node else None
