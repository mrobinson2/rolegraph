# RoleGraph import schema

A RoleGraph dataset is one JSON object whose top-level keys are arrays. Every
array is optional — a partial export still imports — but records inside an array
are validated, and anything unusable is reported on the Import screen rather
than dropped.

```json
{
  "$schema": "rolegraph-import/v1",
  "tenants": [],
  "managementGroups": [],
  "subscriptions": [],
  "resourceGroups": [],
  "resources": [],
  "roleDefinitions": [],
  "users": [],
  "groups": [],
  "servicePrincipals": [],
  "managedIdentities": [],
  "groupMemberships": [],
  "roleAssignments": []
}
```

Keys beginning with `$` are ignored. Any other unrecognised top-level key is
reported as a warning, never silently skipped.

Sections are processed in the order above: scopes and identities must exist
before assignments and memberships can be checked against them.

## Sections

### `tenants`

One record. More than one is a warning; the first is used.

| Field | Required | Notes |
|---|---|---|
| `id` (or `tenantId`) | yes | Entra tenant id |
| `displayName` | no | Shown throughout the UI |
| `domain` | no | e.g. `contoso.onmicrosoft.com` |

### `managementGroups`

| Field | Required | Notes |
|---|---|---|
| `id` (or `name`) | yes | The management group name, not a full scope |
| `displayName` | no | Defaults to `id` |
| `parent` | no | Another management group name, a full scope, or `/` for the tenant root. Missing means tenant root. |

Management-group parentage cannot be derived from the scope string, so it must
come from these records. A parent that was not imported is reported as an orphan
and the group is shown outside the hierarchy.

### `subscriptions`

| Field | Required | Notes |
|---|---|---|
| `subscriptionId` (or `id`) | yes | GUID, or a full `/subscriptions/{id}` scope |
| `displayName` | no | Defaults to the id |
| `managementGroup` (or `parent`) | no | Management group name or full scope. Missing attaches the subscription to the tenant root and raises a warning. |

### `resourceGroups`

| Field | Required | Notes |
|---|---|---|
| `name` | yes | |
| `subscriptionId` | yes | |
| `location` | no | |

The parent subscription is derived from `subscriptionId`.

### `resources`

Supply either a full `id`, or all of `subscriptionId`, `resourceGroup`, `type`
and `name`.

| Field | Required | Notes |
|---|---|---|
| `id` | if the parts are absent | Full ARM resource id |
| `name` | no | Defaults to the leaf of the id |
| `type` (or `resourceType`) | with the parts form | e.g. `Microsoft.Storage/storageAccounts` |
| `location` | no | |

Ids that are not resource scopes (a subscription id, say) are skipped with an
error-level warning.

### `roleDefinitions`

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Full role definition id |
| `roleName` (or `name`) | yes | |
| `description` | no | |
| `roleType` | no | `BuiltInRole` (default) or `CustomRole` |
| `assignableScopes` | no | Array of scope strings |
| `permissions` | no | Array of permission blocks |

A permission block:

```json
{
  "actions": ["Microsoft.Compute/*"],
  "notActions": [],
  "dataActions": [],
  "notDataActions": []
}
```

A role with no permissions is imported and flagged: it confers no access.

### `users`, `groups`, `servicePrincipals`, `managedIdentities`

All four share a shape. `id` is required and must be unique across all four
sections; a repeated id keeps the first record and reports the rest.

| Field | Used by | Notes |
|---|---|---|
| `id` | all | Entra object id |
| `displayName` (or `name`) | all | Defaults to `id` |
| `userPrincipalName` (or `upn`) | users | |
| `mail` (or `email`) | users | |
| `department` | users | Searchable |
| `appId` (or `clientId`) | service principals | |
| `description` | all | |

### `groupMemberships`

| Field | Required | Notes |
|---|---|---|
| `groupId` | yes | The group being joined |
| `memberId` | yes | A user, group, service principal or managed identity |

Nested groups are expressed by giving a group as `memberId`. A group listed as
its own member is skipped. Duplicate pairs are ignored with a warning.
Membership cycles are imported but reported as errors; resolution stops at the
repeat rather than looping.

### `roleAssignments`

| Field | Required | Notes |
|---|---|---|
| `principalId` | yes | |
| `roleDefinitionId` | yes | Must match a `roleDefinitions` id to resolve |
| `scope` | yes | Any valid Azure scope string |
| `id` | no | Generated if absent; duplicates keep the first |
| `principalType` | no | Cross-checked against the imported identity |
| `createdOn`, `createdBy`, `description` | no | Shown on the access path screen |

An assignment whose role definition was not imported is kept for display but
excluded from access paths, and reported as an error.

## Scope strings

| Level | Form |
|---|---|
| Tenant root | `/` |
| Management group | `/providers/Microsoft.Management/managementGroups/{name}` |
| Subscription | `/subscriptions/{subscriptionId}` |
| Resource group | `/subscriptions/{subscriptionId}/resourceGroups/{name}` |
| Resource | `/subscriptions/{id}/resourceGroups/{rg}/providers/{namespace}/{type}/{name}` |

Scopes are compared case-insensitively and a trailing slash is ignored, matching
Azure's own behaviour. The original string is kept for display.

## What "never silently discard" means

Every record that is skipped, deduplicated, or that points at something absent
produces a warning attached to the snapshot. Warning categories:

| Category | Meaning |
|---|---|
| `malformedRecord` | Skipped: not an object, missing a required field, or an unparseable scope |
| `duplicate` | A repeated id or membership pair; the first was kept |
| `danglingReference` | The record points at an entity that was not imported |
| `missingParent` | A subscription with no management group |
| `typeMismatch` | A declared `principalType` disagrees with the imported identity |
| `membershipCycle` | Groups are members of each other |
| `duplicateAssignment` | The same role assigned more than once at the same scope |
| `emptyPermissions` | A role definition that grants nothing |
| `unknownSection` | A top-level key outside this schema |
| `missingSection` | No tenant record; a placeholder root was used |

## Producing a dataset

The MVP is offline-first: no credentials, no Graph permissions, no tenant
configuration. A collection script (Azure CLI or PowerShell) that emits this
shape is the intended next step — the schema was designed so that script can be
written without changing anything here.
