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
#include "simd_isa.h"

namespace Stockfish {
class Position;
}

namespace Stockfish::Eval::NNUE::AtomicV3 {

// HM and relation accumulators have independent source selection.  Feature
// rows are still emitted by the frozen full-refresh oracle on every call; only
// the expensive row-by-row accumulation is reused or updated by exact deltas.
enum class HmSourceKind : u8 {
    None,
    SameFrameReuse,
    StackDelta,
    FullRefresh
};

enum class RelationSourceKind : u8 {
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
    InjectedFailure,
    UnsupportedIsa,
    InvalidRelationRows
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

struct RelationUpdateDiagnostic {
    RelationSourceKind source         = RelationSourceKind::None;
    usize              sourcePly      = 0;
    usize              sourceDistance = 0;
    IndexType          removedRows    = 0;
    IndexType          addedRows      = 0;
};

struct IncrementalCounters {
    u64 hmRefreshes                  = 0;
    u64 hmDeltas                     = 0;
    u64 hmReuses                     = 0;
    u64 relationRefreshes            = 0;
    u64 relationAccumulatorRefreshes = 0;
    u64 relationAccumulatorDeltas    = 0;
    u64 relationAccumulatorReuses    = 0;
    u64 snapshotMismatches           = 0;
    u64 epSquareMismatches           = 0;
};

struct HmDeltaCounters {
    u64 removedRows             = 0;
    u64 addedRows               = 0;
    u64 i16Lanes                = 0;
    u64 sourcePermutationLanes  = 0;
    u64 publishPermutationLanes = 0;
    u64 scalarKernelCalls       = 0;
    u64 sse41KernelCalls        = 0;
    u64 avx2KernelCalls         = 0;

    [[nodiscard]] constexpr u64 kernel_calls() const noexcept {
        return scalarKernelCalls + sse41KernelCalls + avx2KernelCalls;
    }
    [[nodiscard]] constexpr u64 fallback_calls(SimdIsa requestedIsa) const noexcept {
        return requestedIsa == SimdIsa::Scalar ? 0 : scalarKernelCalls;
    }
};

struct HmDeltaDiagnostic {
    bool            enabled      = false;
    SimdIsa         requestedIsa = SimdIsa::Scalar;
    SimdIsa         executedIsa  = SimdIsa::Scalar;
    HmDeltaCounters counters{};
};

struct IncrementalDiagnostic {
    ScalarDiagnostic                               scalar{};
    std::array<ScalarHmPerspective, COLOR_NB>      hmOnly{};
    std::array<HmUpdateDiagnostic, COLOR_NB>       hmUpdates{};
    std::array<RelationUpdateDiagnostic, COLOR_NB> relationUpdates{};
    IncrementalCounters                            eventCounters{};
    HmDeltaDiagnostic                              hmDelta{};
    usize                                          ply                       = 0;
    bool                                           sameFrameSnapshotMismatch = false;
    bool                                           epSquareMismatch          = false;
    Square                                         previousEpSquare          = SQ_NONE;
    Square                                         currentEpSquare           = SQ_NONE;
    Color                                          previousSideToMove        = WHITE;
    Color                                          currentSideToMove         = WHITE;
};

// Compact production result. The verbose IncrementalDiagnostic remains the
// exact trace/test oracle, while search consumes only these wire-scale values.
struct RuntimeOutput {
    i32 psqtDifference = 0;
    i64 rawOutput      = 0;
    i32 scaledOutput   = 0;
};

class IncrementalStack {
   public:
    static constexpr usize MaxSize = MAX_PLY + 1;

    IncrementalStack() = default;
    explicit IncrementalStack(const Network& network) noexcept { reset(network); }
    IncrementalStack(const Network& network, SimdIsa requestedIsa) noexcept {
        reset(network, requestedIsa);
    }

    void        reset(const Network& network) noexcept;
    void        reset(const Network& network, SimdIsa requestedIsa) noexcept;
    DirtyPiece& push() noexcept;
    void        pop() noexcept;

    [[nodiscard]] IncrementalStatus evaluate(const Network&             network,
                                             const CapturePairSnapshot& snapshot,
                                             IncrementalDiagnostic&     result) noexcept;

    [[nodiscard]] IncrementalStatus evaluate(const Network&         network,
                                             const Position&        position,
                                             IncrementalDiagnostic& result) noexcept;

    [[nodiscard]] IncrementalStatus evaluate_runtime(const Network&             network,
                                                     const CapturePairSnapshot& snapshot,
                                                     RuntimeOutput&             result) noexcept;

    [[nodiscard]] IncrementalStatus evaluate_runtime(const Network&  network,
                                                     const Position& position,
                                                     RuntimeOutput&  result) noexcept;

    [[nodiscard]] usize                      size() const noexcept { return size_; }
    [[nodiscard]] usize                      ply() const noexcept { return size_ - 1; }
    [[nodiscard]] const IncrementalCounters& counters() const noexcept { return counters_; }
    [[nodiscard]] const HmDeltaCounters&     hm_delta_counters() const noexcept {
        return hmDeltaCounters_;
    }
    [[nodiscard]] bool hm_delta_execution_enabled() const noexcept {
        return hmDeltaExecutionEnabled_;
    }
    [[nodiscard]] SimdIsa requested_isa() const noexcept { return requestedIsa_; }
    [[nodiscard]] Square  ep_square_when_computed() const noexcept;

    // Deterministic private fault seam used to prove that evaluation never
    // publishes a partially updated frame or diagnostic.
    void set_test_fault(IncrementalFaultPoint fault) noexcept { testFault_ = fault; }

   private:
    struct HmRows {
        std::array<IndexType, HmMaximumActiveDimensions> values{};
        IndexType                                        size = 0;
    };

    static constexpr IndexType RelationMaximumActiveDimensions = CapturePairMaximumActiveFeatures
                                                               + KingBlastEpMaximumActiveFeatures
                                                               + BlastRingMaximumActiveFeatures;

    struct RelationRows {
        std::array<IndexType, RelationMaximumActiveDimensions> values;
        IndexType                                              size = 0;
    };

    struct PerspectiveFrame {
        HmRows              rows{};
        JointOrientation    orientation{};
        ScalarHmPerspective hm{};
        bool                computed = false;
    };

    struct RelationFrame {
        RelationRows                           rows;
        std::array<i32, AccumulatorDimensions> accumulator;
        bool                                   computed = false;
    };

    struct Frame {
        std::array<PerspectiveFrame, COLOR_NB> perspectives{};
        std::array<RelationFrame, COLOR_NB>    relations;
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
        std::array<i64, AccumulatorDimensions>    internalHmAccumulator{};
        std::array<i32, AccumulatorDimensions>    internalRuntimeAccumulator{};
        std::array<u8, Fc0Inputs>                 transformed{};
    };

    [[nodiscard]] Frame&       latest() noexcept { return frames_[size_ - 1]; }
    [[nodiscard]] const Frame& latest() const noexcept { return frames_[size_ - 1]; }

    [[nodiscard]] static bool extract_hm_rows(const HmEmission& emission, HmRows& result) noexcept;
    [[nodiscard]] static bool extract_relation_rows(const FullRefreshEmission& emission,
                                                    RelationRows&              result) noexcept;
    [[nodiscard]] IncrementalStatus build_hm_perspective(const Network&      network,
                                                         Color               perspective,
                                                         const HmEmission&   emission,
                                                         PerspectiveFrame&   target,
                                                         HmUpdateDiagnostic& diagnostic,
                                                         HmDeltaCounters& deltaCounters) noexcept;
    [[nodiscard]] IncrementalStatus
         build_relation_perspective(const Network&             network,
                                    const FullRefreshEmission& emission,
                                    Color                      perspective,
                                    RelationFrame&             target,
                                    RelationUpdateDiagnostic&  diagnostic) noexcept;
    void commit_frame(Frame& candidate) noexcept;

    const Network*             network_ = nullptr;
    std::array<Frame, MaxSize> frames_{};
    usize                      size_ = 1;
    IncrementalCounters        counters_{};
    HmDeltaCounters            hmDeltaCounters_{};
    IncrementalFaultPoint      testFault_               = IncrementalFaultPoint::None;
    SimdIsa                    requestedIsa_            = SimdIsa::Scalar;
    bool                       hmDeltaExecutionEnabled_ = false;
    EvaluationScratch          scratch_{};
};

const char* incremental_error_message(IncrementalError error) noexcept;

static_assert(COLOR_NB == 2 && WHITE == 0 && BLACK == 1);

}  // namespace Stockfish::Eval::NNUE::AtomicV3

#endif  // ATOMIC_V3_INCREMENTAL_BACKEND_H_INCLUDED
