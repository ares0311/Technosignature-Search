"""Exact installed-entry-point acceptance for the canonical Hunter lifecycle."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from techno_search import __version__
from techno_search.hunter_acceptance import CONTROLLED_ACCEPTANCE_SCHEMA_VERSION

COMMITTED_EVIDENCE = Path(
    "docs/evidence/hunter_v1_2_71_controlled_acceptance.json"
)
CURRENT_RELEASE_EVIDENCE = Path(
    "docs/evidence/hunter_v1_2_72_controlled_acceptance.json"
)


def _loopback_bind_allowed() -> bool:
    """Report whether this host lets a process listen on loopback.

    The acceptance harness stands up a throwaway ThreadingHTTPServer to stand
    in for the archive. Sandboxes commonly deny ``bind()``, which stops the
    harness before it does any work.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def test_installed_hunter_controlled_prod_acceptance_is_fresh_and_complete(
    tmp_path: Path,
) -> None:
    if not _loopback_bind_allowed():
        pytest.skip(
            "NOT EXECUTED — this host denies socket bind() on loopback, so the "
            "acceptance harness cannot start. Run it outside the sandbox with "
            "scripts/run_acceptance.sh; contract CLAIM-03 forbids counting this "
            "as a pass."
        )
    work_dir = tmp_path / "fresh_state"
    evidence_path = tmp_path / "acceptance.json"
    executable = Path(
        shutil.which("Techno-Hunter")
        or ".venv/bin/Techno-Hunter"
    )

    completed = subprocess.run(
        [
            str(executable),
            "--acceptance-work-dir",
            str(work_dir),
            "--acceptance-evidence",
            str(evidence_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == CONTROLLED_ACCEPTANCE_SCHEMA_VERSION
    assert evidence["release"]["app_version"] == __version__
    assert evidence["release"]["installed_entry_point"] == "Techno-Hunter"
    assert evidence["request"] == {
        "new": {"mode": "new", "target_count": 1},
        "follow_up": {"mode": "follow-up", "target_count": 1},
    }
    assert evidence["selected_targets"] == {
        "new": ["OUTSIDE"],
        "follow_up": ["OUTSIDE"],
    }
    assert evidence["discovery_coverage"]["expansion_report"]["round_count"] == 1
    assert evidence["validity_report"]["excluded"] == {
        "INVALID": "invalid",
        "PRIOR": "ineligible_new_due_to_prior_search",
        "STALE": "refresh-required",
    }
    assert evidence["search_runs"]["new"]["event_sequence"] == [
        "created",
        "run_started",
        "run_completed",
    ]
    assert evidence["search_runs"]["follow_up"]["event_sequence"] == [
        "created",
        "run_started",
        "run_failed",
        "run_resumed",
        "run_completed",
    ]
    assert all(item["passed"] for item in evidence["assertion_results"])
    assert evidence["follow_up_state"]["history_record_count"] == 2
    assert evidence["detection_claimed"] is False
    assert evidence["discovery_claimed"] is False
    assert evidence["expert_review_claimed"] is False
    assert evidence["external_validation_claimed"] is False
    assert evidence["external_submission_allowed"] is False

    required_portable_sections = {
        "request",
        "discovery_coverage",
        "validity_report",
        "provenance_trace",
        "ranking_evidence",
        "selected_targets",
        "search_runs",
        "follow_up_state",
        "assertion_results",
        "embedded_artifacts",
    }
    assert required_portable_sections <= set(evidence)
    assert "$ACCEPTANCE_WORK_DIR" in json.dumps(evidence["transcript"])
    assert str(work_dir) not in json.dumps(evidence)
    assert evidence["embedded_artifacts"]["observation_provenance"][
        "classification"
    ] == "controlled_acceptance_fixture"
    assert evidence["embedded_artifacts"]["new_candidate_interpretation"][
        "track"
    ] == "radio"
    assert evidence["embedded_artifacts"]["follow_up_candidate_interpretation"][
        "track"
    ] == "radio"
    assert not list(work_dir.glob("data/**/*.h5"))


def test_retired_duplicate_candidate_store_surface_is_absent() -> None:
    cli = Path("src/techno_search/cli.py").read_text(encoding="utf-8")

    assert not Path("src/techno_search/candidate_store.py").exists()
    for command in (
        "candidate-store-init",
        "candidate-store-summary",
        "candidate-store-list",
    ):
        assert command not in cli


def test_current_release_evidence_is_bound_to_the_current_version() -> None:
    """PROD-01 freshness: the current release must have its own evidence bundle.

    This is the assertion that legitimately tracks ``__version__``. It is a
    separate record from the frozen per-release archives below, which stay
    bound to the commit each was produced at (CLAIM-04, E2E-04).

    Regenerate with ``scripts/run_acceptance.sh`` on a host that permits
    ``bind()``, then copy the result to this path.
    """
    evidence = json.loads(CURRENT_RELEASE_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == CONTROLLED_ACCEPTANCE_SCHEMA_VERSION
    assert evidence["release"]["app_version"] == __version__
    assert len(evidence["assertion_results"]) == 14
    assert all(item["passed"] for item in evidence["assertion_results"])


def test_committed_v1_2_71_evidence_is_portable_and_bound_to_clean_code() -> None:
    """The committed bundle is a record of one run, bound to the commit tested.

    Contract CLAIM-04 and E2E-04 bind evidence to the *commit tested*, and the
    state ledger's evidence contract requires a ``tested_commit`` field. This
    bundle is pinned to commit ``edb6e66``, so it records the version that
    commit carried. Asserting it equals the current ``__version__`` demanded
    that a frozen historical artifact track a moving number, so every version
    bump broke it — 1.2.71 -> 1.2.72 is what broke it here.

    Freshness of evidence for the *current* release is a separate requirement
    (PROD-01 real-data evidence freshness). It is covered by prod-check's
    real_data_evidence check and by the fresh-harness test above, which does
    assert the current ``__version__``.
    """
    evidence = json.loads(COMMITTED_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == CONTROLLED_ACCEPTANCE_SCHEMA_VERSION
    assert evidence["release"]["app_version"] == "1.2.71"
    assert evidence["release"]["code_commit"] == "edb6e66"
    assert all(item["passed"] for item in evidence["assertion_results"])
    assert len(evidence["assertion_results"]) == 14
    assert evidence["selected_targets"] == {
        "new": ["OUTSIDE"],
        "follow_up": ["OUTSIDE"],
    }
    assert evidence["search_runs"]["follow_up"]["event_sequence"] == [
        "created",
        "run_started",
        "run_failed",
        "run_resumed",
        "run_completed",
    ]
    serialized = json.dumps(evidence)
    assert "$ACCEPTANCE_WORK_DIR" in serialized
    assert "/tmp/techno-hunter-v1-2-71-edb6e66" not in serialized
    assert "/private/var/folders" not in serialized
