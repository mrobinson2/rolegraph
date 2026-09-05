Paste this as your first message in Claude Code, from inside the empty repo:

---
Read CLAUDE.md and docs/BRIEF.md. The workspace is otherwise empty. The stack and module layout are already decided in CLAUDE.md — do not propose alternatives. Start by scaffolding the project, the domain model, and the test suite for scope hierarchy, group membership resolution, and inheritance, using synthetic fixtures. Then build the importer and the Contoso demo dataset (with the intentional RBAC issues listed in the brief). Then the web UI in this order: identity explorer, access path, roles, findings, overview, import. Run the tests and the server before each milestone report. When the MVP definition of done in CLAUDE.md passes end to end, stop and give me the six-part summary requested at the end of the brief.
---
