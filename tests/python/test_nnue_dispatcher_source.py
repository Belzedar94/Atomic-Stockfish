import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DISPATCHER = (ROOT / "src" / "nnue" / "nnue_dispatcher.h").read_text(
    encoding="utf-8"
)


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def function_body(source: str, signature: str, next_signature: str) -> str:
    start = source.index(signature)
    end = source.index(next_signature, start)
    return source[start:end]


def test_single_backend_facade_is_inline_and_has_no_runtime_dispatch_state():
    dispatcher = compact(DISPATCHER)
    assert "enum class NetworkBackend : u8 { LegacyAtomicV1 };" in dispatcher
    assert "inline constexpr usize NetworkBackendCount = 1;" in dispatcher
    assert "static_assert(NetworkBackendCount == 1);" in dispatcher
    assert "LegacyAtomicV1::Network legacy_;" in DISPATCHER
    assert "LegacyAtomicV1::AccumulatorStack  stack_;" in DISPATCHER
    assert "LegacyAtomicV1::AccumulatorCaches caches_;" in DISPATCHER

    for forbidden in (
        "std::variant",
        "virtual ",
        "NetworkBackend backend_",
        "AnyNetwork*",
        "AnyAccumulator*",
        "void (*",
    ):
        assert forbidden not in DISPATCHER


def test_facade_freezes_the_legacy_shared_memory_contract():
    for contract in (
        "AnyNetwork::backend() == NetworkBackend::LegacyAtomicV1",
        "sizeof(AnyNetwork) == sizeof(LegacyAtomicV1::Network)",
        "alignof(AnyNetwork) == alignof(LegacyAtomicV1::Network)",
        "std::is_trivially_copyable_v<AnyNetwork>",
        "std::is_trivially_copy_constructible_v<AnyNetwork>",
        "std::is_trivially_move_constructible_v<AnyNetwork>",
        "std::is_trivially_destructible_v<AnyNetwork>",
    ):
        assert contract in DISPATCHER

    assert "struct std::hash<Stockfish::Eval::NNUE::AnyNetwork>" in DISPATCHER
    assert "return network.get_content_hash();" in DISPATCHER


def test_engine_and_workers_only_store_the_backend_agnostic_facades():
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
        assert "NNUE::Network" not in contents
        assert "AccumulatorStack" not in contents
        assert "AccumulatorCaches" not in contents

    engine = sources["src/engine.h"]
    search = sources["src/search.h"]
    assert "LazyNumaReplicatedSystemWide<Eval::NNUE::AnyNetwork> network;" in engine
    assert "LazyNumaReplicatedSystemWide<Eval::NNUE::AnyNetwork>& network;" in search
    assert "Eval::NNUE::AnyAccumulator accumulator;" in search
    assert "refreshTable" not in search
    assert "accumulatorStack" not in search


def test_reload_is_quiescent_transactional_and_rebinds_every_worker():
    engine = (ROOT / "src" / "engine.cpp").read_text(encoding="utf-8")
    load = compact(
        function_body(engine, "void Engine::load_network", "void Engine::save_network")
    )
    save = compact(
        function_body(engine, "void Engine::save_network", "// utility functions")
    )

    assert load.index("wait_for_search_finished();") < load.index(
        "network.modify_and_replicate("
    )
    assert "(NN::AnyNetwork& network_)" in load
    assert load.index("network.modify_and_replicate(") < load.index("threads.clear();")
    assert load.index("threads.clear();") < load.index(
        "threads.ensure_network_replicated();"
    )

    assert "wait_for_search_finished();" in save
    assert "network->save(networkFile, file);" in save
    assert "modify_and_replicate" not in save


def test_worker_rebind_does_not_keep_a_concrete_replica_pointer():
    search = compact((ROOT / "src" / "search.cpp").read_text(encoding="utf-8"))

    assert "accumulator(network[token])" in search
    assert "accumulator.rebind(network[numaAccessToken]);" in search
    assert "Eval::evaluate(network[numaAccessToken], pos, accumulator," in search
    assert "&network[token]" not in search
    assert "&network[numaAccessToken]" not in search
