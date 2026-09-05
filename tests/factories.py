"""Synthetic graph builders for tests.

These construct :class:`AccessGraph` objects directly, without going through
the importer, so domain and resolver tests stay independent of the file format.
"""

from __future__ import annotations

from rolegraph.domain.entities import (
    AccessGraph,
    GroupMembership,
    Permission,
    Principal,
    PrincipalType,
    RoleAssignment,
    RoleDefinition,
    ScopeNode,
    Tenant,
)
from rolegraph.domain.ids import (
    ScopeKind,
    management_group_scope,
    normalize_scope,
    resource_group_scope,
    subscription_scope,
)


class GraphBuilder:
    def __init__(self, tenant_name: str = "Contoso") -> None:
        self.graph = AccessGraph(tenant=Tenant(id="tenant-1", display_name=tenant_name))
        self.graph.scopes["/"] = ScopeNode(
            scope="/",
            raw_scope="/",
            kind=ScopeKind.TENANT,
            name=tenant_name,
            display_name=tenant_name,
            parent_scope=None,
        )
        self._counter = 0

    # --- scopes ---------------------------------------------------------

    def mg(self, name: str, parent: str = "/") -> str:
        scope = normalize_scope(management_group_scope(name))
        self.graph.scopes[scope] = ScopeNode(
            scope=scope,
            raw_scope=management_group_scope(name),
            kind=ScopeKind.MANAGEMENT_GROUP,
            name=name,
            display_name=name,
            parent_scope=normalize_scope(parent),
        )
        return scope

    def subscription(self, sub_id: str, parent: str, display_name: str | None = None) -> str:
        scope = normalize_scope(subscription_scope(sub_id))
        self.graph.scopes[scope] = ScopeNode(
            scope=scope,
            raw_scope=subscription_scope(sub_id),
            kind=ScopeKind.SUBSCRIPTION,
            name=sub_id,
            display_name=display_name or sub_id,
            parent_scope=normalize_scope(parent),
        )
        return scope

    def rg(self, sub_id: str, name: str) -> str:
        scope = normalize_scope(resource_group_scope(sub_id, name))
        self.graph.scopes[scope] = ScopeNode(
            scope=scope,
            raw_scope=resource_group_scope(sub_id, name),
            kind=ScopeKind.RESOURCE_GROUP,
            name=name,
            display_name=name,
            parent_scope=normalize_scope(subscription_scope(sub_id)),
        )
        return scope

    def resource(self, sub_id: str, rg_name: str, provider: str, name: str) -> str:
        raw = f"{resource_group_scope(sub_id, rg_name)}/providers/{provider}/{name}"
        scope = normalize_scope(raw)
        self.graph.scopes[scope] = ScopeNode(
            scope=scope,
            raw_scope=raw,
            kind=ScopeKind.RESOURCE,
            name=name,
            display_name=name,
            parent_scope=normalize_scope(resource_group_scope(sub_id, rg_name)),
            resource_type=provider,
        )
        return scope

    # --- principals -----------------------------------------------------

    def user(self, pid: str, name: str, upn: str | None = None) -> str:
        self.graph.principals[pid] = Principal(
            id=pid, display_name=name, type=PrincipalType.USER, upn=upn or f"{pid}@contoso.com"
        )
        return pid

    def group(self, pid: str, name: str) -> str:
        self.graph.principals[pid] = Principal(
            id=pid, display_name=name, type=PrincipalType.GROUP
        )
        return pid

    def service_principal(self, pid: str, name: str) -> str:
        self.graph.principals[pid] = Principal(
            id=pid, display_name=name, type=PrincipalType.SERVICE_PRINCIPAL, app_id=f"app-{pid}"
        )
        return pid

    def managed_identity(self, pid: str, name: str) -> str:
        self.graph.principals[pid] = Principal(
            id=pid, display_name=name, type=PrincipalType.MANAGED_IDENTITY
        )
        return pid

    def member_of(self, member_id: str, group_id: str) -> None:
        self.graph.memberships.append(GroupMembership(group_id=group_id, member_id=member_id))

    # --- roles ----------------------------------------------------------

    def role(
        self,
        rid: str,
        name: str,
        actions: tuple[str, ...] = ("*",),
        not_actions: tuple[str, ...] = (),
        custom: bool = False,
        assignable_scopes: tuple[str, ...] = ("/",),
    ) -> str:
        self.graph.role_definitions[rid] = RoleDefinition(
            id=rid,
            name=name,
            role_type="CustomRole" if custom else "BuiltInRole",
            assignable_scopes=assignable_scopes,
            permissions=(Permission(actions=actions, not_actions=not_actions),),
        )
        return rid

    def assign(self, principal_id: str, role_id: str, scope: str, assignment_id: str | None = None) -> str:
        self._counter += 1
        aid = assignment_id or f"ra-{self._counter}"
        principal = self.graph.principals.get(principal_id)
        self.graph.role_assignments.append(
            RoleAssignment(
                id=aid,
                principal_id=principal_id,
                role_definition_id=role_id,
                scope=normalize_scope(scope),
                raw_scope=scope,
                principal_type=principal.type if principal else None,
            )
        )
        return aid

    def build(self) -> AccessGraph:
        return self.graph.build_indexes()


def contoso_like() -> AccessGraph:
    """A compact tenant exercising every relationship the resolver must handle.

    Deliberately contains: nested groups, a developer who reaches production
    through nesting, a service principal with Owner at a subscription, a
    wildcard custom role, and the same role reachable by two different paths.
    """
    b = GraphBuilder()
    prod = b.mg("Production")
    nonprod = b.mg("NonProduction")
    payments = b.mg("Payments-Prod", parent=prod)
    b.subscription("sub-payments", parent=payments, display_name="Payments Production")
    b.subscription("sub-dev", parent=nonprod, display_name="Development")
    b.rg("sub-payments", "rg-payments-api")
    b.resource("sub-payments", "rg-payments-api", "Microsoft.Storage/storageAccounts", "stpayments")

    b.user("u-jane", "Jane Smith", "jane.smith@contoso.com")
    b.user("u-dev", "Dev Devlin", "dev.devlin@contoso.com")
    b.group("g-platform-admins", "Azure-Platform-Admins")
    b.group("g-developers", "Azure-Developers")
    b.group("g-all-eng", "All-Engineering")
    b.service_principal("sp-deploy", "sp-payments-deploy")

    b.member_of("u-jane", "g-platform-admins")
    b.member_of("u-dev", "g-developers")
    b.member_of("g-developers", "g-all-eng")
    b.member_of("g-all-eng", "g-platform-admins")  # nested route into production
    b.member_of("u-dev", "g-all-eng")  # second, shorter route to the same place

    b.role("rd-owner", "Owner", actions=("*",))
    b.role("rd-contributor", "Contributor", actions=("*",), not_actions=("Microsoft.Authorization/*/Write",))
    b.role("rd-uaa", "User Access Administrator", actions=("*/read", "Microsoft.Authorization/*"))
    b.role("rd-broad", "Contoso-Broad-Operator", actions=("Microsoft.Compute/*", "Microsoft.Storage/*"), custom=True)

    b.assign("g-platform-admins", "rd-contributor", "/providers/Microsoft.Management/managementGroups/Production")
    b.assign("sp-deploy", "rd-owner", "/subscriptions/sub-payments")
    b.assign("u-jane", "rd-uaa", "/subscriptions/sub-payments")
    b.assign("g-developers", "rd-broad", "/subscriptions/sub-dev")
    return b.build()
