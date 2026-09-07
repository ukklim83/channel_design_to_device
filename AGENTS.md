# Codex Project Instructions: Pristine-IPT CAD Onboarding and Release

## Scope

These instructions govern every Codex task started in this deployment directory
or one of its subdirectories. The deployment input contract is intentionally
minimal: one pristine Autodesk Inventor IPT and one or more already-generated
optimized target length-matrix CSV files.

The optimizer program, incidence matrix, concentration inputs, baseline length
matrix, and optimizer execution environment are not deployment inputs. Target
optimization is assumed to have been completed upstream. This workflow converts
the supplied target lengths into validated optimized IPT files.

Read `CODEX_NEW_TOPOLOGY_ONBOARDING.md` completely before beginning a new
topology onboarding run.

## Required input contract

Require only:

```text
input/
├─ <one-pristine-model>.ipt
└─ length_mat/
   ├─ <optimized-target-1>.csv
   └─ <one-or-more-additional-targets>.csv
```

- Require exactly one pristine IPT and at least one target CSV.
- Treat every CSV as an immutable, already-optimized CAD target.
- Require columns `edge` and `length`, unless Codex documents and validates an
  equivalent unambiguous schema before CAD mutation.
- Require unique edge IDs, finite positive lengths, and identical edge sets and
  row order in every target matrix.
- Normalize case-only forms such as `e1` to canonical `E01` without changing row
  order, and record the normalization in the input audit.
- Preserve the CSV edge order. Do not reorder rows by CAD sketch name, spatial
  position, or measured length.
- Derive target IDs from filenames and treat them as opaque strings.
- Do not require or attempt to reconstruct optimizer objectives, concentrations,
  incidence matrices, inlet/outlet indices, or `changing_edges`.
- Do not run an optimizer or modify a supplied target matrix in this workflow.

## Safety and artifact preservation

- Never modify the pristine source IPT or target CSVs in place.
- Never overwrite a prior controller-ready IPT, optimized IPT, report, recipe,
  manifest, screenshot, or validation directory.
- Create a new versioned sibling working directory for every onboarding or retry.
- Do not permanently delete failed or partial outputs. Move them into an
  explicitly named `archive/` directory when cleanup is necessary.
- Record SHA-256 checksums for every input and released artifact.
- Treat Inventor COM timeout, modal state, server-start failure, incomplete
  rebuild, or uncertain save/reopen state as blocked, never as a geometry pass.

## Edge identity and variable-edge discovery

- Logical edge IDs and their order come directly from the target CSVs.
- Inventory the pristine IPT read-only and measure every continuous CAD path.
- Reconstruct a CAD path graph from terminal and junction coordinates.
- Map CSV edge IDs to CAD paths using the established edge-order rule when it is
  encoded in the geometry, plus topology, terminal position, path length,
  direction, and line/arc sequence evidence.
- Measure the pristine CAD path length for every accepted mapping. This measured
  value is the baseline; no separate baseline `length_mat.csv` is required.
- A candidate variable edge is one whose supplied target length differs from its
  measured pristine CAD baseline in any target. An edge unchanged in every target
  is fixed.
- Only a mapped edge with the validated serpentine geometry may receive a driver.
  An unchanged serpentine remains fixed, and a changed non-serpentine is a stop
  condition requiring design review.
- Do not classify an edge as fixed merely because its length is `3 mm`.
- If geometry symmetry or missing topology metadata leaves more than one credible
  CSV-to-CAD mapping, stop before mutation. Present an annotated top view,
  candidate mapping table, and the exact ambiguity for human confirmation.

## Geometry invariants

For every variable serpentine:

- Ground or fully constrain both terminal positions and terminal directions.
- Keep serpentine pitch fixed and every U-bend radius at `0.5 mm`.
- Preserve the path plane and coincident/tangent arc-leg continuity.
- Treat a proven two-arc source semicircle as one rigid 180-degree U-bend;
  otherwise retain its two source arcs as one rigid constrained unit.
- Permit the rigid U-bend to translate along the declared leg axis only.
- Allow only the two internal vertical legs to change length.
- Name the controller `D_E##_COMP_DRIVE`, using the mapped CSV edge ID.
- Use `target_length = measured_baseline_length + 2 * driver_stroke`, and verify
  the relation from rebuilt CAD rather than trusting the formula alone.

Do not silently reinterpret nonmatching geometry or repair it by changing channel
width, height, pitch, radius, terminal location, or unrelated constraints.

## Bounds and physical qualification

- Use the inherited path-length delta qualification interval
  `[-5.5, +2.0] mm` and corresponding two-leg driver interval
  `[-2.75, +1.0] mm`.
- Compute each target delta from the measured pristine CAD baseline.
- Reject a target outside the qualified interval before applying it to Inventor.
- Treat the interval as a required CAD test domain, not automatic approval for a
  new spatial layout.
- Requalify actual path length, terminal position/direction, radius, pitch,
  planarity, tangency, feature health, return to zero, save/reopen stability,
  inter-path clearance, and nonadjacent self-path clearance.
- Reuse the distributed reference channel dimensions and acceptance policies only
  after confirming the pristine IPT uses the same geometry family.

## Contract and mapping requirements

- Inventory all 3D sketches, entities, sweeps, terminals, junctions, lengths,
  line/arc sequences, planes, radii, dependencies, and feature health read-only.
- Produce a mapping table containing CSV edge ID/order, CAD sketch/path, measured
  baseline length, every target length/delta, terminal coordinates, serpentine
  signature, confidence, and evidence.
- Continue automatically only when the mapping is unique and internally
  consistent. Human confirmation of an ambiguous mapping becomes part of the
  contract evidence.
- Compile a new canonical contract bound to the pristine-IPT checksum and every
  target-CSV checksum.
- Never reuse the bundled Proposal 2 contract, controller recipe, or source hash
  for a different pristine IPT or changed target set.

## Script adaptation requirements

Treat distributed scripts as a reference implementation. Create adapted copies
in a new versioned working directory; do not edit the release template in place.

Remove topology-specific hardcoding, including fixed edge counts, the existing
12-edge variable tuple, fixed `12/12` or `3/3` gates, concentration-name parsing,
E24-specific checks, and previous checksums or artifact paths.

Derive instead:

- `all_edges` and edge order from the validated target matrices;
- `variable_edges` from target-versus-measured-baseline deltas;
- `fixed_edges` as the complement;
- expected calibration count from `len(variable_edges)`;
- target count and names from discovered CSV files;
- recipe location from the new controller-ready IPT checksum.

Fail closed on missing inputs, duplicate/inconsistent edge IDs, target row-order
changes, incomplete CAD-path coverage, ambiguous mapping, unexpected drivers,
out-of-range targets, checksum mismatch, or incomplete qualification.

## Required workflow order

1. Audit the one pristine IPT and all target CSVs; record paths and checksums.
2. Validate target schemas, edge IDs/order, dimensions, and numeric values.
3. Inventory pristine Inventor geometry read-only and measure baseline paths.
4. Propose the CSV-edge to CAD-path mapping and classify variable/fixed edges.
5. Obtain human confirmation only when the mapping is not uniquely supported.
6. Compile the new checksum-bound canonical contract.
7. Adapt scripts to the discovered topology and target set.
8. Construct a controller-ready IPT in a new work copy.
9. Audit zero-state rebuild, save, close, reopen, and measured baselines.
10. Validate every target delta and fixed-edge value against measured CAD.
11. Calibrate every variable edge over the full driver range and return to zero.
12. Test every supplied mixed target simultaneously.
13. Independently reapply only eligible cache/reference-history failures.
14. Generate one optimized IPT per target and audit topology-aware clearance.
15. Compile a checksum-bound `QUALIFIED` recipe.
16. Run production preflight for every target.
17. Assemble and verify a source-only package containing only intended inputs,
    scripts, and documentation.

Do not skip a failed gate or edit evidence to manufacture a passing state.

## Independent reapplication and clearance policy

Independent reapplication is allowed only when evidence shows a failure is
caused exclusively by cumulative Inventor solver/cache/reference history. Begin
from a fresh zero-state baseline, apply one target deterministically, require two
identical reopen geometry signatures, and rerun every physical gate. Never use
this route to waive a real geometry, health, or clearance failure.

Measure wall clearance after subtracting channel width. Exclude only legitimate
shared-terminal neighborhoods and configured connected local graph neighbors.
Keep nonadjacent self-path entities eligible. Require nonnegative inter-path and
nonadjacent self-path wall clearance for every released target.

## Completion criteria and reporting

The work is complete only when the minimal input contract passes; mapping is
unique or human-approved; the exact named-driver set passes clean rebuild/reopen;
every variable edge passes full-range calibration; every supplied target passes
geometry, stability, and clearance gates; a checksum-bound `QUALIFIED` recipe is
emitted; and every target preflight reports `PRODUCTION_READY`.

Keep generated IPTs and evidence outside the source-only package. In the final
report, distinguish measured facts, heuristic inferences, and human-approved
decisions, and list released IPT/checksums, controller checksum, recipe,
clearances, preflight status, and archived failed-trial locations.
