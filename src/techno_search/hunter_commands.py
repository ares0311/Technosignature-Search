"""Single source of truth for the Hunter slash-command surface.

``docs/CLI_UX_SPEC.md`` requires the palette (UX-CMD-02), guided parameter entry
(UX-IN-01), and help to agree on every command's description, required
parameters, optional parameters, and state-dependent availability. Deriving all
three from this registry is what keeps them from drifting apart.

This module is presentation metadata only. It contains no candidate selection,
scoring, execution, or persistence logic (CLI_UX_SPEC section 12).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FieldSpec:
    """One guided input field."""

    name: str
    label: str
    description: str
    required: bool = False
    default: str | None = None
    choices: tuple[str, ...] = ()
    advanced: bool = False
    cli_flag: str | None = None

    def placeholder(self) -> str:
        if self.choices:
            return f"[{'|'.join(self.choices)}]"
        if self.default is not None:
            return f"[{self.default}]"
        return "[required]" if self.required else "[optional]"


@dataclass(frozen=True)
class CommandSpec:
    """One slash command's operator-facing contract."""

    name: str
    summary: str
    fields: tuple[FieldSpec, ...] = ()
    aliases: tuple[str, ...] = ()
    availability: Callable[[ShellState], str | None] | None = None

    @property
    def required_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.fields if spec.required)

    @property
    def optional_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.fields if not spec.required)

    def required_label(self) -> str:
        names = [spec.label.lower() for spec in self.required_fields]
        return ", ".join(names) if names else "none"

    def optional_label(self) -> str:
        names = [spec.label.lower() for spec in self.optional_fields]
        return ", ".join(names) if names else "none"

    def unavailable_reason(self, state: ShellState) -> str | None:
        if self.availability is None:
            return None
        return self.availability(state)


@dataclass(frozen=True)
class ShellState:
    """Real durable state the palette uses for availability, never invented."""

    searches_dir: Path
    scans_dir: Path
    priority_queue: Path
    pending_search_count: int = 0
    last_selection_count: int = 0

    @classmethod
    def observe(
        cls,
        *,
        searches_dir: Path,
        scans_dir: Path,
        priority_queue: Path,
        last_selection_count: int = 0,
    ) -> ShellState:
        """Read only real on-disk state; never fabricate a count."""
        pending = 0
        if searches_dir.is_dir():
            for manifest in searches_dir.glob("SEARCH-*/manifest.json"):
                events = manifest.parent / "events.ndjson"
                if not events.is_file():
                    pending += 1
                    continue
                text = events.read_text(encoding="utf-8", errors="replace")
                if "run_completed" not in text:
                    pending += 1
        return cls(
            searches_dir=searches_dir,
            scans_dir=scans_dir,
            priority_queue=priority_queue,
            pending_search_count=pending,
            last_selection_count=last_selection_count,
        )


def _require_pending_search(state: ShellState) -> str | None:
    if state.pending_search_count == 0:
        return "no pending search — create one with /New-Search or /Follow-Up-Search"
    return None


def _require_priority_queue(state: ShellState) -> str | None:
    if not state.priority_queue.is_file():
        return f"candidate queue is absent: {state.priority_queue}"
    return None


_TARGETS_FIELD = FieldSpec(
    name="targets",
    label="Targets",
    description="How many targets to select and freeze. Positive whole number.",
    required=True,
    default="20",
    cli_flag="--targets",
)

_CONSTRAINT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="min_ra_deg",
        label="Minimum RA (deg)",
        description="Lower right-ascension bound, 0-360 degrees.",
        advanced=True,
        cli_flag="--min-ra-deg",
    ),
    FieldSpec(
        name="max_ra_deg",
        label="Maximum RA (deg)",
        description="Upper right-ascension bound, 0-360 degrees.",
        advanced=True,
        cli_flag="--max-ra-deg",
    ),
    FieldSpec(
        name="min_dec_deg",
        label="Minimum declination (deg)",
        description="Lower declination bound, -90 to 90 degrees.",
        advanced=True,
        cli_flag="--min-dec-deg",
    ),
    FieldSpec(
        name="max_dec_deg",
        label="Maximum declination (deg)",
        description="Upper declination bound, -90 to 90 degrees.",
        advanced=True,
        cli_flag="--max-dec-deg",
    ),
    FieldSpec(
        name="min_abs_galactic_latitude_deg",
        label="Minimum |Galactic latitude| (deg)",
        description="Avoid Galactic-plane source confusion. 0-90 degrees.",
        advanced=True,
        cli_flag="--min-abs-galactic-latitude-deg",
    ),
    FieldSpec(
        name="max_estimated_download_gb",
        label="Maximum download per target (GB)",
        description="Skip candidates whose preflighted product exceeds this size.",
        advanced=True,
        cli_flag="--max-estimated-download-gb",
    ),
    FieldSpec(
        name="target_prefixes",
        label="Catalog prefixes",
        description="Restrict to catalog-ID prefixes, for example HIP or TIC.",
        advanced=True,
        cli_flag="--target-prefix",
    ),
)

COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        name="/New-Search",
        summary=(
            "Select and freeze the best available never-before-searched targets."
        ),
        fields=(_TARGETS_FIELD, *_CONSTRAINT_FIELDS),
        availability=_require_priority_queue,
    ),
    CommandSpec(
        name="/Follow-Up-Search",
        summary=(
            "Select and freeze the previously searched targets with the highest "
            "current scientific value."
        ),
        fields=(_TARGETS_FIELD, *_CONSTRAINT_FIELDS),
        availability=_require_priority_queue,
    ),
    CommandSpec(
        name="/Run-Search",
        summary="Execute the exact frozen targets of a pending search.",
        fields=(
            FieldSpec(
                name="search_id",
                label="Search ID",
                description="Durable SEARCH-* identifier. Defaults to the pending search.",
                cli_flag="--search-id",
            ),
            FieldSpec(
                name="approve_acquisition",
                label="Approve acquisition",
                description="Authorize the manifest's bounded raw archive download.",
                choices=("no", "yes"),
                default="no",
                cli_flag="--approve-acquisition",
            ),
        ),
        availability=_require_pending_search,
    ),
    CommandSpec(
        name="/Show-Follow-Ups",
        summary="List actionable durable follow-up targets and their next action.",
        fields=(
            FieldSpec(
                name="json",
                label="Machine-readable output",
                description="Emit the follow-up registry as JSON.",
                choices=("no", "yes"),
                default="no",
                cli_flag="--json",
            ),
        ),
    ),
    CommandSpec(
        name="/Inspect-Target",
        summary=(
            "Show full scientific detail, score components, provenance and "
            "limitations for one selected target."
        ),
        fields=(
            FieldSpec(
                name="reference",
                label="Rank or identity",
                description="A rank number from the last table, or a catalog identity.",
                cli_flag=None,
            ),
            FieldSpec(
                name="search_id",
                label="Search ID",
                description="Durable SEARCH-* identifier to inspect.",
                cli_flag="--search-id",
            ),
        ),
    ),
    CommandSpec(
        name="/Help",
        summary="Show the described command table and keyboard help.",
    ),
    CommandSpec(
        name="/Exit",
        summary="Leave the shell without changing durable search state.",
    ),
)

#: Compatibility aliases (CLI_UX_SPEC section 2). The display casing mirrors the
#: installed console scripts — ``Create-New-Search``, ``Run-New-Search`` — so
#: completion offers the same spelling the operator sees on their PATH. Typed
#: input is still matched case-insensitively via :data:`_ALIAS_LOOKUP`.
CANONICAL_ALIASES: dict[str, str] = {
    "/Create-New-Search": "/New-Search",
    "/Run-New-Search": "/Run-Search",
    "/Quit": "/Exit",
}

_ALIAS_LOOKUP: dict[str, str] = {
    alias.casefold(): canonical for alias, canonical in CANONICAL_ALIASES.items()
}

REQUIRED_COMMAND_NAMES: tuple[str, ...] = tuple(spec.name for spec in COMMANDS)


def command_by_name(name: str) -> CommandSpec | None:
    """Resolve a typed token to its canonical command specification."""
    token = name.strip().casefold()
    if not token.startswith("/"):
        token = "/" + token
    canonical = _ALIAS_LOOKUP.get(token)
    if canonical is not None:
        token = canonical.casefold()
    for spec in COMMANDS:
        if spec.name.casefold() == token or token in {
            alias.casefold() for alias in spec.aliases
        }:
            return spec
    return None


def filter_commands(query: str, state: ShellState | None = None) -> list[CommandSpec]:
    """Live-filter the palette by substring, preserving canonical order."""
    needle = query.strip().lstrip("/").casefold()
    if not needle:
        return list(COMMANDS)
    scored: list[tuple[int, CommandSpec]] = []
    for spec in COMMANDS:
        haystack = spec.name.lstrip("/").casefold()
        if haystack.startswith(needle):
            scored.append((0, spec))
        elif needle in haystack:
            scored.append((1, spec))
        elif needle in spec.summary.casefold():
            scored.append((2, spec))
    scored.sort(key=lambda item: (item[0], COMMANDS.index(item[1])))
    return [spec for _, spec in scored]


def completion_candidates(prefix: str) -> list[str]:
    """Tab-completion candidates for the required commands and canonical aliases."""
    token = prefix.casefold()
    names = [spec.name for spec in COMMANDS] + sorted(CANONICAL_ALIASES)
    return [name for name in names if name.casefold().startswith(token)]


@dataclass(frozen=True)
class KeyboardHelp:
    """Discoverable keyboard help for the palette and guided editor."""

    rows: tuple[tuple[str, str], ...] = field(
        default=(
            ("/", "open the searchable command palette"),
            ("type", "filter the palette live"),
            ("Up / Down", "move the palette selection"),
            ("Enter", "select the command, or execute when every field is valid"),
            ("Tab", "complete a command name, or move to the next field"),
            ("Shift-Tab", "move to the previous field"),
            ("Escape", "close the palette or cancel the guided editor"),
            ("Ctrl-C", "cancel without changing durable search state"),
        )
    )


def build_cli_arguments(spec: CommandSpec, values: dict[str, str]) -> list[str]:
    """Translate guided field values into the canonical scriptable arguments.

    Guided entry must not become a second parameter dialect, so every field maps
    onto the flag its one-shot executable already accepts.
    """
    arguments: list[str] = []
    for field_spec in spec.fields:
        raw = values.get(field_spec.name)
        if raw is None or not str(raw).strip():
            continue
        text = str(raw).strip()
        if field_spec.choices == ("no", "yes"):
            if text.casefold() in {"yes", "y", "true", "1"} and field_spec.cli_flag:
                arguments.append(field_spec.cli_flag)
            continue
        if field_spec.cli_flag is None:
            arguments.append(text)
            continue
        if field_spec.name == "target_prefixes":
            for prefix in text.replace(",", " ").split():
                arguments.extend([field_spec.cli_flag, prefix])
            continue
        arguments.extend([field_spec.cli_flag, text])
    return arguments


def describe_palette(
    specs: Sequence[CommandSpec], state: ShellState | None = None
) -> list[dict[str, str]]:
    """Render palette rows for both the interactive and non-TTY surfaces."""
    rows: list[dict[str, str]] = []
    for spec in specs:
        reason = spec.unavailable_reason(state) if state is not None else None
        rows.append(
            {
                "name": spec.name,
                "summary": spec.summary,
                "required": spec.required_label(),
                "optional": spec.optional_label(),
                "availability": "unavailable — " + reason if reason else "available",
            }
        )
    return rows
