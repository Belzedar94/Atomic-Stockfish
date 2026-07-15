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
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

#include "../../../bitboard.h"
#include "../../../position.h"
#include "../simd_backend.h"
#include "../wire_io.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

constexpr u64 FnvOffset = 1469598103934665603ULL;
constexpr u64 FnvPrime  = 1099511628211ULL;

[[noreturn]] void die(const std::string& detail) {
    std::cerr << "AtomicNNUEV3 SIMD gate FAILED\n" << detail << '\n';
    std::exit(EXIT_FAILURE);
}

template<typename Integer>
void fingerprint_integer(u64& hash, Integer value) {
    using Unsigned      = std::make_unsigned_t<Integer>;
    const Unsigned bits = static_cast<Unsigned>(value);
    for (std::size_t byte = 0; byte < sizeof(Integer); ++byte)
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

u64 fingerprint(const ScalarDiagnostic& value) {
    u64 hash = FnvOffset;
    fingerprint_integer(hash, value.sideToMove);
    fingerprint_integer(hash, value.networkBucket);
    for (const auto& perspective : value.perspectives)
    {
        fingerprint_integer(hash, perspective.perspective);
        fingerprint_emission(hash, perspective.emission.hm);
        fingerprint_emission(hash, perspective.emission.capturePairs);
        fingerprint_emission(hash, perspective.emission.kingBlastEp);
        fingerprint_emission(hash, perspective.emission.blastRing);
        fingerprint_range(hash, perspective.accumulator);
        fingerprint_range(hash, perspective.psqt);
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
    for (std::size_t index = 0; index < lhs.size(); ++index)
        if (lhs[index] != rhs[index])
        {
            std::ostringstream detail;
            detail << label << '[' << index
                   << "] mismatch: simd=" << static_cast<long long>(lhs[index])
                   << " scalar=" << static_cast<long long>(rhs[index]);
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

std::string scalar_difference(const ScalarDiagnostic& simd, const ScalarDiagnostic& scalar) {
    if (simd.sideToMove != scalar.sideToMove)
        return "side_to_move mismatch";
    if (simd.networkBucket != scalar.networkBucket)
        return "network_bucket mismatch";

    for (const Color perspective : {WHITE, BLACK})
    {
        const std::size_t index = static_cast<std::size_t>(perspective);
        const auto&       lhs   = simd.perspectives[index];
        const auto&       rhs   = scalar.perspectives[index];
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

    if (auto detail = range_difference("transformed", simd.transformed, scalar.transformed);
        !detail.empty())
        return detail;
    if (simd.psqtDifference != scalar.psqtDifference || simd.psqtValue != scalar.psqtValue)
        return "PSQT scalar mismatch";
    if (auto detail = range_difference("fc0", simd.dense.fc0, scalar.dense.fc0); !detail.empty())
        return detail;
    if (auto detail =
          range_difference("fc0_squared", simd.dense.fc0Squared, scalar.dense.fc0Squared);
        !detail.empty())
        return detail;
    if (auto detail =
          range_difference("fc0_clipped", simd.dense.fc0Clipped, scalar.dense.fc0Clipped);
        !detail.empty())
        return detail;
    if (auto detail = range_difference("fc1", simd.dense.fc1, scalar.dense.fc1); !detail.empty())
        return detail;
    if (auto detail =
          range_difference("fc1_squared", simd.dense.fc1Squared, scalar.dense.fc1Squared);
        !detail.empty())
        return detail;
    if (auto detail =
          range_difference("fc1_clipped", simd.dense.fc1Clipped, scalar.dense.fc1Clipped);
        !detail.empty())
        return detail;
    if (auto detail = range_difference("fc2", simd.dense.fc2, scalar.dense.fc2); !detail.empty())
        return detail;
    if (simd.rawOutput != scalar.rawOutput || simd.scaledOutput != scalar.scaledOutput
        || simd.positionalValue != scalar.positionalValue)
        return "final scalar output mismatch";
    return {};
}

template<typename Range>
void print_csv(std::string_view key, const Range& values) {
    std::cout << key << '=';
    for (std::size_t index = 0; index < values.size(); ++index)
    {
        if (index)
            std::cout << ',';
        std::cout << static_cast<long long>(values[index]);
    }
    std::cout << '\n';
}

void print_orientation(std::string_view key, const JointOrientation& orientation) {
    std::cout << key << ".perspective=" << int(orientation.perspective) << '\n'
              << key << ".own_king=" << int(orientation.ownKing) << '\n'
              << key << ".oriented_own_king=" << int(orientation.orientedOwnKing) << '\n'
              << key << ".vertical_xor=" << int(orientation.verticalXor) << '\n'
              << key << ".horizontal_xor=" << int(orientation.horizontalXor) << '\n'
              << key << ".king_bucket=" << orientation.kingBucket << '\n';
}

template<typename Emission>
void print_rows(std::string_view key, const Emission& emission) {
    print_orientation(std::string(key) + ".orientation", emission.orientation);
    std::cout << key << ".size=" << emission.size << '\n' << key << ".rows=";
    for (IndexType index = 0; index < emission.size; ++index)
    {
        if (index)
            std::cout << ',';
        std::cout << emission.features[index].physicalIndex;
    }
    std::cout << '\n';
}

void print_diagnostic(std::string_view prefix, const ScalarDiagnostic& value) {
    std::cout << prefix << ".side_to_move=" << int(value.sideToMove) << '\n'
              << prefix << ".network_bucket=" << value.networkBucket << '\n';
    for (const Color perspective : {WHITE, BLACK})
    {
        const auto&       item = value.perspectives[static_cast<std::size_t>(perspective)];
        const std::string base = std::string(prefix) + (perspective == WHITE ? ".white" : ".black");
        std::cout << base << ".perspective=" << int(item.perspective) << '\n';
        print_rows(base + ".hm", item.emission.hm);
        std::cout << base << ".hm.network_bucket=" << item.emission.hm.networkBucket << '\n';
        print_rows(base + ".capture_pair", item.emission.capturePairs);
        print_rows(base + ".king_blast_ep", item.emission.kingBlastEp);
        print_rows(base + ".blast_ring", item.emission.blastRing);
        print_csv(base + ".accumulator", item.accumulator);
        print_csv(base + ".psqt", item.psqt);
    }
    print_csv(std::string(prefix) + ".transformed", value.transformed);
    print_csv(std::string(prefix) + ".fc0", value.dense.fc0);
    print_csv(std::string(prefix) + ".fc0_squared", value.dense.fc0Squared);
    print_csv(std::string(prefix) + ".fc0_clipped", value.dense.fc0Clipped);
    print_csv(std::string(prefix) + ".fc1", value.dense.fc1);
    print_csv(std::string(prefix) + ".fc1_squared", value.dense.fc1Squared);
    print_csv(std::string(prefix) + ".fc1_clipped", value.dense.fc1Clipped);
    print_csv(std::string(prefix) + ".fc2", value.dense.fc2);
    std::cout << prefix << ".psqt_difference=" << value.psqtDifference << '\n'
              << prefix << ".psqt_value=" << value.psqtValue << '\n'
              << prefix << ".raw_output=" << value.rawOutput << '\n'
              << prefix << ".scaled_output=" << value.scaledOutput << '\n'
              << prefix << ".positional_value=" << value.positionalValue << '\n'
              << prefix << ".fingerprint=0x" << std::hex << std::uppercase << std::setfill('0')
              << std::setw(16) << fingerprint(value) << std::dec << std::setfill(' ') << '\n';
}

void print_counters(std::string_view prefix, const SimdCounters& counters, SimdIsa requested) {
    std::cout << prefix << ".bias_i16_rows=" << counters.biasI16Rows << '\n'
              << prefix << ".hm_i16_rows=" << counters.hmI16Rows << '\n'
              << prefix << ".capture_pair_i8_rows=" << counters.capturePairI8Rows << '\n'
              << prefix << ".king_blast_ep_i16_rows=" << counters.kingBlastEpI16Rows << '\n'
              << prefix << ".blast_ring_i8_rows=" << counters.blastRingI8Rows << '\n'
              << prefix << ".i16_rows=" << counters.i16_rows() << '\n'
              << prefix << ".i8_rows=" << counters.i8_rows() << '\n'
              << prefix << ".i16_lanes=" << counters.i16Lanes << '\n'
              << prefix << ".i8_lanes=" << counters.i8Lanes << '\n'
              << prefix << ".scalar_kernel_calls=" << counters.scalarKernelCalls << '\n'
              << prefix << ".sse41_kernel_calls=" << counters.sse41KernelCalls << '\n'
              << prefix << ".avx2_kernel_calls=" << counters.avx2KernelCalls << '\n'
              << prefix << ".kernel_calls=" << counters.kernel_calls() << '\n'
              << prefix << ".fallback_calls=" << counters.fallback_calls(requested) << '\n';
}

void add_counters(SimdCounters& destination, const SimdCounters& source) {
    destination.biasI16Rows += source.biasI16Rows;
    destination.hmI16Rows += source.hmI16Rows;
    destination.capturePairI8Rows += source.capturePairI8Rows;
    destination.kingBlastEpI16Rows += source.kingBlastEpI16Rows;
    destination.blastRingI8Rows += source.blastRingI8Rows;
    destination.i16Lanes += source.i16Lanes;
    destination.i8Lanes += source.i8Lanes;
    destination.scalarKernelCalls += source.scalarKernelCalls;
    destination.sse41KernelCalls += source.sse41KernelCalls;
    destination.avx2KernelCalls += source.avx2KernelCalls;
}

Piece piece_from_fen(char token) {
    switch (token)
    {
    case 'P' :
        return W_PAWN;
    case 'N' :
        return W_KNIGHT;
    case 'B' :
        return W_BISHOP;
    case 'R' :
        return W_ROOK;
    case 'Q' :
        return W_QUEEN;
    case 'K' :
        return W_KING;
    case 'p' :
        return B_PAWN;
    case 'n' :
        return B_KNIGHT;
    case 'b' :
        return B_BISHOP;
    case 'r' :
        return B_ROOK;
    case 'q' :
        return B_QUEEN;
    case 'k' :
        return B_KING;
    default :
        return NO_PIECE;
    }
}

bool parse_placement(std::string_view placement, HmBoard& board) {
    board    = {};
    int rank = 7;
    int file = 0;
    for (const char token : placement)
    {
        if (token == '/')
        {
            if (file != 8 || rank == 0)
                return false;
            --rank;
            file = 0;
        }
        else if (token >= '1' && token <= '8')
        {
            file += token - '0';
            if (file > 8)
                return false;
        }
        else
        {
            const Piece piece = piece_from_fen(token);
            if (piece == NO_PIECE || file >= 8)
                return false;
            board[rank * 8 + file] = piece;
            ++file;
        }
    }
    return rank == 0 && file == 8;
}

bool parse_square(std::string_view text, Square& square) {
    if (text == "-")
    {
        square = SQ_NONE;
        return true;
    }
    if (text.size() != 2 || text[0] < 'a' || text[0] > 'h' || text[1] < '1' || text[1] > '8')
        return false;
    square = make_square(File(text[0] - 'a'), Rank(text[1] - '1'));
    return true;
}

bool decimal_field(std::string_view text) {
    return !text.empty() && std::all_of(text.begin(), text.end(), [](char token) {
        return token >= '0' && token <= '9';
    });
}

bool parse_fen_snapshot(const std::string& fen, CapturePairSnapshot& snapshot) {
    std::istringstream input(fen);
    std::string        placement;
    std::string        side;
    std::string        castling;
    std::string        ep;
    std::string        halfmove;
    std::string        fullmove;
    std::string        trailing;
    if (!(input >> placement >> side >> castling >> ep >> halfmove >> fullmove)
        || (input >> trailing) || (side != "w" && side != "b") || castling.empty()
        || !decimal_field(halfmove) || !decimal_field(fullmove)
        || !parse_placement(placement, snapshot.board) || !parse_square(ep, snapshot.epSquare))
        return false;
    snapshot.sideToMove = side == "w" ? WHITE : BLACK;
    return true;
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

std::string wire_policy_name() {
    constexpr std::array<std::size_t, 8> Identity{{0, 1, 2, 3, 4, 5, 6, 7}};
    constexpr std::array<std::size_t, 8> Avx2Lasx{{0, 2, 1, 3, 4, 6, 5, 7}};
    constexpr std::array<std::size_t, 8> Avx512{{0, 2, 4, 6, 1, 3, 5, 7}};
    if (WireIO::ParameterBlockOrder == Identity)
        return "identity";
    if (WireIO::ParameterBlockOrder == Avx2Lasx)
        return "avx2_lasx";
    if (WireIO::ParameterBlockOrder == Avx512)
        return "avx512";
    return "unknown";
}

void print_identity(const Network& network, SimdIsa requested) {
    std::cout << "record=simd_identity\n"
              << "network.version=0x" << std::hex << std::uppercase << std::setfill('0')
              << std::setw(8) << Network::version() << '\n'
              << "network.hash=0x" << Network::network_hash() << std::dec << std::setfill(' ')
              << '\n'
              << "network.description=" << network.description() << '\n'
              << "wire.policy=" << wire_policy_name() << '\n'
              << "wire.simd_permuted=" << (network.simd_permuted() ? 1 : 0) << '\n'
              << "isa.requested=" << simd_isa_name(requested) << '\n'
              << "isa.maximum=" << simd_isa_name(maximum_simd_isa()) << '\n'
              << "end_identity=1\n";
}

template<typename Source>
void scalar_probe_add_raw(i32* destination, const Source* source, std::size_t count) {
    volatile i32* cells = destination;
    for (std::size_t index = 0; index < count; ++index)
        cells[index] = cells[index] + static_cast<i32>(source[index]);
}

template<typename Source>
void required_probe_add_raw(SimdIsa       isa,
                            i32*          destination,
                            const Source* source,
                            std::size_t   count) {
    if (isa == SimdIsa::Scalar)
    {
        scalar_probe_add_raw(destination, source, count);
        return;
    }
    if constexpr (std::is_same_v<Source, i16>)
    {
        if (isa == SimdIsa::Sse41)
        {
#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)
            atomic_v3_add_i16_sse41_kernel(destination, source, count);
#endif
        }
        else
        {
#if defined(USE_AVX2) || defined(USE_AVX512)
            atomic_v3_add_i16_avx2_kernel(destination, source, count);
#endif
        }
    }
    else
    {
        if (isa == SimdIsa::Sse41)
        {
#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)
            atomic_v3_add_i8_sse41_kernel(destination, source, count);
#endif
        }
        else
        {
#if defined(USE_AVX2) || defined(USE_AVX512)
            atomic_v3_add_i8_avx2_kernel(destination, source, count);
#endif
        }
    }
}

template<typename Source, std::size_t Count>
void scalar_probe_add(std::array<i32, Count>&          destination,
                      const std::array<Source, Count>& source) {
    scalar_probe_add_raw(destination.data(), source.data(), Count);
}

template<typename Source, std::size_t Count>
void required_probe_add(SimdIsa                          isa,
                        std::array<i32, Count>&          destination,
                        const std::array<Source, Count>& source) {
    required_probe_add_raw(isa, destination.data(), source.data(), Count);
}

inline constexpr std::array<std::size_t, 9> TailProbeCounts{{0, 1, 3, 4, 7, 8, 15, 16, 17}};

template<typename Source>
bool run_tail_canary_checks(SimdIsa isa) {
    constexpr std::size_t                MaximumCount = TailProbeCounts.back();
    constexpr i32                        Canary       = 0x13579BDF;
    std::array<i32, MaximumCount + 2>    destination{};
    std::array<Source, MaximumCount + 2> source{};
    for (std::size_t index = 0; index < source.size(); ++index)
        source[index] = static_cast<Source>(int(index * 29 % 127) - 63);
    source[1]                 = std::numeric_limits<Source>::min();
    source[2]                 = std::numeric_limits<Source>::max();
    const auto originalSource = source;

    for (const std::size_t count : TailProbeCounts)
    {
        destination.fill(Canary);
        auto expected = destination;
        for (std::size_t index = 0; index < count; ++index)
            expected[index + 1] += static_cast<i32>(source[index + 1]);
        required_probe_add_raw(isa, destination.data() + 1, source.data() + 1, count);
        if (destination != expected || source != originalSource)
            return false;
    }
    return true;
}

template<typename Source, std::size_t Count>
void widening_probe(unsigned                         probe,
                    std::string_view                 kind,
                    SimdIsa                          isa,
                    const std::array<i32, Count>&    before,
                    const std::array<Source, Count>& source) {
    auto expected = before;
    auto actual   = before;
    for (std::size_t index = 0; index < Count; ++index)
        expected[index] += static_cast<i32>(source[index]);
    required_probe_add(isa, actual, source);
    const bool tailsExact = run_tail_canary_checks<Source>(isa);
    if (actual != expected || !tailsExact)
        die("signed widening probe " + std::string(kind) + " diverged");

    std::cout << "record=simd_widening_probe\n"
              << "probe=" << probe << '\n'
              << "kind=" << kind << '\n'
              << "isa.executed=" << simd_isa_name(isa) << '\n';
    print_csv("before", before);
    print_csv("input", source);
    print_csv("expected", expected);
    print_csv("actual", actual);
    print_csv("tail_counts", TailProbeCounts);
    std::cout << "kernel_calls=" << TailProbeCounts.size() + 1 << '\n'
              << "fallback_calls=0\n"
              << "tail_cases=" << TailProbeCounts.size() << '\n'
              << "tail_canaries=" << TailProbeCounts.size() * 2 << '\n'
              << "tails.exact=1\n"
              << "comparison.exact=1\n"
              << "end_probe=" << probe << '\n';
}

void run_widening_probes(SimdIsa isa) {
    constexpr std::array<i32, 16> Before{
      {1000, -1000, 17, -17, 40000, -40000, 7, -7, 123456, -123456, 1, -1, 99, -99, 2048, -2048}};
    constexpr std::array<i16, 16> I16{{std::numeric_limits<i16>::min(), -32767, -257, -1, 0, 1, 255,
                                       256, 32767, -8192, 8192, -2, 2, -128, 127, 42}};
    constexpr std::array<i8, 16>  I8{{std::numeric_limits<i8>::min(), -127, -65, -1, 0, 1, 63, 64,
                                      127, -2, 2, -32, 32, -100, 100, 42}};
    widening_probe(0, "i16_signed", isa, Before, I16);
    widening_probe(1, "i8_signed", isa, Before, I8);
}

bool counters_are_clear(const SimdCounters& value) {
    return value.biasI16Rows == 0 && value.hmI16Rows == 0 && value.capturePairI8Rows == 0
        && value.kingBlastEpI16Rows == 0 && value.blastRingI8Rows == 0 && value.i16Lanes == 0
        && value.i8Lanes == 0 && value.scalarKernelCalls == 0 && value.sse41KernelCalls == 0
        && value.avx2KernelCalls == 0;
}

bool diagnostic_is_clear(const SimdDiagnostic& value) {
    const ScalarDiagnostic zero{};
    return scalar_difference(value.scalar, zero).empty() && value.requestedIsa == SimdIsa::Scalar
        && value.executedIsa == SimdIsa::Scalar && counters_are_clear(value.counters);
}

struct ErrorProbe {
    std::string_view    name;
    CapturePairSnapshot snapshot;
    SimdIsa             isa;
    SimdError           expectedError;
    ScalarError         expectedScalar;
    FullRefreshError    expectedFeature;
};

void run_error_probes(const Network& network, SimdIsa requiredIsa) {
    CapturePairSnapshot valid{};
    valid.board[SQ_A1] = W_KING;
    valid.board[SQ_H8] = B_KING;

    CapturePairSnapshot invalidSide   = valid;
    invalidSide.sideToMove            = Color(COLOR_NB);
    CapturePairSnapshot missingBlack  = valid;
    missingBlack.board[SQ_H8]         = NO_PIECE;
    CapturePairSnapshot multipleWhite = valid;
    multipleWhite.board[SQ_B1]        = W_KING;

    const std::array<ErrorProbe, 4> probes{{
      {"invalid_side", invalidSide, requiredIsa, SimdError::FeatureOracleError,
       ScalarError::FeatureOracleError, FullRefreshError::InvalidSideToMove},
      {"missing_black_king", missingBlack, requiredIsa, SimdError::FeatureOracleError,
       ScalarError::FeatureOracleError, FullRefreshError::MissingBlackKing},
      {"multiple_white_kings", multipleWhite, requiredIsa, SimdError::FeatureOracleError,
       ScalarError::FeatureOracleError, FullRefreshError::MultipleWhiteKings},
      {"unsupported_isa", valid, SimdIsa(255), SimdError::UnsupportedIsa, ScalarError::None,
       FullRefreshError::None},
    }};

    for (std::size_t index = 0; index < probes.size(); ++index)
    {
        SimdScratch    scratch{};
        SimdDiagnostic output{};
        output.scalar.rawOutput     = 1;
        output.requestedIsa         = SimdIsa::Avx2;
        output.executedIsa          = SimdIsa::Avx2;
        output.counters.biasI16Rows = 1;
        const SimdStatus status =
          evaluate_simd(network, probes[index].snapshot, probes[index].isa, scratch, output);
        const bool exact = !status && status.error == probes[index].expectedError
                        && status.scalarStatus.code == probes[index].expectedScalar
                        && status.scalarStatus.featureError == probes[index].expectedFeature
                        && diagnostic_is_clear(output);
        if (!exact)
            die("transactional error probe " + std::string(probes[index].name) + " diverged");

        std::cout << "record=simd_error_probe\n"
                  << "error_probe=" << index << '\n'
                  << "name=" << probes[index].name << '\n'
                  << "actual.error=" << int(status.error) << '\n'
                  << "actual.scalar_error=" << int(status.scalarStatus.code) << '\n'
                  << "actual.feature_error=" << int(status.scalarStatus.featureError) << '\n'
                  << "transactional=1\n"
                  << "comparison.exact=1\n"
                  << "end_error_probe=" << index << '\n';
    }
}

void run_concurrency_probe(const Network& network, SimdIsa requiredIsa) {
    CapturePairSnapshot snapshot{};
    if (!parse_fen_snapshot("7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", snapshot))
        die("internal concurrency FEN is invalid");

    SimdScratch    baselineScratch{};
    SimdDiagnostic baseline{};
    if (!evaluate_simd(network, snapshot, requiredIsa, baselineScratch, baseline))
        die("concurrency baseline evaluation failed");
    const u64 baselineFingerprint = fingerprint(baseline.scalar);

    for (const unsigned threadCount : {1U, 2U, 4U, 8U})
    {
        std::atomic<bool>        exact{true};
        std::vector<std::thread> threads;
        threads.reserve(threadCount);
        for (unsigned threadIndex = 0; threadIndex < threadCount; ++threadIndex)
            threads.emplace_back([&] {
                SimdScratch scratch{};
                for (unsigned iteration = 0; iteration < 16; ++iteration)
                {
                    SimdDiagnostic   actual{};
                    const SimdStatus status =
                      evaluate_simd(network, snapshot, requiredIsa, scratch, actual);
                    if (!status || actual.executedIsa != requiredIsa
                        || fingerprint(actual.scalar) != baselineFingerprint
                        || !scalar_difference(actual.scalar, baseline.scalar).empty())
                    {
                        exact.store(false, std::memory_order_relaxed);
                        return;
                    }
                }
            });
        for (auto& thread : threads)
            thread.join();
        if (!exact.load(std::memory_order_relaxed))
            die("shared immutable Network diverged with independent SIMD scratch objects");
    }
}

std::pair<std::string, std::string> parse_key_value(const std::string& line) {
    const std::size_t separator = line.find('=');
    if (separator == std::string::npos || separator == 0)
        die("invalid key=value input record: " + line);
    return {line.substr(0, separator), line.substr(separator + 1)};
}

std::string require_input_line(std::string_view expectedKey) {
    std::string line;
    if (!std::getline(std::cin, line))
        die("truncated batch input while reading " + std::string(expectedKey));
    if (!line.empty() && line.back() == '\r')
        line.pop_back();
    const auto [key, value] = parse_key_value(line);
    if (key != expectedKey)
        die("expected input key " + std::string(expectedKey) + ", got " + key);
    return value;
}

unsigned parse_unsigned(std::string_view text, std::string_view label) {
    if (!decimal_field(text))
        die("invalid unsigned " + std::string(label));
    unsigned value          = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size())
        die("out-of-range unsigned " + std::string(label));
    return value;
}

int run_batch(const Network& network, SimdIsa requestedIsa) {
    run_widening_probes(requestedIsa);
    run_error_probes(network, requestedIsa);
    run_concurrency_probe(network, requestedIsa);

    unsigned     caseIndex         = 0;
    unsigned     comparisons       = 0;
    u64          corpusFingerprint = FnvOffset;
    SimdCounters totals{};
    auto         scratch = std::make_unique<SimdScratch>();
    auto         scalar  = std::make_unique<ScalarDiagnostic>();
    auto         simd    = std::make_unique<SimdDiagnostic>();

    while (true)
    {
        std::string line;
        if (!std::getline(std::cin, line))
            die("batch input ended without batch_cases");
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        const auto [key, value] = parse_key_value(line);
        if (key == "batch_cases")
        {
            if (parse_unsigned(value, "batch_cases") != caseIndex)
                die("batch_cases does not match received cases");
            break;
        }
        if (key != "record" || value != "simd_input")
            die("expected record=simd_input or batch_cases");

        const unsigned declaredCase = parse_unsigned(require_input_line("case"), "case");
        if (declaredCase != caseIndex)
            die("non-contiguous input case index");
        const std::string chess960Text = require_input_line("chess960");
        if (chess960Text != "0" && chess960Text != "1")
            die("chess960 must be 0 or 1");
        const bool        chess960 = chess960Text == "1";
        const std::string fen      = require_input_line("fen");
        if (parse_unsigned(require_input_line("end_input"), "end_input") != caseIndex)
            die("end_input does not match case");

        CapturePairSnapshot snapshot{};
        if (!parse_fen_snapshot(fen, snapshot))
            die("invalid six-field FEN in case " + std::to_string(caseIndex));

        const ScalarStatus scalarStatus = evaluate_scalar(network, snapshot, *scalar);
        const SimdStatus   simdStatus =
          evaluate_simd(network, snapshot, requestedIsa, *scratch, *simd);
        if (!scalarStatus)
            die("scalar oracle rejected case " + std::to_string(caseIndex));
        if (!simdStatus)
            die("SIMD backend rejected case " + std::to_string(caseIndex) + ": "
                + simd_error_message(simdStatus.error));
        const std::string difference = scalar_difference(simd->scalar, *scalar);
        if (!difference.empty())
            die("case " + std::to_string(caseIndex) + ": " + difference);

        const u64 scalarFingerprint = fingerprint(*scalar);
        const u64 simdFingerprint   = fingerprint(simd->scalar);
        if (scalarFingerprint != simdFingerprint || simd->requestedIsa != requestedIsa
            || simd->executedIsa != requestedIsa)
            die("case identity or fingerprint mismatch");
        fingerprint_integer(corpusFingerprint, caseIndex);
        fingerprint_integer(corpusFingerprint, scalarFingerprint);
        add_counters(totals, simd->counters);

        std::cout << "record=simd_case\n"
                  << "case=" << caseIndex << '\n'
                  << "chess960=" << (chess960 ? 1 : 0) << '\n'
                  << "fen=" << fen << '\n'
                  << "status=ok\n"
                  << "isa.requested=" << simd_isa_name(simd->requestedIsa) << '\n'
                  << "isa.executed=" << simd_isa_name(simd->executedIsa) << '\n';
        print_diagnostic("scalar", *scalar);
        print_diagnostic("simd", simd->scalar);
        print_counters("simd.counters", simd->counters, requestedIsa);
        std::cout << "comparison.exact=1\n"
                  << "end_case=" << caseIndex << '\n';
        ++caseIndex;
        ++comparisons;
    }

    std::string trailing;
    if (std::getline(std::cin, trailing))
        die("trailing batch input after batch_cases");
    if (!std::cin.eof() || std::cin.bad())
        die("batch input I/O failure");

    std::cout << "record=simd_summary\n"
              << "isa.requested=" << simd_isa_name(requestedIsa) << '\n'
              << "isa.executed=" << simd_isa_name(requestedIsa) << '\n'
              << "cases=" << caseIndex << '\n'
              << "comparisons=" << comparisons << '\n'
              << "errors=0\n"
              << "error_probes=4\n"
              << "widening_probes=2\n";
    print_counters("totals", totals, requestedIsa);
    std::cout << "corpus_fingerprint=0x" << std::hex << std::uppercase << std::setfill('0')
              << std::setw(16) << corpusFingerprint << std::dec << std::setfill(' ') << '\n'
              << "end_summary=1\n"
              << "AtomicNNUEV3 SIMD gate passed: requested=" << simd_isa_name(requestedIsa)
              << " executed=" << simd_isa_name(requestedIsa) << " cases=" << caseIndex
              << " comparisons=" << comparisons << " errors=0 error_probes=4 widening_probes=2"
              << " i16_rows=" << totals.i16_rows() << " i8_rows=" << totals.i8_rows()
              << " kernel_calls=" << totals.kernel_calls()
              << " fallback_calls=" << totals.fallback_calls(requestedIsa) << " fingerprint=0x"
              << std::hex << std::uppercase << std::setfill('0') << std::setw(16)
              << corpusFingerprint << std::dec << std::setfill(' ') << '\n';
    return EXIT_SUCCESS;
}

template<typename Source, std::size_t Count>
std::chrono::nanoseconds benchmark_kernel(SimdIsa                          isa,
                                          bool                             scalar,
                                          const std::array<Source, Count>& source,
                                          unsigned                         repetitions,
                                          u64&                             sink) {
    std::array<i32, Count> destination{};
    const auto             start = std::chrono::steady_clock::now();
    for (unsigned iteration = 0; iteration < repetitions; ++iteration)
    {
        if (scalar)
            scalar_probe_add(destination, source);
        else
            required_probe_add(isa, destination, source);
    }
    const auto elapsed = std::chrono::steady_clock::now() - start;
    for (std::size_t index = 0; index < destination.size(); index += 97)
        fingerprint_integer(sink, destination[index]);
    return std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed);
}

u64 median(std::vector<u64> values) {
    std::sort(values.begin(), values.end());
    return values[values.size() / 2];
}

int run_benchmark(SimdIsa requestedIsa, bool promotionGate) {
    constexpr std::size_t       Dimensions  = AccumulatorDimensions;
    constexpr unsigned          Warmup      = 1;
    constexpr unsigned          Repetitions = 8192;
    constexpr unsigned          Trials      = 5;
    std::array<i16, Dimensions> i16Values{};
    std::array<i8, Dimensions>  i8Values{};
    for (std::size_t index = 0; index < Dimensions; ++index)
    {
        i16Values[index] = static_cast<i16>(int(index % 511) - 255);
        i8Values[index]  = static_cast<i8>(int(index % 127) - 63);
    }

    u64 sink = FnvOffset;
    for (unsigned warmup = 0; warmup < Warmup; ++warmup)
    {
        benchmark_kernel(requestedIsa, true, i16Values, Repetitions / 8, sink);
        benchmark_kernel(requestedIsa, false, i16Values, Repetitions / 8, sink);
        benchmark_kernel(requestedIsa, true, i8Values, Repetitions / 8, sink);
        benchmark_kernel(requestedIsa, false, i8Values, Repetitions / 8, sink);
    }

    std::vector<u64> scalarTimes;
    std::vector<u64> requiredTimes;
    scalarTimes.reserve(Trials);
    requiredTimes.reserve(Trials);
    for (unsigned trial = 0; trial < Trials; ++trial)
    {
        u64 scalarNs   = 0;
        u64 requiredNs = 0;
        if ((trial & 1U) == 0)
        {
            scalarNs += benchmark_kernel(requestedIsa, true, i16Values, Repetitions, sink).count();
            requiredNs +=
              benchmark_kernel(requestedIsa, false, i16Values, Repetitions, sink).count();
            scalarNs += benchmark_kernel(requestedIsa, true, i8Values, Repetitions, sink).count();
            requiredNs +=
              benchmark_kernel(requestedIsa, false, i8Values, Repetitions, sink).count();
        }
        else
        {
            requiredNs +=
              benchmark_kernel(requestedIsa, false, i16Values, Repetitions, sink).count();
            scalarNs += benchmark_kernel(requestedIsa, true, i16Values, Repetitions, sink).count();
            requiredNs +=
              benchmark_kernel(requestedIsa, false, i8Values, Repetitions, sink).count();
            scalarNs += benchmark_kernel(requestedIsa, true, i8Values, Repetitions, sink).count();
        }
        scalarTimes.push_back(scalarNs);
        requiredTimes.push_back(requiredNs);
    }

    const u64    scalarMedian   = median(scalarTimes);
    const u64    requiredMedian = median(requiredTimes);
    const double ratio          = requiredMedian == 0 ? 0.0 : double(scalarMedian) / requiredMedian;
    if (promotionGate && !(ratio > 1.0))
        die("required SIMD kernel did not beat the scalar fallback in isolated benchmark");

    std::cout << "record=simd_benchmark\n"
              << "isa.requested=" << simd_isa_name(requestedIsa) << '\n'
              << "isa.executed=" << simd_isa_name(requestedIsa) << '\n'
              << "warmups=" << Warmup << '\n'
              << "trials=" << Trials << '\n'
              << "repetitions=" << Repetitions << '\n'
              << "promotion_gate=" << (promotionGate ? 1 : 0) << '\n';
    print_csv("scalar_ns", scalarTimes);
    print_csv("required_ns", requiredTimes);
    std::cout << "scalar_median_ns=" << scalarMedian << '\n'
              << "required_median_ns=" << requiredMedian << '\n'
              << std::fixed << std::setprecision(6) << "throughput_ratio=" << ratio << '\n'
              << std::defaultfloat << "sink=0x" << std::hex << std::uppercase << sink << std::dec
              << '\n'
              << "end_benchmark=1\n"
              << "AtomicNNUEV3 SIMD benchmark passed: requested=" << simd_isa_name(requestedIsa)
              << " executed=" << simd_isa_name(requestedIsa) << " warmups=" << Warmup
              << " trials=" << Trials << " ratio=" << std::fixed << std::setprecision(6) << ratio
              << " promotion_gate=" << (promotionGate ? 1 : 0) << std::defaultfloat << '\n';
    return EXIT_SUCCESS;
}

struct Options {
    std::filesystem::path net;
    SimdIsa               requiredIsa   = SimdIsa::Scalar;
    bool                  batch         = false;
    bool                  benchmark     = false;
    bool                  promotionGate = false;
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
        else if (argument == "--batch" && !options.batch)
            options.batch = true;
        else if (argument == "--benchmark" && !options.benchmark)
            options.benchmark = true;
        else if (argument == "--promotion-gate" && !options.promotionGate)
            options.promotionGate = true;
        else
            die("unknown, duplicate or incomplete argument: " + std::string(argument));
    }
    if (!haveNet || !haveIsa || options.batch == options.benchmark
        || (options.promotionGate
            && (!options.benchmark || options.requiredIsa == SimdIsa::Scalar)))
        die("usage: atomic-v3-simd-tests --net FILE --require-isa "
            "scalar|sse41|avx2 (--batch|--benchmark [--promotion-gate])");
    return options;
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
    print_identity(*loaded.network, options.requiredIsa);

    return options.batch ? run_batch(*loaded.network, options.requiredIsa)
                         : run_benchmark(options.requiredIsa, options.promotionGate);
}
