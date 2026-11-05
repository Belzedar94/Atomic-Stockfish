# Atomic browser engine benchmark

Generated 2026-11-05T09:05:47.070Z on AMD Ryzen 9 5950X 16-Core Processor             (25 logical CPUs), win32 10.0.19045.
Configuration: 6 positions, go depth 15, MultiPV 3, Threads 4, Hash 64 MiB, ucinewgame before each position, 3 runs per engine, alternating order, fresh page per run.
Engines: Atomic-Stockfish browser worker (03724fee, Emscripten 4.0.10, network atomic_run3b_e202_l05.nnue) and Fairy-Stockfish 14 (npm fairy-stockfish-nnue.wasm 1.1.12, classical evaluation: no Fairy-Stockfish 14 (HalfKAv2) Atomic network was available).

| Browser | Engine | Runs | Median depth-15 ms | Mean ms | Median NPS | Cold init ms | Peak browser RSS MiB | WASM heap MiB | Workers | Main-thread lateness p99 / max ms |
|---|---|---|---|---|---|---|---|---|---|---|
| chromium | atomic | 3 | 240 | 357 | 4580155 | 440 | 947.3 | 512.0 | 5 | 1.2 / 2.0 |
| chromium | fsf | 3 | 1892 | 2253 | 2380544 | 714 | 932.9 | 382.4 | 5 | 1.0 / 2.0 |
| firefox | atomic | 3 | 253 | 357 | 4445142 | 470 | 1143.9 | 512.0 | 5 | 1.3 / 14.3 |
| firefox | fsf | 3 | 2332 | 2533 | 1955365 | 772 | 887.1 | 382.4 | 5 | 2.0 / 20.3 |

## Per position (median of runs)

| Browser | Position | Atomic-SF ms | Atomic-SF first eval ms | Atomic-SF nodes | FSF ms | FSF first eval ms | FSF nodes | Atomic-SF move | FSF move |
|---|---|---|---|---|---|---|---|---|---|
| chromium | start | 827 | 1 | 3710980 | 4956 | 19 | 9503041 | g1f3 g1f3 g1f3 | g1h3 g1h3 g1h3 |
| chromium | opening e3 e6 | 746 | 1 | 3508910 | 2939 | 1 | 6088870 | d8f6 d8f6 d8f6 | f7f6 f7f6 f7f6 |
| chromium | Italian | 14 | 1 | 64214 | 73 | 1 | 201756 | c4f7 c4f7 c4f7 | c4f7 c4f7 c4f7 |
| chromium | perft-2 middlegame | 241 | 1 | 1026747 | 782 | 1 | 1881864 | c1g5 c1g5 c1g5 | c1g5 c1g5 c1g5 |
| chromium | perft-3 middlegame | 76 | 1 | 305940 | 4518 | 1 | 10762375 | c7c8 c7c8 c7c8 | c7c8 c7c8 c7c8 |
| chromium | rook endgame | 84 | 1 | 418242 | 385 | 1 | 1132743 | f3g5 f3g5 f3g5 | f3g5 f3g5 f3g5 |
| firefox | start | 726 | 1 | 3183594 | 4533 | 2 | 7454951 | g1f3 g1f3 g1f3 | g1h3 g1h3 g1h3 |
| firefox | opening e3 e6 | 961 | 1 | 4208362 | 3819 | 1 | 6546193 | d8f6 d8f6 d8f6 | f7f6 f7f6 f7f6 |
| firefox | Italian | 14 | 1 | 64907 | 131 | 1 | 241889 | c4f7 c4f7 c4f7 | c4f7 c4f7 c4f7 |
| firefox | perft-2 middlegame | 301 | 1 | 1331015 | 923 | 1 | 1878068 | c1g5 c1g5 c1g5 | c1g5 c1g5 c1g5 |
| firefox | perft-3 middlegame | 102 | 1 | 418235 | 5151 | 1 | 10173775 | c7c8 c7c8 c7c8 | c7c8 c7c8 c7c8 |
| firefox | rook endgame | 77 | 1 | 391825 | 315 | 1 | 817176 | f3g5 f3g5 f3g5 | f3g5 f3g5 f3g5 |

## Download sizes

| File | Bytes | gzip -9 | brotli 11 |
|---|---|---|---|
| atomic-stockfish.mjs | 76295 | 21337 | 18832 |
| atomic-stockfish.wasm | 625941 | 229388 | 176429 |
| atomic-stockfish.worker.js | 19817 | 6150 | 5360 |
| stockfish.js | 64273 | 22747 | 19961 |
| stockfish.wasm | 1636293 | 492261 | 345637 |
| stockfish.worker.js | 3321 | 1330 | 1175 |
| atomic_run3b_e202_l05.nnue | 47721376 | 26507442 | 22509371 |

Primary-move differences are informational only. Depth-15 node counts are not comparable between the engines (different search and evaluation).
