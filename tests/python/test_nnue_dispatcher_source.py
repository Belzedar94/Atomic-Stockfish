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
        function_body(
            engine,
            "void Engine::load_network",
            "bool Engine::load_authenticated_network",
        )
    )
    authenticated = compact(
        function_body(
            engine,
            "bool Engine::load_authenticated_network",
            "void Engine::save_network",
        )
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

    assert authenticated.index("wait_for_search_finished();") < authenticated.index(
        "auto candidate = make_unique_large_page<NN::AnyNetwork>();"
    )
    assert "NN::EvalFile candidateFile{std::nullopt, \"\"};" in authenticated
    assert (
        "if (!candidate->load_authenticated(stream, logicalPath, candidateFile)) "
        "return false;"
    ) in authenticated
    assert authenticated.index("network = std::move(candidate);") < authenticated.index(
        "networkFile = std::move(candidateFile);"
    )
    assert authenticated.index("networkFile = std::move(candidateFile);") < (
        authenticated.index("threads.clear();")
    )
    assert authenticated.index("threads.clear();") < authenticated.index(
        "threads.ensure_network_replicated();"
    )
    assert "modify_and_replicate" not in authenticated

    assert "wait_for_search_finished();" in save
    assert "network->save(networkFile, file);" in save
    assert "modify_and_replicate" not in save


def test_wasm_fallback_adopts_the_validated_network_allocation():
    engine = (ROOT / "src" / "engine.cpp").read_text(encoding="utf-8")
    numa = (ROOT / "src" / "numa.h").read_text(encoding="utf-8")
    shm = (ROOT / "src" / "shm.h").read_text(encoding="utf-8")

    assert engine.count("make_unique_large_page<NN::AnyNetwork>()") == 3
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
        "tests/python/test_atomic_v3_simd_wrapper.py",
        "tests/python/test_atomic_v3_simd_benchmark.py",
        "tests/atomic_v3_simd_differential.py",
    ):
        assert relative in workflow


def test_dual_backend_ci_covers_scalar_bmi2_avx2_and_authenticates_supported_networks():
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    atomic_gate = (ROOT / "tests" / "atomic.sh").read_text(encoding="utf-8")
    xboard_gate = (ROOT / "tests" / "xboard_protocol.py").read_text(encoding="utf-8")

    assert "arch: [general-64, x86-64-bmi2, x86-64-avx2]" in workflow
    assert "Legacy Atomic V1|AtomicNNUEV2" in atomic_gate
    assert "supported Atomic NNUE backend" in atomic_gate
    for backend in ("Legacy Atomic V1", "AtomicNNUEV2"):
        assert f'"NNUE evaluation using {backend}"' in xboard_gate
    assert "requested supported Atomic NNUE" in xboard_gate
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
    wire_policy_objects = (
        "atomic_v3_wire_network.o",
        "atomic_v3_wire_tests.o",
        "atomic_v3_scalar_backend.o",
        "atomic_v3_scalar_tests.o",
        "atomic_v3_incremental_backend.o",
        "atomic_v3_incremental_tests.o",
        "atomic_v3_incremental_stress_tests.o",
        "atomic_v3_incremental_simd_kernel_tests.o",
        "atomic_v3_incremental_simd_benchmark.o",
        "atomic_v3_incremental_simd_kernels.o",
        "atomic_v3_simd_isa.o",
        "atomic_v3_simd_backend.o",
        "atomic_v3_simd_tests.o",
    )
    for object_name in wire_policy_objects:
        rule = makefile.split(f"{object_name}:", 1)[1].split("\n\n", 1)[0]
        assert "$(ATOMIC_V3_WIRE_TEST_CPPFLAGS)" in rule
    assert (
        "ATOMIC_V3_STACK_GUARD_FLAGS = "
        "$(if $(filter gcc mingw,$(COMP)),-Werror=stack-usage=128000)"
        in makefile
    )
    stack_guard_objects = (
        "atomic_v3_scalar_backend.o",
        "atomic_v3_incremental_backend.o",
        "atomic_v3_incremental_stress_tests.o",
        "atomic_v3_incremental_simd_kernel_tests.o",
        "atomic_v3_incremental_simd_benchmark.o",
        "atomic_v3_incremental_simd_kernels.o",
        "atomic_v3_simd_isa.o",
        "atomic_v3_simd_backend.o",
        "atomic_v3_simd_tests.o",
    )
    for object_name in stack_guard_objects:
        rule = makefile.split(f"{object_name}:", 1)[1].split("\n\n", 1)[0]
        assert "$(ATOMIC_V3_STACK_GUARD_FLAGS)" in rule
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

    policies = workflow_job(workflow, "nnue-v3-wire-policies")
    assert "policy: [identity, avx2-lasx, avx512]" in policies
    assert policies.count("ARCH=x86-64-avx2") == 5
    assert "ARCH=general-64" not in policies
    assert "ATOMIC_V3_WIRE_TEST_POLICY=${{ matrix.policy }}" in policies
    assert "ATOMIC_V3_SIMD_REQUIRED_ISA=avx2" in policies
    assert "atomic-v3-wire-tests" in policies
    assert "atomic-v3-scalar-tests" in policies
    assert "atomic-v3-incremental-simd-tests" in policies
    assert "atomic-v3-incremental-simd-stress-tests" in policies


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
    dedicated_simd = workflow_job(workflow, "nnue-v3-incremental-simd")
    assert differential in dedicated_simd
    assert workflow.count(differential) == len(instrumented_oracles) + 1

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
    kernel_source = "nnue/atomic_v3/incremental_simd_kernels.cpp"
    kernel_header = "nnue/atomic_v3/incremental_simd_kernels.h"
    kernel_runner = "nnue/atomic_v3/tests/incremental_simd_kernel_core.cpp"
    benchmark_runner = "nnue/atomic_v3/tests/incremental_simd_benchmark.cpp"
    production_sources = makefile.split("SRCS =", 1)[1].split("OTHER_SRCS =", 1)[0]
    production_headers = makefile.split("HEADERS =", 1)[1].split("OBJS =", 1)[0]
    assert source not in production_sources
    assert header not in production_headers
    assert stress_source not in production_sources
    assert kernel_source not in production_sources
    assert kernel_header not in production_headers
    assert kernel_runner not in production_sources
    assert benchmark_runner not in production_sources

    for production_graph in (setup, makefile_js, wasm_build, wasm_builder):
        assert source not in production_graph
        assert header not in production_graph
        assert stress_source not in production_graph
        assert kernel_source not in production_graph
        assert kernel_header not in production_graph
        assert kernel_runner not in production_graph
        assert benchmark_runner not in production_graph

    incremental_sources = makefile.split("ATOMIC_V3_INCREMENTAL_SRCS =", 1)[1].split(
        "ATOMIC_V3_INCREMENTAL_HEADERS =", 1
    )[0]
    incremental_headers = makefile.split("ATOMIC_V3_INCREMENTAL_HEADERS =", 1)[1].split(
        "ATOMIC_V3_INCREMENTAL_DEPS =", 1
    )[0]
    assert source in incremental_sources
    assert header in incremental_headers
    assert f"ATOMIC_V3_INCREMENTAL_STRESS_SRCS = {stress_source}" in makefile
    assert f"ATOMIC_V3_INCREMENTAL_SIMD_KERNEL_SRCS = {kernel_runner}" in makefile
    assert f"ATOMIC_V3_INCREMENTAL_SIMD_BENCHMARK_SRCS = {benchmark_runner}" in makefile
    assert "atomic-v3-incremental-tests:" in makefile
    assert "atomic-v3-incremental-stress-tests:" in makefile
    assert "atomic-v3-incremental-simd-kernel-tests:" in makefile
    assert "atomic-v3-incremental-simd-tests:" in makefile
    assert "atomic-v3-incremental-simd-stress-tests:" in makefile
    assert "atomic-v3-incremental-simd-benchmark:" in makefile
    assert makefile.count(source) == 2
    assert makefile.count(header) == 1
    assert makefile.count(stress_source) == 2
    assert makefile.count(kernel_source) == 2
    assert makefile.count(kernel_header) == 1
    assert makefile.count(kernel_runner) == 2
    assert makefile.count(benchmark_runner) == 2


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
        "debug",
        "data-generator-windows",
    ):
        job = workflow_job(workflow, name)
        assert target in job, f"{name} omitted the private stress gate"
        assert job.count(target) == 1, f"{name} duplicated the private stress gate"
        assert fixture in job

    wire_policies = workflow_job(workflow, "nnue-v3-wire-policies")
    assert "atomic-v3-incremental-simd-stress-tests" in wire_policies

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

    assert source.count("run_castling_fixture(network, options.requiredIsa, false") == 4
    assert source.count("run_castling_fixture(network, options.requiredIsa, true") == 11
    assert source.count("load_authenticated_fixture(fixtureBytes)") == 2
    assert "DirectedMoveCount            = 32" in source
    assert "DirectedCaptureCount         = 23" in source
    assert "hmRefreshes > 0 && totals.hmDeltas > 0 && totals.hmReuses > 0" in source
    assert "snapshotMismatches >= 2 && totals.epSquareMismatches >= 2" in source


def test_v3_full_refresh_simd_ci_executes_real_isas_and_checks_stable_symbols():
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    target = "atomic-v3-simd-tests"
    differential = "tests/atomic_v3_simd_differential.py"
    fixture = "$RUNNER_TEMP/atomic-v3-wire-v1.nnue"

    simd = workflow_job(workflow, "nnue-v3-simd")
    for isa, arch in (
        ("scalar", "general-64"),
        ("sse41", "x86-64-sse41-popcnt"),
        ("avx2", "x86-64-avx2"),
    ):
        assert f"- isa: {isa}\n            arch: {arch}" in simd
    assert (
        "- isa: avx2\n"
        "            arch: x86-64-avx2\n"
        "            compiler: Clang\n"
        "            comp: clang\n"
        "            cxx: clang++"
    ) in simd
    assert simd.count("compiler: GCC") == 3
    assert simd.count("compiler: Clang") == 1
    assert (
        "AtomicNNUEV3 full-refresh SIMD ${{ matrix.compiler }} ${{ matrix.isa }}"
        in simd
    )
    assert "COMP=${{ matrix.comp }} COMPCXX=${{ matrix.cxx }}" in simd
    assert f"--runner src/{target}.bin" in simd
    assert f'--net "{fixture}"' in simd
    assert "--require-isa ${{ matrix.isa }}" in simd
    assert "--timeout 300" in simd
    assert simd.count(differential) == 1
    assert "portable scalar build unexpectedly contains an x86 SIMD kernel" in simd

    stable_symbols = (
        "atomic_v3_add_i16_sse41_kernel",
        "atomic_v3_add_i8_sse41_kernel",
        "atomic_v3_add_i16_avx2_kernel",
        "atomic_v3_add_i8_avx2_kernel",
    )
    assert "objdump -d --disassemble=\"$symbol\"" in simd
    for symbol in stable_symbols:
        assert symbol in simd
    for mnemonic in ("pmovsxwd", "pmovsxbd", "vpmovsxwd", "vpmovsxbd"):
        assert mnemonic in simd

    for job in (
        "native",
        "debug",
        "data-generator-windows",
    ):
        block = workflow_job(workflow, job)
        assert target in block, f"{job} omitted the private SIMD target"
        assert "ATOMIC_V3_SIMD_REQUIRED_ISA=scalar" in block
        assert fixture in block
    policies = workflow_job(workflow, "nnue-v3-wire-policies")
    assert "policy: [identity, avx2-lasx, avx512]" in policies
    assert "ATOMIC_V3_WIRE_TEST_POLICY=${{ matrix.policy }}" in policies
    assert policies.count("ARCH=x86-64-avx2") == 5
    assert "ARCH=general-64" not in policies
    assert "ATOMIC_V3_SIMD_REQUIRED_ISA=avx2" in policies
    assert policies.count(target) == 1

    sanitizers = workflow_job(workflow, "sanitizers")
    assert target + ".bin" in sanitizers
    assert differential in sanitizers
    assert "ARCH=x86-64-sse41-popcnt" in sanitizers
    assert "--require-isa sse41" in sanitizers
    assert "sanitizer: address,undefined" in sanitizers
    assert "sanitizer: thread" in sanitizers
    assert "batch_cases=1" not in sanitizers

    valgrind = workflow_job(workflow, "nnue-v2-v3-valgrind")
    assert target + ".bin" in valgrind
    assert "batch_cases=1" in valgrind
    assert "atomic-v3-simd-one-case.in" in valgrind
    assert "atomic-v3-simd-one-case.out" in valgrind
    assert "--require-isa scalar" in valgrind
    assert "--batch" in valgrind
    assert "--error-exitcode=99" in valgrind
    assert "cases=1 comparisons=1 errors=0" in valgrind

    python_job = workflow_job(workflow, "python")
    assert "tests/python/test_atomic_v3_simd_wrapper.py" in python_job
    assert "tests/python/test_atomic_v3_simd_benchmark.py" in python_job
    assert "tests/atomic_v3_simd_benchmark.py" not in workflow
    assert "--benchmark" not in workflow
    assert "--promotion-gate" not in workflow

    combined = "\n".join((simd, sanitizers))
    for forbidden in (
        "--benchmark",
        "minimum-nps",
        "min_nps",
        "performance threshold",
    ):
        assert forbidden not in combined


def test_v3_full_refresh_simd_target_is_fail_closed_and_private():
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    header = (
        ROOT / "src" / "nnue" / "atomic_v3" / "simd_backend.h"
    ).read_text(encoding="utf-8")
    source = (
        ROOT / "src" / "nnue" / "atomic_v3" / "simd_backend.cpp"
    ).read_text(encoding="utf-8")
    isa_header = (
        ROOT / "src" / "nnue" / "atomic_v3" / "simd_isa.h"
    ).read_text(encoding="utf-8")
    isa_source = (
        ROOT / "src" / "nnue" / "atomic_v3" / "simd_isa.cpp"
    ).read_text(encoding="utf-8")
    runner = (
        ROOT / "src" / "nnue" / "atomic_v3" / "tests" / "simd_core.cpp"
    ).read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    target = "atomic-v3-simd-tests"
    target_block = makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
    assert "$(ATOMIC_V3_SIMD_TEST_EXE) atomic-v3-wire-test-fixture" in target_block
    assert target_block.count("../tests/atomic_v3_simd_differential.py") == 1
    assert '--runner "./$(ATOMIC_V3_SIMD_TEST_EXE)"' in target_block
    assert '--net "$(ATOMIC_NNUE_V3_TEST_NET)"' in target_block
    assert '--require-isa "$(ATOMIC_V3_SIMD_REQUIRED_ISA)"' in target_block
    assert "src/atomic-v3-simd-tests*" in gitignore

    assert '#include "simd_isa.h"' in header
    assert "enum class SimdIsa : u8" in isa_header
    assert "Scalar" in isa_header and "Sse41" in isa_header and "Avx2" in isa_header
    assert "u64 scalarKernelCalls" in header
    assert "fallback_calls(SimdIsa requestedIsa)" in header
    assert "requestedIsa == SimdIsa::Scalar ? 0 : scalarKernelCalls" in header
    assert "case SimdIsa::Scalar" in source
    assert "add_i16_scalar" in source
    assert "add_i8_scalar" in source
    assert "SimdError::UnsupportedIsa" in source
    assert "if (!simd_isa_available(requestedIsa))" in source
    assert "bool simd_isa_available(SimdIsa isa) noexcept" in isa_source
    assert "result.executedIsa  = requestedIsa;" in source
    assert "std::array<FullRefreshEmission, COLOR_NB> emissions{};" in header
    assert "std::array<ScalarHmPerspective, COLOR_NB> completeStates{};" in header
    assert "auto& emissions      = scratch.emissions;" in source
    assert "auto& completeStates = scratch.completeStates;" in source
    assert "SimdDiagnostic candidate{};" not in source
    assert "ScalarDiagnostic   scalar{};" not in source
    assert "ATOMIC_V3_NOINLINE" in source
    for symbol in (
        "atomic_v3_add_i16_sse41_kernel",
        "atomic_v3_add_i8_sse41_kernel",
        "atomic_v3_add_i16_avx2_kernel",
        "atomic_v3_add_i8_avx2_kernel",
    ):
        assert len(re.findall(rf"\bvoid\s+{symbol}\s*\(", source)) == 1

    for marker in (
        "TailProbeCounts{{0, 1, 3, 4, 7, 8, 15, 16, 17}}",
        "run_tail_canary_checks<Source>(isa)",
        'print_csv("tail_counts", TailProbeCounts)',
        '<< "tail_cases=" << TailProbeCounts.size()',
        '<< "tail_canaries=" << TailProbeCounts.size() * 2',
        '<< "tails.exact=1\\n"',
        "void run_concurrency_probe(const Network& network, SimdIsa requiredIsa)",
        "for (const unsigned threadCount : {1U, 2U, 4U, 8U})",
        "std::vector<std::thread> threads",
        "SimdScratch scratch{}",
        "shared immutable Network diverged with independent SIMD scratch objects",
        "run_concurrency_probe(network, requestedIsa);",
    ):
        assert marker in runner
    assert runner.count("run_tail_canary_checks<Source>(isa)") == 1
    assert runner.index("run_concurrency_probe(network, requestedIsa);") < runner.index(
        "while (true)"
    )

    simd_source = "nnue/atomic_v3/simd_backend.cpp"
    simd_header = "nnue/atomic_v3/simd_backend.h"
    isa_source_path = "nnue/atomic_v3/simd_isa.cpp"
    isa_header_path = "nnue/atomic_v3/simd_isa.h"
    runner_source = "nnue/atomic_v3/tests/simd_core.cpp"
    production_sources = makefile.split("SRCS =", 1)[1].split("OTHER_SRCS =", 1)[0]
    production_headers = makefile.split("HEADERS =", 1)[1].split("OBJS =", 1)[0]
    assert simd_source not in production_sources
    assert isa_source_path not in production_sources
    assert runner_source not in production_sources
    assert simd_header not in production_headers
    assert isa_header_path not in production_headers

    production_graphs = (
        (ROOT / "setup.py").read_text(encoding="utf-8"),
        (ROOT / "src" / "Makefile_js").read_text(encoding="utf-8"),
        (ROOT / "tests" / "wasm-engine" / "build.ps1").read_text(
            encoding="utf-8"
        ),
        (ROOT / "tests" / "wasm-engine" / "build.py").read_text(
            encoding="utf-8"
        ),
    )
    for graph in production_graphs:
        assert simd_source not in graph
        assert simd_header not in graph
        assert isa_source_path not in graph
        assert isa_header_path not in graph
        assert runner_source not in graph

    assert makefile.count(simd_source) == 2
    assert makefile.count(simd_header) == 1
    assert makefile.count(runner_source) == 2
    assert makefile.count(isa_source_path) == 3
    assert makefile.count(isa_header_path) == 2
    assert "atomic_v3_simd_isa.o:" in makefile
    assert "atomic_v3_simd_backend.o:" in makefile
    assert "atomic_v3_simd_tests.o:" in makefile


def test_v3_incremental_simd_is_exact_fail_closed_and_cross_platform():
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    kernels = (
        ROOT / "src" / "nnue" / "atomic_v3" / "incremental_simd_kernels.cpp"
    ).read_text(encoding="utf-8")
    backend = (
        ROOT / "src" / "nnue" / "atomic_v3" / "incremental_backend.cpp"
    ).read_text(encoding="utf-8")
    runner = (
        ROOT / "src" / "nnue" / "atomic_v3" / "tests" / "incremental_core.cpp"
    ).read_text(encoding="utf-8")
    kernel_runner = (
        ROOT
        / "src"
        / "nnue"
        / "atomic_v3"
        / "tests"
        / "incremental_simd_kernel_core.cpp"
    ).read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    job = workflow_job(workflow, "nnue-v3-incremental-simd")
    for matrix_entry in (
        "isa: scalar\n            arch: general-64",
        "isa: sse41\n            arch: x86-64-sse41-popcnt",
        "isa: avx2\n            arch: x86-64-avx2",
        "compiler: Clang",
    ):
        assert matrix_entry in job
    assert job.count("- isa: avx2") == 2
    for target in (
        "atomic-v3-incremental-simd-kernel-tests.bin",
        "atomic-v3-incremental-tests.bin",
        "atomic-v3-incremental-stress-tests.bin",
    ):
        assert target in job
    for script in (
        "tests/atomic_v3_incremental_simd_kernel.py",
        "tests/atomic_v3_incremental_differential.py",
        "tests/atomic_v3_incremental_stress.py",
    ):
        assert job.count(script) == 1
    assert job.count("--require-isa ${{ matrix.isa }}") == 3
    assert "fallback" in job.lower()
    assert "objdump -d --disassemble" in job
    assert "pmovsxwd" in job and "pmovsxdq" in job
    assert "p${operation}q" in job

    assert "ATOMIC_V3_INCREMENTAL_SIMD_REQUIRED_ISA ?= scalar" in makefile
    target_expectations = {
        "atomic-v3-incremental-simd-kernel-tests": (
            "../tests/atomic_v3_incremental_simd_kernel.py",
            "ATOMIC_V3_INCREMENTAL_SIMD_KERNEL_TEST_EXE",
        ),
        "atomic-v3-incremental-simd-tests": (
            "../tests/atomic_v3_incremental_differential.py",
            "ATOMIC_V3_INCREMENTAL_TEST_EXE",
        ),
        "atomic-v3-incremental-simd-stress-tests": (
            "../tests/atomic_v3_incremental_stress.py",
            "ATOMIC_V3_INCREMENTAL_STRESS_TEST_EXE",
        ),
    }
    for target, markers in target_expectations.items():
        block = makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
        for marker in markers:
            assert marker in block
        assert '"$(ATOMIC_V3_INCREMENTAL_SIMD_REQUIRED_ISA)"' in block

    assert "src/atomic-v3-incremental-simd-kernel-tests*" in gitignore
    assert "src/atomic-v3-incremental-simd-benchmark*" in gitignore
    for symbol in (
        "atomic_v3_add_i16_i64_sse41_kernel",
        "atomic_v3_sub_i16_i64_sse41_kernel",
        "atomic_v3_add_i16_i64_avx2_kernel",
        "atomic_v3_sub_i16_i64_avx2_kernel",
    ):
        assert len(re.findall(rf"\bSimdIsa\s+{symbol}\s*\(", kernels)) == 1
    assert "!simd_isa_available(isa)" in kernels
    assert "HmDeltaKernelResult" in kernels
    assert "store_wrapped_i64" in kernels
    assert "result.executedIsa == isa" in kernel_runner
    assert "run_wraparound_probes(requiredIsa);" in kernel_runner
    assert "if (hmDeltaExecutionEnabled_ && !simd_isa_available(requestedIsa_))" in backend
    assert "kernelResult.executedIsa != requestedIsa_" in backend
    assert "switch (kernelResult.executedIsa)" in backend
    assert "return {IncrementalError::UnsupportedIsa};" in backend
    assert "run_unsupported_isa_transactionality(*loaded.network);" in runner
    assert "if (!simd_isa_available(isa))" in runner

    python_job = workflow_job(workflow, "python")
    assert "tests/python/test_atomic_v3_incremental_simd_kernel_wrapper.py" in python_job
    assert "tests/python/test_atomic_v3_incremental_simd_benchmark.py" in python_job
    assert "tests/atomic_v3_incremental_simd_benchmark.py" not in workflow

    benchmark_block = makefile.split(
        "atomic-v3-incremental-simd-benchmark:", 1
    )[1].split("\n\n", 1)[0]
    assert "$(ATOMIC_V3_INCREMENTAL_SIMD_BENCHMARK_EXE)" in benchmark_block
    assert "../tests/atomic_v3_incremental_simd_benchmark.py" in benchmark_block
    assert '"$(ATOMIC_V3_INCREMENTAL_SIMD_REQUIRED_ISA)"' in benchmark_block

    wire_policies = workflow_job(workflow, "nnue-v3-wire-policies")
    assert "ATOMIC_V3_INCREMENTAL_SIMD_REQUIRED_ISA=avx2" in wire_policies
    assert "atomic-v3-incremental-simd-tests" in wire_policies
    assert "atomic-v3-incremental-simd-stress-tests" in wire_policies

    windows = workflow_job(workflow, "data-generator-windows")
    sanitizers = workflow_job(workflow, "sanitizers")
    valgrind = workflow_job(workflow, "nnue-v2-v3-valgrind")
    assert "ATOMIC_V3_INCREMENTAL_SIMD_REQUIRED_ISA=scalar" in windows
    assert "--require-isa sse41" in sanitizers
    assert "atomic-v3-incremental-simd-kernel-valgrind" in valgrind
    assert "--require-isa scalar" in valgrind


def test_v2_convenience_targets_generate_an_ignored_local_fixture():
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "ATOMIC_NNUE_V2_TEST_NET ?= ../build/atomic-v2-diagnostic-dense.nnue" in makefile
    assert "atomic-v2-test-fixture:" in makefile
    assert "--output \"$(ATOMIC_NNUE_V2_TEST_NET)\"" in makefile
    assert "atomic-v2-backend-core-tests*" in gitignore
    assert "atomic-v2-incremental-tests*" in gitignore
    assert "/src/build/" in gitignore
