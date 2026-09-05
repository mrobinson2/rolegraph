# RoleGraph

**See who has access to what — and exactly why.**

RoleGraph is a self-hosted Azure RBAC access intelligence platform. It reads an
offline export of your Azure identities, roles, assignments and scopes, resolves
the whole access graph, and shows you the path from a person to a permission on a
resource — through nested groups, role definitions and scope inheritance.

Your identity and RBAC data never has to leave your environment. The application
needs no credentials, makes no outbound network calls, and requires no cloud
service to work.

---

## Run it

```bash
docker compose up
```

Then open <http://localhost:8000> and click **Load Contoso demo** — a synthetic
tenant with deliberately imperfect RBAC, so you can see what the product does
before pointing it at anything real.

If port 8000 is taken:

```bash
ROLEGRAPH_PORT=8010 docker compose up
```

### Run it without Docker

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn rolegraph.web.app:app --reload --port 8000
```

### Run the tests

```bash
.venv/bin/pytest
```

---

## The five-minute tour

1. **Import** → *Load Contoso demo*. You get a summary of what was discovered and
   every warning raised. Nothing is ever discarded silently.
2. **Overview** shows the estate and the findings worth looking at first.
3. **Identities** → search `Jane`. Open her.
4. Her page shows what she can reach, whether each grant is direct or arrives
   through a group, and one plain-English sentence per grant:
   *"Jane Smith has Contributor on Production because they are a member of
   Azure-Platform-Admins, which has Contributor assigned at the Production
   management group."*
5. Click **Access path** on that row. You get the full chain — identity → group →
   role → scope — the inheritance trail down to each subscription, and the exact
   actions the role permits.
6. **Findings** explains the problems the demo tenant contains, each one linked
   back to the access path that proves it.

---

## Screens

| Screen | What it answers |
|---|---|
| **Overview** | How big is the estate, and what should I look at first? |
| **Identity explorer** | What can this user, group, service principal or managed identity actually do? |
| **Access path** | Where did this permission come from, and how far down does it reach? |
| **Role explorer** | What does this role really permit, and who holds it? |
| **Scopes** | Who can reach this subscription or resource, and is it inherited? |
| **Privileged access** | Who holds Owner, Contributor or User Access Administrator? |
| **Findings** | What is worth reviewing, and why? |
| **Import** | Load a dataset, review warnings, switch between snapshots. |

---

## Getting your own data in

RoleGraph imports one JSON document with named top-level arrays: tenants,
management groups, subscriptions, resource groups, resources, role definitions,
role assignments, users, groups, service principals, managed identities and group
memberships.

The format is documented in **[docs/IMPORT_SCHEMA.md](docs/IMPORT_SCHEMA.md)**,
and `data/demo/contoso.json` is a complete worked example. A collection script
that produces it from Azure CLI or PowerShell is the intended next step; the
schema was designed so that script can be written without changing anything here.

Every import creates a snapshot, so you can load a second dataset and switch back
without losing the first.

---

## Findings

Fourteen deterministic rules, each of which names what was detected, the identity,
role and scope involved, why it matters, and the access path that supports it.

| Rule | What it looks for |
|---|---|
| `direct-owner` | Owner assigned straight to an identity |
| `direct-contributor` | Contributor assigned straight to an identity |
| `user-access-administrator` | Anyone who can grant roles to anyone |
| `privileged-at-management-group` | Privileged role inherited by a whole branch |
| `privileged-at-subscription` | Privileged role across an entire subscription |
| `privileged-via-group` | Privileged access that arrives only through membership |
| `custom-role-wildcard` | Custom roles granting `Namespace/*` |
| `custom-role-can-assign-roles` | A custom role that is a path to Owner |
| `write-role-at-broad-scope` | A harmless-looking role that can change most of the estate |
| `multiple-paths-to-same-access` | Revoking one route would not remove the access |
| `duplicate-assignment` | The same role assigned twice at the same scope |
| `redundant-narrow-assignment` | A grant a broader one already covers |
| `unused-custom-role` | Custom roles defined but never assigned |
| `service-principal-privileged` | Non-human identities holding privileged roles |

There is no scoring and no AI. `severity` is a fixed label on the rule, not a
computed number, and findings are observations for review rather than
vulnerabilities.

Which roles count as privileged is configurable in
`config/privileged_roles.yaml` — edit it if your organisation treats Contributor
as a baseline.

---

## Configuration

Everything is an environment variable; nothing is hard-coded.

| Variable | Default | Purpose |
|---|---|---|
| `ROLEGRAPH_DATABASE_URL` | `sqlite:///data/rolegraph.db` | Any SQLAlchemy URL; PostgreSQL is a string change |
| `ROLEGRAPH_DATA_DIR` | `./data` | Where the database lives |
| `ROLEGRAPH_DEMO_DATASET` | `data/demo/contoso.json` | The demo tenant |
| `ROLEGRAPH_PRIVILEGED_ROLES_FILE` | `config/privileged_roles.yaml` | Privileged role list |
| `ROLEGRAPH_MAX_UPLOAD_BYTES` | `26214400` (25 MB) | Upload size limit |
| `ROLEGRAPH_PORT` | `8000` | Host port used by docker compose |

---

## Security

- **No credentials.** The MVP reads offline exports. Nothing connects to Azure.
- **No outbound calls.** Enforced by a Content Security Policy and by the absence
  of any HTTP client in the application path. HTMX is vendored locally, so the
  browser fetches nothing from a CDN either.
- **Uploads are validated** before anything is stored: extension, content type,
  size, UTF-8 decode, JSON parse, then schema validation.
- **Config is separate from code.** No secrets in source control.
- **Administrative actions are logged** to an audit table and shown on the Import
  screen.
- **The container** runs as a non-root user, read-only, with all Linux
  capabilities dropped, bound to `127.0.0.1`.

Authentication is not implemented. Run RoleGraph on a workstation or an
access-controlled network segment. Entra ID sign-in is the intended next step and
the app is structured for it.

---

## How it works

Azure RBAC modelled as an access graph:

```
Identity → Group membership → Role assignment → Role definition
        → Permission → Scope → Azure resource
```

Python 3.12, FastAPI, SQLite via SQLAlchemy, server-rendered Jinja2 with HTMX.
One container. No build step, no queues, no Kubernetes, no cloud dependencies.

The design decisions and their trade-offs are in
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

---

## Project layout

```
rolegraph/domain/     entities, scope model, hierarchy - pure Python
rolegraph/importer/   schema validation and normalization
rolegraph/resolver/   group membership and effective access
rolegraph/findings/   deterministic rules
rolegraph/storage/    SQLAlchemy models and snapshots
rolegraph/web/        routes, view models, templates
data/demo/            the synthetic Contoso tenant
scripts/              demo dataset generator
tests/                pytest suite
docs/                 BRIEF, IMPORT_SCHEMA, ARCHITECTURE
```
