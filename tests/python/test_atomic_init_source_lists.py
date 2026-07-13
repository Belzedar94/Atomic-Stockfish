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
