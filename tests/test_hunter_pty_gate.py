"""Regressions for the real-terminal operator gate.

``docs/CLI_UX_SPEC.md`` UX-CMD-01 requires the palette to open on a bare ``/``
keystroke. A piped ``"/\\n"`` cannot observe that, so the gate must use a real
pseudo-terminal.

These tests cover the parts that are host-independent, plus the rule that
matters most when a host refuses a pseudo-terminal: the gate must report
``NOT_EXECUTED``, never ``PASS``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from techno_search.hunter_prod_check import (
    CANONICAL_EXECUTABLE,
    ProdCheckEnvironment,
    check_interactive_pty_palette,
)
from techno_search.hunter_pty_gate import (
    PtyCapture,
    drive_palette_session,
    operator_env,
    pty_supported,
    strip_ansi,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestAnsiStripping:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("\x1b[31mred\x1b[0m", "red"),
            ("\x1b[2J\x1b[Hcleared", "cleared"),
            ("\x1b]0;title\x07body", "body"),
            ("plain", "plain"),
            ("\x1b[38;5;208mx\x1b[0m", "x"),
        ],
    )
    def test_control_sequences_are_removed(self, raw: str, expected: str) -> None:
        assert strip_ansi(raw) == expected

    def test_palette_text_survives_stripping(self) -> None:
        rendered = "\x1b[1m/New-Search\x1b[0m  Required: targets"
        assert "/New-Search" in strip_ansi(rendered)
        assert "Required: targets" in strip_ansi(rendered)


class TestOperatorEnvironment:
    def test_source_tree_leakage_variables_are_removed(self) -> None:
        """No PYTHONPATH may reach the child, per LAUNCH-04."""
        env = operator_env()
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            assert key not in env

    def test_animation_suppressors_are_removed(self) -> None:
        """The gate must observe the real startup experience, not a degraded one."""
        env = operator_env()
        for key in ("CI", "NO_COLOR", "REDUCE_MOTION", "TECHNO_HUNTER_REDUCE_MOTION"):
            assert key not in env

    def test_terminal_is_declared_capable(self) -> None:
        env = operator_env(columns=140)
        assert env["TERM"] == "xterm-256color"
        assert env["COLUMNS"] == "140"


class TestUnavailableHost:
    def test_capture_reports_unavailability_rather_than_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "techno_search.hunter_pty_gate.pty_supported",
            lambda: (False, "posix_openpt failed: EPERM: Operation not permitted"),
        )
        capture = drive_palette_session(
            Path("/nonexistent") / CANONICAL_EXECUTABLE, cwd=REPO_ROOT
        )
        assert capture.pty_available is False
        assert "EPERM" in (capture.unavailable_reason or "")
        assert capture.steps == []

    def test_check_maps_an_unavailable_pty_to_not_executed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLAIM-03: a gate that cannot run is NOT_EXECUTED, never a pass."""
        monkeypatch.setattr(
            "techno_search.hunter_prod_check.drive_palette_session",
            lambda executable, **kwargs: PtyCapture(
                command=[str(executable)],
                cwd=str(REPO_ROOT),
                pty_available=False,
                unavailable_reason="posix_openpt failed: EPERM: Operation not permitted",
            ),
        )
        env = ProdCheckEnvironment(REPO_ROOT)
        if not env.executable(CANONICAL_EXECUTABLE).exists():
            pytest.skip("canonical executable is not installed in this environment")
        outcome = check_interactive_pty_palette(env)
        assert outcome.status == "NOT_EXECUTED"
        assert outcome.mandatory_failure is True
        assert "pseudo-terminal" in outcome.summary


class TestBareSlashAssertion:
    """The decisive assertion: a line-buffered shell must not pass."""

    def _capture(self, bare_slash_output: str) -> PtyCapture:
        capture = PtyCapture(command=[CANONICAL_EXECUTABLE], cwd="/tmp", pty_available=True)
        capture.exit_status = 0
        for label, text in (
            ("startup", "TechnoHunter 1.2.73\nTechnoHunter> "),
            ("bare_slash", bare_slash_output),
            ("filter", bare_slash_output),
            ("navigate", "> /New-Search"),
            ("escape", "Palette closed."),
            ("exit", "Array idle."),
        ):
            capture.steps.append({"label": label, "sent": "", "raw": text, "plain": text})
        return capture

    def test_line_buffered_shell_fails_the_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A shell that echoes '/' and waits for Enter renders no palette."""
        capture = self._capture("/")
        monkeypatch.setattr(
            "techno_search.hunter_prod_check.drive_palette_session",
            lambda executable, **kwargs: capture,
        )
        env = ProdCheckEnvironment(REPO_ROOT)
        if not env.executable(CANONICAL_EXECUTABLE).exists():
            pytest.skip("canonical executable is not installed in this environment")
        outcome = check_interactive_pty_palette(env)
        assert outcome.status == "FAIL"
        problems = " ".join(outcome.detail["problems"])
        assert "bare '/' keystroke did not open a palette" in problems


class TestHostCapability:
    def test_pty_support_probe_is_observed_not_assumed(self) -> None:
        """The probe must return a concrete reason when it says no."""
        supported, reason = pty_supported()
        assert isinstance(supported, bool)
        if supported:
            assert reason is None
        else:
            assert reason and "failed" in reason
