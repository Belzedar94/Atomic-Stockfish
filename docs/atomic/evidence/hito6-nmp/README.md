# Hito 6 NMP evidence

This directory binds the null-move block introduced at `0c45a9bf` to the final
hardened runtime candidate `ebfe9342`. The root `manifest.json` is the canonical
index and hashes every versioned evidence file. Executables, the 45 MiB network
and Atomic tablebases remain external artifacts; their sizes and SHA-256
identifiers are recorded, but the files are not redistributed here.

`bmi2/` preserves the prior matched Windows comparison. The normative rerun is
under `bmi2/final-ebfe9342/`; it contains the clean-build manifest, a normalized
pipeline summary and UTF-8/LF copies of the benchmark and three strength logs.
The manifest records both the original UTF-16LE capture hashes and normalized
versioned hashes. Both engines use the same MinGW 15.2 release toolchain and
compiler-reported target
`64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT`. The Fairy manifest additionally
binds `all=no`, `largeboards=no`, external NNUE loading, O3/LTO and no PGO.
The logs directly below `bmi2/` predate the final psutil-distribution
provenance, affinity readback and process-watchdog gates and therefore remain
historical byte-exact evidence.
`historical-avx2-vs-sse/` preserves the earlier block-local captures but they
are explicitly non-normative because their compiler targets differ.

The bindings, Windows, Linux, sanitizer and WASM manifests describe artifacts
retained in the external workspace report. Only their review-relevant
manifests, summaries and selected raw logs are copied here. Paths inside those
source manifests therefore refer to the report worktree unless the root
manifest lists a local versioned path. Where PowerShell captures were
normalized from UTF-16 or CRLF to UTF-8/LF, both the raw source hash and the
versioned hash are recorded instead of treating a transcode as byte identity.
Package entries fingerprint the canonical staged Git blobs after
`.gitattributes` normalization, so checkout-specific line endings cannot alter
the recorded versioned identities.

The pipeline evidence under `pipeline/` identifies the earlier dirty tools and
trainer snapshots used for the local executable E2E. The clean pinned
multi-repository closeout is recorded separately under
`../hito6-pinned-pipeline/` and in the public Hito 6 CI runs.
