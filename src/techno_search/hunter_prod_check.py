"""Repository-native Hunter PROD gate.

This module implements the ``prod-check`` command required by
``docs/HUNTER_PROD_CONTRACT.md`` PROD-01. It emits a versioned
machine-readable report and exits non-zero when any mandatory requirement
fails.

Two design rules matter here:

1. Launch and entry-point checks run the *installed* executables as operating
   system subprocesses. Direct imports, ``PYTHONPATH`` injection, and
   source-file invocation are deliberately not used, because the contract
   states they do not satisfy the launch gate.
2. A check that cannot execute reports ``NOT_EXECUTED`` with a reason. It is
   never counted as a pass, per CLAIM-03.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from techno_search import __version__
from techno_search.hunter_prod_state import (
    apply_report,
    audit_recorded_authority_metadata,
    audit_state_authority,
    derive_requirement_statuses,
    load_state,
    uncovered_requirements,
    write_state,
)
from techno_search.hunter_pty_gate import drive_palette_session

REPORT_SCHEMA_VERSION = "hunter_prod_check_report_v1"
CONTRACT_VERSION = "HUNTER-PROD-2026-07-30.3"
CLI_UX_VERSION = "HUNTER-CLI-UX-2026-07-30.3"
REAL_DATA_ACCEPTANCE_SCHEMA_VERSION = "hunter_prod_live_acceptance_v3"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_EXECUTED = "NOT_EXECUTED"

#: Manifests written under an older schema are immutable historical evidence
#: (DUR-02) and are not retro-filled when new provenance fields are added.
CURRENT_MANIFEST_SCHEMA = "hunter_search_manifest_v3"

CANONICAL_EXECUTABLE = "TechnoHunter"
COMPATIBILITY_EXECUTABLES = ("Techno-Hunter",)
ONE_SHOT_EXECUTABLES = ("Create-New-Search", "Run-New-Search", "Show-Follow-Ups")

REQUIRED_SLASH_COMMANDS = (
    "/New-Search",
    "/Follow-Up-Search",
    "/Run-Search",
    "/Show-Follow-Ups",
    "/Inspect-Target",
    "/Help",
    "/Exit",
)

REQUIRED_GOLDEN_TESTS = (
    "startup_neo.txt",
    "startup_exo.txt",
    "startup_techno.txt",
    "command_palette.txt",
    "new_search_fields.txt",
    "invalid_targets.txt",
    "action_preview.txt",
    "results_table_80_columns.txt",
    "results_table_140_columns.txt",
    "operator_error.txt",
    "non_tty_output.txt",
)

REQUIRED_README_HEADINGS = (
    "## Table of Contents",
    "## 1. Executive Summary",
    "### 1.1 Research Objective and Scientific Context",
    "### 1.2 Scope, Boundaries, and Exclusions",
    "### 1.3 System and Workflow Overview",
    "### 1.4 Verified Capability Status",
    "### 1.5 Evidence and Reproducibility",
    "## 2. CLI Tool Usage",
    "### 2.1 Prerequisites",
    "### 2.2 Installation",
    "### 2.3 Environment Setup",
    "### 2.4 Command Structure",
    "### 2.5 End-to-End Workflow",
    "### 2.6 Command Reference",
    "### 2.7 Outputs and Artifacts",
    "### 2.8 Exit Codes and Failure Behavior",
    "### 2.9 Troubleshooting",
    "## 3. Analytics, Mathematics, and Theoretical Foundation",
    "### 3.1 Problem Formulation",
    "### 3.2 Inputs, Outputs, Labels, Units, and Provenance",
    "### 3.3 Mathematical Notation",
    "### 3.4 Models, Algorithms, and Scores",
    "### 3.5 Assumptions, Objectives, and Statistical Methods",
    "### 3.6 Thresholds, Calibration, and Uncertainty",
    "### 3.7 Evaluation and Validation",
    "### 3.8 Limitations and Failure Modes",
    "### 3.9 Implementation and Test Traceability",
    "## 4. Sibling Repositories and Shared Data",
    "### 4.1 Research Program and Repository Responsibilities",
    "### 4.2 Local Discovery and Configuration",
    "### 4.3 Shared Artifacts, Ownership, and Access",
    "### 4.4 Schemas, Provenance, Versioning, and Compatibility",
    "### 4.5 Availability, Failure Behavior, and Regeneration",
    "### 4.6 Cross-Repository Safety Boundaries",
)

# README_SPEC.md forbids planning language outright. "Nonconforming" is the
# required label for a missing or broken required capability.
FORBIDDEN_README_TERMS = ("Planned", "roadmap", "backlog", "future work", "Partial")

SIBLING_REPOSITORY_NAMES = ("2026 Exoplanet Research", "2026 Near Earth Objects")


@dataclass(frozen=True)
class CheckOutcome:
    """One PROD gate observation."""

    check_id: str
    requirement_ids: tuple[str, ...]
    status: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def mandatory_failure(self) -> bool:
        return self.status in {STATUS_FAIL, STATUS_NOT_EXECUTED}

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "requirement_ids": list(self.requirement_ids),
            "status": self.status,
            "summary": self.summary,
            "detail": self.detail,
        }


def _passed(
    check_id: str, requirement_ids: Sequence[str], summary: str, **detail: Any
) -> CheckOutcome:
    return CheckOutcome(check_id, tuple(requirement_ids), STATUS_PASS, summary, detail)


def _failed(
    check_id: str, requirement_ids: Sequence[str], summary: str, **detail: Any
) -> CheckOutcome:
    return CheckOutcome(check_id, tuple(requirement_ids), STATUS_FAIL, summary, detail)


def _not_executed(
    check_id: str, requirement_ids: Sequence[str], reason: str, **detail: Any
) -> CheckOutcome:
    return CheckOutcome(
        check_id,
        tuple(requirement_ids),
        STATUS_NOT_EXECUTED,
        f"NOT EXECUTED — {reason}",
        detail,
    )


def _last_nonempty_line(value: str) -> str:
    """Return the final payload line after import-time diagnostic output."""

    return next((line.strip() for line in reversed(value.splitlines()) if line.strip()), "")


@dataclass(frozen=True)
class CommandResult:
    """Captured operating-system subprocess evidence."""

    command: list[str]
    cwd: str
    exit_status: int
    stdout: str
    stderr: str

    def as_dict(self, *, stream_limit: int = 4000) -> dict[str, Any]:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "exit_status": self.exit_status,
            "stdout": self.stdout[:stream_limit],
            "stderr": self.stderr[:stream_limit],
        }


class ProdCheckEnvironment:
    """Resolve the operator environment the gate must validate."""

    def __init__(self, repo_root: Path, *, bin_dir: Path | None = None) -> None:
        self.repo_root = repo_root
        self.bin_dir = bin_dir or self._default_bin_dir(repo_root)

    @staticmethod
    def _default_bin_dir(repo_root: Path) -> Path:
        # Prefer the environment this process already runs under so the gate
        # validates the same installation the operator invoked.
        candidate = Path(sys.executable).resolve().parent
        if (candidate / CANONICAL_EXECUTABLE).exists():
            return candidate
        return repo_root / ".venv" / "bin"

    def executable(self, name: str) -> Path:
        return self.bin_dir / name

    def metadata(self) -> dict[str, Any]:
        canonical = self.executable(CANONICAL_EXECUTABLE)
        return {
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "virtual_environment": str(self.bin_dir.parent),
            "bin_dir": str(self.bin_dir),
            "resolved_canonical_executable": (
                str(canonical.resolve()) if canonical.exists() else None
            ),
            "installation_mode": self._installation_mode(),
            "app_version": __version__,
            "repo_root": str(self.repo_root),
            "relevant_environment": {
                key: os.environ.get(key)
                for key in (
                    "TERM",
                    "NO_COLOR",
                    "CI",
                    "REDUCE_MOTION",
                    "TECHNO_HUNTER_REDUCE_MOTION",
                    "TECHNO_SEARCH_ENABLE_LIVE_DATA",
                    "VIRTUAL_ENV",
                )
            },
        }

    def _installation_mode(self) -> str:
        try:
            import techno_search
        except ImportError:  # pragma: no cover - the gate cannot run at all
            return "unknown"
        module_file = getattr(techno_search, "__file__", None)
        if module_file is None:
            return "unknown"
        location = Path(module_file).resolve()
        try:
            location.relative_to(self.repo_root.resolve())
        except ValueError:
            return "installed-copy"
        return "editable"


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    stdin_text: str | None = None,
    timeout: float = 180.0,
    env: dict[str, str] | None = None,
    remove_env: Sequence[str] = (),
) -> CommandResult:
    """Run a real operating-system subprocess and capture full evidence."""
    process_env = dict(os.environ)
    if env:
        process_env.update(env)
    for key in remove_env:
        process_env.pop(key, None)
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=process_env,
        )
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout
        partial = (
            raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else (raw or "")
        )
        return CommandResult(
            list(command), str(cwd), 124, partial, f"timeout after {timeout}s"
        )
    except OSError as exc:
        return CommandResult(list(command), str(cwd), 127, "", str(exc))
    return CommandResult(
        list(command), str(cwd), completed.returncode, completed.stdout, completed.stderr
    )


def _non_animated_env() -> dict[str, str]:
    return {"TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "120"}


# --------------------------------------------------------------------------
# P0 — installation and launch
# --------------------------------------------------------------------------


def check_documented_installation(env: ProdCheckEnvironment) -> CheckOutcome:
    """LAUNCH-01: the README's documented install command must be real."""
    readme = env.repo_root / "README.md"
    if not readme.is_file():
        return _failed("documented_installation", ["LAUNCH-01"], "README.md is absent")
    text = readme.read_text(encoding="utf-8")
    missing = [
        fragment
        for fragment in ("uv pip install", ".venv/bin/python", CANONICAL_EXECUTABLE)
        if fragment not in text
    ]
    if missing:
        return _failed(
            "documented_installation",
            ["LAUNCH-01"],
            "README does not document the install command and canonical executable",
            missing_fragments=missing,
        )
    return _passed(
        "documented_installation",
        ["LAUNCH-01"],
        "README documents the uv install command and the canonical executable",
    )


def check_entry_points(env: ProdCheckEnvironment) -> CheckOutcome:
    """LAUNCH-04: every documented entry point resolves and starts cleanly."""
    results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    names = (CANONICAL_EXECUTABLE, *COMPATIBILITY_EXECUTABLES, *ONE_SHOT_EXECUTABLES)
    try:
        import tomllib

        scripts = tomllib.loads((env.repo_root / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["scripts"]
    except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError) as exc:
        return _failed(
            "entry_points",
            ["LAUNCH-02", "LAUNCH-04"],
            f"cannot read registered console-script routing: {exc}",
        )
    canonical_target = scripts.get(CANONICAL_EXECUTABLE)
    for alias in COMPATIBILITY_EXECUTABLES:
        if scripts.get(alias) != canonical_target:
            failures.append(
                f"{alias}: routes to {scripts.get(alias)!r}, not canonical target "
                f"{canonical_target!r}"
            )
    for name in names:
        executable = env.executable(name)
        if not executable.exists():
            failures.append(f"{name}: not installed at {executable}")
            results[name] = {"installed": False}
            continue
        outcome = run_command(
            [str(executable), "--help"], cwd=env.repo_root, env=_non_animated_env()
        )
        results[name] = outcome.as_dict(stream_limit=600)
        if outcome.exit_status != 0:
            failures.append(f"{name}: --help exited {outcome.exit_status}")
    if failures:
        return _failed(
            "entry_points",
            ["LAUNCH-02", "LAUNCH-04"],
            "one or more documented entry points failed to start",
            failures=failures,
            results=results,
        )
    return _passed(
        "entry_points",
        ["LAUNCH-02", "LAUNCH-04"],
        f"all {len(names)} documented entry points started cleanly",
        results=results,
    )


def check_launch_repo_root(env: ProdCheckEnvironment) -> CheckOutcome:
    """LAUNCH-02/04: startup, palette, /Help and /Exit from the repository root."""
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "launch_repo_root",
            ["LAUNCH-02", "LAUNCH-04"],
            f"canonical executable is not installed at {executable}",
        )
    outcome = run_command(
        [str(executable), "--no-animation", "--no-color"],
        cwd=env.repo_root,
        stdin_text="/\n/Help\n/Exit\n",
        env=_non_animated_env(),
    )
    combined = outcome.stdout
    missing = [command for command in REQUIRED_SLASH_COMMANDS if command not in combined]
    if outcome.exit_status != 0 or missing:
        return _failed(
            "launch_repo_root",
            ["LAUNCH-02", "LAUNCH-04"],
            "repository-root launch did not expose every required command",
            missing_commands=missing,
            evidence=outcome.as_dict(),
        )
    return _passed(
        "launch_repo_root",
        ["LAUNCH-02", "LAUNCH-04"],
        "repository-root launch exposed the palette, /Help and /Exit",
        evidence=outcome.as_dict(stream_limit=2000),
    )


def check_launch_outside_repository(env: ProdCheckEnvironment) -> CheckOutcome:
    """LAUNCH-02/04: the installed application must work outside the repository."""
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "launch_outside_repository",
            ["LAUNCH-02", "LAUNCH-04"],
            f"canonical executable is not installed at {executable}",
        )
    with tempfile.TemporaryDirectory(prefix="hunter-prod-check-outside-") as raw_dir:
        work_dir = Path(raw_dir)
        outcome = run_command(
            [
                str(executable),
                "--no-animation",
                "--no-color",
                "--history-file",
                str(work_dir / "history"),
            ],
            cwd=work_dir,
            stdin_text="/Help\n/Exit\n",
            env=_non_animated_env(),
        )
        leaked = sorted(path.name for path in work_dir.iterdir() if path.name != "history")
    if outcome.exit_status != 0 or "/Help" not in outcome.stdout:
        return _failed(
            "launch_outside_repository",
            ["LAUNCH-02", "LAUNCH-04"],
            "the installed application failed outside the repository directory",
            evidence=outcome.as_dict(),
        )
    return _passed(
        "launch_outside_repository",
        ["LAUNCH-02", "LAUNCH-04"],
        "the installed application started and exited cleanly outside the repository",
        unexpected_artifacts=leaked,
        evidence=outcome.as_dict(stream_limit=1200),
    )


def check_built_wheel(env: ProdCheckEnvironment) -> CheckOutcome:
    """LAUNCH-02: a built wheel must install and launch in a clean environment."""
    if shutil.which("uv") is None:
        return _not_executed(
            "built_wheel", ["LAUNCH-02"], "uv is not available to build or install a wheel"
        )
    # The documented install command pins a repository-local uv cache; reuse it so
    # this check works in a restricted sandbox and stays reproducible.
    uv_env = {"UV_CACHE_DIR": str(env.repo_root / ".uv-cache")}
    with tempfile.TemporaryDirectory(prefix="hunter-prod-check-wheel-") as raw_dir:
        work_dir = Path(raw_dir)
        build = run_command(
            ["uv", "build", "--wheel", "--out-dir", str(work_dir)],
            cwd=env.repo_root,
            timeout=600.0,
            env=uv_env,
        )
        if build.exit_status != 0:
            return _failed(
                "built_wheel", ["LAUNCH-02"], "wheel build failed", evidence=build.as_dict()
            )
        wheels = sorted(work_dir.glob("*.whl"))
        if not wheels:
            return _failed(
                "built_wheel", ["LAUNCH-02"], "wheel build produced no artifact"
            )
        venv_dir = work_dir / "venv"
        created = run_command(
            ["uv", "venv", str(venv_dir)], cwd=work_dir, timeout=600.0, env=uv_env
        )
        if created.exit_status != 0:
            return _not_executed(
                "built_wheel",
                ["LAUNCH-02"],
                "a clean virtual environment could not be created",
                evidence=created.as_dict(),
            )
        installed = run_command(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv_dir / "bin" / "python"),
                str(wheels[-1]),
            ],
            cwd=work_dir,
            timeout=900.0,
            env=uv_env,
        )
        if installed.exit_status != 0:
            return _not_executed(
                "built_wheel",
                ["LAUNCH-02"],
                "the built wheel could not be installed offline in this environment",
                evidence=installed.as_dict(),
            )
        launched = run_command(
            [
                str(venv_dir / "bin" / CANONICAL_EXECUTABLE),
                "--no-animation",
                "--no-color",
                "--history-file",
                str(work_dir / "history"),
            ],
            cwd=work_dir,
            stdin_text="/Help\n/Exit\n",
            env=_non_animated_env(),
        )
        if launched.exit_status != 0:
            return _failed(
                "built_wheel",
                ["LAUNCH-02"],
                "the wheel-installed canonical executable failed to launch",
                wheel=wheels[-1].name,
                evidence=launched.as_dict(),
            )
        return _passed(
            "built_wheel",
            ["LAUNCH-02"],
            "the built wheel installed and launched in a clean environment",
            wheel=wheels[-1].name,
            evidence=launched.as_dict(stream_limit=800),
        )


def check_operator_installation_surfaces(env: ProdCheckEnvironment) -> CheckOutcome:
    """LAUNCH-01/02: execute upgrade and fresh all-extras operator installs."""
    requirements = ["LAUNCH-01", "LAUNCH-02", "LAUNCH-03", "LAUNCH-04"]
    uv = shutil.which("uv")
    if uv is None:
        return _not_executed(
            "operator_installation_surfaces",
            requirements,
            "uv is not available for the documented operator installation",
        )

    extras = "dev,radio,science,ml,track_a,photometry"
    install_target = f"{env.repo_root}[{extras}]"
    uv_env = {"UV_CACHE_DIR": str(env.repo_root / ".uv-cache")}
    clean_keys = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
    evidence: dict[str, Any] = {}

    existing_python = env.bin_dir / "python"
    if not existing_python.exists():
        return _not_executed(
            "operator_installation_surfaces",
            requirements,
            f"supported operator interpreter is absent: {existing_python}",
        )
    upgrade = run_command(
        [uv, "pip", "install", "--python", str(existing_python), "-e", install_target],
        cwd=env.repo_root,
        timeout=1200.0,
        env=uv_env,
        remove_env=clean_keys,
    )
    evidence["upgrade_in_place"] = upgrade.as_dict(stream_limit=1600)
    if upgrade.exit_status != 0:
        return _failed(
            "operator_installation_surfaces",
            requirements,
            "the documented upgrade-in-place installation failed",
            evidence=evidence,
        )
    existing_patch = run_command(
        [
            "bash",
            str(env.repo_root / "scripts" / "patch_turbo_seti_numpy2_compat.sh"),
            "--python",
            str(existing_python),
        ],
        cwd=env.repo_root,
        timeout=120.0,
        remove_env=clean_keys,
    )
    evidence["upgrade_patch"] = existing_patch.as_dict(stream_limit=1200)
    if existing_patch.exit_status != 0:
        return _failed(
            "operator_installation_surfaces",
            requirements,
            "the required turboSETI compatibility patch failed after upgrade",
            evidence=evidence,
        )

    with tempfile.TemporaryDirectory(prefix="hunter-prod-check-fresh-") as raw_dir:
        work_dir = Path(raw_dir)
        fresh_venv = work_dir / "venv"
        created = run_command(
            [uv, "venv", "--python", str(existing_python), str(fresh_venv)],
            cwd=work_dir,
            timeout=600.0,
            env=uv_env,
            remove_env=clean_keys,
        )
        evidence["fresh_venv"] = created.as_dict(stream_limit=1000)
        if created.exit_status != 0:
            return _not_executed(
                "operator_installation_surfaces",
                requirements,
                "the fresh operator environment could not be created",
                evidence=evidence,
            )
        fresh_python = fresh_venv / "bin" / "python"
        fresh_install = run_command(
            [uv, "pip", "install", "--python", str(fresh_python), "-e", install_target],
            cwd=work_dir,
            timeout=1200.0,
            env=uv_env,
            remove_env=clean_keys,
        )
        evidence["fresh_install"] = fresh_install.as_dict(stream_limit=1600)
        if fresh_install.exit_status != 0:
            return _not_executed(
                "operator_installation_surfaces",
                requirements,
                "the documented all-extras install did not complete in a fresh environment",
                evidence=evidence,
            )
        fresh_patch = run_command(
            [
                "bash",
                str(env.repo_root / "scripts" / "patch_turbo_seti_numpy2_compat.sh"),
                "--python",
                str(fresh_python),
            ],
            cwd=work_dir,
            timeout=120.0,
            remove_env=clean_keys,
        )
        evidence["fresh_patch"] = fresh_patch.as_dict(stream_limit=1200)
        if fresh_patch.exit_status != 0:
            return _failed(
                "operator_installation_surfaces",
                requirements,
                "the required turboSETI patch failed in the fresh environment",
                evidence=evidence,
            )
        probe = run_command(
            [
                str(fresh_python),
                "-c",
                "import turbo_seti.find_doppler.find_doppler as m; print(m.__file__)",
            ],
            cwd=work_dir,
            timeout=120.0,
            remove_env=clean_keys,
        )
        evidence["fresh_turbo_probe"] = probe.as_dict(stream_limit=1200)
        if probe.exit_status != 0:
            return _failed(
                "operator_installation_surfaces",
                requirements,
                "turboSETI is not importable after the fresh all-extras installation",
                evidence=evidence,
            )
        turbo_source = Path(_last_nonempty_line(probe.stdout))
        fixed_line = '" is: %i" % max_val.total_n_hits[0])'
        if not turbo_source.is_file() or fixed_line not in turbo_source.read_text(
            encoding="utf-8"
        ):
            return _failed(
                "operator_installation_surfaces",
                requirements,
                "the documented patch did not modify the fresh environment",
                evidence=evidence,
                expected_source=str(turbo_source),
            )

        launches: dict[str, Any] = {}
        for executable_name in (CANONICAL_EXECUTABLE, *COMPATIBILITY_EXECUTABLES):
            launched = run_command(
                [
                    str(fresh_venv / "bin" / executable_name),
                    "--no-animation",
                    "--no-color",
                    "--history-file",
                    str(work_dir / f"{executable_name}.history"),
                ],
                cwd=work_dir,
                stdin_text="/Help\n/Exit\n",
                env=_non_animated_env(),
                remove_env=clean_keys,
            )
            launches[executable_name] = launched.as_dict(stream_limit=1000)
            if launched.exit_status != 0 or "/Help" not in launched.stdout:
                evidence["fresh_launches"] = launches
                return _failed(
                    "operator_installation_surfaces",
                    requirements,
                    f"fresh-installed {executable_name} failed outside the repository",
                    evidence=evidence,
                )
        evidence["fresh_launches"] = launches

    return _passed(
        "operator_installation_surfaces",
        requirements,
        "upgrade-in-place and fresh all-extras installs launched both executable names",
        evidence=evidence,
    )


def check_invalid_input_is_actionable(env: ProdCheckEnvironment) -> CheckOutcome:
    """LAUNCH-04 and UX-IN-03: invalid input must not dump a traceback."""
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "invalid_input", ["LAUNCH-04", "CLI-03"], "canonical executable is not installed"
        )
    outcome = run_command(
        [str(executable), "--no-animation", "--no-color"],
        cwd=env.repo_root,
        stdin_text="/Nope\n",
        env=_non_animated_env(),
    )
    streams = outcome.stdout + outcome.stderr
    if outcome.exit_status == 0:
        return _failed(
            "invalid_input",
            ["LAUNCH-04", "CLI-03"],
            "an unknown command exited zero",
            evidence=outcome.as_dict(),
        )
    if "Traceback (most recent call last)" in streams:
        return _failed(
            "invalid_input",
            ["LAUNCH-04", "CLI-03"],
            "an unknown command produced a raw traceback",
            evidence=outcome.as_dict(),
        )
    if "unknown command" not in streams:
        return _failed(
            "invalid_input",
            ["LAUNCH-04", "CLI-03"],
            "an unknown command produced no actionable operator message",
            evidence=outcome.as_dict(),
        )
    return _passed(
        "invalid_input",
        ["LAUNCH-04", "CLI-03"],
        "invalid input exits non-zero with an actionable message and no traceback",
        evidence=outcome.as_dict(stream_limit=600),
    )


# --------------------------------------------------------------------------
# P3 — CLI and UX conformance
# --------------------------------------------------------------------------


def check_command_palette(env: ProdCheckEnvironment) -> CheckOutcome:
    """UX-CMD-01/02: `/` opens a described, searchable palette."""
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "command_palette", ["CLI-01", "CLI-02"], "canonical executable is not installed"
        )
    outcome = run_command(
        [str(executable), "--no-animation", "--no-color"],
        cwd=env.repo_root,
        stdin_text="/\n/Exit\n",
        env=_non_animated_env(),
    )
    text = outcome.stdout
    missing_commands = [command for command in REQUIRED_SLASH_COMMANDS if command not in text]
    missing_shape = [label for label in ("Required", "Optional") if label not in text]
    if outcome.exit_status != 0 or missing_commands or missing_shape:
        return _failed(
            "command_palette",
            ["CLI-01", "CLI-02"],
            "typing / did not open a described palette covering every required command",
            missing_commands=missing_commands,
            missing_parameter_shape=missing_shape,
            evidence=outcome.as_dict(),
        )
    return _passed(
        "command_palette",
        ["CLI-01", "CLI-02"],
        "typing / opens a palette listing every required command with parameter shapes",
        evidence=outcome.as_dict(stream_limit=2500),
    )


def check_interactive_pty_palette(env: ProdCheckEnvironment) -> CheckOutcome:
    """UX-CMD-01/03: a bare ``/`` keystroke opens the palette in a real terminal.

    This is the primary interactive-operator gate. It spawns the installed
    canonical executable as a separate operating-system process attached to a
    real pseudo-terminal, from a directory outside the repository, and sends
    individual keystrokes.

    ``check_command_palette`` pipes ``"/\\n"`` on stdin, which a line-buffered
    ``input()`` loop satisfies. That is explicitly not sufficient: the
    specification requires the palette to open on ``/`` *without* Enter, so
    only a real terminal can observe it.
    """
    requirements = ["CLI-01", "CLI-02", "CLI-03"]
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "interactive_pty_palette", requirements, "canonical executable is not installed"
        )
    # Outside the repository, so no source tree can be resolved implicitly.
    outside = Path(tempfile.gettempdir()).resolve()
    capture = drive_palette_session(executable, cwd=outside)
    if not capture.pty_available:
        return _not_executed(
            "interactive_pty_palette",
            requirements,
            f"the host denied pseudo-terminal allocation ({capture.unavailable_reason})",
            evidence=capture.as_dict(),
        )

    problems: list[str] = []
    try:
        startup = capture.step("startup")["plain"]
        bare = capture.step("bare_slash")["plain"]
        navigate = capture.step("navigate")["plain"]
        escape = capture.step("escape")["plain"]
    except KeyError as exc:
        return _failed(
            "interactive_pty_palette",
            requirements,
            f"the session ended before step {exc} was reached",
            evidence=capture.as_dict(),
        )

    if "TechnoHunter" not in startup:
        problems.append("startup did not present the product identity")
    if __version__ not in startup:
        problems.append("startup did not present the product version")

    # The decisive assertion: the palette must appear from "/" alone.
    missing_commands = [
        command for command in REQUIRED_SLASH_COMMANDS if command not in bare
    ]
    if missing_commands:
        problems.append(
            "a bare '/' keystroke did not open a palette listing "
            + ", ".join(missing_commands)
        )
    if not any(label in bare for label in ("Required", "Optional")):
        problems.append("the palette did not show parameter shapes")
    if not navigate.strip():
        problems.append("Down-arrow produced no visible selection change")
    if capture.exit_status not in (0, None):
        problems.append(f"the shell exited {capture.exit_status} rather than 0")
    if capture.timed_out:
        problems.append("the shell did not exit after /Exit")
    if "\x1b" in escape and not escape.strip():  # pragma: no cover - defensive
        problems.append("Escape left the terminal in an undefined state")

    if problems:
        return _failed(
            "interactive_pty_palette",
            requirements,
            "the installed shell failed the real-terminal palette gate",
            problems=problems,
            evidence=capture.as_dict(),
        )
    return _passed(
        "interactive_pty_palette",
        requirements,
        "a bare '/' keystroke opens the described palette in a real terminal",
        evidence=capture.as_dict(stream_limit=2500),
    )


def check_guided_input_and_validation(env: ProdCheckEnvironment) -> CheckOutcome:
    """UX-IN-01/03/04: guided fields and live validity sentinels."""
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "guided_input", ["CLI-01", "CLI-03"], "canonical executable is not installed"
        )
    fields = run_command(
        [str(executable), "--no-animation", "--no-color"],
        cwd=env.repo_root,
        stdin_text="/New-Search\n/Exit\n",
        env=_non_animated_env(),
    )
    invalid = run_command(
        [str(executable), "--no-animation", "--no-color"],
        cwd=env.repo_root,
        stdin_text="/New-Search twenty\n",
        env=_non_animated_env(),
    )
    zero = run_command(
        [str(executable), "--no-animation", "--no-color"],
        cwd=env.repo_root,
        stdin_text="/New-Search 0\n",
        env=_non_animated_env(),
    )
    problems: list[str] = []
    if "Targets" not in fields.stdout:
        problems.append("no guided Targets field was presented")
    if "positive whole number" not in (invalid.stdout + invalid.stderr):
        problems.append("a non-numeric target count produced no type sentinel")
    if "greater than zero" not in (zero.stdout + zero.stderr):
        problems.append("a zero target count produced no range sentinel")
    for label, result in (("non-numeric", invalid), ("zero", zero)):
        if result.exit_status == 0:
            problems.append(f"invalid {label} target count exited zero")
        if "Traceback (most recent call last)" in result.stdout + result.stderr:
            problems.append(f"invalid {label} target count produced a raw traceback")
        if "usage:" in (result.stdout + result.stderr):
            problems.append(f"invalid {label} target count fell through to raw argparse")
    if problems:
        return _failed(
            "guided_input",
            ["CLI-01", "CLI-03"],
            "guided parameter entry and live validity sentinels are not conforming",
            problems=problems,
            fields_evidence=fields.as_dict(stream_limit=1500),
            invalid_evidence=invalid.as_dict(stream_limit=800),
            zero_evidence=zero.as_dict(stream_limit=800),
        )
    return _passed(
        "guided_input",
        ["CLI-01", "CLI-03"],
        "guided fields and live type/range sentinels reject invalid input actionably",
        fields_evidence=fields.as_dict(stream_limit=1500),
    )


def check_animation_degrades(env: ProdCheckEnvironment) -> CheckOutcome:
    """UX-START-04 and UX-A11Y-01: animation must degrade for machine output."""
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "animation_degrades", ["CLI-01"], "canonical executable is not installed"
        )
    outcome = run_command(
        [str(executable)],
        cwd=env.repo_root,
        stdin_text="/Help\n/Exit\n",
        env={"TERM": "dumb", "NO_COLOR": "1", "CI": "1", "COLUMNS": "120"},
    )
    if outcome.exit_status != 0:
        return _failed(
            "animation_degrades",
            ["CLI-01"],
            "non-TTY startup failed",
            evidence=outcome.as_dict(),
        )
    if "\x1b[" in outcome.stdout:
        return _failed(
            "animation_degrades",
            ["CLI-01"],
            "redirected non-TTY output still contains ANSI control sequences",
            evidence=outcome.as_dict(stream_limit=800),
        )
    return _passed(
        "animation_degrades",
        ["CLI-01"],
        "redirected non-TTY output is free of ANSI control sequences",
    )


def check_machine_output_is_clean(env: ProdCheckEnvironment) -> CheckOutcome:
    """UX-TABLE-04: machine-readable output must parse with no decoration."""
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "machine_output", ["CLI-01", "CLI-03"], "canonical executable is not installed"
        )
    outcome = run_command(
        [str(executable), "--no-animation", "--no-color"],
        cwd=env.repo_root,
        stdin_text="/Show-Follow-Ups --json\n/Exit\n",
        env=_non_animated_env(),
    )
    payload = outcome.stdout
    brace = payload.find("{")
    if outcome.exit_status != 0 or brace < 0:
        return _failed(
            "machine_output",
            ["CLI-01", "CLI-03"],
            "the machine-readable follow-up registry did not render",
            evidence=outcome.as_dict(),
        )
    tail = payload.rfind("}")
    try:
        parsed = json.loads(payload[brace : tail + 1])
    except json.JSONDecodeError as exc:
        return _failed(
            "machine_output",
            ["CLI-01", "CLI-03"],
            f"machine-readable output is not valid JSON: {exc}",
            evidence=outcome.as_dict(stream_limit=1200),
        )
    if "\x1b[" in payload:
        return _failed(
            "machine_output",
            ["CLI-01", "CLI-03"],
            "machine-readable output contains ANSI control sequences",
        )
    return _passed(
        "machine_output",
        ["CLI-01", "CLI-03"],
        "machine-readable follow-up output is clean parseable JSON",
        top_level_keys=sorted(parsed)[:12],
    )


def check_result_table_width_awareness(env: ProdCheckEnvironment) -> CheckOutcome:
    """UX-TABLE-01: the result table must respect terminal width."""
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "result_table", ["CLI-01"], "canonical executable is not installed"
        )
    observations: dict[str, Any] = {}
    problems: list[str] = []
    for width in (80, 140):
        outcome = run_command(
            [str(executable), "--no-animation", "--no-color"],
            cwd=env.repo_root,
            stdin_text="/Inspect-Target\n/Exit\n",
            env={"TERM": "dumb", "NO_COLOR": "1", "COLUMNS": str(width)},
        )
        lines = [line.rstrip() for line in outcome.stdout.splitlines() if line.strip()]
        longest = max((len(line) for line in lines), default=0)
        observations[f"columns_{width}"] = {
            "exit_status": outcome.exit_status,
            "longest_line": longest,
            "line_count": len(lines),
            "sample": lines[:6],
            "stderr": outcome.stderr[:400],
        }
        if outcome.exit_status != 0:
            problems.append(f"width {width}: exited {outcome.exit_status}")
            continue
        if longest > width:
            problems.append(f"width {width}: emitted a {longest}-column line")
    if problems:
        return _failed(
            "result_table",
            ["CLI-01"],
            "the result table is not width-aware",
            problems=problems,
            observations=observations,
        )
    return _passed(
        "result_table",
        ["CLI-01"],
        "the result table stays within 80 and 140 column terminals",
        observations=observations,
    )


def check_action_preview(env: ProdCheckEnvironment) -> CheckOutcome:
    """CLI_UX_SPEC section 8: a resolved-action preview precedes freezing."""
    executable = env.executable(CANONICAL_EXECUTABLE)
    if not executable.exists():
        return _not_executed(
            "action_preview", ["CLI-01"], "canonical executable is not installed"
        )
    outcome = run_command(
        [str(executable), "--no-animation", "--no-color"],
        cwd=env.repo_root,
        stdin_text="/New-Search 5 --preview-only\n/Exit\n",
        env=_non_animated_env(),
    )
    required_rows = (
        "Mode:",
        "Requested targets:",
        "Scientific constraints:",
        "Primary sources:",
        "Source freshness:",
        "Cross-project history freshness:",
        "Estimated discovery universe:",
        "Estimated storage:",
        "Estimated compute:",
        "Output behavior:",
    )
    missing = [row for row in required_rows if row not in outcome.stdout]
    if outcome.exit_status != 0 or missing:
        return _failed(
            "action_preview",
            ["CLI-01"],
            "the resolved-action preview is missing required rows",
            missing_rows=missing,
            evidence=outcome.as_dict(),
        )
    return _passed(
        "action_preview",
        ["CLI-01"],
        "the resolved-action preview reports every required row before freezing",
        evidence=outcome.as_dict(stream_limit=2000),
    )


def check_golden_ux_tests(env: ProdCheckEnvironment) -> CheckOutcome:
    """CLI_UX_SPEC section 13: semantic golden assertions must exist."""
    golden_dir = env.repo_root / "tests" / "golden"
    missing = [name for name in REQUIRED_GOLDEN_TESTS if not (golden_dir / name).is_file()]
    if missing:
        return _failed(
            "golden_ux_tests",
            ["CLI-01", "EVAL-01"],
            "required golden UX baselines are absent",
            missing=missing,
            golden_dir=str(golden_dir),
        )
    return _passed(
        "golden_ux_tests",
        ["CLI-01", "EVAL-01"],
        f"all {len(REQUIRED_GOLDEN_TESTS)} golden UX baselines exist",
        golden_dir=str(golden_dir),
    )


# --------------------------------------------------------------------------
# P1/P2 — pipeline, identity, durability
# --------------------------------------------------------------------------


def check_canonical_routing(env: ProdCheckEnvironment) -> CheckOutcome:
    """PIPE-01/02 and CLI-03: the shell must not duplicate business logic."""
    shell_source = env.repo_root / "src" / "techno_search" / "hunter_shell.py"
    if not shell_source.is_file():
        return _failed("canonical_routing", ["PIPE-01"], "hunter_shell.py is absent")
    text = shell_source.read_text(encoding="utf-8")
    forbidden = {
        "create_search(": "the shell must call the canonical create entry point, not the optimizer",
        "run_search(": "the shell must call the canonical run entry point, not the runner",
        "follow_up_registry(": "the shell must not read the registry directly",
    }
    violations = [reason for token, reason in forbidden.items() if token in text]
    if violations:
        return _failed(
            "canonical_routing",
            ["PIPE-01", "PIPE-02", "CLI-03"],
            "the presentation layer reaches past the canonical entry points",
            violations=violations,
        )
    return _passed(
        "canonical_routing",
        ["PIPE-01", "PIPE-02", "CLI-03"],
        "the shell delegates every command to the canonical Hunter entry points",
    )


def check_durable_record_kinds(env: ProdCheckEnvironment) -> CheckOutcome:
    """DUR-01: five distinct durable record kinds must exist on disk."""
    searches_dir = env.repo_root / "results" / "searches"
    required = {
        "candidate_catalog": env.repo_root
        / "data_selection"
        / "bl_archive_candidate_catalog.csv",
        "review_manifest": env.repo_root / "data_selection" / "target_priority_queue.csv",
        "target_search_history": env.repo_root / "results" / "scan_history.ndjson",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    have_dir = searches_dir.is_dir()
    search_runs = sorted(searches_dir.glob("SEARCH-*/manifest.json")) if have_dir else []
    event_logs = sorted(searches_dir.glob("SEARCH-*/events.ndjson")) if have_dir else []
    if not search_runs:
        missing.append("search_run")
    if not event_logs:
        missing.append("follow_up_registry_events")
    if missing:
        return _failed(
            "durable_record_kinds",
            ["DUR-01"],
            "one or more durable record kinds are absent",
            missing=missing,
        )
    return _passed(
        "durable_record_kinds",
        ["DUR-01"],
        "all five durable record kinds are present",
        search_manifest_count=len(search_runs),
        event_log_count=len(event_logs),
    )


def check_exact_target_freezing(env: ProdCheckEnvironment) -> CheckOutcome:
    """DUR-02: a completed run must have executed its frozen manifest targets."""
    searches_dir = env.repo_root / "results" / "searches"
    if not searches_dir.is_dir():
        return _not_executed(
            "exact_target_freezing", ["DUR-02"], "no durable searches directory exists"
        )
    inspected: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for manifest_path in sorted(searches_dir.glob("SEARCH-*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            mismatches.append({"manifest": str(manifest_path), "error": str(exc)})
            continue
        frozen = [
            str(target.get("hip") or target.get("target_id") or "")
            for target in manifest.get("targets", [])
        ]
        events_path = manifest_path.parent / "events.ndjson"
        if not events_path.is_file():
            continue
        completed: list[str] = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "run_completed":
                completed = [str(name) for name in event.get("targets", frozen)]
        if not completed:
            continue
        inspected.append(
            {
                "search_id": manifest.get("search_id"),
                "frozen_count": len(frozen),
                "executed_count": len(completed),
            }
        )
        if set(completed) - set(frozen):
            mismatches.append(
                {
                    "search_id": manifest.get("search_id"),
                    "substituted": sorted(set(completed) - set(frozen)),
                }
            )
    if mismatches:
        return _failed(
            "exact_target_freezing",
            ["DUR-02"],
            "a completed run executed targets outside its immutable manifest",
            mismatches=mismatches,
        )
    if not inspected:
        return _not_executed(
            "exact_target_freezing",
            ["DUR-02"],
            "no completed durable run was available to compare against its manifest",
        )
    return _passed(
        "exact_target_freezing",
        ["DUR-02"],
        f"{len(inspected)} completed run(s) executed only their frozen targets",
        inspected=inspected[:10],
    )


DISC_02_REQUIRED_FIELDS = (
    "requested_count",
    "candidate_count",
    "eligible_count",
    "rejection_counts_by_reason",
    "sources",
    "round_count",
    "rounds",
    "selection_cutoff_score",
    "highest_unresolved_score",
    "universe_exhausted",
    "expandable_count",
    "termination_reason",
    "strategy",
)


def check_adaptive_discovery_evidence(env: ProdCheckEnvironment) -> CheckOutcome:
    """DISC-01/02: sufficiency evidence must be persisted in the frozen manifest."""
    searches_dir = env.repo_root / "results" / "searches"
    manifests = (
        sorted(searches_dir.glob("SEARCH-*/manifest.json")) if searches_dir.is_dir() else []
    )
    new_mode: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in manifests:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("mode") != "new":
            continue
        block = payload.get("selection", {}).get("adaptive_discovery")
        if isinstance(block, dict):
            new_mode.append((manifest_path, block))
    if not new_mode:
        return _not_executed(
            "adaptive_discovery",
            ["DISC-01", "DISC-02"],
            "no frozen new-mode search carries an adaptive-discovery block",
        )
    manifest_path, block = new_mode[-1]
    missing = [name for name in DISC_02_REQUIRED_FIELDS if name not in block]
    if missing:
        return _failed(
            "adaptive_discovery",
            ["DISC-01", "DISC-02"],
            "the persisted sufficiency evidence omits required DISC-02 fields",
            missing=missing,
            manifest=str(manifest_path.relative_to(env.repo_root)),
        )
    quality = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        .get("selection", {})
        .get("quality")
    )
    if not isinstance(quality, dict) or "interpretation" not in quality:
        return _failed(
            "adaptive_discovery",
            ["DISC-02"],
            "the persisted evidence omits the quality distribution and its limitations",
            manifest=str(manifest_path.relative_to(env.repo_root)),
        )
    return _passed(
        "adaptive_discovery",
        ["DISC-01", "DISC-02"],
        "the frozen manifest persists complete adaptive-discovery sufficiency evidence",
        manifest=str(manifest_path.relative_to(env.repo_root)),
        new_mode_manifest_count=len(new_mode),
        termination_reason=block.get("termination_reason"),
        strategy=block.get("strategy"),
    )


def check_identity_and_history(env: ProdCheckEnvironment) -> CheckOutcome:
    """IDENT-01/03: the cross-project history contract must fail closed."""
    module = env.repo_root / "src" / "techno_search" / "hunter_cross_project_history.py"
    if not module.is_file():
        return _failed(
            "identity_and_history",
            ["IDENT-01", "IDENT-03"],
            "the cross-project history module is absent",
        )
    text = module.read_text(encoding="utf-8")
    required_states = ("valid", "stale-but-usable", "refresh-required", "invalid", "unknown")
    missing = [state for state in required_states if f'"{state}"' not in text]
    if missing:
        return _failed(
            "identity_and_history",
            ["IDENT-01", "IDENT-03"],
            "the history validity vocabulary is incomplete",
            missing_states=missing,
        )
    # Vocabulary alone is not the requirement. IDENT-03 says New eligibility
    # must FAIL CLOSED when history is incomplete, so the selection path has to
    # consume the decision states. Checking only that the contract module
    # spells them lets the New path ignore history entirely and still pass.
    selection = env.repo_root / "src" / "techno_search" / "hunter_search.py"
    if not selection.is_file():
        return _failed(
            "identity_and_history",
            ["IDENT-01", "IDENT-03"],
            "the canonical selection module is absent",
        )
    selection_text = selection.read_text(encoding="utf-8")
    gates = (
        "CROSS_PROJECT_DECISION_STATES",
        "cross_project_history_validity",
    )
    if not any(gate in selection_text for gate in gates):
        return _failed(
            "identity_and_history",
            ["IDENT-01", "IDENT-03", "IDENT-04"],
            "New eligibility does not consume the cross-project history validity "
            "state, so an absent or invalid sibling history still yields "
            "prior_search_count=0 as if novelty had been verified",
            expected_any_of=list(gates),
            selection_module="src/techno_search/hunter_search.py",
        )
    return _passed(
        "identity_and_history",
        ["IDENT-01", "IDENT-03"],
        "the history contract implements every validity state and the New "
        "selection path gates eligibility on the decision states",
    )


def check_restart_and_resume(env: ProdCheckEnvironment) -> CheckOutcome:
    """E2E-03 and DUR-04: resume must reuse the same run without duplication."""
    searches_dir = env.repo_root / "results" / "searches"
    if not searches_dir.is_dir():
        return _not_executed(
            "restart_and_resume", ["E2E-03", "DUR-04"], "no durable searches directory exists"
        )
    resumed: list[dict[str, Any]] = []
    for events_path in sorted(searches_dir.glob("SEARCH-*/events.ndjson")):
        events = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        names = [str(event.get("event", "")) for event in events]
        if "run_resumed" not in names:
            continue
        failed = [event for event in events if event.get("event") == "run_failed"]
        completed = [event for event in events if event.get("event") == "run_completed"]
        resumed.append(
            {
                "search_id": events_path.parent.name,
                "sequence": names,
                "failure_preserved": bool(failed),
                "completed_once": len(completed) <= 1,
            }
        )
    if not resumed:
        return _not_executed(
            "restart_and_resume",
            ["E2E-03", "DUR-04"],
            "no durable search recorded a failure-then-resume cycle",
        )
    broken = [entry for entry in resumed if not entry["failure_preserved"]]
    if broken:
        return _failed(
            "restart_and_resume",
            ["E2E-03", "DUR-04"],
            "a resumed search did not preserve its durable failure event",
            broken=broken,
        )
    return _passed(
        "restart_and_resume",
        ["E2E-03", "DUR-04"],
        f"{len(resumed)} search(es) preserve a durable failure-then-resume sequence",
        resumed=resumed[:5],
    )


def check_real_data_evidence(env: ProdCheckEnvironment) -> CheckOutcome:
    """E2E-04: require current 5+5 installed real-source acceptance evidence."""
    evidence_dir = env.repo_root / "docs" / "evidence"
    bundles = sorted(evidence_dir.glob("hunter_*acceptance*.json")) if evidence_dir.is_dir() else []
    if not bundles:
        return _not_executed(
            "real_data_evidence",
            ["E2E-04", "CLAIM-04"],
            "no machine-readable Hunter acceptance evidence bundle exists",
        )
    expected_commit = _git_commit(env.repo_root)
    expected_runtime_sha256 = _runtime_code_sha256(env.repo_root)
    expected_executable = str(env.executable(CANONICAL_EXECUTABLE).resolve())
    summaries: list[dict[str, Any]] = []
    qualifying: list[Path] = []
    for bundle in bundles:
        try:
            payload = json.loads(bundle.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _failed(
                "real_data_evidence",
                ["E2E-04", "CLAIM-04"],
                f"acceptance evidence {bundle.name} is unreadable: {exc}",
            )
        problems = _real_data_bundle_problems(
            payload,
            repo_root=env.repo_root,
            expected_app_version=__version__,
            expected_commit=expected_commit,
            expected_runtime_sha256=expected_runtime_sha256,
            expected_executable=expected_executable,
        )
        if not problems:
            qualifying.append(bundle)
        summaries.append(
            {
                "bundle": bundle.name,
                "schema_version": payload.get("schema_version"),
                "problems": problems,
            }
        )
    if not qualifying:
        return _not_executed(
            "real_data_evidence",
            ["E2E-04", "CLAIM-04"],
            (
                "no current installed 5-New/5-Follow-up real-source acceptance "
                "bundle satisfies the Phase 5 evidence contract"
            ),
            expected={
                "schema_version": REAL_DATA_ACCEPTANCE_SCHEMA_VERSION,
                "app_version": __version__,
                "code_commit": expected_commit,
                "runtime_tree_sha256": expected_runtime_sha256,
                "installed_entry_point": CANONICAL_EXECUTABLE,
                "resolved_executable": expected_executable,
            },
            bundles=summaries,
        )
    return _passed(
        "real_data_evidence",
        ["E2E-04", "CLAIM-04"],
        f"{len(qualifying)} current installed 5+5 real-data evidence bundle(s) pass",
        qualifying_bundles=[path.name for path in qualifying],
        bundles=summaries,
    )


_REAL_DATA_ARTIFACT_ROLES = {
    "commands",
    "keystrokes",
    "transcript",
    "new_manifest",
    "follow_up_manifest",
    "source_watermarks",
    "requests",
    "responses",
    "environment",
    "package_versions",
    "durable_state_export",
    "restart_resume",
}


def _runtime_code_sha256(repo_root: Path) -> str:
    """Fingerprint the installed Hunter runtime and its production scripts."""

    paths = [repo_root / "pyproject.toml"]
    for directory in (repo_root / "src" / "techno_search", repo_root / "scripts"):
        if directory.is_dir():
            paths.extend(
                path
                for path in directory.rglob("*")
                if path.is_file() and path.suffix in {".py", ".sh"}
            )
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        digest.update(path.relative_to(repo_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def _real_data_bundle_problems(
    payload: object,
    *,
    repo_root: Path,
    expected_app_version: str,
    expected_commit: str | None,
    expected_runtime_sha256: str,
    expected_executable: str,
) -> list[str]:
    """Return every reason a bundle cannot satisfy the explicit Phase 5 gate."""

    if not isinstance(payload, dict):
        return ["bundle is not a JSON object"]
    problems: list[str] = []
    if payload.get("schema_version") != REAL_DATA_ACCEPTANCE_SCHEMA_VERSION:
        problems.append("schema is not hunter_prod_live_acceptance_v3")

    release = payload.get("release")
    if not isinstance(release, dict):
        problems.append("release metadata is absent")
        release = {}
    if release.get("app_version") != expected_app_version:
        problems.append("app version does not match the installed gate version")
    if release.get("installed_entry_point") != CANONICAL_EXECUTABLE:
        problems.append("canonical installed entry point was not used")
    if release.get("resolved_executable") != expected_executable:
        problems.append("resolved executable does not match the gate environment")
    code_identity = release.get("code_identity")
    if not isinstance(code_identity, dict):
        problems.append("tested code identity is absent")
        code_identity = {}
    if code_identity.get("git_commit") != expected_commit:
        problems.append("tested Git commit does not match the gate checkout")
    if code_identity.get("runtime_tree_sha256") != expected_runtime_sha256:
        problems.append("tested runtime-tree hash does not match the gate checkout")

    scope = payload.get("scope")
    if not isinstance(scope, dict):
        problems.append("acceptance scope is absent")
        scope = {}
    for field_name in ("fixtures_used", "mocks_used", "imported_snapshots_used"):
        if scope.get(field_name) is not False:
            problems.append(f"{field_name} must be false")
    if scope.get("real_authoritative_sources") is not True:
        problems.append("real authoritative sources are not affirmed")
    for field_name in (
        "detection_claimed",
        "discovery_claimed",
        "expert_review_claimed",
        "external_validation_claimed",
        "external_submission_allowed",
    ):
        if scope.get(field_name) is not False:
            problems.append(f"{field_name} must be false")

    operator = payload.get("operator_session")
    if not isinstance(operator, dict):
        problems.append("installed operator-session evidence is absent")
        operator = {}
    if operator.get("terminated") is not True:
        problems.append("operator termination is not proven")
    if not operator.get("installation_command"):
        problems.append("operator installation command is absent")

    searches = payload.get("searches")
    if not isinstance(searches, list):
        problems.append("search evidence is not a list")
        searches = []
    by_mode = {
        item.get("mode"): item
        for item in searches
        if isinstance(item, dict) and item.get("mode") in {"new", "follow-up"}
    }
    for mode in ("new", "follow-up"):
        search = by_mode.get(mode)
        if not isinstance(search, dict):
            problems.append(f"{mode} search evidence is absent")
            continue
        target_ids = search.get("target_ids")
        ordered_ids = search.get("ordered_manifest_target_ids")
        executed_ids = search.get("executed_target_ids")
        if search.get("requested_target_count") != 5:
            problems.append(f"{mode} requested target count is not 5")
        if search.get("target_count") != 5:
            problems.append(f"{mode} frozen target count is not 5")
        if not isinstance(target_ids, list) or len(target_ids) != 5 or len(set(target_ids)) != 5:
            problems.append(f"{mode} target IDs are not five distinct identities")
        if ordered_ids != target_ids or executed_ids != target_ids:
            problems.append(f"{mode} execution did not preserve exact frozen ordering")
        if search.get("manifest_checksum_verified") is not True or not _is_sha256(
            search.get("manifest_sha256")
        ):
            problems.append(f"{mode} manifest checksum is not verified")
        if search.get("history_updated") is not True:
            problems.append(f"{mode} durable history update is not proven")
        provenance = search.get("source_provenance")
        if not isinstance(provenance, list) or not provenance:
            problems.append(f"{mode} authoritative source provenance is absent")
        elif any(
            not isinstance(item, dict)
            or not item.get("source_identity")
            or not item.get("watermark")
            for item in provenance
        ):
            problems.append(f"{mode} source identity or watermark is incomplete")
        records = search.get("durable_target_records")
        if not isinstance(records, list) or len(records) != 5:
            problems.append(f"{mode} does not persist five per-target records")
        else:
            record_ids = [item.get("target_id") for item in records if isinstance(item, dict)]
            if record_ids != target_ids:
                problems.append(f"{mode} per-target records do not match frozen ordering")
            for item in records:
                if not isinstance(item, dict):
                    continue
                if not item.get("state") or not item.get("provenance"):
                    problems.append(f"{mode} target state or provenance is incomplete")
                    break
                if "result" not in item and "failure" not in item:
                    problems.append(f"{mode} target result/failure disposition is absent")
                    break
        if mode == "new":
            if search.get("cross_project_novelty_checked") is not True:
                problems.append("new-target cross-project novelty exclusion is not proven")
            if search.get("prior_searched_targets_selected") != 0:
                problems.append("a previously searched target was selected as New")
        else:
            if not search.get("external_authority_boundary"):
                problems.append("follow-up external authority boundary is absent")
            if search.get("disposition_persisted") is not True:
                problems.append("follow-up disposition is not persisted")

    restart = payload.get("restart_resume")
    if not isinstance(restart, dict):
        problems.append("restart/resume evidence is absent")
        restart = {}
    for field_name in (
        "application_restarted",
        "state_survived",
        "eligible_work_resumed",
        "completed_work_not_regenerated",
        "completed_work_not_repeated",
    ):
        if restart.get(field_name) is not True:
            problems.append(f"restart/resume field {field_name} is not proven")

    shared_write = payload.get("shared_state_write")
    if not isinstance(shared_write, dict) or not isinstance(
        shared_write.get("performed"), bool
    ):
        problems.append("shared-state write disposition is absent")
    elif shared_write["performed"]:
        lock = shared_write.get("lock")
        if not isinstance(lock, dict) or not all(
            lock.get(field_name)
            for field_name in ("owner", "acquired_at_utc", "released_at_utc")
        ):
            problems.append("shared write lacks exclusive-lock ownership and release")

    artifacts = payload.get("evidence_artifacts")
    if not isinstance(artifacts, list):
        problems.append("raw evidence artifact index is absent")
        artifacts = []
    roles = {
        item.get("role") for item in artifacts if isinstance(item, dict)
    }
    missing_roles = sorted(_REAL_DATA_ARTIFACT_ROLES - roles)
    if missing_roles:
        problems.append(f"raw evidence roles are missing: {', '.join(missing_roles)}")
    root = repo_root.resolve()
    for item in artifacts:
        if not isinstance(item, dict):
            problems.append("raw evidence index contains a non-object entry")
            continue
        relative = item.get("path")
        if not isinstance(relative, str) or not relative:
            problems.append("raw evidence artifact path is absent")
            continue
        path = (repo_root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            problems.append(f"raw evidence path escapes the repository: {relative}")
            continue
        if not path.is_file():
            problems.append(f"raw evidence artifact is absent: {relative}")
            continue
        expected_sha256 = item.get("sha256")
        if not _is_sha256(expected_sha256):
            problems.append(f"raw evidence hash is invalid: {relative}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            problems.append(f"raw evidence hash mismatch: {relative}")
    return problems


def check_package_completeness(env: ProdCheckEnvironment) -> CheckOutcome:
    """CLAIM-02: every production runtime module must be inside the package."""
    pyproject = env.repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return _failed("package_completeness", ["CLAIM-02"], "pyproject.toml is absent")
    text = pyproject.read_text(encoding="utf-8")
    scripts = (
        CANONICAL_EXECUTABLE,
        *COMPATIBILITY_EXECUTABLES,
        *ONE_SHOT_EXECUTABLES,
        "prod-check",
    )
    missing = [name for name in scripts if f"{name} =" not in text]
    if missing:
        return _failed(
            "package_completeness",
            ["CLAIM-02", "PROD-01"],
            "one or more required console scripts are unregistered",
            missing=missing,
        )
    return _passed(
        "package_completeness",
        ["CLAIM-02", "PROD-01"],
        "every required console script is registered in package metadata",
    )


def check_sibling_write_isolation(env: ProdCheckEnvironment) -> CheckOutcome:
    """WS-01/WS-03: sibling repositories stay read-only with no hidden coupling.

    WS-03 permits shared data through an explicit versioned interoperability
    contract, so repository-relative discovery of a sibling's read-only export is
    conforming. What must not exist is an absolute personal path, a runtime
    import of sibling code, or any write against a sibling-resolved location.
    """
    offenders: list[str] = []
    scanned = 0
    for root in (env.repo_root / "src", env.repo_root / "scripts"):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.name == "hunter_prod_check.py":
                continue
            scanned += 1
            relative = path.relative_to(env.repo_root)
            text = path.read_text(encoding="utf-8", errors="replace")
            for personal_root in ('"/Users/', "'/Users/", '"/home/', "'/home/"):
                if personal_root in text:
                    offenders.append(f"{relative}: hard-codes an absolute personal path")
                    break
            for sibling in SIBLING_REPOSITORY_NAMES:
                token = sibling.replace(" ", "_").lower()
                if f"import {token}" in text or f"from {token}" in text:
                    offenders.append(f"{relative}: imports sibling runtime code")
    coupling = _sibling_coupling_evidence(env.repo_root)
    offenders.extend(coupling["violations"])
    if offenders:
        return _failed(
            "sibling_write_isolation",
            ["WS-01", "WS-03"],
            "a runtime path couples to or could write into a sibling repository",
            offenders=sorted(set(offenders)),
        )
    return _passed(
        "sibling_write_isolation",
        ["WS-01", "WS-03"],
        "sibling access is repository-relative, read-only and schema-versioned",
        modules_scanned=scanned,
        sibling_access=coupling["read_only_surface"],
    )


def _sibling_coupling_evidence(repo_root: Path) -> dict[str, Any]:
    """Confirm the sibling bridge resolves relatively and never opens for write."""
    module = repo_root / "src" / "techno_search" / "hunter_cross_project_history.py"
    if not module.is_file():
        return {"violations": [], "read_only_surface": []}
    text = module.read_text(encoding="utf-8")
    violations: list[str] = []
    if "Path(__file__)" not in text:
        violations.append(
            "hunter_cross_project_history.py: sibling paths are not repository-relative"
        )
    # A write helper in this module is only safe when the repository's own export
    # function owns it; otherwise a sibling-resolved path could be written.
    exports_locally = "export_cross_project_history" in text
    for write_token in ('"w"', "'w'", ".write_text(", ".mkdir("):
        if write_token in text and "sibling" in text.lower() and not exports_locally:
            violations.append(
                "hunter_cross_project_history.py: unguarded write near sibling resolution"
            )
            break
    surface = [
        "read-only sibling export: data_selection/hunter_prior_search_history_v1.json",
        "contract: hunter_prior_search_history_v1",
    ]
    return {"violations": violations, "read_only_surface": surface}


def check_readme_conformance(env: ProdCheckEnvironment) -> CheckOutcome:
    """README-01/03: the README must follow docs/README_SPEC.md exactly."""
    readme = env.repo_root / "README.md"
    if not readme.is_file():
        return _failed("readme_conformance", ["README-01"], "README.md is absent")
    lines = readme.read_text(encoding="utf-8").splitlines()
    headings = [line.strip() for line in lines if line.startswith("#")]
    missing = [heading for heading in REQUIRED_README_HEADINGS if heading not in headings]
    if missing:
        return _failed(
            "readme_conformance",
            ["README-01"],
            "the README is missing required headings",
            missing=missing[:12],
            missing_count=len(missing),
        )
    order_index = [headings.index(heading) for heading in REQUIRED_README_HEADINGS]
    if order_index != sorted(order_index):
        return _failed(
            "readme_conformance",
            ["README-01"],
            "the README's required headings are out of the mandated order",
        )
    duplicates = sorted(
        {
            heading
            for heading in REQUIRED_README_HEADINGS
            if headings.count(heading) > 1
        }
    )
    if duplicates:
        return _failed(
            "readme_conformance",
            ["README-01"],
            "a required README heading occurs more than once",
            duplicates=duplicates,
        )
    body = "\n".join(lines)
    forbidden = sorted({term for term in FORBIDDEN_README_TERMS if term in body})
    if forbidden:
        return _failed(
            "readme_conformance",
            ["README-03"],
            "the README uses forbidden planning vocabulary",
            forbidden_terms=forbidden,
        )
    missing_siblings = [name for name in SIBLING_REPOSITORY_NAMES if name not in body]
    if missing_siblings:
        return _failed(
            "readme_conformance",
            ["README-02"],
            "the README does not name every sibling repository",
            missing=missing_siblings,
        )
    return _passed(
        "readme_conformance",
        ["README-01", "README-02", "README-03"],
        "the README matches the required structure, order and status vocabulary",
        heading_count=len(headings),
    )


def check_skipped_stage_labeling(env: ProdCheckEnvironment) -> CheckOutcome:
    """CLAIM-03: this gate itself must label non-executed stages honestly."""
    # The gate is self-describing: any NOT_EXECUTED outcome is reported with a
    # reason and excluded from the pass total. Confirm the vocabulary exists so a
    # future refactor cannot silently drop it.
    if STATUS_NOT_EXECUTED != "NOT_EXECUTED":  # pragma: no cover - constant guard
        return _failed(
            "skipped_stage_labeling", ["CLAIM-03"], "the not-executed vocabulary changed"
        )
    return _passed(
        "skipped_stage_labeling",
        ["CLAIM-03"],
        "non-executed stages are labeled NOT EXECUTED and excluded from pass totals",
    )


def check_state_authority(
    env: ProdCheckEnvironment, report: dict[str, Any], *, partial: bool = False
) -> CheckOutcome:
    """PROD-01: only a passing gate result may mark a requirement VERIFIED.

    This check closes the status-authority hole. It compares every
    machine-owned value in ``configs/HUNTER_PROD_STATE.json`` against the
    outcomes actually produced by this run, so a hand-written ``VERIFIED`` or
    ``PROD`` is a gate failure rather than an unchallenged assertion.
    """
    requirements = ["PROD-01", "CLAIM-04"]
    try:
        state = load_state(env.repo_root)
    except (FileNotFoundError, ValueError) as exc:
        return _failed(
            "state_authority", requirements, f"the state ledger is unusable: {exc}"
        )
    violations = (
        audit_recorded_authority_metadata(state)
        if partial
        else audit_state_authority(state, report)
    )
    if violations:
        return _failed(
            "state_authority",
            requirements,
            "the state ledger records a status this gate run does not justify",
            violations=[violation.as_dict() for violation in violations],
        )
    return _passed(
        "state_authority",
        requirements,
        (
            "stored machine-owned statuses carry deterministic gate provenance"
            if partial
            else "every machine-owned status in the ledger is justified by this gate run"
        ),
        ledger=str(env.repo_root / "configs" / "HUNTER_PROD_STATE.json"),
    )


def check_ranking_formula_integrity(env: ProdCheckEnvironment) -> CheckOutcome:
    """RANK-01: the published equation must match the canonical implementation.

    Compares every weight term the implementation actually applies against the
    formula block published in the README. This caught a real discrepancy: the
    README published four terms including a ``publication_value`` that does not
    exist, and omitted ``observability_score``, ``false_positive_probability``,
    and the blocking-issue and review-history adjustments.
    """
    requirements = ["RANK-01"]
    try:
        from techno_search.background_search import DEFAULT_PRIORITY_WEIGHTS
    except ImportError as exc:  # pragma: no cover - packaging failure
        return _failed("ranking_formula", requirements, f"cannot import scorer: {exc}")

    readme = env.repo_root / "README.md"
    if not readme.is_file():
        return _failed("ranking_formula", requirements, "README.md is missing")
    text = readme.read_text(encoding="utf-8")

    missing = sorted(term for term in DEFAULT_PRIORITY_WEIGHTS if term not in text)
    # Terms the README names that the implementation does not apply.
    invented = sorted(
        term
        for term in ("publication_value", "followup_leverage", "scientific_novelty")
        if term in text
    )
    if missing or invented:
        return _failed(
            "ranking_formula",
            requirements,
            "the published ranking equation disagrees with the implementation",
            implementation_terms=sorted(DEFAULT_PRIORITY_WEIGHTS),
            terms_missing_from_readme=missing,
            terms_published_but_not_implemented=invented,
        )
    return _passed(
        "ranking_formula",
        requirements,
        f"all {len(DEFAULT_PRIORITY_WEIGHTS)} implemented weight terms are published",
        implementation_terms=sorted(DEFAULT_PRIORITY_WEIGHTS),
    )


def check_launch_environment_evidence(env: ProdCheckEnvironment) -> CheckOutcome:
    """LAUNCH-03: the gate must record full environment evidence."""
    requirements = ["LAUNCH-03"]
    metadata = env.metadata()
    required = (
        "python_version",
        "python_executable",
        "platform",
        "virtual_environment",
        "resolved_canonical_executable",
        "installation_mode",
        "app_version",
        "repo_root",
        "relevant_environment",
    )
    missing = sorted(key for key in required if not metadata.get(key))
    if missing:
        return _failed(
            "launch_environment_evidence",
            requirements,
            "the recorded environment evidence is incomplete",
            missing_fields=missing,
        )
    return _passed(
        "launch_environment_evidence",
        requirements,
        "commit, tree state, interpreter, environment and resolved executable are recorded",
        environment=metadata,
    )


def check_preserved_user_work(env: ProdCheckEnvironment) -> CheckOutcome:
    """WS-02: pre-existing user work must still be present."""
    requirements = ["WS-02"]
    try:
        state = load_state(env.repo_root)
    except (FileNotFoundError, ValueError) as exc:
        return _failed("preserved_user_work", requirements, f"unusable ledger: {exc}")
    entries = state.get("preserved_user_changes") or []
    if not entries:
        return _failed(
            "preserved_user_work", requirements, "no preserved user work is recorded"
        )
    missing: list[str] = []
    for entry in entries:
        for path in [entry.get("path"), *(entry.get("paths") or [])]:
            if not path or " " in str(path):
                continue  # descriptive grouping label, not a real path
            if not (env.repo_root / str(path)).exists():
                missing.append(str(path))
    if missing:
        return _failed(
            "preserved_user_work",
            requirements,
            "recorded pre-existing user work is no longer present",
            missing_paths=sorted(missing),
        )
    return _passed(
        "preserved_user_work",
        requirements,
        f"{len(entries)} recorded pre-existing change group(s) remain present",
    )


def check_authority_order(env: ProdCheckEnvironment) -> CheckOutcome:
    """WS-04: the governing artifacts must exist at the declared versions."""
    requirements = ["WS-04"]
    contract = env.repo_root / "docs" / "HUNTER_PROD_CONTRACT.md"
    cli_spec = env.repo_root / "docs" / "CLI_UX_SPEC.md"
    readme_spec = env.repo_root / "docs" / "README_SPEC.md"
    missing = [
        str(path.relative_to(env.repo_root))
        for path in (contract, cli_spec, readme_spec)
        if not path.is_file()
    ]
    if missing:
        return _failed(
            "authority_order", requirements, "governing artifacts are missing",
            missing=missing,
        )
    problems: list[str] = []
    if CONTRACT_VERSION not in contract.read_text(encoding="utf-8"):
        problems.append(f"contract does not declare {CONTRACT_VERSION}")
    if CLI_UX_VERSION not in cli_spec.read_text(encoding="utf-8"):
        problems.append(f"CLI spec does not declare {CLI_UX_VERSION}")
    if problems:
        return _failed(
            "authority_order",
            requirements,
            "the gate's declared governing versions do not match the artifacts",
            problems=problems,
        )
    return _passed(
        "authority_order",
        requirements,
        "all three governing artifacts exist at the versions this gate enforces",
    )


def check_manifest_provenance(env: ProdCheckEnvironment) -> CheckOutcome:
    """DUR-03/IDENT-04: frozen manifests must persist provenance and eligibility."""
    requirements = ["DUR-03", "IDENT-04"]
    searches = env.repo_root / "results" / "searches"
    manifests = sorted(searches.glob("SEARCH-*/manifest.json")) if searches.is_dir() else []
    if not manifests:
        return _not_executed(
            "manifest_provenance", requirements, "no durable search manifests exist"
        )
    # Contract section 1 permits different eligibility rules per mode, so each
    # mode must persist the evidence its own rule depends on (IDENT-04).
    common_fields = (
        "prior_search_count",
        "prior_search_provenance",
        "prior_search_provenance_summary",
        "selection_reason",
        "target_selection_score",
    )
    # New eligibility turns on cross-project novelty, so it must show that
    # evidence and the config that ranked it.
    new_mode_fields = ("cross_project_prior_search", "priority_config_version")
    # Follow-up eligibility turns on durable follow-up evidence and the ask.
    follow_up_fields = ("follow_up_priority", "recommended_next_action", "evidence")
    # The provenance fields were added to hunter_search_manifest_v3 in place
    # without a version bump, so schema_version alone cannot separate old from
    # new. DUR-02 forbids regenerating a frozen manifest, so historical records
    # are retained unmodified and this asserts what the pipeline persists NOW:
    # the most recent manifest of each mode.
    newest_by_mode: dict[str, Path] = {}
    for path in manifests:
        try:
            mode = str(json.loads(path.read_text(encoding="utf-8")).get("mode", ""))
        except json.JSONDecodeError:
            continue
        newest_by_mode[mode] = path  # sorted order means later wins

    problems: list[str] = []
    inspected = 0
    checked_manifests = 0
    grandfathered = len(manifests) - len(newest_by_mode)
    for path in sorted(newest_by_mode.values()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: malformed ({exc})")
            continue
        if payload.get("schema_version") != CURRENT_MANIFEST_SCHEMA:
            continue
        checked_manifests += 1
        mode = str(payload.get("mode", ""))
        expected = common_fields + (
            follow_up_fields if mode == "follow-up" else new_mode_fields
        )
        for target in payload.get("targets") or []:
            inspected += 1
            absent = [field for field in expected if field not in target]
            if absent:
                problems.append(
                    f"{path.parent.name} ({mode}): missing {', '.join(absent)}"
                )
                break
    if problems:
        return _failed(
            "manifest_provenance",
            requirements,
            "current-schema manifests omit required provenance or eligibility evidence",
            problems=problems[:10],
            current_schema=CURRENT_MANIFEST_SCHEMA,
        )
    if not checked_manifests:
        return _not_executed(
            "manifest_provenance",
            requirements,
            f"no manifest uses the current schema {CURRENT_MANIFEST_SCHEMA}",
        )
    return _passed(
        "manifest_provenance",
        requirements,
        f"{inspected} frozen target(s) across the {checked_manifests} most recent "
        f"per-mode manifest(s) carry complete mode-appropriate provenance; "
        f"{grandfathered} earlier manifest(s) retained unmodified per DUR-02",
        current_schema=CURRENT_MANIFEST_SCHEMA,
    )


def check_adaptive_expansion_adversarial(env: ProdCheckEnvironment) -> CheckOutcome:
    """DISC-03: a high-value candidate outside the initial sample must be found."""
    requirements = ["DISC-03"]
    evidence_dir = env.repo_root / "docs" / "evidence"
    bundles = (
        sorted(evidence_dir.glob("hunter_*controlled_acceptance*.json"))
        if evidence_dir.is_dir()
        else []
    )
    if not bundles:
        return _not_executed(
            "adaptive_expansion_adversarial",
            requirements,
            "no controlled acceptance evidence bundle is committed",
        )
    newest = bundles[-1]
    try:
        payload = json.loads(newest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _failed(
            "adaptive_expansion_adversarial", requirements, f"malformed bundle: {exc}"
        )
    results = {
        str(item.get("assertion")): bool(item.get("passed"))
        for item in payload.get("assertion_results") or []
    }
    assertion = "adaptive_expansion_found_displacing_target"
    if assertion not in results:
        return _failed(
            "adaptive_expansion_adversarial",
            requirements,
            f"{newest.name} does not exercise {assertion}",
            available=sorted(results),
        )
    if not results[assertion]:
        return _failed(
            "adaptive_expansion_adversarial",
            requirements,
            f"{assertion} failed in {newest.name}",
        )
    return _passed(
        "adaptive_expansion_adversarial",
        requirements,
        f"{assertion} passed in {newest.name}",
        bundle=newest.name,
    )


def check_field_failure_regressions(env: ProdCheckEnvironment) -> CheckOutcome:
    """CLAIM-01/EVAL-01: observed field failures must retain regression tests."""
    requirements = ["CLAIM-01"]
    tests_dir = env.repo_root / "tests"
    if not tests_dir.is_dir():
        return _failed("field_failure_regressions", requirements, "tests/ is missing")
    corpus = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in tests_dir.rglob("test_*.py")
    )
    # Each marker corresponds to a field failure this project actually observed.
    required_markers = {
        "installed-path launch": "TechnoHunter",
        "slash command discovery": "/New-Search",
        "invalid required input": "target count",
        "narrow terminal rendering": "80",
        "exact-target execution": "manifest",
        "restart and resume": "resume",
    }
    missing = sorted(
        name for name, marker in required_markers.items() if marker not in corpus
    )
    if missing:
        return _failed(
            "field_failure_regressions",
            requirements,
            "observed field failures lack regression coverage",
            missing_regressions=missing,
        )
    return _passed(
        "field_failure_regressions",
        requirements,
        f"all {len(required_markers)} observed field-failure classes retain regression tests",
    )


def check_record_ownership(env: ProdCheckEnvironment) -> CheckOutcome:
    """IDENT-02: publish records this repository owns, consume siblings read-only.

    Ownership has two halves and both must hold: a publishable export of this
    repository's own validated history, and a versioned read-only consumer for
    sibling history that rejects an incompatible schema rather than guessing.
    """
    requirements = ["IDENT-02"]
    module = env.repo_root / "src" / "techno_search" / "hunter_cross_project_history.py"
    if not module.is_file():
        return _failed(
            "record_ownership", requirements, "the cross-project history module is absent"
        )
    source = module.read_text(encoding="utf-8")
    cli = (env.repo_root / "src" / "techno_search" / "cli.py").read_text(encoding="utf-8")

    problems: list[str] = []
    if "export-cross-project-history" not in cli:
        problems.append("no command publishes this repository's own history export")
    if "CROSS_PROJECT_HISTORY_SCHEMA_VERSION" not in source:
        problems.append("the consumed history contract is not schema-versioned")
    # A consumer that accepts any payload cannot fail closed on an incompatible
    # sibling export, which IDENT-03 requires.
    if 'payload.get("schema_version")' not in source:
        problems.append("the consumer does not validate the sibling schema version")

    outcome = run_command(
        [str(env.executable("techno-search")), "export-cross-project-history", "--help"],
        cwd=env.repo_root,
        env=_non_animated_env(),
    )
    if outcome.exit_status != 0:
        problems.append("the publish command is registered but does not run")

    if problems:
        return _failed(
            "record_ownership",
            requirements,
            "record ownership is not fully established",
            problems=problems,
            evidence=outcome.as_dict(stream_limit=800),
        )
    return _passed(
        "record_ownership",
        requirements,
        "this repository publishes its own history export and consumes sibling "
        "history through a schema-versioned read-only contract",
    )


def check_no_shadow_production(env: ProdCheckEnvironment) -> CheckOutcome:
    """PIPE-03: no orphaned or superseded production module may remain.

    A runtime module that nothing imports and no entry point registers is
    reachable only through tests or direct imports, which the contract requires
    be integrated, demoted, or removed rather than left in place.
    """
    requirements = ["PIPE-03"]
    package = env.repo_root / "src" / "techno_search"
    if not package.is_dir():
        return _failed("no_shadow_production", requirements, "runtime package is absent")

    hunter_modules = sorted(
        path.stem for path in package.glob("hunter_*.py") if path.stem != "__init__"
    )
    if not hunter_modules:
        return _failed("no_shadow_production", requirements, "no Hunter modules found")

    # Everything the package itself imports, plus everything pyproject registers.
    corpus = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in package.rglob("*.py")
    )
    pyproject = (env.repo_root / "pyproject.toml").read_text(encoding="utf-8")

    orphans: list[str] = []
    for name in hunter_modules:
        imported = f"techno_search.{name}" in corpus or f"from .{name}" in corpus
        registered = f"techno_search.{name}:" in pyproject
        if not (imported or registered):
            orphans.append(name)
    if orphans:
        return _failed(
            "no_shadow_production",
            requirements,
            "runtime modules are reachable only through tests or direct imports",
            orphaned_modules=orphans,
        )
    return _passed(
        "no_shadow_production",
        requirements,
        f"all {len(hunter_modules)} Hunter runtime modules are reachable from the "
        "canonical package or a registered entry point",
    )


#: A bundle that self-declares a controlled fixture cannot satisfy E2E-01/E2E-02.
FIXTURE_MARKERS = ("controlled", "fixture", "smoke")


def _live_acceptance_bundles(repo_root: Path) -> tuple[list[Path], list[Path]]:
    """Split committed acceptance bundles into live-source and fixture-backed."""
    evidence_dir = repo_root / "docs" / "evidence"
    live: list[Path] = []
    fixture: list[Path] = []
    if not evidence_dir.is_dir():
        return live, fixture
    for path in sorted(evidence_dir.glob("hunter_*acceptance*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        classification = str(
            (payload.get("release") or {}).get("fixture_classification", "")
        ).casefold()
        watermarks = payload.get("source_watermarks") or (
            payload.get("provenance_trace") or {}
        ).get("source_watermarks")
        # Fail closed: a bundle counts as live-source evidence only when it
        # positively declares live authoritative sources AND carries their
        # watermarks. An unlabelled bundle is NOT evidence of a live run —
        # treating absence as "live" is how a prose-only smoke claim passes.
        declares_live = bool(classification) and not any(
            marker in classification for marker in FIXTURE_MARKERS
        )
        if declares_live and watermarks:
            live.append(path)
        else:
            fixture.append(path)
    return live, fixture


def _real_data_workflow_outcome(
    env: ProdCheckEnvironment, *, mode: str, check_id: str, requirement: str
) -> CheckOutcome:
    """Shared E2E-01/E2E-02 evaluation for one real-data workflow mode."""
    requirements = [requirement, "E2E-04"]
    live, fixture = _live_acceptance_bundles(env.repo_root)
    if not live:
        return _not_executed(
            check_id,
            requirements,
            f"no committed evidence bundle records a real-data {mode} workflow against "
            f"live authoritative sources with source watermarks; {len(fixture)} bundle(s) "
            "are fixture-backed or unlabelled, which contract E2E-01/E2E-02 do not accept",
            non_live_bundles=[path.name for path in fixture],
        )
    newest = live[-1]
    payload = json.loads(newest.read_text(encoding="utf-8"))
    selected = (payload.get("selected_targets") or {}).get(
        "new" if mode == "New" else "follow_up"
    )
    if not selected:
        return _failed(
            check_id, requirements, f"{newest.name} froze no {mode} targets"
        )
    if len(selected) < 5:
        return _failed(
            check_id,
            requirements,
            f"{newest.name} froze {len(selected)} {mode} target(s); the contract requires 5",
            frozen=selected,
        )
    return _passed(
        check_id,
        requirements,
        f"{newest.name} records a real-data {mode} workflow over {len(selected)} targets",
        bundle=newest.name,
    )


def check_real_data_new_workflow(env: ProdCheckEnvironment) -> CheckOutcome:
    """E2E-01: 5 real New targets selected, frozen, executed and persisted."""
    return _real_data_workflow_outcome(
        env, mode="New", check_id="real_data_new_workflow", requirement="E2E-01"
    )


def check_real_data_follow_up_workflow(env: ProdCheckEnvironment) -> CheckOutcome:
    """E2E-02: 5 real Follow-up targets selected, frozen, executed and persisted."""
    return _real_data_workflow_outcome(
        env,
        mode="Follow-up",
        check_id="real_data_follow_up_workflow",
        requirement="E2E-02",
    )


def check_requirement_coverage(
    env: ProdCheckEnvironment, report: dict[str, Any]
) -> CheckOutcome:
    """PROD-01/CLAIM-04: every mandatory requirement must be measured.

    A zero-exit gate proves only that the checks which ran passed. Without this
    check, a requirement that no check covers stays silently ``UNVERIFIED``
    while the gate reports PROD READY — certifying what it happens to measure
    and saying nothing about the rest.
    """
    requirements = ["PROD-01", "CLAIM-04"]
    try:
        state = load_state(env.repo_root)
    except (FileNotFoundError, ValueError) as exc:
        return _failed(
            "requirement_coverage", requirements, f"the state ledger is unusable: {exc}"
        )
    declared = state.get("requirements", {})
    if not isinstance(declared, dict) or not declared:
        return _failed(
            "requirement_coverage",
            requirements,
            "the state ledger declares no requirements to cover",
        )
    derived = derive_requirement_statuses(report, known_requirements=declared.keys())
    uncovered = uncovered_requirements(derived)
    if uncovered:
        return _failed(
            "requirement_coverage",
            requirements,
            f"{len(uncovered)} of {len(declared)} mandatory requirements have no covering check",
            uncovered_requirements=uncovered,
            covered=len(declared) - len(uncovered),
            declared=len(declared),
        )
    return _passed(
        "requirement_coverage",
        requirements,
        f"all {len(declared)} mandatory requirements are covered by an executed check",
    )


CheckFunction = Callable[[ProdCheckEnvironment], CheckOutcome]

LAUNCH_CHECKS: tuple[CheckFunction, ...] = (
    check_documented_installation,
    check_operator_installation_surfaces,
    check_entry_points,
    check_launch_repo_root,
    check_launch_outside_repository,
    check_invalid_input_is_actionable,
)

UX_CHECKS: tuple[CheckFunction, ...] = (
    check_command_palette,
    check_interactive_pty_palette,
    check_guided_input_and_validation,
    check_animation_degrades,
    check_machine_output_is_clean,
    check_result_table_width_awareness,
    check_action_preview,
    check_golden_ux_tests,
)

PIPELINE_CHECKS: tuple[CheckFunction, ...] = (
    check_ranking_formula_integrity,
    check_real_data_new_workflow,
    check_real_data_follow_up_workflow,
    check_record_ownership,
    check_no_shadow_production,
    check_launch_environment_evidence,
    check_preserved_user_work,
    check_authority_order,
    check_manifest_provenance,
    check_adaptive_expansion_adversarial,
    check_field_failure_regressions,
    check_canonical_routing,
    check_durable_record_kinds,
    check_exact_target_freezing,
    check_adaptive_discovery_evidence,
    check_identity_and_history,
    check_restart_and_resume,
    check_real_data_evidence,
    check_package_completeness,
    check_sibling_write_isolation,
    check_readme_conformance,
    check_skipped_stage_labeling,
)

SLOW_CHECKS: tuple[CheckFunction, ...] = (check_built_wheel,)

ALL_CHECKS: tuple[CheckFunction, ...] = (
    *LAUNCH_CHECKS,
    *UX_CHECKS,
    *PIPELINE_CHECKS,
)


#: Checks that need the assembled report rather than only the environment.
REPORT_CHECK_NAMES = ("requirement_coverage", "state_authority")


def _authority_selected(only: Sequence[str]) -> bool:
    wanted = {name.strip() for name in only if name.strip()}
    if not wanted:
        return True
    return any(
        name.startswith(token) for token in wanted for name in REPORT_CHECK_NAMES
    )


def _select_checks(*, include_wheel: bool, only: Sequence[str]) -> list[CheckFunction]:
    selected = list(ALL_CHECKS)
    if include_wheel:
        selected.extend(SLOW_CHECKS)
    if only:
        # Accept either the full function-derived name or the emitted check_id,
        # so `--only result_table` and `--only result_table_width_awareness` agree.
        wanted = {name.strip() for name in only if name.strip()}
        selected = [
            check
            for check in selected
            if any(
                token == check.__name__.replace("check_", "")
                or check.__name__.replace("check_", "").startswith(token)
                for token in wanted
            )
        ]
        unmatched = sorted(
            token
            for token in wanted
            if not any(
                check.__name__.replace("check_", "").startswith(token)
                for check in (*ALL_CHECKS, *SLOW_CHECKS)
            )
            and not any(name.startswith(token) for name in REPORT_CHECK_NAMES)
        )
        if unmatched:
            raise ValueError(
                "unknown --only check name(s): "
                + ", ".join(unmatched)
                + "; available: "
                + ", ".join(sorted(iter_check_names()))
            )
    if include_wheel and check_built_wheel not in selected:
        selected.append(check_built_wheel)
    return selected


def _required_full_gate_outcomes(
    *, include_wheel: bool, is_partial: bool
) -> list[CheckOutcome]:
    """Represent an omitted mandatory full-gate stage as NOT EXECUTED.

    ``--include-wheel`` is intentionally opt-in because partial phase gates must
    stay fast and selectable.  A full gate, however, cannot award PROD unless
    the built-wheel stage actually ran.  Keeping the missing stage in the
    report also prevents requirement coverage from silently treating a
    38-check run as equivalent to the canonical 39-check run.
    """
    if is_partial or include_wheel:
        return []
    return [
        _not_executed(
            "built_wheel",
            ["LAUNCH-02"],
            "the mandatory full PROD gate requires --include-wheel",
            required_option="--include-wheel",
        )
    ]


def run_prod_check(
    *,
    repo_root: Path,
    include_wheel: bool = False,
    only: Sequence[str] = (),
    bin_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute the gate and return its versioned machine-readable report."""
    env = ProdCheckEnvironment(repo_root, bin_dir=bin_dir)
    is_partial = bool(only)
    outcomes = [check(env) for check in _select_checks(include_wheel=include_wheel, only=only)]
    outcomes.extend(
        _required_full_gate_outcomes(
            include_wheel=include_wheel,
            is_partial=is_partial,
        )
    )

    # The status-authority audit compares the ledger against the outcomes this
    # run actually produced, so it must be evaluated after the others.
    if _authority_selected(only):
        interim: dict[str, Any] = {
            "checks": [outcome.as_dict() for outcome in outcomes],
            "prod_ready": not any(outcome.mandatory_failure for outcome in outcomes),
        }
        # Requirement coverage is a whole-PROD property. A partial phase gate
        # must not fail merely because later-phase checks were intentionally
        # outside its selected scope.
        wants_coverage = not is_partial or any(
            "requirement_coverage".startswith(token.strip())
            for token in only
            if token.strip()
        )
        if wants_coverage:
            coverage = check_requirement_coverage(env, interim)
            outcomes.append(coverage)
            interim["checks"] = [outcome.as_dict() for outcome in outcomes]
            interim["prod_ready"] = not any(
                outcome.mandatory_failure for outcome in outcomes
            )
        outcomes.append(check_state_authority(env, interim, partial=is_partial))

    passed = [outcome for outcome in outcomes if outcome.status == STATUS_PASS]
    failed = [outcome for outcome in outcomes if outcome.status == STATUS_FAIL]
    not_executed = [outcome for outcome in outcomes if outcome.status == STATUS_NOT_EXECUTED]
    gate_passed = not failed and not not_executed
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "cli_ux_version": CLI_UX_VERSION,
        "app_version": __version__,
        "environment": env.metadata(),
        "tested_commit": _git_commit(repo_root),
        "working_tree_state": _working_tree_state(repo_root),
        "scope": {
            "kind": "partial" if is_partial else "full",
            "selected_checks": sorted(str(name) for name in only),
        },
        "counts": {
            "executed": len(passed) + len(failed),
            "passed": len(passed),
            "failed": len(failed),
            "not_executed": len(not_executed),
        },
        "gate_passed": gate_passed,
        "prod_ready": gate_passed and not is_partial,
        "checks": [outcome.as_dict() for outcome in outcomes],
        "no_claim": (
            "This gate verifies software conformance only. It makes no detection, "
            "discovery, expert-review, external-validation, or external-submission claim."
        ),
    }


def _git_commit(repo_root: Path) -> str | None:
    result = run_command(["git", "rev-parse", "HEAD"], cwd=repo_root, timeout=30.0)
    return result.stdout.strip() or None


def _working_tree_state(repo_root: Path) -> str:
    result = run_command(["git", "status", "--porcelain"], cwd=repo_root, timeout=30.0)
    return "clean" if not result.stdout.strip() else "dirty"


def _print_summary(report: dict[str, Any], out: TextIO) -> None:
    counts = report["counts"]
    print(
        f"prod-check {report['schema_version']} — app {report['app_version']} "
        f"@ {report['tested_commit']} ({report['working_tree_state']})",
        file=out,
    )
    print(
        f"executed {counts['executed']}  passed {counts['passed']}  "
        f"failed {counts['failed']}  not executed {counts['not_executed']}",
        file=out,
    )
    print("", file=out)
    for check in report["checks"]:
        marker = {STATUS_PASS: "PASS", STATUS_FAIL: "FAIL"}.get(
            check["status"], "NOT EXECUTED"
        )
        print(f"[{marker:<13}] {check['check_id']}: {check['summary']}", file=out)
        if check["status"] != STATUS_PASS:
            for key in ("missing", "missing_commands", "missing_rows", "problems", "violations"):
                value = check["detail"].get(key)
                if value:
                    print(f"                  {key}: {value}", file=out)
    print("", file=out)
    if (report.get("scope") or {}).get("kind") == "partial":
        verdict = "PARTIAL GATE PASS" if report.get("gate_passed") else "PARTIAL GATE FAIL"
    else:
        verdict = "PROD READY" if report["prod_ready"] else "NOT PROD"
    print(f"verdict: {verdict}", file=out)
    print(report["no_claim"], file=out)


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    """Run the repository-native PROD gate."""
    out = stdout or sys.stdout
    parser = argparse.ArgumentParser(
        prog="prod-check",
        description="Repository-native Hunter PROD gate (contract PROD-01).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root to inspect (defaults to the installed source tree).",
    )
    parser.add_argument(
        "--include-wheel",
        action="store_true",
        help="Also build and launch a wheel in a clean environment (slow).",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Run only the named check (repeatable).",
    )
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON.")
    parser.add_argument(
        "--report-path", type=Path, help="Write the machine-readable report to this path."
    )
    parser.add_argument(
        "--update-state",
        action="store_true",
        help=(
            "Write this run's results into configs/HUNTER_PROD_STATE.json. "
            "This is the only supported writer of machine-owned status fields."
        ),
    )
    parser.add_argument(
        "--active-phase",
        help="Record the active execution phase in the state ledger.",
    )
    parser.add_argument(
        "--implementation-state",
        choices=["BLOCKING", "IN_PROGRESS", "IMPLEMENTED_NOT_VERIFIED"],
        help=(
            "Record the agent-owned implementation state. VERIFIED and PROD are "
            "never accepted here; they are derived from gate results alone."
        ),
    )
    args = parser.parse_args(argv)
    try:
        report = run_prod_check(
            repo_root=args.repo_root.resolve(),
            include_wheel=args.include_wheel,
            only=args.only,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.update_state:
        repo_root = args.repo_root.resolve()
        try:
            state = load_state(repo_root)
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: cannot update state ledger: {exc}", file=sys.stderr)
            return 2
        updated = apply_report(
            state,
            report,
            active_phase=args.active_phase,
            implementation_state=args.implementation_state,
        )
        path = write_state(repo_root, updated)
        print(f"state ledger updated: {path}", file=out)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True), file=out)
    else:
        _print_summary(report, out)
    return 0 if report.get("gate_passed", report["prod_ready"]) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


def iter_check_names() -> Iterable[str]:
    """Expose selectable check names for tests and documentation."""
    for check in (*ALL_CHECKS, *SLOW_CHECKS):
        yield check.__name__.replace("check_", "")
    yield from REPORT_CHECK_NAMES
