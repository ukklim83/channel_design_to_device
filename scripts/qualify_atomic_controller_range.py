#!/usr/bin/env python3
"""Calibrate all 12 atomic drivers over optimizer bounds plus guard band.

The controller-ready baseline is never edited.  Inventor creates one versioned
qualification copy.  Each driver is swept monotonically through the complete
[-2.75, +1.00] mm optimizer interval, a 0.05 mm CAD guard on both ends, and
all supplied target points.  The three target matrices are then exercised as
simultaneous states and compared with save/reopen/full-Rebuild results.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
import traceback
from pathlib import Path

import win32com.client

MM = 0.1
UP_TO_DATE = 11778
VARIABLE = ("E01", "E02", "E03", "E04", "E05", "E12", "E13", "E14", "E15", "E20", "E21", "E22")
DRIVER_MIN_MM, DRIVER_MAX_MM, CAD_GUARD_MM = -2.75, 1.0, 0.05
LENGTH_TOL_MM, TERMINAL_TOL_MM, RADIUS_TOL_MM = 0.002, 0.001, 0.0001
PLANE_TOL_MM, TANGENT_TOL_DEG = 0.001, 0.1
ARC_LENGTH_TOL_MM, ARC_ANGLE_TOL_DEG, NORMAL_TOL_DEG = 0.0001, 0.01, 0.01


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest().upper()


def xyz(value):
    if not hasattr(value, "X") and hasattr(value, "Geometry"):
        value = value.Geometry
    return [float(value.X) / MM, float(value.Y) / MM, float(value.Z) / MM]


def distance(left, right):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def unit(vector):
    magnitude = math.sqrt(sum(value * value for value in vector))
    return [value / magnitude for value in vector] if magnitude else [0.0, 0.0, 0.0]


def entities(sketch):
    return ([sketch.SketchLines3D.Item(i) for i in range(1, sketch.SketchLines3D.Count + 1)] +
            [sketch.SketchArcs3D.Item(i) for i in range(1, sketch.SketchArcs3D.Count + 1)])


def endpoints(entity):
    return xyz(entity.StartSketchPoint.Geometry), xyz(entity.EndSketchPoint.Geometry)


def role(entity):
    return "BEND" if "Arc" in str(entity.Type) or hasattr(entity.Geometry, "Radius") else "LINE"


def local_vectors(entity):
    geometry = entity.Geometry
    start, end = endpoints(entity)
    if hasattr(geometry, "Radius"):
        center = xyz(geometry.Center)
        normal = [float(geometry.Normal.X), float(geometry.Normal.Y), float(geometry.Normal.Z)]
        def tangent(point):
            radius = [point[i] - center[i] for i in range(3)]
            return unit([normal[1] * radius[2] - normal[2] * radius[1],
                         normal[2] * radius[0] - normal[0] * radius[2],
                         normal[0] * radius[1] - normal[1] * radius[0]])
        return tangent(start), tangent(end)
    direction = unit([end[i] - start[i] for i in range(3)])
    return direction, [-value for value in direction]


def topology_metrics(sketch, edge):
    all_entities = entities(sketch)
    start_terminal, end_terminal = edge["start_node_coordinate_mm"], edge["end_node_coordinate_mm"]
    unused = list(all_entities)
    current = start_terminal
    ordered = []
    junction_gaps = []
    while unused:
        candidates = []
        for entity in unused:
            start, end = endpoints(entity)
            if distance(start, current) <= TERMINAL_TOL_MM:
                candidates.append((entity, end, distance(start, current)))
            if distance(end, current) <= TERMINAL_TOL_MM:
                candidates.append((entity, start, distance(end, current)))
        if len(candidates) != 1:
            nearest = min((min(distance(start, current), distance(end, current))
                           for entity in unused for start, end in [endpoints(entity)]), default=float("inf"))
            raise RuntimeError(f"{edge['edge_id']}: path traversal stopped after {len(ordered)} entities; "
                               f"{len(candidates)} candidates, nearest endpoint {nearest:.9f} mm, current={current}")
        entity, current, gap = candidates[0]
        junction_gaps.append(gap)
        ordered.append(entity)
        unused.remove(entity)

    terminal_start_error = junction_gaps[0]
    terminal_end_error = distance(current, end_terminal)
    all_points = [point for entity in all_entities for point in endpoints(entity)]

    # The serpentine plane is derived only from bend endpoints and the two
    # driven legs.  Terminal routing may intentionally leave this plane.
    bend_points = [point for entity in all_entities if role(entity) == "BEND" for point in endpoints(entity)]
    contract_bend_points = [point for item in edge["included_entities"] if item["geometry_role"] == "BEND"
                            for point in (item["start_coordinates_mm"], item["end_coordinates_mm"])]
    ranges = [max(point[axis] for point in contract_bend_points) - min(point[axis] for point in contract_bend_points)
              for axis in range(3)]
    plane_axis = min(range(3), key=lambda axis: ranges[axis])
    plane_value = sum(point[plane_axis] for point in contract_bend_points) / len(contract_bend_points)
    plane_error = max(abs(point[plane_axis] - plane_value) for point in bend_points)

    radii = [float(entity.Geometry.Radius) / MM for entity in all_entities if role(entity) == "BEND"]
    radius_error = max(abs(value - 0.5) for value in radii)
    bends = [entity for entity in all_entities if role(entity) == "BEND"]
    arc_lengths = [float(entity.Length) / MM for entity in bends]
    arc_length_error = max(abs(value - math.pi * 0.5) for value in arc_lengths)
    included_angle_error = max(abs(math.degrees(length / radius) - 180.0)
                               for length, radius in zip(arc_lengths, radii))
    normal_errors = []
    for entity in bends:
        normal = unit([float(entity.Geometry.Normal.X), float(entity.Geometry.Normal.Y),
                       float(entity.Geometry.Normal.Z)])
        normal_errors.append(math.degrees(math.acos(max(-1.0, min(1.0, abs(normal[plane_axis]))))))

    # Source arc pairs describe the same three semicircles.  Their farthest
    # endpoint pair is the diameter, so its midpoint is the immutable source
    # center.  A qualified U-bend center may move only along its local tangent
    # (the driven leg axis), never sideways or out of plane.
    contract_bends = [item for item in edge["included_entities"] if item["geometry_role"] == "BEND"]
    source_centers = []
    for index in range(0, len(contract_bends), 2):
        pair = contract_bends[index:index + 2]
        if len(pair) != 2:
            raise RuntimeError(f"{edge['edge_id']}: incomplete source U-bend pair")
        points = [item[key] for item in pair for key in ("start_coordinates_mm", "end_coordinates_mm")]
        left, right = max(((a, b) for i, a in enumerate(points) for b in points[i + 1:]),
                          key=lambda pair_points: distance(*pair_points))
        source_centers.append([(a + b) / 2.0 for a, b in zip(left, right)])
    off_axis_errors = []
    for entity, source_center in zip(bends, source_centers):
        center = xyz(entity.Geometry.Center)
        tangent = local_vectors(entity)[0]
        displacement = [center[axis] - source_center[axis] for axis in range(3)]
        axial = sum(value * direction for value, direction in zip(displacement, tangent))
        off_axis = [value - axial * direction for value, direction in zip(displacement, tangent)]
        off_axis_errors.append(math.sqrt(sum(value * value for value in off_axis)))
    pitch_values = [2.0 * value for value in radii]
    pitch_error = max(abs(value - 1.0) for value in pitch_values)

    nodes = []
    for entity in all_entities:
        vectors = local_vectors(entity)
        for point, vector in zip(endpoints(entity), vectors):
            match = next((node for node in nodes if distance(node["point"], point) <= 1.0e-5), None)
            if match is None:
                match = {"point": point, "attachments": []}
                nodes.append(match)
            match["attachments"].append({"vector": vector, "role": role(entity)})
    tangent_errors = []
    for node in nodes:
        attachments = node["attachments"]
        if len(attachments) == 2 and any(item["role"] == "BEND" for item in attachments):
            dot = abs(sum(a * b for a, b in zip(attachments[0]["vector"], attachments[1]["vector"])))
            tangent_errors.append(math.degrees(math.acos(max(-1.0, min(1.0, dot)))))

    return {
        "actual_length_mm": sum(float(entity.Length) / MM for entity in all_entities),
        "entity_count": len(all_entities),
        "max_internal_gap_mm": max(junction_gaps[1:]) if len(junction_gaps) > 1 else 0.0,
        "start_error_mm": terminal_start_error,
        "end_error_mm": terminal_end_error,
        "max_radius_error_mm": radius_error,
        "radius_count": len(radii),
        "max_arc_length_error_mm": arc_length_error,
        "max_included_angle_error_deg": included_angle_error,
        "max_arc_normal_error_deg": max(normal_errors),
        "max_u_bend_off_axis_translation_mm": max(off_axis_errors),
        "pitch_definition": "centerline pitch = 2R for each tangent 0.5 mm U-bend",
        "max_pitch_error_mm": pitch_error,
        "serpentine_plane_axis": "XYZ"[plane_axis],
        "max_serpentine_plane_error_mm": plane_error,
        "max_tangent_kink_deg": max(tangent_errors) if tangent_errors else 0.0,
        "node_count": len(nodes),
        "all_endpoint_count": len(all_points),
    }


def health(component):
    rows = []
    for i in range(1, component.Sketches3D.Count + 1):
        item = component.Sketches3D.Item(i)
        rows.append({"type": "Sketch3D", "name": item.Name, "health": int(item.HealthStatus)})
    for i in range(1, component.Features.SweepFeatures.Count + 1):
        item = component.Features.SweepFeatures.Item(i)
        rows.append({"type": "Sweep", "name": item.Name, "health": int(item.HealthStatus)})
    return rows


def read_targets(paths):
    result = []
    for path in paths:
        values = {}
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                edge_id = f"E{int(row['edge'].strip().lstrip('Ee')):02d}"
                values[edge_id] = float(row["length"])
        result.append({"name": path.stem, "path": str(path.resolve()), "values": values})
    return result


def accepted(metrics, expected_length, health_rows):
    return (abs(metrics["actual_length_mm"] - expected_length) <= LENGTH_TOL_MM and
            metrics["max_internal_gap_mm"] <= TERMINAL_TOL_MM and
            metrics["start_error_mm"] <= TERMINAL_TOL_MM and
            metrics["end_error_mm"] <= TERMINAL_TOL_MM and
            metrics["max_radius_error_mm"] <= RADIUS_TOL_MM and
            metrics["max_arc_length_error_mm"] <= ARC_LENGTH_TOL_MM and
            metrics["max_included_angle_error_deg"] <= ARC_ANGLE_TOL_DEG and
            metrics["max_arc_normal_error_deg"] <= NORMAL_TOL_DEG and
            metrics["max_u_bend_off_axis_translation_mm"] <= TERMINAL_TOL_MM and
            metrics["max_pitch_error_mm"] <= RADIUS_TOL_MM * 2.0 and
            metrics["max_serpentine_plane_error_mm"] <= PLANE_TOL_MM and
            metrics["max_tangent_kink_deg"] <= TANGENT_TOL_DEG and
            all(row["health"] == UP_TO_DATE for row in health_rows))


def main():
    if len(sys.argv) < 6:
        raise SystemExit("usage: qualify_atomic_controller_range.py baseline.ipt contract.json destination.ipt target1.csv target2.csv [target3.csv ...]")
    baseline, contract_path, destination = map(Path, sys.argv[1:4])
    target_paths = [Path(value) for value in sys.argv[4:]]
    if destination.exists():
        raise SystemExit(f"Refusing overwrite: {destination}")
    report_path = destination.with_suffix(".json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    edge_map = {edge["edge_id"]: edge for edge in contract["edges"]}
    targets = read_targets(target_paths)
    typed_app = win32com.client.Dispatch("Inventor.Application")
    app = win32com.client.dynamic.DumbDispatch(typed_app._oleobj_)
    app.Visible = True
    document = win32com.client.dynamic.DumbDispatch(
        app.Documents.Open(str(baseline.resolve()), False)._oleobj_)
    document.SaveAs(str(destination.resolve()), True)
    document.Close(False)
    document = win32com.client.dynamic.DumbDispatch(
        app.Documents.Open(str(destination.resolve()), False)._oleobj_)
    stage = "open_qualification_copy"
    calibration = []
    simultaneous = []
    try:
        component = document.ComponentDefinition
        drivers = {edge_id: component.Parameters.UserParameters.Item(f"D_{edge_id}_COMP_DRIVE")
                   for edge_id in VARIABLE}
        sketches = {edge_id: component.Sketches3D.Item(f"SK_RIGID_UBEND_{edge_id}_V01")
                    for edge_id in VARIABLE}

        def ramp_all(targets_mm, maximum_step_mm=0.1):
            starts = {edge_id: float(parameter.Value) / MM for edge_id, parameter in drivers.items()}
            steps = max(1, math.ceil(max(abs(float(targets_mm.get(edge_id, 0.0)) - starts[edge_id])
                                         for edge_id in VARIABLE) / maximum_step_mm))
            for step_index in range(1, steps + 1):
                fraction = step_index / steps
                for edge_id, parameter in drivers.items():
                    target = float(targets_mm.get(edge_id, 0.0))
                    value = starts[edge_id] + fraction * (target - starts[edge_id])
                    parameter.Expression = f"{value:.12f} mm"
                document.Update2(True)

        def stabilize(rebuild_count=3):
            for _ in range(rebuild_count):
                document.Rebuild2(True)

        def reset_drivers():
            ramp_all({edge_id: 0.0 for edge_id in VARIABLE})
            stabilize()

        for edge_id in VARIABLE:
            stage = f"calibrate_{edge_id}"
            reset_drivers()
            baseline_length = float(edge_map[edge_id]["baseline_for_optimizer_mm"])
            target_deltas = [(target["values"][edge_id] - baseline_length) / 2.0 for target in targets]
            raw_points = sorted(round(value, 12) for value in
                                ([DRIVER_MIN_MM - CAD_GUARD_MM, DRIVER_MIN_MM, -2.0, -1.0, 0.0,
                                  0.5, DRIVER_MAX_MM, DRIVER_MAX_MM + CAD_GUARD_MM] + target_deltas))
            points = []
            for value in raw_points:
                if not points or abs(value - points[-1]) > 1.0e-6:
                    points.append(value)
            probes = []
            for delta in points:
                print(f"CALIBRATE {edge_id} {delta:+.12f} mm", flush=True)
                ramp_all({candidate: (delta if candidate == edge_id else 0.0)
                          for candidate in VARIABLE})
                stabilize()
                inventory = health(component)
                metrics = topology_metrics(sketches[edge_id], edge_map[edge_id])
                expected = baseline_length + 2.0 * delta
                probe = {"driver_delta_mm": delta, "expected_length_mm": expected,
                         "length_error_mm": metrics["actual_length_mm"] - expected,
                         **metrics, "nonhealthy": [row for row in inventory if row["health"] != UP_TO_DATE]}
                probe["passed"] = accepted(metrics, expected, inventory)
                probes.append(probe)
            monotonic = all(left["actual_length_mm"] < right["actual_length_mm"] + 1.0e-9
                            for left, right in zip(probes, probes[1:]))
            calibration.append({"edge_id": edge_id, "driver_parameter": f"D_{edge_id}_COMP_DRIVE",
                                "optimizer_driver_interval_mm": [DRIVER_MIN_MM, DRIVER_MAX_MM],
                                "tested_interval_with_guard_mm": [DRIVER_MIN_MM - CAD_GUARD_MM,
                                                                   DRIVER_MAX_MM + CAD_GUARD_MM],
                                "monotonic_increasing": monotonic, "probes": probes,
                                "passed": monotonic and all(probe["passed"] for probe in probes)})
            reset_drivers()

        for target in targets:
            stage = f"simultaneous_{target['name']}"
            print(f"SIMULTANEOUS {target['name']} apply/update/full-rebuild", flush=True)
            reset_drivers()
            deltas = {}
            for edge_id in VARIABLE:
                baseline_length = float(edge_map[edge_id]["baseline_for_optimizer_mm"])
                delta = (target["values"][edge_id] - baseline_length) / 2.0
                deltas[edge_id] = delta
            ramp_all(deltas)
            stabilize()
            before_health = health(component)
            before = {edge_id: topology_metrics(sketches[edge_id], edge_map[edge_id]) for edge_id in VARIABLE}
            document.Save2(True)
            document.Close(True)
            document = win32com.client.dynamic.DumbDispatch(
                app.Documents.Open(str(destination.resolve()), False)._oleobj_)
            stabilize()
            component = document.ComponentDefinition
            drivers = {edge_id: component.Parameters.UserParameters.Item(f"D_{edge_id}_COMP_DRIVE")
                       for edge_id in VARIABLE}
            sketches = {edge_id: component.Sketches3D.Item(f"SK_RIGID_UBEND_{edge_id}_V01")
                        for edge_id in VARIABLE}
            after_health = health(component)
            after = {edge_id: topology_metrics(sketches[edge_id], edge_map[edge_id]) for edge_id in VARIABLE}
            edges_evidence = []
            for edge_id in VARIABLE:
                expected = target["values"][edge_id]
                same = abs(before[edge_id]["actual_length_mm"] - after[edge_id]["actual_length_mm"]) <= 1.0e-6
                ok = (accepted(before[edge_id], expected, before_health) and
                      accepted(after[edge_id], expected, after_health) and same)
                edges_evidence.append({"edge_id": edge_id, "driver_delta_mm": deltas[edge_id],
                                       "target_length_mm": expected, "before_reopen": before[edge_id],
                                       "after_reopen": after[edge_id], "reopen_same": same, "passed": ok})
            simultaneous.append({"target": target["name"], "target_path": target["path"],
                                 "nonhealthy_before_reopen": [row for row in before_health if row["health"] != UP_TO_DATE],
                                 "nonhealthy_after_reopen": [row for row in after_health if row["health"] != UP_TO_DATE],
                                 "edges": edges_evidence, "passed": all(row["passed"] for row in edges_evidence)})

        reset_drivers()
        document.Rebuild2(True)
        document.Save2(True)
        final_health = health(component)
        document.Close(True)
        passed = (all(row["passed"] for row in calibration) and all(row["passed"] for row in simultaneous)
                  and all(row["health"] == UP_TO_DATE for row in final_health))
        result = {
            "schema": "MCH_RIGID_UBEND_CONTROLLER_RANGE_QUALIFICATION_V02",
            "status": "PASS_RANGE_TERMINAL_GEOMETRY_REOPEN_PENDING_CLEARANCE" if passed else "BLOCKED_RANGE_OR_GEOMETRY",
            "controller_baseline": str(baseline.resolve()),
            "controller_baseline_sha256": digest(baseline),
            "qualification_workcopy": str(destination.resolve()),
            "optimizer_path_length_bounds_mm": [-5.5, 2.0],
            "driver_bounds_mm": [DRIVER_MIN_MM, DRIVER_MAX_MM],
            "cad_guard_each_side_mm": CAD_GUARD_MM,
            "acceptance": {"length_tolerance_mm": LENGTH_TOL_MM, "terminal_tolerance_mm": TERMINAL_TOL_MM,
                           "radius_tolerance_mm": RADIUS_TOL_MM, "plane_tolerance_mm": PLANE_TOL_MM,
                           "arc_length_tolerance_mm": ARC_LENGTH_TOL_MM,
                           "arc_angle_tolerance_deg": ARC_ANGLE_TOL_DEG,
                           "arc_normal_tolerance_deg": NORMAL_TOL_DEG,
                           "u_bend_off_axis_translation_tolerance_mm": TERMINAL_TOL_MM,
                           "tangent_kink_tolerance_deg": TANGENT_TOL_DEG,
                           "health_required": UP_TO_DATE},
            "calibration": calibration, "simultaneous_target_states": simultaneous,
            "final_zero_state_health": final_health,
            "next_gate": "global sampled clearance for every simultaneous release target",
        }
        report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], "report": str(report_path),
                          "edge_pass_count": sum(row["passed"] for row in calibration),
                          "simultaneous_pass_count": sum(row["passed"] for row in simultaneous)}, indent=2))
        return 0 if passed else 2
    except Exception as error:
        result = {"schema": "MCH_RIGID_UBEND_CONTROLLER_RANGE_QUALIFICATION_V02",
                  "status": "QUALIFICATION_FAILED_PRESERVED_TRIAL", "stage": stage,
                  "controller_baseline": str(baseline.resolve()), "qualification_workcopy": str(destination.resolve()),
                  "calibration_completed": calibration, "simultaneous_completed": simultaneous,
                  "error": repr(error), "traceback": traceback.format_exc()}
        report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        try:
            document.Save2(True)
            document.Close(True)
        except Exception:
            pass
        print(json.dumps({"status": result["status"], "stage": stage, "error": repr(error)}, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
