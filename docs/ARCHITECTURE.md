# RoleGraph architecture

## The shape of the problem

Azure RBAC is a graph, but a small one. A large tenant has tens of thousands of
edges, not millions. The hard part is not scale — it is that access arrives
through several mechanisms at once (direct assignment, group membership, nested
group membership, scope inheritance) and no single Azure screen shows them
together.

So the architecture optimises for explanation, not throughput.

## Modular monolith

```
rolegraph/
  config.py     environment + YAML configuration, no secrets in code
  domain/       entities, scope model, hierarchy walks - pure Python, no I/O
  importer/     schema validation and normalization; never drops a record silently
  resolver/     group membership and effective access; produces AccessPath objects
  findings/     deterministic rules over the resolved graph
  storage/      SQLAlchemy models, snapshots, and graph <-> database translation
  web/          FastAPI routes, view models, Jinja2 templates, HTMX partials
```

Dependencies point one way: `web` → `findings`/`resolver`/`storage` → `importer`
→ `domain`. `domain` imports nothing from the rest of the application, which is
why the domain and resolver test suites need no database and no HTTP client.

## Persistence: SQLite, not a graph database

**Chosen:** SQLite via SQLAlchemy, with the graph rebuilt in memory per snapshot.

**Why:** the "graph" queries RoleGraph needs are a recursive walk up a scope tree
and a recursive walk up group membership. Both are a few hundred dictionary
lookups on data that fits comfortably in memory. Loading a snapshot and
resolving every identity's access on the Contoso dataset takes single-digit
milliseconds.

**Alternative considered:** Neo4j or a similar graph database.

**Why not:** it adds a second service to run, back up, upgrade and secure, in
exchange for query power this workload does not need. For a product whose
promise is "runs on your workstation with `docker compose up`", the operational
cost is the wrong trade. SQLAlchemy means moving to PostgreSQL later is a
connection-string change, and the relationship tables already carry a
`snapshot_id`, so history and diffing can be added without a migration.

## Snapshots from day one

Every import writes a `snapshots` row and stamps every entity with its id. The
active snapshot is what the UI reads. This costs almost nothing now and is what
makes snapshot history, change detection and IaC drift comparison additive
features rather than a rewrite.

Loaded graphs are cached by snapshot id. Snapshots are immutable once written,
so the cache needs no invalidation beyond deletion.

## Resolution model

An `AccessPath` is the unit of explanation. It carries the identity, the chain
of groups that leads to the assignment (empty for a direct assignment), the
assignment, the role definition, and the scope the assignment was made at.
Everything the UI shows is derived from these:

- The identity explorer groups paths by (role, scope) to show distinct grants.
- The access path screen renders one path as ordered steps.
- Inheritance is `assignment scope == target scope, or an ancestor of it`.
- The findings engine runs pure functions over the same paths.

Membership resolution enumerates *all* distinct nesting paths rather than a
single shortest one, because "the same access by two routes" is itself a finding
and because deleting one route does not remove the access.

Every recursive walk is cycle-safe and depth-capped. Real tenants contain
accidental group cycles, and a page render must not hang on one.

## Findings

Rules are registered functions of `(RuleContext) -> Iterable[Finding]`. Each
finding states what was detected, the identity, role and scope, why it matters,
and the supporting path. Output is sorted, so two runs over the same snapshot
produce byte-identical results.

`severity` is a fixed label attached to the rule, not a computed score. There is
no model, no likelihood estimate and no risk number. Findings are observations
for review; whether one is acceptable is the operator's decision.

The privileged role list lives in `config/privileged_roles.yaml`, so an
organisation that treats Contributor as a baseline can say so without touching
code.

## Web layer

Server-rendered Jinja2 with HTMX for partial updates (search-as-you-type on the
identity and role lists). No client framework, no bundler, no build step —
`docker compose up` is the whole toolchain. HTMX is vendored into
`rolegraph/web/static/`, so the browser fetches nothing from a CDN.

View models in `web/viewmodels.py` do the shaping; templates stay declarative.

The focused relationship diagram is server-generated inline SVG with a
deterministic four-column layout. It complements the tables for one selected
identity; it is deliberately not a whole-tenant graph explorer.

## Security posture

- No credentials anywhere. The MVP imports offline exports only.
- No outbound network calls from the application, enforced in the Content
  Security Policy and in what the code does not import.
- Uploads are validated before storage: extension, content type, size limit,
  UTF-8 decode, JSON parse, then schema validation.
- Configuration comes from environment variables and a YAML file, never code.
- Administrative actions (import, snapshot activation, deletion) are recorded in
  an audit table.
- The container runs as a non-root user, read-only, with all capabilities
  dropped, and binds to localhost by default.

## What was deliberately left out

Authentication, multi-tenancy, licensing, background collection, scheduling and
snapshot diffing are all absent. The seams are in place — snapshots exist, config
is external, storage is abstracted — but building them now would slow down the
one thing the MVP has to prove: that the explanation is good enough to be worth
paying for.
