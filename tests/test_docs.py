import csv
import json
import re
import tomllib
from pathlib import Path


def test_cli_usage_references_existing_example_paths() -> None:
    doc = Path("docs/CLI_USAGE.md").read_text(encoding="utf-8")

    assert ".venv/bin/techno-search score examples/candidates/radio_clean_candidate.json" in doc
    assert ".venv/bin/techno-search score-batch" in doc
    assert Path("examples/candidates/radio_clean_candidate.json").exists()
    assert Path("examples/reports/example-radio-clean.md").exists()
    assert Path("examples/reports/example-radio-clean.json").exists()
    assert Path("examples/reports/example-radio-clean.manifest.json").exists()


def test_readme_references_current_authoritative_docs() -> None:
    """Every governing document the README cites must exist.

    The pre-README_SPEC list named the retired freeform sections' companions.
    docs/README_SPEC.md now governs the structure, so this asserts the
    governing artifacts that structure actually depends on.
    """
    readme = Path("README.md").read_text(encoding="utf-8")

    linked_paths = (
        "AGENTS.md",
        "docs/PRODUCTION_READINESS.md",
        "docs/SYSTEMATIC_SEARCH_PLAN.md",
        "docs/HUNTER_PROD_CONTRACT.md",
        "docs/CLI_UX_SPEC.md",
    )
    for path in linked_paths:
        assert path in readme, f"README must cite {path}"
        assert Path(path).exists(), f"cited path {path} does not exist"


def test_readme_follows_the_governing_readme_spec_structure() -> None:
    """README-01: the required headings occur once each, in spec order.

    docs/README_SPEC.md is the governing artifact for README structure and
    outranks the earlier freeform section list (contract WS-04).
    """
    readme = Path("README.md").read_text(encoding="utf-8")

    required_headings = (
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
    for heading in required_headings:
        assert readme.count(heading) == 1, f"{heading!r} must appear exactly once"

    positions = [readme.index(heading) for heading in required_headings]
    assert positions == sorted(positions), "headings must follow README_SPEC order"

    # README-03: only the permitted status vocabulary.
    for forbidden in ("Planned", "Partial", "roadmap", "backlog", "future work"):
        assert forbidden not in readme, f"forbidden planning term: {forbidden}"


def test_readme_states_current_scientific_boundaries() -> None:
    """Claims that must survive any restructuring, per the project directives."""
    readme = Path("README.md").read_text(encoding="utf-8")

    for claim in ("fail-closed", "100 GB", "positive technosignature"):
        assert claim in readme

    retired_claims = (
        "Citizen-Science Production Deployment Readiness",
        "Learned real-label scoring model",
        "Local citizen-science production promotion is allowed",
        "124 real cadence evidence groups labeled",
        "review labels, consensus, and exports",
        "sqlite-operational-log-adapter",
        "candidate-extraction-handoff-summary",
        "benchmark-run-append",
    )
    for claim in retired_claims:
        assert claim not in readme


def test_readme_names_both_sibling_repositories() -> None:
    """README_SPEC section 4 requires naming all three siblings."""
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "2026 Exoplanet Research" in readme
    assert "2026 Near Earth Objects" in readme


def test_readme_documents_the_installed_hunter_lifecycle() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "uv pip install --python .venv/bin/python" in readme
    for command in (
        "TechnoHunter",
        "Techno-Hunter",
        "Create-New-Search",
        "Run-New-Search",
        "Show-Follow-Ups",
    ):
        assert f"{command} =" in pyproject
        assert f".venv/bin/{command}" in readme

    required_contract = (
        # The scriptable surface is the installed executables; the retired
        # "/Create-New-Search --targets ..." slash spelling is not the contract.
        "Create-New-Search --targets",
        "--mode new",
        "--mode follow-up",
        "Run-New-Search --approve-acquisition",
        "/New-Search <N>",
        "/Follow-Up-Search <N>",
        "/Run-Search",
        "/Show-Follow-Ups",
        "/Help",
        "/Exit",
        "Creation performs selection only",
        # DUR-02 exact-target execution, DUR-04 resumable failure semantics.
        # Asserted on the durable property, not one fixed sentence.
        "never regenerates, substitutes, or",
        "resumes under the same",
        "Re-running an already-completed search exits non-zero",
        "results/searches/SEARCH-*/manifest.json",
        "results/searches/SEARCH-*/events.ndjson",
        "results/scan_history.ndjson",
        "--approve-acquisition",
        "--json",
    )
    for claim in required_contract:
        assert claim in readme


def test_readme_shell_examples_start_by_syncing_main() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    bash_blocks = re.findall(r"```bash\n(.*?)```", readme, flags=re.DOTALL)

    assert bash_blocks
    assert all(block.startswith("git pull origin main\n") for block in bash_blocks)


def test_readme_version_badge_matches_the_package() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert f"version-{pyproject['project']['version']}-blue" in readme


def test_readme_candidate_inventory_matches_the_committed_catalogues() -> None:
    """Inventory counts quoted in the README must match the real CSVs."""
    readme = Path("README.md").read_text(encoding="utf-8")
    with Path("data_selection/bl_archive_candidate_catalog.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        catalog = list(csv.DictReader(handle))

    assert f"{len(catalog):,} archive labels" in readme


def test_readme_makes_no_unearned_prod_claim() -> None:
    """CLAIM-04 and the status authority: PROD comes from the gate, not prose.

    The README previously carried a `Hunter workflow-PROD-green` badge and the
    sentence "standalone Hunter workflow is PROD". Only a zero-exit
    `prod-check` run may assert PROD, so a static badge cannot.
    """
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Hunter%20workflow-PROD-green" not in readme
    assert "standalone Hunter workflow is PROD" not in readme
    # README_SPEC permits only its own status vocabulary.
    assert "Hunter%20workflow-Implemented" in readme


def test_publishing_docs_reference_current_validation_commands() -> None:
    doc = Path("docs/PUBLISHING.md").read_text(encoding="utf-8")

    assert "git push origin main" in doc
    assert "caffeinate -i .venv/bin/python scripts/run_parallel_validation.py" in doc
    assert "git diff --check" in doc


def test_dataset_brief_is_wired_into_authoritative_docs() -> None:
    required_docs = (
        "AGENTS.md",
        "CLAUDE.md",
        "docs/PRODUCTION_READINESS.md",
        "docs/PRODUCTION_SCAN_RUNBOOK.md",
        "docs/PROJECT_STATUS.md",
        "docs/ROADMAP.md",
    )

    assert Path("docs/technosignature_datasets_agent_brief.md").exists()
    for doc_path in required_docs:
        doc = Path(doc_path).read_text(encoding="utf-8")
        assert "docs/technosignature_datasets_agent_brief.md" in doc
        assert "Track A" in doc
        assert "unknown_candidate" in doc or "`unknown`" in doc


def test_project_status_tracks_integrated_known_explanation_progress() -> None:
    status = Path("docs/PROJECT_STATUS.md").read_text(encoding="utf-8")

    assert "emits exactly `known`, `unknown`, or `unresolved`" in status
    assert "Anomaly/OOD scores are ranking evidence only" in status
    assert "cadence-complete installed-Hunter run" in status
    assert "receives a durable adversarial dossier" in status
    assert "Not started — brief is merged locally" not in status


def test_track_a_htru2_feature_schema_is_committed() -> None:
    schema_path = Path("schemas/track_a_htru2_schema.json")

    assert schema_path.exists()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["schema_version"] == "track_a_htru2_baseline_v1"
    assert schema["label_column"] == "class"
    assert len(schema["feature_columns"]) == 8


def test_background_scheduler_templates_use_ignored_artifact_paths() -> None:
    cron = Path("docs/templates/background-search.cron").read_text(encoding="utf-8")
    launchd = Path("docs/templates/background-search.launchd.plist").read_text(
        encoding="utf-8"
    )

    for template in (cron, launchd):
        assert ".venv/bin/techno-search background-run-once" in template
        assert "artifacts/background_search_ledger.json" in template
        assert "artifacts/background_reviewed_log.json" in template
        assert "artifacts/background_needs_follow_up_log.json" in template
        assert "logs/techno_search.sqlite3" in template
        assert "--sqlite-log-path" in template
        assert "--acknowledge-local-run" in template
        assert "TECHNO_SEARCH_ENABLE_LIVE_DATA" not in template


def test_ci_template_stays_non_networked_and_outside_workflows() -> None:
    ci_doc = Path("docs/CI.md").read_text(encoding="utf-8")
    template = Path("docs/templates/ci.yml").read_text(encoding="utf-8")
    launcher = Path("scripts/run_parallel_validation.py").read_text(encoding="utf-8")

    assert Path("docs/templates/ci.yml").exists()
    _ = Path(".github/workflows/ci.yml").exists()
    assert "workflow` scope" in ci_doc
    assert "TECHNO_SEARCH_ENABLE_LIVE_DATA=0" in ci_doc
    assert 'TECHNO_SEARCH_ENABLE_LIVE_DATA: "0"' in template
    assert 'python scripts/run_parallel_validation.py -- -m "not integration_live"' in template
    assert "git diff --check" in template
    assert '"validate-all"' in launcher
    assert "techno-search health" in template


def test_cli_docs_include_draft_report_and_decision_workflows() -> None:
    doc = Path("docs/CLI_USAGE.md").read_text(encoding="utf-8")

    assert ".venv/bin/techno-search draft-follow-up-report-write" in doc
    assert ".venv/bin/techno-search validate-draft-reports" in doc
    assert ".venv/bin/techno-search user-decision-record" in doc
    assert ".venv/bin/techno-search init-logs" in doc
    assert ".venv/bin/techno-search sqlite-log-bootstrap-summary" in doc
    assert ".venv/bin/techno-search sqlite-log-summary" in doc
    assert ".venv/bin/techno-search sqlite-log-integrity-summary" in doc
    assert ".venv/bin/techno-search sqlite-recent-runs" in doc
    assert ".venv/bin/techno-search sqlite-needs-follow-up" in doc
    assert ".venv/bin/techno-search sqlite-log-export" in doc
    assert ".venv/bin/techno-search sqlite-migration-summary" in doc
    assert ".venv/bin/techno-search sqlite-log-pragmas" in doc
    assert ".venv/bin/techno-search sqlite-log-backup" in doc
    assert ".venv/bin/techno-search sqlite-log-retention-summary" in doc
    assert ".venv/bin/techno-search sqlite-log-vacuum" in doc
    assert ".venv/bin/techno-search sqlite-log-commit-guard" in doc
    assert ".venv/bin/techno-search validate-sqlite-logs" in doc
    assert ".venv/bin/techno-search validate-input" in doc
    assert ".venv/bin/techno-search run-pipeline" in doc
    assert ".venv/bin/techno-search rfi-database-summary" in doc
    assert ".venv/bin/techno-search rfi-database-admission-summary" in doc
    assert ".venv/bin/techno-search curated-dataset-admission-summary" in doc
    assert ".venv/bin/techno-search sqlite-log-consistency-summary" in doc
    assert ".venv/bin/techno-search project-status-consistency-summary" in doc
    assert ".venv/bin/techno-search mcp-server-policy-summary" in doc
    assert ".venv/bin/techno-search scheduler-dry-run" in doc
    assert "--sqlite-log-path" in doc
    assert "--confirm-external-submission-approval" not in doc
    assert "request_more_tests` and `close_as_reviewed` are the only" in doc
