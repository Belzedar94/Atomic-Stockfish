/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#ifndef NNUE_ACCUMULATOR_H_INCLUDED
#define NNUE_ACCUMULATOR_H_INCLUDED

#include <array>
#include <cassert>
#include <cstddef>
#include <new>
#include <utility>

#include "../types.h"
#include "nnue_architecture.h"
#include "nnue_common.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE {

class FeatureTransformer;

struct alignas(CacheLineSize) Accumulator {
    std::array<std::array<i16, TransformedFeatureDimensions>, COLOR_NB> accumulation;
    std::array<std::array<i32, PSQTBuckets>, COLOR_NB>                  psqtAccumulation;
    std::array<bool, COLOR_NB>                                          computed = {};
};

// Kept as an API-compatible placeholder. Legacy Atomic V1 initially uses a
// direct refresh instead of modern king-square Finny caches.
struct AccumulatorCaches {
    template<typename Network>
    explicit AccumulatorCaches(const Network&) {}

    template<typename Network>
    void clear(const Network&) {}
};

struct AccumulatorState: public Accumulator {
    DirtyPiece   dirtyPiece;
    DirtyThreats dirtyThreats;
};

class AccumulatorStack {
   public:
    static constexpr usize MaxSize = MAX_PLY + 1;

    [[nodiscard]] const AccumulatorState& latest() const noexcept;

    void                                  reset() noexcept;
    std::pair<DirtyPiece&, DirtyThreats&> push() noexcept;
    void                                  pop() noexcept;

    void evaluate(const Position&           pos,
                  const FeatureTransformer& featureTransformer,
                  AccumulatorCaches&        cache) noexcept;

   private:
    [[nodiscard]] AccumulatorState& mut_latest() noexcept;

    void evaluate_side(Color                     perspective,
                       const Position&           pos,
                       const FeatureTransformer& featureTransformer) noexcept;

    void refresh(Color                     perspective,
                 const Position&           pos,
                 const FeatureTransformer& featureTransformer,
                 AccumulatorState&         target) noexcept;

    void update(Color                     perspective,
                Square                    ksq,
                const FeatureTransformer& featureTransformer,
                const AccumulatorState&   source,
                AccumulatorState&         target) noexcept;

    std::array<AccumulatorState, MaxSize> accumulators;
    usize                                 size = 1;
};

}  // namespace Stockfish::Eval::NNUE

#endif  // NNUE_ACCUMULATOR_H_INCLUDED
