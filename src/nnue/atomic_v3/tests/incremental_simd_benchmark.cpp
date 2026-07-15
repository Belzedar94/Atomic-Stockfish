/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

#include "../../../attacks.h"
#include "../../../bitboard.h"
#include "../../../position.h"
#include "../../../uci_move.h"
#include "../incremental_backend.h"
#include "../wire_io.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

constexpr u64      FnvOffset          = 1469598103934665603ULL;
constexpr u64      FnvPrime           = 1099511628211ULL;
constexpr unsigned Warmups            = 1;
constexpr unsigned Trials             = 5;
constexpr unsigned RepetitionsPerCase = 128;

struct CaseSpec {
    std::string_view name;
    std::string_view fen;
    std::string_view uci;
    MoveType         moveType;
    usize            minimumBlast;
    usize            maximumBlast;
};

constexpr std::array<CaseSpec, 5> CaseSpecs{{
  {"quiet", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "e2e4", NORMAL, 0, 0},
  {"capture", "7k/p7/8/8/8/8/R7/K6N w - - 0 1", "a2a7", NORMAL, 1, 8},
  {"promotion", "7k/P7/8/8/8/8/8/K7 w - - 0 1", "a7a8q", PROMOTION, 0, 0},
  {"en-passant", "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", "e5d6", EN_PASSANT, 1, 8},
  {"max-blast", "7k/8/8/2nnn3/2nrn3/2nnnN2/8/K7 w - - 0 1", "f3d4", NORMAL, 9, 9},
}};

[[noreturn]] void die(const std::string& detail) {
    std::cerr << "AtomicNNUEV3 incremental SIMD benchmark FAILED\n" << detail << '\n';
    std::exit(EXIT_FAILURE);
}

void require(bool condition, const std::string& detail) {
    if (!condition)
        die(detail);
}

template<typename Integer>
void fingerprint_integer(u64& hash, Integer value) {
    using Unsigned      = std::make_unsigned_t<Integer>;
    const Unsigned bits = static_cast<Unsigned>(value);
    for (usize byte = 0; byte < sizeof(Integer); ++byte)
    {
        hash ^= static_cast<u8>(bits >> (byte * 8));
        hash *= FnvPrime;
    }
}

template<typename Range>
void fingerprint_range(u64& hash, const Range& values) {
    for (const auto value : values)
        fingerprint_integer(hash, value);
}

template<typename Emission>
void fingerprint_emission(u64& hash, const Emission& emission) {
    fingerprint_integer(hash, emission.size);
    for (IndexType index = 0; index < emission.size; ++index)
        fingerprint_integer(hash, emission.features[index].physicalIndex);
}

u64 fingerprint(const ScalarDiagnostic&                          value,
                const std::array<ScalarHmPerspective, COLOR_NB>& hmOnly) {
    u64 hash = FnvOffset;
    fingerprint_integer(hash, value.sideToMove);
    fingerprint_integer(hash, value.networkBucket);
    for (const Color perspective : {WHITE, BLACK})
    {
        const usize index = static_cast<usize>(perspective);
        const auto& item  = value.perspectives[index];
        fingerprint_integer(hash, item.perspective);
        fingerprint_emission(hash, item.emission.hm);
        fingerprint_emission(hash, item.emission.capturePairs);
        fingerprint_emission(hash, item.emission.kingBlastEp);
        fingerprint_emission(hash, item.emission.blastRing);
        fingerprint_range(hash, item.accumulator);
        fingerprint_range(hash, item.psqt);
        fingerprint_range(hash, hmOnly[index].accumulator);
        fingerprint_range(hash, hmOnly[index].psqt);
    }
    fingerprint_range(hash, value.transformed);
    fingerprint_integer(hash, value.psqtDifference);
    fingerprint_integer(hash, value.psqtValue);
    fingerprint_range(hash, value.dense.fc0);
    fingerprint_range(hash, value.dense.fc0Squared);
    fingerprint_range(hash, value.dense.fc0Clipped);
    fingerprint_range(hash, value.dense.fc1);
    fingerprint_range(hash, value.dense.fc1Squared);
    fingerprint_range(hash, value.dense.fc1Clipped);
    fingerprint_range(hash, value.dense.fc2);
    fingerprint_integer(hash, value.rawOutput);
    fingerprint_integer(hash, value.scaledOutput);
    fingerprint_integer(hash, value.positionalValue);
    return hash;
}

bool same_orientation(const JointOrientation& lhs, const JointOrientation& rhs) {
    return lhs.perspective == rhs.perspective && lhs.ownKing == rhs.ownKing
        && lhs.orientedOwnKing == rhs.orientedOwnKing && lhs.verticalXor == rhs.verticalXor
        && lhs.horizontalXor == rhs.horizontalXor && lhs.kingBucket == rhs.kingBucket;
}

template<typename Range>
std::string range_difference(std::string_view label, const Range& lhs, const Range& rhs) {
    if (lhs.size() != rhs.size())
        return std::string(label) + " size mismatch";
    for (usize index = 0; index < lhs.size(); ++index)
        if (lhs[index] != rhs[index])
        {
            std::ostringstream detail;
            detail << label << '[' << index
                   << "] mismatch: actual=" << static_cast<long long>(lhs[index])
                   << " expected=" << static_cast<long long>(rhs[index]);
            return detail.str();
        }
    return {};
}

template<typename Emission>
std::string emission_difference(std::string_view label, const Emission& lhs, const Emission& rhs) {
    if (!same_orientation(lhs.orientation, rhs.orientation))
        return std::string(label) + ".orientation mismatch";
    if (lhs.size != rhs.size)
        return std::string(label) + ".size mismatch";
    for (IndexType index = 0; index < lhs.size; ++index)
        if (lhs.features[index].physicalIndex != rhs.features[index].physicalIndex)
            return std::string(label) + ".rows mismatch";
    return {};
}

std::string scalar_difference(const ScalarDiagnostic& actual, const ScalarDiagnostic& expected) {
    if (actual.sideToMove != expected.sideToMove)
        return "side_to_move mismatch";
    if (actual.networkBucket != expected.networkBucket)
        return "network_bucket mismatch";

    for (const Color perspective : {WHITE, BLACK})
    {
        const usize       index = static_cast<usize>(perspective);
        const auto&       lhs   = actual.perspectives[index];
        const auto&       rhs   = expected.perspectives[index];
        const std::string side  = perspective == WHITE ? "white" : "black";
        if (lhs.perspective != rhs.perspective)
            return side + ".perspective mismatch";
        if (lhs.emission.hm.networkBucket != rhs.emission.hm.networkBucket)
            return side + ".hm.network_bucket mismatch";
        if (auto detail = emission_difference(side + ".hm", lhs.emission.hm, rhs.emission.hm);
            !detail.empty())
            return detail;
        if (auto detail = emission_difference(side + ".capture_pair", lhs.emission.capturePairs,
                                              rhs.emission.capturePairs);
            !detail.empty())
            return detail;
        if (auto detail = emission_difference(side + ".king_blast_ep", lhs.emission.kingBlastEp,
                                              rhs.emission.kingBlastEp);
            !detail.empty())
            return detail;
        if (auto detail = emission_difference(side + ".blast_ring", lhs.emission.blastRing,
                                              rhs.emission.blastRing);
            !detail.empty())
            return detail;
        if (auto detail = range_difference(side + ".accumulator", lhs.accumulator, rhs.accumulator);
            !detail.empty())
            return detail;
        if (auto detail = range_difference(side + ".psqt", lhs.psqt, rhs.psqt); !detail.empty())
            return detail;
    }

    if (auto detail = range_difference("transformed", actual.transformed, expected.transformed);
        !detail.empty())
        return detail;
    if (actual.psqtDifference != expected.psqtDifference || actual.psqtValue != expected.psqtValue)
        return "PSQT scalar mismatch";
    if (auto detail = range_difference("fc0", actual.dense.fc0, expected.dense.fc0);
        !detail.empty())
        return detail;
    if (auto detail =
          range_difference("fc0_squared", actual.dense.fc0Squared, expected.dense.fc0Squared);
        !detail.empty())
        return detail;
    if (auto detail =
          range_difference("fc0_clipped", actual.dense.fc0Clipped, expected.dense.fc0Clipped);
        !detail.empty())
        return detail;
    if (auto detail = range_difference("fc1", actual.dense.fc1, expected.dense.fc1);
        !detail.empty())
        return detail;
    if (auto detail =
          range_difference("fc1_squared", actual.dense.fc1Squared, expected.dense.fc1Squared);
        !detail.empty())
        return detail;
    if (auto detail =
          range_difference("fc1_clipped", actual.dense.fc1Clipped, expected.dense.fc1Clipped);
        !detail.empty())
        return detail;
    if (auto detail = range_difference("fc2", actual.dense.fc2, expected.dense.fc2);
        !detail.empty())
        return detail;
    if (actual.rawOutput != expected.rawOutput)
        return "raw_output mismatch";
    if (actual.scaledOutput != expected.scaledOutput)
        return "scaled_output mismatch";
    if (actual.positionalValue != expected.positionalValue)
        return "positional_value mismatch";
    return {};
}

std::string hm_only_difference(const std::array<ScalarHmPerspective, COLOR_NB>& actual,
                               const std::array<ScalarHmPerspective, COLOR_NB>& expected) {
    for (const Color perspective : {WHITE, BLACK})
    {
        const usize       index = static_cast<usize>(perspective);
        const std::string side  = perspective == WHITE ? "white" : "black";
        if (auto detail = range_difference(side + ".hm_only.accumulator", actual[index].accumulator,
                                           expected[index].accumulator);
            !detail.empty())
            return detail;
        if (auto detail =
              range_difference(side + ".hm_only.psqt", actual[index].psqt, expected[index].psqt);
            !detail.empty())
            return detail;
    }
    return {};
}

struct PreparedCase {
    const CaseSpec*                           spec = nullptr;
    CapturePairSnapshot                       root{};
    CapturePairSnapshot                       child{};
    DirtyPiece                                dirty{};
    ScalarDiagnostic                          rootFresh{};
    ScalarDiagnostic                          childFresh{};
    std::array<ScalarHmPerspective, COLOR_NB> childHm{};
    usize                                     blastSize   = 0;
    u64                                       fingerprint = 0;
};

void set_position(Position& position, StateInfo& state, const CaseSpec& spec) {
    if (auto error = position.set(std::string(spec.fen), false, &state))
        die("invalid " + std::string(spec.name) + " fixture: " + error->what());
}

Move require_move(const Position& position, const CaseSpec& spec) {
    const Move move = UCI::to_move(position, std::string(spec.uci));
    if (!move)
        die("illegal " + std::string(spec.name) + " fixture move: " + std::string(spec.uci));
    if (move.type_of() != spec.moveType)
        die("wrong move type for " + std::string(spec.name));
    return move;
}

std::unique_ptr<PreparedCase> prepare_case(const Network& network, const CaseSpec& spec) {
    auto                     result = std::make_unique<PreparedCase>();
    Position                 position;
    std::array<StateInfo, 2> states{};
    set_position(position, states[0], spec);
    result->spec = &spec;
    result->root = make_capture_pair_snapshot(position);
    require(bool(evaluate_scalar(network, result->root, result->rootFresh)),
            "fresh root oracle failed for " + std::string(spec.name));

    const Move move = require_move(position, spec);
    position.do_move(move, states[1], position.gives_check(move), result->dirty, nullptr, nullptr);
    result->child     = make_capture_pair_snapshot(position);
    result->blastSize = result->dirty.atomicBlast.size();
    require(result->blastSize >= spec.minimumBlast && result->blastSize <= spec.maximumBlast,
            "unexpected blast size for " + std::string(spec.name));
    require(bool(evaluate_scalar(network, result->child, result->childFresh)),
            "fresh child oracle failed for " + std::string(spec.name));
    for (const Color perspective : {WHITE, BLACK})
    {
        const usize index = static_cast<usize>(perspective);
        require(accumulate_hm_scalar(network, result->childFresh.perspectives[index].emission.hm,
                                     result->childHm[index])
                  == ScalarError::None,
                "fresh HM oracle failed for " + std::string(spec.name));
    }
    result->fingerprint = fingerprint(result->childFresh, result->childHm);
    return result;
}

struct TransitionMetadata {
    u64 removedRows = 0;
    u64 addedRows   = 0;
};

void validate_transition(const PreparedCase&          fixture,
                         SimdIsa                      requestedIsa,
                         const IncrementalStatus&     status,
                         const IncrementalDiagnostic& actual,
                         TransitionMetadata*          metadata) {
    const std::string label = std::string(fixture.spec->name) + '/' + simd_isa_name(requestedIsa);
    require(bool(status),
            label + " incremental evaluation failed: " + incremental_error_message(status.error));
    require(status.featureError == FullRefreshError::None && bool(status.scalarStatus),
            label + " retained a nested error");
    if (const std::string detail = scalar_difference(actual.scalar, fixture.childFresh);
        !detail.empty())
        die(label + " scalar exactness failure: " + detail);
    if (const std::string detail = hm_only_difference(actual.hmOnly, fixture.childHm);
        !detail.empty())
        die(label + " HM exactness failure: " + detail);

    u64 semanticRows = 0;
    for (const Color perspective : {WHITE, BLACK})
    {
        const auto& update = actual.hmUpdates[static_cast<usize>(perspective)];
        require(update.source == HmSourceKind::StackDelta && update.sourceDistance == 1,
                label + " did not execute a one-ply HM stack delta");
        semanticRows += update.removedRows + update.addedRows;
    }
    require(actual.hmDelta.enabled && actual.hmDelta.requestedIsa == requestedIsa
              && actual.hmDelta.executedIsa == requestedIsa,
            label + " did not execute the exact requested policy");
    require(actual.hmDelta.counters.kernel_calls() == semanticRows,
            label + " kernel accounting differs from semantic row operations");
    require(actual.hmDelta.counters.fallback_calls(requestedIsa) == 0,
            label + " used a fallback kernel");
    require(fingerprint(actual.scalar, actual.hmOnly) == fixture.fingerprint,
            label + " exact fingerprint differs from the fresh oracle");
    if (metadata)
    {
        metadata->removedRows = actual.hmDelta.counters.removedRows;
        metadata->addedRows   = actual.hmDelta.counters.addedRows;
    }
}

class TransitionRunner final {
   public:
    TransitionRunner(const Network& network, const PreparedCase& fixture, SimdIsa requestedIsa) :
        network_(network),
        fixture_(fixture),
        requestedIsa_(requestedIsa),
        stack_(std::make_unique<IncrementalStack>(network, requestedIsa)),
        diagnostic_(std::make_unique<IncrementalDiagnostic>()) {
        IncrementalStatus status = stack_->evaluate(network_, fixture_.root, *diagnostic_);
        require(bool(status), std::string(fixture_.spec->name) + " root initialization failed");
        if (const std::string detail = scalar_difference(diagnostic_->scalar, fixture_.rootFresh);
            !detail.empty())
            die(std::string(fixture_.spec->name) + " root exactness failure: " + detail);
    }

    u64 sample(unsigned repetitions, u64& sink) {
        const auto        start = std::chrono::steady_clock::now();
        IncrementalStatus lastStatus{};
        for (unsigned repetition = 0; repetition < repetitions; ++repetition)
        {
            DirtyPiece& dirty = stack_->push();
            dirty             = fixture_.dirty;
            lastStatus        = stack_->evaluate(network_, fixture_.child, *diagnostic_);
            stack_->pop();
            if (!lastStatus)
                die(std::string(fixture_.spec->name)
                    + " timed transition failed: " + incremental_error_message(lastStatus.error));
        }
        const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now() - start);
        validate_transition(fixture_, requestedIsa_, lastStatus, *diagnostic_, nullptr);
        fingerprint_integer(sink, fingerprint(diagnostic_->scalar, diagnostic_->hmOnly));
        require(elapsed.count() > 0, std::string(fixture_.spec->name) + " timer returned zero");
        return static_cast<u64>(elapsed.count());
    }

    TransitionMetadata preflight(u64& sink) {
        DirtyPiece& dirty              = stack_->push();
        dirty                          = fixture_.dirty;
        const IncrementalStatus status = stack_->evaluate(network_, fixture_.child, *diagnostic_);
        stack_->pop();
        TransitionMetadata metadata{};
        validate_transition(fixture_, requestedIsa_, status, *diagnostic_, &metadata);
        fingerprint_integer(sink, fingerprint(diagnostic_->scalar, diagnostic_->hmOnly));
        return metadata;
    }

   private:
    const Network&                         network_;
    const PreparedCase&                    fixture_;
    SimdIsa                                requestedIsa_;
    std::unique_ptr<IncrementalStack>      stack_;
    std::unique_ptr<IncrementalDiagnostic> diagnostic_;
};

struct CaseSamples {
    TransitionMetadata      metadata{};
    std::array<u64, Trials> scalar{};
    std::array<u64, Trials> required{};
};

template<std::size_t Count>
u64 median(std::array<u64, Count> values) {
    std::sort(values.begin(), values.end());
    return values[values.size() / 2];
}

template<std::size_t Count>
void print_csv(std::string_view label, const std::array<u64, Count>& values) {
    std::cout << label << '=';
    for (usize index = 0; index < values.size(); ++index)
    {
        if (index)
            std::cout << ',';
        std::cout << values[index];
    }
    std::cout << '\n';
}

const char* wire_policy_name() {
    constexpr std::array<usize, 8> Identity{{0, 1, 2, 3, 4, 5, 6, 7}};
    constexpr std::array<usize, 8> Avx2Lasx{{0, 2, 1, 3, 4, 6, 5, 7}};
    constexpr std::array<usize, 8> Avx512{{0, 2, 4, 6, 1, 3, 5, 7}};
    if (WireIO::ParameterBlockOrder == Identity)
        return "identity";
    if (WireIO::ParameterBlockOrder == Avx2Lasx)
        return "avx2_lasx";
    if (WireIO::ParameterBlockOrder == Avx512)
        return "avx512";
    return "unknown";
}

bool parse_isa(std::string_view text, SimdIsa& isa) {
    if (text == "scalar")
        isa = SimdIsa::Scalar;
    else if (text == "sse41")
        isa = SimdIsa::Sse41;
    else if (text == "avx2")
        isa = SimdIsa::Avx2;
    else
        return false;
    return true;
}

struct Options {
    std::filesystem::path net;
    SimdIsa               requiredIsa = SimdIsa::Scalar;
};

Options parse_options(int argc, char* argv[]) {
    Options options{};
    bool    haveNet = false;
    bool    haveIsa = false;
    for (int index = 1; index < argc; ++index)
    {
        const std::string_view argument = argv[index];
        if (argument == "--net" && index + 1 < argc && !haveNet)
        {
            options.net = argv[++index];
            haveNet     = true;
        }
        else if (argument == "--require-isa" && index + 1 < argc && !haveIsa)
        {
            if (!parse_isa(argv[++index], options.requiredIsa))
                die("--require-isa expects scalar, sse41 or avx2");
            haveIsa = true;
        }
        else
            die("unknown, duplicate or incomplete argument: " + std::string(argument));
    }
    if (!haveNet || !haveIsa)
        die("usage: atomic-v3-incremental-simd-benchmark --net FILE --require-isa "
            "scalar|sse41|avx2");
    return options;
}

int run_benchmark(const Network& network, SimdIsa requiredIsa) {
    std::vector<std::unique_ptr<PreparedCase>> fixtures;
    fixtures.reserve(CaseSpecs.size());
    for (const auto& spec : CaseSpecs)
        fixtures.push_back(prepare_case(network, spec));

    std::vector<std::unique_ptr<TransitionRunner>> scalarRunners;
    std::vector<std::unique_ptr<TransitionRunner>> requiredRunners;
    scalarRunners.reserve(fixtures.size());
    requiredRunners.reserve(fixtures.size());
    for (const auto& fixture : fixtures)
    {
        scalarRunners.push_back(
          std::make_unique<TransitionRunner>(network, *fixture, SimdIsa::Scalar));
        requiredRunners.push_back(
          std::make_unique<TransitionRunner>(network, *fixture, requiredIsa));
    }

    u64                      sink = FnvOffset;
    std::vector<CaseSamples> samples(fixtures.size());
    for (usize index = 0; index < fixtures.size(); ++index)
    {
        const TransitionMetadata scalar   = scalarRunners[index]->preflight(sink);
        const TransitionMetadata required = requiredRunners[index]->preflight(sink);
        require(scalar.removedRows == required.removedRows
                  && scalar.addedRows == required.addedRows,
                std::string(fixtures[index]->spec->name)
                  + " semantic row accounting differs between policies");
        samples[index].metadata = required;
    }

    for (unsigned warmup = 0; warmup < Warmups; ++warmup)
        for (usize index = 0; index < fixtures.size(); ++index)
        {
            scalarRunners[index]->sample(RepetitionsPerCase / 8, sink);
            requiredRunners[index]->sample(RepetitionsPerCase / 8, sink);
        }

    for (unsigned trial = 0; trial < Trials; ++trial)
        for (usize index = 0; index < fixtures.size(); ++index)
        {
            if ((trial & 1U) == 0)
            {
                samples[index].scalar[trial] =
                  scalarRunners[index]->sample(RepetitionsPerCase, sink);
                samples[index].required[trial] =
                  requiredRunners[index]->sample(RepetitionsPerCase, sink);
            }
            else
            {
                samples[index].required[trial] =
                  requiredRunners[index]->sample(RepetitionsPerCase, sink);
                samples[index].scalar[trial] =
                  scalarRunners[index]->sample(RepetitionsPerCase, sink);
            }
        }

    std::array<u64, Trials> scalarTotals{};
    std::array<u64, Trials> requiredTotals{};
    for (usize index = 0; index < fixtures.size(); ++index)
        for (unsigned trial = 0; trial < Trials; ++trial)
        {
            scalarTotals[trial] += samples[index].scalar[trial];
            requiredTotals[trial] += samples[index].required[trial];
        }

    std::cout << "record=incremental_simd_benchmark_identity\n"
              << "network.version=0x" << std::hex << std::uppercase << std::setfill('0')
              << std::setw(8) << Network::version() << '\n'
              << "network.hash=0x" << Network::network_hash() << std::dec << std::setfill(' ')
              << '\n'
              << "network.description=" << network.description() << '\n'
              << "wire.policy=" << wire_policy_name() << '\n'
              << "wire.simd_permuted=" << (network.simd_permuted() ? 1 : 0) << '\n'
              << "isa.requested=" << simd_isa_name(requiredIsa) << '\n'
              << "isa.maximum=" << simd_isa_name(maximum_simd_isa()) << '\n'
              << "end_identity=1\n";

    for (usize index = 0; index < fixtures.size(); ++index)
    {
        const u64    scalarMedian   = median(samples[index].scalar);
        const u64    requiredMedian = median(samples[index].required);
        const double ratio          = double(scalarMedian) / double(requiredMedian);
        std::cout << "record=transition_case\n"
                  << "case=" << fixtures[index]->spec->name << '\n'
                  << "fen=" << fixtures[index]->spec->fen << '\n'
                  << "move=" << fixtures[index]->spec->uci << '\n'
                  << "blast_size=" << fixtures[index]->blastSize << '\n'
                  << "removed_rows=" << samples[index].metadata.removedRows << '\n'
                  << "added_rows=" << samples[index].metadata.addedRows << '\n';
        print_csv("scalar_ns", samples[index].scalar);
        print_csv("required_ns", samples[index].required);
        std::cout << "scalar_median_ns=" << scalarMedian << '\n'
                  << "required_median_ns=" << requiredMedian << '\n'
                  << std::fixed << std::setprecision(6) << "speed_ratio=" << ratio << '\n'
                  << std::defaultfloat << "exactness=1\n"
                  << "fingerprint=0x" << std::hex << std::uppercase << std::setfill('0')
                  << std::setw(16) << fixtures[index]->fingerprint << std::dec << std::setfill(' ')
                  << '\n'
                  << "end_case=" << fixtures[index]->spec->name << '\n';
    }

    const u64    scalarMedian   = median(scalarTotals);
    const u64    requiredMedian = median(requiredTotals);
    const double ratio          = double(scalarMedian) / double(requiredMedian);
    std::cout << "record=incremental_simd_benchmark_summary\n"
              << "isa.requested=" << simd_isa_name(requiredIsa) << '\n'
              << "isa.executed=" << simd_isa_name(requiredIsa) << '\n'
              << "cases=" << fixtures.size() << '\n'
              << "warmups=" << Warmups << '\n'
              << "trials=" << Trials << '\n'
              << "repetitions_per_case=" << RepetitionsPerCase << '\n'
              << "alternating_trials=1\n"
              << "exactness=1\n";
    print_csv("scalar_total_ns", scalarTotals);
    print_csv("required_total_ns", requiredTotals);
    std::cout << "scalar_median_ns=" << scalarMedian << '\n'
              << "required_median_ns=" << requiredMedian << '\n'
              << std::fixed << std::setprecision(6) << "speed_ratio=" << ratio << '\n'
              << std::defaultfloat << "sink=0x" << std::hex << std::uppercase << sink << std::dec
              << '\n'
              << "end_summary=1\n"
              << "AtomicNNUEV3 incremental SIMD benchmark passed: requested="
              << simd_isa_name(requiredIsa) << " executed=" << simd_isa_name(requiredIsa)
              << " cases=" << fixtures.size() << " warmups=" << Warmups << " trials=" << Trials
              << " ratio=" << std::fixed << std::setprecision(6) << ratio << " exactness=1"
              << std::defaultfloat << '\n';
    return EXIT_SUCCESS;
}

}  // namespace
}  // namespace Stockfish::Eval::NNUE::AtomicV3

int main(int argc, char* argv[]) {
    using namespace Stockfish;
    using namespace Stockfish::Eval::NNUE::AtomicV3;

    Bitboards::init();
    Attacks::init();
    Position::init();

    const Options options = parse_options(argc, argv);
    if (!simd_isa_available(options.requiredIsa))
        die("required ISA is not compiled into this binary");
    LoadResult loaded = load_candidate(options.net);
    if (!loaded)
        die("network load failed: " + loaded.error);
    return run_benchmark(*loaded.network, options.requiredIsa);
}
