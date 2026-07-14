# H9.1 NNUE dispatcher evidence

The measured source commit is
`2ab3d9c03f60817575d5ad3f7452cda4ab38d43b` with tree
`5e6426abfe441e4c9ca959700bd2cd7ef539dec2`. Its H8 control is
`c0197b3bb2474265b95f81c30a8f855453af1b72`.

H9.1 is an architectural seam, not a new evaluator. It preserves the only
active backend as `LegacyAtomicV1`, proves that the facade adds no accumulator
storage or hot-path instructions, and routes native, NUMA, Python, JavaScript
and WASM consumers through the same backend boundary.

## Normative results

- Playing signature: `338376`.
- Incremental release: 1,000,000 operations, 500,000 makes, 500,000 undos,
  18,761 captures, 241,087 full-refresh comparisons, signature
  `0x8742E39B793C46AB`.
- Frozen Fairy: `10,000/10,000`, playing delta `0`, pure trace delta `0.005`.
- Native: release BMI2, debug/assert AVX2 and portable SSE2 all pass.
- Bindings: Python `22/22` and `588/588`; CommonJS `58/58`; ESM `58/58`.
- Syzygy: driver `5/5`, 13 fixture hashes and real 3-6-man UCI probes.
- Legacy pipeline: `status=passed`, deterministic data SHA
  `d95f5180c7d6319e8d838752b49c51f611c311aef728c30b42c2df02c2071639`.
- V2 dataset pipeline: `status=passed`, `global_step=1`, serialized candidate
  SHA `f64731091875c4a1656e204a1064159ef368039edf2e29246e703d331047fbac`.

The complete Hito 5 release log is 75,864 bytes with SHA-256
`08E1C5DD66BE827812A140BA3D798E757071AA1D02C10B56D346358117DB60D0`.
The redacted V2 E2E console log is 1,202 bytes with SHA-256
`8C58C1A1B6E56126865BE2B20380B4A292B98CC59F7988062004E42BB39CCEED`.
The V2 archive `result.json`, `hashes.json` and `provenance.json` hashes are
respectively `E77629AC...F172`, `8293614F...68F3` and
`D3BE0EFA...47E4`; the complete values are frozen in `manifest.json`.

## Reproducible native artifact

- Candidate: 4,257,944 bytes,
  `38ADC76C760257999A821C4023CBC10F83065B84DB93905C5FD6A8FC274BC4E8`.
- H8 control: 4,255,610 bytes,
  `2F700AD6DC3A3A57F1247081B9A67610C165255B30CC17A52560E11C850B1EC9`.
- Two independent candidate links are byte-identical.
- Binary delta: `+2,334` bytes.
- `Eval::evaluate`: 218 instructions before and after.
- `Worker::evaluate`: 329 instructions before and after.

The final serialized A/B is retained in compact form in `commit-ab.log`.
Its pooled result is `-0.337%`, with two positive and three negative batches
under visible host interference. It is diagnostic only. No OpenBench strength
test is appropriate for an exact-signature, evaluation-neutral dispatcher.
