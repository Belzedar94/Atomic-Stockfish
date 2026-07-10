#!/usr/bin/env bash
# Atomic and Atomic960 move-generation gate.
#
# Usage:
#   tests/perft.sh [path/to/atomic-stockfish]
#
# The eight historical Fairy vectors are kept in atomic.sh. The focused Python
# contract adds explosions, Atomic check, en passant, promotions, castling
# rights, mate, stalemate, transitions, and terminal-result assertions.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENGINE="${1:-$REPO_ROOT/src/atomic-stockfish}"
if [[ -n "${PYTHON:-}" ]]; then
    PYTHON_BIN="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
else
    PYTHON_BIN=python
fi

if [[ ! -x "$ENGINE" ]]; then
    echo "Atomic perft engine is not executable: $ENGINE" >&2
    exit 2
fi

bash "$SCRIPT_DIR/atomic.sh" --perft "$ENGINE"
"$PYTHON_BIN" "$SCRIPT_DIR/atomic_rules.py" --candidate-only --candidate "$ENGINE"

echo "Atomic perft and rule-transition suite passed"
