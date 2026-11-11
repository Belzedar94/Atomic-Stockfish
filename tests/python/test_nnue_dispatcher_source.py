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
    assert "atomic_v3" not in HEADER
    assert "AtomicV3" not in SOURCE

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


def test_wasm_dual_backend_gate_repeats_churn_in_one_fixed_heap():
    gate = (ROOT / "tests/wasm-engine/run-dual-backend-tests.mjs").read_text(
        encoding="utf-8"
    )
    assert "argument('--reload-cycles', '3')" in gate
    assert "Number.isInteger(reloadCycles) && reloadCycles >= 3" in gate
    assert "cycle <= reloadCycles" in gate
    assert "for (const threads of [1, 4, 2, 4])" in gate
    assert "reloadCycles=${reloadCycles}" in gate


def test_failed_load_keeps_live_metadata_and_reports_all_accepted_backends():
    dispatcher = compact(SOURCE)
    assert "EvalFile candidateFile{std::nullopt, \"\"};" in dispatcher
    assert "if (candidateFile.current != requested) return false;" in dispatcher
    assert "evalFile = std::move(candidateFile);" in dispatcher
    assert "compatible Legacy Atomic V1 or AtomicNNUEV2 " in SOURCE
    assert "NNUE evaluation using AtomicNNUEV2" in SOURCE
    assert '"Legacy Atomic V1 "' in SOURCE


def test_relative_evalfile_probes_all_backends_before_the_next_path():
    dispatcher = compact(SOURCE)
    assert "requested_candidates(rootDirectory, evalfilePath)" in dispatcher
    loop = function_body(
        dispatcher,
        "bool AnyNetwork::load(",
        "bool AnyNetwork::load_authenticated(",
    )
    candidate_loop = loop[loop.index("for (const fs::path& candidate") :]
    v2 = candidate_loop.index("AtomicV2::load_candidate(v2Stream")
    legacy = candidate_loop.index("storage_.legacy.load_authenticated(")
    assert v2 < legacy
    workflow = (ROOT / ".github/workflows/atomic.yml").read_text(encoding="utf-8")
    assert workflow.count('--v2-net "$RUNNER_TEMP/atomic-v2.nnue"') >= 3
    windows = workflow_job(workflow, "data-generator-windows")
    create_v2 = (
        'python tests/create_synthetic_atomic_v2_nnue.py --output '
        '"$RUNNER_TEMP/atomic-v2.nnue"'
    )
    assert windows.count(create_v2) == 1


def test_modern_v2_evaluation_does_not_reuse_legacy_calibration():
    evaluation = compact(
        without_cpp_comments(
            (ROOT / "src" / "evaluate.cpp").read_text(encoding="utf-8")
        )
    )
    assert "network.backend() == NNUE::NetworkBackend::AtomicNNUEV2" in evaluation
    assert "AtomicNNUEV3" not in evaluation
    v2 = function_body(
        evaluation,
        "else if (network.backend() == NNUE::NetworkBackend::AtomicNNUEV2)",
        "else { const int deltaNpm",
    )
    assert "Detail::atomic_nnue_value_from_raw(rawPsqt, rawPositional)" in v2
    assert "fix_frc(pos)" in v2
    assert "damp_for_atomic_rule50(v, pos)" in v2
    for legacy_only in ("entertainment", "legacyNpm", "LegacyAtomicRoyalValue"):
        assert legacy_only not in v2


def test_search_and_trace_share_wide_clamped_nnue_conversion():
    evaluation = compact(
        without_cpp_comments(
            (ROOT / "src" / "evaluate.cpp").read_text(encoding="utf-8")
        )
    )
    helpers = function_body(
        evaluation,
        "Value Eval::Detail::atomic_nnue_value_from_scaled",
        "Value Eval::evaluate",
    )
    trace = evaluation[evaluation.index("std::string Eval::trace") :]

    call = "Detail::atomic_nnue_value_from_raw(rawPsqt, rawPositional)"
    assert evaluation.count(call) == 3
    assert call in trace
    assert "i64(rawPsqt) + i64(rawPositional)" in helpers
    assert "std::clamp(scaled, Minimum, Maximum)" in helpers
    assert "return atomic_nnue_value_from_scaled(scaled);" in helpers
    assert "rawPsqt + rawPositional" not in trace

    misc = compact(
        (ROOT / "src/nnue/nnue_misc.cpp").read_text(encoding="utf-8")
    )
    assert "format_cp_aligned_dot(t.total[bucket], ss, pos);" in misc


def test_worker_rebind_does_not_keep_a_concrete_replica_pointer():
    search = compact((ROOT / "src" / "search.cpp").read_text(encoding="utf-8"))

    assert "accumulator(network[token])" in search
    assert "accumulator.rebind(network[numaAccessToken]);" in search
    assert "Eval::evaluate(network[numaAccessToken], pos, accumulator," in search
    assert "&network[token]" not in search
    assert "&network[numaAccessToken]" not in search


def test_dispatcher_and_all_backend_contracts_are_wired_into_ci():
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    for relative in (
        "tests/python/test_nnue_dispatcher_source.py",
        "tests/python/test_atomic_nnue_v2_contract.py",
        "tests/python/test_create_synthetic_atomic_v2_nnue.py",
        "tests/python/test_atomic_nnue_incremental_wrapper.py",
        "tests/nnue_v2_modes.py",
        "atomic-nnue-incremental-tests",
    ):
        assert relative in workflow
    assert "v3" not in workflow.lower().replace("gplv3", "")


def test_dual_backend_ci_covers_scalar_bmi2_avx2_and_authenticates_supported_networks():
    workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    atomic_gate = (ROOT / "tests" / "atomic.sh").read_text(encoding="utf-8")
    xboard_gate = (ROOT / "tests" / "xboard_protocol.py").read_text(encoding="utf-8")
    bench_gate = (ROOT / "tests" / "atomic_bench.py").read_text(encoding="utf-8")

    assert "arch: [general-64, x86-64-bmi2, x86-64-avx2]" in workflow
    assert "Legacy Atomic V1|AtomicNNUEV2)" in atomic_gate
    assert "supported Atomic NNUE backend" in atomic_gate
    for backend in ("Legacy Atomic V1", "AtomicNNUEV2"):
        assert f'"NNUE evaluation using {backend}"' in xboard_gate
    assert "requested supported Atomic NNUE" in xboard_gate
    assert '0xA70C0002: "AtomicNNUEV2"' in bench_gate
    assert 'marker = f"NNUE evaluation using {backend}"' in bench_gate
    assert "go nodes 1" in atomic_gate
    assert atomic_gate.index("go nodes 1") < atomic_gate.index("for eval_mode in")


def test_v2_convenience_targets_generate_an_ignored_local_fixture():
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "ATOMIC_NNUE_V2_TEST_NET ?= ../build/atomic-v2-diagnostic-dense.nnue" in makefile
    assert "atomic-v2-test-fixture:" in makefile
    assert "--output \"$(ATOMIC_NNUE_V2_TEST_NET)\"" in makefile
    assert "atomic-v2-backend-core-tests*" in gitignore
    assert "atomic-v2-incremental-tests*" in gitignore
    assert "/src/build/" in gitignore
