#!/usr/bin/env python3
"""Bind a passed corrected-clearance report to files inside a release package."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Refusing overwrite: {args.output}")
    report = json.loads(args.source.read_text(encoding="utf-8-sig"))
    if report.get("status") != "PASS_ALL_RELEASE_TARGET_CLEARANCE" or report.get("pass_count") != 3:
        raise SystemExit("Source corrected-clearance report is not a 3/3 pass")
    report["source_manifest"] = str(args.source_manifest.resolve())
    report["source_manifest_sha256"] = digest(args.source_manifest)
    report["contract"] = str(args.contract.resolve())
    report["contract_sha256"] = digest(args.contract)
    for index, row in enumerate(report["targets"], 1):
        match = re.search(r"(70_30|80_20|90_10)", row["target"])
        tag = match.group(1) if match else f"state_{index:02d}"
        target = args.outputs / f"optimized_rigid_ubend_{tag}_v01.ipt"
        if not target.is_file():
            raise SystemExit(f"Package target is missing: {target}")
        actual = digest(target)
        if actual != row.get("target_workcopy_sha256", "").upper():
            raise SystemExit(f"Package target checksum mismatch: {target}")
        row["target_workcopy"] = str(target.resolve())
        row["target_workcopy_sha256"] = actual
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
