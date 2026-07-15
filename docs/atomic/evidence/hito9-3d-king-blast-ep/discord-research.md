# H9.3d Fairy-Stockfish community research

This is supporting design evidence, not the normative rule source. The frozen
schema, Fairy-Stockfish behavior, differential oracles, perft and strength gates
remain authoritative. The messages below were inspected in the local read-only
Discord export under server `779317816897699850`; no bot credential is required
or recorded here.

The requested sibling `fairy-vault` database was checked first and contained
zero messages, channels, threads and embeddings and no raw export. The useful
historical source was the separate sibling archive
`vault/raw/discord/779317816897699850` (35 JSONL files; 26,359,853 bytes). This
provenance note prevents an empty index from being mistaken for a negative
search result.

## Incremental NNUE and explosion state

- `channel_791249497090686987.jsonl:181`, message
  `801571930120388668`, 2021-01-20, ubdip: Atomic NNUE has two unusual
  integration problems, the king/commoner mapping and explosion-driven
  incremental inputs.
- `channel_784418118503235625.jsonl:468` and `:470`, messages
  `817438294178136104` and `817441651173359666`, 2021-03-05, tttak/ubdip:
  the feature test found an active-versus-changed-index discrepancy in Atomic,
  plausibly involving the substituted NNUE king representation.
- `channel_966610323987660830.jsonl:1869`, message
  `1096379475693944832`, 2023-04-14, ubdip: disabling incremental updates was
  recommended as a bug-isolation check, and Atomic was identified as the most
  problematic variant for incremental updates.
- `channel_779317816897699854.jsonl:1533`, message
  `806944245205172275`, 2021-02-04, ubdip: `StateInfo` needed to retain
  indirectly captured/bycatch pieces.

These reports motivate using full refresh as the scalar truth source before the
later incremental, dirty-list, SIMD and million-operation gates.

## Blast relations are not check legality

- `channel_779319972614242354.jsonl:2040`, message
  `1092084211797721108`, 2023-04-02, ubdip: exposure to a possible explosion
  on the next move is distinct from being in direct check now.
- `channel_779317816897699854.jsonl:3510`, message
  `921009609085435915`, 2021-12-16, ubdip; and
  `channel_791247944463417374.jsonl:2081`, message
  `932053593266200596`, 2022-01-15, fulmene: adjacent-king immunity makes
  pseudo-royal check handling a separate Atomic-specific concern.
- `channel_791247944463417374.jsonl:13154`, message
  `1469783644087517185`, 2026-02-07, ubdip: current handling treats touching
  kings as mutually immune.
- `channel_966610323987660830.jsonl:4225`, `:4228` and `:4329`, messages
  `1259523456207159357`, `1259533751398957119` and `1262674542757806111`,
  2024-07-07 through 2024-07-16, ubdip: capture-based filtering is particularly
  relevant to Atomic, whereas generic check filtering is not a useful model of
  its extinction semantics.

These reports support projecting unfiltered CapturePair relations and naming
class 0 a direct enemy-king target rather than a legal check.

## En passant regression history

- `channel_779317816897699854.jsonl:2268`, message
  `862289243337129984`, 2021-07-07, iq_qi94: an Atomic perft discrepancy was
  associated with en passant.
- `channel_779317816897699854.jsonl:3029`, message
  `883743430902173748`, 2021-09-04, iq_qi94: illegal Atomic moves included two
  EP-related cases.

This history motivates the authenticated CapturePair EP tail as the sole source,
fail-closed malformed metadata tests, both colors and perspectives, two-origin
deduplication and explicit separation of landing center from captured pawn.

## Scope caveat

Messages mixing Atomar, `nocheckatomic` or other immunity rules were not used as
normative evidence. Atomic-Stockfish targets the Fairy/Lichess `atomic` rule set;
community history informs test selection but never overrides executable parity.
