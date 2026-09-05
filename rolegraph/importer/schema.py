"""Import schema: the shape of a RoleGraph dataset, and low-level validation.

The document is a single JSON object of named arrays. Every array is optional -
a partial export still imports - but records inside an array are validated, and
anything unusable is reported rather than dropped.
"""

from __future__ import annotations

from typing import Any

#: Top-level arrays, in the order they are processed. Order matters: scopes and
#: principals must exist before assignments and memberships can be checked.
SECTIONS: tuple[str, ...] = (
    "tenants",
    "managementGroups",
    "subscriptions",
    "resourceGroups",
    "resources",
    "roleDefinitions",
    "users",
    "groups",
    "servicePrincipals",
    "managedIdentities",
    "groupMemberships",
    "roleAssignments",
)

#: Fields a record cannot be understood without.
REQUIRED_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    # each entry is a tuple of acceptable aliases; at least one must be present
    "tenants": (("id", "tenantId"),),
    "managementGroups": (("id", "name"),),
    "subscriptions": (("id", "subscriptionId"),),
    "resourceGroups": (("name",), ("subscriptionId",)),
    "resources": (("id", "name"),),
    "roleDefinitions": (("id",), ("name", "roleName")),
    "users": (("id",),),
    "groups": (("id",),),
    "servicePrincipals": (("id",),),
    "managedIdentities": (("id",),),
    "groupMemberships": (("groupId",), ("memberId",)),
    "roleAssignments": (("principalId",), ("roleDefinitionId",), ("scope",)),
}

MAX_JSON_BYTES_DEFAULT = 25 * 1024 * 1024


class SchemaError(ValueError):
    """The document as a whole cannot be imported."""


def first(record: dict[str, Any], *names: str) -> Any:
    """Return the first present, non-empty value among ``names``."""
    for name in names:
        value = record.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if v not in (None, ""))
    return ()


def validate_document(document: Any) -> list[str]:
    """Structural checks on the whole document. Returns a list of section names
    present but not recognised. Raises :class:`SchemaError` if unusable."""
    if not isinstance(document, dict):
        raise SchemaError("Import file must be a JSON object with named arrays at the top level.")
    for section in SECTIONS:
        value = document.get(section)
        if value is not None and not isinstance(value, list):
            raise SchemaError(f"Section '{section}' must be a JSON array, got {type(value).__name__}.")
    if not any(isinstance(document.get(s), list) and document[s] for s in SECTIONS):
        raise SchemaError(
            "Import file contains none of the expected sections: " + ", ".join(SECTIONS)
        )
    return [k for k in document if k not in SECTIONS and not k.startswith("$")]


def missing_required(section: str, record: dict[str, Any]) -> list[str]:
    """Names of required fields absent from ``record``."""
    missing: list[str] = []
    for aliases in REQUIRED_FIELDS.get(section, ()):
        if first(record, *aliases) is None:
            missing.append(" or ".join(aliases))
    return missing
