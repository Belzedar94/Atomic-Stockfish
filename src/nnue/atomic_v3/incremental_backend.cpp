/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "incremental_backend.h"

#include <algorithm>
#include <cassert>
#include <limits>
#include <utility>

#include "../../position.h"
#include "incremental_simd_kernels.h"
#include "wire_io.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

constexpr std::size_t color_index(Color color) noexcept { return static_cast<std::size_t>(color); }

bool same_orientation(const JointOrientation& lhs, const JointOrientation& rhs) noexcept {
    return lhs.perspective == rhs.perspective && lhs.ownKing == rhs.ownKing
        && lhs.orientedOwnKing == rhs.orientedOwnKing && lhs.verticalXor == rhs.verticalXor
        && lhs.horizontalXor == rhs.horizontalXor && lhs.kingBucket == rhs.kingBucket;
}

bool same_snapshot(const CapturePairSnapshot& lhs, const CapturePairSnapshot& rhs) noexcept {
    return lhs.board == rhs.board && lhs.sideToMove == rhs.sideToMove
        && lhs.epSquare == rhs.epSquare;
}

void add_counters(IncrementalCounters& destination, const IncrementalCounters& source) noexcept {
    destination.hmRefreshes += source.hmRefreshes;
    destination.hmDeltas += source.hmDeltas;
    destination.hmReuses += source.hmReuses;
    destination.relationRefreshes += source.relationRefreshes;
    destination.snapshotMismatches += source.snapshotMismatches;
    destination.epSquareMismatches += source.epSquareMismatches;
}

void add_hm_delta_counters(HmDeltaCounters& destination, const HmDeltaCounters& source) noexcept {
    destination.removedRows += source.removedRows;
    destination.addedRows += source.addedRows;
    destination.i16Lanes += source.i16Lanes;
    destination.sourcePermutationLanes += source.sourcePermutationLanes;
    destination.publishPermutationLanes += source.publishPermutationLanes;
    destination.scalarKernelCalls += source.scalarKernelCalls;
    destination.sse41KernelCalls += source.sse41KernelCalls;
    destination.avx2KernelCalls += source.avx2KernelCalls;
}

IncrementalStatus fail(IncrementalDiagnostic& result,
                       IncrementalError       error,
                       FullRefreshError       featureError = FullRefreshError::None,
                       ScalarStatus           scalarStatus = {}) noexcept {
    result = {};
    return {error, featureError, scalarStatus};
}

}  // namespace

void IncrementalStack::reset(const Network& network) noexcept {
    network_                 = &network;
    frames_[0]               = {};
    size_                    = 1;
    counters_                = {};
    hmDeltaCounters_         = {};
    testFault_               = IncrementalFaultPoint::None;
    requestedIsa_            = SimdIsa::Scalar;
    hmDeltaExecutionEnabled_ = false;
}

void IncrementalStack::reset(const Network& network, SimdIsa requestedIsa) noexcept {
    reset(network);
    requestedIsa_            = requestedIsa;
    hmDeltaExecutionEnabled_ = true;
}

DirtyPiece& IncrementalStack::push() noexcept {
    assert(size_ < MaxSize);
    Frame& target = frames_[size_++];
    target        = {};
    return target.dirtyPiece;
}

void IncrementalStack::pop() noexcept {
    assert(size_ > 1);
    --size_;
}

Square IncrementalStack::ep_square_when_computed() const noexcept {
    return latest().snapshotComputed ? latest().epSquareWhenComputed : SQ_NONE;
}

bool IncrementalStack::extract_hm_rows(const HmEmission& emission, HmRows& result) noexcept {
    result = {};
    if (emission.size > HmMaximumActiveDimensions)
        return false;

    result.size = emission.size;
    for (IndexType index = 0; index < emission.size; ++index)
    {
        const IndexType row = emission.features[index].physicalIndex;
        if (row >= HmPhysicalDimensions)
            return false;
        result.values[index] = row;
    }

    std::sort(result.values.begin(), result.values.begin() + result.size);
    return std::adjacent_find(result.values.begin(), result.values.begin() + result.size)
        == result.values.begin() + result.size;
}

IncrementalStatus IncrementalStack::build_hm_perspective(const Network&      network,
                                                         Color               perspective,
                                                         const HmEmission&   emission,
                                                         PerspectiveFrame&   target,
                                                         HmUpdateDiagnostic& diagnostic,
                                                         HmDeltaCounters& deltaCounters) noexcept {
    target     = {};
    diagnostic = {};

    HmRows currentRows{};
    if (!extract_hm_rows(emission, currentRows)
        || !is_canonical_joint_orientation(emission.orientation)
        || emission.orientation.perspective != perspective)
        return {IncrementalError::InvalidHmRows};

    target.rows        = currentRows;
    target.orientation = emission.orientation;

    const std::size_t perspectiveIndex = color_index(perspective);
    const auto&       current          = latest().perspectives[perspectiveIndex];
    const bool        exactCurrent =
      current.computed && same_orientation(current.orientation, emission.orientation)
      && current.rows.size == currentRows.size
      && std::equal(current.rows.values.begin(), current.rows.values.begin() + current.rows.size,
                    currentRows.values.begin());

    if (exactCurrent)
    {
        target                    = current;
        diagnostic.source         = HmSourceKind::SameFrameReuse;
        diagnostic.sourcePly      = size_ - 1;
        diagnostic.sourceDistance = 0;
        return {};
    }

    const PerspectiveFrame* source    = nullptr;
    usize                   sourcePly = 0;
    for (usize candidate = size_; candidate-- > 0;)
    {
        const auto& state = frames_[candidate].perspectives[perspectiveIndex];
        if (state.computed && same_orientation(state.orientation, emission.orientation))
        {
            source    = &state;
            sourcePly = candidate;
            break;
        }
    }

    if (!source)
    {
        const ScalarError scalarError = accumulate_hm_scalar(network, emission, target.hm);
        if (scalarError != ScalarError::None)
        {
            const ScalarStatus     scalarStatus{scalarError, FullRefreshError::None,
                                            NumericError::None};
            const IncrementalError error = scalarError == ScalarError::InvalidFeatureIndex
                                           ? IncrementalError::InvalidHmRows
                                         : scalarError == ScalarError::FeatureAccumulatorOutOfRange
                                           ? IncrementalError::HmAccumulatorOutOfRange
                                         : scalarError == ScalarError::PsqtAccumulatorOutOfRange
                                           ? IncrementalError::PsqtAccumulatorOutOfRange
                                           : IncrementalError::ScalarCompositionError;
            return {error, FullRefreshError::None, scalarStatus};
        }

        target.computed           = true;
        diagnostic.source         = HmSourceKind::FullRefresh;
        diagnostic.sourcePly      = size_ - 1;
        diagnostic.sourceDistance = 0;
        return {};
    }

    std::array<IndexType, HmMaximumActiveDimensions> removed{};
    std::array<IndexType, HmMaximumActiveDimensions> added{};
    IndexType                                        removedSize = 0;
    IndexType                                        addedSize   = 0;
    IndexType                                        oldIndex    = 0;
    IndexType                                        newIndex    = 0;
    while (oldIndex < source->rows.size || newIndex < currentRows.size)
    {
        if (newIndex == currentRows.size
            || (oldIndex < source->rows.size
                && source->rows.values[oldIndex] < currentRows.values[newIndex]))
            removed[removedSize++] = source->rows.values[oldIndex++];
        else if (oldIndex == source->rows.size
                 || currentRows.values[newIndex] < source->rows.values[oldIndex])
            added[addedSize++] = currentRows.values[newIndex++];
        else
        {
            ++oldIndex;
            ++newIndex;
        }
    }

    if (!network.hm_weights() || !network.hm_psqt_weights())
        return {IncrementalError::InvalidHmRows};

    std::array<i64, PsqtBuckets> psqt = source->hm.psqt;
    ScalarHmPerspective          candidateHm{};
    if (!hmDeltaExecutionEnabled_)
    {
        std::array<i64, AccumulatorDimensions> accumulator{};
        for (std::size_t output = 0; output < AccumulatorDimensions; ++output)
            accumulator[output] = source->hm.accumulator[output];

        auto apply = [&](IndexType row, i64 sign) {
            if (row >= HmPhysicalDimensions)
                return false;
            const i16* weights = network.hm_weights() + std::size_t(row) * AccumulatorDimensions;
            for (std::size_t canonical = 0; canonical < AccumulatorDimensions; ++canonical)
            {
                const std::size_t internal =
                  WireIO::internal_index_from_canonical<i16, 16>(canonical);
                accumulator[canonical] += sign * i64(weights[internal]);
            }
            for (IndexType bucket = 0; bucket < PsqtBuckets; ++bucket)
                psqt[bucket] +=
                  sign * i64(network.hm_psqt_weights()[std::size_t(row) * PsqtBuckets + bucket]);
            return true;
        };

        // Removal first is part of the frozen incremental arithmetic contract.
        for (IndexType index = 0; index < removedSize; ++index)
            if (!apply(removed[index], -1))
                return {IncrementalError::InvalidHmRows};
        for (IndexType index = 0; index < addedSize; ++index)
            if (!apply(added[index], 1))
                return {IncrementalError::InvalidHmRows};

        for (std::size_t output = 0; output < AccumulatorDimensions; ++output)
        {
            if (!feature_transformer_accumulator_in_range(accumulator[output]))
                return {IncrementalError::HmAccumulatorOutOfRange};
            candidateHm.accumulator[output] = static_cast<i32>(accumulator[output]);
        }
    }
    else
    {
        auto& internalAccumulator = scratch_.internalHmAccumulator;
        for (std::size_t canonical = 0; canonical < AccumulatorDimensions; ++canonical)
        {
            const std::size_t internal = WireIO::internal_index_from_canonical<i16, 16>(canonical);
            internalAccumulator[internal] = source->hm.accumulator[canonical];
        }
        deltaCounters.sourcePermutationLanes += AccumulatorDimensions;

        auto apply = [&](IndexType row, HmDeltaOperation operation) {
            if (row >= HmPhysicalDimensions)
                return false;
            const i16* weights = network.hm_weights() + std::size_t(row) * AccumulatorDimensions;
            const HmDeltaKernelResult kernelResult =
              apply_hm_delta_kernel(requestedIsa_, operation, internalAccumulator.data(), weights,
                                    internalAccumulator.size());
            if (!kernelResult || kernelResult.executedIsa != requestedIsa_)
                return false;

            if (operation == HmDeltaOperation::Remove)
                ++deltaCounters.removedRows;
            else
                ++deltaCounters.addedRows;
            deltaCounters.i16Lanes += AccumulatorDimensions;
            switch (kernelResult.executedIsa)
            {
            case SimdIsa::Scalar :
                ++deltaCounters.scalarKernelCalls;
                break;
            case SimdIsa::Sse41 :
                ++deltaCounters.sse41KernelCalls;
                break;
            case SimdIsa::Avx2 :
                ++deltaCounters.avx2KernelCalls;
                break;
            }

            const i64 sign = operation == HmDeltaOperation::Remove ? -1 : 1;
            for (IndexType bucket = 0; bucket < PsqtBuckets; ++bucket)
                psqt[bucket] +=
                  sign * i64(network.hm_psqt_weights()[std::size_t(row) * PsqtBuckets + bucket]);
            return true;
        };

        for (IndexType index = 0; index < removedSize; ++index)
            if (!apply(removed[index], HmDeltaOperation::Remove))
                return {IncrementalError::UnsupportedIsa};
        for (IndexType index = 0; index < addedSize; ++index)
            if (!apply(added[index], HmDeltaOperation::Add))
                return {IncrementalError::UnsupportedIsa};

        for (std::size_t canonical = 0; canonical < AccumulatorDimensions; ++canonical)
        {
            const std::size_t internal = WireIO::internal_index_from_canonical<i16, 16>(canonical);
            const i64         value    = internalAccumulator[internal];
            if (!feature_transformer_accumulator_in_range(value))
                return {IncrementalError::HmAccumulatorOutOfRange};
            candidateHm.accumulator[canonical] = static_cast<i32>(value);
        }
        deltaCounters.publishPermutationLanes += AccumulatorDimensions;
    }
    for (IndexType bucket = 0; bucket < PsqtBuckets; ++bucket)
    {
        if (psqt[bucket] < std::numeric_limits<i32>::min()
            || psqt[bucket] > std::numeric_limits<i32>::max())
            return {IncrementalError::PsqtAccumulatorOutOfRange};
        candidateHm.psqt[bucket] = psqt[bucket];
    }

    target.hm                 = candidateHm;
    target.computed           = true;
    diagnostic.source         = HmSourceKind::StackDelta;
    diagnostic.sourcePly      = sourcePly;
    diagnostic.sourceDistance = size_ - 1 - sourcePly;
    diagnostic.removedRows    = removedSize;
    diagnostic.addedRows      = addedSize;
    return {};
}

IncrementalStatus IncrementalStack::evaluate(const Network&             network,
                                             const CapturePairSnapshot& snapshot,
                                             IncrementalDiagnostic&     result) noexcept {
    result = {};
    if (network_ != &network)
        return {IncrementalError::NetworkMismatch};
    if (hmDeltaExecutionEnabled_ && !simd_isa_available(requestedIsa_))
        return {IncrementalError::UnsupportedIsa};

    const Frame& previous = latest();

    // The caller-owned diagnostic is the transactional scratch. Every failure
    // below clears it before returning, while the only persistent Frame stays
    // local until the final commit. Avoiding a second full diagnostic (and a
    // second ScalarDiagnostic) keeps this hot function's Windows stack frame
    // comfortably below the sanitizer gate.
    IncrementalDiagnostic& candidateDiagnostic = result;
    candidateDiagnostic.hmDelta.enabled        = hmDeltaExecutionEnabled_;
    candidateDiagnostic.hmDelta.requestedIsa   = requestedIsa_;
    candidateDiagnostic.hmDelta.executedIsa    = requestedIsa_;
    candidateDiagnostic.ply                    = size_ - 1;
    candidateDiagnostic.currentEpSquare        = snapshot.epSquare;
    candidateDiagnostic.currentSideToMove      = snapshot.sideToMove;
    if (previous.snapshotComputed)
    {
        candidateDiagnostic.previousEpSquare   = previous.epSquareWhenComputed;
        candidateDiagnostic.previousSideToMove = previous.snapshotWhenComputed.sideToMove;

        // The EP comparison deliberately precedes every same-frame HM reuse.
        // Relations and the dense result are never cached, so a mismatch can
        // reuse only HM and must still pass through a fresh composition below.
        candidateDiagnostic.epSquareMismatch = previous.epSquareWhenComputed != snapshot.epSquare;
        candidateDiagnostic.sameFrameSnapshotMismatch =
          !same_snapshot(previous.snapshotWhenComputed, snapshot);
        candidateDiagnostic.eventCounters.epSquareMismatches = candidateDiagnostic.epSquareMismatch;
        candidateDiagnostic.eventCounters.snapshotMismatches =
          candidateDiagnostic.sameFrameSnapshotMismatch;
    }

    auto& emissions = scratch_.emissions;
    for (const Color perspective : {WHITE, BLACK})
    {
        const auto featureError =
          emit_full_refresh(snapshot, perspective, emissions[color_index(perspective)]);
        if (featureError != FullRefreshError::None)
            return fail(result, IncrementalError::FeatureOracleError, featureError);
        ++candidateDiagnostic.eventCounters.relationRefreshes;
    }

    Frame candidateFrame{};
    candidateFrame.dirtyPiece = previous.dirtyPiece;
    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t       index  = color_index(perspective);
        const IncrementalStatus status = build_hm_perspective(
          network, perspective, emissions[index].hm, candidateFrame.perspectives[index],
          candidateDiagnostic.hmUpdates[index], candidateDiagnostic.hmDelta.counters);
        if (!status)
        {
            result = {};
            return status;
        }

        candidateDiagnostic.hmOnly[index] = candidateFrame.perspectives[index].hm;
        switch (candidateDiagnostic.hmUpdates[index].source)
        {
        case HmSourceKind::SameFrameReuse :
            ++candidateDiagnostic.eventCounters.hmReuses;
            break;
        case HmSourceKind::StackDelta :
            ++candidateDiagnostic.eventCounters.hmDeltas;
            break;
        case HmSourceKind::FullRefresh :
            ++candidateDiagnostic.eventCounters.hmRefreshes;
            break;
        case HmSourceKind::None :
            return fail(result, IncrementalError::InvalidHmRows);
        }

        if (perspective == WHITE && testFault_ == IncrementalFaultPoint::AfterFirstPerspective)
            return fail(result, IncrementalError::InjectedFailure);
    }

    if (testFault_ == IncrementalFaultPoint::BeforeComposition)
        return fail(result, IncrementalError::InjectedFailure);

    const ScalarStatus scalarStatus = compose_scalar_diagnostic(
      network, snapshot, emissions, candidateDiagnostic.hmOnly, candidateDiagnostic.scalar);
    if (!scalarStatus)
        return fail(result, IncrementalError::ScalarCompositionError, scalarStatus.featureError,
                    scalarStatus);

    if (testFault_ == IncrementalFaultPoint::AfterCompositionBeforeCommit)
        return fail(result, IncrementalError::InjectedFailure);

    candidateFrame.snapshotWhenComputed = snapshot;
    candidateFrame.epSquareWhenComputed = snapshot.epSquare;
    candidateFrame.snapshotComputed     = true;

    latest() = std::move(candidateFrame);
    add_counters(counters_, candidateDiagnostic.eventCounters);
    add_hm_delta_counters(hmDeltaCounters_, candidateDiagnostic.hmDelta.counters);
    if (candidateDiagnostic.hmDelta.counters.avx2KernelCalls)
        candidateDiagnostic.hmDelta.executedIsa = SimdIsa::Avx2;
    else if (candidateDiagnostic.hmDelta.counters.sse41KernelCalls)
        candidateDiagnostic.hmDelta.executedIsa = SimdIsa::Sse41;
    else if (candidateDiagnostic.hmDelta.counters.scalarKernelCalls)
        candidateDiagnostic.hmDelta.executedIsa = SimdIsa::Scalar;
    return {};
}

IncrementalStatus IncrementalStack::evaluate(const Network&         network,
                                             const Position&        position,
                                             IncrementalDiagnostic& result) noexcept {
    return evaluate(network, make_capture_pair_snapshot(position), result);
}

const char* incremental_error_message(IncrementalError error) noexcept {
    switch (error)
    {
    case IncrementalError::None :
        return "none";
    case IncrementalError::NetworkMismatch :
        return "incremental stack is bound to a different network object";
    case IncrementalError::FeatureOracleError :
        return "full-refresh feature oracle rejected the snapshot";
    case IncrementalError::InvalidHmRows :
        return "HM rows are invalid, duplicated or outside the physical tensor";
    case IncrementalError::HmAccumulatorOutOfRange :
        return "HM-only accumulator escaped the proved i32 envelope";
    case IncrementalError::PsqtAccumulatorOutOfRange :
        return "HM PSQT accumulator escaped the proved i32 envelope";
    case IncrementalError::ScalarCompositionError :
        return "shared scalar relation/transform/dense composition failed";
    case IncrementalError::InjectedFailure :
        return "private incremental transactional fault was injected";
    case IncrementalError::UnsupportedIsa :
        return "requested incremental SIMD ISA is not compiled into this binary";
    }
    return "unknown Atomic V3 incremental error";
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3
