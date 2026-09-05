# Agent instructions for the RoleGraph repository

Read **`docs/HANDOFF.md` first** — it is the engineering handoff: code map,
invariants, traps, and the prioritised next three pieces of work.
Then `docs/BRIEF.md` (the product spec, authoritative) and
`docs/ARCHITECTURE.md` (why the code is shaped this way).

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                                    # must stay green
.venv/bin/uvicorn rolegraph.web.app:app --port 8000
```

`ROLEGRAPH_PORT=8010 docker compose up` for the container path.
`.venv/bin/python scripts/generate_contoso_demo.py` after changing the demo
tenant — never hand-edit `data/demo/contoso.json`.

## The stack is decided — do not propose alternatives

Python 3.12, FastAPI, SQLite via SQLAlchemy, server-rendered Jinja2 with HTMX,
single container, no build step. No Kubernetes, queues, microservices, client
frameworks, external AI or cloud dependencies. The reasoning is in
`docs/ARCHITECTURE.md`.

## Invariants that must survive every change

1. No outbound network calls from the application. No credentials anywhere.
2. Findings stay deterministic — fixed severity labels, no scoring, no models.
3. No import record is ever discarded silently; every skip emits a warning.
4. `rolegraph/domain/` performs no I/O.
5. Every recursive walk stays cycle-safe and depth-capped.
6. Plain English leads; raw ids and scope strings are secondary.

## Working rules

- Run `pytest` **and** exercise the running server before claiming anything works.
- Add tests in the same change as the behaviour. `tests/factories.py::GraphBuilder`
  builds synthetic graphs; name tests as sentences describing the behaviour.
- Put logic in `rolegraph/web/viewmodels.py`, not in templates.
- No public deployment, cloud resources, purchases, paid APIs, real Azure
  connections, package publishing or irreversible changes without explicit
  approval. Flag any new dependency instead of adding it quietly.
- Report at milestones with architectural choices explained as
  chosen / why / alternative / why not.
