"""Persistent slash-command shell for the canonical Techno-Hunter lifecycle.

This is a presentation and interaction layer only. Candidate selection,
scientific scoring, execution, persistence, provenance, and business validation
all live behind the canonical one-shot entry points in
:mod:`techno_search.hunter_cli` (CLI_UX_SPEC section 12, contract PIPE-02).
"""

from __future__ import annotations

import argparse
import os
import select
import shlex
import sys
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from techno_search import __version__
from techno_search.hunter_cli import (
    create_new_search,
    inspect_target_command,
    run_new_search,
    show_follow_ups,
)
from techno_search.hunter_commands import (
    COMMANDS,
    CommandSpec,
    KeyboardHelp,
    ShellState,
    build_cli_arguments,
    command_by_name,
    completion_candidates,
    describe_palette,
    filter_commands,
)
from techno_search.hunter_tables import (
    build_console,
    render_guided_fields,
    render_palette,
)
from techno_search.hunter_validation import (
    FieldValidationError,
    validate_search_id,
    validate_target_count,
)

try:  # readline is optional on some Python platforms.
    import readline
except ImportError:  # pragma: no cover - platform-specific fallback
    readline = None  # type: ignore[assignment]


SHELL_DISCLAIMER = (
    "Local scientific triage only — no detection, discovery, expert-review, "
    "external-validation, or submission claim."
)
REQUIRED_COMMANDS = tuple(spec.name for spec in COMMANDS)

PROMPT = "TechnoHunter> "

# The startup identity animation (UX-START-01/02): an encoded-noise waterfall
# resolving into a structured signal. Frames are decorative only and never
# assert a target, discovery, percentage, or data state (UX-START-03).
_STARTUP_FRAMES = (
    "·  ˙ ·˙  ·  ˙· ˙ ·  ˙ ·˙ ·  ˙",
    "· ▁˙ ·˙ ▁·  ˙· ▁ ·  ˙▁·˙ ·  ˙",
    "· ▃˙ ▁·˙ ▃· ▁˙· ▃ ·  ▁▃·˙ ▁ ˙",
    "▁ ▅▃ ▃·▁ ▅· ▃▁· ▅ ▃· ▃▅·▁ ▃ ▁",
    "▃ ▇▅ ▅▃▁ ▇▃ ▅▃▁ ▇ ▅▃ ▅▇▃▁ ▅ ▃",
    "▅ █▇ ▇▅▃ █▅ ▇▅▃ █ ▇▅ ▇█▅▃ ▇ ▅",
    "▇ █▇▇█▇▅ █▇ █▇▅ █ █▇ █████ █ ▇",
    "█▇█ ▇█ ▇█▇ ▇█▇ █▇█ ▇█ ▇█▇ █▇█",
)
_SIGNAL_FRAMES = (
    "▁▂▄▆█▆▄▂▁",
    "▂▄▆█▆▄▂▁▂",
    "▄▆█▆▄▂▁▂▄",
    "▆█▆▄▂▁▂▄▆",
    "█▆▄▂▁▂▄▆█",
    "▆▄▂▁▂▄▆█▆",
)

CommandHandler = Callable[[Sequence[str] | None], int]


@dataclass(frozen=True)
class CommandHandlers:
    """Canonical one-shot adapters used by the persistent shell."""

    create: CommandHandler = create_new_search
    run: CommandHandler = run_new_search
    show_follow_ups: CommandHandler = show_follow_ups
    inspect: CommandHandler = inspect_target_command


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of one slash command."""

    exit_code: int
    exit_requested: bool = False


class SignalSweep:
    """Technosignature-specific live spectrum animation for real command work."""

    def __init__(self, console: Console, *, enabled: bool) -> None:
        self.console = console
        self.enabled = enabled
        self._label = "Tuning array"
        self._frame_index = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._live: Live | None = None

    def __enter__(self) -> SignalSweep:
        if not self.enabled:
            return self
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=12,
            transient=True,
            redirect_stdout=True,
            redirect_stderr=True,
        )
        self._live.start()
        self._thread = threading.Thread(
            target=self._animate,
            name="techno-hunter-signal-sweep",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._live is not None:
            self._live.stop()

    def event(self, label: str) -> None:
        """Update the animation label from a real lifecycle event."""
        self._label = label
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
        elif not self.enabled:
            self.console.print(f"... {label}", style="cyan")

    def _animate(self) -> None:
        while not self._stop.wait(0.09):
            self._frame_index = (self._frame_index + 1) % len(_SIGNAL_FRAMES)
            if self._live is not None:
                self._live.update(self._render(), refresh=True)

    def _render(self) -> Text:
        return Text.assemble(
            ("  ≋ ", "bold cyan"),
            (_SIGNAL_FRAMES[self._frame_index], "bright_magenta"),
            (f"  {self._label}", "cyan"),
        )


class HunterShell:
    """Parse slash commands and delegate to the canonical Hunter entry points."""

    def __init__(
        self,
        *,
        handlers: CommandHandlers | None = None,
        stdin: TextIO = sys.stdin,
        stdout: TextIO = sys.stdout,
        stderr: TextIO = sys.stderr,
        interactive: bool | None = None,
        no_animation: bool = False,
        no_color: bool = False,
        history_path: Path = Path("artifacts/techno_hunter_history"),
        searches_dir: Path = Path("results/searches"),
        scans_dir: Path = Path("results/scans"),
        priority_queue: Path = Path("data_selection/target_priority_queue.csv"),
    ) -> None:
        self.handlers = handlers or CommandHandlers()
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.interactive = (
            bool(stdin.isatty() and stdout.isatty())
            if interactive is None
            else interactive
        )
        terminal_capable = bool(
            self.interactive
            and stdout.isatty()
            and os.environ.get("TERM", "") != "dumb"
        )
        color_enabled = terminal_capable and not no_color and "NO_COLOR" not in os.environ
        reduce_motion = bool(
            os.environ.get("REDUCE_MOTION")
            or os.environ.get("TECHNO_HUNTER_REDUCE_MOTION")
            or os.environ.get("CI")
        )
        self.animation_enabled = bool(
            terminal_capable and not no_animation and not reduce_motion
        )
        self.console = build_console(stdout, color=color_enabled)
        self.history_path = history_path
        self.searches_dir = searches_dir
        self.scans_dir = scans_dir
        self.priority_queue = priority_queue

    # -- state -----------------------------------------------------------

    def state(self) -> ShellState:
        """Observe only real durable state for palette availability."""
        return ShellState.observe(
            searches_dir=self.searches_dir,
            scans_dir=self.scans_dir,
            priority_queue=self.priority_queue,
        )

    # -- lifecycle -------------------------------------------------------

    def run(self, commands: Sequence[str] = ()) -> int:
        """Run explicit commands, piped commands, or the persistent prompt."""
        if commands:
            return self._run_lines(commands)
        if not self.interactive:
            return self._run_lines(self.stdin)

        self._configure_readline()
        self._play_startup_animation()
        self._print_banner()
        exit_code = 0
        try:
            while True:
                try:
                    line = self._read_command_line(PROMPT)
                except EOFError:
                    self.console.print()
                    break
                except KeyboardInterrupt:
                    self.console.print(
                        "\n[yellow]Command cancelled; search state is unchanged.[/]"
                    )
                    continue
                if isinstance(line, CommandSpec):
                    # UX-CMD-01: `/` already opened the palette and the
                    # operator chose from it, so run that command directly.
                    result = self._run_selected_command(line, self.state())
                else:
                    result = self.dispatch(line)
                exit_code = result.exit_code
                if result.exit_requested:
                    break
        finally:
            self._save_history()
        return exit_code

    # -- prompt ----------------------------------------------------------

    def _read_command_line(self, prompt: str) -> str | CommandSpec:
        """Read one command, opening the palette the instant `/` is typed.

        ``input()`` cannot satisfy UX-CMD-01: it is line buffered, so a bare
        ``/`` is not delivered until Enter is pressed. This reads keystrokes
        directly and hands over to the palette as soon as ``/`` starts a line.

        Returns the typed line, or the :class:`CommandSpec` the operator chose
        from the palette. Raw mode is always released before the caller runs
        anything, because command execution reads whole lines.

        When raw mode is unavailable — a pipe, a dumb terminal, a platform
        without ``termios`` — it falls back to ``input()`` so redirected and
        non-TTY operation keep working unchanged.
        """
        reader = _KeyReader(self.stdin)
        if not reader.available:
            return input(prompt)

        self.stdout.write(prompt)
        self.stdout.flush()
        buffer = ""
        history_index: int | None = None
        chosen: CommandSpec | None = None
        with reader:
            while True:
                key = reader.read_key()
                if key == "/" and not buffer:
                    # Hand over to the palette on the SAME raw reader. Leaving
                    # and re-entering raw mode here would discard keystrokes
                    # already queued behind the "/".
                    self.stdout.write("\r\n")
                    self.stdout.flush()
                    chosen = self._palette_loop(reader, "", self.state())
                    break
                if key == "ENTER":
                    self.stdout.write("\r\n")
                    self.stdout.flush()
                    self._remember(buffer)
                    return buffer
                if key == "CTRL_C":
                    raise KeyboardInterrupt
                if key in {"CTRL_D", "EOF"} and not buffer:
                    raise EOFError
                if key == "BACKSPACE":
                    if buffer:
                        buffer = buffer[:-1]
                        # Erase the character the terminal is still showing.
                        self.stdout.write("\b \b")
                        self.stdout.flush()
                    continue
                if key in {"UP", "DOWN"}:
                    buffer, history_index = self._history_step(
                        key, buffer, history_index, prompt
                    )
                    continue
                if key == "TAB":
                    buffer = self._complete_inline(buffer)
                    continue
                if len(key) == 1 and key.isprintable():
                    buffer += key
                    self.stdout.write(key)
                    self.stdout.flush()
        # Reached only via the palette hand-off above; raw mode is now released.
        if chosen is None:
            return ""
        return chosen

    def _redraw(self, prompt: str, buffer: str) -> None:
        """Repaint the prompt line after replacing its contents."""
        self.stdout.write("\r\x1b[2K" + prompt + buffer)
        self.stdout.flush()

    def _complete_inline(self, buffer: str) -> str:
        """Complete a partially typed slash command in place."""
        candidates = completion_candidates(buffer)
        if len(candidates) != 1:
            return buffer
        completed = candidates[0]
        self.stdout.write(completed[len(buffer):])
        self.stdout.flush()
        return completed

    def _history_entries(self) -> list[str]:
        if readline is None:
            return []
        return [
            readline.get_history_item(index)
            for index in range(1, readline.get_current_history_length() + 1)
        ]

    def _history_step(
        self, key: str, buffer: str, index: int | None, prompt: str
    ) -> tuple[str, int | None]:
        entries = self._history_entries()
        if not entries:
            return buffer, index
        if key == "UP":
            index = len(entries) - 1 if index is None else max(0, index - 1)
        else:
            if index is None:
                return buffer, index
            index += 1
            if index >= len(entries):
                self._redraw(prompt, "")
                return "", None
        buffer = entries[index]
        self._redraw(prompt, buffer)
        return buffer, index

    def _remember(self, line: str) -> None:
        if readline is not None and line.strip():
            readline.add_history(line)

    def dispatch(self, line: str) -> DispatchResult:
        """Execute one slash command without duplicating scientific logic."""
        stripped = line.strip()
        if not stripped:
            return DispatchResult(0)
        if stripped == "/":
            return self._open_palette("")
        try:
            tokens = shlex.split(stripped)
        except ValueError as exc:
            self._error(f"could not parse command: {exc}")
            return DispatchResult(1)
        spec = command_by_name(tokens[0])
        if spec is None:
            if tokens[0].startswith("/") and len(tokens) == 1:
                # A partial slash token filters the palette rather than erroring.
                matches = filter_commands(tokens[0])
                if matches:
                    return self._open_palette(tokens[0])
            self._error(
                f"unknown command {tokens[0]!r}; type / or /Help to list commands"
            )
            return DispatchResult(1)

        arguments = tokens[1:]

        # Validate what the operator actually typed before reporting a state
        # prerequisite, so a mistyped value gets the specific sentinel it needs
        # rather than a less actionable availability message (UX-IN-03).
        if (
            spec.name in {"/New-Search", "/Follow-Up-Search"}
            and arguments
            and not arguments[0].startswith("-")
        ):
            try:
                validate_target_count(arguments[0])
            except FieldValidationError as exc:
                self._error(str(exc))
                return DispatchResult(1)

        reason = spec.unavailable_reason(self.state())
        if reason is not None and spec.name not in {"/Help", "/Exit"}:
            self._error(f"{spec.name} is unavailable — {reason}")
            return DispatchResult(1)

        if spec.name == "/Help":
            self._print_help()
            return DispatchResult(0)
        if spec.name == "/Exit":
            self.console.print(
                "Array idle. Durable searches remain on disk.", style="dim"
            )
            return DispatchResult(0, exit_requested=True)
        if spec.name == "/New-Search":
            return DispatchResult(self._dispatch_create(spec, arguments, mode="new"))
        if spec.name == "/Follow-Up-Search":
            return DispatchResult(
                self._dispatch_create(spec, arguments, mode="follow-up")
            )
        if spec.name == "/Run-Search":
            return DispatchResult(self._dispatch_run(spec, arguments))
        if spec.name == "/Inspect-Target":
            return DispatchResult(self._dispatch_inspect(spec, arguments))
        command_args = list(arguments)
        self._append_nondefault_path(
            command_args,
            "--scans-dir",
            self.scans_dir,
            Path("results/scans"),
        )
        self._append_nondefault_path(
            command_args,
            "--searches-dir",
            self.searches_dir,
            Path("results/searches"),
        )
        self._append_nondefault_path(
            command_args,
            "--priority-queue",
            self.priority_queue,
            Path("data_selection/target_priority_queue.csv"),
        )
        return DispatchResult(self.handlers.show_follow_ups(command_args))

    # -- palette ---------------------------------------------------------

    def _open_palette(self, query: str) -> DispatchResult:
        """UX-CMD-01: `/` immediately opens a searchable, described palette.

        Returns a full :class:`DispatchResult` rather than a bare exit code so
        that choosing ``/Exit`` from the palette actually leaves the shell.
        """
        state = self.state()
        if self.interactive and self.stdin.isatty():
            return self._interactive_palette(query, state)
        rows = describe_palette(filter_commands(query, state), state)
        render_palette(rows, console=self.console, query=query)
        self._print_keyboard_help()
        self._print_option_convention()
        return DispatchResult(0)

    def _print_option_convention(self) -> None:
        self.console.print(
            "Options after a slash command are the same as its one-shot executable. "
            "Use --json for machine-readable output.",
            style="dim",
        )

    def _interactive_palette(self, query: str, state: ShellState) -> DispatchResult:
        """Live-filtering palette with Up/Down, Enter, Escape and Tab."""
        reader = _KeyReader(self.stdin)
        if not reader.available:
            rows = describe_palette(filter_commands(query, state), state)
            render_palette(rows, console=self.console, query=query)
            self._print_keyboard_help()
            return DispatchResult(0)
        # The command must run in cooked mode, so the reader context closes
        # before dispatch: guided entry reads whole lines, not keystrokes.
        with reader:
            chosen = self._palette_loop(reader, query, state)
        if chosen is None:
            return DispatchResult(0)
        return self._run_selected_command(chosen, state)

    def _palette_loop(
        self, reader: _KeyReader, query: str, state: ShellState
    ) -> CommandSpec | None:
        """Run the palette keystroke loop on an already-active raw reader.

        Returns the chosen command, or ``None`` when the operator closed the
        palette.

        Taking the reader as a parameter is what lets the prompt hand over to
        the palette without leaving and re-entering raw mode. That transition
        discards keystrokes already sitting in the terminal's input queue, so a
        fast typist — or a scripted burst like ``/Exit\\r`` — loses everything
        after the ``/``.
        """
        selected = 0
        while True:
            matches = filter_commands(query, state)
            selected = max(0, min(selected, max(0, len(matches) - 1)))
            self.console.clear()
            render_palette(
                describe_palette(matches, state),
                console=self.console,
                query=query,
                selected_index=selected,
            )
            self._print_keyboard_help()
            key = reader.read_key()
            if key in {"ESCAPE", "CTRL_C", "EOF"}:
                self.console.print("Palette closed.", style="dim")
                return None
            if key == "UP":
                selected -= 1
            elif key == "DOWN":
                selected += 1
            elif key == "BACKSPACE":
                if not query:
                    self.console.print("Palette closed.", style="dim")
                    return None
                query = query[:-1]
            elif key in {"ENTER", "TAB"}:
                if not matches:
                    continue
                chosen = matches[selected]
                if key == "TAB":
                    query = chosen.name
                    continue
                return chosen
            elif len(key) == 1 and key.isprintable():
                query += key
                selected = 0

    def _run_selected_command(
        self, spec: CommandSpec, state: ShellState
    ) -> DispatchResult:
        """Run a palette selection, preserving the dispatch result verbatim.

        Returning the whole :class:`DispatchResult` matters: collapsing it to an
        exit code silently discarded ``exit_requested``, so choosing ``/Exit``
        from the palette printed its farewell and then returned to the prompt.
        """
        reason = spec.unavailable_reason(state)
        if reason is not None:
            self._error(f"{spec.name} is unavailable — {reason}")
            return DispatchResult(1)
        if spec.name in {"/Help", "/Exit"}:
            return self.dispatch(spec.name)
        values = self._guided_entry(spec)
        if values is None:
            self.console.print("Cancelled; durable state unchanged.", style="dim")
            return DispatchResult(0)
        return self.dispatch(" ".join([spec.name, *build_cli_arguments(spec, values)]))

    # -- guided entry ----------------------------------------------------

    def _field_rows(self, spec: CommandSpec, *, advanced: bool) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for field_spec in spec.fields:
            if field_spec.advanced and not advanced:
                continue
            rows.append(
                {
                    "label": field_spec.label,
                    "placeholder": field_spec.placeholder(),
                    "description": field_spec.description,
                    "required": "yes" if field_spec.required else "no",
                }
            )
        if not advanced and any(field.advanced for field in spec.fields):
            rows.append(
                {
                    "label": "Scientific constraints",
                    "placeholder": "[Open…]",
                    "description": (
                        "Optional RA/Dec window, Galactic-latitude floor, per-target "
                        "size cap and catalog prefixes."
                    ),
                    "required": "no",
                }
            )
        return rows

    def _guided_entry(self, spec: CommandSpec) -> dict[str, str] | None:
        """UX-IN-01/02/03: guided fields, visible defaults, live sentinels."""
        render_guided_fields(
            spec.name, self._field_rows(spec, advanced=False), console=self.console
        )
        if not (self.interactive and self.stdin.isatty()):
            return None
        values: dict[str, str] = {}
        show_advanced = False
        for field_spec in spec.fields:
            if field_spec.advanced and not show_advanced:
                continue
            while True:
                prompt = f"  {field_spec.label} {field_spec.placeholder()}: "
                try:
                    raw = input(prompt).strip()
                except (EOFError, KeyboardInterrupt):
                    return None
                if raw == "":
                    if field_spec.required and field_spec.default is None:
                        self.console.print(
                            f"  Invalid — {field_spec.label} is required.", style="red"
                        )
                        continue
                    if field_spec.required and field_spec.default is not None:
                        raw = field_spec.default
                    else:
                        break
                try:
                    values[field_spec.name] = self._validate_field(spec, field_spec.name, raw)
                except FieldValidationError as exc:
                    self.console.print(f"  {exc}", style="red")
                    continue
                break
            if (
                not show_advanced
                and field_spec.name == "targets"
                and any(item.advanced for item in spec.fields)
            ):
                try:
                    answer = input("  Open scientific constraints? [y/N]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    return None
                show_advanced = answer.casefold().startswith("y")
                if show_advanced:
                    render_guided_fields(
                        "Scientific constraints",
                        self._field_rows(spec, advanced=True),
                        console=self.console,
                    )
        return values

    def _validate_field(self, spec: CommandSpec, name: str, raw: str) -> str:
        """Route every guided value through the canonical shared validators."""
        if name == "targets":
            return str(validate_target_count(raw))
        if name == "search_id":
            return str(validate_search_id(raw) or "")
        return raw

    # -- command dispatch ------------------------------------------------

    def _dispatch_create(
        self, spec: CommandSpec, args: list[str], *, mode: str
    ) -> int:
        if not args:
            values = self._guided_entry(spec)
            if values is None:
                self._error(
                    f"{spec.name} requires a positive target count — "
                    f"for example: {spec.name} 20"
                )
                return 1
            args = build_cli_arguments(spec, values)
        if args and not args[0].startswith("-"):
            try:
                count = validate_target_count(args[0])
            except FieldValidationError as exc:
                self._error(str(exc))
                return 1
            args = ["--targets", str(count), *args[1:]]
        elif "--targets" not in args:
            self._error(
                f"{spec.name} requires a positive target count — "
                f"for example: {spec.name} 20"
            )
            return 1
        # Mode is inherent in the command name, so it is injected immediately
        # after --targets. An explicitly supplied --mode is never duplicated.
        if "--mode" in args:
            command_args = list(args)
        else:
            index = args.index("--targets") + 2
            command_args = [*args[:index], "--mode", mode, *args[index:]]
        self._append_nondefault_path(
            command_args,
            "--scans-dir",
            self.scans_dir,
            Path("results/scans"),
        )
        self._append_nondefault_path(
            command_args,
            "--searches-dir",
            self.searches_dir,
            Path("results/searches"),
        )
        self._append_nondefault_path(
            command_args,
            "--priority-queue",
            self.priority_queue,
            Path("data_selection/target_priority_queue.csv"),
        )
        label = (
            "Adaptive discovery → identity/history → new-target ranking"
            if mode == "new"
            else "Durable evidence → follow-up value ranking"
        )
        with SignalSweep(self.console, enabled=self.animation_enabled) as sweep:
            sweep.event(label)
            return self.handlers.create(command_args)

    def _dispatch_run(self, spec: CommandSpec, args: list[str]) -> int:
        command_args = list(args)
        if command_args and not command_args[0].startswith("-"):
            try:
                search_id = validate_search_id(command_args[0])
            except FieldValidationError as exc:
                self._error(str(exc))
                return 1
            command_args = ["--search-id", str(search_id), *command_args[1:]]
        self._append_nondefault_path(
            command_args,
            "--searches-dir",
            self.searches_dir,
            Path("results/searches"),
        )
        self._animate_transition("Authenticating immutable search → signal pipeline")
        return self.handlers.run(command_args)

    def _dispatch_inspect(self, spec: CommandSpec, args: list[str]) -> int:
        command_args = list(args)
        if "--searches-dir" not in command_args:
            command_args.extend(["--searches-dir", str(self.searches_dir)])
        return self.handlers.inspect(command_args)

    @staticmethod
    def _append_nondefault_path(
        command_args: list[str], option: str, value: Path, default: Path
    ) -> None:
        """Keep shell state and canonical handler paths identical when overridden."""

        if option not in command_args and value != default:
            command_args.extend([option, str(value)])

    def _animate_transition(self, label: str) -> None:
        if not self.animation_enabled:
            return
        with SignalSweep(self.console, enabled=True) as sweep:
            sweep.event(label)
            time.sleep(0.45)

    # -- presentation ----------------------------------------------------

    def _play_startup_animation(self) -> None:
        """UX-START-01/02: an immediate, domain-specific identity animation."""
        if not self.animation_enabled:
            return
        with Live(
            Text(""), console=self.console, refresh_per_second=20, transient=True
        ) as live:
            for frame in _STARTUP_FRAMES:
                live.update(
                    Text.assemble(
                        ("  ≋ ", "bold cyan"),
                        (frame, "bright_magenta"),
                        ("  resolving encoded noise", "dim cyan"),
                    )
                )
                time.sleep(0.07)
            for frame in _STARTUP_FRAMES[::-1][:3]:
                live.update(
                    Text.assemble(
                        ("  ≋ ", "bold cyan"),
                        (frame, "bright_cyan"),
                        ("  array synchronized", "dim cyan"),
                    )
                )
                time.sleep(0.07)

    def _print_banner(self) -> None:
        self.console.print(
            Text.assemble(
                ("≋ ", "bold cyan"),
                ("TechnoHunter", "bold bright_magenta"),
                (f" v{__version__}", "cyan"),
            )
        )
        self.console.print(SHELL_DISCLAIMER, style="dim")
        self.console.print("Type / for the command palette, or /Help.", style="cyan")

    def _print_help(self) -> None:
        state = self.state()
        table = Table(
            title="TechnoHunter commands", show_header=True, header_style="bold",
            expand=False,
        )
        table.add_column("Command", no_wrap=True, overflow="ellipsis", min_width=18)
        table.add_column("Purpose", overflow="fold")
        table.add_column("Required", no_wrap=True, overflow="ellipsis", max_width=14)
        table.add_column("Optional", no_wrap=True, overflow="ellipsis", max_width=14)
        for row in describe_palette(COMMANDS, state):
            table.add_row(row["name"], row["summary"], row["required"], row["optional"])
        self.console.print(table)
        self._print_keyboard_help()
        self._print_option_convention()
        # The no-claim guardrail is shown on every help surface, not only at
        # interactive startup, so a redirected or scripted operator sees it too.
        self.console.print(SHELL_DISCLAIMER, style="dim")

    def _print_keyboard_help(self) -> None:
        table = Table(
            title="Keyboard", show_header=False, box=None, pad_edge=False, expand=False
        )
        table.add_column("Key", no_wrap=True, style="bold", min_width=12)
        table.add_column("Action", overflow="fold")
        for key, action in KeyboardHelp().rows:
            table.add_row(key, action)
        self.console.print(table)

    # -- readline --------------------------------------------------------

    def complete(self, text: str, state: int) -> str | None:
        """Return readline completions for the required slash commands."""
        matches = [name + " " for name in completion_candidates(text)]
        return matches[state] if state < len(matches) else None

    def _configure_readline(self) -> None:
        if readline is None:
            return
        readline.set_completer(self.complete)
        readline.set_completer_delims(" \t\n")
        readline.parse_and_bind("tab: complete")
        if self.history_path.is_file():
            with suppress(OSError):
                readline.read_history_file(self.history_path)

    def _save_history(self) -> None:
        if readline is None:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            readline.set_history_length(500)
            readline.write_history_file(self.history_path)
        except OSError as exc:
            self._error(f"could not persist command history: {exc}")

    def _run_lines(self, lines: Sequence[str] | TextIO) -> int:
        exit_code = 0
        for line in lines:
            result = self.dispatch(line)
            exit_code = result.exit_code
            if result.exit_requested or exit_code != 0:
                break
        return exit_code

    def _error(self, message: str) -> None:
        print(f"ERROR: {message}", file=self.stderr)


class _KeyReader:
    """Read single keystrokes for the palette, degrading when unavailable."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self._settings: object | None = None
        try:
            import termios
            import tty
        except ImportError:  # pragma: no cover - non-POSIX platform
            self._termios = None
            self._tty = None
        else:
            self._termios = termios
            self._tty = tty

    @property
    def available(self) -> bool:
        if self._termios is None or self._tty is None:
            return False
        try:
            return bool(self.stream.isatty())
        except ValueError:  # pragma: no cover - closed stream
            return False

    def __enter__(self) -> _KeyReader:
        if self.available and self._termios is not None and self._tty is not None:
            self._settings = self._termios.tcgetattr(self.stream.fileno())
            self._tty.setraw(self.stream.fileno())
        return self

    def __exit__(self, *_args: object) -> None:
        # UX-A11Y: terminal state must be restored after cancellation or error.
        if self._settings is not None and self._termios is not None:
            self._termios.tcsetattr(
                self.stream.fileno(), self._termios.TCSADRAIN, self._settings
            )
            self._settings = None

    # A lone Escape and the start of an arrow sequence are the same first byte.
    # Real terminals send the remainder of an arrow sequence in the same burst,
    # so a short wait distinguishes them without stalling on a bare Escape.
    ESCAPE_SEQUENCE_TIMEOUT = 0.05

    def _read_char(self) -> str:
        """Read one character, bypassing text-layer buffering when possible."""
        try:
            fileno = self.stream.fileno()
        except (AttributeError, OSError, ValueError):
            return self.stream.read(1)
        data = os.read(fileno, 1)
        if not data:
            return ""
        # Continuation bytes complete a multi-byte character.
        while data[-1] >= 0x80 and len(data) < 4:
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                more = os.read(fileno, 1)
                if not more:
                    break
                data += more
        return data.decode("utf-8", errors="replace")

    def _more_input_ready(self, timeout: float) -> bool:
        try:
            readable, _, _ = select.select([self.stream.fileno()], [], [], timeout)
        except (AttributeError, OSError, ValueError):  # pragma: no cover
            return False
        return bool(readable)

    def read_key(self) -> str:
        char = self._read_char()
        if char == "\x1b":
            if not self._more_input_ready(self.ESCAPE_SEQUENCE_TIMEOUT):
                return "ESCAPE"
            if self._read_char() != "[":
                return "ESCAPE"
            if not self._more_input_ready(self.ESCAPE_SEQUENCE_TIMEOUT):
                return "ESCAPE"
            return {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}.get(
                self._read_char(), "ESCAPE"
            )
        if char in {"\r", "\n"}:
            return "ENTER"
        if char == "\t":
            return "TAB"
        if char in {"\x7f", "\b"}:
            return "BACKSPACE"
        if char == "\x03":
            return "CTRL_C"
        if char == "\x04":
            return "CTRL_D"
        if char == "":
            return "EOF"
        return char


#: Console scripts that both resolve to :func:`main`. They are two names for
#: one implementation, not two implementations.
ENTRY_POINT_NAMES = ("TechnoHunter", "Techno-Hunter")


def _program_name() -> str:
    """Report the entry point the operator actually invoked.

    Both console scripts run this same function, so hard-coding one of them
    made ``TechnoHunter --help`` print ``usage: Techno-Hunter``. Anything that
    is not a recognised entry point — a test harness, ``python -m`` — falls
    back to the canonical name so help output stays deterministic.
    """
    invoked = Path(sys.argv[0]).name if sys.argv else ""
    return invoked if invoked in ENTRY_POINT_NAMES else ENTRY_POINT_NAMES[0]


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Launch the persistent shell or execute scriptable slash commands."""
    parser = argparse.ArgumentParser(prog=_program_name())
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="Execute one slash command non-interactively (repeatable).",
    )
    parser.add_argument("--no-animation", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument(
        "--history-file",
        type=Path,
        default=Path("artifacts/techno_hunter_history"),
    )
    parser.add_argument(
        "--searches-dir", type=Path, default=Path("results/searches")
    )
    parser.add_argument("--scans-dir", type=Path, default=Path("results/scans"))
    parser.add_argument(
        "--priority-queue",
        type=Path,
        default=Path("data_selection/target_priority_queue.csv"),
    )
    parser.add_argument(
        "--acceptance-work-dir",
        type=Path,
        help=(
            "Run the fresh-state controlled PROD acceptance harness in this "
            "directory instead of opening the operator shell."
        ),
    )
    parser.add_argument(
        "--acceptance-evidence",
        type=Path,
        help="Write the portable controlled PROD acceptance evidence bundle here.",
    )
    args = parser.parse_args(argv)
    if bool(args.acceptance_work_dir) != bool(args.acceptance_evidence):
        parser.error(
            "--acceptance-work-dir and --acceptance-evidence must be supplied together"
        )
    if args.acceptance_work_dir is not None:
        from techno_search.hunter_acceptance import run_controlled_prod_acceptance

        return run_controlled_prod_acceptance(
            work_dir=args.acceptance_work_dir,
            evidence_path=args.acceptance_evidence,
            stdout=stdout,
            stderr=stderr,
        )
    shell = HunterShell(
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        no_animation=args.no_animation,
        no_color=args.no_color,
        history_path=args.history_file,
        searches_dir=args.searches_dir,
        scans_dir=args.scans_dir,
        priority_queue=args.priority_queue,
    )
    return shell.run(args.command)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
