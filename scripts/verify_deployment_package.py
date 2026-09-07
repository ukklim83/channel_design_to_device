#!/usr/bin/env python3
"""Verify every checksum and required release gate in a deployment package."""
from __future__ import annotations

import argparse
import hashlib
import json
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
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    failures = []
    for row in manifest.get("artifacts", []):
        path = package / row["path"]
        if not path.is_file():
            failures.append(f"missing:{row['path']}")
        elif digest(path) != row["sha256"].upper():
            failures.append(f"checksum:{row['path']}")
    recipe_path = package / manifest["qualified_recipe"]
    release_path = package / manifest["qualified_release_manifest"]
    recipe = json.loads(recipe_path.read_text(encoding="utf-8-sig"))
    release = json.loads(release_path.read_text(encoding="utf-8-sig"))
    checks = {
        "manifest_production_ready": manifest.get("status") == "PRODUCTION_READY",
        "recipe_qualified": recipe.get("status") == "QUALIFIED",
        "release_manifest_qualified": release.get("status") == "QUALIFIED_RELEASE_MANIFEST",
        "controller_checksum_bound": recipe.get("source_ipt_sha256", "").upper()
        == manifest.get("controller_ready_sha256", "").upper(),
        "three_preflights_ready": set(manifest.get("preflight_statuses", {}).values())
        == {"PRODUCTION_READY"} and len(manifest.get("preflight_statuses", {})) == 3,
    }
    failures.extend(name for name, passed in checks.items() if not passed)
    status = "PASS_DEPLOYMENT_PACKAGE" if not failures else "BLOCKED_DEPLOYMENT_PACKAGE"
    print(json.dumps({"status": status, "artifact_count": len(manifest.get("artifacts", [])),
                      "checks": checks, "failures": failures}, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
