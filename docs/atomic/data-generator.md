# Atomic training-data generator

Atomic-Stockfish provides a separate `data-generator` build for NNUE dataset
production. The normal UCI/XBoard playing binary does not link the generator
codec, coordinator or commands.

## Build and capability handshake

From `src`:

```sh
make -j data-generator ARCH=x86-64-bmi2
./atomic-stockfish-data-generator
```

The output executable is `atomic-stockfish-data-generator` (with `.exe` on
Windows). Its `atomic_data_schema` command reports the exact write capability:

```json
{"schema_sha256":"acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1","formats":{"legacy-atomic-v1":{"read":false,"write":true,"record_size":72}}}
```

The schema is frozen in `schemas/atomic-schema.json`. The generator is
write-only. The `variant-nnue-tools` `atomic` line is now a thin reader/writer
wrapper pinned to Atomic-Stockfish: its `atomic-data-tools` artifact validates,
decodes, converts and reports statistics, while PV self-play exists only in
this Atomic-Stockfish generator.

## Generate Legacy Atomic V1 data

Load a compatible Atomic network and select `pure` before starting generation:

```text
setoption name EvalFile value /absolute/path/to/atomic.nnue
setoption name Use NNUE value pure
setoption name Threads value 1
isready
generate_training_data depth 3 count 1000000 output_file_name training_data data_format bin seed run-001
```

`pure` is reserved for dataset generation. Normal play and Elo/LOS matches use
`Use NNUE=true`.

The supported PV self-play options retain the historical names: `depth`,
`min_depth`, `max_depth`, `nodes`, `count`, `eval_limit`, `eval_diff_limit`,
the random-move and random-MultiPV controls, `write_min_ply`, `write_max_ply`,
`save_every`, `book`, `random_file_name`, `keep_draws`, both draw-adjudication
flags, the three position filters, `data_format`, `seed`, and
`set_recommended_uci_options`.

Current H7.2 scope is deliberately narrow:

- only `data_format bin` (`legacy-atomic-v1`) is written;
- Atomic960 is rejected because V1 has no castling-rook origins;
- both kings must be present in every saved record;
- depth starts at one; `depth=0` is rejected rather than silently changing to
  quiescence behavior;
- non-PV generation, converters, puzzle generation and statistics remain in
  the thin tools wrapper during the Legacy V1 consolidation.

## Safety and reproducibility

Output files use exclusive creation. Existing paths are never appended,
overwritten or deleted. A failed run removes only files created by that run;
empty and partial datasets are invalid.

The resolved decimal seed is printed as `PRNG::initial_seed`. Replaying it is
byte-deterministic when the binary, network, command, book, UCI options and
`Threads=1` are identical. Every run clears worker state and the transposition
table first. With multiple threads, record ordering is intentionally not
defined.

An EPD book is shuffled once with worker zero's historical PRNG stream and then
served round-robin across workers. With no book, selecting the single Atomic
start position consumes no random number.

## Tests

With the external development network available at
`../../atomic_run3b_e202_l05.nnue`, run from `src`:

```sh
make -j data-generator-tests ARCH=x86-64-bmi2
```

The gate covers the complete 504-byte Fairy wire fixture, deterministic
same/fresh-process generation, exact Threads=2 execution, real Apery reply
insertion, random-MultiPV replay and extreme diff arithmetic,
`random_move_min_ply=-1`, shuffled multi-FEN books across game boundaries,
invalid network/mode/Atomic960 and configuration failures, exclusive output
behavior, and proof that the normal playing binary does not expose generator
commands. All valid fixtures and normal-binary isolation run in sanitizer and
platform smoke jobs. The full `tests/data_generator.py` invocation additionally
passes every generated dataset through the tools validator and trainer native
loader.
