"""Status-authority regressions for ``configs/HUNTER_PROD_STATE.json``.

The execution directive requires that only a deterministic gate result may
produce ``VERIFIED`` or ``PROD``. Before ``hunter_prod_state`` existed, nothing
read or wrote the ledger, so an agent could type ``"VERIFIED"`` into it and no
executable check disagreed.

These tests are the negative controls for that hole.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from techno_search.hunter_prod_check import (
    ProdCheckEnvironment,
    check_state_authority,
    run_prod_check,
)
from techno_search.hunter_prod_state import (
    PROD_STATUS_PROD,
    PROD_STATUS_REVOKED,
    STATUS_BLOCKING,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    apply_report,
    audit_recorded_authority_metadata,
    audit_state_authority,
    derive_requirement_statuses,
    load_state,
    prod_status_is_earned,
    uncovered_requirements,
    write_state,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _report(*checks: dict[str, object], prod_ready: bool | None = None) -> dict[str, object]:
    entries = list(checks)
    if prod_ready is None:
        prod_ready = all(entry["status"] == "PASS" for entry in entries)
    return {
        "schema_version": "hunter_prod_check_report_v1",
        "tested_commit": "0" * 40,
        "working_tree_state": "clean",
        "app_version": "1.2.73",
        "environment": {"checked_at_utc": "2026-07-31T00:00:00Z"},
        "checks": entries,
        "prod_ready": prod_ready,
    }


def _check(check_id: str, status: str, *requirements: str) -> dict[str, object]:
    return {
        "check_id": check_id,
        "status": status,
        "requirement_ids": list(requirements),
        "summary": check_id,
        "detail": {},
    }


def _state(**requirements: str) -> dict[str, object]:
    return {
        "prod_status": PROD_STATUS_REVOKED,
        "requirements": {key: {"status": value} for key, value in requirements.items()},
        "completion": {"prod_check_exit_status": None},
    }


class TestDerivation:
    def test_all_covering_checks_passing_yields_verified(self) -> None:
        report = _report(_check("palette", "PASS", "CLI-01"))
        derived = derive_requirement_statuses(report, known_requirements=["CLI-01"])
        assert derived["CLI-01"] == STATUS_VERIFIED

    @pytest.mark.parametrize("status", ["FAIL", "NOT_EXECUTED"])
    def test_any_failing_or_unexecuted_covering_check_blocks(self, status: str) -> None:
        report = _report(
            _check("palette", "PASS", "CLI-01"),
            _check("pty_palette", status, "CLI-01"),
            prod_ready=False,
        )
        derived = derive_requirement_statuses(report, known_requirements=["CLI-01"])
        assert derived["CLI-01"] == STATUS_BLOCKING

    def test_uncovered_requirement_never_inherits_a_pass(self) -> None:
        """An unmeasured requirement is UNVERIFIED, not VERIFIED by proximity."""
        report = _report(_check("palette", "PASS", "CLI-01"))
        derived = derive_requirement_statuses(report, known_requirements=["CLI-01", "E2E-01"])
        assert derived["E2E-01"] == STATUS_UNVERIFIED


class TestAudit:
    def test_hand_written_verified_without_a_passing_gate_is_a_violation(self) -> None:
        """The core negative control for the status-authority hole."""
        state = _state(**{"CLI-01": STATUS_VERIFIED})
        report = _report(_check("pty_palette", "NOT_EXECUTED", "CLI-01"), prod_ready=False)
        violations = audit_state_authority(state, report)
        assert [violation.field_path for violation in violations] == [
            "requirements.CLI-01.status"
        ]
        assert violations[0].recorded == STATUS_VERIFIED
        assert violations[0].justified == STATUS_BLOCKING

    def test_verified_for_an_uncovered_requirement_is_a_violation(self) -> None:
        state = _state(**{"E2E-01": STATUS_VERIFIED})
        report = _report(_check("palette", "PASS", "CLI-01"))
        violations = audit_state_authority(state, report)
        assert violations and violations[0].justified == STATUS_UNVERIFIED

    def test_agent_authorable_statuses_are_accepted(self) -> None:
        state = _state(**{"CLI-01": "IMPLEMENTED_NOT_VERIFIED"})
        report = _report(_check("pty_palette", "NOT_EXECUTED", "CLI-01"), prod_ready=False)
        assert audit_state_authority(state, report) == []

    def test_unknown_status_vocabulary_is_a_violation(self) -> None:
        state = _state(**{"CLI-01": "PROD_ACCEPTED"})
        report = _report(_check("palette", "PASS", "CLI-01"))
        violations = audit_state_authority(state, report)
        assert violations and "vocabulary" in violations[0].reason

    def test_prod_status_without_a_passing_report_is_a_violation(self) -> None:
        state = _state(**{"CLI-01": STATUS_BLOCKING})
        state["prod_status"] = PROD_STATUS_PROD
        report = _report(_check("palette", "FAIL", "CLI-01"), prod_ready=False)
        fields = {violation.field_path for violation in audit_state_authority(state, report)}
        assert "prod_status" in fields

    def test_zero_exit_status_without_a_passing_report_is_a_violation(self) -> None:
        state = _state(**{"CLI-01": STATUS_BLOCKING})
        state["completion"]["prod_check_exit_status"] = 0
        report = _report(_check("palette", "FAIL", "CLI-01"), prod_ready=False)
        fields = {violation.field_path for violation in audit_state_authority(state, report)}
        assert "completion.prod_check_exit_status" in fields

    def test_partial_audit_accepts_verified_status_with_gate_provenance(self) -> None:
        state = _state(**{"CLI-01": STATUS_VERIFIED})
        state["requirements"]["CLI-01"].update(
            {
                "status_authority": "prod-check",
                "derived_from": ["palette"],
                "tested_commit": "0" * 40,
                "app_version": "1.2.72",
                "gate_report_schema": "hunter_prod_check_report_v1",
            }
        )
        assert audit_recorded_authority_metadata(state) == []

    def test_partial_audit_rejects_verified_status_without_gate_provenance(self) -> None:
        state = _state(**{"CLI-01": STATUS_VERIFIED})
        violations = audit_recorded_authority_metadata(state)
        assert [violation.field_path for violation in violations] == [
            "requirements.CLI-01.status"
        ]


class TestProdIsNotEarnedByCoverageGaps:
    """Regression: a zero-exit gate is necessary but not sufficient for PROD.

    apply_report once wrote prod_status=PROD whenever every check that ran
    passed, even with requirements no check measured. That certified the
    measured subset and stayed silent about the rest.
    """

    def test_prod_is_not_earned_while_a_requirement_is_uncovered(self) -> None:
        report = _report(_check("palette", "PASS", "CLI-01"))
        derived = derive_requirement_statuses(
            report, known_requirements=["CLI-01", "E2E-01"]
        )
        assert prod_status_is_earned(report, derived) is False
        assert uncovered_requirements(derived) == ["E2E-01"]

    def test_prod_is_earned_when_every_requirement_is_covered_and_passing(self) -> None:
        report = _report(_check("palette", "PASS", "CLI-01", "E2E-01"))
        derived = derive_requirement_statuses(
            report, known_requirements=["CLI-01", "E2E-01"]
        )
        assert prod_status_is_earned(report, derived) is True

    def test_apply_report_refuses_prod_with_an_uncovered_requirement(self) -> None:
        state = _state(**{"CLI-01": STATUS_UNVERIFIED, "E2E-01": STATUS_UNVERIFIED})
        report = _report(_check("palette", "PASS", "CLI-01"))
        updated = apply_report(state, report)
        assert updated["prod_status"] == PROD_STATUS_REVOKED
        assert updated["completion"]["verified_commit"] is None
        assert updated["gate_execution"]["requirements_without_a_covering_check"] == [
            "E2E-01"
        ]

    def test_audit_flags_prod_when_a_requirement_is_uncovered(self) -> None:
        state = _state(**{"CLI-01": STATUS_UNVERIFIED, "E2E-01": STATUS_UNVERIFIED})
        state["prod_status"] = PROD_STATUS_PROD
        report = _report(_check("palette", "PASS", "CLI-01"))
        violations = audit_state_authority(state, report)
        fields = {violation.field_path for violation in violations}
        assert "prod_status" in fields
        assert any("no check covers" in v.reason for v in violations)


class TestApplyReport:
    def test_machine_fields_are_derived_and_agent_fields_preserved(self) -> None:
        state = _state(**{"CLI-01": STATUS_UNVERIFIED, "E2E-01": STATUS_UNVERIFIED})
        state["preserved_user_changes"] = [{"path": "docs/README_SPEC.md"}]
        report = _report(
            _check("palette", "PASS", "CLI-01"),
            _check("pty_palette", "NOT_EXECUTED", "CLI-01"),
            prod_ready=False,
        )
        updated = apply_report(
            state, report, active_phase="PHASE 0", implementation_state="IN_PROGRESS"
        )
        assert updated["requirements"]["CLI-01"]["status"] == STATUS_BLOCKING
        assert updated["requirements"]["CLI-01"]["status_authority"] == "prod-check"
        assert updated["requirements"]["E2E-01"]["status"] == STATUS_UNVERIFIED
        assert updated["prod_status"] == PROD_STATUS_REVOKED
        assert updated["completion"]["prod_check_exit_status"] == 1
        assert updated["completion"]["verified_commit"] is None
        assert updated["implementation_state"] == "IN_PROGRESS"
        # Agent-owned narrative survives untouched.
        assert updated["preserved_user_changes"] == [{"path": "docs/README_SPEC.md"}]

    def test_applied_state_always_passes_its_own_audit(self) -> None:
        state = _state(**{"CLI-01": STATUS_UNVERIFIED})
        report = _report(_check("palette", "PASS", "CLI-01"))
        assert audit_state_authority(apply_report(state, report), report) == []

    def test_partial_report_preserves_unrelated_requirement_and_cannot_award_prod(
        self,
    ) -> None:
        state = _state(**{"CLI-01": STATUS_UNVERIFIED, "E2E-01": STATUS_VERIFIED})
        state["prod_status"] = PROD_STATUS_REVOKED
        report = _report(_check("palette", "PASS", "CLI-01"), prod_ready=True)
        report["scope"] = {"kind": "partial", "selected_checks": ["palette"]}

        updated = apply_report(state, report)

        assert updated["requirements"]["CLI-01"]["status"] == STATUS_VERIFIED
        assert updated["requirements"]["E2E-01"]["status"] == STATUS_VERIFIED
        assert updated["prod_status"] == PROD_STATUS_REVOKED
        assert updated["last_partial_gate_execution"]["prod_status_earned"] is False


class TestLedgerOnDisk:
    def test_partial_state_gate_passes_without_awarding_prod(self) -> None:
        report = run_prod_check(repo_root=REPO_ROOT, only=["state_authority"])
        assert report["gate_passed"] is True
        assert report["prod_ready"] is False
        assert report["scope"]["kind"] == "partial"

    def test_committed_ledger_is_valid_and_passes_the_gate_check(self) -> None:
        state = load_state(REPO_ROOT)
        assert isinstance(state.get("requirements"), dict)
        env = ProdCheckEnvironment(REPO_ROOT)
        report = _report(_check("palette", "PASS", "CLI-01"), prod_ready=False)
        outcome = check_state_authority(env, report)
        assert outcome.status in {"PASS", "FAIL"}

    def test_forged_verified_on_disk_fails_the_gate_check(self, tmp_path: Path) -> None:
        """End-to-end: a forged ledger makes prod-check's own check FAIL."""
        forged = load_state(REPO_ROOT)
        for entry in forged["requirements"].values():
            entry["status"] = STATUS_VERIFIED
        forged["prod_status"] = PROD_STATUS_PROD
        write_state(tmp_path, forged)

        env = ProdCheckEnvironment(tmp_path)
        report = _report(_check("pty_palette", "NOT_EXECUTED", "CLI-01"), prod_ready=False)
        outcome = check_state_authority(env, report)
        assert outcome.status == "FAIL"
        assert outcome.detail["violations"]

    def test_missing_ledger_fails_rather_than_passing_silently(self, tmp_path: Path) -> None:
        env = ProdCheckEnvironment(tmp_path)
        outcome = check_state_authority(env, _report())
        assert outcome.status == "FAIL"

    def test_malformed_ledger_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "configs" / "HUNTER_PROD_STATE.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        outcome = check_state_authority(ProdCheckEnvironment(tmp_path), _report())
        assert outcome.status == "FAIL"

    def test_round_trip_write_is_deterministic(self, tmp_path: Path) -> None:
        state = load_state(REPO_ROOT)
        first = write_state(tmp_path, state).read_text(encoding="utf-8")
        second = write_state(tmp_path, json.loads(first)).read_text(encoding="utf-8")
        assert first == second
