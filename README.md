# Pristine Source-Only CAD Deployment

This minimal package regenerates and validates a controller-ready baseline and
the 70/30, 80/20, and 90/10 optimized IPT files from the original pristine IPT
and three optimized CAD input length matrices.

For a new topology, the deployment user supplies only one pristine IPT and one
or more already-generated optimized target length-matrix CSV files. The
optimizer source code and its incidence, concentration, baseline-length, and
other input files are not part of the CAD deployment contract.

The package intentionally contains no pre-generated controller-ready IPT,
optimized IPT, audit JSON, recipe, release manifest, or other run output. Always
write execution results to a separate versioned working directory outside this
package.

## Package contents

- `AGENTS.md`
  - Project-level Codex instructions automatically discovered when Codex starts
    in this directory
- `CODEX_NEW_TOPOLOGY_ONBOARDING.md`
  - Task brief and execution runbook for adapting this workflow to a new
    inlet/outlet topology
- `input/microchannel_network_2_5_param_sweep_new_pilot.ipt`
  - Original pristine IPT
  - SHA-256:
    `D43A42DD930B2982BE993EE3D54A12DEDB7CE19D0E7F7033F3CEC2D70F422995`
- `input/length_mat/*.csv`
  - Three optimized CAD input length matrices for 70/30, 80/20, and 90/10
- `scripts/*.py`
  - Scripts for controller construction, calibration, reopen auditing, global
    clearance inspection, and recipe compilation
- `scripts/materialize_canonical_contract.py`
  - A self-contained script that materializes the checksum-bound Proposal 2
    mapping contract inside the run workspace

For a new topology, start Codex in this directory and ask it to read
`AGENTS.md` and `CODEX_NEW_TOPOLOGY_ONBOARDING.md` completely before beginning.
The bundled contract materializer is valid only for the bundled reference IPT;
Codex must compile and bind a new contract for a different pristine IPT.

## New-topology input contract

Work from a copy of this package. Replace the contents of `input/` with only:

```text
input/
├─ <one-pristine-model>.ipt
└─ length_mat/
   ├─ <optimized-target-1>.csv
   ├─ <optimized-target-2>.csv
   └─ ...
```

Each target CSV must contain a unique ordered edge identifier and its desired
path length, preferably in columns named `edge` and `length`. Every target must
contain the same edge IDs in the same order. Target filenames are used only as
opaque target names; concentration values do not have to be encoded in them.
Case-only edge forms such as `e1` are normalized and audited as canonical `E01`.

Do not add `electric_analogy_programming.py`, an incidence matrix, concentration
files, a separate baseline `length_mat.csv`, or the optimizer repository. The
target matrices are assumed to be final upstream optimization results. Codex
measures the pristine baseline directly from the IPT, maps the CSV edges to CAD
paths, identifies changed serpentines, and adapts the scripts in a separate
versioned working directory.

Because target matrices contain lengths but not network connectivity, a new
highly symmetric topology can leave more than one valid CAD-edge mapping. In
that case Codex must present annotated candidate mappings and obtain human
confirmation before modifying CAD.

Suggested Codex request:

```text
Read AGENTS.md and CODEX_NEW_TOPOLOGY_ONBOARDING.md completely. Use only the
pristine IPT and optimized target length-matrix CSV files under input/. Adapt
the CAD workflow in a new versioned working directory, validate the mapping and
all geometry gates, and produce one qualified optimized IPT per target. Do not
run or reconstruct the optimizer and do not modify the deployment inputs.
```

The commands below are the exact reference run for the IPT and three matrices
already bundled in this directory. For a different topology, follow the
configuration-driven onboarding brief instead of reusing its Proposal 2
contract, fixed edge counts, matrix names, or checksums.

## Requirements

- Windows and Autodesk Inventor Professional 2026
- Python 3 with `pywin32`
- Write access to the package and run locations
- All previously opened test or source IPT documents saved and closed before
  starting

An Inventor COM timeout, modal dialog, or server-start failure is an execution
failure, not a geometry pass. Close Inventor cleanly and restart in a new
versioned run directory.

## Prepare an external run directory

Open PowerShell in the directory containing this README. Set `$run` to a new,
nonexistent location outside the deployment package.

```powershell
$pkg = (Get-Location).Path
$run = "Y:\User\jeongwooklim\microchannel_exp\deployment_runs\pristine_run_YYYYMMDD_v01"
$scripts = Join-Path $pkg "scripts"
$source = Join-Path $pkg "input\microchannel_network_2_5_param_sweep_new_pilot.ipt"
$matrixDir = Join-Path $pkg "input\length_mat"

New-Item -ItemType Directory -Path $run | Out-Null
New-Item -ItemType Directory -Path (Join-Path $run "reference") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $run "optimized") | Out-Null
```

Do not reuse an old run directory or overwrite prior results. Failed or partial
results are diagnostic evidence and must be preserved. Start a new versioned run
for each retry.

## 1. Materialize the Proposal 2 contract

```powershell
$contract = Join-Path $run "reference\canonical_length_contract_proposal_2.json"

python (Join-Path $scripts "materialize_canonical_contract.py") `
  --output $contract
```

The reported contract SHA-256 must be:

`7DFABD36B1DE0B830E1DCD0F6DD806BE66DA89271E253B727666AE62A1D75E61`

## 2. Build a controller-ready baseline

```powershell
$baseline = Join-Path $run "controller_ready_baseline.ipt"

python (Join-Path $scripts "build_integrated_native_atomic_controllers.py") `
  $source $contract $baseline
```

Inspect the generated `controller_ready_baseline.json`. It must show:

- Status `PASS_CLEAN_FULL_REBUILD_PENDING_CALIBRATION`
- 26 completed edges
- Exactly 12 named controller parameters
- Controller names following `D_E##_COMP_DRIVE`
- Zero-state path-length and terminal errors within tolerance
- Health status `11778` for all relevant features after save and reopen

This status confirms controller construction only. It is not production
approval.

## 3. Run full-range calibration and mixed-target tests

```powershell
$qualificationIpt = Join-Path $run "range_qualification_workcopy.ipt"
$m70 = Join-Path $matrixDir "new_length_mat_target_70_30_bound_m5p5_p2_pso_v01.csv"
$m80 = Join-Path $matrixDir "new_length_mat_target_80_20_bound_m5p5_p2_pso_v01.csv"
$m90 = Join-Path $matrixDir "new_length_mat_target_90_10_bound_m5p5_p2_pso_v01.csv"

python (Join-Path $scripts "qualify_atomic_controller_range.py") `
  $baseline $contract $qualificationIpt $m70 $m80 $m90

$rawQualification = Join-Path $run "range_qualification_workcopy.json"
```

The qualification covers the inherited CAD path-length interval of `-5.5/+2.0 mm`,
the CAD guard range, return to zero, monotonicity, terminal positions, radius,
pitch, planarity, tangency, feature health, and the three simultaneous mixed
targets before and after reopen.

## 4. Apply the release regate

```powershell
$rawRegate = Join-Path $run "rigid_ubend_regate_raw.json"

python (Join-Path $scripts "regate_rigid_ubend_qualification.py") `
  $rawQualification $rawRegate
```

If the result is `PASS_RIGID_UBEND_RANGE_AND_REOPEN_PENDING_CLEARANCE`, use
`$rawRegate` in the clearance stage.

## 5. Run independent reapplication only when justified

Use this recovery path only when the raw regate is blocked exclusively by
cumulative ramp cache/reference-history behavior. It must never waive a real
path-length, terminal, radius, pitch, planarity, tangency, feature-health, or
clearance failure.

```powershell
$independentDir = Join-Path $run "independent_reapplication"
$independentEvidence = Join-Path $run "independent_reapplication.json"

python (Join-Path $scripts "audit_independent_controller_reapplication.py") `
  --baseline $baseline `
  --contract $contract `
  --raw-qualification $rawQualification `
  --qualification-script (Join-Path $scripts "qualify_atomic_controller_range.py") `
  --output-dir $independentDir `
  --evidence $independentEvidence

$releaseRegate = Join-Path $run "rigid_ubend_regate_release.json"

python (Join-Path $scripts "promote_regate_with_independent_evidence.py") `
  --regate $rawRegate `
  --independent-evidence $independentEvidence `
  --output $releaseRegate
```

The promoted evidence must report 12/12 calibration passes and 3/3 mixed-target
passes. If the raw regate already passed, use:

```powershell
$releaseRegate = $rawRegate
```

## 6. Generate optimized IPT files and audit global clearance

```powershell
$clearanceManifest = Join-Path $run "global_clearance_manifest.json"

python (Join-Path $scripts "audit_rigid_ubend_release_clearance.py") `
  --baseline $baseline `
  --contract $contract `
  --regate $releaseRegate `
  --output-dir (Join-Path $run "optimized") `
  --manifest $clearanceManifest
```

This stage generates the three optimized IPT files. Require all of the
following:

- Status `PASS_ALL_RELEASE_TARGET_CLEARANCE`
- 3/3 target passes
- Stable physical-geometry signatures across two consecutive reopens
- Path, driver, terminal, and feature-health gates passing
- Inter-path and nonadjacent self-path wall clearances both `>= 0 mm`

The release gate uses wall clearance after subtracting channel width, not raw
centerline distance.

## 7. Compile a checksum-bound QUALIFIED recipe

```powershell
$baselineHash = (Get-FileHash -LiteralPath $baseline -Algorithm SHA256).Hash.ToLowerInvariant()
$recipeRoot = Join-Path $run "controller_recipes"
$recipe = Join-Path $recipeRoot "$baselineHash\recipe.json"
$releaseManifest = Join-Path $run "release_manifest.json"

python (Join-Path $scripts "compile_rigid_ubend_release_recipe.py") `
  --baseline $baseline `
  --contract $contract `
  --raw-qualification $rawQualification `
  --regate $releaseRegate `
  --clearance $clearanceManifest `
  --auditor (Join-Path $scripts "audit_rigid_ubend_release_clearance.py") `
  --recipe $recipe `
  --release-manifest $releaseManifest
```

The recipe must be emitted with status `QUALIFIED`. If the compiler emits only
a blocked release manifest, resolve every failed check before proceeding.

## 8. Run production preflight for all three matrices

```powershell
foreach ($matrix in @($m70, $m80, $m90)) {
  $name = [System.IO.Path]::GetFileNameWithoutExtension($matrix)
  python (Join-Path $scripts "preflight_controller_readiness.py") `
    --source-ipt $baseline `
    --length-mat $matrix `
    --recipe-root $recipeRoot `
    --output (Join-Path $run "preflight_$name.json")
}
```

Use the three IPT files under `optimized/` as production results only when all
three preflight reports return `PRODUCTION_READY`.

## Mandatory stop conditions

Stop production promotion if any of the following occurs:

- Pristine-IPT or contract checksum mismatch
- Controller build does not pass
- The exact set of 12 named drivers is missing or changed
- Any physical geometry acceptance gate fails
- Calibration coverage is not 12/12
- Mixed-target coverage is not 3/3
- Any global wall clearance is negative
- Optimized-IPT geometry is not stable after reopen
- A checksum-bound `QUALIFIED` recipe is not emitted
- Any target preflight is not `PRODUCTION_READY`

Never permanently delete failed or partial outputs. Move them into a dedicated
`archive/` directory if cleanup is necessary.
