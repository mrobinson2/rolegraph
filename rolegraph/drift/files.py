"""Local file boundaries for the drift CLI. No cloud dependencies."""

from __future__ import annotations

import hashlib
import html
import json
import os
import tempfile
from pathlib import Path

from .schema import TargetState

MAX_BYTES = 25 * 1024 * 1024


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON property: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def parse_json(payload: bytes):
    if len(payload) > MAX_BYTES:
        raise ValueError("JSON exceeds the 25 MB limit.")
    try:
        return json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the supported depth.") from exc


def read_json(path: Path):
    with path.open("rb") as handle:
        return parse_json(handle.read(MAX_BYTES + 1))


def load_target(path: Path) -> tuple[TargetState, dict]:
    paths = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    if not paths:
        raise ValueError("Target directory contains no JSON files; an empty approval directory cannot pass.")
    return merge_targets([
        (str(source.relative_to(path)) if path.is_dir() else source.name, TargetState.model_validate(read_json(source)))
        for source in paths
    ])


def merge_targets(documents: list[tuple[str, TargetState]]) -> tuple[TargetState, dict]:
    if not documents:
        raise ValueError("At least one approved target file is required.")
    merged = {"schemaVersion": 1, "scopes": [], "assignments": [], "roleDefinitions": []}
    sources = []
    seen_scopes = {}
    for name, doc in sorted(documents, key=lambda item: item[0]):
        if "tenantId" in merged and merged["tenantId"] != doc.tenantId:
            raise ValueError("All target files in one scan must belong to the same tenant.")
        merged["tenantId"] = doc.tenantId
        for scope in doc.scopes:
            if scope.id in seen_scopes and seen_scopes[scope.id] != scope:
                raise ValueError("Conflicting includeDescendants settings across target files.")
            if scope.id not in seen_scopes:
                merged["scopes"].append(scope.model_dump())
                seen_scopes[scope.id] = scope
        merged["assignments"].extend(a.model_dump() for a in doc.assignments)
        merged["roleDefinitions"].extend(r.model_dump() for r in doc.roleDefinitions)
        sources.append(name)
    target = TargetState.model_validate(merged)
    canonical = json.dumps(target.model_dump(by_alias=True), sort_keys=True, separators=(",", ":")).encode()
    return target, {"files": sources, "sha256": hashlib.sha256(canonical).hexdigest()}


def new_output_dir(path: Path):
    # Every run gets its own directory. A failed run cannot leave an old green
    # report at the destination, or overwrite a previous scan's evidence.
    path.mkdir(parents=True, exist_ok=False, mode=0o700)


def write_json(path: Path, value):
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def atomic_write(path: Path, value: str):
    descriptor, temporary = tempfile.mkstemp(prefix=".rbac-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _cell(value) -> str:
    # Untrusted display names must not inject Markdown links or HTML into CI.
    value = html.escape(str(value)).replace("\n", " ").replace("\r", " ")
    for char in ("\\", "|", "`", "[", "]", "*", "_"):
        value = value.replace(char, "\\" + char)
    return value


def markdown(report: dict) -> str:
    lines = ["# Azure RBAC target state comparison", "", f"Status: **{_cell(report['status'])}**", ""]
    if report["status"] == "error":
        lines += ["The scan did not complete. No compliance conclusion is available.", "", _cell(report["error"]), ""]
    else:
        summary = report["summary"]
        lines += [f"Approved assignments: {summary['approvedAssignments']}. Observed assignments: {summary['observedAssignments']}.",
                  "", "| Difference | Identity | Role | Originating scope |", "|---|---|---|---|"]
        for finding in report["findings"]:
            values = (finding["kind"], finding.get("principalName") or finding.get("principalId", "—"),
                      finding.get("roleName") or finding["roleDefinitionId"], finding.get("scope", "Role definition"))
            lines.append("| " + " | ".join(_cell(v) for v in values) + " |")
        if not report["findings"]:
            lines += ["", "The observed assignments match the approved target within the declared coverage."]
        lines += ["", "Exact conditions, assignment IDs and role permission changes are in report.json.", ""]
    if report.get("target"):
        lines += [f"Target SHA-256: {_cell(report['target']['sha256'])}", ""]
    if report.get("revision"):
        lines += [f"Repository revision: {_cell(report['revision'])}", ""]
    lines += [_cell(v) for v in report.get("limitations", [])]
    return "\n".join(lines) + "\n"


def write_report(path: Path, report: dict):
    write_json(path / "report.json", report)
    atomic_write(path / "report.md", markdown(report))
