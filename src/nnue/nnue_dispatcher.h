/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef NNUE_DISPATCHER_H_INCLUDED
#define NNUE_DISPATCHER_H_INCLUDED

#include <cassert>
#include <filesystem>
#include <functional>
#include <new>
#include <optional>
#include <string_view>
#include <type_traits>
#include <utility>

#include "../types.h"
#include "atomic_v2/backend.h"
#include "network.h"
#include "nnue_accumulator.h"

namespace Stockfish::Eval::NNUE {

// The released V1 reader, arithmetic, accumulator layout, and serializer stay
// independently named. Loading V2 must never reinterpret or convert V1 bytes.
namespace LegacyAtomicV1 {
using Network           = ::Stockfish::Eval::NNUE::Network;
using AccumulatorStack  = ::Stockfish::Eval::NNUE::AccumulatorStack;
using AccumulatorCaches = ::Stockfish::Eval::NNUE::AccumulatorCaches;
using AccumulatorState  = ::Stockfish::Eval::NNUE::AccumulatorState;
}

enum class NetworkBackend : u8 {
    LegacyAtomicV1,
    AtomicNNUEV2
};
inline constexpr usize NetworkBackendCount = 2;

[[nodiscard]] constexpr std::string_view backend_name(NetworkBackend backend) noexcept {
    switch (backend)
    {
    case NetworkBackend::LegacyAtomicV1 :
        return "Legacy Atomic V1";
    case NetworkBackend::AtomicNNUEV2 :
        return "AtomicNNUEV2";
    }
    return "Unknown Atomic NNUE backend";
}

class AnyAccumulator;

// Both backends remain inline and trivially copyable. This is intentionally a
// tagged union rather than a virtual interface: NUMA replicas may be copied to
// shared memory verbatim and evaluation pays only one predictable branch.
class AnyNetwork {
   public:
    AnyNetwork() = default;

    AnyNetwork(const AnyNetwork&)            = default;
    AnyNetwork(AnyNetwork&&)                 = default;
    AnyNetwork& operator=(const AnyNetwork&) = default;
    AnyNetwork& operator=(AnyNetwork&&)      = default;

    [[nodiscard]] NetworkBackend   backend() const noexcept { return backend_; }
    [[nodiscard]] std::string_view backend_name() const noexcept {
        return NNUE::backend_name(backend_);
    }

    // Load into this candidate object. Engine publishes the object only after
    // success, so a malformed or incompatible file cannot mutate a live NUMA
    // replica or its EvalFile metadata.
    [[nodiscard]] bool load(const std::filesystem::path& rootDirectory,
                            const std::filesystem::path& evalfilePath,
                            EvalFile&                    evalFile);
    [[nodiscard]] bool load_authenticated(std::istream&                stream,
                                          const std::filesystem::path& logicalPath,
                                          EvalFile&                    evalFile);

    bool save(const EvalFile& evalFile, const std::optional<std::filesystem::path>& filename) const;

    [[nodiscard]] usize get_content_hash() const;

    bool verify(const std::function<void(std::string_view)>& onVerify,
                const EvalFile&                              evalFile,
                std::filesystem::path                        evalfilePath) const;

    NetworkOutput    evaluate(const Position&, AnyAccumulator&) const;
    RawNetworkOutput evaluate_raw(const Position&, AnyAccumulator&) const;
    NnueEvalTrace    trace_evaluate(const Position&, AnyAccumulator&) const;

   private:
    union Storage {
        LegacyAtomicV1::Network legacy;
        AtomicV2::Network       atomicV2;

        Storage() :
            legacy() {}
        ~Storage() = default;
    } storage_;

    NetworkBackend backend_ = NetworkBackend::LegacyAtomicV1;

    void activate_legacy() noexcept;
    void activate_v2() noexcept;

    friend class AnyAccumulator;
};

class AnyAccumulator {
   public:
    explicit AnyAccumulator(const AnyNetwork& network) { construct(network); }

    AnyAccumulator(const AnyAccumulator&)            = delete;
    AnyAccumulator(AnyAccumulator&&)                 = delete;
    AnyAccumulator& operator=(const AnyAccumulator&) = delete;
    AnyAccumulator& operator=(AnyAccumulator&&)      = delete;

    ~AnyAccumulator() { destroy(); }

    [[nodiscard]] NetworkBackend backend() const noexcept { return backend_; }

    void reset() noexcept {
        switch (backend_)
        {
        case NetworkBackend::LegacyAtomicV1 :
            storage_.legacy.stack.reset();
            return;
        case NetworkBackend::AtomicNNUEV2 :
            storage_.atomicV2.stack.reset();
            return;
        }
        assert(false);
    }

    DirtyPiece& push() noexcept {
        switch (backend_)
        {
        case NetworkBackend::LegacyAtomicV1 :
            return storage_.legacy.stack.push();
        case NetworkBackend::AtomicNNUEV2 :
            return storage_.atomicV2.stack.push();
        }
        assert(false);
        return storage_.legacy.stack.push();
    }

    void pop() noexcept {
        switch (backend_)
        {
        case NetworkBackend::LegacyAtomicV1 :
            storage_.legacy.stack.pop();
            return;
        case NetworkBackend::AtomicNNUEV2 :
            storage_.atomicV2.stack.pop();
            return;
        }
        assert(false);
    }

    void rebind(const AnyNetwork& network) noexcept {
        if (backend_ != network.backend_)
        {
            destroy();
            construct(network);
            return;
        }

        switch (backend_)
        {
        case NetworkBackend::LegacyAtomicV1 :
            storage_.legacy.stack.reset();
            storage_.legacy.caches().clear(network.storage_.legacy);
            return;
        case NetworkBackend::AtomicNNUEV2 :
            storage_.atomicV2.stack.reset();
            storage_.atomicV2.caches.clear(network.storage_.atomicV2);
            return;
        }
        assert(false);
    }

    // Kept for the frozen Legacy incremental differential. V2 tests use the
    // explicitly typed accessor so incompatible accumulator layouts can never
    // be confused accidentally.
    [[nodiscard]] const LegacyAtomicV1::AccumulatorState& latest() const noexcept {
        assert(backend_ == NetworkBackend::LegacyAtomicV1);
        return storage_.legacy.stack.latest();
    }

    [[nodiscard]] const AtomicV2::AccumulatorState& v2_latest() const noexcept {
        assert(backend_ == NetworkBackend::AtomicNNUEV2);
        return storage_.atomicV2.stack.latest();
    }

   private:
    struct LegacyState: private LegacyAtomicV1::AccumulatorCaches {
        explicit LegacyState(const LegacyAtomicV1::Network& network) :
            LegacyAtomicV1::AccumulatorCaches(network) {
            stack.reset();
        }

        LegacyAtomicV1::AccumulatorCaches& caches() noexcept { return *this; }

        LegacyAtomicV1::AccumulatorStack stack;
    };

    struct AtomicV2State {
        explicit AtomicV2State(const AtomicV2::Network& network) :
            caches(network) {
            stack.reset();
        }

        AtomicV2::AccumulatorCaches caches;
        AtomicV2::AccumulatorStack  stack;
    };

    union Storage {
        LegacyState   legacy;
        AtomicV2State atomicV2;

        Storage() {}
        ~Storage() {}
    } storage_;

    NetworkBackend backend_ = NetworkBackend::LegacyAtomicV1;

    void construct(const AnyNetwork& network) noexcept {
        backend_ = network.backend_;
        switch (backend_)
        {
        case NetworkBackend::LegacyAtomicV1 :
            ::new (static_cast<void*>(&storage_.legacy)) LegacyState(network.storage_.legacy);
            return;
        case NetworkBackend::AtomicNNUEV2 :
            ::new (static_cast<void*>(&storage_.atomicV2)) AtomicV2State(network.storage_.atomicV2);
            return;
        }
        assert(false);
    }

    void destroy() noexcept {
        switch (backend_)
        {
        case NetworkBackend::LegacyAtomicV1 :
            storage_.legacy.~LegacyState();
            return;
        case NetworkBackend::AtomicNNUEV2 :
            storage_.atomicV2.~AtomicV2State();
            return;
        }
        assert(false);
    }

    friend class AnyNetwork;
};

inline NetworkOutput AnyNetwork::evaluate(const Position& pos, AnyAccumulator& accumulator) const {
    assert(backend_ == accumulator.backend_);
    switch (backend_)
    {
    case NetworkBackend::LegacyAtomicV1 :
        return storage_.legacy.evaluate(pos, accumulator.storage_.legacy.stack,
                                        accumulator.storage_.legacy.caches());
    case NetworkBackend::AtomicNNUEV2 :
        return storage_.atomicV2.evaluate(pos, accumulator.storage_.atomicV2.stack,
                                          accumulator.storage_.atomicV2.caches);
    }
    assert(false);
    return {};
}

inline RawNetworkOutput AnyNetwork::evaluate_raw(const Position& pos,
                                                 AnyAccumulator& accumulator) const {
    assert(backend_ == accumulator.backend_);
    switch (backend_)
    {
    case NetworkBackend::LegacyAtomicV1 :
        return storage_.legacy.evaluate_raw(pos, accumulator.storage_.legacy.stack,
                                            accumulator.storage_.legacy.caches());
    case NetworkBackend::AtomicNNUEV2 :
        return storage_.atomicV2.evaluate_raw(pos, accumulator.storage_.atomicV2.stack,
                                              accumulator.storage_.atomicV2.caches);
    }
    assert(false);
    return {};
}

inline NnueEvalTrace AnyNetwork::trace_evaluate(const Position& pos,
                                                AnyAccumulator& accumulator) const {
    assert(backend_ == accumulator.backend_);
    switch (backend_)
    {
    case NetworkBackend::LegacyAtomicV1 :
        return storage_.legacy.trace_evaluate(pos, accumulator.storage_.legacy.stack,
                                              accumulator.storage_.legacy.caches());
    case NetworkBackend::AtomicNNUEV2 :
        return storage_.atomicV2.trace_evaluate(pos, accumulator.storage_.atomicV2.stack,
                                                accumulator.storage_.atomicV2.caches);
    }
    assert(false);
    return {};
}

static_assert(NetworkBackendCount == 2);
static_assert(std::is_trivially_copyable_v<AnyNetwork>);
static_assert(std::is_trivially_copy_constructible_v<AnyNetwork>);
static_assert(std::is_trivially_move_constructible_v<AnyNetwork>);
static_assert(std::is_trivially_destructible_v<AnyNetwork>);
static_assert(sizeof(AnyNetwork) >= sizeof(LegacyAtomicV1::Network));
static_assert(sizeof(AnyNetwork) >= sizeof(AtomicV2::Network));

}  // namespace Stockfish::Eval::NNUE

template<>
struct std::hash<Stockfish::Eval::NNUE::AnyNetwork> {
    Stockfish::usize operator()(const Stockfish::Eval::NNUE::AnyNetwork& network) const noexcept {
        return network.get_content_hash();
    }
};

#endif  // NNUE_DISPATCHER_H_INCLUDED
