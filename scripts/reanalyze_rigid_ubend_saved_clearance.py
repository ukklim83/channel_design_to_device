"""Re-audit saved targets with split inter-path and topology-aware self gates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import win32com.client


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest().upper()


def load_auditor(path: Path):
    spec = importlib.util.spec_from_file_location("rigid_clearance", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--auditor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Refusing overwrite: {args.output}")

    source = json.loads(args.source_manifest.read_text(encoding="utf-8-sig"))
    contract = json.loads(args.contract.read_text(encoding="utf-8-sig"))
    audit = load_auditor(args.auditor)
    nodes = []
    for edge in contract["edges"]:
        for point in (edge["start_node_coordinate_mm"], edge["end_node_coordinate_mm"]):
            if not any(audit.distance(point, known) <= 1.0e-6 for known in nodes):
                nodes.append(point)

    typed_app = win32com.client.Dispatch("Inventor.Application")
    app = win32com.client.dynamic.DumbDispatch(typed_app._oleobj_)
    results = []
    for target in source["targets"]:
        workcopy = Path(target["target_workcopy"])
        resolved_workcopy = str(workcopy.resolve())
        document = None
        opened_here = True
        for index in range(1, app.Documents.Count + 1):
            candidate = app.Documents.Item(index)
            if str(candidate.FullFileName).lower() == resolved_workcopy.lower():
                document = win32com.client.dynamic.DumbDispatch(candidate._oleobj_)
                opened_here = False
                break
        if document is None:
            document = win32com.client.dynamic.DumbDispatch(
                app.Documents.Open(resolved_workcopy, False)._oleobj_)
        try:
            for _ in range(3):
                document.Rebuild2(True)
            component = document.ComponentDefinition
            health_rows = audit.health(component)
            paths = []
            for index in range(1, component.Sketches3D.Count + 1):
                sketch = component.Sketches3D.Item(index)
                paths.append((sketch.Name, audit.entity_rows(sketch)))

            self_best = (float("inf"), None)
            inter_best = (float("inf"), None)
            for index, (_, left_rows) in enumerate(paths):
                current_self = audit.nearest_pair(left_rows, left_rows, nodes, same_path=True)
                if current_self[0] < self_best[0]:
                    self_best = current_self
                for _, right_rows in paths[index + 1:]:
                    current_inter = audit.nearest_pair(left_rows, right_rows, nodes)
                    if current_inter[0] < inter_best[0]:
                        inter_best = current_inter

            self_wall = self_best[0] - audit.CHANNEL_WIDTH_MM
            inter_wall = inter_best[0] - audit.CHANNEL_WIDTH_MM
            health_ok = all(row["health"] == audit.UP_TO_DATE for row in health_rows)
            self_ok = self_wall >= audit.REQUIRED_WALL_CLEARANCE_MM
            inter_ok = inter_wall >= audit.REQUIRED_WALL_CLEARANCE_MM
            passed = health_ok and self_ok and inter_ok
            results.append({
                "target": target["target"],
                "target_workcopy": str(workcopy.resolve()),
                "target_workcopy_sha256": digest(workcopy),
                "inter_path": {"min_centerline_mm": inter_best[0],
                               "wall_clearance_mm": inter_wall,
                               "witness": inter_best[1], "passed": inter_ok},
                "nonadjacent_self_path": {"min_centerline_mm": self_best[0],
                                          "wall_clearance_mm": self_wall,
                                          "witness": self_best[1], "passed": self_ok},
                "nonhealthy_after_rebuild": [row for row in health_rows
                                             if row["health"] != audit.UP_TO_DATE],
                "health_passed": health_ok,
                "passed": passed,
            })
        finally:
            if opened_here:
                document.Close(True)

    all_passed = all(row["passed"] for row in results)
    result = {
        "schema": "MCH_RIGID_UBEND_SAVED_TARGET_CLEARANCE_V03",
        "status": "PASS_ALL_RELEASE_TARGET_CLEARANCE"
        if all_passed else "BLOCKED_ONE_OR_MORE_RELEASE_TARGETS",
        "source_manifest": str(args.source_manifest.resolve()),
        "source_manifest_sha256": digest(args.source_manifest),
        "contract": str(args.contract.resolve()),
        "contract_sha256": digest(args.contract),
        "policy": {
            **source["policy"],
            "self_path_local_graph_hops_excluded": audit.SELF_PATH_LOCAL_GRAPH_HOPS,
            "local_geometry_covered_by_release_regate": [
                "radius", "pitch", "tangency", "planarity", "feature_health"
            ],
        },
        "targets": results,
        "pass_count": sum(row["passed"] for row in results),
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "pass_count": result["pass_count"],
                      "output": str(args.output.resolve())}, indent=2))
    return 0 if all_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
