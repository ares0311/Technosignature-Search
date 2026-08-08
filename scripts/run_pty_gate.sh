#!/usr/bin/env bash
# Run the Phase 2 real-terminal operator gate.
#
# This script exists so the sandbox exclusion can be narrow and stable. It runs
# exactly one thing: prod-check's interactive_pty_palette check, which must
# allocate a pseudo-terminal. macOS Seatbelt denies the TIOCPTYGRANT ioctl that
# grantpt() needs, so the gate cannot execute inside the sandbox regardless of
# any sandbox.filesystem.* setting.
#
#   Verified failing step inside the sandbox:
#     posix_openpt(O_RDWR)  OK
#     grantpt(master)       EPERM   <-- ioctl, not a filesystem operation
#
# Deliberately does NOT accept an arbitrary command. Anything excluded from the
# sandbox should do one known thing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Usage:
#   run_pty_gate.sh [REPORT_PATH]            only the real-PTY check
#   run_pty_gate.sh --full [REPORT_PATH]     the whole PROD gate
#   run_pty_gate.sh --full --include-wheel [REPORT_PATH]
#
# Every mode invokes this repository's own prod-check and nothing else. The
# script accepts no arbitrary command, so the sandbox exclusion stays auditable.
MODE_ARGS=(--only interactive_pty_palette)
REPORT_PATH=""
FULL=0

STATE_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    --include-wheel) EXTRA_WHEEL=1 ;;
    --update-state) STATE_ARGS+=(--update-state) ;;
    --active-phase=*) STATE_ARGS+=(--active-phase "${arg#*=}") ;;
    --implementation-state=*) STATE_ARGS+=(--implementation-state "${arg#*=}") ;;
    -*) echo "ERROR: unsupported option: $arg" >&2; exit 2 ;;
    *) REPORT_PATH="$arg" ;;
  esac
done

if [[ "$FULL" == "1" ]]; then
  MODE_ARGS=()
  [[ "${EXTRA_WHEEL:-0}" == "1" ]] && MODE_ARGS=(--include-wheel)
  REPORT_PATH="${REPORT_PATH:-docs/evidence/prod_gates/prod_check_full.json}"
fi

REPORT_PATH="${REPORT_PATH:-docs/evidence/prod_gates/phase2_pty_gate.json}"
PROD_CHECK="$REPO_ROOT/.venv/bin/prod-check"

if [[ ! -x "$PROD_CHECK" ]]; then
  echo "ERROR: prod-check is not installed at $PROD_CHECK" >&2
  echo "Install with: UV_CACHE_DIR=.uv-cache uv pip install --python .venv/bin/python -e '.[dev,radio,science,ml,track_a,photometry]'" >&2
  exit 2
fi

mkdir -p "$(dirname "$REPORT_PATH")"

echo "== Phase 2 real-PTY operator gate =="
echo "repo:   $REPO_ROOT"
echo "commit: $(git rev-parse HEAD 2>/dev/null || echo unknown)"
echo "tree:   $(if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then echo dirty; else echo clean; fi)"
echo "report: $REPORT_PATH"
echo

set +e
"$PROD_CHECK" "${MODE_ARGS[@]}" "${STATE_ARGS[@]+"${STATE_ARGS[@]}"}" --report-path "$REPORT_PATH"
STATUS=$?
set -e

echo
echo "exit status: $STATUS"
exit "$STATUS"
