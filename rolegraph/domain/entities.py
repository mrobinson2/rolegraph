"""Pure-Python RBAC entities. No I/O, no persistence, no framework types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .ids import SCOPE_DEPTH, ScopeKind


class PrincipalType(str, Enum):
    USER = "user"
    GROUP = "group"
    SERVICE_PRINCIPAL = "servicePrincipal"
    MANAGED_IDENTITY = "managedIdentity"


PRINCIPAL_LABELS: dict[PrincipalType, str] = {
    PrincipalType.USER: "User",
    PrincipalType.GROUP: "Group",
    PrincipalType.SERVICE_PRINCIPAL: "Service principal",
    PrincipalType.MANAGED_IDENTITY: "Managed identity",
}


@dataclass(frozen=True, slots=True)
class Principal:
    """A user, group, service principal or managed identity."""

    id: str
    display_name: str
    type: PrincipalType
    upn: str | None = None
    email: str | None = None
    department: str | None = None
    app_id: str | None = None
    description: str | None = None

    @property
    def type_label(self) -> str:
        return PRINCIPAL_LABELS[self.type]

    @property
    def subtitle(self) -> str:
        return self.upn or self.email or self.app_id or self.id


@dataclass(frozen=True, slots=True)
class ScopeNode:
    """One node of the tenant -> MG -> subscription -> RG -> resource tree."""

    scope: str  # normalized scope id
    raw_scope: str
    kind: ScopeKind
    name: str
    display_name: str
    parent_scope: str | None = None
    resource_type: str | None = None
    location: str | None = None

    @property
    def depth(self) -> int:
        """How far down the hierarchy this scope sits. Lower means broader."""
        return SCOPE_DEPTH[self.kind]

    @property
    def kind_label(self) -> str:
        return {
            ScopeKind.TENANT: "Tenant",
            ScopeKind.MANAGEMENT_GROUP: "Management group",
            ScopeKind.SUBSCRIPTION: "Subscription",
            ScopeKind.RESOURCE_GROUP: "Resource group",
            ScopeKind.RESOURCE: "Resource",
        }[self.kind]


@dataclass(frozen=True, slots=True)
class Permission:
    """One permission block of a role definition."""

    actions: tuple[str, ...] = ()
    not_actions: tuple[str, ...] = ()
    data_actions: tuple[str, ...] = ()
    not_data_actions: tuple[str, ...] = ()

    @property
    def has_wildcard_action(self) -> bool:
        return any(a.endswith("*") for a in self.actions)

    @property
    def is_full_control(self) -> bool:
        return "*" in self.actions


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    id: str
    name: str
    description: str | None = None
    role_type: str = "BuiltInRole"  # or "CustomRole"
    assignable_scopes: tuple[str, ...] = ()
    permissions: tuple[Permission, ...] = ()

    @property
    def is_custom(self) -> bool:
        return self.role_type.lower().startswith("custom")

    @property
    def all_actions(self) -> tuple[str, ...]:
        return tuple(a for p in self.permissions for a in p.actions)

    @property
    def all_not_actions(self) -> tuple[str, ...]:
        return tuple(a for p in self.permissions for a in p.not_actions)

    @property
    def all_data_actions(self) -> tuple[str, ...]:
        return tuple(a for p in self.permissions for a in p.data_actions)

    @property
    def wildcard_actions(self) -> tuple[str, ...]:
        return tuple(a for a in self.all_actions if a.endswith("*"))

    @property
    def grants_full_control(self) -> bool:
        return any(p.is_full_control for p in self.permissions)

    @property
    def can_assign_roles(self) -> bool:
        """True if the role can hand out role assignments (write on roleAssignments)."""
        targets = ("microsoft.authorization/roleassignments/write", "microsoft.authorization/*")
        for action in self.all_actions:
            a = action.lower()
            if a == "*" or a in targets:
                return True
            if a.endswith("*") and any(t.startswith(a[:-1]) for t in targets):
                return True
        return False


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    id: str
    principal_id: str
    role_definition_id: str
    scope: str  # normalized
    raw_scope: str
    principal_type: PrincipalType | None = None
    created_on: str | None = None
    created_by: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class GroupMembership:
    """``member_id`` is a member of ``group_id``. Members may themselves be groups."""

    group_id: str
    member_id: str


@dataclass(frozen=True, slots=True)
class Tenant:
    id: str
    display_name: str
    domain: str | None = None


@dataclass(slots=True)
class ImportWarning:
    """A record that could not be used, or was used with a caveat.

    Warnings are surfaced in the Import screen. Bad records are never silently
    dropped: every skipped record produces one of these.
    """

    category: str
    message: str
    record_type: str | None = None
    record_id: str | None = None
    severity: str = "warning"  # "warning" | "error"

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "message": self.message,
            "recordType": self.record_type,
            "recordId": self.record_id,
            "severity": self.severity,
        }


@dataclass(slots=True)
class AccessGraph:
    """The whole imported tenant, indexed for resolution.

    Small by design: tens of thousands of edges live comfortably in memory, so
    the resolver walks plain dicts instead of issuing recursive SQL.
    """

    tenant: Tenant | None = None
    principals: dict[str, Principal] = field(default_factory=dict)
    scopes: dict[str, ScopeNode] = field(default_factory=dict)
    role_definitions: dict[str, RoleDefinition] = field(default_factory=dict)
    role_assignments: list[RoleAssignment] = field(default_factory=list)
    memberships: list[GroupMembership] = field(default_factory=list)
    warnings: list[ImportWarning] = field(default_factory=list)

    # --- derived indexes, built by build_indexes() ---
    children_by_scope: dict[str, list[str]] = field(default_factory=dict)
    assignments_by_principal: dict[str, list[RoleAssignment]] = field(default_factory=dict)
    assignments_by_scope: dict[str, list[RoleAssignment]] = field(default_factory=dict)
    members_by_group: dict[str, list[str]] = field(default_factory=dict)
    groups_by_member: dict[str, list[str]] = field(default_factory=dict)

    def build_indexes(self) -> AccessGraph:
        self.children_by_scope = {}
        for node in self.scopes.values():
            if node.parent_scope:
                self.children_by_scope.setdefault(node.parent_scope, []).append(node.scope)

        self.assignments_by_principal = {}
        self.assignments_by_scope = {}
        for ra in self.role_assignments:
            self.assignments_by_principal.setdefault(ra.principal_id, []).append(ra)
            self.assignments_by_scope.setdefault(ra.scope, []).append(ra)

        self.members_by_group = {}
        self.groups_by_member = {}
        for m in self.memberships:
            self.members_by_group.setdefault(m.group_id, []).append(m.member_id)
            self.groups_by_member.setdefault(m.member_id, []).append(m.group_id)
        return self

    # --- convenience lookups -------------------------------------------------

    def principal(self, principal_id: str) -> Principal | None:
        return self.principals.get(principal_id)

    def scope(self, scope: str) -> ScopeNode | None:
        return self.scopes.get(scope)

    def role(self, role_definition_id: str) -> RoleDefinition | None:
        return self.role_definitions.get(role_definition_id)

    def principals_of_type(self, ptype: PrincipalType) -> list[Principal]:
        return [p for p in self.principals.values() if p.type == ptype]

    def scopes_of_kind(self, kind: ScopeKind) -> list[ScopeNode]:
        return [s for s in self.scopes.values() if s.kind == kind]

    @property
    def custom_roles(self) -> list[RoleDefinition]:
        return [r for r in self.role_definitions.values() if r.is_custom]
