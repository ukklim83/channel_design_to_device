# Codex Task Brief: Onboard a New Microchannel Topology

## Start the task

Create a copy of this deployment package, replace the contents of `input/` with
one pristine IPT and the already-generated optimized target length matrices, and
start Codex with that copied directory as the workspace root. Send:

```text
Read AGENTS.md and CODEX_NEW_TOPOLOGY_ONBOARDING.md completely. Using only the
pristine IPT and optimized target length-matrix CSV files under input/, adapt the
distributed CAD workflow in a new versioned working directory and continue
through qualified optimized-IPT production. Do not run or reconstruct the
optimizer. Preserve all inputs, follow every fail-closed gate, and pause only for
a genuinely ambiguous CSV-edge-to-CAD-path mapping or another decision that
cannot be resolved from measured evidence.
```

`AGENTS.md` supplies persistent project rules. This file supplies the concrete
objective, minimal input contract, phases, and deliverables.

## Objective

Starting only from a pristine Autodesk Inventor IPT and one or more optimized
target length matrices, produce:

- an accepted CSV-edge-to-CAD-path mapping;
- a measured pristine-CAD baseline and variable/fixed edge classification;
- a pristine-and-target-checksum-bound canonical contract;
- configuration-driven adapted scripts;
- a controller-ready baseline IPT;
- one validated optimized IPT per supplied target;
- calibration, reopen, history-reapplication, and clearance evidence;
- a checksum-bound `QUALIFIED` recipe;
- `PRODUCTION_READY` preflight reports for every target.

## Minimal expected inputs

```text
input/
├─ <new-pristine-model>.ipt
└─ length_mat/
   ├─ <optimized-target-1>.csv
   ├─ <optimized-target-2>.csv
   └─ ...
```

Do not request or copy an optimizer repository, `electric_analogy_programming.py`,
`incidence_mat.csv`, concentration inputs, `changing_edges`, inlet/outlet index
arrays, or a separate baseline `length_mat.csv`. The supplied target matrices
are upstream results and are not generated or edited here.

Require exactly one IPT and at least one CSV. Before Inventor mutation, report
absolute paths and SHA-256 checksums and validate:

- each matrix has an unambiguous `edge`/`length` schema;
- edge IDs are unique and lengths are finite and positive;
- every matrix has the same edge IDs in the same row order;
- case-only IDs such as `e1` can be deterministically normalized to `E01`, with
  the conversion recorded in the audit;
- target IDs derived from filenames are unique;
- no required input is missing.

## Fixed CAD assumptions

- CSV row order is authoritative for logical edge order.
- Variable serpentines use the validated two-equal-leg rigid U-bend family.
- Path-length delta qualification bounds are `[-5.5, +2.0] mm`.
- Driver-stroke qualification bounds are `[-2.75, +1.0] mm`.
- Channel dimensions, U-bend radius, pitch, wall-clearance policy, and geometry
  tolerances match the distributed reference workflow.
- Bounds define the required CAD qualification domain; they do not bypass
  new-layout clearance or reopen testing.

Verify these assumptions against the new pristine IPT and stop on a conflict.

## Phase 1: audit and normalize target matrices

Discover all target CSVs dynamically. Build a preliminary configuration using
their common edge order and opaque filename-derived target IDs. Preserve target
values exactly. Do not infer concentrations from filenames and do not execute an
optimizer.

Create an input audit recording file size, checksum, schema, row count, edge
order, duplicate/missing IDs, numeric validity, and cross-target consistency.

## Phase 2: inventory the pristine IPT read-only

Use the Inventor API to record all 3D sketches, entities, sweeps and references,
ordered line/arc geometry, terminal/junction coordinates, entity/path lengths,
arc centers/radii/normals/angles, planes, constraints, dependencies, and health.
Never save over the pristine IPT.

Reconstruct continuous CAD paths and measure their baseline lengths. Store
machine-readable evidence externally and render an annotated top view.

## Phase 3: map CSV edges to CAD paths

The target CSVs define edge names/order and desired lengths, but do not by
themselves define graph connectivity. Therefore:

1. Build the CAD graph from measured terminals and clustered junctions.
2. Apply the established edge-order convention only when the CAD geometry
   provides enough evidence to reproduce it.
3. Match CSV rows to CAD paths using path count, order evidence, terminal
   coordinates, baseline/target length compatibility, first/last direction, and
   line/arc/serpentine signatures.
4. Record confidence and all evidence for every assignment.
5. Continue automatically only when exactly one internally consistent mapping
   remains.

If symmetry or missing logical topology leaves multiple credible mappings, stop
before CAD mutation. Present annotated candidate mappings and request a human
choice. This is a required safety gate, not an execution failure.

For the accepted mapping, use the measured pristine CAD length as baseline.
Classify an edge as variable when any target differs from that baseline within
the configured comparison tolerance; otherwise classify it as fixed. A variable
edge must have the expected serpentine signature. Do not use a `3 mm` value alone
to decide whether an edge is fixed.

Compile a canonical contract bound to the pristine IPT and every target CSV
checksum.

## Phase 4: adapt scripts in a new directory

Copy the distributed scripts into a new versioned work directory. Do not edit
the deployment template. Remove previous-topology assumptions:

- 26 total edges;
- the previous 12-edge variable set;
- fixed 12/12 and 3/3 pass counts;
- hardcoded concentration tag parsing;
- E24-specific policy;
- previous topology checksums and paths.

Derive all edge/target lists and counts from the validated matrices and new
contract. Derive variable edges from target-versus-measured-baseline deltas and
fixed edges as the complement. Treat target IDs as opaque strings. Run Python
syntax checks and non-Inventor tests before CAD construction.

## Phase 5: build the controller-ready baseline

On a new IPT work copy:

- preserve fixed paths source-faithfully;
- build one named rigid U-bend translator per variable edge;
- ground terminal positions and directions;
- hold pitch, radius, plane, coincidence, and tangency;
- merge a proven two-arc semicircle into one rigid 180-degree arc, or retain a
  rigid two-arc unit when equivalence cannot be proven;
- create exactly one `D_E##_COMP_DRIVE` per variable edge;
- remove legacy path features only after validated replacements exist;
- full rebuild, save, close, reopen, and rebuild.

Require full path coverage, the exact driver set, zero-state length/terminal
tolerances, and healthy features before and after reopen. Construction success
remains pending calibration.

## Phase 6: validate target feasibility

For every edge and target, compare the supplied target with the measured
pristine-CAD baseline. Require:

- complete unique edge coverage and preserved order;
- fixed-edge targets equal to measured baseline within tolerance;
- changes only on validated variable serpentines;
- every variable delta within `[baseline - 5.5, baseline + 2.0] mm`;
- valid numeric results.

Do not change an invalid matrix and do not send it to Inventor. Report the exact
edge, baseline, target, delta, and failed rule.

## Phase 7: qualify the full CAD domain

For every variable edge, test the complete driver interval, guard points,
intermediate points, all target-specific points, and return to zero. Measure
actual length, terminals, radius, arc length/angle/normal, pitch, off-axis motion,
planarity, tangent kink, feature health, monotonicity, and save/reopen
repeatability.

Apply all variable drivers simultaneously for every supplied target and repeat
the geometry and reopen gates. Required coverage is dynamic: all classified
variable edges and all discovered target matrices.

## Phase 8: handle eligible history failures

Only a failure caused exclusively by cumulative Inventor cache/reference history
may be retried from a fresh zero-state baseline with deterministic independent
reapplication. Require two consecutive stable reopen signatures and every
physical gate. Never waive a genuine geometry, health, or clearance failure.

## Phase 9: generate optimized IPTs and audit clearance

Generate one optimized IPT per target. Apply bounded ramping, rebuild/save/reopen,
require stable physical signatures, and audit path length, driver values,
terminals, feature health, inter-path clearance, and nonadjacent self-path
clearance.

Use wall clearance after subtracting channel width. Exclude connected local
geometry only under the configured graph-neighborhood rule. Every released wall
clearance must be nonnegative.

## Phase 10: compile and preflight release

Compile a new `QUALIFIED` recipe binding the controller-ready IPT, canonical
contract, input target matrices, raw qualification, release regate, clearance
evidence, optimized IPTs, and per-edge driver/bound data by checksum. Store it
under the controller-ready checksum. Require `PRODUCTION_READY` preflight for
every target.

## Phase 11: package source-only artifacts

Create a new deployment containing only:

- the pristine IPT;
- supplied optimized target length matrices;
- adapted Python scripts needed for CAD construction and validation;
- `AGENTS.md`, this task brief, and the README.

Do not include optimizer programs or optimizer input datasets. Keep generated
controller-ready/optimized IPTs, JSON evidence, recipes, screenshots, and
manifests in a separate validation/release directory.

## Mandatory stop conditions

Stop and report on a missing/inconsistent input, ambiguous mapping, unmatched or
duplicate CAD path, variable edge without the expected serpentine, out-of-range
target, checksum mismatch, unexpected driver, build/reopen-health failure,
incomplete calibration/target coverage, physical geometry failure, negative wall
clearance, unstable reopen geometry, non-`QUALIFIED` recipe, or non-
`PRODUCTION_READY` preflight.

## Required final report

Report input and output paths/checksums; accepted mapping and its evidence;
measured baselines; variable/fixed edge lists; controller-ready checksum;
calibration and target pass counts; optimized IPT paths/checksums; minimum
inter-path and nonadjacent self-path wall clearances; recipe and preflight status;
archived failed trials; and any human-approved mapping decision.
