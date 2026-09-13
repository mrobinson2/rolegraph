"""Read Azure RBAC through an existing CLI session, then compare approved JSON.

This is the standalone connected boundary. It is never imported by the web app.
No login, role writes, Graph reads or subscription context changes are performed.
"""

from __future__ import annotations

import argparse
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from rolegraph.drift.engine import LIMITATIONS, compare
from rolegraph.drift.files import load_target, new_output_dir, parse_json, write_json, write_report
from rolegraph.drift.schema import Observation, ObservedAssignment, PinnedRole, ScopeResult, TargetState

ARM = "https://management.azure.com"
API_VERSION = "2022-04-01"


class CollectionError(ValueError):
    pass


def azure_read(arguments: list[str]):
    for attempt in range(3):
        try:
            result = subprocess.run(["az", *arguments, "--only-show-errors", "--output", "json"],
                                    capture_output=True, timeout=90)
        except subprocess.TimeoutExpired as exc:
            raise CollectionError("Azure read timed out; no complete observation was produced.") from exc
        if result.returncode == 0:
            return parse_json(result.stdout)
        error = result.stderr.decode("utf-8", errors="replace").lower()
        if attempt < 2 and any(code in error for code in ("429", "toomanyrequests", "503", "502", "504")):
            time.sleep(2 ** attempt)
            continue
        # Never echo raw CLI error payloads or authentication context to CI logs.
        raise CollectionError("Azure read failed. Verify the CLI session, configured tenant, scope access and service availability.")


def validate_url(url: str):
    if not isinstance(url, str):
        raise CollectionError("Azure returned an invalid pagination URL.")
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc != "management.azure.com" or parsed.fragment
            or not parsed.path.startswith("/") or "%" in parsed.path or "\\" in parsed.path
            or any(p in (".", "..") for p in parsed.path.split("/"))):
        raise CollectionError("Azure returned an unexpected ARM URL; collection stopped.")


def pages(url: str, read=azure_read) -> list[dict]:
    seen = set()
    values = []
    original_path = urlparse(url).path.lower()
    while url:
        validate_url(url)
        if urlparse(url).path.lower() != original_path:
            raise CollectionError("Azure pagination changed the requested resource path.")
        if url in seen or len(seen) >= 10000:
            raise CollectionError("Azure pagination repeated or exceeded 10,000 pages.")
        seen.add(url)
        response = read(["rest", "--method", "get", "--url", url])
        if not isinstance(response, dict) or not isinstance(response.get("value"), list):
            raise CollectionError("Azure returned an invalid assignment collection.")
        if any(not isinstance(v, dict) for v in response["value"]):
            raise CollectionError("Azure returned an invalid assignment record.")
        values.extend(response["value"])
        url = response.get("nextLink")
        if url is not None and (not isinstance(url, str) or not url):
            raise CollectionError("Azure returned an invalid nextLink.")
    return values


def observed_assignment(raw: dict) -> ObservedAssignment:
    props = raw.get("properties")
    if not isinstance(props, dict):
        raise CollectionError("Azure assignment is missing properties.")
    fields = {name: props[name] for name in ObservedAssignment.model_fields if name in props and name != "id"}
    fields["id"] = raw.get("id")
    return ObservedAssignment.model_validate(fields)


def collect(target: TargetState, read=azure_read) -> Observation:
    account = read(["account", "show"])
    if (not isinstance(account, dict) or str(account.get("tenantId", "")).lower() != target.tenantId
            or account.get("environmentName") != "AzureCloud"):
        raise CollectionError("Azure session must match the target tenant and use public Azure.")
    # Explicit subscriptions prevent an empty/stale CLI account cache from
    # silently reducing coverage, and check the tenant of every subscription.
    paths = [s.id for s in target.scopes] + [r.id for r in target.roleDefinitions]
    subscriptions = sorted({path.split("/")[2] for path in paths if path.startswith("/subscriptions/")})
    for subscription in subscriptions:
        account = read(["account", "show", "--subscription", subscription])
        if (not isinstance(account, dict) or str(account.get("tenantId", "")).lower() != target.tenantId
                or str(account.get("id", "")).lower() != subscription or account.get("state") != "Enabled"
                or account.get("environmentName") != "AzureCloud"):
            raise CollectionError("A configured subscription is unavailable, disabled, in another tenant or in another cloud.")
    results = []
    for scope in target.scopes:
        base = f"{ARM}{scope.id.rstrip('/')}/providers/Microsoft.Authorization/roleAssignments?api-version={API_VERSION}"
        # atScope explicitly includes ancestors; the unfiltered query includes
        # descendants. Overlapping returned IDs are deduplicated by the engine.
        urls = ([base] if scope.includeDescendants else []) + [base + "&$filter=atScope()"]
        assignments = [observed_assignment(raw) for url in urls for raw in pages(url, read)]
        results.append(ScopeResult(scope=scope, assignments=assignments))
    roles = []
    for pinned in target.roleDefinitions:
        url = f"{ARM}{pinned.id}?api-version={API_VERSION}"
        validate_url(url)
        raw = read(["rest", "--method", "get", "--url", url])
        if not isinstance(raw, dict) or not isinstance(raw.get("properties"), dict):
            raise CollectionError("Azure returned an invalid pinned role definition.")
        role = PinnedRole.model_validate({"id": raw.get("id"), "permissions": raw["properties"].get("permissions")})
        if role.key != pinned.key:
            raise CollectionError("Azure returned a different role definition than requested.")
        roles.append(role)
    return Observation(schemaVersion=1, tenantId=target.tenantId, collectedAt=datetime.now(timezone.utc).isoformat(),
                       scopeResults=results, roleDefinitions=roles)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="New report directory; existing paths are refused.")
    parser.add_argument("--revision", default="")
    args = parser.parse_args(argv)
    try:
        new_output_dir(args.output_dir)
    except OSError as exc:
        print(f"Cannot create a fresh report directory: {exc}")
        return 2
    metadata = None
    try:
        target, metadata = load_target(args.target)
        observation = collect(target)
        report = compare(target, observation)
        write_json(args.output_dir / "observed.json", observation.model_dump())
    except (OSError, ValueError) as exc:
        report = {"schemaVersion": 1, "status": "error", "error": str(exc), "limitations": LIMITATIONS}
    report.update(target=metadata, revision=args.revision)
    try:
        write_report(args.output_dir, report)
    except OSError as exc:
        print(f"Cannot write scan evidence: {exc}")
        return 2
    print(f"RBAC scan: {report['status']}. Reports: {args.output_dir}")
    return {"in-sync": 0, "drift": 1, "error": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
