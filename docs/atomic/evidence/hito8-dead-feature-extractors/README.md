# H8.2a inactive feature-extractor evidence

H8.2a removes the `FullThreats` and `HalfKAv2_hm` source families from the
native and NNUE-WASM build graphs. `HalfKAv2Atomic` remains the only compiled
feature extractor. This directory binds the no-functional-change claim to the
exact source, binaries, frozen net, corpus, and clean-machine commit A/B run.

The normative command was:

```text
python tests/atomic_bench_ab.py --candidate src/atomic-stockfish.exe --control ../Atomic-Stockfish-h8-dirty-threats/src/atomic-stockfish.exe --eval-file ../atomic_run3b_e202_l05.nnue --nodes 100000 --hash 64 --affinity 24 --timeout 60
```

OpenBench was paused for the serialized measurement and restored before further
project work. The complete output is retained in `commit-ab.log`; `manifest.json`
records the authenticated inputs and result.
