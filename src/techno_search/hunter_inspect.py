"""Read-only target detail resolution for ``/Inspect-Target``.

``docs/CLI_UX_SPEC.md`` UX-TABLE-02 requires a separate detail view carrying
canonical identity, scientific metrics, score components, selection reason,
provenance, prior-search evidence, resource requirements, and limitations.

This module only *reads* durable records already written by the canonical
lifecycle. It never selects, scores, executes, or persists, so it cannot become a
shadow production path (contract PIPE-02).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class TargetInspectionError(ValueError):
    """The requested target reference could not be resolved from durable records."""


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TargetInspectionError(f"durable manifest is unreadable: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise TargetInspectionError(f"durable manifest is not a JSON object: {path}")
    return payload


def latest_search_manifest(searches_dir: Path) -> tuple[str, dict[str, Any]]:
    """Resolve the most recently created durable search manifest."""
    if not searches_dir.is_dir():
        raise TargetInspectionError(
            f"no durable searches directory exists: {searches_dir}"
        )
    manifests = sorted(searches_dir.glob("SEARCH-*/manifest.json"))
    if not manifests:
        raise TargetInspectionError(
            "no durable search exists yet — create one with /New-Search or "
            "/Follow-Up-Search"
        )
    latest = manifests[-1]
    return latest.parent.name, _load_manifest(latest)


def resolve_search_manifest(
    searches_dir: Path, search_id: str | None
) -> tuple[str, dict[str, Any]]:
    """Resolve a specific durable search, or the most recent one."""
    if search_id is None:
        return latest_search_manifest(searches_dir)
    path = searches_dir / search_id / "manifest.json"
    if not path.is_file():
        raise TargetInspectionError(f"unknown search ID: {search_id}")
    return search_id, _load_manifest(path)


def _matches(target: Mapping[str, Any], reference: str) -> bool:
    needle = reference.replace(" ", "").casefold()
    for key in ("hip", "target_id", "name", "canonical_target_id"):
        value = target.get(key)
        if value and str(value).replace(" ", "").casefold() == needle:
            return True
    return False


def resolve_target(
    manifest: Mapping[str, Any], reference: str
) -> tuple[int, dict[str, Any]]:
    """Resolve a rank number or catalog identity to one frozen target."""
    targets = list(manifest.get("targets", []))
    if not targets:
        raise TargetInspectionError("this search froze no targets")
    stripped = reference.strip()
    if stripped.isdigit():
        rank = int(stripped)
        if not 1 <= rank <= len(targets):
            raise TargetInspectionError(
                f"rank {rank} is out of range — this search has {len(targets)} target(s)"
            )
        return rank, dict(targets[rank - 1])
    for rank, target in enumerate(targets, start=1):
        if _matches(target, stripped):
            return rank, dict(target)
    raise TargetInspectionError(
        f"no frozen target matches {reference!r} in this search"
    )


_METRIC_FIELDS = (
    ("ra_deg", "Right ascension (deg)"),
    ("dec_deg", "Declination (deg)"),
    ("galactic_latitude_deg", "Galactic latitude (deg)"),
    ("distance_light_years", "Distance (light years)"),
    ("spectral_type", "Spectral type"),
    ("object_type", "SIMBAD object type"),
    ("exoplanet_host", "Exoplanet host"),
)

_SCORE_FIELDS = (
    ("target_selection_score", "target_selection_score (ranking key)"),
    ("background_target_priority_score", "background_target_priority_score"),
    ("total_priority", "total_priority (policy audit sum)"),
    ("follow_up_priority", "follow_up_priority"),
    ("prior_search_count", "prior_search_count"),
)


def build_target_detail(
    *, search_id: str, manifest: Mapping[str, Any], rank: int, target: Mapping[str, Any]
) -> dict[str, Any]:
    """Assemble the detail payload from durable manifest evidence only."""
    mode = str(manifest.get("mode", "new"))
    selection = manifest.get("selection", {})
    quality = selection.get("quality", {}) if isinstance(selection, dict) else {}
    aliases = target.get("aliases") or target.get("cross_project_prior_search") or ""

    metrics = {
        label: target[key] for key, label in _METRIC_FIELDS if target.get(key) not in (None, "")
    }
    components = {
        label: target[key] for key, label in _SCORE_FIELDS if target.get(key) not in (None, "")
    }

    provenance = [
        f"producing project: {manifest.get('created_by_project', 'unknown')}",
        f"search created: {manifest.get('created_at_utc', 'unknown')}",
        f"code commit: {manifest.get('code_commit', 'unknown')}",
        f"app version: {manifest.get('app_version', 'unknown')}",
        f"manifest schema: {manifest.get('schema_version', 'unknown')}",
        f"queue status: {target.get('queue_status', 'unknown')}",
        f"data products: {target.get('data_products_available', 'unknown')}",
    ]
    if target.get("source_hdf5_url"):
        provenance.append(f"archive product: {target['source_hdf5_url']}")
    if target.get("prior_seti_coverage_reference"):
        provenance.append(
            f"prior SETI coverage reference: {target['prior_seti_coverage_reference']}"
        )

    prior_evidence: list[str] = []
    summary = target.get("prior_search_provenance_summary")
    if summary:
        prior_evidence.append(str(summary))
    cross = target.get("cross_project_prior_search")
    if cross:
        prior_evidence.append(f"cross-project prior search: {cross}")
    raw_provenance = target.get("prior_search_provenance")
    if isinstance(raw_provenance, list):
        prior_evidence.extend(str(item) for item in raw_provenance[:5])
    if not prior_evidence:
        prior_evidence.append("no prior search recorded for this identity")

    limitations = [
        (
            "target_selection_score is a deterministic relative ranking value, not a "
            "calibrated probability or an absolute eligibility threshold"
        ),
        (
            "the semisupervised anomaly score is an uncalibrated ranking diagnostic and "
            "cannot define the known-versus-unknown boundary"
        ),
        (
            "selection is local scientific triage only — no detection, discovery, "
            "expert-review, external-validation, or submission claim follows"
        ),
    ]
    if isinstance(quality, dict) and quality.get("interpretation"):
        limitations.insert(0, str(quality["interpretation"]))

    return {
        "search_id": search_id,
        "mode": mode,
        "rank": rank,
        "canonical_identity": str(
            target.get("hip") or target.get("target_id") or "unknown"
        ),
        "aliases": str(aliases) if aliases else "none recorded",
        "execution_kind": target.get("execution_kind", "unknown"),
        "selection_reason": target.get("selection_reason", "not recorded"),
        "scientific_metrics": metrics,
        "score_components": components,
        "provenance": provenance,
        "prior_search_evidence": prior_evidence,
        "resource_requirements": [
            f"estimated download: {float(target.get('estimated_download_gb') or 0.0):.3f} GB",
            (
                "raw payload retention: "
                f"{manifest.get('acquisition', {}).get('raw_retention_policy', 'unknown')}"
            ),
        ],
        "limitations": limitations,
    }


def inspect_target(
    *, searches_dir: Path, reference: str, search_id: str | None = None
) -> dict[str, Any]:
    """Resolve one frozen target's full durable detail."""
    resolved_id, manifest = resolve_search_manifest(searches_dir, search_id)
    rank, target = resolve_target(manifest, reference)
    return build_target_detail(
        search_id=resolved_id, manifest=manifest, rank=rank, target=target
    )


def list_inspectable_targets(
    *, searches_dir: Path, search_id: str | None = None
) -> dict[str, Any]:
    """Return the frozen selection table for the resolved search."""
    resolved_id, manifest = resolve_search_manifest(searches_dir, search_id)
    return {"search_id": resolved_id, **manifest}
