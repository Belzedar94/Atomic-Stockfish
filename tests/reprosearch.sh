#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -n "${PYTHON:-}" ]]; then
    PYTHON_BIN="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
else
    PYTHON_BIN=python
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/reprosearch.py" \
    --engine "${EXE:-$REPO_ROOT/src/atomic-stockfish}" \
    --eval-file "${ATOMIC_NNUE_NET:-$REPO_ROOT/../atomic_run3b_e202_l05.nnue}" \
    "$@"
