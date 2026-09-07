#!/usr/bin/env python3
"""Promote a blocked solver-history regate only with measured independent evidence."""
from __future__ import annotations

import argparse
import copy
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
    parser.add_argument("--regate", type=Path, required=True)
    parser.add_argument("--independent-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Refusing overwrite: {args.output}")
    regate = json.loads(args.regate.read_text(encoding="utf-8-sig"))
    evidence = json.loads(args.independent_evidence.read_text(encoding="utf-8-sig"))
    checks = {
        "input_regate_blocked_only_by_release_gate": regate.get("status") == "BLOCKED_RIGID_UBEND_RELEASE_GATE",
        "independent_status_pass": evidence.get("status") == "PASS_INDEPENDENT_REAPPLICATION_PENDING_CLEARANCE",
        "baseline_checksum_match": evidence.get("controller_baseline_sha256", "").upper()
        == regate.get("controller_baseline_sha256", "").upper(),
        "raw_qualification_checksum_match": evidence.get("raw_qualification_sha256", "").upper()
        == regate.get("raw_report_sha256", "").upper(),
        "all_independent_calibration_rechecks_pass": bool(evidence.get("calibration_rechecks"))
        and all(row.get("release_gate_passed") for row in evidence["calibration_rechecks"]),
        "all_three_independent_targets_pass": len(evidence.get("target_rechecks", [])) == 3
        and all(row.get("release_gate_passed") for row in evidence["target_rechecks"]),
        "target_names_match": {row["target"] for row in evidence.get("target_rechecks", [])}
        == {row["target"] for row in regate.get("simultaneous_target_states", [])},
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit(f"Independent promotion checks failed: {failed}")

    result = copy.deepcopy(regate)
    result["schema"] = "MCH_RIGID_UBEND_RELEASE_REGATE_V02_INDEPENDENT_REAPPLICATION"
    result["generated_utc"] = datetime.now(timezone.utc).isoformat()
    result["status"] = "PASS_RIGID_UBEND_RANGE_AND_REOPEN_PENDING_CLEARANCE"
    result["parent_regate"] = str(args.regate.resolve())
    result["parent_regate_sha256"] = digest(args.regate)
    result["independent_reapplication_evidence"] = str(args.independent_evidence.resolve())
    result["independent_reapplication_evidence_sha256"] = digest(args.independent_evidence)
    result["independent_promotion_checks"] = checks
    result.setdefault("policy", {})["solver_history_recovery"] = {
        "failed_calibration_probe": "fresh baseline copy",
        "mixed_target": "simultaneous ramp, feature-order independent reapplication after reopen",
        "final_state": "two consecutive verification reopens",
        "intermediate_post_reapplication_state": "diagnostic; final consecutive reopen states are authoritative",
        "physical_signature_and_clearance": "mandatory in the next audit",
    }

    calibration_evidence = {row["edge_id"]: row for row in evidence["calibration_rechecks"]}
    for row in result["calibration"]:
        if row["edge_id"] in calibration_evidence:
            row["independent_reapplication_passed"] = calibration_evidence[row["edge_id"]]["release_gate_passed"]
            row["independent_workcopy"] = calibration_evidence[row["edge_id"]]["workcopy"]
            row["independent_workcopy_sha256"] = calibration_evidence[row["edge_id"]]["workcopy_sha256"]
        row["release_gate_passed"] = bool(row.get("raw_strict_passed")) or bool(
            row.get("independent_reapplication_passed"))
    result["calibration_pass_count"] = sum(row["release_gate_passed"] for row in result["calibration"])

    target_evidence = {row["target"]: row for row in evidence["target_rechecks"]}
    for row in result["simultaneous_target_states"]:
        independent = target_evidence[row["target"]]
        row["independent_reapplication_passed"] = independent["release_gate_passed"]
        row["independent_workcopy"] = independent["workcopy"]
        row["independent_workcopy_sha256"] = independent["workcopy_sha256"]
        row["release_gate_passed"] = independent["release_gate_passed"]
    result["simultaneous_pass_count"] = sum(
        row["release_gate_passed"] for row in result["simultaneous_target_states"])
    if result["calibration_pass_count"] != 12 or result["simultaneous_pass_count"] != 3:
        raise SystemExit("Promotion did not produce 12/12 calibration and 3/3 target coverage")
    result["next_gate"] = "global clearance and canonicalized save/reopen physical-signature audit"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "calibration_pass_count": 12,
                      "simultaneous_pass_count": 3, "output": str(args.output.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
