#!/usr/bin/env bash
# Run the controlled Hunter PROD acceptance harness.
#
# This script exists so the sandbox exclusion can be narrow and auditable. It
# runs exactly one thing: the installed Hunter's controlled acceptance mode.
#
# Why an exclusion is needed:
#   hunter_acceptance._execute_controlled_acceptance starts a throwaway local
#   HTTP server to stand in for the archive:
#
#     ThreadingHTTPServer(("127.0.0.1", 0), ...)
#       -> socket.bind()  ->  PermissionError: [Errno 1] Operation not permitted
#
#   The sandbox denies bind(), so the harness cannot start inside it. That is
#   NOT EXECUTED, never a pass, per contract CLAIM-03.
#
# Deliberately accepts no arbitrary command and no arbitrary executable.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

WORK_DIR="${1:-artifacts/controlled_acceptance/work}"
EVIDENCE_PATH="${2:-artifacts/controlled_acceptance/evidence.json}"

HUNTER="$REPO_ROOT/.venv/bin/Techno-Hunter"
if [[ ! -x "$HUNTER" ]]; then
  echo "ERROR: the Hunter is not installed at $HUNTER" >&2
  exit 2
fi

# The harness requires an absent or empty work directory.
rm -rf "$WORK_DIR"
mkdir -p "$(dirname "$WORK_DIR")" "$(dirname "$EVIDENCE_PATH")"

echo "== controlled Hunter PROD acceptance =="
echo "repo:     $REPO_ROOT"
echo "commit:   $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "tree:     $(if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then echo dirty; else echo clean; fi)"
echo "work:     $WORK_DIR"
echo "evidence: $EVIDENCE_PATH"
echo

set +e
"$HUNTER" --acceptance-work-dir "$WORK_DIR" --acceptance-evidence "$EVIDENCE_PATH"
STATUS=$?
set -e

echo
echo "exit status: $STATUS"
exit "$STATUS"
