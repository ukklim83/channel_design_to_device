"""Create target-specific rigid-U-bend IPTs and audit sampled clearance.

The controller baseline is read-only.  Each simultaneous target from the
checksum-bound release re-gate is applied to a new IPT, ramped in <=0.1 mm
driver steps, rebuilt, saved, reopened, rebuilt again, health checked, and
sampled across all 26 paths.  Inter-path shared terminal capsules and local
same-path entity neighborhoods are excluded from collision witnesses.  The
local neighborhood includes directly connected entities and entities separated
by one connector.  Radius, pitch, tangency, and controller health remain
separate release gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import traceback
from pathlib import Path

import win32com.client


MM = 0.1
CHANNEL_WIDTH_MM = 0.5
REQUIRED_WALL_CLEARANCE_MM = 0.0
SHARED_TERMINAL_CAPSULE_MM = 0.51
SAMPLES_PER_ENTITY = 41
SELF_PATH_LOCAL_GRAPH_HOPS = 2
UP_TO_DATE = 11778
VARIABLE = ("E01", "E02", "E03", "E04", "E05", "E12", "E13", "E14", "E15", "E20", "E21", "E22")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest().upper()


def distance(left, right):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def xyz(point):
    if hasattr(point, "Geometry"):
        point = point.Geometry
    return [float(point.X) / MM, float(point.Y) / MM, float(point.Z) / MM]


def sample(entity):
    geometry = entity.Geometry
    start, end = xyz(entity.StartSketchPoint), xyz(entity.EndSketchPoint)
    if not hasattr(geometry, "Radius"):
        return [[start[axis] + (end[axis] - start[axis]) * index / (SAMPLES_PER_ENTITY - 1)
                 for axis in range(3)] for index in range(SAMPLES_PER_ENTITY)]

    # Inventor 2026's generated wrapper does not allocate the [out] SAFEARRAY
    # when it is omitted.  Supplying an explicit zero-filled output sequence
    # makes the kernel evaluator return the actual bounded arc branch (which
    # matters for a 180-degree arc because its endpoints alone are ambiguous).
    evaluator = geometry.Evaluator
    low, high = evaluator.GetParamExtents()
    parameters = tuple(low + (high - low) * index / (SAMPLES_PER_ENTITY - 1)
                       for index in range(SAMPLES_PER_ENTITY))
    _, coordinates = evaluator.GetPointAtParam(parameters, (0.0,) * (3 * SAMPLES_PER_ENTITY))
    points = [[float(coordinates[index]) / MM,
               float(coordinates[index + 1]) / MM,
               float(coordinates[index + 2]) / MM]
              for index in range(0, len(coordinates), 3)]
    if min(distance(points[0], start) + distance(points[-1], end),
           distance(points[0], end) + distance(points[-1], start)) > 1.0e-4:
        raise RuntimeError("Analytic Arc3d sampling did not reproduce entity endpoints")
    return points


def entity_rows(sketch):
    rows = []
    for kind, collection in (("LINE", sketch.SketchLines3D), ("ARC", sketch.SketchArcs3D)):
        for index in range(1, collection.Count + 1):
            entity = collection.Item(index)
            points = sample(entity)
            rows.append({
                "name": f"{sketch.Name}:{kind}:{index}",
                "points": points,
                "endpoints": [xyz(entity.StartSketchPoint), xyz(entity.EndSketchPoint)],
                "bbox_low": [min(point[axis] for point in points) for axis in range(3)],
                "bbox_high": [max(point[axis] for point in points) for axis in range(3)],
            })
    return rows


def bbox_lower(left, right):
    return math.sqrt(sum(max(left["bbox_low"][axis] - right["bbox_high"][axis],
                             right["bbox_low"][axis] - left["bbox_high"][axis], 0.0) ** 2
                         for axis in range(3)))


def connected(left, right):
    return any(distance(a, b) <= 1.0e-5 for a in left["endpoints"] for b in right["endpoints"])


def connection_hops(rows):
    """Return all-pairs entity-graph hop counts for one continuous path.

    A hop of one means two entities share an endpoint.  A hop of two means
    they are consecutive route elements with exactly one entity between them,
    such as ARC--short LEG--ARC.  Those pairs are a local swept-path
    neighborhood, not two independent branches competing for wall clearance.
    """
    count = len(rows)
    hops = [[count + 1] * count for _ in range(count)]
    for index in range(count):
        hops[index][index] = 0
    for left_index, left in enumerate(rows):
        for right_index in range(left_index + 1, count):
            if connected(left, rows[right_index]):
                hops[left_index][right_index] = 1
                hops[right_index][left_index] = 1
    for middle in range(count):
        for left_index in range(count):
            through_middle = hops[left_index][middle]
            if through_middle > count:
                continue
            for right_index in range(count):
                candidate = through_middle + hops[middle][right_index]
                if candidate < hops[left_index][right_index]:
                    hops[left_index][right_index] = candidate
    return hops


def nearest_pair(left_rows, right_rows, terminal_nodes, same_path=False):
    best = (float("inf"), None)
    hops = connection_hops(left_rows) if same_path else None
    for left_index, left in enumerate(left_rows):
        candidates = right_rows[left_index + 1:] if same_path else right_rows
        for candidate_index, right in enumerate(candidates):
            right_index = left_index + 1 + candidate_index if same_path else candidate_index
            graph_hops = hops[left_index][right_index] if same_path else None
            if same_path and graph_hops <= SELF_PATH_LOCAL_GRAPH_HOPS:
                continue
            if bbox_lower(left, right) >= best[0]:
                continue
            for left_point in left["points"]:
                for right_point in right["points"]:
                    current = distance(left_point, right_point)
                    if current >= best[0]:
                        continue
                    if not same_path and any(
                        # Include a tiny numerical tolerance: a sampled point
                        # at the intended 0.510 mm terminal capsule boundary
                        # can otherwise evaluate as 0.5100000000000016 mm and
                        # be falsely reported as an inter-path clearance pair.
                        distance(left_point, node) <= SHARED_TERMINAL_CAPSULE_MM + 1.0e-8
                        and distance(right_point, node) <= SHARED_TERMINAL_CAPSULE_MM + 1.0e-8
                        for node in terminal_nodes
                    ):
                        continue
                    best = (current, {
                        "left": left["name"], "right": right["name"],
                        "points_mm": [left_point, right_point],
                        "scope": "self" if same_path else "inter_path",
                        **({"entity_graph_hops": graph_hops} if same_path else {}),
                    })
    return best


def health(component):
    rows = []
    for collection_type, collection in (
        ("Sketch3D", component.Sketches3D),
        ("Sweep", component.Features.SweepFeatures),
    ):
        for index in range(1, collection.Count + 1):
            item = collection.Item(index)
            rows.append({"type": collection_type, "name": item.Name,
                         "health": int(item.HealthStatus)})
    return rows


def topology_signature(component):
    """Stable feature signature used across consecutive reopen cycles."""
    sketches = {}
    sweeps = {}
    for index in range(1, component.Sketches3D.Count + 1):
        item = component.Sketches3D.Item(index)
        sketches[str(item.Name)] = {
            "health": int(item.HealthStatus),
            "lines": int(item.SketchLines3D.Count),
            "arcs": int(item.SketchArcs3D.Count),
        }
    for index in range(1, component.Features.SweepFeatures.Count + 1):
        item = component.Features.SweepFeatures.Item(index)
        sweeps[str(item.Name)] = {
            "health": int(item.HealthStatus),
            "faces": int(item.Faces.Count),
            "side_faces": int(item.SideFaces.Count),
            "suppressed": bool(item.Suppressed),
        }
    return {"sketches": sketches, "sweeps": sweeps}


def geometry_signature(component):
    """Physical-body signature; independent of harmless B-rep face splitting."""
    bodies = []
    for index in range(1, component.SurfaceBodies.Count + 1):
        body = component.SurfaceBodies.Item(index)
        box = body.PreciseRangeBox
        mass = body.MassProperties
        center = mass.CenterOfMass
        bodies.append({
            "name": str(body.Name),
            "is_solid": bool(body.IsSolid),
            "volume_cm3": round(float(body.Volume(0.001)), 10),
            "area_cm2": round(float(mass.Area), 10),
            "center_cm": [round(float(center.X), 10), round(float(center.Y), 10),
                          round(float(center.Z), 10)],
            "precise_box_cm": [round(float(box.MinPoint.X), 10), round(float(box.MinPoint.Y), 10),
                               round(float(box.MinPoint.Z), 10), round(float(box.MaxPoint.X), 10),
                               round(float(box.MaxPoint.Y), 10), round(float(box.MaxPoint.Z), 10)],
        })
    return sorted(bodies, key=lambda row: row["name"])


def compare_geometry(left, right):
    volume_tol_cm3 = 1.0e-6
    area_tol_cm2 = 1.0e-5
    coordinate_tol_cm = 1.0e-5
    left_rows = {row["name"]: row for row in left}
    right_rows = {row["name"]: row for row in right}
    names_equal = set(left_rows) == set(right_rows)
    names = sorted(set(left_rows) & set(right_rows))
    if not names:
        return {"equivalent": False, "body_names_equal": names_equal}
    result = {
        "body_names_equal": names_equal,
        "solid_flags_equal": all(left_rows[name]["is_solid"] == right_rows[name]["is_solid"]
                                 for name in names),
        "max_volume_delta_cm3": max(abs(left_rows[name]["volume_cm3"] - right_rows[name]["volume_cm3"])
                                    for name in names),
        "max_area_delta_cm2": max(abs(left_rows[name]["area_cm2"] - right_rows[name]["area_cm2"])
                                  for name in names),
        "max_center_delta_cm": max(abs(a - b) for name in names
                                   for a, b in zip(left_rows[name]["center_cm"], right_rows[name]["center_cm"])),
        "max_box_delta_cm": max(abs(a - b) for name in names
                                for a, b in zip(left_rows[name]["precise_box_cm"], right_rows[name]["precise_box_cm"])),
        "tolerances": {"volume_cm3": volume_tol_cm3, "area_cm2": area_tol_cm2,
                       "center_and_box_cm": coordinate_tol_cm},
    }
    result["equivalent"] = (result["body_names_equal"] and result["solid_flags_equal"] and
                            result["max_volume_delta_cm3"] <= volume_tol_cm3 and
                            result["max_area_delta_cm2"] <= area_tol_cm2 and
                            result["max_center_delta_cm"] <= coordinate_tol_cm and
                            result["max_box_delta_cm"] <= coordinate_tol_cm)
    return result


def final_path_metric(component, edge_contract, target_length_mm):
    edge_id = edge_contract["edge_id"]
    sketch = component.Sketches3D.Item(f"SK_RIGID_UBEND_{edge_id}_V01")
    entities = [sketch.SketchLines3D.Item(i) for i in range(1, sketch.SketchLines3D.Count + 1)]
    entities += [sketch.SketchArcs3D.Item(i) for i in range(1, sketch.SketchArcs3D.Count + 1)]
    actual = sum(float(entity.Length) / MM for entity in entities)
    endpoints = []
    for entity in entities:
        endpoints.extend([xyz(entity.StartSketchPoint), xyz(entity.EndSketchPoint)])
    terminals = []
    for point in endpoints:
        if sum(distance(point, other) <= 1.0e-5 for other in endpoints) == 1:
            terminals.append(point)
    expected = [edge_contract["start_node_coordinate_mm"], edge_contract["end_node_coordinate_mm"]]
    if len(terminals) != 2:
        terminal_error = float("inf")
    else:
        terminal_error = min(
            max(distance(terminals[0], expected[0]), distance(terminals[1], expected[1])),
            max(distance(terminals[0], expected[1]), distance(terminals[1], expected[0])),
        )
    return {
        "edge_id": edge_id,
        "target_length_mm": target_length_mm,
        "actual_length_mm": actual,
        "length_error_mm": actual - target_length_mm,
        "terminal_count": len(terminals),
        "terminal_max_error_mm": terminal_error,
    }


def safe_target_tag(name: str) -> str:
    match = re.search(r"(70_30|80_20|90_10)", name)
    return match.group(1) if match else re.sub(r"[^A-Za-z0-9_-]+", "_", name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--regate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    baseline = args.baseline.resolve()
    contract_path = args.contract.resolve()
    regate_path = args.regate.resolve()
    output_dir = args.output_dir.resolve()
    manifest_path = args.manifest.resolve()
    if manifest_path.exists():
        raise SystemExit(f"Refusing overwrite: {manifest_path}")

    contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    regate = json.loads(regate_path.read_text(encoding="utf-8-sig"))
    if regate.get("status") != "PASS_RIGID_UBEND_RANGE_AND_REOPEN_PENDING_CLEARANCE":
        raise SystemExit("Release re-gate is not clearance-authorized")
    if digest(baseline) != regate.get("controller_baseline_sha256", "").upper():
        raise SystemExit("Baseline checksum does not match release re-gate")
    if not all(row.get("release_gate_passed") for row in regate["simultaneous_target_states"]):
        raise SystemExit("Not every simultaneous target passed the release re-gate")

    terminal_nodes = []
    for edge in contract["edges"]:
        for point in (edge["start_node_coordinate_mm"], edge["end_node_coordinate_mm"]):
            if not any(distance(point, known) <= 1.0e-6 for known in terminal_nodes):
                terminal_nodes.append(point)

    output_dir.mkdir(parents=True, exist_ok=True)
    typed_app = win32com.client.Dispatch("Inventor.Application")
    app = win32com.client.dynamic.DumbDispatch(typed_app._oleobj_)
    app.Visible = True
    results = []
    stage = "start"
    document = None
    try:
        for target in regate["simultaneous_target_states"]:
            tag = safe_target_tag(target["target"])
            destination = output_dir / f"optimized_rigid_ubend_{tag}_v01.ipt"
            report_path = destination.with_suffix(".clearance.json")
            if destination.exists() or report_path.exists():
                raise RuntimeError(f"Refusing overwrite for target {tag}")

            stage = f"{tag}:copy_baseline"
            document = win32com.client.dynamic.DumbDispatch(
                app.Documents.Open(str(baseline), False)._oleobj_)
            document.SaveAs(str(destination), True)
            document.Close(False)
            document = win32com.client.dynamic.DumbDispatch(
                app.Documents.Open(str(destination), False)._oleobj_)
            component = document.ComponentDefinition
            drivers = {edge_id: component.Parameters.UserParameters.Item(f"D_{edge_id}_COMP_DRIVE")
                       for edge_id in VARIABLE}
            target_deltas = {row["edge_id"]: float(row["driver_delta_mm"])
                             for row in target["edges"]}

            stage = f"{tag}:ramp"
            starts = {edge_id: float(driver.Value) / MM for edge_id, driver in drivers.items()}
            step_count = max(1, math.ceil(max(abs(target_deltas[edge_id] - starts[edge_id])
                                               for edge_id in VARIABLE) / 0.1))
            for step_index in range(1, step_count + 1):
                fraction = step_index / step_count
                for edge_id, driver in drivers.items():
                    value = starts[edge_id] + fraction * (target_deltas[edge_id] - starts[edge_id])
                    driver.Expression = f"{value:.12f} mm"
                document.Update2(True)
            for _ in range(3):
                document.Rebuild2(True)
            ramp_health = health(component)

            # Canonicalize every variable feature after simultaneous ramping.
            # Other drivers remain at their final targets while one edge is
            # independently invalidated and rebuilt at a time.
            stage = f"{tag}:independent_canonicalization"
            canonicalization = []
            for edge_id in VARIABLE:
                driver = drivers[edge_id]
                driver.Expression = "0 mm"
                document.Update2(True)
                driver.Expression = f"{target_deltas[edge_id]:.12f} mm"
                document.Update2(True)
                document.Rebuild2(True)
                canonicalization.append({
                    "edge_id": edge_id,
                    "nonhealthy_after_reapply": [row for row in health(component)
                                                  if row["health"] != UP_TO_DATE],
                })
            for _ in range(3):
                document.Rebuild2(True)
            before_health = health(component)
            topology_before_save = topology_signature(component)
            geometry_before_save = geometry_signature(component)
            document.Save2(True)
            document.Close(True)
            document = None
            reopen_cycles = []
            previous_geometry = None
            geometry_stable = False
            max_reopen_cycles = 4
            for cycle in range(1, max_reopen_cycles + 1):
                document = win32com.client.dynamic.DumbDispatch(
                    app.Documents.Open(str(destination), False)._oleobj_)
                for _ in range(3):
                    document.Rebuild2(True)
                component = document.ComponentDefinition
                cycle_health = health(component)
                cycle_topology = topology_signature(component)
                cycle_geometry = geometry_signature(component)
                geometry_comparison = (compare_geometry(previous_geometry, cycle_geometry)
                                       if previous_geometry is not None else None)
                stable_with_previous = bool(geometry_comparison and geometry_comparison["equivalent"])
                reopen_cycles.append({
                    "cycle": cycle,
                    "nonhealthy": [row for row in cycle_health if row["health"] != UP_TO_DATE],
                    "topology": cycle_topology,
                    "geometry": cycle_geometry,
                    "geometry_comparison_with_previous": geometry_comparison,
                    "physical_geometry_stable_with_previous": stable_with_previous,
                })
                if stable_with_previous:
                    geometry_stable = True
                    break
                document.Save2(True)
                document.Close(True)
                document = None
                previous_geometry = cycle_geometry
            if document is None:
                raise RuntimeError(f"{tag}: physical geometry did not converge within {max_reopen_cycles} cycles")

            stage = f"{tag}:sample"
            paths = []
            for index in range(1, component.Sketches3D.Count + 1):
                sketch = component.Sketches3D.Item(index)
                paths.append((sketch.Name, entity_rows(sketch)))
            if len(paths) != 26:
                raise RuntimeError(f"Expected 26 path sketches, found {len(paths)}")

            best = (float("inf"), None)
            for index, (left_name, left_rows) in enumerate(paths):
                self_result = nearest_pair(left_rows, left_rows, terminal_nodes, same_path=True)
                if self_result[0] < best[0]:
                    best = self_result
                for right_name, right_rows in paths[index + 1:]:
                    pair_result = nearest_pair(left_rows, right_rows, terminal_nodes)
                    if pair_result[0] < best[0]:
                        best = pair_result

            min_centerline = best[0]
            wall_clearance = min_centerline - CHANNEL_WIDTH_MM
            edge_contracts = {row["edge_id"]: row for row in contract["edges"]}
            path_metrics = [final_path_metric(component, edge_contracts[row["edge_id"]],
                                                float(row["target_length_mm"]))
                            for row in target["edges"]]
            driver_errors = {edge_id: float(component.Parameters.UserParameters.Item(
                f"D_{edge_id}_COMP_DRIVE").Value) / MM - target_deltas[edge_id]
                for edge_id in VARIABLE}
            health_ok = (all(row["health"] == UP_TO_DATE for row in before_health) and
                         all(not cycle["nonhealthy"] for cycle in reopen_cycles))
            path_ok = all(abs(row["length_error_mm"]) <= 0.002 and
                          row["terminal_max_error_mm"] <= 0.001 for row in path_metrics)
            drivers_ok = all(abs(value) <= 1.0e-8 for value in driver_errors.values())
            passed = (health_ok and geometry_stable and path_ok and drivers_ok and
                      wall_clearance >= REQUIRED_WALL_CLEARANCE_MM)
            result = {
                "schema": "MCH_RIGID_UBEND_TARGET_CLEARANCE_V01",
                "status": "PASS_TARGET_GLOBAL_CLEARANCE_AND_REOPEN"
                if passed else "BLOCKED_TARGET_CLEARANCE_OR_HEALTH",
                "target": target["target"],
                "target_path": target["target_path"],
                "controller_baseline": str(baseline),
                "controller_baseline_sha256": digest(baseline),
                "target_workcopy": str(destination),
                "driver_deltas_mm": target_deltas,
                "ramp_max_step_mm": 0.1,
                "independent_reapplication_order": list(VARIABLE),
                "independent_reapplication_reset_mm": 0.0,
                "canonicalization_stages": canonicalization,
                "post_ramp_rebuild_count": 3,
                "reopen_cycle_count": len(reopen_cycles),
                "maximum_reopen_cycles": max_reopen_cycles,
                "rebuilds_per_reopen": 3,
                "channel_width_mm": CHANNEL_WIDTH_MM,
                "required_wall_clearance_mm": REQUIRED_WALL_CLEARANCE_MM,
                "samples_per_entity": SAMPLES_PER_ENTITY,
                "shared_terminal_capsule_mm": SHARED_TERMINAL_CAPSULE_MM,
                "self_path_local_graph_hops_excluded": SELF_PATH_LOCAL_GRAPH_HOPS,
                "path_count": len(paths),
                "global_min_centerline_mm": min_centerline,
                "global_wall_clearance_mm": wall_clearance,
                "witness": best[1],
                "path_metrics": path_metrics,
                "driver_errors_mm": driver_errors,
                "physical_geometry_stable_across_reopens": geometry_stable,
                "physical_geometry_converged_at_cycle": reopen_cycles[-1]["cycle"] if geometry_stable else None,
                "topology_before_save": topology_before_save,
                "geometry_before_save": geometry_before_save,
                "reopen_cycles": reopen_cycles,
                "nonhealthy_after_simultaneous_ramp": [row for row in ramp_health if row["health"] != UP_TO_DATE],
                "nonhealthy_before_reopen": [row for row in before_health if row["health"] != UP_TO_DATE],
                "passed": passed,
            }
            document.Close(False)
            document = None
            result["target_workcopy_sha256"] = digest(destination)
            report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            results.append({**result, "report": str(report_path), "report_sha256": digest(report_path)})
            print(json.dumps({"target": tag, "status": result["status"],
                              "wall_clearance_mm": wall_clearance}, indent=2), flush=True)

        passed = all(row["passed"] for row in results)
        manifest = {
            "schema": "MCH_RIGID_UBEND_GLOBAL_CLEARANCE_BATCH_V02",
            "status": "PASS_ALL_RELEASE_TARGET_CLEARANCE"
            if passed else "BLOCKED_ONE_OR_MORE_RELEASE_TARGETS",
            "controller_baseline": str(baseline),
            "controller_baseline_sha256": digest(baseline),
            "contract": str(contract_path),
            "contract_sha256": digest(contract_path),
            "release_regate": str(regate_path),
            "release_regate_sha256": digest(regate_path),
            "policy": {
                "channel_width_mm": CHANNEL_WIDTH_MM,
                "required_wall_clearance_mm": REQUIRED_WALL_CLEARANCE_MM,
                "samples_per_entity": SAMPLES_PER_ENTITY,
                "shared_terminal_capsule_mm": SHARED_TERMINAL_CAPSULE_MM,
                "includes_inter_path": True,
                "includes_nonadjacent_self_path": True,
                "self_path_local_graph_hops_excluded": SELF_PATH_LOCAL_GRAPH_HOPS,
                "local_geometry_covered_by_release_regate": [
                    "radius", "pitch", "tangency", "planarity", "feature_health"
                ],
            },
            "targets": results,
            "pass_count": sum(row["passed"] for row in results),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": manifest["status"], "pass_count": manifest["pass_count"],
                          "manifest": str(manifest_path)}, indent=2))
        return 0 if passed else 2
    except Exception as error:
        failure = {
            "schema": "MCH_RIGID_UBEND_GLOBAL_CLEARANCE_BATCH_V02",
            "status": "CLEARANCE_AUDIT_FAILED_PRESERVED_TRIAL",
            "stage": stage,
            "results_completed": results,
            "error": repr(error),
            "traceback": traceback.format_exc(),
        }
        manifest_path.write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        if document is not None:
            try:
                document.Save2(True)
                document.Close(True)
            except Exception:
                pass
        print(json.dumps({"status": failure["status"], "stage": stage,
                          "error": repr(error)}, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
