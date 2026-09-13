"""Strict target and observation contracts, separate from the permissive importer.

Assignment comparison is deliberately not effective-action evaluation. In
particular, ABAC expressions remain case-sensitive opaque strings.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

GUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
Text = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


def guid(value: str) -> str:
    if not re.fullmatch(GUID, value):
        raise ValueError("Use a directory object or role GUID, not a display name or application ID.")
    return value.lower()


def scope_id(value: str) -> str:
    value = value.rstrip("/") or "/"
    if value == "/":
        return value
    if not value.startswith("/") or re.search(r"[?#%\\\s]", value):
        raise ValueError("Use an absolute Azure resource scope without query strings or escapes.")
    parts = value[1:].split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("Scope contains an invalid path segment.")
    lower = value.lower()
    mg = "/providers/microsoft.management/managementgroups/"
    if lower.startswith(mg) and len(parts) == 4:
        return lower
    if len(parts) < 2 or parts[0].lower() != "subscriptions":
        raise ValueError("Use a subscription, resource group, resource, management group, or / scope.")
    guid(parts[1])
    if len(parts) == 2:
        return lower
    rest = parts[2:]
    if rest[0].lower() == "resourcegroups":
        if len(rest) < 2:
            raise ValueError("Resource group name is missing.")
        rest = rest[2:]
    if rest and (len(rest) < 4 or len(rest) % 2 or rest[0].lower() != "providers"):
        raise ValueError("Resource scope requires a provider and resource type/name pairs.")
    return lower


def role_id(value: str) -> str:
    if re.fullmatch(GUID, value):
        return value.lower()
    match = re.fullmatch(r"(.*)/providers/microsoft\.authorization/roledefinitions/(" + GUID + r")/?", value, re.I)
    if not match:
        raise ValueError("Use a role definition GUID or full roleDefinitions resource ID.")
    scope_id(match[1] or "/")
    return guid(match[2])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("schemaVersion", mode="before", check_fields=False)
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("schemaVersion must be an integer.")
        return value


class ScanScope(StrictModel):
    id: Text
    includeDescendants: bool = True

    _normalize = field_validator("id")(scope_id)

    @model_validator(mode="after")
    def explicit_management_group_boundary(self):
        if self.includeDescendants and not self.id.startswith("/subscriptions/"):
            raise ValueError("Management group and / scopes require includeDescendants=false; enumerate subscriptions explicitly.")
        return self

    def contains(self, scope: str) -> bool:
        return scope == self.id or (self.includeDescendants and scope.startswith(self.id + "/"))


class Assignment(StrictModel):
    principalId: Text
    roleDefinitionId: Text
    scope: Text
    condition: str | None = Field(default=None, max_length=32768)
    conditionVersion: Literal["2.0"] | None = None
    delegatedManagedIdentityResourceId: Text | None = None

    _principal = field_validator("principalId")(guid)
    _role = field_validator("roleDefinitionId")(role_id)
    _scope = field_validator("scope")(scope_id)

    @field_validator("delegatedManagedIdentityResourceId")
    @classmethod
    def delegated_scope(cls, value):
        return scope_id(value) if value is not None else None

    @field_validator("condition")
    @classmethod
    def empty_condition(cls, value):
        if value is not None and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def condition_has_version(self):
        if (self.condition is None) != (self.conditionVersion is None):
            raise ValueError("condition and conditionVersion must be specified together.")
        return self

    @property
    def key(self) -> tuple[str, str, str]:
        return self.principalId, self.roleDefinitionId, self.scope

    @property
    def constraints(self) -> tuple:
        return self.condition, self.conditionVersion, self.delegatedManagedIdentityResourceId


class ApprovedAssignment(Assignment):
    principalName: Text | None = None
    roleName: Text | None = None
    justification: Text | None = None


class ObservedAssignment(Assignment):
    id: Text

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value):
        match = re.fullmatch(r"(.*)/providers/microsoft\.authorization/roleassignments/(" + GUID + r")/?", value, re.I)
        if not match:
            raise ValueError("Observed assignment id must be a full Azure roleAssignments resource ID.")
        scope_id(match[1] or "/")
        return value.rstrip("/").lower()

    @model_validator(mode="after")
    def id_matches_originating_scope(self):
        origin = self.id.rsplit("/providers/microsoft.authorization/roleassignments/", 1)[0] or "/"
        if origin != self.scope:
            raise ValueError("Assignment ID and originating scope disagree.")
        return self


class Permission(StrictModel):
    actions: list[Text]
    notActions: list[Text]
    dataActions: list[Text]
    notDataActions: list[Text]

    @property
    def canonical(self) -> tuple:
        return tuple(tuple(sorted({a.lower() for a in getattr(self, field)})) for field in type(self).model_fields)


class PinnedRole(StrictModel):
    id: Text
    permissions: list[Permission] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def full_role_id(cls, value):
        role_id(value)
        if not value.startswith("/"):
            raise ValueError("Pinned role definitions need a full resource ID for collection.")
        return value.rstrip("/").lower()

    @property
    def key(self) -> str:
        return role_id(self.id)

    @property
    def canonical(self) -> tuple:
        return tuple(sorted({p.canonical for p in self.permissions}))


class TargetState(StrictModel):
    schema_ref: str | None = Field(default=None, alias="$schema")
    schemaVersion: Literal[1]
    tenantId: Text
    scopes: list[ScanScope] = Field(min_length=1)
    assignments: list[ApprovedAssignment]
    roleDefinitions: list[PinnedRole] = Field(default_factory=list)

    _tenant = field_validator("tenantId")(guid)

    @model_validator(mode="after")
    def unambiguous_target(self):
        for label, values in (
            ("scope", [s.id for s in self.scopes]),
            ("assignment", [a.key for a in self.assignments]),
            ("role definition", [r.key for r in self.roleDefinitions]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {label} in target state.")
        for assignment in self.assignments:
            if not any(s.contains(assignment.scope) for s in self.scopes):
                raise ValueError(f"Approved assignment scope {assignment.scope} is outside the declared scan scopes. Add its originating scope explicitly.")
        return self


class ScopeResult(StrictModel):
    scope: ScanScope
    assignments: list[ObservedAssignment]


class Observation(StrictModel):
    schemaVersion: Literal[1]
    tenantId: Text
    collectedAt: str
    scopeResults: list[ScopeResult]
    roleDefinitions: list[PinnedRole] = Field(default_factory=list)

    _tenant = field_validator("tenantId")(guid)

    @field_validator("collectedAt")
    @classmethod
    def timestamp(cls, value):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("collectedAt must include its UTC offset.")
        return value
