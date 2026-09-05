# RoleGraph — project instructions for Claude Code

RoleGraph is a self-hosted Azure RBAC access intelligence platform.
Promise: "See who has access to what — and exactly why."
Read `docs/BRIEF.md` in full before doing anything. It is the product spec and it is authoritative.

## Architecture decisions (already made — do not re-litigate)

**Stack:** Python 3.12 + FastAPI, SQLite (via SQLAlchemy, so Postgres is a config swap later),
server-rendered Jinja2 templates + HTMX, single container, Docker Compose.
No JS build step. No Kubernetes, queues, microservices, or external AI/API dependencies.

**Why SQLite, not a graph DB:** the access graph is small (tens of thousands of edges).
The "graph" is relationship tables + a resolver that walks
identity → group membership (recursive) → role assignment → role definition → scope inheritance → resource.
A graph database adds an operational dependency without any query we need at this scale.

**Modular monolith layout:**
```
rolegraph/
  domain/      # entities, relationships, scope model (pure Python, no I/O)
  importer/    # schema validation, normalization, warnings — never silently drop bad records
  resolver/    # effective access + access-path computation (membership, inheritance)
  findings/    # deterministic rules; each rule = what/identity/role/scope/why/path
  web/         # FastAPI routes + templates (Overview, Identities, Access Path, Roles, Privileged, Import)
  storage/     # SQLAlchemy models, snapshot table (imports are snapshots from day one)
data/demo/     # synthetic Contoso tenant (JSON, matches import schema)
tests/         # pytest; fixtures include nested groups, wildcards, MG inheritance, malformed records
docs/          # BRIEF.md, IMPORT_SCHEMA.md, ARCHITECTURE.md
```

**Import schema:** one JSON document with top-level arrays: `tenants, managementGroups, subscriptions,
resourceGroups, resources, roleDefinitions, roleAssignments, users, groups, servicePrincipals,
managedIdentities, groupMemberships`. Document it in `docs/IMPORT_SCHEMA.md`. Every import creates a
snapshot row so history/diff can be added later without a migration.

**Privileged roles** are configurable (`config/privileged_roles.yaml`), defaulting to
Owner, Contributor, User Access Administrator.

## Working rules

- Security first: no secrets in repo, validate all uploads (size limit, JSON only, schema check),
  no outbound network calls from the app, config via environment variables.
- Every finding must be deterministic and explainable. No scoring, no AI.
- Prefer plain-English UI ("Jane can modify Production because she is a member of Platform-Admins…")
  over raw IDs; show raw IDs secondarily.
- Never claim something works without running the tests or the server.
- Report to the user only at milestones: stack scaffolded → data model + tests → importer + demo data →
  identity explorer + access path → findings → MVP ready for review.
- Ask a question only if the answer would materially change what gets built.

## Definition of done for the MVP
`docker compose up` → open browser → load Contoso demo → search "Jane" → see her production access,
whether direct or inherited, the full path through group and role assignment, at least one privilege
finding tied to that path, and the underlying role's permissions.
