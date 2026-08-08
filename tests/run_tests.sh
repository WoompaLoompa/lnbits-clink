#!/usr/bin/env bash
# Run the test suite.
#
# The repo basename `lnbits-clink` is not a valid Python identifier, so pytest
# cannot import the extension from the repo root directly. LNbits installs the
# extension into its `extensions/clink` folder; this script reproduces that by
# symlinking the repo into a temp dir named `clink` and running pytest from
# there.
#
# Usage: tests/run_tests.sh [pytest-args...]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ln -s "$REPO_ROOT" "$WORK/clink"
cd "$WORK"

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  exec "$REPO_ROOT/.venv/bin/python" -m pytest clink/tests "$@"
else
  exec python3 -m pytest clink/tests "$@"
fi
