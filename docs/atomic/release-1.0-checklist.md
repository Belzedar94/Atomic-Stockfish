# Atomic-Stockfish 1.0 release checklist

This checklist is the release controller for `v1.0.0`. A green development CI
run is necessary but is not, by itself, permission to publish. Every item is
executed against the exact tagged commit and its hashes are recorded in the
release manifest.

## Frozen inputs

- Atomic-Stockfish `main` contains engine PR #42 merge
  `dde43fc08fb2bd45eec09d3dbe9f6d06845eeb24` and PR #43 merge
  `420c9f35266fbdc2167dc5b9d8d20d90281c60c9`.
- `variant-nnue-pytorch/atomic` contains trainer PR #13 merge
  `44663e28c3e5464ff3be2cdaa26c8518b3951c5f` and intentionally authenticates
  the H9.3l-a contract commit where required.
- `variant-nnue-tools/atomic` pins final engine merge
  `420c9f35266fbdc2167dc5b9d8d20d90281c60c9` in reviewed PR #33 merge
  `450049ee7a0ece32694b11f6c55deb7df1d42a84`.
- `tests/legacy_pipeline.lock.json` records tools merge
  `450049ee7a0ece32694b11f6c55deb7df1d42a84` and trainer merge
  `44663e28c3e5464ff3be2cdaa26c8518b3951c5f`, never branch-head commits.
- The 500M bootstrap campaign remains an independent Atomic BIN V2 pilot, not a
  V3 publication-ready release dataset. Its live progress and exact engine,
  network, book and command are recorded, but completion does not alter the
  versioned playing binary or its release artifacts.
- OpenBench v39 is deployed only after the live v38 campaign has drained and
  its database/media backup has been verified; the engine release never forces
  a controller upgrade underneath an active campaign.
- The reference Legacy Atomic V1 network is external and identified only by
  SHA-256 `99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6`.
  Release packages do not redistribute it without a separate rights decision.
- Atomic Syzygy files remain external; the release records the fixture/table
  manifests used by the gate and does not repackage third-party tables.

## Exact-head quality gates

1. Build release and debug/assert binaries with GCC and Clang on Linux and
   MinGW on Windows. Build portable x86-64, AVX2 and BMI2 release targets. The
   release assets specifically use the digest-pinned GCC 14.2.0 Bookworm and
   Dockcross MinGW images recorded in the v2 inventory. Each native target is
   built in two isolated roots; both executable bytes and final archive bytes
   must match. Windows protocol smoke runs on `windows-2022` only after it
   downloads and extracts the cross-built archive.
2. Run ASan, UBSan, TSan and the applicable Valgrind gates.
3. Run the Atomic rules/API units, `test.py`, every Atomic/Atomic960 perft,
   differential, signature and reprosearch suite.
4. Run UCI and XBoard/CECP protocol suites, including clocks, analyze, ponder,
   setboard, Atomic960 and clean shutdown.
5. Run the Python stable-ABI matrix on Python 3.9 and the newest supported
   Python on Windows and Linux. The `source` job is the only sdist authority;
   wheel jobs download its normalized sdist, authenticate its SHA-256 and
   provenance, and never invoke `sdist` themselves. Build each wheel twice
   under the commit-derived `SOURCE_DATE_EPOCH`, require byte-identical wheels,
   test the installed wheel outside the source tree with pinned `mypy 1.19.1`,
   and require strict `abi3audit 0.0.26` success. Then download the exact wheel
   artifact and install it without dependency resolution on Python 3.9, 3.12
   and 3.14 on both Linux and Windows; version, variant, perft and engine identity
   must pass outside the checkout. Both producers target the CPython 3.9 stable
   ABI and use pip 26.0.1, setuptools 80.9.0 and wheel 0.45.1 from the
   hash-closed build lock. The pinned manylinux image supplies the exact Linux
   CPython micro-version; cibuildwheel supplies CPython 3.9.13 on Windows. Each
   producer's two builds use separate caches. On Windows, both build interpreters must
   emit the same canonical runner/Visual Studio/SDK/CPython fingerprint and its
   bytes and SHA-256 must equal the reviewed document named in the release inventory.
   The release PR workflow first reproduces the normalized sdist twice and then
   runs this exact Windows wheel recipe twice against that authenticated sdist;
   the tag workflow repeats the same authority chain for publication.
6. Build the CommonJS and ES-module Board package twice with the same pinned
   Emscripten image, require byte-identical JavaScript/WASM, and run lifecycle,
   exception and cross-surface parity tests.
7. Build the complete Node UCI WASM twice, require byte-identical artifacts,
   and run classical plus Legacy V1/AtomicNNUEV2 load, search, switch and export
   tests with authenticated external networks. `Use NNUE=pure` is exercised by
   the data-generation surface, not advertised as a playing mode.
8. Run Atomic Syzygy driver, UCI and real-table fixtures, including touching
   kings and six-man positions, with tablebases enabled and disabled.
9. Run Legacy V1 and Atomic BIN V2 generation, validation, lossless decode,
   training-step, serialization, reimport and engine-load gates. Run all V3
   structural, trajectory, reachability and trainer execution-block tests that
   exist at the tagged commit.
10. Re-run the fixed multiposition speed corpus with one thread, fixed hash,
    affinity, warm-up and five repetitions. The median release BMI2 binary must
    remain faster than the frozen optimized Fairy baseline. Playing code changes
    after the last accepted OpenBench result require their normal STC/LTC gate;
    release-only metadata and packaging changes must preserve the engine bench.

No skip is added for convenience. A platform-only skip must name the missing
dependency and be recorded in the manifest.

## Release assets

- Linux x86-64 portable, AVX2 and BMI2 native engine archives.
- Windows x86-64 portable, AVX2 and BMI2 native engine archives.
- Python source distribution and CPython 3.9 stable-ABI wheels for Windows and
  manylinux x86-64.
- `@atomic-stockfish/ffish` CommonJS/ES-module Board WASM package.
- Complete Node UCI NNUE WASM package without an embedded network.
- Source archive, GPL, AUTHORS, README, release notes, machine-readable build
  manifest and `SHA256SUMS`.

Each native archive exposes both UCI and XBoard from the same executable. There
is no separate protocol binary.

## Publication transaction

1. Verify `AtomicVersionMajor/Minor/Patch`, Python metadata, JavaScript metadata
   and the proposed tag all equal `1.0.0`.
2. Enable GitHub immutable releases with the repository administration API
   before creating the tag. The release workflow only performs the read check
   and fails closed when `GET /immutable-releases` is absent or not `enabled`;
   it never silently changes repository policy. Store a repository-scoped
   fine-grained token with only `Administration: read` as the Actions secret
   `ATOMIC_RELEASE_POLICY_TOKEN`; the normal `GITHUB_TOKEN` cannot request that
   permission. Create or rotate it before expiry using the same minimal scope,
   replace the secret without logging its value, and remove it after the release
   if it is not part of the standing rotation policy. It is consumed only by
   the trusted `v1.0.0` tag-push publication jobs, never by pull-request jobs,
   and only for the two `GET /immutable-releases` calls.
3. Create every asset in an isolated clean checkout with a fixed toolchain.
   Linux, MinGW and Emscripten producers are selected by immutable container
   digests. GitHub does not expose an immutable selector for its hosted Windows
   image, so the Windows wheel instead fails closed unless `ImageOS`,
   `ImageVersion` and the complete toolchain fingerprint match the reviewed
   inventory. If that hosted image is no longer available, prepare and review a
   new release commit; never update the expected fingerprint during a release
   run.
4. Re-read and hash all assets, then write the manifest and `SHA256SUMS` last.
   Every producer-side provenance descriptor freezes its asset SHA-256; the
   assembler verifies that digest again before and after its authenticated copy.
5. Create an annotated `v1.0.0` tag only for the exact reviewed commit. Record
   and re-check both the tag-object SHA and its direct peeled commit SHA through
   local Git and the GitHub Git Database API; a lightweight or nested tag fails.
6. Require a successful `Atomic CI` run produced by a `push` of that same tag
   and SHA. A `main`, pull-request, workflow-dispatch, or same-SHA/different-ref
   run is not publication evidence.
7. Upload assets to a draft GitHub release only. Download the complete draft
   with `gh release download`, require its exact file list and byte hashes to
   equal the candidate, and re-run `SHA256SUMS`. An invalid draft is deleted;
   a valid draft remains unpublished for human review. The workflow first
   reserves a unique draft ID and its `always()` cleanup may delete only that
   reserved ID, including after a partial upload failure. This workflow contains
   no automatic publish transition.
8. Immediately before the manual publish click, repeat the remote trust checks
   rather than relying on the earlier workflow run. Re-read the exact tag ref,
   annotated tag object and direct peeled commit through the GitHub Git Database
   API and require all three recorded SHAs to remain byte-for-byte identical.
   Re-read the immutable-releases policy and require it to remain enabled. Fetch
   the reserved release by its exact ID and require it to remain the unique draft
   for `v1.0.0`; download that draft again into a new empty directory, compare its
   exact names and bytes with the frozen candidate, and re-run `SHA256SUMS`.
9. Manually publish release notes that state the external NNUE/Syzygy requirements,
   supported protocols/bindings and exact known limitations.
10. Immediately after publication, re-read the immutable-releases policy and
    require it still enabled. Fetch the release by the same exact ID and require
    `draft=false`, a non-null publication timestamp and the unchanged `v1.0.0`
    tag. Re-read the tag ref, annotated tag object and direct peeled commit and
    require the recorded SHAs unchanged. Download the now-published assets into
    another new empty directory, require byte equality with the frozen candidate,
    and re-run `SHA256SUMS`. Any discrepancy is a release incident; do not move
    or recreate the tag.
11. Preserve the build logs and gate manifest under `docs/atomic/evidence` in a
   follow-up evidence PR without rewriting the tagged source.

If any downloaded asset, tag object, peeled commit, version, bench, CI ref or
gate differs, delete the draft release and investigate. Never move an existing
release tag and never publish from this workflow.
