/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V2_ACCUMULATOR_H_INCLUDED
#define ATOMIC_V2_ACCUMULATOR_H_INCLUDED

#include <array>
#include <cassert>
#include <cstddef>

#include "../../types.h"
#include "../nnue_common.h"
#include "constants.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV2 {

class FeatureTransformer;

struct alignas(CacheLineSize) Accumulator {
    std::array<std::array<i16, L1>, COLOR_NB>          accumulation;
    std::array<std::array<i32, PSQTBuckets>, COLOR_NB> psqtAccumulation;
    std::array<bool, COLOR_NB>                         computed{};
};

// Atomic V2 Finny cache. A cache entry is keyed by the actual king square and
// stores the exact Atomic piece map, so explosion removals are naturally part
// of the next refresh delta.
struct AccumulatorCaches {
    struct alignas(CacheLineSize) Entry {
        std::array<BiasType, L1>                accumulation;
        std::array<PSQTWeightType, PSQTBuckets> psqtAccumulation;
        std::array<Piece, SQUARE_NB>            pieces;
        Bitboard                                pieceBB = 0;

        void clear(const std::array<BiasType, L1>& biases) noexcept {
            accumulation = biases;
            psqtAccumulation.fill(0);
            pieces.fill(NO_PIECE);
            pieceBB = 0;
        }
    };

    template<typename Network>
    explicit AccumulatorCaches(const Network& network) {
        clear(network);
    }

    template<typename Network>
    void clear(const Network& network) {
        for (auto& squareEntries : entries)
            for (auto& entry : squareEntries)
                entry.clear(network.feature_transformer().biases);
    }

    std::array<Entry, COLOR_NB>& operator[](Square square) noexcept {
        assert(is_ok(square));
        return entries[square];
    }

    std::array<std::array<Entry, COLOR_NB>, SQUARE_NB> entries;
};

struct AccumulatorState: public Accumulator {
    DirtyPiece dirtyPiece;
};

class AccumulatorStack {
   public:
    static constexpr usize MaxSize = MAX_PLY + 1;

    [[nodiscard]] const AccumulatorState& latest() const noexcept;

    void        reset() noexcept;
    DirtyPiece& push() noexcept;
    void        pop() noexcept;

    void evaluate(const Position&           pos,
                  const FeatureTransformer& featureTransformer,
                  AccumulatorCaches&        cache) noexcept;

   private:
    [[nodiscard]] AccumulatorState& mut_latest() noexcept;

    void evaluate_side(Color                     perspective,
                       const Position&           pos,
                       const FeatureTransformer& featureTransformer,
                       AccumulatorCaches&        cache) noexcept;

    void refresh(Color                     perspective,
                 const Position&           pos,
                 const FeatureTransformer& featureTransformer,
                 AccumulatorState&         target,
                 AccumulatorCaches&        cache) noexcept;

    void update(Color                     perspective,
                Square                    kingSquare,
                const FeatureTransformer& featureTransformer,
                const AccumulatorState&   source,
                AccumulatorState&         target) noexcept;

    std::array<AccumulatorState, MaxSize> accumulators;
    usize                                 size = 1;
};

static_assert(FeatureSet::MaxRemovedDimensions == 2 + DirtyPiece::MAX_ATOMIC_BLAST_PIECES);

}  // namespace Stockfish::Eval::NNUE::AtomicV2

#endif  // ATOMIC_V2_ACCUMULATOR_H_INCLUDED
