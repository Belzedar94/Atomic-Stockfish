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


def test_reader_test_links_include_transposition_table_dependency():
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")

    manifest_objects = makefile.split(
        "ATOMIC_BIN_V2_MANIFEST_READER_TEST_OBJS =", 1
    )[1].split("ATOMIC_BIN_V2_READER_TEST_EXE", 1)[0]
    reader_objects = makefile.split("ATOMIC_BIN_V2_READER_TEST_OBJS =", 1)[1].split(
        "ATOMIC_BIN_V2_SRCS", 1
    )[0]

    assert "tt.o" in manifest_objects
    assert "tt.o" in reader_objects
