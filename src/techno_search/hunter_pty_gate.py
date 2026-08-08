"""Real-PTY operator gate for the installed Hunter shell.

``docs/CLI_UX_SPEC.md`` UX-CMD-01 requires that typing ``/`` *immediately*
opens the searchable command palette. That requirement is only observable
against a real terminal: a pipe delivers bytes to the child in line-buffered
chunks, so a piped ``"/\\n"`` proves nothing about a bare ``/`` keystroke.

This module therefore drives the *installed* canonical executable as a real
operating-system subprocess attached to a pseudo-terminal, sends individual
keystrokes, and reports what the terminal actually rendered.

Two rules are deliberate:

1. The child is spawned by absolute path from a directory outside the
   repository, with no ``PYTHONPATH`` and no source-tree leakage, because the
   contract states those substitutions do not satisfy the launch gate.
2. When the host denies pseudo-terminal allocation, the driver reports
   ``pty_available=False`` with the operating-system reason. Callers must
   surface that as ``NOT_EXECUTED``. It is never a pass, per CLAIM-03.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import select
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Conservative caps so a hung child can never wedge the gate.
DEFAULT_STARTUP_TIMEOUT = 20.0
DEFAULT_KEY_TIMEOUT = 8.0
DEFAULT_EXIT_TIMEOUT = 20.0

_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_SIMPLE = re.compile(r"\x1b[@-Z\\-_]")


def strip_ansi(text: str) -> str:
    """Return ``text`` with terminal control sequences removed."""
    without_osc = _ANSI_OSC.sub("", text)
    without_csi = _ANSI_CSI.sub("", without_osc)
    return _ANSI_SIMPLE.sub("", without_csi)


class PtyUnavailableError(OSError):
    """Raised when the host refuses to allocate a pseudo-terminal."""


@dataclass
class PtyCapture:
    """Everything one PTY session observed, for durable evidence."""

    command: list[str]
    cwd: str
    pty_available: bool
    unavailable_reason: str | None = None
    exit_status: int | None = None
    timed_out: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)
    raw: str = ""

    @property
    def plain(self) -> str:
        """The full transcript with ANSI control sequences removed."""
        return strip_ansi(self.raw)

    def step(self, label: str) -> dict[str, Any]:
        """Return the recorded step named ``label``."""
        for entry in self.steps:
            if entry["label"] == label:
                return entry
        raise KeyError(label)

    def as_dict(self, *, stream_limit: int = 4000) -> dict[str, Any]:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "pty_available": self.pty_available,
            "unavailable_reason": self.unavailable_reason,
            "exit_status": self.exit_status,
            "timed_out": self.timed_out,
            "steps": [
                {
                    "label": entry["label"],
                    "sent": entry["sent"],
                    "plain_tail": entry["plain"][-stream_limit:],
                }
                for entry in self.steps
            ],
            "plain_transcript_tail": self.plain[-stream_limit:],
        }


def pty_supported() -> tuple[bool, str | None]:
    """Report whether this host will allocate a *complete* pseudo-terminal.

    Returns ``(True, None)`` or ``(False, reason)``.

    The probe performs a full round trip rather than only opening a master
    device. That distinction is load-bearing: a sandbox can permit the
    ``/dev/ptmx`` open while still denying ``grantpt()``, which macOS implements
    as the ``TIOCPTYGRANT`` ioctl. Probing only the open reports a false
    positive and degrades the failure reason to a bare ``OSError``.
    """
    try:
        import pty as _pty

        master_fd, slave_fd = _pty.openpty()
    except OSError as exc:
        return False, _describe_pty_failure(exc)
    os.close(master_fd)
    os.close(slave_fd)
    return True, None


def _errno_name(exc: OSError) -> str:
    """Return a stable symbolic name for an OS error number."""
    number = exc.errno
    if number is None:
        return "UNKNOWN"
    return errno.errorcode.get(number, str(number))


def _describe_pty_failure(exc: OSError) -> str:
    """Explain an allocation failure, naming the step that actually failed.

    ``pty.openpty()`` reports ``out of pty devices`` for every failure mode,
    including permission denials, and raises it with no ``errno``. That message
    is actively misleading in a sandbox, so this walks the allocation sequence
    to identify the first step the host refused.
    """
    detail = exc.strerror or str(exc) or exc.__class__.__name__
    base = f"openpty failed: {_errno_name(exc)}: {detail}"
    step = _first_failing_allocation_step()
    return base if step is None else f"{base}; first refused step: {step}"


def _first_failing_allocation_step() -> str | None:
    """Return the first refused step of the manual allocation sequence."""
    try:
        import ctypes
        import ctypes.util

        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    except Exception:  # pragma: no cover - ctypes unavailable
        return None

    opener = getattr(os, "posix_openpt", None)
    if opener is None:  # pragma: no cover - platform dependent
        return None
    try:
        master_fd = opener(os.O_RDWR | os.O_NOCTTY)
    except OSError as exc:
        return f"posix_openpt() -> {_errno_name(exc)}"

    try:
        for name in ("grantpt", "unlockpt"):
            function = getattr(libc, name, None)
            if function is None:  # pragma: no cover - platform dependent
                continue
            ctypes.set_errno(0)
            if function(master_fd) != 0:
                code = ctypes.get_errno()
                symbol = errno.errorcode.get(code, str(code))
                hint = (
                    " (macOS implements this as an ioctl, which a sandbox may "
                    "deny independently of any filesystem rule)"
                    if name == "grantpt"
                    else ""
                )
                return f"{name}() -> {symbol}{hint}"
    finally:
        os.close(master_fd)
    return None


class PtySession:
    """Drive one installed-executable process attached to a real PTY."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        columns: int = 100,
        lines: int = 30,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.columns = columns
        self.lines = lines
        self._master_fd: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._raw = bytearray()

    # -- lifecycle -------------------------------------------------------

    def __enter__(self) -> PtySession:
        import pty as _pty  # imported lazily so import never fails on odd hosts

        try:
            master_fd, slave_fd = _pty.openpty()
        except OSError as exc:
            raise PtyUnavailableError(
                exc.errno, f"openpty failed: {_errno_name(exc)}: {exc.strerror}"
            ) from exc
        self._set_window_size(master_fd)
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(self.cwd),
                env=self.env,
                close_fds=True,
                # A new session gives the child the PTY as its controlling
                # terminal, which is what makes isatty() true for it.
                start_new_session=True,
            )
        finally:
            os.close(slave_fd)
        self._master_fd = master_fd
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _set_window_size(self, master_fd: int) -> None:
        try:
            import fcntl
            import struct
            import termios

            packed = struct.pack("HHHH", self.lines, self.columns, 0, 0)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, packed)
        except Exception:  # pragma: no cover - non-fatal cosmetic sizing
            pass

    def close(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):  # pragma: no cover
                process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        if self._master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._master_fd)
            self._master_fd = None

    # -- io --------------------------------------------------------------

    def send(self, data: str) -> None:
        """Write raw bytes to the terminal exactly as typed."""
        assert self._master_fd is not None
        os.write(self._master_fd, data.encode("utf-8"))

    def read_until(
        self,
        *,
        timeout: float,
        predicate: Any = None,
        settle: float = 0.35,
    ) -> str:
        """Read until ``predicate`` is satisfied, then let output settle.

        Returns only the text produced by this call. ``settle`` keeps reading
        briefly after the predicate matches so a redraw is captured whole
        rather than truncated mid-frame.
        """
        assert self._master_fd is not None
        chunk_start = len(self._raw)
        deadline = time.monotonic() + timeout
        satisfied_at: float | None = None
        while True:
            now = time.monotonic()
            if satisfied_at is not None and now - satisfied_at >= settle:
                break
            if now >= deadline:
                break
            readable, _, _ = select.select([self._master_fd], [], [], 0.1)
            if readable:
                try:
                    data = os.read(self._master_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                self._raw.extend(data)
            produced = self._decode(self._raw[chunk_start:])
            if satisfied_at is None and (predicate is None or predicate(strip_ansi(produced))):
                if predicate is None and not readable:
                    # No predicate means "drain until quiet".
                    satisfied_at = time.monotonic()
                elif predicate is not None:
                    satisfied_at = time.monotonic()
        return self._decode(self._raw[chunk_start:])

    @staticmethod
    def _decode(data: bytes | bytearray) -> str:
        return bytes(data).decode("utf-8", errors="replace")

    @property
    def raw(self) -> str:
        return self._decode(self._raw)

    def wait(self, timeout: float) -> int | None:
        process = self._process
        if process is None:  # pragma: no cover
            return None
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


def operator_env(*, columns: int = 100) -> dict[str, str]:
    """Build a clean interactive operator environment.

    Removes every variable that could let the child resolve the repository
    source tree instead of its installed package, and every variable that
    suppresses animation, since the gate must observe the real startup
    experience.
    """
    env = dict(os.environ)
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "CI",
        "NO_COLOR",
        "REDUCE_MOTION",
        "TECHNO_HUNTER_REDUCE_MOTION",
    ):
        env.pop(key, None)
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = str(columns)
    env["LINES"] = "30"
    return env


def drive_palette_session(
    executable: Path,
    *,
    cwd: Path,
    columns: int = 100,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    key_timeout: float = DEFAULT_KEY_TIMEOUT,
    exit_timeout: float = DEFAULT_EXIT_TIMEOUT,
) -> PtyCapture:
    """Drive the canonical UX-CMD-01 keystroke sequence and capture it.

    The recorded steps are, in order:

    ``startup``
        Everything rendered before any key is pressed.
    ``bare_slash``
        The response to a single ``/`` byte with **no** carriage return. This
        is the assertion that distinguishes a real palette from a
        line-buffered ``input()`` loop.
    ``filter``
        The response to typing further characters, proving live filtering.
    ``navigate``
        The response to a Down-arrow, proving selection moves.
    ``escape``
        The response to Escape, proving the palette closes.
    """
    command = [str(executable)]
    capture = PtyCapture(command=command, cwd=str(cwd), pty_available=True)
    supported, reason = pty_supported()
    if not supported:
        capture.pty_available = False
        capture.unavailable_reason = reason
        return capture

    env = operator_env(columns=columns)
    try:
        session_cm = PtySession(command, cwd=cwd, env=env, columns=columns)
    except PtyUnavailableError as exc:  # pragma: no cover - defensive
        capture.pty_available = False
        capture.unavailable_reason = str(exc)
        return capture

    def record(label: str, sent: str, text: str) -> None:
        capture.steps.append({"label": label, "sent": sent, "raw": text, "plain": strip_ansi(text)})

    try:
        with session_cm as session:
            startup = session.read_until(
                timeout=startup_timeout,
                predicate=lambda text: "TechnoHunter>" in text,
            )
            record("startup", "", startup)

            session.send("/")
            bare = session.read_until(
                timeout=key_timeout,
                predicate=lambda text: "/New-Search" in text,
            )
            record("bare_slash", "/", bare)

            session.send("New")
            filtered = session.read_until(
                timeout=key_timeout,
                predicate=lambda text: "/New-Search" in text,
            )
            record("filter", "New", filtered)

            session.send("\x7f\x7f\x7f")  # backspace back to a bare "/"
            session.read_until(timeout=key_timeout)

            session.send("\x1b[B")  # Down arrow
            navigated = session.read_until(timeout=key_timeout)
            record("navigate", "<Down>", navigated)

            session.send("\x1b")  # Escape closes the palette
            escaped = session.read_until(timeout=key_timeout)
            record("escape", "<Escape>", escaped)

            session.send("/Exit\r")
            closing = session.read_until(timeout=key_timeout)
            record("exit", "/Exit<CR>", closing)

            status = session.wait(timeout=exit_timeout)
            capture.exit_status = status
            capture.timed_out = status is None
            capture.raw = session.raw
    except PtyUnavailableError as exc:
        capture.pty_available = False
        capture.unavailable_reason = str(exc)
    return capture
