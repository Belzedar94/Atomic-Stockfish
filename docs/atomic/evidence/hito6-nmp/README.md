# Hito 6 NMP evidence

This directory binds the null-move block to source commit `0c45a9bf` and tree
`f92e23c5`. The root `manifest.json` is the canonical index and hashes every
versioned evidence file. Executables, the 45 MiB network and Atomic tablebases
remain external artifacts; their sizes and SHA-256 identifiers are recorded,
but the files are not redistributed here.

`bmi2/` preserves the prior matched Windows comparison. Both engines use the
same MinGW 15.2 release toolchain and compiler-reported target
`64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT`. The Fairy manifest additionally
binds `all=no`, `largeboards=no`, external NNUE loading, O3/LTO and no PGO.
Those speed and LOS samples predate the final psutil-distribution provenance,
affinity readback and process-watchdog gates, so they are historical evidence
until the current benchmark and all three TCs are rerun and this package is
regenerated.
`historical-avx2-vs-sse/` preserves the earlier block-local captures but they
are explicitly non-normative because their compiler targets differ.

The bindings, Windows, Linux, sanitizer and WASM manifests describe artifacts
retained in the external workspace report. Only their review-relevant
manifests, summaries and selected raw logs are copied here. Paths inside those
source manifests therefore refer to the report worktree unless the root
manifest lists a local versioned path. Where PowerShell captures were
normalized from UTF-16 or CRLF to UTF-8/LF, both the raw source hash and the
versioned hash are recorded instead of treating a transcode as byte identity.

The pipeline evidence under `pipeline/` identifies the exact dirty tools and
trainer snapshots used for the local executable E2E. It proves compatibility
but is not the clean pinned multi-repository CI closeout; that separate gate is
required before Hito 6 is declared complete.
