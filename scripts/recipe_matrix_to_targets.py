#!/usr/bin/env python3
"""Translate a legacy ``edge,length`` matrix using a qualified recipe.

This is the portable boundary between the optimizer and CAD.  It deliberately
does not know any d### dimensions: only recipe-approved named drivers may be
emitted, and every fixed edge is checked against its immutable baseline.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def read_matrix(path: Path) -> dict[str, float]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    result = {}
    for row in rows:
        match = re.fullmatch(r"E?(\d{1,2})", row["edge"].strip().upper())
        if not match:
            raise ValueError(f"unrecognised edge identifier: {row['edge']!r}")
        result[f"E{int(match.group(1)):02d}"] = float(row["length"])
    if set(result) != {f"E{i:02d}" for i in range(1, 27)}:
        raise ValueError("matrix must contain exactly E01 through E26")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", type=Path, required=True)
    ap.add_argument("--length-mat", type=Path, required=True)
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--gate-report", type=Path, required=True)
    args = ap.parse_args()
    if args.targets.exists() or args.gate_report.exists():
        raise SystemExit("Refusing overwrite of targets or gate report")
    recipe = json.loads(args.recipe.read_text(encoding="utf-8"))
    if recipe.get("status") != "QUALIFIED":
        raise SystemExit("Recipe is not QUALIFIED")
    values = read_matrix(args.length_mat)
    rows, failures = [], []
    for edge in recipe["edges"]:
        eid, value = edge["edge_id"], values[edge["edge_id"]]
        if edge["mode"] == "VARIABLE":
            delta = (value - float(edge["baseline_length_mm"])) / 2.0
            if delta < float(edge["driver_min_mm"]) - 1e-8 or delta > float(edge["driver_max_mm"]) + 1e-8:
                failures.append(f"{eid}: driver {delta:.9f} outside qualified range")
            rows.append({"edge_id":eid, "target_length_mm":f"{value:.12f}",
                         "driver_parameter":edge["driver_parameter"],
                         "driver_delta_mm":f"{delta:.12f}", "mode":"VARIABLE"})
        else:
            base = float(edge["fixed_baseline_mm"])
            if abs(value-base) > 1e-6:
                failures.append(f"{eid}: fixed target {value:.9f} != {base:.9f}")
            rows.append({"edge_id":eid, "target_length_mm":f"{value:.12f}",
                         "driver_parameter":"", "driver_delta_mm":"", "mode":"FIXED"})
    args.targets.parent.mkdir(parents=True, exist_ok=True)
    with args.targets.open("w", newline="", encoding="utf-8") as stream:
        out = csv.DictWriter(stream, fieldnames=list(rows[0])); out.writeheader(); out.writerows(rows)
    report = {"schema":"MCH_RECIPE_MATRIX_GATE_V01", "created_utc":datetime.now(timezone.utc).isoformat(),
              "recipe":str(args.recipe.resolve()), "length_mat":str(args.length_mat.resolve()),
              "cad_apply_permitted":not failures, "failures":failures,
              "variable_edge_count":sum(r["mode"]=="VARIABLE" for r in rows), "edge_count":len(rows)}
    args.gate_report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print("PASS" if not failures else "FAIL", args.gate_report)
    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    main()
