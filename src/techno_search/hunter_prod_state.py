"""Machine-owned status authority for ``configs/HUNTER_PROD_STATE.json``.

The execution directive separates two roles that were previously conflated:

* an implementation agent may record ``BLOCKING``, ``IN_PROGRESS``, or
  ``IMPLEMENTED_NOT_VERIFIED``;
* only a deterministic gate runner may produce ``VERIFIED`` or ``PROD``.

Before this module existed nothing read or wrote the ledger at all, so any
agent could type ``"VERIFIED"`` into the JSON and no executable check would
contradict it. That is the defect repaired here.

The rules are:

1. :func:`derive_requirement_statuses` computes requirement status *only* from
   a ``prod-check`` report's check outcomes. It has no other input.
2. :func:`audit_state_authority` reports every machine-owned field whose value
   is not justified by the supplied report. ``prod-check`` runs this audit as
   a mandatory check, so a hand-written ``VERIFIED`` makes the gate fail.
3. :func:`apply_report` is the only supported writer of the machine-owned
   fields. Agent-owned fields are passed through untouched.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATE_RELATIVE_PATH = Path("configs") / "HUNTER_PROD_STATE.json"

STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_BLOCKING = "BLOCKING"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_IMPLEMENTED_NOT_VERIFIED = "IMPLEMENTED_NOT_VERIFIED"
STATUS_VERIFIED = "VERIFIED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"

#: Statuses an implementation agent may author by hand.
AGENT_AUTHORABLE_STATUSES = frozenset(
    {
        STATUS_UNVERIFIED,
        STATUS_BLOCKING,
        STATUS_IN_PROGRESS,
        STATUS_IMPLEMENTED_NOT_VERIFIED,
        STATUS_NOT_APPLICABLE,
    }
)

#: Statuses only a deterministic gate result may produce.
MACHINE_ONLY_STATUSES = frozenset({STATUS_VERIFIED})

PROD_STATUS_REVOKED = "REVOKED_UNTIL_VERIFIED"
PROD_STATUS_PROD = "PROD"


@dataclass(frozen=True)
class AuthorityViolation:
    """One machine-owned field whose value the gate results do not justify."""

    field_path: str
    recorded: str
    justified: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "field": self.field_path,
            "recorded": self.recorded,
            "justified": self.justified,
            "reason": self.reason,
        }


def load_state(repo_root: Path) -> dict[str, Any]:
    """Read the ledger, raising a clear error when it is unusable."""
    path = repo_root / STATE_RELATIVE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"missing state ledger: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed state ledger {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"state ledger {path} must contain a JSON object")
    return data


def write_state(repo_root: Path, state: Mapping[str, Any]) -> Path:
    """Persist the ledger deterministically."""
    path = repo_root / STATE_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _report_requirement_results(report: Mapping[str, Any]) -> dict[str, list[str]]:
    """Map each requirement id to the statuses of the checks covering it."""
    results: dict[str, list[str]] = {}
    for check in report.get("checks", []):
        status = str(check.get("status", ""))
        for requirement_id in check.get("requirement_ids", []):
            results.setdefault(str(requirement_id), []).append(status)
    return results


def derive_requirement_statuses(
    report: Mapping[str, Any],
    *,
    known_requirements: Iterable[str],
) -> dict[str, str]:
    """Derive each requirement's status from gate outcomes alone.

    A requirement is ``VERIFIED`` only when at least one check covers it and
    every covering check passed. Any covering FAIL or NOT_EXECUTED is
    ``BLOCKING``. A requirement no check covers stays ``UNVERIFIED`` — an
    uncovered requirement has not been demonstrated, so it must never inherit
    a pass from unrelated checks.
    """
    covered = _report_requirement_results(report)
    derived: dict[str, str] = {}
    for requirement_id in known_requirements:
        statuses = covered.get(requirement_id, [])
        if not statuses:
            derived[requirement_id] = STATUS_UNVERIFIED
        elif all(status == "PASS" for status in statuses):
            derived[requirement_id] = STATUS_VERIFIED
        else:
            derived[requirement_id] = STATUS_BLOCKING
    return derived


def prod_status_is_earned(
    report: Mapping[str, Any], derived: Mapping[str, str]
) -> bool:
    """Report whether PROD is justified by this run.

    A zero-exit gate is necessary but NOT sufficient. The contract's completion
    rule requires every mandatory requirement to be *executed and passing*, so a
    requirement that no check covers — status ``UNVERIFIED`` — blocks PROD even
    when every check that did run passed. Otherwise the gate would certify the
    requirements it happens to measure and stay silent about the rest.
    """
    if not report.get("prod_ready"):
        return False
    return all(status == STATUS_VERIFIED for status in derived.values())


def uncovered_requirements(derived: Mapping[str, str]) -> list[str]:
    """Return requirements no check in this report measured."""
    return sorted(key for key, status in derived.items() if status == STATUS_UNVERIFIED)


def audit_state_authority(
    state: Mapping[str, Any], report: Mapping[str, Any]
) -> list[AuthorityViolation]:
    """Return every machine-owned value the supplied gate report cannot justify."""
    violations: list[AuthorityViolation] = []
    requirements = state.get("requirements", {})
    if not isinstance(requirements, dict):
        return [
            AuthorityViolation(
                "requirements",
                repr(requirements),
                "object",
                "requirements must be a JSON object",
            )
        ]

    derived = derive_requirement_statuses(report, known_requirements=requirements.keys())
    for requirement_id, entry in sorted(requirements.items()):
        recorded = str((entry or {}).get("status", ""))
        justified = derived.get(requirement_id, STATUS_UNVERIFIED)
        if recorded in MACHINE_ONLY_STATUSES and justified != recorded:
            violations.append(
                AuthorityViolation(
                    f"requirements.{requirement_id}.status",
                    recorded,
                    justified,
                    "VERIFIED requires every covering gate check to pass in this report",
                )
            )
        elif recorded not in AGENT_AUTHORABLE_STATUSES and recorded not in MACHINE_ONLY_STATUSES:
            violations.append(
                AuthorityViolation(
                    f"requirements.{requirement_id}.status",
                    recorded,
                    justified,
                    "status is not in the allowed vocabulary",
                )
            )

    earned = prod_status_is_earned(report, derived)
    prod_status = str(state.get("prod_status", ""))
    justified_prod = PROD_STATUS_PROD if earned else PROD_STATUS_REVOKED
    if prod_status == PROD_STATUS_PROD and not earned:
        uncovered = uncovered_requirements(derived)
        reason = (
            "PROD requires a zero-exit gate report with no failed or unexecuted check"
        )
        if uncovered:
            reason = (
                "PROD requires every mandatory requirement to be executed and "
                "passing; no check covers " + ", ".join(uncovered)
            )
        violations.append(
            AuthorityViolation("prod_status", prod_status, justified_prod, reason)
        )

    completion = state.get("completion", {})
    if isinstance(completion, dict):
        recorded_exit = completion.get("prod_check_exit_status")
        justified_exit = 0 if report.get("prod_ready") else 1
        if recorded_exit == 0 and not report.get("prod_ready"):
            violations.append(
                AuthorityViolation(
                    "completion.prod_check_exit_status",
                    str(recorded_exit),
                    str(justified_exit),
                    "a zero exit status must come from an actually passing gate run",
                )
            )
    return violations


def audit_recorded_authority_metadata(
    state: Mapping[str, Any],
) -> list[AuthorityViolation]:
    """Validate stored machine-owned statuses during a partial gate run.

    A partial report cannot re-evaluate requirements outside its selected
    scope. It can still prove that each stored ``VERIFIED`` value carries the
    deterministic gate provenance required by the status-authority contract.
    Full PROD runs continue to use :func:`audit_state_authority` and re-derive
    every requirement from the complete report.
    """
    violations: list[AuthorityViolation] = []
    requirements = state.get("requirements", {})
    if not isinstance(requirements, dict):
        return [
            AuthorityViolation(
                "requirements",
                repr(requirements),
                "object",
                "requirements must be a JSON object",
            )
        ]

    for requirement_id, entry in sorted(requirements.items()):
        if not isinstance(entry, dict):
            violations.append(
                AuthorityViolation(
                    f"requirements.{requirement_id}",
                    repr(entry),
                    "object",
                    "requirement status entry must be a JSON object",
                )
            )
            continue
        status = str(entry.get("status", ""))
        if status not in MACHINE_ONLY_STATUSES:
            if status not in AGENT_AUTHORABLE_STATUSES:
                violations.append(
                    AuthorityViolation(
                        f"requirements.{requirement_id}.status",
                        status,
                        "allowed status",
                        "status is not in the allowed vocabulary",
                    )
                )
            continue

        provenance = {
            "status_authority": entry.get("status_authority"),
            "derived_from": entry.get("derived_from"),
            "tested_commit": entry.get("tested_commit"),
            "app_version": entry.get("app_version"),
            "gate_report_schema": entry.get("gate_report_schema"),
        }
        missing = [key for key, value in provenance.items() if value in (None, "", [])]
        if entry.get("status_authority") != "prod-check":
            missing.append("status_authority=prod-check")
        if missing:
            violations.append(
                AuthorityViolation(
                    f"requirements.{requirement_id}.status",
                    status,
                    STATUS_UNVERIFIED,
                    "stored VERIFIED lacks deterministic gate provenance: "
                    + ", ".join(sorted(set(missing))),
                )
            )

    if state.get("prod_status") == PROD_STATUS_PROD:
        completion = state.get("completion", {})
        execution = state.get("gate_execution", {})
        justified = (
            isinstance(completion, dict)
            and completion.get("prod_check_exit_status") == 0
            and bool(completion.get("verified_commit"))
            and bool(completion.get("verified_at_utc"))
            and isinstance(execution, dict)
            and execution.get("prod_status_earned") is True
            and not execution.get("requirements_without_a_covering_check")
        )
        if not justified:
            violations.append(
                AuthorityViolation(
                    "prod_status",
                    PROD_STATUS_PROD,
                    PROD_STATUS_REVOKED,
                    "stored PROD lacks a complete zero-exit gate record",
                )
            )
    return violations


def apply_report(
    state: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    active_phase: str | None = None,
    implementation_state: str | None = None,
    next_action: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a new ledger with machine-owned fields set from ``report``.

    Agent-owned narrative fields are preserved. ``implementation_state`` is
    recorded separately from the gate result so the two can never be confused.
    """
    updated: dict[str, Any] = json.loads(json.dumps(state))
    requirements = updated.get("requirements", {})
    is_partial = (report.get("scope") or {}).get("kind") == "partial"
    covered_requirement_ids = {
        str(requirement_id)
        for check in report.get("checks", [])
        for requirement_id in check.get("requirement_ids", [])
    }
    requirement_scope = covered_requirement_ids if is_partial else set(requirements.keys())
    derived = derive_requirement_statuses(report, known_requirements=requirement_scope)

    environment = report.get("environment", {}) or {}
    evidence_stamp = {
        "tested_commit": report.get("tested_commit"),
        "working_tree_state": report.get("working_tree_state"),
        "app_version": report.get("app_version"),
        "gate_report_schema": report.get("schema_version"),
    }

    for requirement_id, entry in requirements.items():
        if is_partial and requirement_id not in requirement_scope:
            continue
        if not isinstance(entry, dict):
            entry = {}
        entry["status"] = derived.get(requirement_id, STATUS_UNVERIFIED)
        entry["status_authority"] = "prod-check"
        entry["derived_from"] = sorted(
            str(check.get("check_id"))
            for check in report.get("checks", [])
            if requirement_id in check.get("requirement_ids", [])
        )
        entry.update(evidence_stamp)
        requirements[requirement_id] = entry
    updated["requirements"] = requirements

    earned = False if is_partial else prod_status_is_earned(report, derived)
    uncovered = uncovered_requirements(derived)
    if is_partial:
        partial_passed = bool(report.get("gate_passed", report.get("prod_ready")))
        if not partial_passed:
            updated["prod_status"] = PROD_STATUS_REVOKED
            updated["completion"] = {
                **(updated.get("completion") or {}),
                "prod_check_exit_status": 1,
                "verified_commit": None,
                "verified_at_utc": None,
            }
        updated["last_partial_gate_execution"] = {
            "counts": report.get("counts"),
            "gate_passed": partial_passed,
            "prod_ready": False,
            "requirement_scope": sorted(requirement_scope),
            "prod_status_earned": False,
            "contract_version": report.get("contract_version"),
            "cli_ux_version": report.get("cli_ux_version"),
            "tested_commit": report.get("tested_commit"),
            "tested_at_utc": environment.get("checked_at_utc"),
        }
    else:
        updated["prod_status"] = PROD_STATUS_PROD if earned else PROD_STATUS_REVOKED
        updated["gate_execution"] = {
            "counts": report.get("counts"),
            "prod_ready": bool(report.get("prod_ready")),
            "requirements_without_a_covering_check": uncovered,
            "prod_status_earned": earned,
            "contract_version": report.get("contract_version"),
            "cli_ux_version": report.get("cli_ux_version"),
        }
        updated["completion"] = {
            **(updated.get("completion") or {}),
            "prod_check_command": "prod-check --json --report-path <path>",
            "prod_check_exit_status": 0 if report.get("prod_ready") else 1,
            "verified_commit": report.get("tested_commit") if earned else None,
            "verified_at_utc": environment.get("checked_at_utc") if earned else None,
        }
    if active_phase is not None:
        updated["active_phase"] = active_phase
    if implementation_state is not None:
        updated["implementation_state"] = implementation_state
    if next_action is not None:
        updated["next_action"] = dict(next_action)
    return updated
