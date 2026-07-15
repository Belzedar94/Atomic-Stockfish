import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HEADER = (ROOT / "src" / "nnue" / "nnue_dispatcher.h").read_text(
    encoding="utf-8"
)
SOURCE = (ROOT / "src" / "nnue" / "nnue_dispatcher.cpp").read_text(
    encoding="utf-8"
)


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def without_cpp_comments(text: str) -> str:
    return re.sub(r"//.*?$|/\*.*?\*/", "", text, flags=re.MULTILINE | re.DOTALL)


def function_body(source: str, signature: str, next_signature: str) -> str:
    start = source.index(signature)
    end = source.index(next_signature, start)
    return source[start:end]


def workflow_job(workflow: str, job: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job)}:\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    assert match is not None, f"missing workflow job: {job}"
    return match.group(0)


def test_dual_backend_facade_is_a_tagged_inline_union_without_indirection():
    dispatcher = compact(HEADER)
    assert (
        "enum class NetworkBackend : u8 { LegacyAtomicV1, AtomicNNUEV2 };"
        in dispatcher
    )
    assert "inline constexpr usize NetworkBackendCount = 2;" in dispatcher
    assert "static_assert(NetworkBackendCount == 2);" in dispatcher
    assert "LegacyAtomicV1::Network legacy;" in dispatcher
    assert "AtomicV2::Network atomicV2;" in dispatcher
    assert "NetworkBackend backend_" in HEADER
    assert "AtomicNNUEV3" not in HEADER
    assert "AtomicNNUEV3" not in SOURCE
    assert "atomic_v3" not in HEADER
    assert "atomic_v3" not in SOURCE

    code = without_cpp_comments(HEADER)
    for forbidden in (
        "std::variant",
        "virtual ",
        "AnyNetwork*",
        "AnyAccumulator*",
        "void (*",
        "unique_ptr<LegacyAtomicV1",
        "unique_ptr<AtomicV2",
    ):
        assert forbidden not in code


def test_facade_preserves_the_shared_memory_copy_contract():
    for contract in (
        "std::is_trivially_copyable_v<AnyNetwork>",
        "std::is_trivially_copy_constructible_v<AnyNetwork>",
        "std::is_trivially_move_constructible_v<AnyNetwork>",
        "std::is_trivially_destructible_v<AnyNetwork>",
        "sizeof(AnyNetwork) >= sizeof(LegacyAtomicV1::Network)",
        "sizeof(AnyNetwork) >= sizeof(AtomicV2::Network)",
    ):
        assert contract in HEADER

    assert "struct std::hash<Stockfish::Eval::NNUE::AnyNetwork>" in HEADER
    assert "return network.get_content_hash();" in HEADER
    assert "hash_combine(hash, static_cast<usize>(backend_));" in SOURCE


def test_hot_dispatch_selects_matching_network_accumulator_pairs():
    for function in ("evaluate", "evaluate_raw", "trace_evaluate"):
        assert f"AnyNetwork::{function}" in HEADER
    assert HEADER.count("assert(backend_ == accumulator.backend_);") == 3
    assert "storage_.legacy.evaluate_raw" in HEADER
    assert "storage_.atomicV2.evaluate_raw" in HEADER
    assert "storage_.legacy.trace_evaluate" in HEADER
    assert "storage_.atomicV2.trace_evaluate" in HEADER


def test_engine_and_workers_only_store_backend_agnostic_facades():
    paths = (
        "src/engine.h",
        "src/search.h",
        "src/evaluate.h",
        "src/evaluate.cpp",
        "src/nnue/nnue_misc.h",
        "src/nnue/nnue_misc.cpp",
    )
    sources = {
        relative: (ROOT / relative).read_text(encoding="utf-8")
        for relative in paths
    }

    for contents in sources.values():
        assert "Eval::NNUE::Network" not in contents
        assert not re.search(r"\bNNUE::Network\b", contents)
        assert "AccumulatorStack" not in contents
        assert "AccumulatorCaches" not in contents

    engine = sources["src/engine.h"]
    search = sources["src/search.h"]
    assert "LazyNumaReplicatedSystemWide<Eval::NNUE::AnyNetwork> network;" in engine
    assert "LazyNumaReplicatedSystemWide<Eval::NNUE::AnyNetwork>& network;" in search
    assert "Eval::NNUE::AnyAccumulator accumulator;" in search
    assert "refreshTable" not in search
    assert "accumulatorStack" not in search


def test_reload_is_quiescent_candidate_first_transactional_and_rebinds_workers():
    engine = (ROOT / "src" / "engine.cpp").read_text(encoding="utf-8")
    load = compact(
        function_body(engine, "void Engine::load_network", "void Engine::save_network")
    )
    save = compact(
        function_body(engine, "void Engine::save_network", "// utility functions")
    )

    assert load.index("wait_for_search_finished();") < load.index(
        "auto candidate = make_unique_large_page<NN::AnyNetwork>();"
    )
    assert "NN::EvalFile candidateFile{std::nullopt, \"\"};" in load
    assert "if (!candidate->load(binaryDirectory, file, candidateFile)) return;" in load
    assert load.index("network = std::move(candidate);") < load.index(
        "networkFile = std::move(candidateFile);"
    )
    assert load.index("networkFile = std::move(candidateFile);") < load.index(
        "threads.clear();"
    )
    assert load.index("threads.clear();") < load.index(
        "threads.ensure_network_replicated();"
    )
    assert "modify_and_replicate" not in load

    assert "wait_for_search_finished();" in save
    assert "network->save(networkFile, file);" in save
    assert "modify_and_replicate" not in save


def test_wasm_fallback_adopts_the_validated_network_allocation():
    engine = (ROOT / "src" / "engine.cpp").read_text(encoding="utf-8")
    numa = (ROOT / "src" / "numa.h").read_text(encoding="utf-8")
    shm = (ROOT / "src" / "shm.h").read_text(encoding="utf-8")

    assert engine.count("make_unique_large_page<NN::AnyNetwork>()") == 2
    assert "prepare_replicate_from(LargePagePtr<T>&& source)" in numa
    assert "SystemWideSharedConstant<T>(std::move(source), get_discriminator(0))" in numa
    assert "SharedMemoryBackendFallback(const std::string&, LargePagePtr<T>&& value)" in shm
    assert "fallback_object(std::move(value))" in shm


def test_failed_load_keeps_live_metadata_and_reports_both_accepted_backends():
    dispatcher = compact(SOURCE)
    assert "EvalFile candidateFile{std::nullopt, \"\"};" in dispatcher
    assert "if (candidateFile.current != requested) return false;" in dispatcher
    assert "evalFile = std::move(candidateFile);" in dispatcher
    assert "compatible Legacy Atomic V1 or AtomicNNUEV2" in SOURCE
    assert "NNUE evaluation using AtomicNNUEV2" in SOURCE
    assert '"Legacy Atomic V1 "' in SOURCE


def test_v2_evaluation_does_not_reuse_legacy_calibration():
    evaluation = compact(
        without_cpp_comments(
            (ROOT / "src" / "evaluate.cpp").read_text(encoding="utf-8")
        )
    )
    assert "network.backend() == NNUE::NetworkBackend::AtomicNNUEV2" in evaluation
    v2 = function_body(
        evaluation,
        "else if (network.backend() == NNUE::NetworkBackend::AtomicNNUEV2)",
        "else { const int deltaNpm",
    )
    assert "rawPsqt + rawPositional" in v2
    assert "fix_frc(pos)" in v2
    assert "damp_for_atomic_rule50(v, pos)" in v2
    for legacy_only in ("entertainment", "legacyNpm", "LegacyAtomicRoyalValue"):
        assert legacy_only not in v2


def test_worker_rebind_does_not_keep_a_concrete_replica_pointer():
    search = compact((ROOT / "src" / "search.cpp").read_text(encoding="utf-8"))

    assert "accumulator(network[token])" in search
    assert "accumulator.rebind(network[numaAccessToken]);" in search
    assert "Eval::evaluate(network[numaAccessToken], pos, accumulator," in search
    assert "&network[token]" not in search
    assert "&network[numaAccessToken]" not in search


def test_dispatcher_and_both_backend_contracts_are_wired_into_ci():
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    for relative in (
        "tests/python/test_nnue_dispatcher_source.py",
        "tests/python/test_atomic_nnue_v2_contract.py",
        "tests/python/test_create_synthetic_atomic_v2_nnue.py",
        "tests/python/test_atomic_nnue_incremental_wrapper.py",
        "tests/nnue_v2_modes.py",
        "tests/nnue_v3_dispatch_reject.py",
        "tests/python/test_atomic_v3_wire_reference.py",
        "tests/python/test_create_synthetic_atomic_v3_nnue.py",
        "tests/python/test_atomic_v3_incremental_oracle.py",
        "tests/atomic_v3_incremental_differential.py",
        "tests/python/test_atomic_v3_incremental_stress_wrapper.py",
        "tests/atomic_v3_incremental_stress.py",
    ):
        assert relative in workflow


def test_v2_ci_covers_scalar_bmi2_avx2_and_authenticates_perft_network():
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    atomic_gate = (ROOT / "tests" / "atomic.sh").read_text(encoding="utf-8")

    assert "arch: [general-64, x86-64-bmi2, x86-64-avx2]" in workflow
    assert "NNUE evaluation using AtomicNNUEV2" in atomic_gate
    assert "go nodes 1" in atomic_gate
    assert atomic_gate.index("go nodes 1") < atomic_gate.index("for eval_mode in")


def test_v3_wire_permutation_policies_are_isolated_and_ci_forces_each_path():
    wire_io = (ROOT / "src" / "nnue" / "atomic_v3" / "wire_io.h").read_text(
        encoding="utf-8"
    )
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )

    for override in (
        "ATOMIC_V3_WIRE_TEST_FORCE_IDENTITY",
        "ATOMIC_V3_WIRE_TEST_FORCE_AVX2_LASX",
        "ATOMIC_V3_WIRE_TEST_FORCE_AVX512",
    ):
        assert f"defined({override})" in wire_io
    assert "wire test permutation overrides are mutually exclusive" in wire_io
    compact_wire_io = compact(wire_io.replace("\\\n", ""))
    assert compact_wire_io.count(
        "#elif defined(ATOMIC_V3_WIRE_TEST_FORCE_AVX512) || "
        "defined(USE_AVX512)"
    ) == 2
    assert compact_wire_io.count(
        "#elif defined(ATOMIC_V3_WIRE_TEST_FORCE_AVX2_LASX) || "
        "defined(USE_AVX2) || defined(USE_LASX)"
    ) == 2
    assert "#elif defined(USE_AVX512)" not in wire_io
    assert "#elif defined(USE_AVX2)" not in wire_io

    assert "ATOMIC_V3_WIRE_TEST_POLICY ?= native" in makefile
    assert (
        "ATOMIC_V3_WIRE_TEST_POLICIES := native identity avx2-lasx avx512"
        in makefile
    )
    assert "Unsupported ATOMIC_V3_WIRE_TEST_POLICY" in makefile
    assert makefile.count("$(ATOMIC_V3_WIRE_TEST_CPPFLAGS)") == 7
    assert "atomic_v3_scalar_backend.o:" in makefile
    assert "atomic_v3_scalar_tests.o:" in makefile
    assert "atomic_v3_incremental_backend.o:" in makefile
    assert "atomic_v3_incremental_tests.o:" in makefile
    assert "atomic_v3_incremental_stress_tests.o:" in makefile
    assert (
        "ATOMIC_V3_STACK_GUARD_FLAGS = "
        "$(if $(filter gcc mingw,$(COMP)),-Werror=stack-usage=128000)"
        in makefile
    )
    assert makefile.count("$(ATOMIC_V3_STACK_GUARD_FLAGS)") == 3
    assert (
        "atomic-v3-incremental-tests: config-sanity "
        "$(ATOMIC_V3_INCREMENTAL_TEST_EXE) atomic-v3-wire-test-fixture"
        in makefile
    )
    assert (
        "atomic-v3-incremental-stress-tests: config-sanity "
        "$(ATOMIC_V3_INCREMENTAL_STRESS_TEST_EXE) atomic-v3-wire-test-fixture"
        in makefile
    )
    assert "CXXFLAGS += $(ATOMIC_V3_WIRE_TEST_CPPFLAGS)" not in makefile

    assert "nnue-v3-wire-policies:" in workflow
    assert "policy: [identity, avx2-lasx, avx512]" in workflow
    assert "ARCH=general-64" in workflow
    assert "ATOMIC_V3_WIRE_TEST_POLICY=${{ matrix.policy }}" in workflow
    assert "atomic-v3-wire-tests" in workflow
    assert "atomic-v3-scalar-tests" in workflow
    assert "atomic-v3-incremental-tests" in workflow


def test_v3_incremental_ci_covers_every_required_toolchain_and_stays_private():
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    generator = (
        ROOT / "tests" / "create_synthetic_atomic_v3_nnue.py"
    ).read_text(encoding="utf-8")
    jobs = {
        name: workflow_job(workflow, name)
        for name in (
            "native",
            "nnue-v2-simd",
            "nnue-v3-wire-policies",
            "debug",
            "data-generator-windows",
            "sanitizers",
            "nnue-v2-v3-valgrind",
            "python",
        )
    }

    target = "atomic-v3-incremental-tests"
    differential = "tests/atomic_v3_incremental_differential.py"
    fixture = "$RUNNER_TEMP/atomic-v3-wire-v1.nnue"
    target_jobs = (
        "native",
        "nnue-v2-simd",
        "nnue-v3-wire-policies",
        "debug",
        "data-generator-windows",
    )
    for name in target_jobs:
        assert target in jobs[name], f"{name} omitted the incremental runner"
        assert jobs[name].count(target) == 1, f"{name} duplicated the incremental runner"
        assert fixture in jobs[name], f"{name} did not reuse the frozen fixture"

    target_block = makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
    assert f"../{differential}" in target_block
    assert target_block.count(differential) == 1
    assert '--oracle "./$(ATOMIC_V3_INCREMENTAL_TEST_EXE)"' in target_block
    assert '--fixture "$(ATOMIC_NNUE_V3_TEST_NET)"' in target_block

    instrumented_oracles = {
        "sanitizers": "--oracle src/atomic-v3-incremental-tests.bin",
        "nnue-v2-v3-valgrind": (
            '--oracle "$RUNNER_TEMP/atomic-v3-incremental-valgrind"'
        ),
    }
    for name, oracle_argument in instrumented_oracles.items():
        assert target in jobs[name], f"{name} omitted the incremental runner"
        assert differential in jobs[name], f"{name} omitted the differential"
        assert jobs[name].count(differential) == 1
        assert fixture in jobs[name], f"{name} did not reuse the frozen fixture"
        assert f'--fixture "{fixture}"' in jobs[name]
        assert oracle_argument in jobs[name]
    assert workflow.count(differential) == len(instrumented_oracles)

    assert "- compiler: GCC" in jobs["native"]
    assert "- compiler: Clang" in jobs["native"]
    assert "if: matrix.arch == 'x86-64-avx2'" in jobs["nnue-v2-simd"]
    assert "policy: [identity, avx2-lasx, avx512]" in jobs[
        "nnue-v3-wire-policies"
    ]
    assert "debug=yes optimize=no" in jobs["debug"]
    assert "COMP=mingw" in jobs["data-generator-windows"]
    assert "sanitizer: address,undefined" in jobs["sanitizers"]
    assert "sanitizer: thread" in jobs["sanitizers"]
    assert "atomic-v3-incremental-valgrind" in jobs["nnue-v2-v3-valgrind"]
    assert "--error-exitcode=99" in jobs["nnue-v2-v3-valgrind"]
    assert "tests/python/test_atomic_v3_incremental_oracle.py" in jobs["python"]

    for name in ("native", "data-generator-windows"):
        rejection = "tests/nnue_v3_dispatch_reject.py"
        assert rejection in jobs[name]
        assert jobs[name].index(target) < jobs[name].index(rejection)

    assert "EXPECTED_SIZE: Optional[int] = 77_349_879" in generator
    assert (
        'EXPECTED_SHA256 = '
        '"00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2"'
        in generator
    )


def test_v3_incremental_sources_remain_outside_every_production_build_graph():
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    setup = (ROOT / "setup.py").read_text(encoding="utf-8")
    makefile_js = (ROOT / "src" / "Makefile_js").read_text(encoding="utf-8")
    wasm_build = (
        ROOT / "tests" / "wasm-engine" / "build.ps1"
    ).read_text(encoding="utf-8")
    wasm_builder = (
        ROOT / "tests" / "wasm-engine" / "build.py"
    ).read_text(encoding="utf-8")

    source = "nnue/atomic_v3/incremental_backend.cpp"
    header = "nnue/atomic_v3/incremental_backend.h"
    stress_source = "nnue/atomic_v3/tests/incremental_stress.cpp"
    production_sources = makefile.split("SRCS =", 1)[1].split("OTHER_SRCS =", 1)[0]
    production_headers = makefile.split("HEADERS =", 1)[1].split("OBJS =", 1)[0]
    assert source not in production_sources
    assert header not in production_headers
    assert stress_source not in production_sources

    for production_graph in (setup, makefile_js, wasm_build, wasm_builder):
        assert source not in production_graph
        assert header not in production_graph
        assert stress_source not in production_graph

    assert f"ATOMIC_V3_INCREMENTAL_SRCS = {source}" in makefile
    assert f"ATOMIC_V3_INCREMENTAL_HEADERS = {header}" in makefile
    assert f"ATOMIC_V3_INCREMENTAL_STRESS_SRCS = {stress_source}" in makefile
    assert "atomic-v3-incremental-tests:" in makefile
    assert "atomic-v3-incremental-stress-tests:" in makefile
    assert makefile.count(source) == 2
    assert makefile.count(header) == 1
    assert makefile.count(stress_source) == 2


def test_v3_incremental_stress_is_cross_platform_instrumented_and_private():
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    wrapper = (ROOT / "tests" / "atomic_v3_incremental_stress.py").read_text(
        encoding="utf-8"
    )

    target = "atomic-v3-incremental-stress-tests"
    script = "tests/atomic_v3_incremental_stress.py"
    fixture = "$RUNNER_TEMP/atomic-v3-wire-v1.nnue"
    for name in (
        "native",
        "nnue-v2-simd",
        "nnue-v3-wire-policies",
        "debug",
        "data-generator-windows",
    ):
        job = workflow_job(workflow, name)
        assert target in job, f"{name} omitted the private stress gate"
        assert job.count(target) == 1, f"{name} duplicated the private stress gate"
        assert fixture in job

    release = workflow_job(workflow, "nnue-v3-incremental-stress")
    assert "compiler: GCC" in release
    assert "compiler: Clang" in release
    assert target + ".bin" in release
    assert script in release
    assert "--mode smoke" in release
    assert "--threads 2" in release
    assert "--mode release" in release
    assert "fixture size mismatch" in release
    assert "fixture SHA-256 mismatch" in release
    assert "flip_first_byte" in release
    assert "atomic-v3-corrupt.nnue" not in release
    assert "--operations --full-refresh-interval --threads" in release

    sanitizers = workflow_job(workflow, "sanitizers")
    assert target + ".bin" in sanitizers
    assert script in sanitizers
    assert '--threads 4' in sanitizers
    assert "sanitizer: address,undefined" in sanitizers
    assert "sanitizer: thread" in sanitizers

    valgrind = workflow_job(workflow, "atomic-v3-incremental-stress-valgrind")
    assert target + ".bin" in valgrind
    assert script in valgrind
    assert "--error-exitcode=99" in valgrind
    assert "--operations 256" in valgrind

    python_job = workflow_job(workflow, "python")
    assert "tests/python/test_atomic_v3_incremental_stress_wrapper.py" in python_job

    target_block = makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
    assert "../" + script in target_block
    assert 'ATOMIC_V3_INCREMENTAL_STRESS_TEST_EXE' in target_block
    assert '--mode smoke' in target_block
    assert "Profile(4_096, 1, 1" in wrapper
    assert "Profile(65_536, 1, 8" in wrapper
    assert "Profile(1_048_576, 1, 8" in wrapper

    combined = "\n".join((release, sanitizers, valgrind, target_block))
    for forbidden in ("OpenBench", "variantfishtest", "training"):
        assert forbidden not in combined


def test_v3_incremental_stress_freezes_real_incremental_and_atomic_coverage():
    source = (
        ROOT / "src" / "nnue" / "atomic_v3" / "tests" / "incremental_stress.cpp"
    ).read_text(encoding="utf-8")

    for marker in (
        "append_incremental_signature",
        "counters_advanced_by",
        "successful evaluation published impossible HM/relation accounting",
        "std::array<DirectedMoveFixture, DirectedMoveCount>",
        '"max-nine-piece-blast"',
        '"white-max-en-passant-blast"',
        '"black-max-en-passant-blast"',
        '"capture-promotion-n"',
        '"black-capture-promotion-n"',
        '"capture-promotion-missing-white"',
        '"white-king-mirror-crossing"',
        '"material-bucket-crossing"',
        "do_null_move",
        "IncrementalStack::MaxSize - 1",
        "IncrementalFaultPoint::AfterFirstPerspective",
        "IncrementalFaultPoint::BeforeComposition",
        "IncrementalFaultPoint::AfterCompositionBeforeCommit",
        "IncrementalError::NetworkMismatch",
        "FullRefreshError::TooManyPiecesPerColor",
        '"directed-too-many-black-pieces"',
        "read_authenticated_fixture",
        "load_authenticated_fixture(fixtureBytes)",
        "std::vector<std::thread>",
        "maxSourceDistance == IncrementalStack::MaxSize - 1",
    ):
        assert marker in source

    assert source.count("run_castling_fixture(network, false") == 4
    assert source.count("run_castling_fixture(network, true") == 11
    assert source.count("load_authenticated_fixture(fixtureBytes)") == 2
    assert "DirectedMoveCount            = 32" in source
    assert "DirectedCaptureCount         = 23" in source
    assert "hmRefreshes > 0 && totals.hmDeltas > 0 && totals.hmReuses > 0" in source
    assert "snapshotMismatches >= 2 && totals.epSquareMismatches >= 2" in source


def test_v2_convenience_targets_generate_an_ignored_local_fixture():
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "ATOMIC_NNUE_V2_TEST_NET ?= ../build/atomic-v2-diagnostic-dense.nnue" in makefile
    assert "atomic-v2-test-fixture:" in makefile
    assert "--output \"$(ATOMIC_NNUE_V2_TEST_NET)\"" in makefile
    assert "atomic-v2-backend-core-tests*" in gitignore
    assert "atomic-v2-incremental-tests*" in gitignore
    assert "/src/build/" in gitignore
