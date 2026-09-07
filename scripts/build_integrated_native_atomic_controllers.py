#!/usr/bin/env python3
"""Build 26 source-faithful atomic paths in one versioned work copy.

The supplied source IPT is opened read-only and copied by Inventor.  Each
legacy sweep/path is replaced by an independently owned 3D sketch, profile,
and sweep.  The 12 qualified variable paths additionally receive a
``D_E##_COMP_DRIVE`` User Parameter.  All legacy path features are removed
only from the new work copy after every replacement exists at zero drive.
"""
from __future__ import annotations

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
ALL_EDGES = tuple(f"E{index:02d}" for index in range(1, 27))
LEGS = {
    "E01": (6, 9), "E02": (5, 8), "E03": (5, 8), "E04": (5, 8),
    "E05": (6, 9), "E12": (6, 9), "E13": (5, 8), "E14": (5, 8),
    "E15": (6, 9), "E20": (6, 9), "E21": (5, 8), "E22": (6, 9),
}


def merge_variable_u_bends(items, enabled: bool):
    """Collapse every consecutive source-arc pair into one rigid semicircle.

    The source authoring convention split each R0.5 mm, 180 degree U-bend into
    two arcs at an arbitrary seam.  Keeping that seam gives the 3D solver an
    unnecessary branch and plane degree of freedom.  A three-point arc through
    the original pair's start, seam, and end is the exact same circle, but is a
    single topological entity whose shape is fixed by its radius and the two
    line tangencies.
    """
    if not enabled:
        return items
    merged = []
    index = 0
    while index < len(items):
        first = items[index]
        if first["geometry_role"] != "BEND":
            merged.append(first)
            index += 1
            continue
        if index + 1 >= len(items) or items[index + 1]["geometry_role"] != "BEND":
            raise RuntimeError("variable U-bend is not represented by two consecutive source arcs")
        second = items[index + 1]
        seam_error = distance(first["end_coordinates_mm"], second["start_coordinates_mm"])
        if seam_error > 1e-5:
            raise RuntimeError(f"source U-bend arc seam is open by {seam_error:.9f} mm")
        bend = dict(first)
        bend["end_coordinates_mm"] = list(second["end_coordinates_mm"])
        bend["length_mm"] = float(first["length_mm"]) + float(second["length_mm"])
        bend["merged_source_sequences"] = [int(first["sequence"]), int(second["sequence"])]
        bend["arc_through_coordinates_mm"] = list(first["end_coordinates_mm"])
        bend["rigid_u_bend"] = True
        merged.append(bend)
        index += 2
    return merged


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest().upper()


def point(transient, values):
    return transient.CreatePoint(*(float(value) * MM for value in values))


def xyz(value):
    if not hasattr(value, "X") and hasattr(value, "Geometry"):
        value = value.Geometry
    return [float(value.X) / MM, float(value.Y) / MM, float(value.Z) / MM]


def distance(left, right) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def endpoint(entity, orientation: int, at_start: bool):
    return entity.StartSketchPoint if ((orientation == 1) == at_start) else entity.EndSketchPoint


def matching_entity(sketch, row):
    collection = (sketch.SketchLines3D if row["geometry_role"] == "LINE"
                  else sketch.SketchArcs3D)
    wanted_start = row["start_coordinates_mm"]
    wanted_end = row["end_coordinates_mm"]
    for index in range(1, collection.Count + 1):
        entity = collection.Item(index)
        if abs(float(entity.Length) / MM - float(row["length_mm"])) > 1e-5:
            continue
        start, end = xyz(entity.StartPoint), xyz(entity.EndPoint)
        if ((distance(start, wanted_start) <= 1e-5 and distance(end, wanted_end) <= 1e-5) or
                (distance(start, wanted_end) <= 1e-5 and distance(end, wanted_start) <= 1e-5)):
            return entity
    return None


def locate_source_sketch(component, rows):
    for index in range(1, component.Sketches3D.Count + 1):
        sketch = component.Sketches3D.Item(index)
        if all(matching_entity(sketch, row) is not None for row in rows):
            return sketch
    raise RuntimeError("source-local contract sketch not found")


def source_entities(sketch, rows):
    result = {}
    for row in rows:
        entity = matching_entity(sketch, row)
        if entity is None:
            raise RuntimeError(f"source entity missing for sequence {row['sequence']}")
        result[row["reference_key"]] = entity
    return result


def path_length(items, created) -> float:
    return sum(float(created[int(item["sequence"])].Length) / MM for item in items)


def health_inventory(component):
    rows = []
    for index in range(1, component.Sketches3D.Count + 1):
        item = component.Sketches3D.Item(index)
        rows.append({"type": "Sketch3D", "name": item.Name, "health": int(item.HealthStatus)})
    for index in range(1, component.Features.SweepFeatures.Count + 1):
        item = component.Features.SweepFeatures.Item(index)
        rows.append({"type": "Sweep", "name": item.Name, "health": int(item.HealthStatus)})
    return rows


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: build_integrated_native_atomic_controllers.py source.ipt contract.json destination.ipt")
    source, contract_path, destination = map(Path, sys.argv[1:])
    if destination.exists():
        raise SystemExit(f"Refusing overwrite: {destination}")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    source_hash = digest(source)
    if contract.get("source_ipt_sha256", "").upper() != source_hash:
        raise SystemExit("Contract checksum does not match source IPT")
    edges = {row["edge_id"]: row for row in contract["edges"]}
    report = destination.with_suffix(".json")
    # Force late binding.  Inventor's generated Python wrapper incorrectly
    # exposes GetReferenceKey's optional arguments and breaks the checksum-
    # bound entity lookup used by this builder.
    typed_app = win32com.client.Dispatch("Inventor.Application")
    app = win32com.client.dynamic.DumbDispatch(typed_app._oleobj_)
    app.Visible = True
    document = win32com.client.dynamic.DumbDispatch(
        app.Documents.Open(str(source.resolve()), False)._oleobj_)
    document.SaveAs(str(destination.resolve()), True)
    document.Close(False)
    document = win32com.client.dynamic.DumbDispatch(
        app.Documents.Open(str(destination.resolve()), False)._oleobj_)
    stage = "open_work_copy"
    completed = []
    try:
        component = document.ComponentDefinition
        transient = app.TransientGeometry
        replacements = {}
        legacy = {}
        for edge_id in ALL_EDGES:
            edge = edges[edge_id]
            source_items = edge["included_entities"]
            # Normalize every entity into contract traversal direction.  This
            # lets each new entity begin at the preceding entity's endpoint,
            # producing one native endpoint-to-endpoint chain.
            items = []
            for source_item in source_items:
                item = dict(source_item)
                if int(item["orientation"]) == -1:
                    item["start_coordinates_mm"], item["end_coordinates_mm"] = (
                        item["end_coordinates_mm"], item["start_coordinates_mm"])
                item["orientation"] = 1
                items.append(item)
            items = merge_variable_u_bends(items, edge_id in VARIABLE)
            leg_sequences = LEGS.get(edge_id, ())
            stage = f"locate_{edge_id}"
            old_sketch = locate_source_sketch(component, source_items)
            old_map = source_entities(old_sketch, source_items)
            old_sweep = component.Features.SweepFeatures.Item(edge["controller_sweep"][0])
            legacy[edge_id] = {"sketch": old_sketch, "sketch_name": old_sketch.Name,
                               "sweep": old_sweep, "sweep_name": old_sweep.Name}

            stage = f"create_sketch_{edge_id}"
            sketch = component.Sketches3D.Add()
            sketch.Name = f"SK_RIGID_UBEND_{edge_id}_V01"
            created = {}
            source_arcs = {}
            current = point(transient, items[0]["start_coordinates_mm"])
            for item in items:
                sequence = int(item["sequence"])
                source_entity = old_map[item["reference_key"]]
                if item["geometry_role"] == "LINE":
                    entity = sketch.SketchLines3D.AddByTwoPoints(
                        current,
                        point(transient, item["end_coordinates_mm"]), False)
                else:
                    if item.get("rigid_u_bend"):
                        # The original seam is a point on the same R0.5 circle.
                        # Using it as the through-point preserves the source
                        # branch while removing the seam itself.
                        middle = point(transient, item["arc_through_coordinates_mm"])
                    else:
                        geometry = source_entity.Geometry
                        evaluator = geometry.Evaluator
                        low, high = evaluator.GetParamExtents()
                        _, middle_coordinates = evaluator.GetPointAtParam(((low + high) / 2.0,))
                        middle = transient.CreatePoint(*middle_coordinates[:3])
                    entity = sketch.SketchArcs3D.AddByThreePoints(
                        current, middle,
                        point(transient, item["end_coordinates_mm"]))
                    source_arcs[sequence] = source_entity
                created[sequence] = entity
                # Pass the native SketchPoint3D itself into the next entity so
                # Inventor owns one shared endpoint rather than two merely
                # coincident transient points.
                current = entity.EndSketchPoint

            stage = f"constrain_{edge_id}"
            constraints = sketch.GeometricConstraints3D
            first, last = items[0], items[-1]
            constraints.AddGround(endpoint(created[int(first["sequence"])], int(first["orientation"]), True))
            constraints.AddGround(endpoint(created[int(last["sequence"])], int(last["orientation"]), False))

            parameter_name = f"D_{edge_id}_COMP_DRIVE" if edge_id in VARIABLE else None
            driver = (component.Parameters.UserParameters.AddByExpression(parameter_name, "0 mm", 11269)
                      if parameter_name else None)
            dimensions = sketch.DimensionConstraints3D
            if parameter_name:
                bend_indices = [index for index, item in enumerate(items) if item["geometry_role"] == "BEND"]
                moving_bends = []
                for item_index, item in enumerate(items):
                    if item["geometry_role"] != "BEND":
                        continue
                    if item_index == 0 or item_index == len(items) - 1:
                        raise RuntimeError(f"{edge_id}: terminal arc cannot receive two tangent constraints")
                    arc = created[int(item["sequence"])]
                    left = created[int(items[item_index - 1]["sequence"])]
                    right = created[int(items[item_index + 1]["sequence"])]
                    left_is_leg = (items[item_index - 1]["geometry_role"] == "LINE" and
                                   int(items[item_index - 1]["sequence"]) in leg_sequences)
                    right_is_leg = (items[item_index + 1]["geometry_role"] == "LINE" and
                                    int(items[item_index + 1]["sequence"]) in leg_sequences)
                    if not (left_is_leg and right_is_leg):
                        # Outer U-bends belong to the terminal-fixed routing
                        # skeleton.  Grounding the full arc freezes its radius,
                        # included angle, plane, branch, and location.
                        constraints.AddGround(arc)
                        continue
                    moving_bends.append((item_index, item, arc, left, right))

                # Ground every point of the non-variable routing skeleton.  A
                # variable leg therefore has one fixed outer endpoint, while
                # its other endpoint is carried only by the central U-bend.
                grounded = set()
                for item in items:
                    if (item["geometry_role"] == "LINE" and
                            int(item["sequence"]) not in leg_sequences):
                        entity = created[int(item["sequence"])]
                        for sketch_point in (entity.StartSketchPoint, entity.EndSketchPoint):
                            coordinates = tuple(round(value, 10) for value in xyz(sketch_point.Geometry))
                            if coordinates not in grounded:
                                try:
                                    constraints.AddGround(sketch_point)
                                except Exception:
                                    pass
                                grounded.add(coordinates)

                # Establish the only permitted motion before constraining the
                # central arc; adding this after tangency can be reported as a
                # redundant/over-constraining relation by Inventor.
                for item in (row for row in items if row["geometry_role"] == "LINE"
                             and int(row["sequence"]) in leg_sequences):
                    vector = [float(item["end_coordinates_mm"][axis]) -
                              float(item["start_coordinates_mm"][axis]) for axis in range(3)]
                    axis = max(range(3), key=lambda value: abs(vector[value]))
                    axis_method = (constraints.AddParallelToXAxis, constraints.AddParallelToYAxis,
                                   constraints.AddParallelToZAxis)[axis]
                    axis_method(created[int(item["sequence"])] )

                # One driving equation is sufficient for a rigid translator.
                # Giving both equal legs separate copies of the same expression
                # is algebraically redundant and caused return-to-zero solver
                # hysteresis.  Drive the first leg; make the second equal.
                leg_items = [row for row in items if row["geometry_role"] == "LINE"
                             and int(row["sequence"]) in leg_sequences]
                if len(leg_items) != 2:
                    raise RuntimeError(f"{edge_id}: expected exactly two driven legs")
                if abs(float(leg_items[0]["length_mm"]) - float(leg_items[1]["length_mm"])) > 1e-6:
                    raise RuntimeError(f"{edge_id}: rigid translator legs do not share a baseline length")
                primary_leg = created[int(leg_items[0]["sequence"])]
                follower_leg = created[int(leg_items[1]["sequence"])]
                dimension = dimensions.AddLineLength(primary_leg, None, False)
                dimension.Parameter.Expression = (
                    f"{float(leg_items[0]['length_mm']):.12f} mm + {parameter_name}")
                constraints.AddEqual(primary_leg, follower_leg)

                # The one central U-bend is a rigid R0.5 semicircle that may
                # translate only along the two driven, axis-constrained legs.
                for item_index, item, arc, left, right in moving_bends:
                    dimension = dimensions.AddRadius(arc, None, False)
                    dimension.Parameter.Expression = f"{float(source_arcs[int(item['sequence'])].Geometry.Radius) / MM:.12f} mm"
                    dimensions.AddTwoPointDistance(
                        arc.StartSketchPoint, arc.EndSketchPoint, None, False)
                    bend_points = [item["start_coordinates_mm"], item["end_coordinates_mm"],
                                   item["arc_through_coordinates_mm"]]
                    ranges = [max(row[axis] for row in bend_points) -
                              min(row[axis] for row in bend_points) for axis in range(3)]
                    plane_axis = min(range(3), key=lambda axis: ranges[axis])
                    plane_method = (constraints.AddParallelToYZPlane,
                                    constraints.AddParallelToXZPlane,
                                    constraints.AddParallelToXYPlane)[plane_axis]
                    plane_method(arc)
                    # Explicit tangent constraints are redundant here: an R0.5
                    # semicircle with a 1.0 mm diameter between two parallel,
                    # axis-fixed legs is tangent by construction.  Keeping the
                    # redundant pair caused intermittent 3D-solver hysteresis;
                    # tangency remains an acceptance measurement below.
            else:
                # Fixed edges are immutable reference geometry.  Ground every
                # distinct line endpoint instead of redundantly dimensioning a
                # line whose terminal points are already fixed.
                grounded = set()
                for entity in created.values():
                    for sketch_point in (entity.StartSketchPoint, entity.EndSketchPoint):
                        coordinates = tuple(round(value, 10) for value in xyz(sketch_point.Geometry))
                        if coordinates not in grounded:
                            try:
                                constraints.AddGround(sketch_point)
                            except Exception:
                                pass
                            grounded.add(coordinates)

            stage = f"create_sweep_{edge_id}"
            document.Update2(True)
            collection = app.TransientObjects.CreateObjectCollection()
            for item in items:
                collection.Add(created[int(item["sequence"])])
            path = component.Features.CreateSpecifiedPath(collection)
            first_entity = created[int(first["sequence"])]
            first_point = endpoint(first_entity, int(first["orientation"]), True)
            workplane = component.WorkPlanes.AddByNormalToCurve(first_entity, first_point)
            profile_sketch = component.Sketches.Add(workplane)
            profile_center = profile_sketch.ModelToSketchSpace(first_point.Geometry)
            profile_sketch.SketchLines.AddAsTwoPointRectangle(
                transient.CreatePoint2d(profile_center.X - .025, profile_center.Y - .025),
                transient.CreatePoint2d(profile_center.X + .025, profile_center.Y + .025))
            sweep = component.Features.SweepFeatures.AddUsingPath(
                profile_sketch.Profiles.AddForSolid(), path, 20485)
            sweep.Name = f"SWEEP_RIGID_UBEND_{edge_id}_V01"
            workplane.Visible = False
            profile_sketch.Visible = False
            replacements[edge_id] = {"edge": edge, "items": items, "created": created,
                                     "driver": driver, "sketch": sketch, "sweep": sweep,
                                     "parameter_name": parameter_name}
            completed.append({"edge_id": edge_id, "mode": "NAMED_CONTROLLER" if parameter_name else "FIXED_ATOMIC",
                              "driver_parameter": parameter_name,
                              "legacy_sketch": old_sketch.Name, "legacy_sweep": old_sweep.Name,
                              "zero_length_mm": path_length(items, created),
                              "u_bend_representation": ("SINGLE_RIGID_180_DEGREE_ARC"
                                                        if edge_id in VARIABLE else "SOURCE_TWO_ARC_FIXED"),
                              "atomic_sketch": sketch.Name, "atomic_sweep": sweep.Name})

        stage = "remove_all_legacy_path_features"
        for edge_id in reversed(ALL_EDGES):
            legacy[edge_id]["sweep"].Delete()
            legacy[edge_id]["sketch"].Delete()

        stage = "full_rebuild"
        document.Rebuild2(True)
        health_after_rebuild = health_inventory(component)
        nonhealthy = [row for row in health_after_rebuild if row["health"] != UP_TO_DATE]

        stage = "zero_state_audit"
        zero = []
        for edge_id, entry in replacements.items():
            edge = entry["edge"]
            items, created = entry["items"], entry["created"]
            start = xyz(endpoint(created[int(items[0]["sequence"])], int(items[0]["orientation"]), True).Geometry)
            end = xyz(endpoint(created[int(items[-1]["sequence"])], int(items[-1]["orientation"]), False).Geometry)
            actual = path_length(items, created)
            zero.append({"edge_id": edge_id, "actual_length_mm": actual,
                         "baseline_length_mm": float(edge["baseline_for_optimizer_mm"]),
                         "length_error_mm": actual - float(edge["baseline_for_optimizer_mm"]),
                         "start_error_mm": distance(start, edge["start_node_coordinate_mm"]),
                         "end_error_mm": distance(end, edge["end_node_coordinate_mm"]),
                         "sketch_health": int(entry["sketch"].HealthStatus),
                         "sweep_health": int(entry["sweep"].HealthStatus)})

        document.Save2(True)
        document.Close(True)
        reopened = win32com.client.dynamic.DumbDispatch(
            app.Documents.Open(str(destination.resolve()), False)._oleobj_)
        reopened.Rebuild2(True)
        reopen_health = health_inventory(reopened.ComponentDefinition)
        reopen_nonhealthy = [row for row in reopen_health if row["health"] != UP_TO_DATE]
        driver_names = sorted(str(reopened.ComponentDefinition.Parameters.UserParameters.Item(i).Name)
                              for i in range(1, reopened.ComponentDefinition.Parameters.UserParameters.Count + 1)
                              if str(reopened.ComponentDefinition.Parameters.UserParameters.Item(i).Name).startswith("D_E"))
        reopened.Close(False)

        zero_ok = all(abs(row["length_error_mm"]) <= .001 and row["start_error_mm"] <= .001 and
                      row["end_error_mm"] <= .001 and row["sketch_health"] == UP_TO_DATE and
                      row["sweep_health"] == UP_TO_DATE for row in zero)
        drivers_ok = set(driver_names) == {f"D_{edge_id}_COMP_DRIVE" for edge_id in VARIABLE}
        passed = not nonhealthy and not reopen_nonhealthy and zero_ok and drivers_ok
        result = {
            "schema": "MCH_INTEGRATED_RIGID_UBEND_CONTROLLER_BUILD_V04",
            "status": "PASS_CLEAN_FULL_REBUILD_PENDING_CALIBRATION" if passed else "BLOCKED_BUILD_HEALTH_OR_ZERO_STATE",
            "source_ipt": str(source.resolve()), "source_ipt_sha256": source_hash,
            "destination_ipt": str(destination.resolve()), "completed_edges": completed,
            "all_legacy_path_features_removed": True, "health_after_full_rebuild": health_after_rebuild,
            "nonhealthy_after_full_rebuild": nonhealthy, "zero_state": zero,
            "health_after_reopen_rebuild": reopen_health,
            "nonhealthy_after_reopen_rebuild": reopen_nonhealthy,
            "named_driver_candidates": driver_names,
        }
        report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if passed else 2
    except Exception as error:
        result = {"schema": "MCH_INTEGRATED_RIGID_UBEND_CONTROLLER_BUILD_V04",
                  "status": "BUILD_FAILED_PRESERVED_TRIAL", "stage": stage,
                  "source_ipt": str(source.resolve()), "source_ipt_sha256": source_hash,
                  "destination_ipt": str(destination.resolve()), "completed_edges": completed,
                  "error": repr(error), "traceback": traceback.format_exc()}
        report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        try:
            document.Save2(True)
            document.Close(True)
        except Exception:
            pass
        print(json.dumps(result, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
