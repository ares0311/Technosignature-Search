"""Width-aware Hunter result presentation.

``docs/CLI_UX_SPEC.md`` UX-TABLE-01 requires terminal-width detection, stable
column widths, intentional truncation with a visible marker, and no uncontrolled
multi-line wrapping. UX-TABLE-02 moves long scientific explanations into a
separate detail view, and UX-TABLE-03 exports large requests instead of flooding
the terminal.

This module renders only values it is given. It performs no selection, scoring,
execution, or persistence (CLI_UX_SPEC section 12).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from rich.console import Console
from rich.table import Table
from rich.text import Text

TABLE_PAGE_SIZE = 25
LARGE_REQUEST_THRESHOLD = 100
TRUNCATION_MARKER = "…"


def detect_terminal_width(default: int = 100) -> int:
    """Resolve the usable terminal width without guessing when it is declared."""
    declared = os.environ.get("COLUMNS")
    if declared and declared.isdigit():
        return max(40, int(declared))
    measured = shutil.get_terminal_size(fallback=(default, 24)).columns
    return max(40, measured)


def build_console(
    out: TextIO, *, color: bool = False, width: int | None = None
) -> Console:
    """Build a console pinned to the detected width so tables cannot overflow."""
    return Console(
        file=out,
        width=width or detect_terminal_width(),
        color_system="auto" if color else None,
        highlight=False,
        soft_wrap=False,
        emoji=False,
    )


def _cell(value: Any, *, dash: str = "unknown") -> str:
    if value is None or value == "":
        return dash
    return str(value)


def _score_of(target: Mapping[str, Any], mode: str) -> float:
    key = "follow_up_priority" if mode == "follow-up" else "target_selection_score"
    try:
        return float(target.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class _Column:
    """One candidate table column with a fitting priority."""

    header: str
    width: int
    priority: int
    justify: str = "left"


def fit_columns(available_width: int, columns: Sequence[_Column]) -> list[_Column]:
    """Choose the widest column set that fits, keeping the highest priorities.

    UX-TABLE-01 requires stable column widths and no uncontrolled wrapping, and
    it requires rank and identity to stay visible. Priority 0 columns are
    therefore always retained; lower-priority columns drop out as width shrinks.
    """
    ordered = sorted(columns, key=lambda column: (column.priority, columns.index(column)))
    chosen: list[_Column] = []
    # Each rendered column costs its content width plus one separator and two
    # padding cells; the table also draws one closing border character.
    used = 1
    for column in ordered:
        cost = column.width + 3
        if column.priority == 0 or used + cost <= available_width:
            chosen.append(column)
            used += cost
    return [column for column in columns if column in chosen]


_SELECTION_COLUMNS: tuple[_Column, ...] = (
    _Column("#", 4, 0, "right"),
    _Column("Target", 10, 0),
    _Column("Score", 9, 1, "right"),
    _Column("GB", 6, 2, "right"),
    _Column("Type", 10, 4),
    _Column("Dist ly", 7, 4, "right"),
    _Column("Spec", 6, 5),
    _Column("Prior", 5, 3, "right"),
    _Column("Execution", 18, 6),
)


def render_selection_table(
    manifest: Mapping[str, Any],
    *,
    console: Console,
    page: int = 1,
    page_size: int = TABLE_PAGE_SIZE,
) -> dict[str, Any]:
    """Render the decision-critical selection columns inside the terminal width.

    Long-form rationale is intentionally excluded; it belongs to
    ``/Inspect-Target`` per UX-TABLE-02.
    """
    mode = str(manifest.get("mode", "new"))
    targets = list(manifest.get("targets", []))
    total = len(targets)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    window = targets[start : start + page_size]

    columns = fit_columns(console.width, _SELECTION_COLUMNS)
    headers = [column.header for column in columns]
    table = Table(
        title=f"{mode} selection — {manifest.get('search_id', 'pending')}",
        show_header=True,
        header_style="bold",
        pad_edge=False,
        expand=False,
        width=console.width,
    )
    for column in columns:
        table.add_column(
            column.header,
            justify="right" if column.justify == "right" else "left",
            no_wrap=True,
            overflow="ellipsis",
            width=column.width,
        )

    for offset, target in enumerate(window, start=start + 1):
        distance = target.get("distance_light_years")
        available = {
            "#": str(offset),
            "Target": _cell(target.get("hip") or target.get("target_id")),
            "Type": _cell(target.get("object_type")),
            "Dist ly": f"{float(distance):.1f}" if distance is not None else "unknown",
            "Spec": _cell(target.get("spectral_type")),
            "Prior": _cell(target.get("prior_search_count", 0), dash="0"),
            "Score": f"{_score_of(target, mode):.6f}",
            "GB": f"{float(target.get('estimated_download_gb') or 0.0):.3f}",
            "Execution": _cell(target.get("execution_kind")),
        }
        table.add_row(*[available[header] for header in headers])

    console.print(table)
    console.print(
        Text(
            f"page {page} of {pages} — {total} frozen target(s). "
            f"Columns truncate with {TRUNCATION_MARKER}; "
            "use /Inspect-Target <rank-or-id> for full detail.",
            style="dim",
        )
    )
    return {"page": page, "pages": pages, "total": total, "rendered": len(window)}


_FOLLOW_UP_COLUMNS: tuple[_Column, ...] = (
    _Column("#", 4, 0, "right"),
    _Column("Target", 10, 0),
    _Column("Priority", 8, 1, "right"),
    _Column("Score", 7, 3, "right"),
    _Column("SNR", 6, 3, "right"),
    _Column("RFI", 3, 4),
    _Column("Next action", 34, 2),
)


def render_follow_up_table(
    registry: Mapping[str, Any], *, console: Console, page_size: int = TABLE_PAGE_SIZE
) -> dict[str, Any]:
    """Render actionable follow-ups within the terminal width."""
    entries = list(registry.get("eligible_entries", []))
    columns = fit_columns(console.width, _FOLLOW_UP_COLUMNS)
    headers = [column.header for column in columns]
    table = Table(
        title="Actionable follow-up targets",
        show_header=True,
        header_style="bold",
        pad_edge=False,
        expand=False,
        width=console.width,
    )
    for column in columns:
        table.add_column(
            column.header,
            justify="right" if column.justify == "right" else "left",
            no_wrap=True,
            overflow="ellipsis",
            width=column.width,
        )

    for index, entry in enumerate(entries[:page_size], start=1):
        evidence = entry.get("evidence", {})
        available = {
            "#": str(index),
            "Target": _cell(entry.get("hip")),
            "Priority": f"{float(entry.get('follow_up_priority') or 0.0):.3f}",
            "Score": f"{float(evidence.get('score') or 0.0):.3f}",
            "SNR": f"{float(evidence.get('snr') or 0.0):.2f}",
            "RFI": "yes" if evidence.get("cross_target_rfi_flagged") else "no",
            "Next action": _cell(entry.get("recommended_next_action")),
        }
        table.add_row(*[available[header] for header in headers])

    console.print(table)
    console.print(
        Text(
            f"{len(entries)} actionable follow-up target(s) from "
            f"{registry.get('source_ledger_count', 0)} durable ledger(s); "
            f"{registry.get('unresolved_identity_count', 0)} row(s) excluded for "
            "unresolved identity.",
            style="dim",
        )
    )
    return {"total": len(entries), "rendered": min(len(entries), page_size)}


def render_action_preview(preview: Mapping[str, Any], *, console: Console) -> None:
    """Render the resolved-action preview required before freezing a search."""
    table = Table(
        title="Resolved action preview",
        show_header=False,
        pad_edge=False,
        expand=False,
        box=None,
    )
    table.add_column("Field", no_wrap=True, style="bold", min_width=32)
    table.add_column("Value", overflow="fold")
    for label, key in (
        ("Mode:", "mode"),
        ("Requested targets:", "requested_targets"),
        ("Scientific constraints:", "scientific_constraints"),
        ("Primary sources:", "primary_sources"),
        ("Source freshness:", "source_freshness"),
        ("Cross-project history freshness:", "cross_project_history_freshness"),
        ("Estimated discovery universe:", "estimated_discovery_universe"),
        ("Estimated storage:", "estimated_storage"),
        ("Estimated compute:", "estimated_compute"),
        ("Output behavior:", "output_behavior"),
    ):
        table.add_row(label, _cell(preview.get(key), dash="not determined"))
    console.print(table)


def render_target_detail(detail: Mapping[str, Any], *, console: Console) -> None:
    """Render the separate detail view required by UX-TABLE-02."""
    identity = Table(show_header=False, box=None, pad_edge=False, expand=False)
    identity.add_column("Field", no_wrap=True, style="bold", min_width=26)
    identity.add_column("Value", overflow="fold")
    for label, key in (
        ("Canonical identity", "canonical_identity"),
        ("Aliases", "aliases"),
        ("Search ID", "search_id"),
        ("Rank", "rank"),
        ("Mode", "mode"),
        ("Execution kind", "execution_kind"),
        ("Selection reason", "selection_reason"),
    ):
        identity.add_row(label, _cell(detail.get(key), dash="unknown"))
    console.print(identity)

    metrics = detail.get("scientific_metrics") or {}
    if metrics:
        table = Table(
            title="Scientific metrics", show_header=True, box=None, expand=False
        )
        table.add_column("Metric", no_wrap=True, overflow="ellipsis", min_width=24)
        table.add_column("Value", justify="right", no_wrap=True)
        for name, value in metrics.items():
            table.add_row(str(name), _cell(value))
        console.print(table)

    components = detail.get("score_components") or {}
    if components:
        table = Table(title="Score components", show_header=True, box=None, expand=False)
        table.add_column("Component", no_wrap=True, overflow="ellipsis", min_width=24)
        table.add_column("Value", justify="right", no_wrap=True)
        for name, value in components.items():
            table.add_row(str(name), _cell(value))
        console.print(table)

    for heading, key in (
        ("Source and transformation provenance", "provenance"),
        ("Prior-search evidence", "prior_search_evidence"),
        ("Estimated resource requirements", "resource_requirements"),
        ("Limitations", "limitations"),
    ):
        values = detail.get(key)
        if not values:
            continue
        console.print(Text(heading, style="bold"))
        if isinstance(values, str):
            console.print(f"  {values}")
            continue
        for item in values:
            console.print(f"  - {item}")


def write_large_request_export(
    manifest: Mapping[str, Any], *, export_dir: Path
) -> Path:
    """Write the timestamped complete export required by UX-TABLE-03."""
    export_dir.mkdir(parents=True, exist_ok=True)
    search_id = str(manifest.get("search_id", "pending"))
    path = export_dir / f"{search_id}.csv"
    mode = str(manifest.get("mode", "new"))
    columns = (
        "rank",
        "target_id",
        "object_type",
        "distance_light_years",
        "spectral_type",
        "prior_search_count",
        "score",
        "estimated_download_gb",
        "execution_kind",
        "selection_reason",
    )
    lines = [",".join(columns)]
    for rank, target in enumerate(manifest.get("targets", []), start=1):
        row = (
            str(rank),
            str(target.get("hip") or target.get("target_id") or ""),
            str(target.get("object_type") or ""),
            str(target.get("distance_light_years") or ""),
            str(target.get("spectral_type") or ""),
            str(target.get("prior_search_count") or 0),
            f"{_score_of(target, mode):.6f}",
            str(target.get("estimated_download_gb") or 0.0),
            str(target.get("execution_kind") or ""),
            '"' + str(target.get("selection_reason") or "").replace('"', "'") + '"',
        )
        lines.append(",".join(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_PALETTE_COLUMNS: tuple[_Column, ...] = (
    _Column(" ", 1, 0),
    _Column("Command", 18, 0),
    _Column("Description", 34, 1),
    _Column("Required", 12, 2),
    _Column("Optional", 12, 2),
    _Column("Availability", 20, 3),
)


def render_palette(
    rows: Sequence[Mapping[str, str]],
    *,
    console: Console,
    query: str = "",
    selected_index: int = 0,
) -> None:
    """Render the searchable command palette (UX-CMD-02)."""
    title = "Command palette" + (f" — filter: {query}" if query else "")
    columns = fit_columns(console.width, _PALETTE_COLUMNS)
    headers = [column.header for column in columns]
    table = Table(
        title=title,
        show_header=True,
        header_style="bold",
        expand=False,
        width=console.width,
    )
    for column in columns:
        table.add_column(
            column.header,
            no_wrap=column.header != "Description",
            overflow="ellipsis" if column.header != "Description" else "fold",
            width=column.width,
        )
    for index, row in enumerate(rows):
        available = {
            " ": ">" if index == selected_index else " ",
            "Command": row["name"],
            "Description": row["summary"],
            "Required": row["required"],
            "Optional": row["optional"],
            "Availability": row["availability"],
        }
        table.add_row(*[available[header] for header in headers])
    console.print(table)


def render_guided_fields(
    command_name: str,
    fields: Sequence[Mapping[str, str]],
    *,
    console: Console,
    focus_index: int = 0,
) -> None:
    """Render the guided parameter editor (UX-IN-01)."""
    console.print(Text(command_name, style="bold"))
    table = Table(show_header=False, box=None, pad_edge=False, expand=False)
    table.add_column(" ", no_wrap=True, width=1)
    table.add_column("Field", no_wrap=True, style="bold", min_width=24)
    table.add_column("Value", no_wrap=True, max_width=18)
    table.add_column("Description", overflow="fold")
    for index, spec in enumerate(fields):
        marker = ">" if index == focus_index else " "
        label = spec["label"] + (" *" if spec.get("required") == "yes" else "")
        table.add_row(marker, label, spec["placeholder"], spec["description"])
    console.print(table)
    console.print(
        Text(
            "* required. Tab next field, Shift-Tab previous, Enter execute when "
            "valid, Escape cancel.",
            style="dim",
        )
    )
