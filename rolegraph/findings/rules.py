"""The rule set.

Each rule answers four questions in plain English: what was detected, which
identity and role and scope it concerns, why it matters, and which access path
supports it. Nothing here estimates likelihood or assigns a score.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.hierarchy import descendants, is_ancestor_of
from ..domain.entities import PrincipalType
from ..domain.ids import ScopeKind
from ..resolver.access import distinct_grants
from ..resolver.membership import describe_chain
from .engine import Finding, RuleContext, rule

#: A write-capable role assigned at or above this many subscriptions counts as
#: an unusually broad grant.
BROAD_SCOPE_SUBSCRIPTION_THRESHOLD = 2


def _subscriptions_under(ctx: RuleContext, scope: str) -> int:
    covered = descendants(ctx.graph, scope, include_self=True)
    return sum(
        1
        for s in covered
        if (node := ctx.graph.scopes.get(s)) and node.kind is ScopeKind.SUBSCRIPTION
    )


#: Suffixes that identify a read-only or invoke-only operation.
READ_ONLY_SUFFIXES = ("/read", "/action")


def _grants_write(role) -> bool:
    """True if any action can change state.

    Read-only estates use ``*/read``-shaped actions. Anything else - a bare
    ``*``, a namespace wildcard, or a named write/delete operation - can modify
    something.
    """
    return any(
        not action.lower().endswith(READ_ONLY_SUFFIXES) for action in role.all_actions
    )


@rule("direct-owner")
def direct_owner(ctx: RuleContext) -> Iterable[Finding]:
    for path in ctx.all_paths():
        if path.role.name.lower() != "owner" or not path.is_direct:
            continue
        if path.principal.type is PrincipalType.GROUP:
            continue  # a group assignment is reported against the people who inherit it
        yield Finding(
            rule_id="direct-owner",
            title="Owner assigned directly to an identity",
            severity="high",
            what=f"{path.principal.display_name} holds Owner directly at {path.scope_display}.",
            why=(
                "Owner grants full control of everything in the scope, including the ability to "
                "grant access to other identities. Assigned straight to an identity rather than "
                "through a group, it is not governed by group review and survives role changes."
            ),
            principal=path.principal,
            role=path.role,
            scope=path.scope,
            scope_display=path.scope_display,
            paths=(path,),
            evidence=(
                f"Assignment {path.assignment.id} at {ctx.breadcrumb(path.scope)}.",
                f"Covers {len(descendants(ctx.graph, path.scope, include_self=True))} scope(s).",
            ),
        )


@rule("direct-contributor")
def direct_contributor(ctx: RuleContext) -> Iterable[Finding]:
    for path in ctx.all_paths():
        if path.role.name.lower() != "contributor" or not path.is_direct:
            continue
        if path.principal.type is PrincipalType.GROUP:
            continue  # group assignments are the intended mechanism; see the privileged-* rules
        yield Finding(
            rule_id="direct-contributor",
            title="Contributor assigned directly to an identity",
            severity="medium",
            what=f"{path.principal.display_name} holds Contributor directly at {path.scope_display}.",
            why=(
                "Contributor can create, change and delete every resource in the scope. Assigned "
                "straight to an identity rather than through a group, the access is not managed by "
                "group membership and will not be removed when the person changes role."
            ),
            principal=path.principal,
            role=path.role,
            scope=path.scope,
            scope_display=path.scope_display,
            paths=(path,),
            evidence=(f"Assignment {path.assignment.id} at {ctx.breadcrumb(path.scope)}.",),
        )


@rule("user-access-administrator")
def user_access_administrator(ctx: RuleContext) -> Iterable[Finding]:
    for path in ctx.all_paths():
        if path.role.name.lower() != "user access administrator":
            continue
        how = "directly" if path.is_direct else f"via {describe_chain(ctx.graph, path.membership_chain)}"
        yield Finding(
            rule_id="user-access-administrator",
            title="User Access Administrator assignment",
            severity="high",
            what=f"{path.principal.display_name} holds User Access Administrator at {path.scope_display} ({how}).",
            why=(
                "User Access Administrator can grant any role to any identity at the scope, so the "
                "holder can escalate themselves or anyone else to Owner. It should be rare, "
                "time-boxed and closely reviewed."
            ),
            principal=path.principal,
            role=path.role,
            scope=path.scope,
            scope_display=path.scope_display,
            paths=(path,),
            evidence=(f"Assignment {path.assignment.id} at {ctx.breadcrumb(path.scope)}.",),
        )


@rule("privileged-at-management-group")
def privileged_at_management_group(ctx: RuleContext) -> Iterable[Finding]:
    for assignment in ctx.graph.role_assignments:
        node = ctx.graph.scopes.get(assignment.scope)
        role = ctx.graph.role_definitions.get(assignment.role_definition_id)
        if node is None or role is None or node.kind is not ScopeKind.MANAGEMENT_GROUP:
            continue
        if not ctx.is_privileged(role):
            continue
        holder = ctx.graph.principals.get(assignment.principal_id)
        subs = _subscriptions_under(ctx, assignment.scope)
        yield Finding(
            rule_id="privileged-at-management-group",
            title="Privileged role assigned at a management group",
            severity="high",
            what=(
                f"{holder.display_name if holder else assignment.principal_id} has {role.name} "
                f"assigned at the {node.display_name} management group."
            ),
            why=(
                "A management group assignment is inherited by every subscription, resource group "
                f"and resource beneath it - here, {subs} subscription(s). Privileged access granted "
                "this high up is rarely reviewed at the places it actually applies."
            ),
            principal=holder,
            role=role,
            scope=assignment.scope,
            scope_display=node.display_name,
            evidence=(
                f"Assignment {assignment.id} at {ctx.breadcrumb(assignment.scope)}.",
                f"Inherited by {len(descendants(ctx.graph, assignment.scope))} scope(s) below it.",
            ),
        )


@rule("privileged-at-subscription")
def privileged_at_subscription(ctx: RuleContext) -> Iterable[Finding]:
    for assignment in ctx.graph.role_assignments:
        node = ctx.graph.scopes.get(assignment.scope)
        role = ctx.graph.role_definitions.get(assignment.role_definition_id)
        if node is None or role is None or node.kind is not ScopeKind.SUBSCRIPTION:
            continue
        if not ctx.is_privileged(role):
            continue
        holder = ctx.graph.principals.get(assignment.principal_id)
        yield Finding(
            rule_id="privileged-at-subscription",
            title="Privileged role assigned at a subscription",
            severity="medium",
            what=(
                f"{holder.display_name if holder else assignment.principal_id} has {role.name} "
                f"assigned at the {node.display_name} subscription."
            ),
            why=(
                "The assignment applies to every resource group and resource in the subscription. "
                "Where the identity only needs a single workload, a narrower scope removes a large "
                "amount of unnecessary access."
            ),
            principal=holder,
            role=role,
            scope=assignment.scope,
            scope_display=node.display_name,
            evidence=(
                f"Assignment {assignment.id} at {ctx.breadcrumb(assignment.scope)}.",
                f"Applies to {len(descendants(ctx.graph, assignment.scope))} scope(s) in the subscription.",
            ),
        )


@rule("custom-role-wildcard")
def custom_role_wildcard(ctx: RuleContext) -> Iterable[Finding]:
    for role in ctx.graph.custom_roles:
        wildcards = role.wildcard_actions
        if not wildcards:
            continue
        severity = "high" if role.grants_full_control else "medium"
        yield Finding(
            rule_id="custom-role-wildcard",
            title="Custom role with wildcard permissions",
            severity=severity,
            what=f"The custom role {role.name} grants {len(wildcards)} wildcard action(s).",
            why=(
                "A wildcard grants every current and future operation in that namespace, including "
                "operations Microsoft has not shipped yet. The role's real reach cannot be reviewed "
                "from its definition."
            ),
            role=role,
            evidence=tuple(f"Grants {action}" for action in wildcards),
        )


@rule("custom-role-can-assign-roles")
def custom_role_can_assign_roles(ctx: RuleContext) -> Iterable[Finding]:
    for role in ctx.graph.custom_roles:
        if not role.can_assign_roles:
            continue
        yield Finding(
            rule_id="custom-role-can-assign-roles",
            title="Custom role can create role assignments",
            severity="high",
            what=f"The custom role {role.name} can write role assignments.",
            why=(
                "Any identity holding this role can grant itself, or anyone else, any other role "
                "at the same scope. That makes it equivalent to User Access Administrator, and a "
                "path to Owner."
            ),
            role=role,
            evidence=tuple(
                f"Grants {action}"
                for action in role.all_actions
                if "roleassignments" in action.lower() or action == "*" or "authorization" in action.lower()
            ),
        )


@rule("privileged-via-group")
def privileged_via_nested_group(ctx: RuleContext) -> Iterable[Finding]:
    for path in ctx.all_paths():
        if path.is_direct or not ctx.is_privileged(path.role):
            continue
        if path.principal.type is PrincipalType.GROUP:
            continue  # reported against the people who end up with the access
        chain = describe_chain(ctx.graph, path.membership_chain)
        levels = len(path.membership_chain)
        through = (
            f"through {levels} levels of group nesting"
            if levels > 1
            else f"through membership of {chain}"
        )
        yield Finding(
            rule_id="privileged-via-group",
            title="Privileged access inherited through group membership",
            severity="high",
            what=(
                f"{path.principal.display_name} ends up with {path.role.name} on "
                f"{path.scope_display} {through}."
            ),
            why=(
                "Nobody granted this identity the role directly - it arrives through group "
                "membership. Access that is several groups deep is the hardest kind to notice "
                "during a review and the easiest to acquire by accident."
            ),
            principal=path.principal,
            role=path.role,
            scope=path.scope,
            scope_display=path.scope_display,
            paths=(path,),
            evidence=(
                f"Membership chain: {path.principal.display_name} > {chain}.",
                f"Assignment {path.assignment.id} held by {chain.split(' > ')[-1]} at {ctx.breadcrumb(path.scope)}.",
            ),
        )


@rule("multiple-paths-to-same-access")
def multiple_paths_to_same_access(ctx: RuleContext) -> Iterable[Finding]:
    for principal_id, paths in ctx.paths_by_principal.items():
        for key, group in distinct_grants(paths).items():
            if len(group) < 2:
                continue
            first = group[0]
            routes = []
            for path in group:
                routes.append(
                    "direct assignment"
                    if path.is_direct
                    else describe_chain(ctx.graph, path.membership_chain)
                )
            if len(set(routes)) < 2:
                continue
            yield Finding(
                rule_id="multiple-paths-to-same-access",
                title="The same access is granted by more than one path",
                severity="low",
                what=(
                    f"{first.principal.display_name} reaches {first.role.name} on "
                    f"{first.scope_display} by {len(set(routes))} different paths."
                ),
                why=(
                    "Removing one path does not remove the access. Clean-up and offboarding often "
                    "miss the second route, so the identity keeps the permission after someone "
                    "believes it was revoked."
                ),
                principal=first.principal,
                role=first.role,
                scope=first.scope,
                scope_display=first.scope_display,
                paths=tuple(group),
                evidence=tuple(f"Via {route}." for route in sorted(set(routes))),
            )


@rule("duplicate-assignment")
def duplicate_assignment(ctx: RuleContext) -> Iterable[Finding]:
    seen: dict[tuple[str, str, str], list[str]] = {}
    for assignment in ctx.graph.role_assignments:
        key = (assignment.principal_id, assignment.role_definition_id, assignment.scope)
        seen.setdefault(key, []).append(assignment.id)
    for (principal_id, role_id, scope), ids in seen.items():
        if len(ids) < 2:
            continue
        holder = ctx.graph.principals.get(principal_id)
        role = ctx.graph.role_definitions.get(role_id)
        yield Finding(
            rule_id="duplicate-assignment",
            title="The same role is assigned more than once",
            severity="low",
            what=(
                f"{holder.display_name if holder else principal_id} has "
                f"{role.name if role else role_id} assigned {len(ids)} times at "
                f"{ctx.scope_label(scope)}."
            ),
            why=(
                "Duplicate assignments usually mean two processes are managing the same access. "
                "Deleting one leaves the access in place, which makes revocation unreliable."
            ),
            principal=holder,
            role=role,
            scope=scope,
            scope_display=ctx.scope_label(scope),
            evidence=tuple(f"Assignment {aid}." for aid in sorted(ids)),
        )


@rule("redundant-narrow-assignment")
def redundant_narrow_assignment(ctx: RuleContext) -> Iterable[Finding]:
    """A grant that an inherited grant of the same role already covers."""
    for principal_id, paths in ctx.paths_by_principal.items():
        by_role: dict[str, list] = {}
        for path in paths:
            by_role.setdefault(path.role.id, []).append(path)
        for role_id, role_paths in by_role.items():
            scopes = {p.scope for p in role_paths}
            for path in role_paths:
                broader = [
                    s for s in scopes if s != path.scope and is_ancestor_of(ctx.graph, s, path.scope)
                ]
                if not broader:
                    continue
                yield Finding(
                    rule_id="redundant-narrow-assignment",
                    title="Assignment is already covered by a broader one",
                    severity="low",
                    what=(
                        f"{path.principal.display_name} has {path.role.name} at "
                        f"{path.scope_display}, but already inherits {path.role.name} from "
                        f"{ctx.scope_label(broader[0])}."
                    ),
                    why=(
                        "The narrower assignment grants nothing extra. Removing it reduces the "
                        "number of assignments to review without changing anyone's access - and "
                        "removing only the narrow one would not reduce access at all."
                    ),
                    principal=path.principal,
                    role=path.role,
                    scope=path.scope,
                    scope_display=path.scope_display,
                    paths=(path,),
                    evidence=tuple(f"Also granted at {ctx.breadcrumb(s)}." for s in sorted(broader)),
                )


@rule("write-role-at-broad-scope")
def write_role_at_broad_scope(ctx: RuleContext) -> Iterable[Finding]:
    """Write-capable roles that are not on the privileged list, granted very high up."""
    for assignment in ctx.graph.role_assignments:
        node = ctx.graph.scopes.get(assignment.scope)
        role = ctx.graph.role_definitions.get(assignment.role_definition_id)
        if node is None or role is None:
            continue
        if node.kind not in (ScopeKind.TENANT, ScopeKind.MANAGEMENT_GROUP):
            continue
        if ctx.is_privileged(role):
            continue  # already reported by privileged-at-management-group
        if not _grants_write(role):
            continue
        subs = _subscriptions_under(ctx, assignment.scope)
        if subs < BROAD_SCOPE_SUBSCRIPTION_THRESHOLD:
            continue
        holder = ctx.graph.principals.get(assignment.principal_id)
        yield Finding(
            rule_id="write-role-at-broad-scope",
            title="A role that can change resources is assigned very high up",
            severity="medium",
            what=(
                f"{holder.display_name if holder else assignment.principal_id} has {role.name} at "
                f"{node.display_name}, which covers {subs} subscriptions."
            ),
            why=(
                "The role is not on the privileged list, but it can modify resources, and the "
                "scope reaches most of the estate. Broad write access attracts little attention "
                "precisely because the role name looks harmless."
            ),
            principal=holder,
            role=role,
            scope=assignment.scope,
            scope_display=node.display_name,
            evidence=(
                f"Assignment {assignment.id} at {ctx.breadcrumb(assignment.scope)}.",
                f"Covers {subs} subscription(s) and "
                f"{len(descendants(ctx.graph, assignment.scope))} scope(s) in total.",
            ),
        )


@rule("unused-custom-role")
def unused_custom_role(ctx: RuleContext) -> Iterable[Finding]:
    assigned = {ra.role_definition_id for ra in ctx.graph.role_assignments}
    for role in ctx.graph.custom_roles:
        if role.id in assigned:
            continue
        yield Finding(
            rule_id="unused-custom-role",
            title="Custom role is defined but never assigned",
            severity="low",
            what=f"The custom role {role.name} is not assigned to any identity.",
            why=(
                "Unassigned custom roles accumulate and are rarely reviewed. If one is later "
                "assigned by mistake or by automation, nobody remembers what it grants."
            ),
            role=role,
            evidence=(
                f"Defined with {len(role.all_actions)} action(s).",
                f"Assignable at: {', '.join(role.assignable_scopes) or 'not specified'}.",
            ),
        )


@rule("service-principal-privileged")
def service_principal_privileged(ctx: RuleContext) -> Iterable[Finding]:
    for path in ctx.all_paths():
        if path.principal.type not in (PrincipalType.SERVICE_PRINCIPAL, PrincipalType.MANAGED_IDENTITY):
            continue
        if not ctx.is_privileged(path.role):
            continue
        yield Finding(
            rule_id="service-principal-privileged",
            title="Non-human identity holds a privileged role",
            severity="high",
            what=(
                f"{path.principal.type_label} {path.principal.display_name} holds "
                f"{path.role.name} on {path.scope_display}."
            ),
            why=(
                "Application identities are not covered by joiner-mover-leaver processes and their "
                "credentials often outlive the project that created them. Privileged access held "
                "by automation is a standing risk with no owner."
            ),
            principal=path.principal,
            role=path.role,
            scope=path.scope,
            scope_display=path.scope_display,
            paths=(path,),
            evidence=(
                f"Assignment {path.assignment.id} at {ctx.breadcrumb(path.scope)}.",
                path.assignment.description or "No description was recorded for this assignment.",
            ),
        )
