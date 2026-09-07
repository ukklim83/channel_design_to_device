"""Fail-closed rigid-U-bend release decision and recipe compiler."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


VARIABLE = {"E01", "E02", "E03", "E04", "E05", "E12", "E13", "E14", "E15", "E20", "E21", "E22"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--raw-qualification", type=Path, required=True)
    parser.add_argument("--regate", type=Path, required=True)
    parser.add_argument("--clearance", type=Path, required=True)
    parser.add_argument("--auditor", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--release-manifest", "--decision", dest="release_manifest",
                        type=Path, required=True)
    args = parser.parse_args()
    if args.recipe.exists() or args.release_manifest.exists():
        raise SystemExit("Refusing to overwrite recipe or release manifest")

    baseline_hash = digest(args.baseline)
    contract = json.loads(args.contract.read_text(encoding="utf-8-sig"))
    raw = json.loads(args.raw_qualification.read_text(encoding="utf-8-sig"))
    regate = json.loads(args.regate.read_text(encoding="utf-8-sig"))
    clearance = json.loads(args.clearance.read_text(encoding="utf-8-sig"))
    clearance_targets = clearance.get("targets", [])
    policy = clearance.get("policy", {})
    target_artifacts_bound = all(
        Path(row.get("target_workcopy", "")).is_file()
        and digest(Path(row["target_workcopy"])) == row.get("target_workcopy_sha256", "").upper()
        for row in clearance_targets
    )
    source_manifest = Path(clearance.get("source_manifest", ""))
    checks = {
        "baseline_matches_raw_qualification": raw.get("controller_baseline_sha256", "").upper() == baseline_hash,
        "baseline_matches_regate": regate.get("controller_baseline_sha256", "").upper() == baseline_hash,
        "individual_calibration_12_of_12": regate.get("calibration_pass_count") == 12,
        "simultaneous_reopen_3_of_3": regate.get("simultaneous_pass_count") == 3,
        "final_zero_health": regate.get("final_zero_state_health_passed") is True,
        "clearance_manifest_bound": clearance.get("contract_sha256", "").upper() == digest(args.contract),
        "clearance_targets_3_of_3": clearance.get("pass_count") == 3,
        "clearance_status_pass": clearance.get("status") == "PASS_ALL_RELEASE_TARGET_CLEARANCE",
        "corrected_clearance_schema": clearance.get("schema") == "MCH_RIGID_UBEND_SAVED_TARGET_CLEARANCE_V03",
        "corrected_topology_policy": policy.get("self_path_local_graph_hops_excluded") == 2,
        "nonnegative_wall_clearance_policy": float(policy.get("required_wall_clearance_mm", -1.0)) == 0.0,
        "all_observed_wall_clearances_nonnegative": len(clearance_targets) == 3 and all(
            float(row["inter_path"]["wall_clearance_mm"]) >= 0.0
            and float(row["nonadjacent_self_path"]["wall_clearance_mm"]) >= 0.0
            and row.get("health_passed") is True and row.get("passed") is True
            for row in clearance_targets
        ),
        "target_workcopy_checksums_bound": target_artifacts_bound,
        "clearance_source_manifest_bound": source_manifest.is_file()
        and digest(source_manifest) == clearance.get("source_manifest_sha256", "").upper(),
        "clearance_auditor_present": args.auditor.is_file(),
    }
    passed = all(checks.values())
    release_manifest = {
        "schema": "MCH_RIGID_UBEND_QUALIFIED_RELEASE_MANIFEST_V02",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "QUALIFIED_RELEASE_MANIFEST" if passed else "BLOCKED_RELEASE_MANIFEST",
        "controller_baseline": str(args.baseline.resolve()),
        "controller_baseline_sha256": baseline_hash,
        "contract": str(args.contract.resolve()),
        "contract_sha256": digest(args.contract),
        "raw_qualification": str(args.raw_qualification.resolve()),
        "raw_qualification_sha256": digest(args.raw_qualification),
        "release_regate": str(args.regate.resolve()),
        "release_regate_sha256": digest(args.regate),
        "clearance": str(args.clearance.resolve()),
        "clearance_sha256": digest(args.clearance),
        "clearance_auditor": str(args.auditor.resolve()),
        "clearance_auditor_sha256": digest(args.auditor),
        "clearance_policy": policy,
        "accepted_minimum_wall_clearance_mm": 0.0,
        "observed_minimum_inter_path_wall_clearance_mm": min(
            (float(row["inter_path"]["wall_clearance_mm"]) for row in clearance_targets),
            default=None,
        ),
        "observed_minimum_self_path_wall_clearance_mm": min(
            (float(row["nonadjacent_self_path"]["wall_clearance_mm"])
             for row in clearance_targets), default=None,
        ),
        "released_targets": [
            {
                "target": row["target"],
                "target_workcopy": row["target_workcopy"],
                "target_workcopy_sha256": row["target_workcopy_sha256"],
                "inter_path_wall_clearance_mm": row["inter_path"]["wall_clearance_mm"],
                "self_path_wall_clearance_mm": row["nonadjacent_self_path"]["wall_clearance_mm"],
                "health_passed": row["health_passed"],
            }
            for row in clearance_targets
        ],
        "checks": checks,
        "recipe_requested_path": str(args.recipe.resolve()),
        "recipe_emitted": passed,
        "blocked_targets": [
            {
                "target": row["target"],
                "inter_path_wall_clearance_mm": row["inter_path"]["wall_clearance_mm"],
                "self_path_wall_clearance_mm": row["nonadjacent_self_path"]["wall_clearance_mm"],
                "self_path_witness": row["nonadjacent_self_path"]["witness"],
                "health_passed": row["health_passed"],
            }
            for row in clearance.get("targets", []) if not row.get("passed")
        ],
        "required_remediation": (
            "Resolve every failed checksum, range/reopen, topology-aware clearance, "
            "target-artifact, and health check, then rerun the fail-closed compiler."
        ) if not passed else None,
    }
    args.release_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.release_manifest.write_text(json.dumps(release_manifest, indent=2) + "\n", encoding="utf-8")
    if not passed:
        print(json.dumps({"status": release_manifest["status"], "recipe_emitted": False,
                          "release_manifest": str(args.release_manifest.resolve())}, indent=2))
        return 2

    calibration = {row["edge_id"]: row for row in raw["calibration"]}
    calibration_release = {row["edge_id"]: bool(row.get("release_gate_passed",
                                                         row.get("raw_strict_passed")))
                           for row in regate["calibration"]}
    edges = []
    for edge in contract["edges"]:
        edge_id = edge["edge_id"]
        baseline_length = float(edge["baseline_for_optimizer_mm"])
        if edge_id in VARIABLE:
            row = calibration[edge_id]
            edges.append({
                "edge_id": edge_id, "mode": "VARIABLE",
                "driver_parameter": f"D_{edge_id}_COMP_DRIVE",
                "driver_mapping": "driver_delta_mm = (target_length_mm - baseline_length_mm) / 2",
                "baseline_length_mm": baseline_length,
                "driver_min_mm": row["optimizer_driver_interval_mm"][0],
                "driver_max_mm": row["optimizer_driver_interval_mm"][1],
                "achievable_min_mm": baseline_length + 2 * row["optimizer_driver_interval_mm"][0],
                "achievable_max_mm": baseline_length + 2 * row["optimizer_driver_interval_mm"][1],
                "calibration_pass": calibration_release[edge_id],
                "raw_strict_calibration_pass": row["passed"],
            })
        else:
            edges.append({"edge_id": edge_id, "mode": "FIXED",
                          "fixed_baseline_mm": baseline_length})
    recipe = {
        "schema": "MCH_QUALIFIED_RIGID_UBEND_CONTROLLER_RECIPE_V02",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "QUALIFIED",
        "source_ipt": str(args.baseline.resolve()),
        "source_ipt_sha256": baseline_hash,
        "contract": str(args.contract.resolve()),
        "contract_sha256": digest(args.contract),
        "qualification_release_manifest": str(args.release_manifest.resolve()),
        "qualification_release_manifest_sha256": digest(args.release_manifest),
        "clearance_auditor": str(args.auditor.resolve()),
        "clearance_auditor_sha256": digest(args.auditor),
        "clearance_policy": policy,
        "accepted_minimum_wall_clearance_mm": 0.0,
        "released_targets": release_manifest["released_targets"],
        "edges": edges,
    }
    args.recipe.parent.mkdir(parents=True, exist_ok=True)
    args.recipe.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "QUALIFIED", "recipe": str(args.recipe.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
