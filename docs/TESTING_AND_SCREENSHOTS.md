# Testing and screenshot review

Use synthetic data for screenshots. Real exports contain sensitive identities,
scope IDs and access paths; do not commit them or include them in public reports.

## Python and collector checks

From the repository root, with Python 3.12+ and the development environment ready:

```sh
.venv/bin/python -m pytest
```

Collector tests use PowerShell 7.4+ from `PATH`, or an explicit binary:

```sh
ROLEGRAPH_PWSH=/absolute/path/to/pwsh .venv/bin/python -m pytest
```

On Windows, use `.venv\Scripts\python.exe` and set `$env:ROLEGRAPH_PWSH` in
PowerShell. Without PowerShell, collector tests are **skipped**, not passed. A
collector release check must have no skips in `tests/test_collector.py`.

These tests do not need cloud credentials. `tests/fixtures/collector_driver.ps1`
replaces only Graph/CLI reads; the real script writes to pytest's temporary
directory, and the actual importer/resolver verifies the resulting access chains.
Tests cover dry-run, paging, nested and service-principal membership, optional
resources, ID normalization, narrow-scope role lookup, omissions, tenant mismatch,
read failures, pagination guards, hidden groups, overwrite and symlink safeguards.

Live acceptance is separate: collect an approved test tenant using
[COLLECTION.md](COLLECTION.md), compare known assignments and nested paths with
Azure, and confirm intentionally unavailable reads fail without replacing an
existing export. Do not run live collection against production as a casual test.

## Automated browser journey and screenshots

Playwright is optional **development tooling**, not an application dependency.
Install it in an ignored local tools directory; an installed Google Chrome is
used by default:

```sh
npm install --prefix .tools/browser --no-audit --no-fund playwright@1.58.2
node scripts/capture_screenshots.mjs
```

If Chrome is unavailable, install Playwright's Chromium and select its channel:

```sh
./.tools/browser/node_modules/.bin/playwright install chromium
ROLEGRAPH_BROWSER_CHANNEL=chromium node scripts/capture_screenshots.mjs
```

Tool downloads require network access; the app under test does not. Supported
overrides are `ROLEGRAPH_BROWSER_MODULES` (directory containing `node_modules`),
`ROLEGRAPH_BROWSER_CHANNEL`, and `ROLEGRAPH_PYTHON` (Python executable).
The first positional argument changes the screenshot output directory:

```sh
node scripts/capture_screenshots.mjs artifacts/screenshots
```

The runner starts its **own** loopback server on a dynamically allocated port,
uses a disposable SQLite database and the checked-in Contoso demo, and shuts down
its server/browser and removes that database when done. It does not connect to
or delete an existing RoleGraph database. Images with the same names in the
chosen output directory are replaced on reruns.

On success it creates `artifacts/screenshots/index.html` (clickable gallery),
`manifest.json` (browser version, routes, sizes, timestamp and checks), and 27 PNGs.
Artifacts and tools are gitignored. A nonzero exit is a failed acceptance run;
partial images do not establish success. The runner invalidates an old manifest
before replacing images, then marks it passed only after all checks complete.

The journey exercises first-run demo import, overview, live identity and role
search/count updates, Jane's inherited access path, high findings, role detail,
scope tree, privileged access, empty results and malformed-file rejection.
Checks include preservation of the active snapshot after a rejected upload,
skip-link keyboard navigation, readable diagram link overlays, no clipped header
navigation, and no page-wide horizontal overflow. External browser requests are
blocked and fail the run; JavaScript errors, console errors and server 5xx fail it.

## Visual review checklist

| Viewport | What to inspect |
|---|---|
| Desktop, 1440 × 1000 | Names and explanations lead; badges, tables and cards align; group → role → scope diagram labels remain visible. |
| Phone, 390 × 844 | Navigation wraps inside its header; search fits; wide tables and diagrams scroll inside their containers, not the entire page; metadata wraps. |
| Tablet, 768 × 1024 | Identity page columns collapse cleanly; stat cards and relationship diagram remain usable. |

Open several full-size images, not just gallery thumbnails. Tab through navigation,
filters, table links and the relationship diagram. Check focus outlines and the
visible upload label. On a phone, swipe a wide table/diagram and confirm the page
itself stays in place. Also test 200% zoom, unusually long names/IDs, and a real
mobile browser before production release. Automated layout checks are not a full
accessibility audit; screenshots alone do not verify screen-reader announcements.

The current polish fixes mobile header/page overflow, opaque SVG link overlays,
stale HTMX result counts, insufficiently visible focus, and overly faint metadata.
No new frontend framework, CDN, build step or application network client is added.
