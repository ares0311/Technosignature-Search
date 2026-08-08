"""Regression coverage for the turbo_seti compatibility patch interpreter."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from pytest import MonkeyPatch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/patch_turbo_seti_numpy2_compat.sh"
BUGGY_LINE = (
    "str(datah5_obj.header['cchan_id']) + \" is: %i\" % max_val.total_n_hits)"
)
FIXED_LINE = (
    "str(datah5_obj.header['cchan_id']) + \" is: %i\" % max_val.total_n_hits[0])"
)


def test_patch_modifies_package_owned_by_requested_python(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    module_dir = tmp_path / "turbo_seti" / "find_doppler"
    module_dir.mkdir(parents=True)
    (module_dir.parent / "__init__.py").write_text("", encoding="utf-8")
    (module_dir / "__init__.py").write_text("", encoding="utf-8")
    module = module_dir / "find_doppler.py"
    module.write_text(
        "def candidate_count(datah5_obj, max_val):\n"
        "    return (\n"
        f"        {BUGGY_LINE}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    completed = subprocess.run(
        ["bash", str(SCRIPT), "--python", sys.executable],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "[OK] Patched" in completed.stdout
    patched = module.read_text(encoding="utf-8")
    assert BUGGY_LINE not in patched
    assert FIXED_LINE in patched


def test_patch_uses_requested_python_interpreter(tmp_path: Path) -> None:
    marker = tmp_path / "invoked"
    fake_python = tmp_path / "fresh environment python"
    fake_python.write_text(
        "#!/bin/sh\n"
        f"touch {shlex.quote(str(marker))}\n"
        "exit 7\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(SCRIPT), "--python", str(fake_python)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert marker.exists()
    assert str(fake_python) in completed.stderr
    assert "turbo_seti is not importable" in completed.stderr


def test_patch_rejects_missing_python_argument() -> None:
    completed = subprocess.run(
        ["bash", str(SCRIPT), "--python"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "--python requires an interpreter path" in completed.stderr


def test_patch_does_not_fall_back_to_system_python() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "python3 -c" not in source
    assert '"${TARGET_PYTHON}" -c' in source
