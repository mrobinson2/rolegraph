"""Deterministic, I/O-free assignment and pinned role comparison."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math

from .schema import Observation, TargetState

LIMITATIONS = [
    "Compares Azure RBAC assignments, including their originating scope and exact condition text.",
    "Group membership, PIM eligibility, deny assignments, Lighthouse and service-specific data-plane ACLs are not evaluated.",
    "Active PIM grants visible in roleAssignments are compared like other active assignments.",
    "Role permission changes are checked only for explicitly pinned roleDefinitions; conditions are not executed.",
    "Coverage is limited to declared scopes and assignments returned by Azure to the scanner identity.",
]

# Stable built-in identifiers already present in RoleGraph's demo/domain data.
# Labels are presentation only; approvals always match the identifier itself.
BUILT_IN_LABELS = {
    "8e3af657-a8ff-443c-a75c-2fe8c4bcb635": "Owner",
    "b24988ac-6180-42a0-ab88-20f7382dd24c": "Contributor",
    "acdd72a7-3385-48ef-bd42-f606fba81ae7": "Reader",
    "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9": "User Access Administrator",
    "4633458b-17de-408a-b874-0445c86b69e6": "Key Vault Secrets User",
    "ba92f5b4-2d11-453d-a403-e96b0029c9fe": "Storage Blob Data Contributor",
}


class IncompleteScan(ValueError):
    """A compliance conclusion cannot be made from this observation."""


def compare(target: TargetState, observed: Observation, *, now: datetime | None = None, max_age_hours: float = 36) -> dict:
    now = now or datetime.now(timezone.utc)
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise ValueError("max_age_hours must be positive.")
    if observed.tenantId != target.tenantId:
        raise IncompleteScan("Observation tenant does not match approved target tenant.")
    age = (now - datetime.fromisoformat(observed.collectedAt.replace("Z", "+00:00"))).total_seconds()
    if age < -300 or age > max_age_hours * 3600:
        raise IncompleteScan("Observation is stale or has a future collection timestamp; collect a fresh scan.")
    expected_scopes = {(s.id, s.includeDescendants) for s in target.scopes}
    actual_scopes = [(r.scope.id, r.scope.includeDescendants) for r in observed.scopeResults]
    if len(actual_scopes) != len(set(actual_scopes)) or set(actual_scopes) != expected_scopes:
        raise IncompleteScan("Observation must contain exactly one completed result for every configured scope and descendant setting.")

    # An assignment can appear in several scope queries. Collapse only the same
    # Azure assignment ID; distinct IDs for the same grant are reportable drift.
    by_id = {}
    for result in observed.scopeResults:
        for assignment in result.assignments:
            prior = by_id.get(assignment.id)
            if prior and prior != assignment:
                raise IncompleteScan("Conflicting records for the same Azure assignment ID; retry a stable scan.")
            by_id[assignment.id] = assignment
    actual = defaultdict(list)
    for assignment in by_id.values():
        actual[assignment.key].append(assignment)
    desired = {a.key: a for a in target.assignments}
    principal_names = {a.principalId: a.principalName for a in target.assignments if a.principalName}
    role_names = {**BUILT_IN_LABELS, **{a.roleDefinitionId: a.roleName for a in target.assignments if a.roleName}}
    findings = []
    matched = 0
    for key in sorted(set(actual) | set(desired)):
        expected = desired.get(key)
        current = sorted(actual.get(key, []), key=lambda a: a.id)
        detail = {"principalId": key[0], "roleDefinitionId": key[1], "scope": key[2]}
        detail.update(principalName=principal_names.get(key[0]), roleName=role_names.get(key[1]))
        detail["assignmentIds"] = [a.id for a in current]
        if not current:
            findings.append({"kind": "missing", **detail, "expected": expected.model_dump(exclude_none=True)})
        elif not expected:
            findings.append({"kind": "unexpected", **detail, "actual": [a.model_dump(exclude_none=True) for a in current]})
        else:
            changed = [a for a in current if a.constraints != expected.constraints]
            if changed:
                findings.append({"kind": "changed", **detail, "expected": expected.model_dump(exclude_none=True),
                                 "actual": [a.model_dump(exclude_none=True) for a in changed]})
            else:
                matched += 1
        if len(current) > 1:
            findings.append({"kind": "duplicate", **detail})

    roles = {r.key: r for r in observed.roleDefinitions}
    if len(roles) != len(observed.roleDefinitions) or set(roles) != {r.key for r in target.roleDefinitions}:
        raise IncompleteScan("Observation must contain every pinned role definition exactly once.")
    for role in sorted(target.roleDefinitions, key=lambda r: r.key):
        if role.canonical != roles[role.key].canonical:
            findings.append({"kind": "role-definition-changed", "roleDefinitionId": role.key,
                             "expected": role.model_dump(), "actual": roles[role.key].model_dump()})
    return {
        "schemaVersion": 1, "status": "drift" if findings else "in-sync", "tenantId": target.tenantId,
        "collectedAt": observed.collectedAt, "checkedAt": now.isoformat(),
        "summary": {"approvedAssignments": len(desired), "observedAssignments": len(by_id), "matchedAssignments": matched,
                    **{kind: sum(f["kind"] == kind for f in findings) for kind in
                       ("missing", "unexpected", "changed", "duplicate", "role-definition-changed")}},
        "scopes": [s.model_dump() for s in sorted(target.scopes, key=lambda s: s.id)],
        "findings": findings, "limitations": LIMITATIONS,
    }
