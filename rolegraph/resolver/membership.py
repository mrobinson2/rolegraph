"""Recursive Entra ID group membership resolution.

A principal's effective groups include groups it belongs to directly and every
group reachable through nested membership. Real tenants contain accidental
membership cycles, so every walk here is cycle-safe.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.entities import AccessGraph, Principal

#: Guard rails. Real nesting is shallow; these stop pathological data from
#: turning a page render into an exponential walk.
MAX_DEPTH = 20
MAX_PATHS = 500


@dataclass(frozen=True, slots=True)
class MembershipPath:
    """How a principal reaches a group.

    ``chain`` runs from the group the principal joins first to the group that
    actually holds the role assignment. An empty chain means "the principal
    itself" - i.e. a direct assignment.
    """

    group_id: str
    chain: tuple[str, ...]

    @property
    def depth(self) -> int:
        return len(self.chain)

    @property
    def is_direct(self) -> bool:
        return len(self.chain) == 1

    @property
    def is_nested(self) -> bool:
        return len(self.chain) > 1


def membership_paths(
    graph: AccessGraph,
    principal_id: str,
    max_depth: int = MAX_DEPTH,
    max_paths: int = MAX_PATHS,
) -> list[MembershipPath]:
    """Every distinct nesting path from ``principal_id`` up to a group.

    Multiple paths to the same group are all returned - that duplication is
    itself a finding ("same role via multiple paths").
    """
    results: list[MembershipPath] = []

    def walk(current: str, chain: tuple[str, ...], visited: frozenset[str]) -> None:
        if len(results) >= max_paths or len(chain) >= max_depth:
            return
        for group_id in graph.groups_by_member.get(current, ()):
            if group_id in visited:
                continue  # membership cycle
            path = chain + (group_id,)
            results.append(MembershipPath(group_id=group_id, chain=path))
            walk(group_id, path, visited | {group_id})

    walk(principal_id, (), frozenset({principal_id}))
    return results


def effective_group_ids(graph: AccessGraph, principal_id: str) -> set[str]:
    """Flat set of every group the principal is in, directly or via nesting."""
    return {p.group_id for p in membership_paths(graph, principal_id)}


def direct_group_ids(graph: AccessGraph, principal_id: str) -> list[str]:
    return list(graph.groups_by_member.get(principal_id, ()))


def transitive_members(
    graph: AccessGraph, group_id: str, max_depth: int = MAX_DEPTH
) -> set[str]:
    """Every principal that is a member of ``group_id``, directly or nested."""
    out: set[str] = set()
    stack: list[tuple[str, int]] = [(group_id, 0)]
    seen = {group_id}
    while stack:
        current, depth = stack.pop()
        if depth >= max_depth:
            continue
        for member_id in graph.members_by_group.get(current, ()):
            if member_id in seen:
                continue
            seen.add(member_id)
            out.add(member_id)
            stack.append((member_id, depth + 1))
    return out


def membership_cycles(graph: AccessGraph) -> list[tuple[str, ...]]:
    """Detect group membership cycles. Reported as import warnings."""
    cycles: list[tuple[str, ...]] = []
    seen_signatures: set[frozenset[str]] = set()

    def walk(node: str, path: tuple[str, ...], on_path: set[str]) -> None:
        for group_id in graph.groups_by_member.get(node, ()):
            if group_id in on_path:
                start = path.index(group_id)
                cycle = path[start:] + (group_id,)
                signature = frozenset(cycle)
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    cycles.append(cycle)
                continue
            if len(path) >= MAX_DEPTH:
                continue
            walk(group_id, path + (group_id,), on_path | {group_id})

    for group_id in list(graph.members_by_group.keys()):
        walk(group_id, (group_id,), {group_id})
    return cycles


def describe_chain(graph: AccessGraph, chain: tuple[str, ...]) -> str:
    """``Azure-Developers > Azure-Platform-Admins`` for display."""
    names = []
    for gid in chain:
        principal: Principal | None = graph.principals.get(gid)
        names.append(principal.display_name if principal else gid)
    return " > ".join(names)
