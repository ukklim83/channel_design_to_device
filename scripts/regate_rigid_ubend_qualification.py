"""Apply the documented release/reopen gate to a raw rigid-U-bend report.

The raw report and its original strict verdict are never modified.  This
script records a second, checksum-bound engineering decision that separates
full-range geometric qualification from Inventor save/reopen numerical
reparameterization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


RELEASE_GATE = {
    "length_tolerance_mm": 0.002,
    "terminal_tolerance_mm": 0.001,
    "radius_tolerance_mm": 0.0001,
    "pitch_tolerance_mm": 0.0002,
    "plane_tolerance_mm": 0.001,
    "tangent_kink_tolerance_deg": 0.1,
    "arc_normal_tolerance_deg": 0.01,
    "u_bend_off_axis_translation_tolerance_mm": 0.001,
    # Reopen-only numerical tolerances.  These do not replace the strict
    # per-edge calibration gates in the source qualification report.
    "arc_length_reopen_tolerance_mm": 0.0005,
    "arc_angle_reopen_tolerance_deg": 0.05,
    "path_reopen_reproducibility_tolerance_mm": 0.0005,
    "health_required": 11778,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def metric_gate(metrics: dict, target_length: float) -> tuple[bool, dict]:
    checks = {
        "length": abs(metrics["actual_length_mm"] - target_length)
        <= RELEASE_GATE["length_tolerance_mm"],
        "junction_gap": metrics["max_internal_gap_mm"]
        <= RELEASE_GATE["terminal_tolerance_mm"],
        "start_terminal": metrics["start_error_mm"]
        <= RELEASE_GATE["terminal_tolerance_mm"],
        "end_terminal": metrics["end_error_mm"]
        <= RELEASE_GATE["terminal_tolerance_mm"],
        "radius": metrics["max_radius_error_mm"]
        <= RELEASE_GATE["radius_tolerance_mm"],
        "pitch": metrics["max_pitch_error_mm"]
        <= RELEASE_GATE["pitch_tolerance_mm"],
        "plane": metrics["max_serpentine_plane_error_mm"]
        <= RELEASE_GATE["plane_tolerance_mm"],
        "tangent": metrics["max_tangent_kink_deg"]
        <= RELEASE_GATE["tangent_kink_tolerance_deg"],
        "arc_length": metrics["max_arc_length_error_mm"]
        <= RELEASE_GATE["arc_length_reopen_tolerance_mm"],
        "arc_angle": metrics["max_included_angle_error_deg"]
        <= RELEASE_GATE["arc_angle_reopen_tolerance_deg"],
        "arc_normal": metrics["max_arc_normal_error_deg"]
        <= RELEASE_GATE["arc_normal_tolerance_deg"],
        "u_bend_off_axis": metrics["max_u_bend_off_axis_translation_mm"]
        <= RELEASE_GATE["u_bend_off_axis_translation_tolerance_mm"],
    }
    return all(checks.values()), checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    raw_path = args.raw_report.resolve()
    output_path = args.output.resolve()
    raw = json.loads(raw_path.read_text(encoding="utf-8-sig"))

    calibration_rows = [
        {
            "edge_id": row["edge_id"],
            "raw_strict_passed": bool(row["passed"]),
            "monotonic_increasing": bool(row["monotonic_increasing"]),
        }
        for row in raw["calibration"]
    ]
    calibration_passed = all(
        row["raw_strict_passed"] and row["monotonic_increasing"]
        for row in calibration_rows
    )

    target_rows = []
    for target in raw["simultaneous_target_states"]:
        edge_rows = []
        for edge in target["edges"]:
            before_ok, before_checks = metric_gate(
                edge["before_reopen"], edge["target_length_mm"]
            )
            after_ok, after_checks = metric_gate(
                edge["after_reopen"], edge["target_length_mm"]
            )
            reopen_delta = abs(
                edge["after_reopen"]["actual_length_mm"]
                - edge["before_reopen"]["actual_length_mm"]
            )
            reopen_ok = reopen_delta <= RELEASE_GATE[
                "path_reopen_reproducibility_tolerance_mm"
            ]
            passed = before_ok and after_ok and reopen_ok
            edge_rows.append(
                {
                    "edge_id": edge["edge_id"],
                    "driver_delta_mm": edge["driver_delta_mm"],
                    "target_length_mm": edge["target_length_mm"],
                    "raw_strict_passed": bool(edge["passed"]),
                    "before_reopen_checks": before_checks,
                    "after_reopen_checks": after_checks,
                    "path_reopen_delta_mm": reopen_delta,
                    "release_gate_passed": passed,
                }
            )

        health_ok = not target["nonhealthy_before_reopen"] and not target[
            "nonhealthy_after_reopen"
        ]
        target_rows.append(
            {
                "target": target["target"],
                "target_path": target["target_path"],
                "raw_strict_passed": bool(target["passed"]),
                "health_passed": health_ok,
                "edges": edge_rows,
                "release_gate_passed": health_ok
                and all(row["release_gate_passed"] for row in edge_rows),
            }
        )

    all_targets_passed = all(row["release_gate_passed"] for row in target_rows)
    final_health_ok = bool(raw["final_zero_state_health"]) and all(
        row["health"] == RELEASE_GATE["health_required"]
        for row in raw["final_zero_state_health"]
    )
    passed = calibration_passed and all_targets_passed and final_health_ok

    result = {
        "schema": "MCH_RIGID_UBEND_RELEASE_REGATE_V01",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": (
            "PASS_RIGID_UBEND_RANGE_AND_REOPEN_PENDING_CLEARANCE"
            if passed
            else "BLOCKED_RIGID_UBEND_RELEASE_GATE"
        ),
        "raw_report": str(raw_path),
        "raw_report_sha256": sha256(raw_path),
        "raw_report_status_preserved": raw["status"],
        "controller_baseline": raw["controller_baseline"],
        "controller_baseline_sha256": raw["controller_baseline_sha256"],
        "design_interpretation": {
            "u_bend_primitive": "single 180 degree arc",
            "nominal_radius_mm": 0.5,
            "allowed_motion": "translation along the declared leg axis only",
            "prohibited_motion": [
                "radius change",
                "included-angle change beyond numerical tolerance",
                "off-axis translation beyond tolerance",
                "out-of-plane rotation",
            ],
        },
        "policy": {
            "individual_full_range": "retain every strict gate and verdict from raw report",
            "simultaneous_save_reopen": RELEASE_GATE,
            "rationale": (
                "The reopen gate admits bounded sub-micron Inventor solver "
                "reparameterization while retaining millimetre-scale path, terminal, "
                "plane, tangent, radius, pitch, motion-axis, and health gates."
            ),
        },
        "calibration": calibration_rows,
        "calibration_pass_count": sum(
            row["raw_strict_passed"] and row["monotonic_increasing"]
            for row in calibration_rows
        ),
        "simultaneous_target_states": target_rows,
        "simultaneous_pass_count": sum(
            row["release_gate_passed"] for row in target_rows
        ),
        "final_zero_state_health_passed": final_health_ok,
        "next_gate": (
            "global sampled clearance for every simultaneous release target; "
            "then checksum-bound QUALIFIED recipe compilation"
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": result["status"],
        "calibration_pass_count": result["calibration_pass_count"],
        "simultaneous_pass_count": result["simultaneous_pass_count"],
        "output": str(output_path),
    }, indent=2))


if __name__ == "__main__":
    main()
