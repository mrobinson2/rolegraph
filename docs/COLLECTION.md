# Collect an offline dataset

`scripts/collect/Export-RoleGraphDataset.ps1` reads Microsoft Graph and Azure
Resource Manager through existing authenticated sessions and writes one JSON
document accepted by RoleGraph. The collector runs separately from the web app;
the app remains offline and never receives credentials.

## Before starting

- PowerShell **7.4 or newer** (`pwsh`), Azure CLI, and the
  `Microsoft.Graph.Authentication` PowerShell module, installed separately.
- Public Azure (`AzureCloud`) and Graph `Global`. Sovereign clouds are refused.
- Azure **Reader** at the tenant root management group, inherited down through
  the scopes to collect. A subscription-only login is not a complete tenant view.
- Graph **Directory.Read.All** with administrator consent. Hidden-membership
  groups additionally need **Member.Read.Hidden** and a supported directory role.
  The collector refuses visible hidden groups when that scope is not in the
  Graph context; this conservative check also applies to application sessions.
- An existing private output directory, sufficient disk space, and a fresh
  filename. Exports contain sensitive identity and permission information.

An administrator should approve the read permissions. The script never grants
permissions, installs dependencies, logs in, or changes the selected subscription.

## Preview without credentials

From the repository root:

```powershell
pwsh -NoProfile -File ./scripts/collect/Export-RoleGraphDataset.ps1 `
  -OutputPath ./tenant.json -IncludeResources -DryRun
```

Dry-run validates the destination and prints the collection plan and permissions.
It does **not** contact Azure, load Graph modules, inspect authenticated sessions,
create directories, or create the export. It is not a permission/connectivity test.
The parent directory must already exist and existing files need `-Overwrite`,
even during dry-run. Symbolic links/junctions in the path are rejected. On macOS,
use `/private/tmp/...`, not the `/tmp` symlink, for temporary exports.

## Authenticate, then collect

In a PowerShell session, authenticate explicitly to the intended tenant. These
are **operator setup steps**, not actions performed by the collector:

```powershell
$tenant = 'YOUR-TENANT-GUID'
az login --tenant $tenant
Connect-MgGraph -TenantId $tenant -Scopes 'Directory.Read.All' -ContextScope Process
# If required and approved, include 'Member.Read.Hidden' in -Scopes too.

./scripts/collect/Export-RoleGraphDataset.ps1 `
  -TenantId $tenant -OutputPath ./tenant.json -IncludeResources
if ($LASTEXITCODE -ne 0) { throw 'Collection failed; do not import the output.' }
```

Run the script in the **same PowerShell session** as `Connect-MgGraph`; a new
`pwsh -File` process will not inherit a process-scoped Graph login. The script
checks Azure CLI, Graph, organization response, and optional `-TenantId` agree.
Refresh your CLI login deliberately when its subscription cache is stale.

Omit `-IncludeResources` for a smaller export; `resources` remains an empty array
and the omission is reported. Resource-level role assignments are still read.
Use a new output filename for each collection. `-Overwrite` is an explicit opt-in
to replace one existing file after all reads and serialization have succeeded.

## Coverage and safety boundaries

| Area | Behavior |
|---|---|
| Directory | Organization, paginated users/groups/service principals; managed identities separated by servicePrincipalType. |
| Membership | Direct group edges, not transitive flattening. Every service principal's direct `memberOf` is also read because Graph v1.0 group membership can omit service principals. |
| Hierarchy | Management-group entities and enabled subscriptions in the CLI cache for this tenant; resource groups per subscription. Missing visible subscriptions are named in warnings. |
| RBAC | Definitions and assignments per management group; assignments across each subscription and descendants. Missing assigned definitions are fetched at their assignment scope. |
| Identity keys | Role-definition GUIDs use one canonical provider ID; assignment IDs use their final name segment for access-path URLs. Identical repeats are deduplicated with warnings; conflicting records stop collection. |
| Failed required reads | Tenant mismatch, Graph/CLI failure, bad response shape, repeated/foreign pagination link, and unreadable assigned roles stop the export. No new output is written for these failures. |
| File writes | The script itself writes only the requested JSON file. It creates no output directories, temporary files, transcripts, or app database records. New Unix files use mode `0600`; Windows files inherit the directory ACL. Overwriting retains existing permissions. |

**Output-only is a script boundary, not a filesystem sandbox.** Azure CLI and
Graph authentication/runtime dependencies may maintain their own caches or logs.
They are not controlled by this script. Run with pre-provisioned credentials and
an OS-level filesystem/network policy if you need a strict whole-process boundary.
Do not export into directories other users can alter concurrently. Link checks
are not protection against every filesystem race or hard-link alias.

Reads complete before the destination is opened, but the final write is **not
atomic**: a disk-full event or process interruption during writing can leave a
partial file, including with `-Overwrite`. No sidecar backup is created. Check
exit status and parse the output before importing; keep earlier exports separately.

The export is a view of what the authenticated sessions can see, not proof of
tenant-wide completeness or a transactionally consistent point-in-time snapshot.
Unassigned custom definitions visible only at narrower scopes may not be included.
Large tenants entail many sequential reads; Graph SDK/CLI retry behavior is used,
and an exhausted request fails the export. Large-tenant performance is unverified.

RoleGraph does not evaluate deny assignments, ABAC conditions, PIM eligibility,
or net action exclusions (`notActions`/`notDataActions`). Tenant `/` assignments
and classic administrators are not collected. **Do not treat the result as an
authorization decision.** Review it as an assignment and membership inventory.

## Validate and import

Collection warnings are printed to stderr and included in `$collection.warnings`.
Review them before upload:

```powershell
$dataset = Get-Content -Raw ./tenant.json | ConvertFrom-Json
$dataset.'$collection'.warnings | Format-Table category, message -Wrap
```

All twelve [schema](IMPORT_SCHEMA.md) arrays are present, including empty ones.
`$collection` holds timestamps, tenant, collector version and coverage warnings.
The current importer ignores `$` metadata: its own import warnings do **not**
replace the collector's coverage warnings. Keep the original export for provenance.

Open **Import → Dataset JSON file → Import file**. Review imported/skipped counts
and warnings; verify a known direct grant, a nested-group grant, and subscription
parentage before relying on the snapshot. The upload limit defaults to 25 MB.

## Verification status and sources

The automated collector suite executes real PowerShell serialization, paging,
normalization, filesystem safeguards and the actual Python importer against
cloud-response doubles. **No live Azure tenant was accessed for this build.**
Follow [TESTING_AND_SCREENSHOTS.md](TESTING_AND_SCREENSHOTS.md) to reproduce it.

Implementation references: [Graph group members and service-principal caveat](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0),
[management hierarchy entities](https://learn.microsoft.com/en-us/rest/api/managementgroups/entities/list?view=rest-managementgroups-2020-05-01),
[Azure CLI assignment listing](https://learn.microsoft.com/en-us/cli/azure/role/assignment?view=azure-cli-latest).
