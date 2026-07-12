# Historical, non-normative comparison

These captures used an AVX2 Atomic-Stockfish candidate and the original Fairy
baseline reporting only SSE4.1/SSSE3/SSE2/POPCNT. They are retained to preserve
the search-block history, but their speed and Elo figures are ISA-confounded
and do not satisfy the final Hito 6 gate. The accepted comparison is under the
sibling `bmi2/` directory.
