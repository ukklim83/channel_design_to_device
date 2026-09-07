#!/usr/bin/env python3
"""Re-audit solver-history-sensitive controller states from a clean baseline.

Failed calibration probes are each applied to a fresh Inventor copy.  Release
targets are ramped simultaneously and then every named driver is independently
reset and returned in feature-tree order before save/reopen measurement.  This
is a supplemental gate; it does not perform global clearance.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import shutil
import time
from pathlib import Path

import win32com.client

MM = 0.1
UP_TO_DATE = 11778
VARIABLE = ("E01", "E02", "E03", "E04", "E05", "E12", "E13", "E14", "E15", "E20", "E21", "E22")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest().upper()


def load_qualification_helpers(path: Path):
    spec = importlib.util.spec_from_file_location("qualification_helpers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def release_checks(metrics, expected_length, health_rows):
    checks = {
        "length": abs(metrics["actual_length_mm"] - expected_length) <= 0.002,
        "junction_gap": metrics["max_internal_gap_mm"] <= 0.001,
        "start_terminal": metrics["start_error_mm"] <= 0.001,
        "end_terminal": metrics["end_error_mm"] <= 0.001,
        "radius": metrics["max_radius_error_mm"] <= 0.0001,
        "pitch": metrics["max_pitch_error_mm"] <= 0.0002,
        "plane": metrics["max_serpentine_plane_error_mm"] <= 0.001,
        "tangent": metrics["max_tangent_kink_deg"] <= 0.1,
        "arc_length": metrics["max_arc_length_error_mm"] <= 0.0005,
        "arc_angle": metrics["max_included_angle_error_deg"] <= 0.05,
        "arc_normal": metrics["max_arc_normal_error_deg"] <= 0.01,
        "u_bend_off_axis": metrics["max_u_bend_off_axis_translation_mm"] <= 0.001,
        "health": all(row["health"] == UP_TO_DATE for row in health_rows),
    }
    return checks, all(checks.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--raw-qualification", type=Path, required=True)
    parser.add_argument("--qualification-script", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    baseline = args.baseline.resolve()
    contract = json.loads(args.contract.read_text(encoding="utf-8-sig"))
    raw = json.loads(args.raw_qualification.read_text(encoding="utf-8-sig"))
    helpers = load_qualification_helpers(args.qualification_script.resolve())
    edge_map = {row["edge_id"]: row for row in contract["edges"]}
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.evidence.exists():
        raise SystemExit(f"Refusing overwrite: {args.evidence}")

    # Use the registered Inventor automation session.  Inventor 2026 may start
    # an executable for DispatchEx but fail COM registration with
    # CO_E_SERVER_EXEC_FAILURE; the normal Dispatch path is the stable API used
    # by the controller builder and range qualification on this workstation.
    def start_app():
        typed = win32com.client.Dispatch("Inventor.Application")
        dynamic = win32com.client.dynamic.DumbDispatch(typed._oleobj_)
        dynamic.Visible = True
        # COM registration can complete before Documents.Open is ready after
        # an automation-process restart.
        time.sleep(5.0)
        return dynamic

    app = start_app()

    def recycle_app():
        nonlocal app
        try:
            app.Quit()
        finally:
            del app
            gc.collect()
            time.sleep(2.0)
            app = start_app()

    def open_fresh(destination: Path):
        if destination.exists():
            raise RuntimeError(f"Refusing overwrite: {destination}")
        # The controller-ready IPT is a standalone part.  Copy the closed,
        # checksum-qualified file byte-for-byte before opening the work copy.
        # Repeated source Open -> SaveAs transitions can leave Inventor's COM
        # server waiting on a stale NAS document lock between audit cases.
        shutil.copy2(baseline, destination)
        last_error = None
        for _ in range(3):
            try:
                return win32com.client.dynamic.DumbDispatch(
                    app.Documents.Open(str(destination), False)._oleobj_)
            except Exception as error:
                last_error = error
                time.sleep(5.0)
        raise RuntimeError(f"Inventor could not open copied work file {destination}: {last_error!r}")

    def objects(document):
        component = document.ComponentDefinition
        drivers = {edge_id: component.Parameters.UserParameters.Item(f"D_{edge_id}_COMP_DRIVE")
                   for edge_id in VARIABLE}
        sketches = {edge_id: component.Sketches3D.Item(f"SK_RIGID_UBEND_{edge_id}_V01")
                    for edge_id in VARIABLE}
        return component, drivers, sketches

    def ramp(document, drivers, targets, step=0.1):
        starts = {edge_id: float(driver.Value) / MM for edge_id, driver in drivers.items()}
        count = max(1, math.ceil(max(abs(float(targets.get(edge_id, 0.0)) - starts[edge_id])
                                     for edge_id in VARIABLE) / step))
        for index in range(1, count + 1):
            fraction = index / count
            for edge_id, driver in drivers.items():
                value = starts[edge_id] + fraction * (float(targets.get(edge_id, 0.0)) - starts[edge_id])
                driver.Expression = f"{value:.12f} mm"
            document.Update2(True)

    def rebuild(document, count=3):
        for _ in range(count):
            document.Rebuild2(True)

    calibration = []
    for edge in raw["calibration"]:
        failed = [probe for probe in edge["probes"] if not probe["passed"]]
        for probe_index, probe in enumerate(failed, 1):
            edge_id = edge["edge_id"]
            delta = float(probe["driver_delta_mm"])
            destination = output_dir / f"calibration_{edge_id}_{probe_index:02d}.ipt"
            print(f"INDEPENDENT CALIBRATION {edge_id} {delta:+.12f}", flush=True)
            document = open_fresh(destination)
            component, drivers, sketches = objects(document)
            ramp(document, drivers, {candidate: delta if candidate == edge_id else 0.0 for candidate in VARIABLE})
            rebuild(document)
            before_health = helpers.health(component)
            before = helpers.topology_metrics(sketches[edge_id], edge_map[edge_id])
            expected = float(probe["expected_length_mm"])
            before_checks, before_pass = release_checks(before, expected, before_health)
            document.Save2(True)
            document.Close(True)
            document = win32com.client.dynamic.DumbDispatch(app.Documents.Open(str(destination), False)._oleobj_)
            rebuild(document)
            component, drivers, sketches = objects(document)
            after_health = helpers.health(component)
            after = helpers.topology_metrics(sketches[edge_id], edge_map[edge_id])
            after_checks, after_pass = release_checks(after, expected, after_health)
            document.Close(False)
            calibration.append({
                "edge_id": edge_id, "driver_delta_mm": delta, "expected_length_mm": expected,
                "workcopy": str(destination), "workcopy_sha256": digest(destination),
                "before_reopen": before, "before_checks": before_checks,
                "after_reopen": after, "after_checks": after_checks,
                "release_gate_passed": before_pass and after_pass,
            })

    # Inventor can retain stale document/reference state after a calibration
    # copy is closed.  Start every target family from a new automation process
    # so the result cannot depend on that preceding document history.
    recycle_app()

    targets = []
    for target_index, target in enumerate(raw["simultaneous_target_states"]):
        tag = target["target"]
        print(f"INDEPENDENT TARGET {tag}", flush=True)
        compact_tag = next((name for name in ("70_30", "80_20", "90_10") if name in tag),
                           f"state_{target_index + 1:02d}")
        # Inventor's Documents.Open still exercises legacy path-length limits
        # on this workstation.  Keep the work-copy leaf short even though the
        # evidence retains the full optimizer target name.
        destination = output_dir / f"target_{compact_tag}.ipt"
        document = open_fresh(destination)
        component, drivers, sketches = objects(document)
        deltas = {row["edge_id"]: float(row["driver_delta_mm"]) for row in target["edges"]}
        expected = {row["edge_id"]: float(row["target_length_mm"]) for row in target["edges"]}
        ramp(document, drivers, deltas)
        rebuild(document)
        for edge_id in VARIABLE:
            drivers[edge_id].Expression = "0 mm"
            document.Update2(True)
            drivers[edge_id].Expression = f"{deltas[edge_id]:.12f} mm"
            document.Update2(True)
        rebuild(document)
        before_health = helpers.health(component)
        before = {}
        passed = True
        for edge_id in VARIABLE:
            metrics = helpers.topology_metrics(sketches[edge_id], edge_map[edge_id])
            checks, ok = release_checks(metrics, expected[edge_id], before_health)
            before[edge_id] = {"metrics": metrics, "checks": checks, "passed": ok}
            passed = passed and ok
        document.Save2(True)
        document.Close(True)
        document = win32com.client.dynamic.DumbDispatch(app.Documents.Open(str(destination), False)._oleobj_)
        rebuild(document)
        component, drivers, sketches = objects(document)
        after_health = helpers.health(component)
        raw_after_reopen = {}
        for edge_id in VARIABLE:
            metrics = helpers.topology_metrics(sketches[edge_id], edge_map[edge_id])
            checks, ok = release_checks(metrics, expected[edge_id], after_health)
            raw_after_reopen[edge_id] = {"metrics": metrics, "checks": checks, "passed": ok}

        # A reopen can restore a solver branch from cached reference history.
        # Canonicalize again in feature order, save that state, then verify one
        # additional reopen without touching any driver.
        for edge_id in VARIABLE:
            drivers[edge_id].Expression = "0 mm"
            document.Update2(True)
            drivers[edge_id].Expression = f"{deltas[edge_id]:.12f} mm"
            document.Update2(True)
        rebuild(document)
        canonical_health = helpers.health(component)
        canonical = {}
        for edge_id in VARIABLE:
            metrics = helpers.topology_metrics(sketches[edge_id], edge_map[edge_id])
            checks, ok = release_checks(metrics, expected[edge_id], canonical_health)
            canonical[edge_id] = {"metrics": metrics, "checks": checks, "passed": ok}
        document.Save2(True)
        document.Close(True)

        document = win32com.client.dynamic.DumbDispatch(app.Documents.Open(str(destination), False)._oleobj_)
        rebuild(document)
        component, drivers, sketches = objects(document)
        verification_health = helpers.health(component)
        verification_1 = {}
        for edge_id in VARIABLE:
            metrics = helpers.topology_metrics(sketches[edge_id], edge_map[edge_id])
            checks, ok = release_checks(metrics, expected[edge_id], verification_health)
            verification_1[edge_id] = {"metrics": metrics, "checks": checks, "passed": ok}
            passed = passed and ok
        document.Close(False)

        document = win32com.client.dynamic.DumbDispatch(app.Documents.Open(str(destination), False)._oleobj_)
        rebuild(document)
        component, drivers, sketches = objects(document)
        verification_2_health = helpers.health(component)
        verification_2 = {}
        for edge_id in VARIABLE:
            metrics = helpers.topology_metrics(sketches[edge_id], edge_map[edge_id])
            checks, ok = release_checks(metrics, expected[edge_id], verification_2_health)
            verification_2[edge_id] = {"metrics": metrics, "checks": checks, "passed": ok}
            passed = passed and ok
        document.Close(False)
        targets.append({
            "target": tag, "workcopy": str(destination), "workcopy_sha256": digest(destination),
            "driver_deltas_mm": deltas, "before_reopen": before,
            "raw_after_first_reopen_diagnostic": raw_after_reopen,
            "post_reopen_independent_reapplication_diagnostic": canonical,
            "verification_reopen_1": verification_1,
            "verification_reopen_2": verification_2,
            "release_gate_passed": passed,
        })
        if target_index + 1 < len(raw["simultaneous_target_states"]):
            recycle_app()

    result = {
        "schema": "MCH_INDEPENDENT_CONTROLLER_REAPPLICATION_AUDIT_V01",
        "status": ("PASS_INDEPENDENT_REAPPLICATION_PENDING_CLEARANCE"
                   if all(row["release_gate_passed"] for row in calibration + targets)
                   else "BLOCKED_INDEPENDENT_REAPPLICATION"),
        "controller_baseline": str(baseline),
        "controller_baseline_sha256": digest(baseline),
        "raw_qualification": str(args.raw_qualification.resolve()),
        "raw_qualification_sha256": digest(args.raw_qualification.resolve()),
        "calibration_rechecks": calibration,
        "target_rechecks": targets,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    app.Quit()
    print(json.dumps({"status": result["status"], "calibration_pass_count": sum(
        row["release_gate_passed"] for row in calibration), "target_pass_count": sum(
        row["release_gate_passed"] for row in targets), "evidence": str(args.evidence)}, indent=2))
    return 0 if result["status"].startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
