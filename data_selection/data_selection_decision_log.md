# Data Selection Decision Log

## 2026-07-09 - HPRC Local-Coverage Target Priority Queue

Repo: `2026 Technosignature Search`

Data: Breakthrough Listen HPRC full target metadata from the committed full
seed CSV (`data/bl_hprc_full_seed_targets.csv`) plus tracked local acquisition
provenance (`docs/data_collection_status.json`).

Role: `live_search`

Acquisition mode: `metadata_only`

Decision: Build and commit `data_selection/target_priority_queue.csv` before
future raw live-search pulls. This queue ranks targets using metadata-first
local-coverage novelty, not model score and not stratified-random sampling.

Reason: `docs/SYSTEMATIC_SEARCH_PLAN.md` Step 3a requires a detection-optimized
novel-target selector. The project already has real full HPRC metadata and a
tracked record of targets acquired or skipped by the current extended-corpus
process, but that information was not assembled into a GitHub-visible target
queue.

Guardrails:
- This queue is a scheduling aid only.
- Raw download is not authorized from this queue alone.
- Product type, cadence, URI, estimated download size, and storage impact must
  be verified before any raw acquisition batch.
- Follow-up scoring remains zero until a real unresolved candidate exists.
- Live-search rows must not be used for model training unless demoted into a
  future training manifest and excluded from later blind-search claims.

Artifacts:
- `data/bl_hprc_full_seed_targets.csv`
- `data/bl_hprc_full_targets_vizier.csv`
- `data_selection/target_priority_queue.csv`
- `src/techno_search/target_priority_queue.py`

## 2026-08-02 - Hunter PROD Live 5-New / 5-Follow-Up Acceptance

Repo: `2026 Technosignatures`

Data: Ten priority-ranked Breakthrough Listen HDF5 search targets frozen by the
installed `TechnoHunter` application: five never-before-searched targets
(`HIP2`, `HIP1444`, `HIP57866`, `HIP5938`, `HIP6748`) and five later-epoch
three-ON/three-OFF follow-up cadences (`HIP103096`, `HIP111313`, `HIP113357`,
`HIP11565`, `HIP117712`).

Role: `live_search` for the New manifest and `followup_live_search` for the
Follow-up manifest. These observations are forbidden from training,
calibration, threshold selection, or frozen evaluation.

Acquisition mode: `stream_process_evict`

Estimated download GB: `8.551058` total (`1.219611` New plus `7.331447`
Follow-up).

Actual download GB: pending completion; the durable data-collection status and
acceptance evidence bundle will record the observed total.

Free space before: `327 GiB` on `/System/Volumes/Data`.

Free space after: pending completion; the permanent 100 GB project cap and
10 GB conservative reserve remain enforced.

Training priority score: not applicable; this is live-search data and cannot be
used as labeled training data.

Live search priority score: canonical manifest ranking values
`0.537316-0.538042` for New and `0.997193` follow-up priority for all five
Follow-up targets. The values are deterministic relative ranking scores, not
calibrated probabilities.

Storage cost penalty: `1` for the combined bounded batch (`>5 GB` and
`<=25 GB`).

Why this data: the installed adaptive selector chose the best available five
decision-grade novel targets, while the installed follow-up selector found five
complete real later-epoch cadences that address durable evidence gaps. The
operator explicitly approved the bounded 8.55 GB transfer.

Why not alternatives: no targets were hand-picked or substituted. The exact
ordered manifests were frozen from the canonical queues after current source,
identity, cross-project-history, eligibility, ranking, and sufficiency checks.

Why this acquisition mode: all raw products are public and re-downloadable;
stream/process/evict preserves manifests, provenance, candidate evidence, and
results without retaining the 8.55 GB raw working set.

Eviction or pin rule: evict raw HDF5 products immediately after successful
candidate-report generation. Pin compact manifests, checksums, provenance,
results, failure records, event ledgers, and acceptance evidence.

Leakage risks: these live observations must remain outside training,
validation, calibration, and frozen-evaluation datasets. No unlabeled result
may be converted into ground truth or a positive technosignature label.

Manifests:

- `results/prod_acceptance_v3/searches/SEARCH-20260803T013540Z-9EC480FD/manifest.json`
- `results/prod_acceptance_v3/searches/SEARCH-20260803T013634Z-84635BDA/manifest.json`

Expected scientific or model-hardening value: five current novel-target
results and five later-epoch cadence dispositions, with durable known,
unknown, or unresolved states and explicit limitations. No detection,
discovery, expert-review, external-validation, or submission claim is allowed.

Citations: Breakthrough Listen Open Data Archive; Painter et al. (2024); Pardo
et al. (2025), as listed in `docs/astrometrics_data_selection_policy.md`.
