"""Turn an import document into an :class:`AccessGraph`.

Rules of the house:

* No record is ever silently discarded. Anything skipped, deduplicated, or
  pointing at something that was not imported produces an :class:`ImportWarning`.
* Import is total: a document with problems still yields a usable graph, with
  the problems attached.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..domain.entities import (
    AccessGraph,
    GroupMembership,
    ImportWarning,
    Permission,
    Principal,
    PrincipalType,
    RoleAssignment,
    RoleDefinition,
    ScopeNode,
    Tenant,
)
from ..domain.hierarchy import orphan_scopes
from ..domain.ids import (
    ScopeKind,
    ScopeParseError,
    management_group_scope,
    normalize_scope,
    parse_scope,
    resource_group_scope,
    subscription_scope,
)
from ..resolver.membership import membership_cycles
from .schema import SECTIONS, SchemaError, as_str, as_tuple, first, missing_required, validate_document


@dataclass(slots=True)
class ImportResult:
    graph: AccessGraph
    counts: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    warnings: list[ImportWarning] = field(default_factory=list)
    source_name: str = "upload"
    checksum: str = ""
    imported_at: str = ""

    @property
    def total_records(self) -> int:
        return sum(self.counts.values())

    @property
    def total_skipped(self) -> int:
        return sum(self.skipped.values())

    @property
    def error_count(self) -> int:
        return sum(1 for w in self.warnings if w.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for w in self.warnings if w.severity == "warning")


class _Importer:
    def __init__(self, document: dict[str, Any], source_name: str) -> None:
        self.doc = document
        self.source_name = source_name
        self.graph = AccessGraph()
        self.counts: dict[str, int] = {s: 0 for s in SECTIONS}
        self.skipped: dict[str, int] = {s: 0 for s in SECTIONS}
        self.warnings: list[ImportWarning] = []

    # --- warning helpers -------------------------------------------------

    def warn(
        self,
        category: str,
        message: str,
        *,
        section: str | None = None,
        record_id: str | None = None,
        severity: str = "warning",
    ) -> None:
        self.warnings.append(
            ImportWarning(
                category=category,
                message=message,
                record_type=section,
                record_id=record_id,
                severity=severity,
            )
        )

    def skip(self, section: str, message: str, record_id: str | None = None) -> None:
        self.skipped[section] += 1
        self.warn("malformedRecord", message, section=section, record_id=record_id, severity="error")

    def records(self, section: str) -> list[dict[str, Any]]:
        raw = self.doc.get(section) or []
        out: list[dict[str, Any]] = []
        for index, record in enumerate(raw):
            if not isinstance(record, dict):
                self.skip(section, f"Record {index} is not a JSON object; skipped.")
                continue
            missing = missing_required(section, record)
            if missing:
                out_id = as_str(first(record, "id", "name", "principalId", "groupId"))
                self.skip(
                    section,
                    f"Record is missing required field(s): {', '.join(missing)}; skipped.",
                    out_id,
                )
                continue
            out.append(record)
        return out

    def add_scope(self, node: ScopeNode, section: str) -> None:
        existing = self.graph.scopes.get(node.scope)
        if existing is not None:
            self.warn(
                "duplicate",
                f"Scope {node.raw_scope} appears more than once; the first record was kept.",
                section=section,
                record_id=node.scope,
            )
            self.skipped[section] += 1
            return
        self.graph.scopes[node.scope] = node
        self.counts[section] += 1

    # --- sections --------------------------------------------------------

    def load_tenant(self) -> None:
        records = self.records("tenants")
        if not records:
            self.graph.tenant = Tenant(id="tenant", display_name="Tenant")
            self.graph.scopes["/"] = ScopeNode("/", "/", ScopeKind.TENANT, "/", "Tenant")
            self.warn("missingSection", "No tenant record supplied; using a placeholder tenant root.")
            self.counts["tenants"] = 0
            return
        if len(records) > 1:
            self.warn(
                "duplicate",
                f"{len(records)} tenant records supplied; RoleGraph models one tenant per import "
                "and used the first.",
                section="tenants",
            )
            self.skipped["tenants"] += len(records) - 1
        record = records[0]
        tenant = Tenant(
            id=as_str(first(record, "id", "tenantId")) or "tenant",
            display_name=as_str(first(record, "displayName", "name")) or "Tenant",
            domain=as_str(first(record, "domain", "defaultDomain")),
        )
        self.graph.tenant = tenant
        self.graph.scopes["/"] = ScopeNode("/", "/", ScopeKind.TENANT, tenant.id, tenant.display_name)
        self.counts["tenants"] = 1

    def load_management_groups(self) -> None:
        for record in self.records("managementGroups"):
            name = as_str(first(record, "id", "name"))
            raw = management_group_scope(name)
            parent_raw = as_str(first(record, "parent", "parentId", "parentName"))
            if parent_raw in (None, "/", "tenant", "tenantRoot"):
                parent = "/"
            elif parent_raw.startswith("/"):
                try:
                    parent = normalize_scope(parent_raw)
                except ScopeParseError:
                    self.warn(
                        "malformedScope",
                        f"Management group '{name}' has an unparseable parent scope "
                        f"{parent_raw!r}; attached to the tenant root instead.",
                        section="managementGroups",
                        record_id=name,
                    )
                    parent = "/"
            else:
                parent = normalize_scope(management_group_scope(parent_raw))
            self.add_scope(
                ScopeNode(
                    scope=normalize_scope(raw),
                    raw_scope=raw,
                    kind=ScopeKind.MANAGEMENT_GROUP,
                    name=name,
                    display_name=as_str(first(record, "displayName", "name")) or name,
                    parent_scope=parent,
                ),
                "managementGroups",
            )

    def load_subscriptions(self) -> None:
        for record in self.records("subscriptions"):
            sub_id = as_str(first(record, "subscriptionId", "id"))
            if sub_id.startswith("/subscriptions/"):
                sub_id = sub_id.split("/")[2]
            raw = subscription_scope(sub_id)
            parent_raw = as_str(first(record, "managementGroup", "parent", "parentId"))
            if parent_raw is None:
                parent = "/"
                self.warn(
                    "missingParent",
                    f"Subscription '{sub_id}' has no management group; attached to the tenant root.",
                    section="subscriptions",
                    record_id=sub_id,
                )
            elif parent_raw.startswith("/"):
                parent = normalize_scope(parent_raw)
            else:
                parent = normalize_scope(management_group_scope(parent_raw))
            self.add_scope(
                ScopeNode(
                    scope=normalize_scope(raw),
                    raw_scope=raw,
                    kind=ScopeKind.SUBSCRIPTION,
                    name=sub_id,
                    display_name=as_str(first(record, "displayName", "name")) or sub_id,
                    parent_scope=parent,
                ),
                "subscriptions",
            )

    def load_resource_groups(self) -> None:
        for record in self.records("resourceGroups"):
            name = as_str(record.get("name"))
            sub_id = as_str(record.get("subscriptionId"))
            raw = resource_group_scope(sub_id, name)
            self.add_scope(
                ScopeNode(
                    scope=normalize_scope(raw),
                    raw_scope=raw,
                    kind=ScopeKind.RESOURCE_GROUP,
                    name=name,
                    display_name=name,
                    parent_scope=normalize_scope(subscription_scope(sub_id)),
                    location=as_str(record.get("location")),
                ),
                "resourceGroups",
            )

    def load_resources(self) -> None:
        for record in self.records("resources"):
            raw = as_str(record.get("id"))
            if raw is None:
                sub_id = as_str(record.get("subscriptionId"))
                rg = as_str(first(record, "resourceGroup", "resourceGroupName"))
                rtype = as_str(first(record, "type", "resourceType"))
                name = as_str(record.get("name"))
                if not all((sub_id, rg, rtype, name)):
                    self.skip(
                        "resources",
                        f"Resource '{name or '?'}' has neither a full id nor "
                        "subscriptionId + resourceGroup + type + name; skipped.",
                        name,
                    )
                    continue
                raw = f"{resource_group_scope(sub_id, rg)}/providers/{rtype}/{name}"
            try:
                ref = parse_scope(raw)
            except ScopeParseError as exc:
                self.skip("resources", f"Resource scope could not be parsed ({exc}); skipped.", raw)
                continue
            if ref.kind is not ScopeKind.RESOURCE:
                self.skip(
                    "resources",
                    f"Resource id {raw!r} is a {ref.kind.value} scope, not a resource; skipped.",
                    raw,
                )
                continue
            self.add_scope(
                ScopeNode(
                    scope=ref.scope,
                    raw_scope=raw,
                    kind=ScopeKind.RESOURCE,
                    name=ref.name,
                    display_name=as_str(record.get("name")) or ref.name,
                    parent_scope=normalize_scope(
                        resource_group_scope(ref.subscription_id, ref.resource_group)
                    ),
                    resource_type=as_str(first(record, "type", "resourceType")) or ref.provider_path,
                    location=as_str(record.get("location")),
                ),
                "resources",
            )

    def load_role_definitions(self) -> None:
        for record in self.records("roleDefinitions"):
            rid = as_str(record.get("id"))
            if rid in self.graph.role_definitions:
                self.warn(
                    "duplicate",
                    f"Role definition {rid} appears more than once; the first record was kept.",
                    section="roleDefinitions",
                    record_id=rid,
                )
                self.skipped["roleDefinitions"] += 1
                continue
            permissions: list[Permission] = []
            raw_permissions = record.get("permissions") or []
            if not isinstance(raw_permissions, list):
                self.warn(
                    "malformedRecord",
                    f"Role definition {rid} has a non-list 'permissions' value; treated as empty.",
                    section="roleDefinitions",
                    record_id=rid,
                )
                raw_permissions = []
            for block in raw_permissions:
                if not isinstance(block, dict):
                    self.warn(
                        "malformedRecord",
                        f"Role definition {rid} has a permission block that is not an object; skipped.",
                        section="roleDefinitions",
                        record_id=rid,
                    )
                    continue
                permissions.append(
                    Permission(
                        actions=as_tuple(block.get("actions")),
                        not_actions=as_tuple(block.get("notActions")),
                        data_actions=as_tuple(block.get("dataActions")),
                        not_data_actions=as_tuple(block.get("notDataActions")),
                    )
                )
            if not permissions:
                self.warn(
                    "emptyPermissions",
                    f"Role definition {rid} grants no actions; it was imported but confers no access.",
                    section="roleDefinitions",
                    record_id=rid,
                )
            self.graph.role_definitions[rid] = RoleDefinition(
                id=rid,
                name=as_str(first(record, "roleName", "name")) or rid,
                description=as_str(record.get("description")),
                role_type=as_str(first(record, "roleType", "type")) or "BuiltInRole",
                assignable_scopes=as_tuple(record.get("assignableScopes")),
                permissions=tuple(permissions),
            )
            self.counts["roleDefinitions"] += 1

    def _load_principals(self, section: str, ptype: PrincipalType) -> None:
        for record in self.records(section):
            pid = as_str(record.get("id"))
            if pid in self.graph.principals:
                self.warn(
                    "duplicate",
                    f"Principal {pid} appears more than once; the first record was kept.",
                    section=section,
                    record_id=pid,
                )
                self.skipped[section] += 1
                continue
            self.graph.principals[pid] = Principal(
                id=pid,
                display_name=as_str(first(record, "displayName", "name", "userPrincipalName")) or pid,
                type=ptype,
                upn=as_str(first(record, "userPrincipalName", "upn")),
                email=as_str(first(record, "mail", "email")),
                department=as_str(record.get("department")),
                app_id=as_str(first(record, "appId", "clientId")),
                description=as_str(record.get("description")),
            )
            self.counts[section] += 1

    def load_principals(self) -> None:
        self._load_principals("users", PrincipalType.USER)
        self._load_principals("groups", PrincipalType.GROUP)
        self._load_principals("servicePrincipals", PrincipalType.SERVICE_PRINCIPAL)
        self._load_principals("managedIdentities", PrincipalType.MANAGED_IDENTITY)

    def load_memberships(self) -> None:
        seen: set[tuple[str, str]] = set()
        for record in self.records("groupMemberships"):
            group_id = as_str(record.get("groupId"))
            member_id = as_str(record.get("memberId"))
            if group_id == member_id:
                self.skip(
                    "groupMemberships",
                    f"Group {group_id} is listed as a member of itself; skipped.",
                    group_id,
                )
                continue
            key = (group_id, member_id)
            if key in seen:
                self.warn(
                    "duplicate",
                    f"Membership {member_id} -> {group_id} is listed more than once; "
                    "the duplicate was ignored.",
                    section="groupMemberships",
                    record_id=f"{member_id}->{group_id}",
                )
                self.skipped["groupMemberships"] += 1
                continue
            seen.add(key)
            group = self.graph.principals.get(group_id)
            if group is None:
                self.warn(
                    "danglingReference",
                    f"Membership refers to group {group_id}, which was not imported. "
                    "The membership was kept, but the group has no details.",
                    section="groupMemberships",
                    record_id=group_id,
                )
            elif group.type is not PrincipalType.GROUP:
                self.warn(
                    "typeMismatch",
                    f"Membership target {group_id} is a {group.type.value}, not a group. "
                    "The membership was kept as supplied.",
                    section="groupMemberships",
                    record_id=group_id,
                )
            if member_id not in self.graph.principals:
                self.warn(
                    "danglingReference",
                    f"Membership refers to member {member_id}, which was not imported.",
                    section="groupMemberships",
                    record_id=member_id,
                )
            self.graph.memberships.append(GroupMembership(group_id=group_id, member_id=member_id))
            self.counts["groupMemberships"] += 1

    def load_role_assignments(self) -> None:
        seen_ids: set[str] = set()
        for index, record in enumerate(self.records("roleAssignments")):
            raw_scope = as_str(record.get("scope"))
            try:
                scope = normalize_scope(raw_scope)
                parse_scope(raw_scope)
            except ScopeParseError as exc:
                self.skip(
                    "roleAssignments",
                    f"Role assignment has an unparseable scope {raw_scope!r} ({exc}); skipped.",
                    as_str(record.get("id")),
                )
                continue
            aid = as_str(record.get("id")) or f"ra-{index}"
            if aid in seen_ids:
                self.warn(
                    "duplicate",
                    f"Role assignment id {aid} appears more than once; the first record was kept.",
                    section="roleAssignments",
                    record_id=aid,
                )
                self.skipped["roleAssignments"] += 1
                continue
            seen_ids.add(aid)
            principal_id = as_str(record.get("principalId"))
            role_id = as_str(record.get("roleDefinitionId"))
            principal = self.graph.principals.get(principal_id)
            if principal is None:
                self.warn(
                    "danglingReference",
                    f"Role assignment {aid} is held by principal {principal_id}, which was not "
                    "imported. The assignment is shown, but the identity has no details.",
                    section="roleAssignments",
                    record_id=aid,
                )
            if role_id not in self.graph.role_definitions:
                self.warn(
                    "danglingReference",
                    f"Role assignment {aid} refers to role definition {role_id}, which was not "
                    "imported. The assignment cannot be resolved and is excluded from access paths.",
                    section="roleAssignments",
                    record_id=aid,
                    severity="error",
                )
            if scope not in self.graph.scopes:
                self.warn(
                    "danglingReference",
                    f"Role assignment {aid} is made at scope {raw_scope}, which was not imported. "
                    "Inheritance below that scope cannot be calculated.",
                    section="roleAssignments",
                    record_id=aid,
                )
            declared_type = as_str(record.get("principalType"))
            ptype = principal.type if principal else _principal_type(declared_type)
            if principal and declared_type and _principal_type(declared_type) not in (None, principal.type):
                self.warn(
                    "typeMismatch",
                    f"Role assignment {aid} declares principalType '{declared_type}' but "
                    f"{principal_id} was imported as a {principal.type.value}.",
                    section="roleAssignments",
                    record_id=aid,
                )
            self.graph.role_assignments.append(
                RoleAssignment(
                    id=aid,
                    principal_id=principal_id,
                    role_definition_id=role_id,
                    scope=scope,
                    raw_scope=raw_scope,
                    principal_type=ptype,
                    created_on=as_str(record.get("createdOn")),
                    created_by=as_str(record.get("createdBy")),
                    description=as_str(record.get("description")),
                )
            )
            self.counts["roleAssignments"] += 1

    # --- post-import integrity ------------------------------------------

    def check_integrity(self) -> None:
        for node in orphan_scopes(self.graph):
            self.warn(
                "danglingReference",
                f"{node.kind_label} '{node.display_name}' declares parent scope "
                f"{node.parent_scope}, which was not imported. It is shown outside the hierarchy.",
                section="scopes",
                record_id=node.scope,
            )
        for cycle in membership_cycles(self.graph):
            names = " -> ".join(
                (self.graph.principals[g].display_name if g in self.graph.principals else g)
                for g in cycle
            )
            self.warn(
                "membershipCycle",
                f"Group membership cycle detected: {names}. Resolution stops at the repeat.",
                section="groupMemberships",
                severity="error",
            )
        duplicate_grants: dict[tuple[str, str, str], int] = {}
        for ra in self.graph.role_assignments:
            key = (ra.principal_id, ra.role_definition_id, ra.scope)
            duplicate_grants[key] = duplicate_grants.get(key, 0) + 1
        for (pid, rid, scope), count in duplicate_grants.items():
            if count > 1:
                role = self.graph.role_definitions.get(rid)
                principal = self.graph.principals.get(pid)
                self.warn(
                    "duplicateAssignment",
                    f"{principal.display_name if principal else pid} has "
                    f"{role.name if role else rid} assigned {count} times at {scope}. "
                    "All copies were imported.",
                    section="roleAssignments",
                    record_id=f"{pid}|{rid}|{scope}",
                )

    def run(self) -> ImportResult:
        unknown = validate_document(self.doc)
        for key in unknown:
            self.warn(
                "unknownSection",
                f"Top-level key '{key}' is not part of the RoleGraph import schema and was ignored.",
            )
        self.load_tenant()
        self.load_management_groups()
        self.load_subscriptions()
        self.load_resource_groups()
        self.load_resources()
        self.load_role_definitions()
        self.load_principals()
        self.load_memberships()
        self.load_role_assignments()
        self.graph.build_indexes()
        self.check_integrity()
        self.graph.warnings = self.warnings
        return ImportResult(
            graph=self.graph,
            counts=self.counts,
            skipped=self.skipped,
            warnings=self.warnings,
            source_name=self.source_name,
            checksum=checksum(self.doc),
            imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )


def _principal_type(value: str | None) -> PrincipalType | None:
    if not value:
        return None
    key = value.replace(" ", "").replace("_", "").lower()
    return {
        "user": PrincipalType.USER,
        "group": PrincipalType.GROUP,
        "serviceprincipal": PrincipalType.SERVICE_PRINCIPAL,
        "managedidentity": PrincipalType.MANAGED_IDENTITY,
    }.get(key)


def checksum(document: dict[str, Any]) -> str:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def import_document(document: Any, source_name: str = "upload") -> ImportResult:
    """Validate and normalize an already-parsed import document."""
    return _Importer(document, source_name).run()


def import_json_bytes(payload: bytes, source_name: str = "upload", max_bytes: int | None = None) -> ImportResult:
    """Parse and import raw bytes. Enforces the size limit before parsing."""
    if max_bytes is not None and len(payload) > max_bytes:
        raise SchemaError(
            f"Import file is {len(payload) / 1_048_576:.1f} MB, over the "
            f"{max_bytes / 1_048_576:.0f} MB limit."
        )
    try:
        document = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SchemaError(f"Import file is not valid UTF-8 text: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SchemaError(f"Import file is not valid JSON: {exc.msg} (line {exc.lineno}).") from exc
    return import_document(document, source_name)
