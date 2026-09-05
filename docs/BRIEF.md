# RoleGraph — Product Brief

RoleGraph is a self-hosted Azure RBAC access intelligence platform for cloud platform teams, identity teams, security engineers, enterprise architects, and Azure administrators.

## The problem
Azure RBAC becomes extremely difficult to understand as environments grow. Permissions can come from built-in roles, custom roles, direct assignments, Entra ID group membership, nested groups, service principals, managed identities, management-group / subscription / resource-group / resource assignments, inheritance, and multiple overlapping assignments.

Organizations struggle to answer: What can this user actually do? Why? Where did it originate? At what scope? Is it inherited? Which resources can this identity affect? Who has Owner / Contributor / User Access Administrator? Which custom roles are overly broad? Are there redundant assignments? What changed since last scan? Does actual RBAC differ from IaC? What access path connects an identity to a permission on a resource?

## Core concept
Model RBAC as an access graph:
Identity → Group Membership → Role Assignment → Role Definition → Permission → Scope → Azure Resource

Primary promise: **"See who has access to what — and exactly why."**
Secondary: "Self-hosted access intelligence for Azure. Your identity and RBAC data never has to leave your environment."

## Constraint: self-hosted, local-first
Sensitive data plane runs entirely inside the customer's environment. V1 runs on a workstation or corporate network via Docker Compose (`docker compose up`, then open a browser). Must be able to evolve later to Kubernetes/AKS/Helm, private registries, restricted networks, air-gapped — design for it, don't build it now.

Security principle: no RoleGraph cloud service required for core functionality. A future control plane may hold only licensing, update metadata, config templates, docs, optional anonymous telemetry — never identities, assignments, resources, or permission graphs.

## MVP goal
Smallest useful version that proves the idea. Not a full IGA product; not competing with SailPoint / Entra ID Governance / Saviynt / CyberArk.

Core journey: start locally → import RBAC data → normalize into graph model → browse identities/roles/assignments/permissions/scopes/resources → search identity → see access → see why → follow path through roles and scopes → identify privileged or suspicious assignments.

## MVP data collection
Offline import first. No credentials, Graph permissions, or tenant config in the prototype. Define a simple, documented import schema (eventually produced by a PowerShell / Azure CLI collection script). Sources: subscriptions, management groups, resource groups, resources (if practical), role definitions (built-in + custom), role assignments, users, groups, service principals, managed identities, group memberships, scope info. Provide realistic synthetic demo data so the app works without a real tenant.

Future connected mode: design so a least-privilege in-environment collector can query ARM / Microsoft Graph / CLI / PowerShell / managed identity / service principal later. Do not require credentials without explicit approval.

## Domain model
Identities: user, group, service principal, managed identity.
Hierarchy: tenant, management group, subscription, resource group, resource.
RBAC: role definition (built-in / custom), role assignment, scope, inheritance, Actions, NotActions, DataActions, NotDataActions.
Relationships: USER MEMBER_OF GROUP, GROUP MEMBER_OF GROUP, IDENTITY ASSIGNED ROLE, ROLE CONTAINS PERMISSION, ROLE ASSIGNED_AT SCOPE, RESOURCE CHILD_OF SCOPE, ACCESS INHERITED_FROM SCOPE.
A graph database is not required. Choose the simplest maintainable persistence and explain the choice.

## MVP screens
1. **Overview** — identities, assignments, custom roles, privileged assignments, subscriptions, management groups, last import; a few risk indicators.
2. **Identity explorer** — search/browse users, groups, SPs, MIs. Selecting one shows roles, scope, direct vs inherited, group relationships, resulting access. Strongest part of the MVP.
3. **Access path** — e.g. Jane Smith → Member of Azure-Platform-Admins → Assigned Contributor → At Production MG → Inherited by Subscription A → Provides Microsoft.Compute/*.
4. **Role explorer** — built-in and custom roles, permissions, identities assigned, scopes. Custom roles easy to inspect.
5. **Privileged access view** — Owner, Contributor, User Access Administrator by default; extensible config. Label as findings/observations, not vulnerabilities.
6. **Import** — upload/load dataset; show success/failure, entities and assignments discovered, warnings, malformed records. Never silently discard bad data.

Graph visualization: focused relationship graph for a selected identity only. Complements tables and search; must not consume the MVP.

## Findings engine
Small, deterministic, explainable rules: direct Owner; direct Contributor; UAA assignment; privileged role at MG scope; privileged role at subscription scope; custom role with wildcard permissions; same role via multiple paths; unusually broad scope. Each finding shows what was detected, identity, role, scope, why it matters, supporting path. No AI scoring.

## Future (design for, don't build unless cheap)
- Snapshot history and diff (added/removed assignments, role changes, privilege gained, custom role broadened).
- IaC drift: declared state (Terraform/Bicep/ARM) vs actual Azure state.
- Editions: Community (free, manual import, browsing, basic viz/search) · Professional (continuous collection, scheduling, history, comparison, advanced findings, reporting, export, IaC) · Enterprise (multi-tenant, Entra SSO, in-app RBAC, audit logging, custom rules, API, SIEM, private registry, offline licensing, air-gapped, signed releases, support). No licensing code in the MVP; keep the code organized so it can be added cleanly.

## Technology principles
Solo technical founder, not a full-time developer; product must be operable and extendable via AI-assisted development. Boring, well-supported tech. Modular monolith. Optimize for simplicity, low cost, maintainability, local dev experience, simple deployment, minimal dependencies, clear separation of concerns, enterprise security, prototype-to-product path. No Kubernetes, microservices, queues, cloud deps, premature scale work, complex event architectures, or proprietary services in the MVP. Explain tradeoffs in plain business language.

AI: not required; core analysis deterministic; no model API dependency for the core product.

## Security requirements
No secrets in source control; no hard-coded credentials; validate imports; protect against malicious uploads; avoid and document outbound calls; secure defaults; config separate from code; prepare for Entra ID auth; log admin actions; avoid storing credentials. Sensitive data never transmitted externally without explicit configuration and approval.

## Testing
Automated tests for: role assignment parsing, scope hierarchy, permission relationships, inheritance, group membership, findings rules, import validation. Synthetic fixtures with nested groups, multiple assignments, duplicates, MG inheritance, subscription assignments, custom roles, wildcards, malformed records, missing referenced entities. Never claim functionality works without testing or verifying.

## Demo dataset — Contoso
Tenant → Production MG (Payments-Prod, Customer-Prod) and NonProduction MG (Development). Identities: cloud admins, developers, security engineers, SPs, MIs, Entra groups. Intentional issues: developer indirectly gets Contributor to production; SP has Owner at subscription; user inherits privilege via nested groups; overly broad custom role; duplicate assignment paths.

## UX principle
Prefer "Jane can modify resources in Production because she belongs to Platform-Admins, which has Contributor assigned at the Production management group" over raw IDs. Expose raw identifiers secondarily.

## Success criteria
Start locally → load Contoso → search "Jane" → open identity → see production access → direct vs inherited → follow path through groups and assignments → understand why → see at least one meaningful privilege finding → inspect role and permissions.

## Boundaries
No public deployment, cloud resources, purchases, paid APIs, Azure connections, production credentials, package publishing, external pushes, DNS, account creation, or irreversible changes without explicit approval. Flag significant dependencies.

## Working style
Inspect workspace first. Ask only questions that materially change the build. Explain major architecture choices briefly (chosen / why / alternative / why not) then proceed. Report only at milestones.

## When finished, deliver
1. What you built (plain English). 2. How to run and test it (exact steps). 3. Important decisions and tradeoffs. 4. How you verified it (tests, manual checks, scenarios, edge cases). 5. What is incomplete or uncertain. 6. Three best next improvements, prioritized by customer value, and which to build first and why.
