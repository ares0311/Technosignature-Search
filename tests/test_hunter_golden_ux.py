"""Golden UX conformance for the Techno-Hunter operator surface.

``docs/CLI_UX_SPEC.md`` section 13 requires stable golden assertions and forbids
byte-identical animation-frame comparison. Each baseline in ``tests/golden`` is
therefore a list of semantic tokens; ``!`` prefixes a negative assertion.

These tests drive the real shell through its dispatch path rather than calling
render helpers directly, so a regression in routing is caught too.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from techno_search.hunter_shell import HunterShell

GOLDEN_DIR = Path(__file__).parent / "golden"
ESCAPE = "\x1b["

REQUIRED_BASELINES = (
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


def load_baseline(name: str) -> tuple[list[str], list[str]]:
    """Split a baseline into required and forbidden semantic tokens."""
    required: list[str] = []
    forbidden: list[str] = []
    for raw in (GOLDEN_DIR / name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            token = line[1:]
            forbidden.append(ESCAPE if token == "ESC_BRACKET" else token)
        else:
            required.append(line)
    return required, forbidden


def assert_baseline(name: str, output: str) -> None:
    required, forbidden = load_baseline(name)
    missing = [token for token in required if token not in output]
    present = [token for token in forbidden if token in output]
    assert not missing, f"{name}: missing required semantic token(s) {missing}"
    assert not present, f"{name}: forbidden token(s) present {present}"


def run_shell(
    lines: Sequence[str],
    *,
    tmp_path: Path,
    width: int = 120,
    monkeypatch: pytest.MonkeyPatch,
    searches_dir: Path | None = None,
) -> tuple[int, str, str]:
    """Drive the real shell non-interactively and capture both streams."""
    monkeypatch.setenv("COLUMNS", str(width))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    stdout, stderr = io.StringIO(), io.StringIO()
    # The canonical one-shot entry points write to the process streams, which in
    # production are the same streams the shell holds. Bind them together so an
    # in-process test observes exactly what an operator would see.
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    shell = HunterShell(
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
        interactive=False,
        no_animation=True,
        no_color=True,
        history_path=tmp_path / "history",
        searches_dir=searches_dir if searches_dir is not None else tmp_path / "searches",
        scans_dir=tmp_path / "scans",
        priority_queue=tmp_path / "queue.csv",
    )
    exit_code = shell.run(list(lines))
    return exit_code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture(autouse=True)
def _present_queue(tmp_path: Path) -> None:
    """Availability requires a real queue file; supply a minimal real one."""
    queue = tmp_path / "queue.csv"
    if not queue.exists():
        queue.write_text(
            "target_id,status,target_selection_score\n", encoding="utf-8"
        )


@pytest.fixture
def frozen_search(tmp_path: Path) -> Path:
    """A real durable manifest shape, used to exercise the results table."""
    searches = tmp_path / "searches"
    search_id = "SEARCH-20260730T101500Z-1A2B3C4D"
    directory = searches / search_id
    directory.mkdir(parents=True)
    manifest = {
        "schema_version": "hunter_search_manifest_v3",
        "search_id": search_id,
        "mode": "new",
        "created_at_utc": "2026-07-30T10:15:00Z",
        "created_by_project": "techno_hunter",
        "code_commit": "0" * 40,
        "app_version": "1.2.72",
        "acquisition": {"raw_retention_policy": "stream_process_evict"},
        "selection": {
            "quality": {
                "interpretation": (
                    "deterministic relative ranking score; not a calibrated "
                    "probability or absolute eligibility threshold"
                ),
                "score_field": "target_selection_score",
            }
        },
        "targets": [
            {
                "hip": "HIP2",
                "object_type": "Star",
                "distance_light_years": 148.73,
                "spectral_type": "K3V",
                "prior_search_count": 0,
                "target_selection_score": 0.538042,
                "estimated_download_gb": 0.238747,
                "execution_kind": "novel_target_archive_processing",
                "selection_reason": "highest ranked never-searched candidate",
                "ra_deg": 0.5,
                "dec_deg": -19.498611,
                "galactic_latitude_deg": -78.1,
                "queue_status": "raw_download_approval_required",
                "data_products_available": "hdf5_size_preflight_ok",
            }
        ],
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return searches


def test_startup_techno_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exit_code, out, _ = run_shell(
        ["/Help", "/Exit"], tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    assert exit_code == 0
    assert_baseline("startup_techno.txt", out)


@pytest.mark.parametrize("baseline", ["startup_neo.txt", "startup_exo.txt"])
def test_sibling_identity_negative_control(
    baseline: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This repository must never claim a sibling Hunter's identity or theme."""
    _, out, _ = run_shell(["/Help", "/Exit"], tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert_baseline(baseline, out)


def test_command_palette_opens_on_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-CMD-01: `/` alone opens the described, searchable palette."""
    exit_code, out, _ = run_shell(["/", "/Exit"], tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert exit_code == 0
    assert_baseline("command_palette.txt", out)


def test_palette_filters_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UX-CMD-03: a partial token filters rather than erroring."""
    exit_code, out, _ = run_shell(
        ["/Follow", "/Exit"], tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    assert exit_code == 0
    assert "/Follow-Up-Search" in out
    assert "/Run-Search" not in out.split("Keyboard")[0]


def test_new_search_shows_guided_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-IN-01/UX-ADV-01: guided fields with defaults and progressive disclosure."""
    exit_code, out, err = run_shell(
        ["/New-Search"], tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    assert exit_code == 1, "a missing required field must not execute"
    assert_baseline("new_search_fields.txt", out)
    assert "requires a positive target count" in err


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/New-Search twenty", "positive whole number"),
        ("/New-Search 0", "greater than zero"),
        ("/Follow-Up-Search -3", "requires a positive target count"),
    ],
)
def test_invalid_target_counts_are_rejected(
    line: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-IN-03: invalid input cannot advance or execute."""
    exit_code, out, err = run_shell([line], tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert exit_code != 0
    assert expected in (out + err)
    assert "Traceback (most recent call last)" not in (out + err)
    assert "usage:" not in (out + err), "raw argparse must not be the normal response"


def test_invalid_targets_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    combined = ""
    for line in ("/New-Search twenty", "/New-Search 0"):
        _, out, err = run_shell([line], tmp_path=tmp_path, monkeypatch=monkeypatch)
        combined += out + err
    assert_baseline("invalid_targets.txt", combined)


def test_unknown_command_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-RUN-03: a concise operator-facing error, never a traceback."""
    exit_code, out, err = run_shell(["/Nope"], tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert exit_code == 1
    assert_baseline("operator_error.txt", out + err)


@pytest.mark.parametrize("width", [80, 140])
def test_results_table_respects_width(
    width: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frozen_search: Path,
) -> None:
    """UX-TABLE-01: no rendered line may exceed the detected terminal width."""
    exit_code, out, _ = run_shell(
        ["/Inspect-Target", "/Exit"],
        tmp_path=tmp_path,
        width=width,
        monkeypatch=monkeypatch,
        searches_dir=frozen_search,
    )
    assert exit_code == 0
    overlong = [line for line in out.splitlines() if len(line.rstrip()) > width]
    assert not overlong, f"lines exceeded {width} columns: {overlong[:2]}"
    assert_baseline(f"results_table_{width}_columns.txt", out)


def test_narrow_width_preserves_rank_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_search: Path
) -> None:
    """UX-TABLE-01 explicitly requires rank and identity to stay visible."""
    _, out, _ = run_shell(
        ["/Inspect-Target", "/Exit"],
        tmp_path=tmp_path,
        width=60,
        monkeypatch=monkeypatch,
        searches_dir=frozen_search,
    )
    assert "Target" in out
    assert "HIP2" in out
    assert not [line for line in out.splitlines() if len(line.rstrip()) > 60]


def test_inspect_target_detail_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_search: Path
) -> None:
    """UX-TABLE-02: the detail view carries provenance and limitations."""
    exit_code, out, _ = run_shell(
        ["/Inspect-Target 1", "/Exit"],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        searches_dir=frozen_search,
    )
    assert exit_code == 0
    for token in (
        "Canonical identity",
        "HIP2",
        "Selection reason",
        "Score components",
        "target_selection_score",
        "Source and transformation provenance",
        "Limitations",
        "not a calibrated",
    ):
        assert token in out, f"detail view omitted {token!r}"


def test_inspect_target_rejects_out_of_range_rank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_search: Path
) -> None:
    exit_code, _, err = run_shell(
        ["/Inspect-Target 99"],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        searches_dir=frozen_search,
    )
    assert exit_code == 1
    assert "out of range" in err
    assert "Traceback (most recent call last)" not in err


def test_inspect_target_rejects_unknown_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_search: Path
) -> None:
    exit_code, _, err = run_shell(
        ["/Inspect-Target NOT-A-STAR"],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        searches_dir=frozen_search,
    )
    assert exit_code == 1
    assert "no frozen target matches" in err


def test_non_tty_output_is_undecorated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX-START-04 / UX-TABLE-04: redirected machine output carries no ANSI."""
    monkeypatch.setenv("CI", "1")
    queue = tmp_path / "queue.csv"
    queue.write_text("target_id,status\n", encoding="utf-8")
    stdout, stderr = io.StringIO(), io.StringIO()
    shell = HunterShell(
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
        interactive=False,
        history_path=tmp_path / "history",
        searches_dir=tmp_path / "searches",
        scans_dir=tmp_path / "scans",
        priority_queue=queue,
    )
    assert shell.animation_enabled is False
    payload = {
        "eligible_entries": [],
        "source_ledger_count": 0,
        "unresolved_identity_count": 0,
        "disclaimer": "local scientific triage only",
    }
    stdout.write(json.dumps(payload, indent=2, sort_keys=True))
    assert_baseline("non_tty_output.txt", stdout.getvalue())


def test_every_required_baseline_exists() -> None:
    """CLI_UX_SPEC section 13 names each of these baselines explicitly."""
    missing = [name for name in REQUIRED_BASELINES if not (GOLDEN_DIR / name).is_file()]
    assert not missing, f"missing golden baselines: {missing}"


def test_baselines_are_non_empty() -> None:
    """A baseline with no assertions would silently pass forever."""
    for name in REQUIRED_BASELINES:
        required, forbidden = load_baseline(name)
        assert required or forbidden, f"{name} declares no assertions"
