"""python -m rolegraph.drift: validate repository targets or compare offline files."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from .engine import LIMITATIONS, compare
from .files import load_target, new_output_dir, read_json, write_report
from .schema import Observation


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate one target JSON file or a directory of files.")
    validate.add_argument("--target", type=Path, required=True)
    check = commands.add_parser("check", help="Compare a complete observation without connecting to Azure.")
    check.add_argument("--target", type=Path, required=True)
    check.add_argument("--observed", type=Path, required=True)
    check.add_argument("--output-dir", type=Path, required=True, help="New directory for this run; existing paths are refused.")
    check.add_argument("--revision", default="")
    check.add_argument("--max-age-hours", type=float, default=36)
    args = parser.parse_args(argv)
    if args.command == "check":
        try:
            new_output_dir(args.output_dir)
        except OSError as exc:
            print(f"Cannot create a fresh report directory: {exc}")
            return 2
    metadata = None
    try:
        target, metadata = load_target(args.target)
        if args.command == "validate":
            print(f"Valid target: {len(metadata['files'])} files, {len(target.scopes)} scopes, {len(target.assignments)} assignments.")
            return 0
        if not math.isfinite(args.max_age_hours) or args.max_age_hours <= 0:
            raise ValueError("max-age-hours must be a positive finite number.")
        report = compare(target, Observation.model_validate(read_json(args.observed)), max_age_hours=args.max_age_hours)
    except (OSError, ValueError) as exc:
        if args.command == "validate":
            print(f"Invalid target: {exc}")
            return 2
        report = {"schemaVersion": 1, "status": "error", "error": str(exc), "limitations": LIMITATIONS}
    report.update(target=metadata, revision=args.revision)
    try:
        write_report(args.output_dir, report)
    except OSError as exc:
        print(f"Cannot write scan evidence: {exc}")
        return 2
    print(f"RBAC comparison: {report['status']}. Reports: {args.output_dir}")
    return {"in-sync": 0, "drift": 1, "error": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
