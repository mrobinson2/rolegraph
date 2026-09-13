"""Produce a fresh synthetic drift report without Azure, credentials or network."""

from datetime import datetime, timezone
from pathlib import Path
import argparse

from rolegraph.drift.engine import compare
from rolegraph.drift.files import load_target, new_output_dir, write_json, write_report
from rolegraph.drift.schema import Observation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    target, metadata = load_target(Path(__file__).resolve().parents[1] / "rbac/examples/approved-state.json")
    grant = target.assignments[0]
    observed = Observation.model_validate({
        "schemaVersion": 1, "tenantId": target.tenantId, "collectedAt": datetime.now(timezone.utc).isoformat(),
        "scopeResults": [{"scope": target.scopes[0].model_dump(), "assignments": [{
            "id": grant.scope + "/providers/Microsoft.Authorization/roleAssignments/dddddddd-dddd-dddd-dddd-dddddddddddd",
            "principalId": grant.principalId, "scope": grant.scope,
            "roleDefinitionId": "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"}]}],
    })
    new_output_dir(args.output_dir)
    write_json(args.output_dir / "observed.json", observed.model_dump())
    report = compare(target, observed)
    report["target"] = metadata
    write_report(args.output_dir, report)
    print(f"Synthetic example: approved Reader is missing; unexpected Owner is present. Reports: {args.output_dir}")


if __name__ == "__main__":
    main()
