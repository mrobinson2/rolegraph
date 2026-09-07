# RoleGraph — engineering handoff

Written for an AI coding agent (Codex or similar) picking this repository up
cold. Read this, then `docs/BRIEF.md` (the product spec, authoritative) and
`docs/ARCHITECTURE.md` (why the code is shaped the way it is).

Last verified: September 7, 2026 — 181 tests passing with PowerShell enabled;
the isolated Chrome browser journey passed and produced 27 desktop/mobile/tablet
screenshots. This update adds the standalone collector and UI polish.
Live Azure collection remains unverified. The earlier MVP Docker verification was
at commit `48d4e38`; Docker was not rerun for this update. Reproduce current checks
with `docs/TESTING_AND_SCREENSHOTS.md`.

---

## 1. What this is in one paragraph

RoleGraph reads an **offline** JSON export of an Azure tenant's RBAC data —
identities, groups, role definitions, role assignments, and the tenant → management
group → subscription → resource group → resource hierarchy — resolves every route by
which an identity acquires a permission, and explains that route in plain English.
The promise is *"See who has access to what, and exactly why."* It runs entirely
inside the customer's environment, needs no Azure credentials, and makes no
outbound network calls.

## 2. Get it running in 60 seconds

```bash
cd rolegraph
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest                         # collector tests need pwsh
.venv/bin/uvicorn rolegraph.web.app:app --port 8000
```

Then open <http://localhost:8000> and click **Load Contoso demo**.

Container path (what customers use):

```bash
ROLEGRAPH_PORT=8010 docker compose up      # 8000 is often taken locally
```

Regenerate the demo tenant after editing it:

```bash
.venv/bin/python scripts/generate_contoso_demo.py
```

**Do not hand-edit `data/demo/contoso.json`.** It is generated from
`scripts/generate_contoso_demo.py`, which also documents every deliberately
planted RBAC problem in its `$notes.intentionalIssues` block. Edit the script.

## 3. Map of the code

Roughly 3,900 lines of Python plus templates. Dependencies point one way:
`web` → `findings`/`resolver`/`storage` → `importer` → `domain`.

| Path | What lives here |
|---|---|
| `rolegraph/domain/ids.py` | Azure scope string parsing and normalization. `parse_scope`, `normalize_scope`, `ScopeKind`, `SCOPE_DEPTH`. |
| `rolegraph/domain/entities.py` | Frozen dataclasses for every entity, plus `AccessGraph` — the in-memory tenant with its indexes. Start here. |
| `rolegraph/domain/hierarchy.py` | Scope tree walks: `ancestors`, `descendants`, `covers`, `scope_chain`, `orphan_scopes`. All cycle-safe. |
| `rolegraph/importer/schema.py` | The section list, required-field table, and document-level validation. |
| `rolegraph/importer/importer.py` | `import_document` / `import_json_bytes` → `ImportResult`. Every skip or oddity emits an `ImportWarning`. |
| `rolegraph/resolver/membership.py` | Recursive nested-group resolution. `membership_paths` returns **every distinct path**, not the shortest. |
| `rolegraph/resolver/access.py` | `AccessPath` — the unit of explanation — plus `access_paths`, `access_at_scope`, `explain`, `path_steps`. |
| `rolegraph/findings/engine.py` | `@rule` registry, `RuleContext`, `evaluate`, `Finding`. |
| `rolegraph/findings/rules.py` | The 14 rules. Each is a pure function of `RuleContext`. |
| `rolegraph/storage/models.py` | SQLAlchemy tables. Everything carries `snapshot_id`. |
| `rolegraph/storage/repository.py` | `save_import`, `load_graph`, snapshot activation/deletion, audit log, graph cache. |
| `rolegraph/web/viewmodels.py` | All template shaping. Templates stay declarative; put logic here, not in Jinja. |
| `rolegraph/web/routes/` | One module per screen. `_shared.py` has `dataset()`, `render()`, `is_htmx()`. |
| `rolegraph/web/relationship_graph.py` | Deterministic four-column SVG layout for one identity. |
| `rolegraph/config.py` | `get_settings()` — env vars plus `config/privileged_roles.yaml`. |

The single most important type is `AccessPath` in `resolver/access.py`. Identity
explorer, access path screen, privileged view and every finding are all derived
from lists of these. If you understand that dataclass and `access_paths()`, you
understand the application.

## 4. Invariants — do not break these

These are load-bearing. Several have tests that will fail loudly; the rest are
product promises that a reviewer will catch.

1. **No outbound network calls from the application.** No HTTP client in the
   request path, ever. The CSP in `web/app.py` enforces it browser-side and
   `htmx.min.js` is vendored locally for the same reason. This is a core sales
   claim, not a nicety.
2. **No application credentials.** The app reads offline exports only. The
   standalone `scripts/collect/Export-RoleGraphDataset.ps1` uses operator-managed
   Graph/CLI sessions outside the web process. Do not add Azure SDK dependencies
   to the application without an explicit decision to build connected mode.
3. **Findings are deterministic.** Same snapshot in, byte-identical list out.
   No scoring, no likelihood estimates, no model calls. `severity` is a fixed
   label on the rule. `test_evaluation_is_deterministic` guards this.
4. **Nothing is discarded silently.** Every skipped, deduplicated or dangling
   record produces an `ImportWarning` attached to the snapshot. If you add a
   code path that drops a record, add the warning in the same change.
5. **`domain/` performs no I/O.** No database, no filesystem, no framework types.
   It is why the domain and resolver suites need neither.
6. **Snapshots are immutable once written.** `_GRAPH_CACHE` in
   `storage/repository.py` relies on this and has no invalidation beyond deletion.
7. **Every recursive walk is cycle-safe and depth-capped.** Real tenants contain
   accidental group cycles. A page render must never hang. See `MAX_DEPTH` and
   `MAX_PATHS` in `resolver/membership.py`.
8. **Plain English before raw identifiers.** Every screen leads with names and a
   sentence; object ids and full scope strings are secondary. This is the product.

## 5. Things that will trip you up

- **Management-group parentage is not in the scope string.** Four of the five
  scope levels encode their ancestry; management groups do not. Their parent comes
  from the `managementGroups` records. `parent_scope_from_string` returns `None`
  for tenant, MG and subscription for exactly this reason.
- **Scopes are keyed lowercase.** `AccessGraph.scopes` is keyed by
  `normalize_scope(...)` output. `ScopeNode.raw_scope` keeps the original for
  display. Compare normalized, display raw.
- **`get_settings()` is `lru_cache`d.** Changing an environment variable
  mid-process has no effect. Tests that need different settings construct
  `Settings(...)` directly — see `tests/test_findings.py::settings`.
- **`membership_paths` returns duplicates by design.** Two routes to the same
  group are two entries. `distinct_grants()` collapses them when you want unique
  (identity, role, scope) triples. Do not "fix" the duplication.
- **Port 8000 is frequently already bound** on a dev machine. Compose honours
  `ROLEGRAPH_PORT`.
- **`docker compose` uses a named volume** (`rolegraph-data`). Do not delete it
  just to test first-run behavior. The screenshot runner uses a disposable SQLite
  database and separate loopback server without touching existing snapshots.
- **`_grants_write` in `findings/rules.py` is a suffix heuristic**, not real
  action semantics. It is correct on every built-in role checked so far. If you
  build a proper action model, that function is the first consumer.

## 6. How to make common changes

**Add a findings rule** — in `rolegraph/findings/rules.py`:

```python
@rule("my-rule-id")
def my_rule(ctx: RuleContext) -> Iterable[Finding]:
    for path in ctx.all_paths():
        if not some_condition(path):
            continue
        yield Finding(
            rule_id="my-rule-id",
            title="Short noun phrase",
            severity="high",           # fixed label, never computed
            what="One sentence naming the identity, role and scope.",
            why="Why a reviewer should care, in plain English.",
            principal=path.principal,
            role=path.role,
            scope=path.scope,
            scope_display=path.scope_display,
            paths=(path,),
            evidence=("Assignment ...", "Covers N scopes."),
        )
```

Importing the module registers it. Add a focused test in `tests/test_findings.py`
using `GraphBuilder` — build the smallest graph that triggers the rule, and one
that must *not* trigger it.

**Add an import schema section** — add it to `SECTIONS` and `REQUIRED_FIELDS` in
`importer/schema.py` (order matters: referenced entities load first), write a
`load_*` method on `_Importer`, call it from `run()`, then add the table to
`storage/models.py` and both directions in `storage/repository.py`. Document it
in `docs/IMPORT_SCHEMA.md` and add malformed examples to
`tests/fixtures/malformed.json`.

**Add a screen** — a route module in `web/routes/`, registered in
`web/app.py`; a view-model function in `web/viewmodels.py`; a template extending
`base.html`. If it has a search box, return
`partials/<thing>_rows.html` when `is_htmx(request)` and the full page otherwise —
`routes/identities.py` is the pattern.

**Test conventions** — `tests/factories.py::GraphBuilder` builds synthetic graphs
without touching the importer or the database; `contoso_like()` is a compact
tenant exercising every relationship. Web tests use a `tmp_path` SQLite file and
must call `repository.clear_cache()` and `state.invalidate()`. Test names are
sentences describing the behaviour, not the function under test.

## 7. Known gaps, in the order they matter

**Correctness of the RBAC model** — a customer running this on a real tenant will
hit these:

1. **`notActions` are displayed but not subtracted.** RoleGraph reports which role
   you hold, not the net effective action set after exclusions.
2. **Deny assignments are not modelled at all.** RoleGraph will currently report
   access that a deny assignment blocks. This is the most dangerous gap: it makes
   the tool wrong in the confident direction.
3. **ABAC conditions on role assignments are ignored** — a conditioned assignment
   is treated as unconditional.
4. **PIM eligible assignments are not read.** Someone one activation away from
   Owner does not appear as an Owner.
5. **Classic administrators are not modelled.**

**Product and operational:**

6. **No authentication.** Anyone who can reach the port reads everything. Entra ID
   sign-in is the intended path and the app is structured for it.
7. **Collector needs live-tenant acceptance.** It exists with dry-run, failure
   safeguards and mocked integration tests. Review `docs/COLLECTION.md`, especially
   subscription-cache coverage and `$collection` warnings (not shown by the importer).
8. **Performance is unmeasured above demo scale** (73 records).
   `principals_with_access_to` scans every identity per scope page and will be the
   first thing to feel slow. `membership_paths` truncates silently at 500 paths /
   depth 20.
9. **Only synthetic data has ever been imported.** No real Azure export has been
   through the schema.
10. **The relationship diagram truncates at 12 rows per column** with no
    indication to the viewer.

## 8. Collection delivered; follow-on work

The collection implementation is delivered. The next implementation priorities
remain snapshot comparison and RBAC correctness; neither is included in this update.

### 1. The collection script — implemented, live validation next

`scripts/collect/Export-RoleGraphDataset.ps1` uses Azure CLI reads plus Graph GETs,
checks tenant agreement and emits all twelve schema arrays. It preserves direct
membership edges, including service-principal `memberOf` supplementation; do not
replace them with flattened `transitiveMembers`, which loses explanatory paths.

`-DryRun` needs no credentials and performs no cloud calls or file writes. Live
collection writes only its requested JSON; authentication dependencies may maintain
their own caches, so this is not an OS sandbox. The final write is not atomic.
Read failures preserve an existing output, but interrupted writes may not. See
`docs/COLLECTION.md` for the exact contract, permission requirements and warnings.

Tests execute the actual PowerShell collector and Python importer against mocked
cloud responses. An approved real tenant is still needed to validate live API
behavior and inventory coverage. No real credentials or tenant connections were
used in this implementation.

UI polish is also delivered: responsive navigation, locally scrolling tables and
diagrams, readable SVG links, live result counts and keyboard/accessibility basics.
`scripts/capture_screenshots.mjs` exercises a disposable demo app and produces a
review gallery; instructions are in `docs/TESTING_AND_SCREENSHOTS.md`.

### 2. Snapshot comparison — "what changed since last scan"

Diff two snapshots: assignments added and removed, custom roles broadened,
privilege gained, identities that newly reached production.

The groundwork exists — `snapshots` rows, `snapshot_id` on every entity,
`repository.load_graph(id)` for any snapshot — so this is mostly a new
`rolegraph/diff/` module comparing two `AccessGraph`s plus a screen. Diff at the
level of resolved grants (identity, role, scope), not raw assignment rows;
"Dan Okafor gained Contributor on Production" is the useful sentence, not
"assignment ra-0042 appeared".

Why second: it is the next thing every customer in the brief asks for, it turns a
one-off audit into a recurring habit, and "privilege gained since last week" is a
far sharper demo than any static finding.

### 3. Correctness: deny assignments, PIM eligibility, effective actions

Third by sequence, not importance. The moment RoleGraph reports access that a
deny assignment blocks, trust is gone and hard to recover.

Sketch: add `denyAssignments` to the schema and a `DenyAssignment` entity, then
have `access_paths` (or a wrapper) mark paths as suppressed rather than removing
them — *"Jane has Contributor here, but a deny assignment blocks it"* is more
useful than silence. PIM eligibility is a new principal-to-role relationship with
an `eligible` flag that the findings must treat as a distinct, reportable state.

Do not start this until at least three real tenant exports have been imported.
You cannot prioritise these gaps sensibly without knowing which ones actually
appear in the wild.

## 9. Boundaries for an autonomous agent

From `docs/BRIEF.md`, and they apply to you:

- No public deployment, no cloud resources, no purchases, no paid APIs, no
  connections to a real Azure tenant, no production credentials, no package
  publishing, no DNS or account creation, and no irreversible changes without
  explicit approval from the repository owner.
- Flag any significant new dependency rather than adding it quietly. There is one
  vendored third-party file today — `rolegraph/web/static/htmx.min.js` (htmx
  2.0.4) — and it should stay that way.
- Never claim something works without running `pytest` **and** exercising the
  server. Several bugs during the initial build passed the unit tests and only
  surfaced on a real request.
- Report at milestones, not per file. Explain architectural choices as
  chosen / why / alternative / why not, in plain business language.
