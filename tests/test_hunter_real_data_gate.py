"""Regression coverage for the Phase 5 real-data evidence authority gate."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from techno_search.hunter_prod_check import (
    _REAL_DATA_ARTIFACT_ROLES,
    REAL_DATA_ACCEPTANCE_SCHEMA_VERSION,
    _real_data_bundle_problems,
)


def _valid_bundle(repo_root: Path) -> dict[str, Any]:
    evidence_dir = repo_root / "docs" / "evidence" / "phase5"
    evidence_dir.mkdir(parents=True)
    artifacts: list[dict[str, str]] = []
    hashes: dict[str, str] = {}
    for role in sorted(_REAL_DATA_ARTIFACT_ROLES):
        path = evidence_dir / f"{role}.txt"
        path.write_text(f"real evidence for {role}\n", encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[role] = digest
        artifacts.append(
            {
                "role": role,
                "path": path.relative_to(repo_root).as_posix(),
                "sha256": digest,
            }
        )

    def search(mode: str) -> dict[str, Any]:
        target_ids = [f"{mode.upper()}-{index}" for index in range(1, 6)]
        return {
            "mode": mode,
            "requested_target_count": 5,
            "target_count": 5,
            "target_ids": target_ids,
            "ordered_manifest_target_ids": target_ids,
            "executed_target_ids": target_ids,
            "manifest_sha256": hashes[
                "new_manifest" if mode == "new" else "follow_up_manifest"
            ],
            "manifest_checksum_verified": True,
            "history_updated": True,
            "source_provenance": [
                {
                    "source_identity": "authoritative public archive",
                    "watermark": "2026-08-02T00:00:00Z",
                }
            ],
            "durable_target_records": [
                {
                    "target_id": target_id,
                    "state": "completed",
                    "provenance": {"archive_product_id": target_id},
                    "result": {"disposition": "local_triage_only"},
                }
                for target_id in target_ids
            ],
            "cross_project_novelty_checked": mode == "new",
            "prior_searched_targets_selected": 0,
            "external_authority_boundary": (
                "credentialed third-party review requires separate authorization"
                if mode == "follow-up"
                else None
            ),
            "disposition_persisted": mode == "follow-up",
        }

    return {
        "schema_version": REAL_DATA_ACCEPTANCE_SCHEMA_VERSION,
        "release": {
            "app_version": "1.2.72",
            "installed_entry_point": "TechnoHunter",
            "resolved_executable": str(repo_root / "venv" / "bin" / "TechnoHunter"),
            "code_identity": {
                "git_commit": "a" * 40,
                "runtime_tree_sha256": "b" * 64,
            },
        },
        "scope": {
            "fixtures_used": False,
            "mocks_used": False,
            "imported_snapshots_used": False,
            "real_authoritative_sources": True,
            "detection_claimed": False,
            "discovery_claimed": False,
            "expert_review_claimed": False,
            "external_validation_claimed": False,
            "external_submission_allowed": False,
        },
        "operator_session": {
            "installation_command": "uv pip install --python .venv/bin/python -e .[all]",
            "terminated": True,
        },
        "searches": [search("new"), search("follow-up")],
        "restart_resume": {
            "application_restarted": True,
            "state_survived": True,
            "eligible_work_resumed": True,
            "completed_work_not_regenerated": True,
            "completed_work_not_repeated": True,
        },
        "shared_state_write": {"performed": False},
        "evidence_artifacts": artifacts,
    }


def _problems(payload: object, repo_root: Path) -> list[str]:
    return _real_data_bundle_problems(
        payload,
        repo_root=repo_root,
        expected_app_version="1.2.72",
        expected_commit="a" * 40,
        expected_runtime_sha256="b" * 64,
        expected_executable=str(repo_root / "venv" / "bin" / "TechnoHunter"),
    )


def test_complete_current_real_five_plus_five_bundle_passes(tmp_path: Path) -> None:
    assert _problems(_valid_bundle(tmp_path), tmp_path) == []


def test_one_target_controlled_fixture_cannot_satisfy_phase_five(
    tmp_path: Path,
) -> None:
    payload = _valid_bundle(tmp_path)
    payload["schema_version"] = "hunter_controlled_prod_acceptance_v1"
    payload["scope"]["fixtures_used"] = True
    payload["searches"][0]["requested_target_count"] = 1
    payload["searches"][0]["target_count"] = 1

    problems = _problems(payload, tmp_path)

    assert "schema is not hunter_prod_live_acceptance_v3" in problems
    assert "fixtures_used must be false" in problems
    assert "new requested target count is not 5" in problems
    assert "new frozen target count is not 5" in problems


def test_bundle_is_bound_to_exact_runtime_identity(tmp_path: Path) -> None:
    payload = copy.deepcopy(_valid_bundle(tmp_path))
    payload["release"]["code_identity"]["runtime_tree_sha256"] = "c" * 64

    assert "tested runtime-tree hash does not match the gate checkout" in _problems(
        payload, tmp_path
    )
