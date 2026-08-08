"""Regression coverage for Hunter installation-surface gate selection."""

from __future__ import annotations

import tomllib
from pathlib import Path

from techno_search.hunter_prod_check import (
    CANONICAL_EXECUTABLE,
    COMPATIBILITY_EXECUTABLES,
    _last_nonempty_line,
    _required_full_gate_outcomes,
    _select_checks,
    check_built_wheel,
    check_operator_installation_surfaces,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_include_wheel_survives_only_filter() -> None:
    selected = _select_checks(include_wheel=True, only=["documented_installation"])
    assert check_built_wheel in selected


def test_phase_one_installation_surface_check_is_selectable() -> None:
    selected = _select_checks(
        include_wheel=False, only=["operator_installation_surfaces"]
    )
    assert selected == [check_operator_installation_surfaces]


def test_full_gate_without_wheel_is_not_executed() -> None:
    outcomes = _required_full_gate_outcomes(include_wheel=False, is_partial=False)
    assert len(outcomes) == 1
    assert outcomes[0].check_id == "built_wheel"
    assert outcomes[0].status == "NOT_EXECUTED"
    assert outcomes[0].mandatory_failure is True
    assert outcomes[0].detail["required_option"] == "--include-wheel"


def test_partial_gate_without_wheel_remains_selectable() -> None:
    assert _required_full_gate_outcomes(include_wheel=False, is_partial=True) == []


def test_probe_path_uses_final_nonempty_line_after_import_diagnostics() -> None:
    stdout = "matplotlib WARNING cache unavailable\n\n/tmp/venv/site-packages/module.py\n"
    assert _last_nonempty_line(stdout) == "/tmp/venv/site-packages/module.py"


def test_canonical_and_compatibility_names_route_to_same_business_logic() -> None:
    scripts = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["scripts"]
    canonical_target = scripts[CANONICAL_EXECUTABLE]
    assert canonical_target == "techno_search.hunter_shell:main"
    assert all(scripts[name] == canonical_target for name in COMPATIBILITY_EXECUTABLES)
