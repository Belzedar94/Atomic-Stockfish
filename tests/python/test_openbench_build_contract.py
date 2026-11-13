from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
OPENBENCH = MAKEFILE.split("### Section 12. OpenBench build shim", 1)[1]
BUNDLE_SOURCE = (ROOT / "src" / "data" / "openbench_bundle.cpp").read_text(
    encoding="utf-8"
)
BRIDGE_SOURCE = (ROOT / "src" / "data" / "openbench_datagen.cpp").read_text(
    encoding="utf-8"
)


def test_openbench_selects_playing_or_datagen_default_only_with_evalfile():
    assert re.search(
        r"ifneq \(\$\(strip \$\(EVALFILE\)\),\)\s*"
        r"ifeq \(\$\(OPENBENCH_DATAGEN\),1\)\s*"
        r"\.DEFAULT_GOAL := openbench-datagen\s*else\s*"
        r"\.DEFAULT_GOAL := openbench\s*endif\s*endif\s*$",
        OPENBENCH,
    )


def test_openbench_selects_the_normative_compiler_on_windows_and_linux():
    mapping = re.search(
        r"ifeq \(\$\(OS\),Windows_NT\)\s*"
        r"OPENBENCH_COMP = (?P<windows>\S+)\s*else\s*"
        r"OPENBENCH_COMP = (?P<linux>\S+)\s*endif",
        OPENBENCH,
    )
    assert mapping is not None
    assert mapping.groupdict() == {"windows": "mingw", "linux": "gcc"}


def test_openbench_forwards_worker_output_and_compiler_to_the_native_pgo_build():
    # The playing-engine shim builds with PGO for the worker's own CPU, so each
    # machine benches the binary it plays with. The worker's EXE and CXX must
    # reach that build unchanged.
    playing = OPENBENCH.split("\nopenbench:", 1)[1].split("\nopenbench-datagen:", 1)[0]
    assert re.search(
        r"\+\$\(MAKE\) profile-build EXE=\"\$\(EXE\)\" CXX=\"\$\(CXX\)\"\s*\\\s*"
        r"ARCH=native COMP=\$\(OPENBENCH_COMP\)",
        playing,
    )


def test_openbench_embeds_the_authenticated_network_under_the_canonical_name():
    assert "OPENBENCH_NET = atomic-ft5-0p8B.nnue" in OPENBENCH
    assert 'cp "$(EVALFILE)" "$(OPENBENCH_NET).tmp"' in OPENBENCH
    assert 'mv "$(OPENBENCH_NET).tmp" "$(OPENBENCH_NET)"' in OPENBENCH
    assert "-DATOMIC_NNUE_EMBEDDING" in OPENBENCH


def test_openbench_datagen_build_is_isolated_and_embeds_only_for_worker_bench():
    datagen = OPENBENCH.split("openbench-datagen:", 1)[1].split(
        "ifneq ($(strip $(EVALFILE)),)", 1
    )[0]
    assert "data-generator-profile-build EXE=atomic-openbench-playing-unused" in datagen
    assert 'ATOMIC_DATA_GENERATOR_EXE="$(EXE)"' in datagen
    assert "ARCH=x86-64-bmi2 COMP=$(OPENBENCH_COMP)" in datagen
    assert 'cp "$(EVALFILE)" "$(OPENBENCH_NET).tmp"' in datagen
    assert "-DATOMIC_NNUE_EMBEDDING" in datagen


def test_openbench_datagen_profile_build_profiles_the_generator():
    recipe = MAKEFILE.split("data-generator-profile-build: ", 1)[1].split("\n\n", 1)[0]
    assert "PROFILE_TARGET=data-generator $(profile_make)" in recipe
    assert "$(DATAGEN_PGO_RUN) > PGOBENCH.out" in recipe
    assert "PROFILE_TARGET=data-generator $(profile_use)" in recipe
    run = MAKEFILE.split("DATAGEN_PGO_RUN  = ", 1)[1].split("\n\n", 1)[0]
    assert "'bench'" in run
    assert "generate_training_data" in run


def test_openbench_bridge_objects_belong_only_to_generator_source_list():
    generator_sources = re.search(
        r"ATOMIC_DATA_GENERATOR_SRCS = (?P<body>.*?)(?:\n\S|\Z)",
        MAKEFILE,
        re.DOTALL,
    )
    assert generator_sources is not None
    assert "data/openbench_datagen.cpp" in generator_sources.group("body")
    assert "data/openbench_bundle.cpp" in generator_sources.group("body")
    playing_sources = MAKEFILE.split("SRCS =", 1)[1].split("OTHER_SRCS", 1)[0]
    assert "openbench_datagen" not in playing_sources
    assert "openbench_bundle" not in playing_sources


def test_openbench_bundle_copy_buffer_is_heap_allocated():
    assert "std::vector<char> buffer(CopyBufferSize);" in BUNDLE_SOURCE
    assert "std::array<char, CopyBufferSize>" not in BUNDLE_SOURCE


def test_openbench_authenticates_supplied_sha_gates_before_self_play():
    preflight = BRIDGE_SOURCE.index(
        "authenticate_sha256_gate(path_from_utf8(params.network)"
    )
    generation = BRIDGE_SOURCE.index("generate_training_data(engine, generatorInput)")
    assert preflight < generation
    assert (
        'authenticate_sha256_gate(path_from_utf8(params.book), params.bookSha256, "book"'
        in BRIDGE_SOURCE
    )
    assert 'authenticate_sha256_gate(path_from_utf8(params.network)' in BRIDGE_SOURCE
    assert 'const auto output = path_from_utf8(params.output)' in BRIDGE_SOURCE
