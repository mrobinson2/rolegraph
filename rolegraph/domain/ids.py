"""Azure scope identifier parsing and normalization.

Scopes in Azure are path-like strings. Four of the five levels encode their own
ancestry in the string; management-group parentage does not, and has to come
from the imported ``managementGroups`` records instead.

    tenant root     /
    management grp  /providers/Microsoft.Management/managementGroups/{name}
    subscription    /subscriptions/{id}
    resource group  /subscriptions/{id}/resourceGroups/{name}
    resource        /subscriptions/{id}/resourceGroups/{name}/providers/{ns}/{type}/{name}
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MG_PREFIX = "/providers/microsoft.management/managementgroups/"


class ScopeKind(str, Enum):
    TENANT = "tenant"
    MANAGEMENT_GROUP = "managementGroup"
    SUBSCRIPTION = "subscription"
    RESOURCE_GROUP = "resourceGroup"
    RESOURCE = "resource"


#: Broadest first. Used for "how broad is this scope" comparisons.
SCOPE_DEPTH: dict[ScopeKind, int] = {
    ScopeKind.TENANT: 0,
    ScopeKind.MANAGEMENT_GROUP: 1,
    ScopeKind.SUBSCRIPTION: 2,
    ScopeKind.RESOURCE_GROUP: 3,
    ScopeKind.RESOURCE: 4,
}


class ScopeParseError(ValueError):
    """Raised when a scope string does not match any known Azure scope shape."""


def normalize_scope(scope: str) -> str:
    """Return a canonical comparison key for a scope string.

    Azure treats scope segments case-insensitively and tolerates a trailing
    slash. Everything inside RoleGraph keys off this normalized form; the
    original string is kept for display.
    """
    if scope is None:
        raise ScopeParseError("scope is required")
    s = scope.strip()
    if not s:
        raise ScopeParseError("scope is empty")
    if not s.startswith("/"):
        raise ScopeParseError(f"scope must start with '/': {scope!r}")
    if len(s) > 1:
        s = s.rstrip("/")
    return s.lower()


@dataclass(frozen=True, slots=True)
class ScopeRef:
    """A parsed scope: what kind of thing it points at, and its raw form."""

    kind: ScopeKind
    scope: str  # normalized
    raw: str  # as supplied
    name: str  # leaf name (mg name, subscription id, rg name, resource name)
    subscription_id: str | None = None
    resource_group: str | None = None
    provider_path: str | None = None  # e.g. microsoft.storage/storageaccounts

    @property
    def depth(self) -> int:
        return SCOPE_DEPTH[self.kind]


def parse_scope(scope: str) -> ScopeRef:
    """Parse an Azure scope string into a :class:`ScopeRef`."""
    norm = normalize_scope(scope)
    raw = scope.strip()

    if norm == "/":
        return ScopeRef(ScopeKind.TENANT, "/", raw, name="/")

    if norm.startswith(MG_PREFIX):
        name = norm[len(MG_PREFIX):]
        if not name or "/" in name:
            raise ScopeParseError(f"malformed management group scope: {scope!r}")
        return ScopeRef(ScopeKind.MANAGEMENT_GROUP, norm, raw, name=name)

    parts = norm.strip("/").split("/")
    if parts[0] != "subscriptions" or len(parts) < 2 or not parts[1]:
        raise ScopeParseError(f"unrecognized scope: {scope!r}")
    sub = parts[1]

    if len(parts) == 2:
        return ScopeRef(ScopeKind.SUBSCRIPTION, norm, raw, name=sub, subscription_id=sub)

    if parts[2] != "resourcegroups" or len(parts) < 4 or not parts[3]:
        raise ScopeParseError(f"unrecognized scope: {scope!r}")
    rg = parts[3]

    if len(parts) == 4:
        return ScopeRef(
            ScopeKind.RESOURCE_GROUP, norm, raw, name=rg, subscription_id=sub, resource_group=rg
        )

    if parts[4] != "providers" or len(parts) < 8:
        raise ScopeParseError(f"unrecognized resource scope: {scope!r}")
    provider_path = "/".join(parts[5:-1])
    return ScopeRef(
        ScopeKind.RESOURCE,
        norm,
        raw,
        name=parts[-1],
        subscription_id=sub,
        resource_group=rg,
        provider_path=provider_path,
    )


def parent_scope_from_string(scope: str) -> str | None:
    """Derive the parent scope that is encoded in the scope string itself.

    Returns ``None`` for tenant root, and for management groups and
    subscriptions, whose parents are only knowable from imported records.
    """
    ref = parse_scope(scope)
    if ref.kind in (ScopeKind.TENANT, ScopeKind.MANAGEMENT_GROUP, ScopeKind.SUBSCRIPTION):
        return None
    if ref.kind == ScopeKind.RESOURCE_GROUP:
        return f"/subscriptions/{ref.subscription_id}"
    return f"/subscriptions/{ref.subscription_id}/resourcegroups/{ref.resource_group}"


def management_group_scope(name: str) -> str:
    return f"/providers/Microsoft.Management/managementGroups/{name}"


def subscription_scope(subscription_id: str) -> str:
    return f"/subscriptions/{subscription_id}"


def resource_group_scope(subscription_id: str, name: str) -> str:
    return f"/subscriptions/{subscription_id}/resourceGroups/{name}"
