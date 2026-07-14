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

#include <filesystem>
#include <functional>
#include <optional>
#include <string_view>
#include <type_traits>
#include <utility>

#include "../types.h"
#include "network.h"
#include "nnue_accumulator.h"

namespace Stockfish::Eval::NNUE {

// Keep the proven Legacy Atomic V1 implementation as an independently named
// backend. H9.1 deliberately has exactly one compiled backend, so these aliases
// add no storage and cannot change its wire format or evaluation arithmetic.
namespace LegacyAtomicV1 {
using Network           = ::Stockfish::Eval::NNUE::Network;
using AccumulatorStack  = ::Stockfish::Eval::NNUE::AccumulatorStack;
using AccumulatorCaches = ::Stockfish::Eval::NNUE::AccumulatorCaches;
}

enum class NetworkBackend : u8 {
    LegacyAtomicV1
};
inline constexpr usize NetworkBackendCount = 1;

class AnyAccumulator;

// Header-only, single-backend facade. The Legacy network remains stored inline
// so NUMA shared-memory replication retains the same trivial-copy contract.
// A later H9 block can extend this public seam without leaking concrete backend
// objects into Engine, Search workers, evaluation, or protocol code.
class AnyNetwork {
   public:
    static constexpr NetworkBackend backend() noexcept { return NetworkBackend::LegacyAtomicV1; }

    void load(const std::filesystem::path& rootDirectory,
              std::filesystem::path        evalfilePath,
              EvalFile&                    evalFile) {
        legacy_.load(rootDirectory, std::move(evalfilePath), evalFile);
    }

    bool save(const EvalFile&                             evalFile,
              const std::optional<std::filesystem::path>& filename) const {
        return legacy_.save(evalFile, filename);
    }

    usize get_content_hash() const { return legacy_.get_content_hash(); }

    bool verify(const std::function<void(std::string_view)>& onVerify,
                const EvalFile&                              evalFile,
                std::filesystem::path                        evalfilePath) const {
        return legacy_.verify(onVerify, evalFile, std::move(evalfilePath));
    }

    NetworkOutput    evaluate(const Position&, AnyAccumulator&) const;
    RawNetworkOutput evaluate_raw(const Position&, AnyAccumulator&) const;
    NnueEvalTrace    trace_evaluate(const Position&, AnyAccumulator&) const;

   private:
    LegacyAtomicV1::Network legacy_;

    friend class AnyAccumulator;
};

// The worker-facing accumulator owns both Legacy objects. No pointer or
// reference to a concrete NUMA replica is retained: rebind() receives the
// current facade after every network replication and resets all cached state.
class AnyAccumulator {
   public:
    explicit AnyAccumulator(const AnyNetwork& network) :
        caches_(network.legacy_) {
        stack_.reset();
    }

    void reset() noexcept { stack_.reset(); }

    DirtyPiece& push() noexcept { return stack_.push(); }

    void pop() noexcept { stack_.pop(); }

    void rebind(const AnyNetwork& network) noexcept {
        stack_.reset();
        caches_.clear(network.legacy_);
    }

    [[nodiscard]] const AccumulatorState& latest() const noexcept { return stack_.latest(); }

   private:
    LegacyAtomicV1::AccumulatorStack  stack_;
    LegacyAtomicV1::AccumulatorCaches caches_;

    friend class AnyNetwork;
};

inline NetworkOutput AnyNetwork::evaluate(const Position& pos, AnyAccumulator& accumulator) const {
    return legacy_.evaluate(pos, accumulator.stack_, accumulator.caches_);
}

inline RawNetworkOutput AnyNetwork::evaluate_raw(const Position& pos,
                                                 AnyAccumulator& accumulator) const {
    return legacy_.evaluate_raw(pos, accumulator.stack_, accumulator.caches_);
}

inline NnueEvalTrace AnyNetwork::trace_evaluate(const Position& pos,
                                                AnyAccumulator& accumulator) const {
    return legacy_.trace_evaluate(pos, accumulator.stack_, accumulator.caches_);
}

static_assert(AnyNetwork::backend() == NetworkBackend::LegacyAtomicV1);
static_assert(NetworkBackendCount == 1);
static_assert(sizeof(AnyNetwork) == sizeof(LegacyAtomicV1::Network));
static_assert(alignof(AnyNetwork) == alignof(LegacyAtomicV1::Network));
static_assert(std::is_trivially_copyable_v<AnyNetwork>);
static_assert(std::is_trivially_copy_constructible_v<AnyNetwork>);
static_assert(std::is_trivially_move_constructible_v<AnyNetwork>);
static_assert(std::is_trivially_destructible_v<AnyNetwork>);

}  // namespace Stockfish::Eval::NNUE

template<>
struct std::hash<Stockfish::Eval::NNUE::AnyNetwork> {
    Stockfish::usize operator()(const Stockfish::Eval::NNUE::AnyNetwork& network) const noexcept {
        return network.get_content_hash();
    }
};

#endif  // NNUE_DISPATCHER_H_INCLUDED
