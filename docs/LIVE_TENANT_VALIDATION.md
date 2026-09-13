# Live tenant validation — three real exports

This is the acceptance campaign that `HANDOFF.md` §7 and §8 gate correctness work
behind: import at least three real Azure tenant exports, cross-check what RoleGraph
shows against what Azure says, and record which known model gaps actually appear.
Nothing in `rolegraph/` changes during this campaign. The output is evidence.

Read [COLLECTION.md](COLLECTION.md) first; it is the collector's contract. This
document sequences the work and says what to check and what to write down.

## Ground rules

- **Owner approval per tenant, in writing, before any login.** `BRIEF.md` forbids
  connecting to a real tenant without explicit approval. Record who approved,
  which tenant, and on what date in the results log (§7).
- **Exports never enter the repository.** Keep them in a private directory outside
  the checkout. `/tenant.json` is ignored at the repo root only; anything else you
  name is not. Run `git status` before every commit during the campaign.
- **Read-only permissions only.** Azure Reader at the root management group, Graph
  `Directory.Read.All`. Never request Owner, Contributor, or write Graph scopes for
  collection. If a tenant admin offers more, decline.
- **Never collect production as a casual test.** Two of the three tenants may be
  non-production. At least one should be a real production tenant, scheduled with
  its owner, because the model gaps (deny assignments, PIM, ABAC) mostly live there.
- **Results contain identities and scopes.** The results log (§7) lives outside the
  repository too. Only the anonymised summary in §8 is committed.

## Choose the three tenants

Pick tenants that differ, or the campaign proves nothing. Aim for:

| Slot | Profile | What it tests |
|---|---|---|
| T1 | Small dev/test tenant, 1–3 subscriptions, few groups | Baseline: does a real export pass the schema at all |
| T2 | Mid-size tenant with nested groups, custom roles, and management-group hierarchy | Resolver correctness on real hierarchy and membership shapes |
| T3 | Production tenant, ideally with PIM, deny assignments or ABAC conditions in use | Which known gaps appear in the wild, and how badly |

Ask each tenant owner up front, and note the answer:

- Is PIM used for Azure resource roles?
- Are there deny assignments (usually from Blueprints or managed applications)?
- Are there conditional (ABAC) role assignments?
- Are there classic administrators still present?
- Approximate counts: users, groups, service principals, subscriptions, role assignments.

These answers are the expected-findings list you check against in §5.

## Per-tenant procedure

Repeat §1–§6 for each tenant. Do not start T2 before T1 is fully logged.

### 1. Prepare the workstation

```powershell
pwsh --version                          # 7.4 or newer
az --version                            # Azure CLI present
Get-Module -ListAvailable Microsoft.Graph.Authentication
```

Create a private export directory outside the repository, e.g.
`~/rolegraph-validation/<tenant-slug>/`. On macOS use a real path, not `/tmp`.

Run the dry-run from the repository root and keep its output; it prints the
permission plan you will hand to the tenant admin:

```powershell
pwsh -NoProfile -File ./scripts/collect/Export-RoleGraphDataset.ps1 `
  -OutputPath ~/rolegraph-validation/<tenant-slug>/export-01.json `
  -IncludeResources -DryRun
```

### 2. Authenticate and collect

Same PowerShell session for `Connect-MgGraph` and the script; a process-scoped
Graph login does not survive a new `pwsh -File` process.

```powershell
$tenant = 'TENANT-GUID'
az login --tenant $tenant
az account show --query tenantId -o tsv       # must equal $tenant
Connect-MgGraph -TenantId $tenant -Scopes 'Directory.Read.All' -ContextScope Process
# Add 'Member.Read.Hidden' only if approved and hidden-membership groups exist.

$sw = [Diagnostics.Stopwatch]::StartNew()
./scripts/collect/Export-RoleGraphDataset.ps1 `
  -TenantId $tenant `
  -OutputPath ~/rolegraph-validation/<tenant-slug>/export-01.json `
  -IncludeResources
$exit = $LASTEXITCODE
$sw.Stop()
"exit=$exit elapsed=$($sw.Elapsed)"
if ($exit -ne 0) { throw 'Collection failed; do not import the output.' }
```

Record: exit code, wall-clock time, output file size. If collection fails, keep
the stderr text verbatim in the log, fix the cause (usually permissions or a stale
CLI subscription cache), and rerun to a **new** filename (`export-02.json`). Do
not use `-Overwrite` during validation; every attempt is evidence.

### 3. Review collector warnings before importing

```powershell
$dataset = Get-Content -Raw ~/rolegraph-validation/<tenant-slug>/export-01.json | ConvertFrom-Json
$dataset.'$collection' | Select-Object -ExcludeProperty warnings | Format-List
$dataset.'$collection'.warnings | Group-Object category | Sort-Object Count -Descending | Format-Table Count, Name
$dataset.'$collection'.warnings | Format-Table category, message -Wrap
foreach ($s in 'tenants','managementGroups','subscriptions','resourceGroups','resources','roleDefinitions','roleAssignments','users','groups','servicePrincipals','managedIdentities','groupMemberships') {
  '{0,-20} {1}' -f $s, @($dataset.$s).Count
}
```

Log the per-section counts and the per-category warning counts. Pay attention to:

- `conditionNotEvaluated` — ABAC present. Count them; note which roles and scopes.
- `subscriptionNotCollected`, `disabledSubscription`, `otherTenant` — coverage gaps.
  Compare against `az account list --all` for the same tenant.
- `unsupportedMember`, `unsupportedMembership` — membership shapes the schema drops.
- `duplicate` — repeated records seen across scope queries; expected in small
  numbers, suspicious in large ones.

The importer ignores `$collection`, so these warnings will **not** appear in the
web UI. This step is the only place they get reviewed.

### 4. Import into a disposable RoleGraph instance

Do not import into a database that holds anything you care about. Use a fresh
data directory and a non-default port:

```bash
mkdir -p ~/rolegraph-validation/<tenant-slug>/data
ROLEGRAPH_DATA_DIR=~/rolegraph-validation/<tenant-slug>/data \
  .venv/bin/uvicorn rolegraph.web.app:app --port 8011
```

Open <http://localhost:8011/import> → **Dataset JSON file** → choose the export →
**Import file**. Record from the result page:

- imported and skipped counts per section
- import warning counts per category (`malformedRecord`, `danglingReference`,
  `missingParent`, `typeMismatch`, `membershipCycle`, `duplicateAssignment`, …)
- page load time for `/`, `/identities`, `/findings`, `/privileged` (browser
  network tab is fine). Anything over two seconds goes in the performance section.

If the upload is rejected for size, set `ROLEGRAPH_MAX_UPLOAD_BYTES` higher and
note the export size; the 25 MB default is itself a finding if a mid-size tenant
exceeds it.

Every `malformedRecord` and `danglingReference` warning on a real export is a
schema or collector defect until proven otherwise. Copy the first ten of each
into the log with the raw record they refer to.

### 5. Cross-check against Azure

Pick five identities per tenant, chosen by the tenant owner, not by you:

1. one user with a **direct** privileged assignment at a subscription
2. one user who reaches privilege only through a **nested group** (two or more hops)
3. one **service principal** or managed identity with any assignment
4. one identity the owner believes has **no** access to a named production scope
5. one identity affected by a known gap in this tenant — PIM-eligible, denied, or
   conditioned — if the owner said any exist

For each, in RoleGraph open `/identities/<objectId>` and record what it shows.
Then get Azure's answer with the same credentials:

```bash
# Effective assignments for the principal, including inherited
az role assignment list --all --assignee <objectId> --include-inherited \
  --include-groups -o table

# Group membership Azure sees (transitive) — compare with RoleGraph's paths
az rest --method GET \
  --url "https://graph.microsoft.com/v1.0/directoryObjects/<objectId>/transitiveMemberOf?\$select=id,displayName"

# Deny assignments at a scope (T3 especially)
az rest --method GET \
  --url "https://management.azure.com<scope>/providers/Microsoft.Authorization/denyAssignments?api-version=2022-04-01"

# PIM eligible assignments at a scope
az rest --method GET \
  --url "https://management.azure.com<scope>/providers/Microsoft.Authorization/roleEligibilityScheduleInstances?api-version=2020-10-01"
```

Classify each identity as one of:

| Verdict | Meaning |
|---|---|
| `match` | Same grants, same scopes, and every RoleGraph path corresponds to a real membership chain |
| `over-report` | RoleGraph shows access Azure blocks or does not grant (deny, condition, stale membership). **Highest severity — the confident-wrong case.** |
| `under-report` | Azure grants something RoleGraph does not show (PIM eligible, classic admin, uncollected subscription, tenant `/` scope) |
| `path-wrong` | Grant matches but the explanation names the wrong group chain |

Also compare the Findings page against the owner's expected-findings list from
the tenant intake: does every planted-in-reality problem the owner knows about
appear as a finding, and are any findings plainly wrong?

### 6. Repeat collection once

Run §2 again to `export-02.json` at least an hour later, ideally the next day.
Import it as a second snapshot. Confirm:

- both snapshots list on `/import`, activation switches between them cleanly
- section counts differ only where the owner can explain the difference
- collection time is within 20% of the first run

This is the raw material for the snapshot-diff feature; keep both files.

### 7. Write the per-tenant results log

One file per tenant, outside the repository:
`~/rolegraph-validation/<tenant-slug>/RESULTS.md`. Sections, in order:

1. **Approval** — approver, date, tenant slug, permissions granted
2. **Intake answers** — the five owner questions from "Choose the three tenants"
3. **Collection** — attempts, exit codes, elapsed time, file size, `$collection` metadata
4. **Collector warnings** — counts by category, full list of anything not
   `modelLimitations` / `visibility` / `resourcesOmitted`
5. **Import** — counts per section, warning counts by category, first ten
   malformed/dangling records with raw JSON
6. **Cross-check table** — the five identities, verdicts, Azure output, RoleGraph output
7. **Findings review** — expected vs shown, false positives, missing
8. **Performance** — page timings, anything slow, `membership_paths` truncation seen
9. **Second collection** — diff of counts, unexplained changes

## After all three tenants

### 8. Commit the anonymised summary

Add `docs/LIVE_TENANT_VALIDATION_RESULTS.md` to the repository containing **no**
tenant IDs, object IDs, names, or scope strings. Only:

- three rows: tenant slot, profile, record counts, collection time, export size
- cross-check tally: 15 identities → how many `match` / `over-report` /
  `under-report` / `path-wrong`
- which model gaps appeared, ranked by count of affected assignments:
  deny assignments, PIM eligibility, ABAC conditions, `notActions`, classic admins,
  tenant `/` scope
- schema or collector defects found, each linked to an issue or fixed in the same PR
- performance observations at the largest tenant size

### 9. Update the handoff

In `HANDOFF.md` §7, replace "Only synthetic data has ever been imported" with a
pointer to the results file, and reorder §8.3 (deny / PIM / effective actions) by
what the tally showed. That reorder is the whole point of the campaign: the next
correctness feature is whichever gap produced the most `over-report` verdicts.

## Exit criteria

The campaign is complete when:

- three tenants collected and imported, each twice
- fifteen identities cross-checked with a verdict each
- every `over-report` has a root cause named (deny, condition, stale group, bug)
- every `malformedRecord` / `danglingReference` on real data has an issue or a fix
- the anonymised summary is committed and `HANDOFF.md` points at it
- `git status` shows no export files staged at any point
