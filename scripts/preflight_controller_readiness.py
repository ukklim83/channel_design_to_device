#!/usr/bin/env python3
"""Fail-closed readiness gate for a two-input IPT + length-matrix run.

It inspects an IPT without saving it, looks up only a recipe whose directory
matches that exact source checksum, and records whether production application
or first-use controller onboarding is required.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest().upper()


def matrix_e24(path: Path) -> float | None:
    if path.suffix.lower() != ".csv":
        return None
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row.get("edge", "").strip().upper() == "E24":
                return float(row["length"])
    raise ValueError("CSV does not contain edge E24")


def inspect_named_drivers(source: Path) -> tuple[str, list[str]]:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize(); doc = None; opened_here = True
    try:
        typed_app = win32com.client.Dispatch("Inventor.Application")
        app = win32com.client.dynamic.DumbDispatch(typed_app._oleobj_)
        resolved = str(source.resolve())
        for index in range(1, app.Documents.Count + 1):
            candidate = app.Documents.Item(index)
            if str(candidate.FullFileName).lower() == resolved.lower():
                doc = win32com.client.dynamic.DumbDispatch(candidate._oleobj_)
                opened_here = False; break
        if doc is None:
            doc = win32com.client.dynamic.DumbDispatch(
                app.Documents.Open(resolved, False)._oleobj_)
        user = doc.ComponentDefinition.Parameters.UserParameters
        names = [str(user.Item(i).Name) for i in range(1, user.Count + 1)]
        return str(app.SoftwareVersion.DisplayVersion), sorted(x for x in names if x.upper().startswith("D_E") and "COMP_DRIVE" in x.upper())
    finally:
        if doc is not None and opened_here: doc.Close(True)
        pythoncom.CoUninitialize()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-ipt", type=Path, required=True)
    ap.add_argument("--length-mat", type=Path, required=True)
    ap.add_argument("--recipe-root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists(): raise SystemExit(f"Refusing overwrite: {args.output}")
    if not args.source_ipt.is_file() or not args.length_mat.is_file(): raise SystemExit("source IPT and length matrix must exist")
    source_hash = digest(args.source_ipt); e24 = matrix_e24(args.length_mat)
    inventor_version, drivers = inspect_named_drivers(args.source_ipt)
    recipe_path = args.recipe_root / source_hash.lower() / "recipe.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8")) if recipe_path.is_file() else None
    reasons = []
    if recipe is None: reasons.append("checksum-matched qualified controller recipe is absent")
    elif recipe.get("status") != "QUALIFIED" or recipe.get("source_ipt_sha256", "").upper() != source_hash: reasons.append("checksum-matched recipe is not QUALIFIED for this source IPT")
    if recipe is not None:
        manifest = Path(recipe.get("qualification_release_manifest", ""))
        auditor = Path(recipe.get("clearance_auditor", ""))
        contract = Path(recipe.get("contract", ""))
        if not manifest.is_file() or digest(manifest) != recipe.get("qualification_release_manifest_sha256", "").upper():
            reasons.append("qualification release manifest is absent or checksum-mismatched")
        if not auditor.is_file() or digest(auditor) != recipe.get("clearance_auditor_sha256", "").upper():
            reasons.append("clearance auditor is absent or checksum-mismatched")
        if not contract.is_file() or digest(contract) != recipe.get("contract_sha256", "").upper():
            reasons.append("canonical contract is absent or checksum-mismatched")
        policy = recipe.get("clearance_policy", {})
        if policy.get("self_path_local_graph_hops_excluded") != 2 or float(policy.get("required_wall_clearance_mm", -1.0)) != 0.0:
            reasons.append("recipe clearance policy does not match the qualified corrected topology policy")
        # Released workcopies are mandatory for the original release recipe.
        # A portable deployment recipe instead carries the qualified release
        # manifest as evidence and intentionally creates fresh workcopies.
        if recipe.get("released_target_artifacts_required", True):
            for released in recipe.get("released_targets", []):
                target = Path(released.get("target_workcopy", ""))
                if not target.is_file() or digest(target) != released.get("target_workcopy_sha256", "").upper():
                    reasons.append("released target IPT is absent or checksum-mismatched: " + released.get("target", "UNKNOWN"))
    required = [] if recipe is None else [row["driver_parameter"] for row in recipe.get("edges", []) if row.get("mode") == "VARIABLE"]
    missing = sorted(set(required) - set(drivers))
    if missing: reasons.append("recipe requires named drivers absent from source IPT: " + ", ".join(missing))
    if not drivers: reasons.append("source IPT has no D_E##_COMP_DRIVE named native drivers")
    result = {"schema":"MCH_CONTROLLER_READINESS_V01", "created_utc":datetime.now(timezone.utc).isoformat(), "source_ipt":str(args.source_ipt.resolve()), "source_ipt_sha256":source_hash, "length_mat":str(args.length_mat.resolve()), "length_mat_E24_mm":e24, "E24_policy":"FIXED_NO_DRIVER_TARGET_MUST_EQUAL_SOURCE_SPECIFIC_BASELINE", "inventor_version":inventor_version, "named_driver_candidates":drivers, "recipe_path":str(recipe_path.resolve()), "status":"PRODUCTION_READY" if not reasons else "ONBOARDING_REQUIRED", "reasons":reasons}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{result['status']}: {args.output}")
    raise SystemExit(0 if result["status"] == "PRODUCTION_READY" else 1)


if __name__ == "__main__": main()
