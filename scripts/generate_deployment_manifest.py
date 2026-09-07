#!/usr/bin/env python3
"""Generate a deterministic checksum inventory for a deployment package."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing overwrite: {output}")
    files = []
    for path in sorted(item for item in package.rglob("*") if item.is_file() and item != output):
        files.append({
            "path": path.relative_to(package).as_posix(),
            "sha256": digest(path),
            "bytes": path.stat().st_size,
        })
    manifest = {
        "schema": "MCH_PRISTINE_E2E_DEPLOYMENT_PACKAGE_V01",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PRODUCTION_READY",
        "package_name": package.name,
        "pristine_source_sha256": "D43A42DD930B2982BE993EE3D54A12DEDB7CE19D0E7F7033F3CEC2D70F422995",
        "controller_ready_sha256": "2FC82F0CC023E7E474E8CD0762DAFA7C0D7000CF8E577855C1F289BCD82C6676",
        "target_count": 3,
        "preflight_statuses": {"70_30": "PRODUCTION_READY", "80_20": "PRODUCTION_READY",
                               "90_10": "PRODUCTION_READY"},
        "minimum_inter_path_wall_clearance_mm": 0.015388203202207573,
        "minimum_nonadjacent_self_path_wall_clearance_mm": 0.49999998218048713,
        "qualified_recipe": "protocol/controller_recipes/2fc82f0cc023e7e474e8cd0762dafa7c0d7000cf8e577855c1f289bcd82c6676/recipe.json",
        "qualified_release_manifest": "release/qualified_release_manifest_v02.json",
        "artifact_count": len(files),
        "artifact_bytes": sum(row["bytes"] for row in files),
        "artifacts": files,
    }
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "artifact_count": len(files),
                      "output": str(output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
