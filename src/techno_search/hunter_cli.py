"""Polished shell entry points for the durable Hunter search lifecycle."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any, TextIO

from techno_search.hunter_adaptive_discovery import prepare_production_new_target_queue
from techno_search.hunter_follow_up_discovery import (
    FollowUpDiscoveryError,
    discover_follow_up_targets,
)
from techno_search.hunter_inspect import (
    TargetInspectionError,
    inspect_target,
    list_inspectable_targets,
)
from techno_search.hunter_search import (
    SearchApprovalRequired,
    SearchLifecycleError,
    create_search,
    follow_up_registry,
    run_search,
)
from techno_search.hunter_tables import (
    LARGE_REQUEST_THRESHOLD,
    build_console,
    render_action_preview,
    render_follow_up_table,
    render_selection_table,
    render_target_detail,
    write_large_request_export,
)
from techno_search.hunter_validation import (
    FieldValidationError,
    validate_constraints,
    validate_search_id,
    validate_target_count,
)


def create_new_search(
    argv: Sequence[str] | None = None,
    *,
    create_search_func: Callable[..., dict[str, Any]] | None = None,
    adaptive_discovery_func: (
        Callable[..., tuple[Path, dict[str, Any]]] | None
    ) = None,
    follow_up_discovery_func: (
        Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]] | None
    ) = None,
) -> int:
    create_search_impl = create_search_func or create_search
    adaptive_discovery_impl = (
        adaptive_discovery_func or prepare_production_new_target_queue
    )
    follow_up_discovery_impl = (
        follow_up_discovery_func or discover_follow_up_targets
    )
    parser = argparse.ArgumentParser(prog="Create-New-Search")
    parser.add_argument("--targets", type=int, required=True)
    parser.add_argument("--mode", choices=("new", "follow-up"), required=True)
    parser.add_argument(
        "--candidate-catalog",
        type=Path,
        default=Path("data_selection/bl_archive_candidate_catalog.csv"),
    )
    parser.add_argument(
        "--priority-queue",
        type=Path,
        default=Path("data_selection/target_priority_queue.csv"),
    )
    parser.add_argument("--scans-dir", type=Path, default=Path("results/scans"))
    parser.add_argument("--searches-dir", type=Path, default=Path("results/searches"))
    parser.add_argument(
        "--manifest-dir", type=Path, default=Path("results/search_manifests")
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-ra-deg", type=float)
    parser.add_argument("--max-ra-deg", type=float)
    parser.add_argument("--min-dec-deg", type=float)
    parser.add_argument("--max-dec-deg", type=float)
    parser.add_argument("--min-abs-galactic-latitude-deg", type=float)
    parser.add_argument("--max-estimated-download-gb", type=float)
    parser.add_argument(
        "--target-prefix",
        action="append",
        dest="target_prefixes",
        help="Restrict selection to one or more catalog-ID prefixes (repeatable).",
    )
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help=(
            "Show the resolved-action preview and exit without freezing a search."
        ),
    )
    args = parser.parse_args(argv)

    # Interactive and scriptable operation share these canonical validators so a
    # scripted run can never accept what guided entry would reject (UX-IN-04).
    try:
        target_count = validate_target_count(args.targets)
        constraints = validate_constraints(
            min_ra_deg=args.min_ra_deg,
            max_ra_deg=args.max_ra_deg,
            min_dec_deg=args.min_dec_deg,
            max_dec_deg=args.max_dec_deg,
            min_abs_galactic_latitude_deg=args.min_abs_galactic_latitude_deg,
            max_estimated_download_gb=args.max_estimated_download_gb,
            target_prefixes=args.target_prefixes or (),
        )
    except FieldValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.preview_only:
        preview = _build_action_preview(
            mode=args.mode,
            target_count=target_count,
            constraints=constraints,
            candidate_catalog=args.candidate_catalog,
            priority_queue=args.priority_queue,
        )
        if args.json:
            print(json.dumps(preview, indent=2, sort_keys=True))
        else:
            render_action_preview(preview, console=build_console(sys.stdout))
            print(
                "No search was frozen. Re-run without --preview-only to freeze, "
                "or adjust the constraints above."
            )
        return 0

    try:
        manifest = create_search_impl(
            target_count=target_count,
            mode=args.mode,
            candidate_catalog_path=args.candidate_catalog,
            queue_path=args.priority_queue,
            scans_dir=args.scans_dir,
            searches_dir=args.searches_dir,
            manifest_dir=args.manifest_dir,
            adaptive_discovery=(
                lambda queue_path, target_count, search_id, constraints: (
                    adaptive_discovery_impl(
                        queue_path,
                        target_count=target_count,
                        search_id=search_id,
                        constraints=constraints,
                    )
                )
            )
            if args.mode == "new"
            else None,
            follow_up_discovery=(
                lambda targets, target_count: follow_up_discovery_impl(
                    targets, target_count=target_count
                )
            )
            if args.mode == "follow-up"
            else None,
            constraints=constraints.as_dict(),
        )
    except (FollowUpDiscoveryError, SearchLifecycleError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        _print_created_search(manifest, args.searches_dir, args.manifest_dir, sys.stdout)
    return 0


def run_new_search(
    argv: Sequence[str] | None = None,
    *,
    run_search_func: Callable[..., dict[str, Any]] | None = None,
) -> int:
    run_search_impl = run_search_func or run_search
    parser = argparse.ArgumentParser(prog="Run-New-Search")
    parser.add_argument("--search-id")
    parser.add_argument("--searches-dir", type=Path, default=Path("results/searches"))
    parser.add_argument("--history-file", type=Path, default=Path("results/scan_history.ndjson"))
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument("--pipeline-workers", type=int, default=12)
    parser.add_argument("--no-rich", action="store_true")
    parser.add_argument(
        "--approve-acquisition",
        action="store_true",
        help="Approve the immutable manifest's bounded raw archive acquisition.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_search_impl(
            searches_dir=args.searches_dir,
            search_id=args.search_id,
            approve_acquisition=args.approve_acquisition,
            chunk_size=args.chunk_size,
            pipeline_workers=args.pipeline_workers,
            history_path=args.history_file,
            stdout=StringIO() if args.json else sys.stdout,
            use_rich=not args.no_rich and not args.json,
            command_runner=_quiet_command_runner if args.json else None,
        )
    except SearchApprovalRequired as exc:
        print(f"APPROVAL REQUIRED: {exc}", file=sys.stderr)
        return 2
    except (SearchLifecycleError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"Completed {result['search_id']} as {result['run_id']}: "
            f"{result['target_count']} target(s), "
            f"{result['follow_up_required_count']} follow-up target(s)."
        )
    return 0


def show_follow_ups(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Show-Follow-Ups")
    parser.add_argument("--scans-dir", type=Path, default=Path("results/scans"))
    parser.add_argument("--searches-dir", type=Path, default=Path("results/searches"))
    parser.add_argument(
        "--priority-queue",
        type=Path,
        default=Path("data_selection/target_priority_queue.csv"),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        registry = follow_up_registry(
            scans_dirs=(args.scans_dir, args.searches_dir),
            queue_path=args.priority_queue,
        )
    except SearchLifecycleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(registry, indent=2, sort_keys=True))
    else:
        _print_follow_ups(registry, sys.stdout)
    return 0


def _build_action_preview(
    *,
    mode: str,
    target_count: int,
    constraints: Any,
    candidate_catalog: Path,
    priority_queue: Path,
) -> dict[str, Any]:
    """Resolve the pre-freeze action preview from real on-disk evidence only.

    Every value is measured or explicitly reported as undetermined; nothing here
    is estimated from a guess (CLI_UX_SPEC section 8, UX-RUN-02).
    """

    def _freshness(path: Path) -> str:
        if not path.is_file():
            return f"absent: {path}"
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return f"{path.name} modified {stamp.isoformat(timespec='seconds')}"

    def _row_count(path: Path) -> int | None:
        if not path.is_file():
            return None
        with path.open(encoding="utf-8", newline="") as handle:
            return max(0, sum(1 for _ in handle) - 1)

    catalog_rows = _row_count(candidate_catalog)
    queue_rows = _row_count(priority_queue)
    sibling_export = Path("data_selection/hunter_prior_search_history_v1.json")
    universe = (
        f"{catalog_rows} archive label(s); {queue_rows} ranked queue row(s)"
        if catalog_rows is not None and queue_rows is not None
        else "not determined — candidate sources are absent"
    )
    return {
        "mode": mode,
        "requested_targets": target_count,
        "scientific_constraints": constraints.described(),
        "primary_sources": (
            f"{candidate_catalog} (candidate universe); {priority_queue} (eligibility)"
        ),
        "source_freshness": (
            f"{_freshness(candidate_catalog)}; {_freshness(priority_queue)}"
        ),
        "cross_project_history_freshness": _freshness(sibling_export),
        "estimated_discovery_universe": universe,
        "estimated_storage": (
            "determined at freeze from real HEAD-preflighted product sizes; "
            "bounded by the 100 GB local cap"
        ),
        "estimated_compute": (
            f"turboSETI plus radio pipeline per target, up to {target_count} target(s), "
            "12 bounded workers"
        ),
        "output_behavior": (
            "durable immutable manifest and append-only events; terminal table for "
            f"N <= {LARGE_REQUEST_THRESHOLD}, timestamped CSV export above it"
        ),
        "no_claim": (
            "Selection is local scientific triage only. No detection, discovery, "
            "expert-review, external-validation, or submission claim follows."
        ),
    }


def _print_created_search(
    manifest: dict[str, Any], searches_dir: Path, manifest_dir: Path, out: TextIO
) -> None:
    search_id = str(manifest["search_id"])
    targets = list(manifest["targets"])
    console = build_console(out)
    print(
        f"Created pending {manifest['mode']} search {search_id} with {len(targets)} target(s).",
        file=out,
    )
    print(f"Durable manifest: {searches_dir / search_id / 'manifest.json'}", file=out)
    print(
        f"Candidate universe: {manifest['candidate_catalog']['candidate_count']}; "
        f"eligible: {manifest['eligibility_queue']['viable_candidate_count']}; "
        f"projected acquisition: {manifest['selection']['projected_download_gb']:.3f} GB",
        file=out,
    )
    shortfall = manifest["selection"].get("shortfall")
    if shortfall:
        print(
            f"SHORTFALL: returned {shortfall['returned_count']} of "
            f"{shortfall['requested_count']} requested target(s) -- {shortfall['reason']}.",
            file=out,
        )
    if len(targets) > LARGE_REQUEST_THRESHOLD:
        export = write_large_request_export(manifest, export_dir=manifest_dir)
        print(
            f"{len(targets)} target(s) exceed the {LARGE_REQUEST_THRESHOLD}-row "
            f"terminal threshold; complete export written to {export}",
            file=out,
        )
        print(
            f"Durable non-CSV system of record: {searches_dir / search_id}", file=out
        )
        return
    render_selection_table(manifest, console=console)


def _print_follow_ups(registry: dict[str, Any], out: TextIO) -> None:
    if not registry.get("eligible_entries"):
        print(
            f"0 actionable follow-up target(s) from "
            f"{registry['source_ledger_count']} durable run ledger(s); "
            f"{registry['unresolved_identity_count']} row(s) excluded for "
            "unresolved identity.",
            file=out,
        )
        print("Follow-up targets: none", file=out)
        return
    render_follow_up_table(registry, console=build_console(out))


def inspect_target_command(argv: Sequence[str] | None = None) -> int:
    """Show full durable detail for one selected target (UX-TABLE-02)."""
    parser = argparse.ArgumentParser(prog="Inspect-Target")
    parser.add_argument(
        "reference",
        nargs="?",
        help="Rank number from the last selection table, or a catalog identity.",
    )
    parser.add_argument("--search-id")
    parser.add_argument("--searches-dir", type=Path, default=Path("results/searches"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        search_id = validate_search_id(args.search_id)
    except FieldValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        if args.reference is None:
            # With no reference, list the frozen selection so the operator can
            # choose a rank. This reuses the same width-aware table.
            manifest = list_inspectable_targets(
                searches_dir=args.searches_dir, search_id=search_id
            )
            if args.json:
                print(json.dumps(manifest, indent=2, sort_keys=True))
                return 0
            console = build_console(sys.stdout)
            render_selection_table(manifest, console=console)
            # Routed through the width-aware console so it cannot exceed the
            # detected terminal width (UX-TABLE-01).
            console.print(
                "Pass a rank number or catalog identity to inspect one target, "
                "for example: /Inspect-Target 1"
            )
            return 0
        detail = inspect_target(
            searches_dir=args.searches_dir,
            reference=args.reference,
            search_id=search_id,
        )
    except TargetInspectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(detail, indent=2, sort_keys=True))
    else:
        render_target_detail(detail, console=build_console(sys.stdout))
    return 0


def _quiet_command_runner(command: Sequence[str]) -> int:
    """Keep a machine-readable run clean; the batch runner writes its own log."""
    completed = subprocess.run(
        list(command),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode
