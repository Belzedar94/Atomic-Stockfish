from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_every_binding_and_wasm_build_links_shared_initialization():
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    makefile_js = (ROOT / "src" / "Makefile_js").read_text(encoding="utf-8")
    wasm_build = (ROOT / "tests" / "wasm-engine" / "build.ps1").read_text(
        encoding="utf-8"
    )
    setup = (ROOT / "setup.py").read_text(encoding="utf-8")

    assert "SRCS = atomic_init.cpp" in makefile
    assert "\tatomic_init.cpp \\\n" in makefile_js
    assert "    'atomic_init.cpp'," in wasm_build
    assert '"src/atomic_init.cpp",' in setup


def test_native_and_nnue_wasm_build_only_the_atomic_feature_extractor():
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    wasm_build = (ROOT / "tests" / "wasm-engine" / "build.ps1").read_text(
        encoding="utf-8"
    )

    assert "nnue/features/half_ka_v2_atomic.cpp" in makefile
    assert "nnue/features/half_ka_v2_atomic.h" in makefile
    assert "'nnue/features/half_ka_v2_atomic.cpp'" in wasm_build

    removed = (
        "nnue/features/half_ka_v2_hm.cpp",
        "nnue/features/half_ka_v2_hm.h",
        "nnue/features/full_threats.cpp",
        "nnue/features/full_threats.h",
    )
    for relative in removed:
        assert relative not in makefile
        assert relative not in wasm_build
        assert not (ROOT / "src" / relative).exists()


def test_legacy_threat_delta_plumbing_is_not_reintroduced():
    sources = {
        relative: (ROOT / "src" / relative).read_text(encoding="utf-8")
        for relative in (
            "types.h",
            "position.h",
            "position.cpp",
            "attacks.h",
            "attacks.cpp",
            "misc.h",
            "nnue/nnue_common.h",
            "nnue/features/half_ka_v2_atomic.h",
        )
    }

    for token in ("DirtyThreat", "UsesThreatDeltas", "ThreatWeightType"):
        assert all(token not in contents for contents in sources.values())

    for token in (
        "piece_array()",
        "line_bb(",
        "ray_pass_bb(",
        "PawnPushOrAttacks",
        "make_space(",
    ):
        assert all(token not in contents for contents in sources.values())

    accumulator = (ROOT / "src" / "nnue" / "nnue_accumulator.cpp").read_text(
        encoding="utf-8"
    )
    assert "std::is_same_v<FeatureSet::DiffType, DirtyPiece>" in accumulator


def test_legacy_atomic_v1_inventory_omits_unselected_modern_nnue_layers():
    # AtomicNNUEV2 may legitimately add modern layers in H9. Its build graph
    # must then split this LegacyAtomicV1 inventory guard by backend.
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    common = (ROOT / "src" / "nnue" / "nnue_common.h").read_text(encoding="utf-8")
    simd = (ROOT / "src" / "nnue" / "simd.h").read_text(encoding="utf-8")

    removed = (
        "nnue/layers/affine_transform_sparse_input.h",
        "nnue/layers/sqr_clipped_relu.h",
        "nnue/nnz_helper.h",
    )
    for relative in removed:
        assert relative not in makefile
        assert not (ROOT / "src" / relative).exists()

    for token in ("FtMaxVal", "HiddenOneVal"):
        assert token not in common
    assert "vec_nnz" not in simd


def test_atomic_state_info_only_stores_live_check_metadata():
    header = (ROOT / "src" / "position.h").read_text(encoding="utf-8")
    source = (ROOT / "src" / "position.cpp").read_text(encoding="utf-8")
    unit_test = (ROOT / "tests" / "atomic_see.cpp").read_text(encoding="utf-8")

    assert "usize(QUEEN) - usize(PAWN) + 1" in header
    assert "static_assert(CHECK_SQUARE_NB == 5);" in header
    assert "std::array<Bitboard, CHECK_SQUARE_NB>" in header
    assert "pt == KING ? 0" in header
    assert "Position::checkers() const { return 0; }" in header

    for removed in ("checkersBB", "pinners[", "Position::pinners"):
        assert removed not in header
        assert removed not in source

    assert "assert(givesCheck == atomic_in_check(sideToMove));" not in source
    for contract in (
        "std::is_standard_layout_v<StateInfo>",
        "std::is_trivially_copyable_v<StateInfo>",
        "offsetof(StateInfo, key)",
        "expect_gives_check_matches_child",
    ):
        assert contract in unit_test


def test_orthodox_evasion_generator_is_not_instantiated():
    header = (ROOT / "src" / "movegen.h").read_text(encoding="utf-8")
    movegen = (ROOT / "src" / "movegen.cpp").read_text(encoding="utf-8")
    movepick = (ROOT / "src" / "movepick.cpp").read_text(encoding="utf-8")
    position = (ROOT / "src" / "position.cpp").read_text(encoding="utf-8")

    assert "moveList = generate<NON_EVASIONS>(pos, moveList);" in movegen
    assert "generate<EVASIONS>(pos" not in movegen
    assert "template Move* generate<EVASIONS>" not in movegen
    assert "MoveList<EVASIONS>" not in position
    assert "MoveList<EVASIONS>" not in movepick

    for removed in ("EVASION_TT", "EVASION_INIT", "score<EVASIONS>"):
        assert removed not in movepick

    # Keep the uninstantiated upstream template vocabulary. Removing it would
    # renumber GenType and widen this behavior-neutral specialization change.
    assert "EVASIONS," in header
    assert "Type == EVASIONS" in movegen


def test_reader_test_links_keep_transposition_table_dependency_isolated():
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    manifest_objects = makefile.split(
        "ATOMIC_BIN_V2_MANIFEST_READER_TEST_OBJS =", 1
    )[1].split("ATOMIC_BIN_V2_READER_TEST_EXE", 1)[0]
    reader_objects = makefile.split("ATOMIC_BIN_V2_READER_TEST_OBJS =", 1)[1].split(
        "ATOMIC_BIN_V2_SRCS", 1
    )[0]

    assert "tt.o" not in manifest_objects
    assert "tt.o" not in reader_objects
    assert "src/atomic-bin-v2-manifest-reader-tests*" in gitignore
    assert "src/atomic-bin-v2-reader-tests*" in gitignore

    for source in (
        ROOT / "tests" / "atomic_bin_v2_manifest_reader.cpp",
        ROOT / "tests" / "atomic_bin_v2_reader.cpp",
    ):
        contents = source.read_text(encoding="utf-8")
        assert '#include "tt.h"' in contents
        assert "TranspositionTable::first_entry(Key) const { return nullptr; }" in contents
