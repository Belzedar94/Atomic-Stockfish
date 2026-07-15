/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_SCALAR_BACKEND_H_INCLUDED
#define ATOMIC_V3_SCALAR_BACKEND_H_INCLUDED

#include <array>

#include "numeric.h"
#include "wire_network.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

// H9.3h is deliberately a private, full-refresh correctness backend. It is
// not stored by AnyNetwork and is not reachable from UCI/search. Its output is
// intentionally verbose so later incremental and SIMD backends can identify
// the first semantic layer that diverges.
enum class ScalarError : u8 {
    None,
    FeatureOracleError,
    InvalidFeatureIndex,
    InconsistentPerspectiveBucket,
    FeatureAccumulatorOutOfRange,
    PsqtAccumulatorOutOfRange,
    NumericContractError
};

struct ScalarStatus {
    ScalarError      code         = ScalarError::None;
    FullRefreshError featureError = FullRefreshError::None;
    NumericError     numericError = NumericError::None;

    explicit constexpr operator bool() const noexcept { return code == ScalarError::None; }
};

struct ScalarPerspectiveDiagnostic {
    Color                                          perspective = WHITE;
    FullRefreshEmission                            emission{};
    std::array<i32, AccumulatorDimensions>         accumulator{};
    std::array<HmPsqtAccumulatorType, PsqtBuckets> psqt{};
};

// Canonical HM-only state shared by the H9.3h full-refresh oracle and the
// private H9.3i incremental backend. Biases are included in accumulator;
// relation rows are deliberately absent. Keeping this seam canonical makes an
// incremental caller independent of the authenticated network's ISA-specific
// in-memory permutation.
struct ScalarHmPerspective {
    std::array<i32, AccumulatorDimensions>         accumulator{};
    std::array<HmPsqtAccumulatorType, PsqtBuckets> psqt{};
};

struct ScalarDenseDiagnostic {
    std::array<i32, Fc0Outputs> fc0{};
    std::array<u8, Fc0Outputs>  fc0Squared{};
    std::array<u8, Fc0Outputs>  fc0Clipped{};
    std::array<i32, Fc1Outputs> fc1{};
    std::array<u8, Fc1Outputs>  fc1Squared{};
    std::array<u8, Fc1Outputs>  fc1Clipped{};
    std::array<i32, Fc2Outputs> fc2{};
};

struct ScalarDenseResult {
    ScalarDenseDiagnostic layers{};
    i64                   rawOutput       = 0;
    i32                   scaledOutput    = 0;
    i32                   positionalValue = 0;
};

struct ScalarDiagnostic {
    Color sideToMove = WHITE;

    // Indexed by Color (WHITE=0, BLACK=1), independent of side to move.
    std::array<ScalarPerspectiveDiagnostic, COLOR_NB> perspectives{};

    // Canonical SFNNv15 input order: side-to-move half first, opponent half
    // second; each half is the pairwise product of two 512-element accumulator
    // halves after clipping each operand to [0, 255].
    std::array<u8, Fc0Inputs> transformed{};

    IndexType             networkBucket  = 0;
    i32                   psqtDifference = 0;
    i32                   psqtValue      = 0;
    ScalarDenseDiagnostic dense{};
    i64                   rawOutput       = 0;
    i32                   scaledOutput    = 0;
    i32                   positionalValue = 0;
};

// Private scalar SFNNv15 seam shared by the full-refresh backend and the
// adversarial dense differential. It accepts canonical output-major dense
// parameters and canonical transformed bytes; every i32 affine output is
// checked and the result is transactional on all failures.
[[nodiscard]] NumericError propagate_dense_scalar(const DenseStackParameters&      stack,
                                                  const std::array<u8, Fc0Inputs>& transformed,
                                                  ScalarDenseResult&               result) noexcept;

// Private composition seams for H9.3i. accumulate_hm_scalar() consumes the HM
// rows already authenticated by emit_full_refresh(). compose_scalar_diagnostic()
// layers the three relation slices over those HM-only states and then executes
// the exact H9.3h transform and SFNNv15 tail. Both functions are transactional.
[[nodiscard]] ScalarError accumulate_hm_scalar(const Network&       network,
                                               const HmEmission&    emission,
                                               ScalarHmPerspective& result) noexcept;

[[nodiscard]] ScalarStatus
compose_scalar_diagnostic(const Network&                                   network,
                          const CapturePairSnapshot&                       snapshot,
                          const std::array<FullRefreshEmission, COLOR_NB>& emissions,
                          const std::array<ScalarHmPerspective, COLOR_NB>& hmStates,
                          ScalarDiagnostic&                                result);

// Both entry points are transactional: on every failure result is reset to a
// bytewise/default-zero diagnostic. The returned status retains only the error
// domains required to diagnose the rejected input.
[[nodiscard]] ScalarStatus evaluate_scalar(const Network&             network,
                                           const CapturePairSnapshot& snapshot,
                                           ScalarDiagnostic&          result);

[[nodiscard]] ScalarStatus
evaluate_scalar(const Network& network, const Position& position, ScalarDiagnostic& result);

const char* scalar_error_message(ScalarError error) noexcept;

static_assert(Fc0Inputs == AccumulatorDimensions);
static_assert(Fc0Inputs == 2 * 512);
static_assert(COLOR_NB == 2 && WHITE == 0 && BLACK == 1);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_SCALAR_BACKEND_H_INCLUDED
