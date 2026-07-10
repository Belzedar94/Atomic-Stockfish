#!/usr/bin/env bash
# Verify the Atomic-only UCI contract and record the normative Atomic perft suite.
#
# Usage:
#   tests/atomic.sh --protocol-only [path/to/engine]
#   tests/atomic.sh --perft [path/to/engine]
#
# The protocol-only gate is expected to pass before the Atomic rules port. The
# perft gate is intentionally strict and will fail until those rules are wired
# into Position and move generation.

set -u

MODE="${1:---protocol-only}"
if [[ "$MODE" == "--protocol-only" || "$MODE" == "--perft" ]]; then
    shift
else
    echo "usage: $0 [--protocol-only|--perft] [path/to/engine]" >&2
    exit 2
fi

ENGINE="${1:-./atomic-stockfish}"

if [[ ! -x "$ENGINE" ]]; then
    echo "Atomic test engine is not executable: $ENGINE" >&2
    exit 2
fi

fail() {
    echo "Atomic test failed: $*" >&2
    exit 1
}

protocol_output="$({
    printf 'uci\n'
    printf 'setoption name UCI_Variant value atomic\n'
    printf 'setoption name Use NNUE value false\n'
    printf 'isready\n'
    printf 'quit\n'
} | "$ENGINE")"

grep -Eq '^id name Atomic-Stockfish( |$)' <<<"$protocol_output" \
    || fail "missing Atomic-Stockfish UCI identity"
grep -Fxq 'id author the Stockfish developers (see AUTHORS file)' <<<"$protocol_output" \
    || fail "upstream Stockfish attribution changed"
grep -Fxq 'option name UCI_Variant type combo default atomic var atomic' <<<"$protocol_output" \
    || fail "UCI_Variant must be a single-value atomic combo"
[[ "$(grep -Ec '^option name UCI_Variant ' <<<"$protocol_output")" -eq 1 ]] \
    || fail "UCI_Variant must be advertised exactly once"
if grep -Eq '^option name (UCI_Elo|UCI_ShowWDL|UCI_LimitStrength)' <<<"$protocol_output"; then
    fail "orthodox Elo and WDL options must not be exposed"
fi
grep -Fxq 'option name SyzygyProbeLimit type spin default 6 min 0 max 6' <<<"$protocol_output" \
    || fail "Atomic Syzygy must be exposed with a six-piece limit"
grep -Fxq 'uciok' <<<"$protocol_output" || fail "missing uciok"
grep -Fxq 'readyok' <<<"$protocol_output" || fail "missing readyok"

terminal_eval_output="$({
    printf 'setoption name Use NNUE value false\n'
    printf 'position fen 8/8/8/8/8/8/8/K7 b - - 0 1\n'
    printf 'eval\n'
    printf 'isready\n'
    printf 'quit\n'
} | "$ENGINE")"
grep -Fq 'Final evaluation: none (Atomic terminal)' <<<"$terminal_eval_output" \
    || fail "terminal eval must not enter an orthodox two-king evaluator"
grep -Fxq 'readyok' <<<"$terminal_eval_output" \
    || fail "engine did not remain responsive after terminal eval"

echo "Atomic UCI protocol contract passed"

if [[ "$MODE" == "--protocol-only" ]]; then
    exit 0
fi

# variant|chess960|position|depth|expected nodes
PERFT_CASES=(
    'atomic|false|startpos|4|197326'
    'atomic|false|fen rn2kb1r/1pp1p2p/p2q1pp1/3P4/2P3b1/4PN2/PP3PPP/R2QKB1R b KQkq - 0 1|4|1434825'
    'atomic|false|fen rn1qkb1r/p5pp/2p5/3p4/N3P3/5P2/PPP4P/R1BQK3 w Qkq - 0 1|4|714499'
    'atomic|false|fen r4b1r/2kb1N2/p2Bpnp1/8/2Pp3p/1P1PPP2/P5PP/R3K2R b KQ - 0 1|2|148'
    'atomic|true|fen 8/8/8/8/8/8/2k5/rR4KR w KQ - 0 1|4|61401'
    'atomic|true|fen r3k1rR/5K2/8/8/8/8/8/8 b kq - 0 1|4|98729'
    'atomic|true|fen Rr2k1rR/3K4/3p4/8/8/8/7P/8 w kq - 0 1|4|241478'
    'atomic|true|fen 1R4kr/4K3/8/8/8/8/8/8 b k - 0 1|4|17915'
)

perft_failed=0
for case_data in "${PERFT_CASES[@]}"; do
    IFS='|' read -r variant chess960 position depth expected <<<"$case_data"

    output="$({
        printf 'uci\n'
        printf 'setoption name UCI_Variant value %s\n' "$variant"
        printf 'setoption name Use NNUE value false\n'
        printf 'setoption name UCI_Chess960 value %s\n' "$chess960"
        printf 'position %s\n' "$position"
        printf 'go perft %s\n' "$depth"
        printf 'quit\n'
    } | "$ENGINE")"

    if grep -Fxq "Nodes searched: $expected" <<<"$output"; then
        printf 'PASS perft depth=%s expected=%s chess960=%s\n' "$depth" "$expected" "$chess960"
    else
        printf 'FAIL perft depth=%s expected=%s chess960=%s\n' "$depth" "$expected" "$chess960" >&2
        perft_failed=1
    fi
done

if [[ "$perft_failed" -ne 0 ]]; then
    fail "one or more normative Atomic perft cases did not match"
fi

echo "Atomic perft contract passed"
