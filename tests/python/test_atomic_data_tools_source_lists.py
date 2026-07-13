from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def makefile_assignment(makefile: str, name: str, next_name: str) -> str:
    return makefile.split(f"{name} =", 1)[1].split(f"{next_name} =", 1)[0]


def test_data_tools_is_a_separate_manifest_reader_binary() -> None:
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    playing_sources = makefile_assignment(makefile, "SRCS", "OTHER_SRCS")
    tools_sources = makefile_assignment(
        makefile, "ATOMIC_DATA_TOOLS_SRCS", "ATOMIC_DATA_TOOLS_HEADERS"
    )
    tools_objects = makefile.split("ATOMIC_DATA_TOOLS_OBJS =", 1)[1].split(
        "\nifeq ($(target_windows),yes)", 1
    )[0]

    assert "data/atomic_data_tools.cpp" not in playing_sources
    assert "data/atomic_data_tools_json.cpp" not in playing_sources
    assert "data/atomic_bin_v2_reader.cpp" not in playing_sources
    assert "data/atomic_data_tools.cpp" in tools_sources
    assert "data/atomic_data_tools_json.cpp" in tools_sources
    assert "data/atomic_bin_v2_reader.cpp" in tools_sources
    assert "data/atomic_bin_v2_sink.cpp" not in tools_sources
    assert "data/training_data_generator.cpp" not in tools_sources

    for required in (
        "atomic_init.o",
        "atomic_bin_v2_wire_codec.o",
        "atomic_bin_v2_adapter_codec.o",
        "atomic_bin_v2_sha256_codec.o",
        "atomic_bin_v2_manifest_codec.o",
        "atomic_bin_v2_reader_codec.o",
        "atomic_data_tools_json.o",
        "atomic_data_tools.o",
    ):
        assert required in tools_objects

    for forbidden in (
        "main.o",
        "search.o",
        "thread.o",
        "tt.o",
        "atomic_data_generator_",
        "atomic_bin_v2_sink_codec.o",
    ):
        assert forbidden not in tools_objects

    assert "data-tools: config-sanity $(ATOMIC_DATA_TOOLS_EXE)" in makefile
    assert "data-tools-tests: config-sanity $(ATOMIC_DATA_TOOLS_EXE)" in makefile
    assert "$(ATOMIC_DATA_TOOLS_JSON_TEST_EXE)" in makefile
    assert "./$(ATOMIC_DATA_TOOLS_JSON_TEST_EXE)" in makefile
    assert "ATOMIC_DATA_TOOLS_ENTRY_LDFLAGS = -municode" in makefile
    assert "$(ATOMIC_DATA_TOOLS_ENTRY_LDFLAGS) $(LDFLAGS)" in makefile

    objclean = makefile.split("objclean:", 1)[1].split("# clean auxiliary", 1)[0]
    assert "atomic-stockfish-data-tools" in objclean
    assert "atomic-stockfish-data-tools.exe" in objclean
    assert "atomic-stockfish-data-tools.*" in objclean
    assert "atomic-data-tools-json-tests.exe" in objclean
    assert "atomic-data-tools-json-tests.bin" in objclean
    assert "atomic-data-tools-json-tests.*" in objclean
    assert "src/atomic-data-tools-json-tests*" in gitignore


def test_data_tools_supplies_its_own_prefetch_stub() -> None:
    source = (ROOT / "src" / "data" / "atomic_data_tools.cpp").read_text(
        encoding="utf-8"
    )
    assert '#include "tt.h"' in source
    assert "TranspositionTable::first_entry(const Key) const { return nullptr; }" in source
    assert "int wmain(int argc, wchar_t* argv[])" in source
    assert "configure_binary_output();" in source
    assert "std::filesystem::u8path(*manifest)" in source


def test_generator_capability_remains_writer_only() -> None:
    source = (ROOT / "src" / "data" / "legacy_atomic_v1.cpp").read_text(
        encoding="utf-8"
    )
    assert '"atomic-bin-v2":{"schema_sha256":' in source
    assert '"read":false,"write":true,"header_size":96,"record_size":64' in source


def test_data_tools_contract_runs_in_native_debug_mingw_and_sanitizer_lanes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "ARCH=x86-64 COMP=${{ matrix.comp }} COMPCXX=${{ matrix.cxx }} "
        "data-tools-tests"
    ) in workflow
    assert "ARCH=x86-64 debug=yes optimize=no data-tools-tests" in workflow
    assert "ARCH=x86-64 COMP=mingw data-tools-tests" in workflow
    assert (
        "sanitize=${{ matrix.sanitizer }} debug=yes optimize=no data-tools-tests"
        in workflow
    )
