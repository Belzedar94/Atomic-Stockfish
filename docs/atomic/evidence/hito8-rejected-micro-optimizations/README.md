# Rejected H8 micro-optimizations

These experiments were correct but did not meet Atomic-Stockfish's
performance-first acceptance rule. Neither change is present in H8.4b or
`main`.

## H8.3c compact castling-rights mask

Commit `1e05a34e9d648e9b43582840bd5cc647234d5634` changed
`castlingRightsMask[64]` from `int` to `u8`. It reduced `Position` from 664 to
472 bytes, passed release/debug correctness, exact signature, frozen-Fairy,
generator, and binding gates, but enlarged the executable by 990 bytes.

The clean 25-sample-per-side A/B measured median 1,394,263 NPS for the
candidate and 1,400,266 for H8.3b: ratio `0.995713` (-0.429%). Three batches
favored the control and two tied; none favored the candidate. `Position` is
approximately one object per worker rather than one per search ply, so the
layout saving did not justify the observed hot-path cost. The experiment was
rejected without a PR.

## H8.4a remove redundant-looking DirtyPiece zeroing

Commit `dbfafdaf` removed `target.dirtyPiece = {}` from accumulator-stack
push. It passed units, perft, protocols, Python, one million incremental
operations, the 10,000-position differential, and all seven generator
fixtures. Its executable was 4,257,136 bytes, SHA-256
`03B4C64C64985B76911B835CFDC71C2C2228E28093F9475F2B3F0B3BB8F21016`,
512 bytes smaller than H8.3b.

The clean A/B measured median 1,400,266 NPS for the candidate and 1,403,287
for H8.3b: ratio `0.997847` (-0.215%). Four batches favored the control and
one the candidate. Safety and size reduction alone were not enough for a
hot-path performance change, so the experiment was rejected without a PR.

Both decisions were independently audited. They remain useful negative
evidence and should not be silently reintroduced or bundled with another
optimization.
