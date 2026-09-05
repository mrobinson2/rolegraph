"""Findings engine.

Every rule is a pure function over the graph. Same input, same output, every
time - no scoring, no heuristics, no model calls. ``severity`` is a fixed label
attached to the rule, not a computed risk score.

Findings are observations, not vulnerabilities. A finding says what was
detected and why it matters; whether it is acceptable is the operator's call.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..domain.entities import AccessGraph, Principal, RoleDefinition
from ..domain.hierarchy import scope_breadcrumb
from ..domain.ids import ScopeKind
from ..resolver.access import AccessPath, access_paths

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True, slots=True)
class Finding:
    """One detected observation, fully explained."""

    rule_id: str
    title: str
    severity: str
    what: str
    why: str
    principal: Principal | None = None
    role: RoleDefinition | None = None
    scope: str | None = None
    scope_display: str = ""
    paths: tuple[AccessPath, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        parts = [
            self.rule_id,
            self.principal.id if self.principal else "-",
            self.role.id if self.role else "-",
            self.scope or "-",
        ]
        return "|".join(parts)

    @property
    def severity_rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 9)


@dataclass(slots=True)
class RuleContext:
    """Everything the rules need, resolved once per run."""

    graph: AccessGraph
    settings: Settings
    paths_by_principal: dict[str, list[AccessPath]] = field(default_factory=dict)

    @classmethod
    def build(cls, graph: AccessGraph, settings: Settings | None = None) -> RuleContext:
        settings = settings or get_settings()
        ctx = cls(graph=graph, settings=settings)
        ctx.paths_by_principal = {pid: access_paths(graph, pid) for pid in graph.principals}
        return ctx

    def all_paths(self) -> Iterable[AccessPath]:
        for paths in self.paths_by_principal.values():
            yield from paths

    def is_privileged(self, role: RoleDefinition) -> bool:
        return self.settings.is_privileged(role.name)

    def breadcrumb(self, scope: str) -> str:
        return scope_breadcrumb(self.graph, scope)

    def scope_label(self, scope: str) -> str:
        node = self.graph.scopes.get(scope)
        return node.display_name if node else scope


Rule = Callable[[RuleContext], Iterable[Finding]]

_RULES: list[tuple[str, Rule]] = []


def rule(rule_id: str) -> Callable[[Rule], Rule]:
    def register(func: Rule) -> Rule:
        _RULES.append((rule_id, func))
        return func

    return register


def registered_rules() -> list[str]:
    return [rule_id for rule_id, _ in _RULES]


def evaluate(graph: AccessGraph, settings: Settings | None = None) -> list[Finding]:
    """Run every rule. Output is sorted, so two runs produce identical lists."""
    ctx = RuleContext.build(graph, settings)
    found: dict[str, Finding] = {}
    for _rule_id, func in _RULES:
        for finding in func(ctx):
            found.setdefault(finding.key, finding)
    return sorted(
        found.values(),
        key=lambda f: (f.severity_rank, f.rule_id, (f.principal.display_name if f.principal else ""), f.scope or ""),
    )


def summarise(findings: list[Finding]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0, "total": len(findings)}
    for finding in findings:
        if finding.severity in counts:
            counts[finding.severity] += 1
    return counts


def group_by_rule(findings: list[Finding]) -> dict[str, list[Finding]]:
    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.rule_id, []).append(finding)
    return grouped


def findings_for_principal(findings: list[Finding], principal_id: str) -> list[Finding]:
    return [f for f in findings if f.principal and f.principal.id == principal_id]


def findings_for_scope(findings: list[Finding], scope: str) -> list[Finding]:
    return [f for f in findings if f.scope == scope]


BROAD_SCOPE_KINDS = (ScopeKind.TENANT, ScopeKind.MANAGEMENT_GROUP)
