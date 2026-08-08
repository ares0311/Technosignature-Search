# Techno-Hunter

![Status](https://img.shields.io/badge/Hunter%20workflow-Implemented-blue)
![Version](https://img.shields.io/badge/version-1.2.72-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)
![Focus](https://img.shields.io/badge/focus-multimodal%20technosignature%20search-purple)

| Field | Value |
|---|---|
| Research domain | Radio, transit-photometry, infrared and spectroscopic technosignature search over public astronomical archives |
| Primary task | Given a request for `N` targets, select, freeze, execute and persist the best available `N` new or follow-up searches |
| Validated status | Implemented for deterministic local production triage; see [1.4 Verified Capability Status](#14-verified-capability-status) |
| CLI entry point | `Techno-Hunter` (compatibility alias `TechnoHunter`) |
| Data-contract versions | `hunter_search_manifest_v3`, `hunter_search_event_v2`, `hunter_follow_up_registry_v1`, `hunter_follow_up_discovery_report_v2`, `target_priority_queue_v5`, `bl_archive_candidate_catalog_v1`, `hunter_prior_search_history_v1`, `hunter_prod_check_report_v1` |
| Sibling repositories | `2026 Exoplanet Research` (EXO-Hunter), `2026 Near Earth Objects` (NEO-Hunter) |
| Canonical documentation | `docs/HUNTER_PROD_CONTRACT.md`, `docs/CLI_UX_SPEC.md`, `AGENTS.md`, `docs/PRODUCTION_READINESS.md`, `docs/SYSTEMATIC_SEARCH_PLAN.md` |
| Application version | 1.2.72 |
| License | Apache-2.0 |

## Table of Contents

- [1. Executive Summary](#1-executive-summary)
  - [1.1 Research Objective and Scientific Context](#11-research-objective-and-scientific-context)
  - [1.2 Scope, Boundaries, and Exclusions](#12-scope-boundaries-and-exclusions)
  - [1.3 System and Workflow Overview](#13-system-and-workflow-overview)
  - [1.4 Verified Capability Status](#14-verified-capability-status)
  - [1.5 Evidence and Reproducibility](#15-evidence-and-reproducibility)
- [2. CLI Tool Usage](#2-cli-tool-usage)
  - [2.1 Prerequisites](#21-prerequisites)
  - [2.2 Installation](#22-installation)
  - [2.3 Environment Setup](#23-environment-setup)
  - [2.4 Command Structure](#24-command-structure)
  - [2.5 End-to-End Workflow](#25-end-to-end-workflow)
  - [2.6 Command Reference](#26-command-reference)
  - [2.7 Outputs and Artifacts](#27-outputs-and-artifacts)
  - [2.8 Exit Codes and Failure Behavior](#28-exit-codes-and-failure-behavior)
  - [2.9 Troubleshooting](#29-troubleshooting)
- [3. Analytics, Mathematics, and Theoretical Foundation](#3-analytics-mathematics-and-theoretical-foundation)
  - [3.1 Problem Formulation](#31-problem-formulation)
  - [3.2 Inputs, Outputs, Labels, Units, and Provenance](#32-inputs-outputs-labels-units-and-provenance)
  - [3.3 Mathematical Notation](#33-mathematical-notation)
  - [3.4 Models, Algorithms, and Scores](#34-models-algorithms-and-scores)
  - [3.5 Assumptions, Objectives, and Statistical Methods](#35-assumptions-objectives-and-statistical-methods)
  - [3.6 Thresholds, Calibration, and Uncertainty](#36-thresholds-calibration-and-uncertainty)
  - [3.7 Evaluation and Validation](#37-evaluation-and-validation)
  - [3.8 Limitations and Failure Modes](#38-limitations-and-failure-modes)
  - [3.9 Implementation and Test Traceability](#39-implementation-and-test-traceability)
- [4. Sibling Repositories and Shared Data](#4-sibling-repositories-and-shared-data)
  - [4.1 Research Program and Repository Responsibilities](#41-research-program-and-repository-responsibilities)
  - [4.2 Local Discovery and Configuration](#42-local-discovery-and-configuration)
  - [4.3 Shared Artifacts, Ownership, and Access](#43-shared-artifacts-ownership-and-access)
  - [4.4 Schemas, Provenance, Versioning, and Compatibility](#44-schemas-provenance-versioning-and-compatibility)
  - [4.5 Availability, Failure Behavior, and Regeneration](#45-availability-failure-behavior-and-regeneration)
  - [4.6 Cross-Repository Safety Boundaries](#46-cross-repository-safety-boundaries)

## 1. Executive Summary

### 1.1 Research Objective and Scientific Context

Techno-Hunter searches publicly available astronomical data for signals that
cannot be explained by known natural or instrumental phenomena, and escalates
only what survives every implemented explanatory check.

The research question is narrow and falsifiable: **for a given observation, does
any completed known-explanation check supply a reliable explanation?** A run
resolves each hit-bearing observation into exactly one durable state:

- `known` — at least one completed check supplies a reliable explanation
  (catalogued pulsar, FRB, blazar or AGN, gamma-ray source, satellite or
  transmitter, terrestrial RFI, instrument artifact, cadence failure, or
  non-detection).
- `unknown` — every required check completed and none supplied an explanation.
  This is a construction from exhausted known-class checks. It is not a learned
  label, a positive technosignature class, a detection, or a discovery.
- `unresolved` — no reliable explanation was found, but a required check could
  not run or lacked required evidence. Missing catalogues, sky, time or
  observatory metadata, cadence, or provenance are never silently treated as a
  negative match.

The methodology follows the established radio-SETI literature: ON/OFF cadence
rejection after Enriquez et al. 2017 and Price et al. 2020, cross-target RFI
suppression, Earth-rotation drift-rate consistency, and the irregular-dimming
diagnostics Boyajian et al. 2016 applied to KIC 8462852 for transit photometry.
Infrared excess follows the single-temperature blackbody first pass used by
Wright et al. 2014 and Griffith et al. 2015.

There are no confirmed positive technosignature labels anywhere in this field.
The project therefore never trains a binary "technosignature versus not"
classifier and never creates, solicits, or infers labels.

### 1.2 Scope, Boundaries, and Exclusions

In scope: candidate-universe construction, canonical identity resolution,
cross-project search history, mode-specific eligibility, deterministic ranking,
adaptive discovery with persisted sufficiency evidence, exact-target freezing,
approval-gated acquisition, preprocessing, scoring, interpretation, durable
results and provenance, follow-up registration, and restart-safe resume.

Explicitly out of scope:

| Excluded | Reason |
|---|---|
| Detection or discovery claims | Requires third-party expert confirmation this repository cannot perform |
| External submission | Human-gated; no code path is authorized to transmit a candidate |
| Label creation of any kind | Prohibited by `AGENTS.md`; only pre-existing independent row-level labels are admissible |
| Synthetic training data | Models trained on synthetic signals do not transfer to real detection |
| Opaque model judgment as ranking logic | Ranking must be auditable arithmetic over real features |
| Minor-planet and orbital-debris search | Owned by the NEO-Hunter sibling |
| Exoplanet transit-parameter cataloguing | Owned by the EXO-Hunter sibling |

### 1.3 System and Workflow Overview

One integrated canonical path serves both interactive and scripted operation:

```text
request
  -> adaptive discovery
  -> source validity and provenance
  -> canonical identity and complete cross-project history
  -> mode-specific eligibility
  -> deterministic ranking
  -> sufficiency evaluation and expansion
  -> exact selected-target manifest (frozen, hashed, immutable)
  -> durable search creation
  -> approval-gated acquisition and preprocessing
  -> scoring and interpretation
  -> durable results and provenance
  -> history and follow-up update
```

The terminal application is a presentation layer over that path. It does not
duplicate selection, scoring, execution, persistence, or validation; `prod-check`
asserts this mechanically through its `canonical_routing` check.

### 1.4 Verified Capability Status

| Capability | Status | Evidence |
|---|---|---|
| Documented install and launch of the canonical executable | Implemented | `prod-check` checks `documented_installation`, `entry_points`, `launch_repo_root`, `launch_outside_repository`, `built_wheel` |
| Persistent shell with all seven required slash commands | Implemented | `src/techno_search/hunter_shell.py`; `tests/test_hunter_golden_ux.py` |
| Searchable described command palette on `/` | Implemented | `src/techno_search/hunter_commands.py`, `hunter_tables.render_palette`; `tests/golden/command_palette.txt` |
| Guided parameter entry with live validity sentinels | Implemented | `src/techno_search/hunter_validation.py`; `tests/golden/new_search_fields.txt`, `tests/golden/invalid_targets.txt` |
| Shared interactive and scriptable validators | Implemented | `hunter_validation.validate_target_count` and `validate_constraints`, used by both surfaces |
| Resolved-action preview before freezing | Implemented | `hunter_cli._build_action_preview`; `tests/golden/action_preview.txt` |
| Width-aware result table and separate detail view | Implemented | `hunter_tables.fit_columns`, `hunter_inspect.py`; `tests/golden/results_table_80_columns.txt`, `tests/golden/results_table_140_columns.txt` |
| Adaptive discovery with persisted sufficiency evidence | Implemented | `hunter_adaptive_discovery.py`; `prod-check` check `adaptive_discovery` |
| Exact-target freezing and execution | Implemented | `hunter_search.py`; `prod-check` check `exact_target_freezing` |
| Cross-project identity and history, fail-closed | Implemented | `hunter_cross_project_history.py`; `prod-check` check `identity_and_history` |
| Durable five-record model with provenance | Implemented | `prod-check` check `durable_record_kinds` |
| Restart and resume | Implemented | `prod-check` check `restart_and_resume` |
| Repository-native PROD gate | Implemented | `prod-check`, report schema `hunter_prod_check_report_v1` |
| Installed real-data New and Follow-up acceptance | Implemented | `docs/evidence/hunter_prod_live_acceptance_v3.json`; `prod-check` checks `real_data_new_workflow`, `real_data_follow_up_workflow`, `real_data_evidence` |
| Deterministic known, unknown and unresolved resolution | Implemented | `known_explanation.py`, `pipeline_runner.py`, `adversarial_review.py` |
| Semisupervised anomaly and OOD calibration | Nonconforming | No qualifying pre-existing row-level labelled source exists (`docs/seti_labeled_hit_data_research.md`); the score is ranking-only and every dependent gate fails closed |
| Archive-identity coverage for all catalogue labels | Nonconforming | 4,894 of 12,086 archive labels have no resolvable identity; they are excluded, never guessed |
| Deep-space spacecraft attribution category | Nonconforming | Earth-orbit catalogues cannot attribute deep-space probes, so such signals resolve `unresolved` rather than being mis-attributed |
| External submission and expert review | Not applicable | Human-gated outside this repository by design |

### 1.5 Evidence and Reproducibility

Every capability claim above maps to a command, a code path, and a durable
artifact. The machine-enforced gate is:

```bash
git pull origin main
.venv/bin/prod-check --include-wheel \
  --report-path artifacts/prod_check_report.json
```

It emits a `hunter_prod_check_report_v1` report and exits non-zero when any
mandatory requirement fails. A check that cannot execute is reported as
`NOT_EXECUTED` with a reason and is never counted as a pass.

Deterministic reports are retained under `docs/evidence/prod_gates/`; a report
is current only for its recorded commit and working-tree identity. The current
installed real-data bundle is
`docs/evidence/hunter_prod_live_acceptance_v3_after_gate_repair.json`; it
preserves exact New and Follow-up five-target manifests, execution, restart,
resume, provenance, and no-repeat evidence. These artifacts verify software
behavior only and make no detection, discovery, expert-review,
external-validation, or submission claim.

Retained acceptance bundles live in `docs/evidence/`. The controlled fresh-state
bundle replaces archive transport with a loopback adapter; its fixture
provenance is explicitly non-real and is rejected outside that acceptance
process. It is not observational, training, or scientific performance evidence.

## 2. CLI Tool Usage

### 2.1 Prerequisites

- Git
- `uv`
- Python 3.11 or newer; this workstation runs 3.14.3 and CI runs 3.11
- `jq` for inspecting durable JSON artifacts
- macOS `caffeinate` for long runs; omit the wrapper on systems without it

Local storage is capped at 100 GB across `data/`, `models/` and `artifacts/`
combined. Raw archive payloads are streamed, processed, and evicted; they are
never accumulated.

### 2.2 Installation

```bash
git pull origin main
UV_CACHE_DIR=.uv-cache uv pip install --python .venv/bin/python \
  -e ".[dev,radio,science,ml,track_a,photometry]"
bash scripts/patch_turbo_seti_numpy2_compat.sh --python .venv/bin/python
```

Install every extra. The run path spans acquisition, radio processing,
scientific catalogues, and validation, and a bare `pip install -e .` installs
only the core dependencies.

The turboSETI patch is required after every reinstall of the `radio` extra. It
applies an idempotent NumPy 2 correction to the pinned turboSETI 2.3.2
site-packages copy and does not modify repository source.

### 2.3 Environment Setup

```bash
git pull origin main
.venv/bin/Techno-Hunter --help
.venv/bin/Create-New-Search --help
.venv/bin/Run-New-Search --help
.venv/bin/Show-Follow-Ups --help
.venv/bin/Inspect-Target --help
.venv/bin/prod-check --help
```

Recognized environment variables:

| Variable | Effect |
|---|---|
| `NO_COLOR` | Disables colour |
| `REDUCE_MOTION`, `TECHNO_HUNTER_REDUCE_MOTION` | Disables animation |
| `CI` | Disables animation |
| `TERM=dumb` | Disables colour and animation |
| `COLUMNS` | Overrides detected terminal width |
| `TECHNO_SEARCH_ENABLE_LIVE_DATA` | Opt-in for live SIMBAD and Gaia cross-match |
| `TECHNO_LOCAL_STORAGE_CAP_GB` | Local storage cap, default 100 |

### 2.4 Command Structure

```bash
git pull origin main
.venv/bin/Techno-Hunter
```

`.venv/bin/TechnoHunter` is a registered alias for the same command. Both
console scripts resolve to the identical `techno_search.hunter_shell:main`
entry point, so they are two names for one implementation rather than two
parallel code paths; each reports its own name in `--help`.

At `TechnoHunter>`, type `/` to open the searchable command palette. Mode is
inherent in the command; no redundant `--mode` is required interactively.

```text
/New-Search <N> [scientific constraints]
/Follow-Up-Search <N> [scientific constraints]
/Run-Search [SEARCH-ID] [--approve-acquisition]
/Show-Follow-Ups [--json]
/Inspect-Target [<rank-or-id>] [--search-id SEARCH-ID] [--json]
/Help
/Exit
```

Keyboard: `/` opens the palette, typing filters it live, Up and Down move the
selection, Enter selects or executes, Tab completes or advances a field,
Shift-Tab moves back a field, Escape closes or cancels, and Ctrl-C cancels
without changing durable state.

For automation, the same commands run non-interactively through the same
canonical logic and validators:

```bash
git pull origin main
.venv/bin/Techno-Hunter --no-animation \
  --command "/Show-Follow-Ups --json" \
  --command "/Exit"
```

### 2.5 End-to-End Workflow

```bash
git pull origin main

# 1. Preview the resolved action without freezing anything.
.venv/bin/Create-New-Search --targets 5 --mode new --preview-only

# 2. Freeze the exact selection.
.venv/bin/Create-New-Search --targets 5 --mode new

# 3. Inspect why a target was selected.
.venv/bin/Inspect-Target 1

# 4. Execute the exact frozen targets. Acquisition requires explicit approval.
caffeinate -i .venv/bin/Run-New-Search --approve-acquisition

# 5. Review durable follow-ups.
.venv/bin/Show-Follow-Ups

# 6. Freeze and run a follow-up search.
.venv/bin/Create-New-Search --targets 5 --mode follow-up
caffeinate -i .venv/bin/Run-New-Search --approve-acquisition
```

Creation performs selection only; it never downloads or processes raw data.
Execution consumes the frozen manifest and never regenerates, substitutes, or
reorders targets.

### 2.6 Command Reference

| Command | Required | Optional |
|---|---|---|
| `Create-New-Search` | `--targets`, `--mode` | `--preview-only`, `--json`, `--min-ra-deg`, `--max-ra-deg`, `--min-dec-deg`, `--max-dec-deg`, `--min-abs-galactic-latitude-deg`, `--max-estimated-download-gb`, `--target-prefix`, `--candidate-catalog`, `--priority-queue`, `--scans-dir`, `--searches-dir`, `--manifest-dir` |
| `Run-New-Search` | none | `--search-id`, `--approve-acquisition`, `--json`, `--no-rich`, `--chunk-size`, `--pipeline-workers`, `--searches-dir`, `--history-file` |
| `Show-Follow-Ups` | none | `--json`, `--scans-dir`, `--searches-dir`, `--priority-queue` |
| `Inspect-Target` | none | `reference`, `--search-id`, `--searches-dir`, `--json` |
| `prod-check` | none | `--json`, `--report-path`, `--only`, `--include-wheel`, `--repo-root` |
| `Techno-Hunter` | none | `--command`, `--no-animation`, `--no-color`, `--history-file`, `--searches-dir`, `--scans-dir`, `--priority-queue`, `--acceptance-work-dir`, `--acceptance-evidence` |

`techno-search` remains the lower-level scriptable surface for pipeline,
catalogue, and diagnostic subcommands. It uses the same canonical business logic
and validation layer.

### 2.7 Outputs and Artifacts

| Artifact | Location | Schema |
|---|---|---|
| Immutable search manifest | `results/searches/SEARCH-*/manifest.json` | `hunter_search_manifest_v3` |
| Append-only lifecycle events | `results/searches/SEARCH-*/events.ndjson` | `hunter_search_event_v2` |
| Adaptive-discovery rounds | `results/adaptive_discovery/SEARCH-*/` | `target_priority_manifest_v2` |
| Target search history | `results/scan_history.ndjson` | `prod_scan_history_v1` |
| Follow-up registry | per-run production ledgers under `results/` | `hunter_follow_up_registry_v1` |
| Candidate universe | `data_selection/bl_archive_candidate_catalog.csv` | `bl_archive_candidate_catalog_v1` |
| Eligibility queue | `data_selection/target_priority_queue.csv` | `target_priority_queue_v5` |
| Cross-project history export | `data_selection/hunter_prior_search_history_v1.json` | `hunter_prior_search_history_v1` |
| Acquisition status | `docs/data_collection_status.json` | tracked manifest |
| PROD gate report | operator-specified `--report-path` | `hunter_prod_check_report_v1` |

CSV output is an export for operator review. The durable non-CSV records above
are the system of record. Requests above 100 targets write a timestamped
complete CSV export and print a concise summary instead of flooding the
terminal.

### 2.8 Exit Codes and Failure Behavior

| Code | Meaning |
|---|---|
| `0` | The requested operation completed |
| `1` | A validation, lifecycle, identity, or inspection error occurred; the message is operator-facing |
| `2` | Acquisition approval is required, or an unknown `prod-check` selector was supplied |

Failures are loud, durable, and resumable. A failed run preserves its
`run_failed` event and resumes under the same `run_id`; history is appended
exactly once. Re-running an already-completed search exits non-zero without
changing its event ledger or history. Incomplete turboSETI or pipeline output is
removed rather than counted complete, and raw input is retained for the retry.
Detailed tracebacks go to logs, never to the primary interactive response.

### 2.9 Troubleshooting

| Symptom | Cause and resolution |
|---|---|
| `APPROVAL REQUIRED` with exit 2 | Acquisition is approval-gated. Review the frozen manifest, then re-run with `--approve-acquisition`. |
| `no pending search` | `/Run-Search` needs a frozen search. Create one with `/New-Search` or `/Follow-Up-Search`. |
| `candidate queue is absent` | Regenerate with `techno-search build-target-priority-queue`. |
| turboSETI crashes immediately after a `radio` reinstall | Re-run `bash scripts/patch_turbo_seti_numpy2_compat.sh --python .venv/bin/python`. |
| Packages install but are not importable | Use `.venv/bin/python -m pip`, never `.venv/bin/pip`; a stale shim can target another interpreter. |
| Table columns are missing | The terminal is narrow. Lower-priority columns drop first and rank and identity are always retained. Widen the terminal or set `COLUMNS`. |
| `git push` prints `fatal: failed to store: 100001` | A git-lfs locking-API artifact. Verify with `git rev-parse origin/main HEAD` before retrying. |

## 3. Analytics, Mathematics, and Theoretical Foundation

### 3.1 Problem Formulation

Let `C` be the candidate universe of archive-resolvable observation targets and
`H` the union of this project's and its siblings' validated search history. For
mode `m` and request size `N`, selection returns

```text
S = argmax over T subset of E(m), |T| = min(N, |E(m)|)  of  sum over t in T of score_m(t)
```

where `E(m)` is a subset of `C` holding the mode-specific eligible targets.
Because `score_m` is computed independently per target, this reduces to ranking
`E(m)` by `score_m` and taking the first `min(N, |E(m)|)` rows under a
deterministic tie-breaker.

Relative rank and absolute quality are reported separately. Weak absolute
quality never suppresses a result; fewer than `N` rows are returned only when
fewer than `N` valid candidates exist after sufficiency-bounded exploration, and
the shortfall is reported explicitly.

### 3.2 Inputs, Outputs, Labels, Units, and Provenance

| Symbol | Meaning | Units | Source |
|---|---|---|---|
| `ra_deg`, `dec_deg` | ICRS sky position | degrees | SIMBAD name resolution or archive header |
| `galactic_latitude_deg` | Galactic latitude | degrees | `astropy.coordinates.SkyCoord` transform of `ra_deg` and `dec_deg` |
| `dist_pc` | Distance | parsec | HPRC seed catalogue, Isaacson et al. 2017 |
| `distance_light_years` | Distance | light years | derived from `dist_pc` |
| `spec_type` | Spectral type | dimensionless | HPRC seed catalogue |
| `object_type` | SIMBAD `%OTYPE(S)` classification | dimensionless | SIMBAD, never inferred from label text |
| `estimated_download_gb` | Archive product size | gigabytes | HTTP HEAD `Content-Length` preflight |
| `snr` | Detection signal-to-noise | dimensionless | turboSETI hit table |
| `drift_rate` | Doppler drift | Hz/s | turboSETI, searched to a 10 Hz/s ceiling |
| `prior_search_count` | Prior searches of this identity | count | local scan history plus validated sibling export |

There are no positive technosignature labels. `follow_up`, `unknown_candidate`,
`unknown`, anomaly, synthetic injection, Voyager, and known human transmitters
are explicitly not positive labels and are never used as training or calibration
truth.

### 3.3 Mathematical Notation

| Symbol | Definition |
|---|---|
| `N` | Requested target count, a positive integer |
| `C`, `E(m)`, `S` | Candidate universe, mode-eligible set, selected set |
| `score_m(t)` | Mode-specific ranking score: `target_selection_score` for new mode, `follow_up_priority` for follow-up mode |
| `w_i` | Configured component weight; the weights sum to one |
| `theta` | A decision threshold. Where no calibrated `theta` exists, the dependent gate fails closed. |
| `norm(x)` | Component normalization to the closed interval zero to one before weighting |

### 3.4 Models, Algorithms, and Scores

**New-mode ranking.** `target_selection_score` is computed in
`src/techno_search/background_search.py`. It is `target_priority_score` — a
weighted sum of five normalized components, less a blocking-issue penalty —
adjusted by prior review history:

```text
target_priority_score  = w_fu  * followup_value               (w_fu  = +0.35)
                       + w_nov * novelty_score                (w_nov = +0.25)
                       + w_dq  * data_quality_score           (w_dq  = +0.20)
                       + w_obs * observability_score          (w_obs = +0.10)
                       + w_fpp * false_positive_probability   (w_fpp = -0.30)
                       - min(0.25, 0.05 * blocking_issue_count)

target_selection_score = target_priority_score
                       + never_reviewed_target_boost   (+0.08, if never reviewed)
                       - min(max_prior_review_penalty,
                             prior_review_penalty_per_entry * prior_review_count)
```

All weights are the defaults in `DEFAULT_PRIORITY_WEIGHTS` and are overridable
through the background priority config; the persisted
`priority_config_version` field records which configuration produced a score.
`false_positive_probability` carries a negative weight, so a higher
false-positive probability lowers the score.

`data_quality` includes a continuous Galactic-latitude term derived by exact
`astropy` transform of each row's own RA and declination. This term exists for a
measured reason: before it was added, only 2 distinct scores existed across
4,835 eligible candidates, so any top-`N` request returned an essentially
arbitrary alphabetical subset of a multi-thousand-way tie. After it was added,
4,408 of 4,825 rows carried a distinct score and the largest remaining tie group
was 4 rows. Scores are persisted at 6 decimal places so real precision survives
into the ranking key. Ties break deterministically on `canonical_target_id`.

**Follow-up-mode ranking.** `follow_up_priority` ranks durable follow-up ledger
evidence and maps each unresolved condition to a concrete next-observation ask
rather than a bare number.

**Adaptive discovery.** `adaptive_discovery_loop` in
`src/techno_search/hunter_adaptive_discovery.py` expands metadata discovery only
while an unresolved candidate could still displace the current `N`th result,
doubling the examined batch each round. It terminates on
`top_n_supported_no_candidate_can_displace_nth`, `accessible_universe_exhausted`,
or `no_eligibility_progress`, which raises rather than silently narrowing the
search. Each round persists examined counts, minimum examined score, artifact
hashes, and top-`N` membership churn.

**Known-explanation resolution.** Track A runs the four local source catalogues
(ATNF pulsars, CHIME/FRB, Roma-BZCAT, Fermi 4FGL), time-and-direction-specific
satellite matching via SGP4, RFI database overlap, instrument-artifact scoring,
ABACAB cadence validation, detector-threshold evidence, and provenance checks.
Track B resolves `known`, `unknown`, or `unresolved` from those structured checks
alone. An `unknown` result automatically writes an adversarial-review dossier in
the same run.

**Transit photometry.** Box Least Squares via
`astropy.timeseries.BoxLeastSquares`, plus a from-scratch median and MAD
aperiodic dip detector that fits per-event ingress and egress slopes and does not
assume periodicity.

**Infrared excess.** A single-temperature blackbody fitted to the WISE W1 and W2
colour using verified Wright et al. 2010 Table 1 zero points of
309.54, 171.79, 31.676 and 8.3635 Jy at 3.4, 4.6, 12 and 22 micrometres, then
predicting W3 and W4 and reporting observed-minus-predicted significance in real
per-source uncertainty units.

### 3.5 Assumptions, Objectives, and Statistical Methods

- Target selection optimizes detection probability, not population
  representativeness. Stratified sampling is retained only to defend a null
  result against a cherry-picking charge, never as the primary selector.
- False positive is the default hypothesis. A candidate advances only by failing
  every implemented explanatory check.
- Ranking is auditable arithmetic over real features. No opaque model judgment
  participates in core ranking.
- Absent evidence is never a negative match. A check that cannot run yields
  `unresolved`.
- The single-temperature blackbody infrared fit is a documented first-pass
  approximation, not a Kurucz or BT-Settl grid fit.
- Scores are normalized per component before weighting so no single unit
  dominates by scale.

### 3.6 Thresholds, Calibration, and Uncertainty

| Threshold | Value | Basis |
|---|---|---|
| Doppler drift ceiling | 10 Hz/s | Resolvable first drift bin of the `.0002` products; a lower ceiling is refused |
| Detection SNR | turboSETI configured threshold, preserved in the frozen manifest | Detector configuration, not a post-hoc cut |
| Earth-rotation drift consistency | 0.44 Hz/s/GHz | Standard geometric expectation |
| ABACAB cadence | signal present in ON scans and absent in OFF scans | Enriquez et al. 2017, Price et al. 2020 |
| Local storage cap | 100 GB | Operator constraint, enforced before every payload download |
| Anomaly and OOD threshold | none exists | No qualifying labelled source; the score is ranking-only and dependent gates fail closed |

`target_selection_score` is a deterministic relative ranking value. It is not a
calibrated probability and carries no absolute eligibility interpretation; the
frozen manifest records this in its own `selection.quality.interpretation` field.
Legacy `false_positive_probability` fields are uncalibrated routing scores; new
artifacts name the field `routing_false_positive_score` and preserve the
calibration record.

Uncertainty is propagated where real per-source uncertainty exists: AllWISE
`w3sigmpro` and `w4sigmpro` feed infrared significance directly, with a
documented 10 percent relative fallback when absent.

### 3.7 Evaluation and Validation

```bash
git pull origin main
caffeinate -i .venv/bin/python scripts/run_parallel_validation.py
.venv/bin/prod-check --include-wheel \
  --report-path artifacts/prod_check_report.json
```

`run_parallel_validation.py` is the canonical full-validation entry point: six
pytest-xdist workers operating as six non-overlapping `loadfile` shards with
aggregated package coverage, then Ruff, mypy, `validate-all`, directive-parity,
no-fake-completion, app-version, and verification-freshness checks concurrently.

The Phase 6 production-runtime denominator is recorded in
`docs/evidence/prod_gates/phase7_coverage_full_runtime_python.json`: 20,735
statements across 144 Python files, comprising all 124 files under
`src/techno_search` and 20 production scripts (including every registered entry
point plus shell/TUI, routing, orchestration, persistence, configuration,
`scripts/bl_fetch.py`, `scripts/ingest_gbt_cadence.py`, and
`scripts/run_parallel_validation.py`). The measured result was 15,372 covered
statements, 5,363 missing, or 74.135520%. Repository-owned shell launchers are
separately inventoried and SHA-256 checked; no 100% coverage claim is made.

Validated recovery evidence includes injection-recovery on real backgrounds and
a historical replay that recovered 13 of 13 known sources across four catalogues
with a correct negative control. Independent rediscovery of KIC 8462852's
documented 0.88-day periodicity across 12 of 18 real Kepler quarters, each
correctly classified non-transit by the harmonic-versus-transit statistic, is
real validation of that statistic on genuine data and is not a detection claim.

### 3.8 Limitations and Failure Modes

- No calibrated anomaly or OOD threshold exists, so every gate requiring one
  fails closed. The exhausted literature search is recorded in
  `docs/seti_labeled_hit_data_research.md`; do not reopen it without a genuinely
  new already-labelled source.
- 4,894 of 12,086 archive labels have no resolvable identity and are excluded
  rather than guessed.
- 95 resolved non-HIP stellar candidates are outside the HPRC-keyed discovery
  sweep.
- Earth-orbit satellite catalogues cannot attribute deep-space spacecraft, so
  Voyager-class signals resolve `unresolved` rather than being mis-attributed.
- BLS run on a single 90-day Kepler quarter cannot distinguish a genuinely
  periodic signal from three coincidentally spaced one-off dips. The aperiodic
  dip detector, not the periodic fit, is the correct tool for such events.
- The infrared model is a single-temperature blackbody approximation, and
  natural-contaminant rejection for dust, debris disks, and AGN is
  caller-supplied rather than computed.
- Cross-instrument transfer is unproven: a MeerKAT-trained scorer did not
  discriminate meaningfully on GBT L-band data.
- Live acquisition depends on current public-archive availability. Source
  failures remain explicit and durable and never trigger a silent data-source
  substitution.

### 3.9 Implementation and Test Traceability

| Claim | Implementation | Validation |
|---|---|---|
| Best-available-`N` with honest shortfall | `hunter_search.create_search` | `tests/test_hunter_search.py` |
| Exact-target freezing and execution | `hunter_search.run_search` | `tests/test_hunter_search.py`; `prod-check` `exact_target_freezing` |
| Adaptive sufficiency and expansion | `hunter_adaptive_discovery.py` | `tests/test_hunter_adaptive_discovery.py`; `prod-check` `adaptive_discovery` |
| Follow-up discovery and lifecycle | `hunter_follow_up_discovery.py` | `tests/test_hunter_follow_up_discovery.py` |
| Cross-project identity and history | `hunter_cross_project_history.py`, `target_alias.py` | `tests/test_hunter_cross_project_history.py` |
| Known, unknown and unresolved resolution | `known_explanation.py`, `pipeline_runner.py` | `tests/test_known_explanation.py`, `tests/test_end_to_end.py` |
| Adversarial dossier on `unknown` | `adversarial_review.py` | `tests/test_adversarial_review.py` |
| Command palette, guided entry, tables | `hunter_shell.py`, `hunter_commands.py`, `hunter_tables.py` | `tests/test_hunter_golden_ux.py`, `tests/golden/` |
| Shared validators | `hunter_validation.py` | `tests/test_hunter_golden_ux.py` |
| Target detail view | `hunter_inspect.py` | `tests/test_hunter_golden_ux.py` |
| Ranking formula integrity | `background_search.py`, `target_priority_queue.py` | `tests/test_target_priority_queue.py` |
| Restart and resume | `hunter_search.py` | `tests/test_hunter_search.py`; `prod-check` `restart_and_resume` |
| PROD gate | `hunter_prod_check.py` | `tests/test_hunter_installation_gate.py`, `tests/test_hunter_pty_gate.py`, `tests/test_hunter_real_data_gate.py`, `tests/test_hunter_prod_state_authority.py` |
| Controlled fresh-state acceptance | `hunter_acceptance.py` | `tests/test_hunter_controlled_acceptance.py` |
| No label-creation path exists | not applicable | `tests/test_no_label_creation_paths.py`, `tests/test_labeled_data_only_directive.py` |

## 4. Sibling Repositories and Shared Data

### 4.1 Research Program and Repository Responsibilities

Three independently sandboxed repositories form one Astrometrics search program.
Each is independently buildable, testable, and runnable. Techno-Hunter New mode
additionally requires decision-grade, read-only history exports from all three
Hunters before it may claim that a target is novel; Follow-up mode remains
available when that novelty precondition is unavailable.

| Repository | Hunter | Scientific responsibility | Identity space |
|---|---|---|---|
| `2026 Technosignatures`, this repository | Techno-Hunter | Technosignature search across radio, transit photometry, infrared and spectroscopy | Stellar catalogues: HIP, GJ, HD, BD, TIC |
| `2026 Exoplanet Research` | EXO-Hunter | Exoplanet detection and characterization | Stellar catalogues: TIC, HIP |
| `2026 Near Earth Objects` | NEO-Hunter | Near-Earth object discovery and close-approach analysis | Minor-planet designations |

Techno-Hunter and EXO-Hunter share the TIC and HIP stellar identity space, so
cross-project novelty exclusion between them is scientifically necessary.
NEO-Hunter uses a disjoint minor-planet identity space, but its versioned export
still participates in the three-project federation so that disjointness and
history completeness are demonstrated rather than assumed.

### 4.2 Local Discovery and Configuration

Sibling discovery is repository-relative, never an absolute personal path:
`hunter_search.cross_project_history_federation_validity` validates this
repository's export and uses
`hunter_cross_project_history.sibling_history_export_path` to resolve both
sibling exports from this repository's own location. Lower-level queue tooling
also supports an explicit read-only file path:

```bash
git pull origin main

# Direct EXO example, when siblings are checked out side by side.
techno-search build-target-priority-queue --cross-project-sibling exo_hunter

# File-copy fallback, for environments where they are not.
techno-search build-target-priority-queue \
  --cross-project-history-path data_selection/cross_project_imports/exo.json
```

The canonical New path performs the three-project check automatically. Publish
this repository's own export with:

```bash
git pull origin main
.venv/bin/techno-search export-cross-project-history
```

### 4.3 Shared Artifacts, Ownership, and Access

| Artifact | Producer | Permitted readers | Access |
|---|---|---|---|
| `data_selection/hunter_prior_search_history_v1.json` | Each Hunter publishes its own | Sibling Hunters, read-only | Repository-relative path or operator copy |
| Search manifests, event ledgers, follow-up registries | This repository | This repository only | Local durable records |
| `docs/data_collection_status.json` | This repository | This repository only | Tracked manifest |

Each Hunter publishes only records it owns and consumes sibling records
read-only. A matched cross-project target receives the same novelty adjustment as
one this project scanned itself, reusing the existing
`prior_review_penalty_per_entry` configuration rather than a new weight, and
records a `cross_project_prior_search` audit column.

### 4.4 Schemas, Provenance, Versioning, and Compatibility

The interoperability contract is `hunter_prior_search_history_v1` with
`schema_version` one. Loading is fail-closed: a wrong schema version, a
non-object payload, or a malformed entry raises rather than degrading silently.

History validity is one of five states, and `stale-but-usable` can never justify
a known-incomplete novelty decision:

| State | New-mode eligibility |
|---|---|
| `valid` | Eligible |
| `stale-but-usable` | Completed entries remain visible but cannot justify a known-incomplete novelty decision |
| `refresh-required` | Fails closed |
| `invalid` | Fails closed |
| `unknown` | Fails closed |

`target_priority_queue_v5` and `hunter_search_manifest_v3` bind the candidate
catalogue and eligibility queue with independent hashes and counts, so the
universe is never conflated with the eligibility stage. Older manifest versions
remain readable for historical audit and are never rewritten.

### 4.5 Availability, Failure Behavior, and Regeneration

An absent sibling export resolves to `unknown`; a present but malformed export
resolves to `invalid`. Either condition fails New selection closed with an
actionable error instead of treating absence as evidence of novelty. A pending
sibling search from an incompatible application version is classified
`refresh-required` and cannot justify a New eligibility decision. Follow-up
selection does not make a novelty claim and remains available.

Regenerate derived artifacts with:

```bash
git pull origin main
.venv/bin/techno-search build-target-priority-queue
.venv/bin/techno-search export-cross-project-history
```

Immutable frozen search manifests are never regenerated. Completed historical
evidence remains durable across every regeneration.

### 4.6 Cross-Repository Safety Boundaries

Exactly one repository is writable per session. This repository never modifies,
formats, migrates, commits, branches, pushes, or opens a pull request in a
sibling. There are no runtime imports of sibling code, no cross-repository
symlinks, no hard-coded personal paths, and no undocumented filesystem
dependencies. Sibling access is limited to reading the read-only,
schema-versioned history export named above.

The `sibling_write_isolation` check in `prod-check` enforces this mechanically by
scanning every module under `src/` and `scripts/` for absolute personal paths,
sibling runtime imports, and unguarded writes near sibling path resolution.

No output of this repository constitutes a detection, discovery, expert review,
external validation, or authorization for external submission.
