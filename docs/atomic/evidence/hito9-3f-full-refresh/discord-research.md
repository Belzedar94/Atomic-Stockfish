# H9.3f local Discord evidence: full refresh before optimization

This note records the non-normative community context consulted for H9.3f.
The executable contract remains the schema, ADR and C++/Python differentials.
No live bot, token or network API was used.

## Archive provenance

- Guild export: `779317816897699850`.
- Source file: `channel_784418118503235625.jsonl` in the local read-only
  Discord vault.
- Source SHA-256:
  `CF41E8D7B79103CBF30A94AB3465B31527DEB5DB7CB576D77459C152CA5F5467`.
- The JSONL line number and immutable Discord message id below jointly identify
  each record. The archive was read only.

## Relevant records

| JSONL line | Message id | Date (UTC) | Author | Architectural signal |
| ---: | --- | --- | --- | --- |
| 440 | `816528064900497428` | 2021-03-03 | `tttak` | Incremental NNUE was already recognized as framework-specific: `DirtyPiece` differed across engines and the old feature-test command could not simply be ported after Stockfish framework changes. |
| 446 | `816679157139243059` | 2021-03-03 | `tttak` | A first incremental port produced less NPS than expected and was suspected of containing bugs. Correctness and measured benefit therefore need separate gates. |
| 470 | `817441651173359666` | 2021-03-05 | `ubdip` | Generic feature changes failed to account for Atomic's special NNUE king representation. Variant-specific semantics must be explicit rather than inherited accidentally. |
| 616 | `821789349187813388` | 2021-03-17 | `ubdip` | Atomic NNUE had learned a variant-specific starting-square valuation, evidence that representational details can materially affect learned behavior. |

## H9.3f consequence

The evidence supports a narrow sequencing decision, not a numeric claim:

1. freeze one scalar, full-board composition oracle first;
2. prove HM and CapturePair are each enumerated once and that one exact CP set
   feeds both downstream projections;
3. keep all scratch caller-owned and verify immutable concurrent reads;
4. add incremental/SIMD state only when it is bit-identical to this oracle;
5. measure speed separately instead of assuming that incremental complexity is
   automatically beneficial.

The historical `nnue_king` implementation is not copied into V3. H9.3f uses
the already frozen compact `KING` and joint-orientation contract. The archive
is useful only as a warning against untested generic reuse.
