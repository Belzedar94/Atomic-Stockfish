/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_V3_INCREMENTAL_BACKEND_H_INCLUDED
#define ATOMIC_V3_INCREMENTAL_BACKEND_H_INCLUDED

#include <array>
#include <cstddef>

#include "../../types.h"
#include "scalar_backend.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

// H9.3i-a remains a private scalar backend. These source labels describe HM
// reuse only; relation slices are independently full-refreshed on every call.
enum class HmSourceKind : u8 {
    None,
    SameFrameReuse,
    StackDelta,
    FullRefresh
};

enum class IncrementalError : u8 {
    None,
    NetworkMismatch,
    FeatureOracleError,
    InvalidHmRows,
    HmAccumulatorOutOfRange,
    PsqtAccumulatorOutOfRange,
    ScalarCompositionError,
    InjectedFailure
};

enum class IncrementalFaultPoint : u8 {
    None,
    AfterFirstPerspective,
    BeforeComposition,
    AfterCompositionBeforeCommit
};

struct IncrementalStatus {
    IncrementalError error        = IncrementalError::None;
    FullRefreshError featureError = FullRefreshError::None;
    ScalarStatus     scalarStatus{};

    explicit constexpr operator bool() const noexcept { return error == IncrementalError::None; }
};

struct HmUpdateDiagnostic {
    HmSourceKind source         = HmSourceKind::None;
    usize        sourcePly      = 0;
    usize        sourceDistance = 0;
    IndexType    removedRows    = 0;
    IndexType    addedRows      = 0;
};

struct IncrementalCounters {
    u64 hmRefreshes        = 0;
    u64 hmDeltas           = 0;
    u64 hmReuses           = 0;
    u64 relationRefreshes  = 0;
    u64 snapshotMismatches = 0;
    u64 epSquareMismatches = 0;
};

struct IncrementalDiagnostic {
    ScalarDiagnostic                          scalar{};
    std::array<ScalarHmPerspective, COLOR_NB> hmOnly{};
    std::array<HmUpdateDiagnostic, COLOR_NB>  hmUpdates{};
    IncrementalCounters                       eventCounters{};
    usize                                     ply                       = 0;
    bool                                      sameFrameSnapshotMismatch = false;
    bool                                      epSquareMismatch          = false;
    Square                                    previousEpSquare          = SQ_NONE;
    Square                                    currentEpSquare           = SQ_NONE;
    Color                                     previousSideToMove        = WHITE;
    Color                                     currentSideToMove         = WHITE;
};

class IncrementalStack {
   public:
    static constexpr usize MaxSize = MAX_PLY + 1;

    IncrementalStack() = default;
    explicit IncrementalStack(const Network& network) noexcept { reset(network); }

    void        reset(const Network& network) noexcept;
    DirtyPiece& push() noexcept;
    void        pop() noexcept;

    [[nodiscard]] IncrementalStatus evaluate(const Network&             network,
                                             const CapturePairSnapshot& snapshot,
                                             IncrementalDiagnostic&     result) noexcept;

    [[nodiscard]] IncrementalStatus evaluate(const Network&         network,
                                             const Position&        position,
                                             IncrementalDiagnostic& result) noexcept;

    [[nodiscard]] usize                      size() const noexcept { return size_; }
    [[nodiscard]] usize                      ply() const noexcept { return size_ - 1; }
    [[nodiscard]] const IncrementalCounters& counters() const noexcept { return counters_; }
    [[nodiscard]] Square                     ep_square_when_computed() const noexcept;

    // Deterministic private fault seam used to prove that evaluation never
    // publishes a partially updated frame or diagnostic.
    void set_test_fault(IncrementalFaultPoint fault) noexcept { testFault_ = fault; }

   private:
    struct HmRows {
        std::array<IndexType, HmMaximumActiveDimensions> values{};
        IndexType                                        size = 0;
    };

    struct PerspectiveFrame {
        HmRows              rows{};
        JointOrientation    orientation{};
        ScalarHmPerspective hm{};
        bool                computed = false;
    };

    struct Frame {
        std::array<PerspectiveFrame, COLOR_NB> perspectives{};
        DirtyPiece                             dirtyPiece{};
        CapturePairSnapshot                    snapshotWhenComputed{};
        Square                                 epSquareWhenComputed = SQ_NONE;
        bool                                   snapshotComputed     = false;
    };

    // Evaluation is single-owner, like the frame stack itself. Keeping the
    // large relation emissions here avoids a >128 KiB nested Windows/ASan
    // call stack without adding a per-call allocation or making temporary
    // state observable after a failed transaction.
    struct alignas(CacheLineSize) EvaluationScratch {
        std::array<FullRefreshEmission, COLOR_NB> emissions{};
    };

    [[nodiscard]] Frame&       latest() noexcept { return frames_[size_ - 1]; }
    [[nodiscard]] const Frame& latest() const noexcept { return frames_[size_ - 1]; }

    [[nodiscard]] static bool extract_hm_rows(const HmEmission& emission, HmRows& result) noexcept;
    [[nodiscard]] IncrementalStatus
    build_hm_perspective(const Network&      network,
                         Color               perspective,
                         const HmEmission&   emission,
                         PerspectiveFrame&   target,
                         HmUpdateDiagnostic& diagnostic) const noexcept;

    const Network*             network_ = nullptr;
    std::array<Frame, MaxSize> frames_{};
    usize                      size_ = 1;
    IncrementalCounters        counters_{};
    IncrementalFaultPoint      testFault_ = IncrementalFaultPoint::None;
    EvaluationScratch          scratch_{};
};

const char* incremental_error_message(IncrementalError error) noexcept;

static_assert(COLOR_NB == 2 && WHITE == 0 && BLACK == 1);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_INCREMENTAL_BACKEND_H_INCLUDED
