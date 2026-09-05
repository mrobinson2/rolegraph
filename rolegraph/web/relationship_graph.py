"""Focused relationship diagram for one identity.

Four columns - identity, groups, roles, scopes - laid out deterministically so
the same identity always renders the same picture. Complements the tables; it is
not a whole-tenant graph explorer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.entities import AccessGraph, Principal
from ..resolver.access import AccessPath

COLUMN_X = {"identity": 90, "group": 300, "role": 530, "scope": 770}
NODE_WIDTH = {"identity": 150, "group": 170, "role": 170, "scope": 180}
ROW_HEIGHT = 54
TOP_MARGIN = 30
NODE_HEIGHT = 34


@dataclass(slots=True)
class GraphNode:
    key: str
    column: str
    label: str
    sublabel: str
    x: int
    y: int
    width: int
    href: str | None = None
    emphasis: bool = False

    @property
    def cx(self) -> int:
        return self.x + self.width // 2

    @property
    def cy(self) -> int:
        return self.y + NODE_HEIGHT // 2


@dataclass(slots=True)
class GraphEdge:
    source: str
    target: str
    d: str
    dashed: bool = False


@dataclass(slots=True)
class RelationshipDiagram:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    width: int = 980
    height: int = 260

    @property
    def is_empty(self) -> bool:
        return len(self.nodes) <= 1


def _curve(a: GraphNode, b: GraphNode) -> str:
    x1, y1 = a.x + a.width, a.cy
    x2, y2 = b.x, b.cy
    mid = (x1 + x2) / 2
    return f"M {x1} {y1} C {mid} {y1}, {mid} {y2}, {x2} {y2}"


def build(graph: AccessGraph, principal: Principal, paths: list[AccessPath], max_rows: int = 12) -> RelationshipDiagram:
    diagram = RelationshipDiagram()
    columns: dict[str, list[str]] = {"group": [], "role": [], "scope": []}
    nodes: dict[str, GraphNode] = {}
    edge_pairs: set[tuple[str, str, bool]] = set()

    def ensure(column: str, key: str, label: str, sublabel: str, href: str | None, emphasis: bool = False) -> str:
        node_key = f"{column}:{key}"
        if node_key not in nodes:
            if column != "identity" and len(columns[column]) >= max_rows:
                return ""
            index = 0 if column == "identity" else len(columns[column])
            if column != "identity":
                columns[column].append(node_key)
            nodes[node_key] = GraphNode(
                key=node_key,
                column=column,
                label=label,
                sublabel=sublabel,
                x=COLUMN_X[column],
                y=TOP_MARGIN + index * ROW_HEIGHT,
                width=NODE_WIDTH[column],
                href=href,
                emphasis=emphasis,
            )
        return node_key

    identity_key = ensure(
        "identity", principal.id, principal.display_name, principal.type_label, None, emphasis=True
    )

    for path in paths:
        previous = identity_key
        for group_id in path.membership_chain:
            group = graph.principals.get(group_id)
            key = ensure(
                "group",
                group_id,
                group.display_name if group else group_id,
                "Group",
                f"/identities/{group_id}",
            )
            if not key:
                break
            edge_pairs.add((previous, key, False))
            previous = key
        else:
            role_key = ensure(
                "role",
                path.role.id,
                path.role.name,
                "Custom role" if path.role.is_custom else "Built-in role",
                f"/roles/{_quote(path.role.id)}",
            )
            if not role_key:
                continue
            edge_pairs.add((previous, role_key, False))
            scope_key = ensure(
                "scope",
                path.scope,
                path.scope_display,
                path.scope_node.kind_label if path.scope_node else "Scope",
                f"/scopes?scope={_quote(path.scope)}" if path.scope_node else None,
            )
            if scope_key:
                edge_pairs.add((role_key, scope_key, not path.is_direct))

    diagram.nodes = list(nodes.values())
    for source, target, dashed in sorted(edge_pairs):
        if source in nodes and target in nodes:
            diagram.edges.append(GraphEdge(source, target, _curve(nodes[source], nodes[target]), dashed))
    rows = max((len(v) for v in columns.values()), default=1)
    diagram.height = TOP_MARGIN * 2 + max(rows, 1) * ROW_HEIGHT
    return diagram


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
