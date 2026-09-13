# Approved target state

Place reviewed target JSON files here in your **private** deployment repository.
Each file describes one tenant's scopes and approved assignments. A scan merges
all JSON files recursively and requires a single tenant; use separate pipeline
runs and directories for separate tenants.

Start from [the synthetic example](../examples/approved-state.json), replace all
tenant, subscription and principal IDs, and review every desired grant before
merging. Keep examples out of this directory. No production baseline is shipped.

Validate with `python -m rolegraph.drift validate --target rbac/approved`.
An empty directory is an error for a scan. See [the setup guide](../../docs/RBAC_TARGET_STATE.md).
