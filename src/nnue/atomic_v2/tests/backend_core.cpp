/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <algorithm>
#include <array>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <initializer_list>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>
#include <tuple>

#include "../../../attacks.h"
#include "../../../bitboard.h"
#include "../../../position.h"
#include "../../nnue_misc.h"
#include "../io.h"
#include "../network.h"

namespace Stockfish::Eval::NNUE::AtomicV2 {
namespace {

namespace fs = std::filesystem;

int failures = 0;

void expect(bool condition, const std::string& label) {
    if (condition)
        std::cout << "PASS " << label << '\n';
    else
    {
        std::cerr << "FAIL " << label << '\n';
        ++failures;
    }
}

template<typename IntType, usize Count>
bool round_trip_leb(const std::array<IntType, Count>& input) {
    std::ostringstream output(std::ios::binary);
    if (!IO::write_signed_leb(output, input))
        return false;

    std::array<IntType, Count> decoded{};
    std::istringstream         inputStream(output.str(), std::ios::binary);
    return IO::read_signed_leb(inputStream, decoded) && decoded == input;
}

template<typename IntType>
std::string leb_payload(u32 byteCount, std::initializer_list<u8> bytes) {
    std::ostringstream stream(std::ios::binary);
    stream.write(IO::Leb128Magic, std::streamsize(IO::Leb128MagicLength));
    IO::write_little_endian(stream, byteCount);
    for (const u8 byte : bytes)
        stream.put(static_cast<char>(byte));
    return stream.str();
}

template<typename IntType>
bool rejects_leb(u32 byteCount, std::initializer_list<u8> bytes) {
    std::array<IntType, 1> value{};
    std::istringstream     stream(leb_payload<IntType>(byteCount, bytes), std::ios::binary);
    return !IO::read_signed_leb(stream, value);
}

bool files_equal(const fs::path& lhsPath, const fs::path& rhsPath) {
    std::error_code error;
    const auto      lhsSize = fs::file_size(lhsPath, error);
    if (error)
        return false;
    const auto rhsSize = fs::file_size(rhsPath, error);
    if (error || lhsSize != rhsSize)
        return false;

    std::ifstream               lhs(lhsPath, std::ios::binary);
    std::ifstream               rhs(rhsPath, std::ios::binary);
    std::array<char, 64 * 1024> lhsBuffer{};
    std::array<char, 64 * 1024> rhsBuffer{};
    while (lhs && rhs)
    {
        lhs.read(lhsBuffer.data(), std::streamsize(lhsBuffer.size()));
        rhs.read(rhsBuffer.data(), std::streamsize(rhsBuffer.size()));
        if (lhs.gcount() != rhs.gcount()
            || !std::equal(lhsBuffer.begin(), lhsBuffer.begin() + lhs.gcount(), rhsBuffer.begin()))
            return false;
    }
    return lhs.eof() && rhs.eof();
}

bool copy_with_trailing_byte(const fs::path& source, const fs::path& destination) {
    std::ifstream input(source, std::ios::binary);
    std::ofstream output(destination, std::ios::binary | std::ios::trunc);
    output << input.rdbuf();
    output.put(static_cast<char>(0xA5));
    return bool(input) && bool(output);
}

constexpr std::array<i32, LayerStacks>   ExpectedRawByBucket   = {10587, 10662, 10737, 10812,
                                                                  10887, 10962, 11037, 11112};
constexpr std::array<Value, LayerStacks> ExpectedValueByBucket = {
  Value(661), Value(666), Value(671), Value(675), Value(680), Value(685), Value(689), Value(694)};
constexpr std::array<usize, 20> ExpectedNnzGroups = {
  0, 3, 4, 7, 8, 15, 16, 63, 64, 127, 128, 131, 132, 135, 136, 143, 144, 191, 192, 255};

bool exact_nnz_groups(const NNZInfo<L1>& info) {
#if defined(USE_AVX512)
    if (info.count != ExpectedNnzGroups.size())
        return false;

    std::array<usize, ExpectedNnzGroups.size()> actual{};
    std::copy_n(info.nnz, actual.size(), actual.begin());
    std::sort(actual.begin(), actual.end());
    return std::equal(actual.begin(), actual.end(), ExpectedNnzGroups.begin());
#elif defined(VECTOR) || defined(USE_RVV)
    for (usize group = 0; group < L1 / 4; ++group)
    {
        const bool actual = ((info.bitset[group / 8] >> (group % 8)) & 1U) != 0;
        const bool expected =
          std::binary_search(ExpectedNnzGroups.begin(), ExpectedNnzGroups.end(), group);
        if (actual != expected)
            return false;
    }
    return true;
#else
    // Portable scalar propagation deliberately does not consume NNZInfo. The
    // exact group assertion runs in every SIMD and WASM CI build.
    (void) info;
    return true;
#endif
}

void test_strict_leb() {
    expect(round_trip_leb(std::array<i16, 7>{-32768, -129, -1, 0, 1, 127, 32767}),
           "canonical signed LEB round trip");
    expect(rejects_leb<i16>(2, {0x80, 0x00}), "non-canonical signed LEB rejected");
    expect(rejects_leb<i16>(1, {0x80}), "unterminated signed LEB rejected");
    expect(rejects_leb<i16>(2, {0x00}), "truncated declared LEB payload rejected");
    expect(rejects_leb<i8>(2, {0x80, 0x01}), "out-of-range signed LEB rejected");

    std::string badMagic = leb_payload<i16>(1, {0x00});
    badMagic[0] ^= 1;
    std::array<i16, 1> value{};
    std::istringstream stream(badMagic, std::ios::binary);
    expect(!IO::read_signed_leb(stream, value), "invalid signed LEB magic rejected");
}

void write_header(std::ostream& stream, u32 version, u32 hash, u32 descriptionSize) {
    IO::write_little_endian(stream, version);
    IO::write_little_endian(stream, hash);
    IO::write_little_endian(stream, descriptionSize);
}

void test_strict_headers(Network& candidate) {
    std::ostringstream wrongVersion(std::ios::binary);
    write_header(wrongVersion, FileVersion ^ 1U, NetworkHash, 0);
    std::istringstream wrongVersionInput(wrongVersion.str(), std::ios::binary);
    expect(!load_candidate(wrongVersionInput, candidate) && !candidate.initialized(),
           "wrong Atomic V2 version rejected");

    std::ostringstream wrongHash(std::ios::binary);
    write_header(wrongHash, FileVersion, NetworkHash ^ 1U, 0);
    std::istringstream wrongHashInput(wrongHash.str(), std::ios::binary);
    expect(!load_candidate(wrongHashInput, candidate) && !candidate.initialized(),
           "wrong Atomic V2 network hash rejected");

    std::ostringstream oversized(std::ios::binary);
    write_header(oversized, FileVersion, NetworkHash, (1U << 20) + 1U);
    std::istringstream oversizedInput(oversized.str(), std::ios::binary);
    expect(!load_candidate(oversizedInput, candidate) && !candidate.initialized(),
           "oversized Atomic V2 description rejected");

    std::ostringstream wrongTransformer(std::ios::binary);
    write_header(wrongTransformer, FileVersion, NetworkHash, 0);
    IO::write_little_endian(wrongTransformer, FeatureTransformerHash ^ 1U);
    std::istringstream wrongTransformerInput(wrongTransformer.str(), std::ios::binary);
    expect(!load_candidate(wrongTransformerInput, candidate) && !candidate.initialized(),
           "wrong Atomic V2 transformer hash rejected");
}

void test_controlled_network(const fs::path& fixture) {
    constexpr std::string_view ExpectedDescription =
      "Atomic-Stockfish AtomicNNUEV2 controlled synthetic CI source";

    const fs::path roundTrip = fixture.string() + ".roundtrip";
    const fs::path trailing  = fixture.string() + ".trailing";
    fs::remove(roundTrip);
    fs::remove(trailing);

    auto       network = std::make_unique<Network>();
    const auto loaded  = load_candidate({}, fixture, *network);
    expect(bool(loaded), "controlled Atomic V2 fixture loads");
    if (!loaded)
    {
        std::cerr << "parser error: " << loaded.error << '\n';
        return;
    }

    expect(network->initialized(), "controlled Atomic V2 fixture initializes candidate");
    expect(loaded.description == ExpectedDescription, "controlled Atomic V2 description exact");
    expect(network->get_content_hash() != 0, "controlled Atomic V2 content hash nonzero");

    Bitboards::init();
    Attacks::init();
    Position::init();

    Position   pos;
    StateInfo  state{};
    const bool invalid =
      bool(pos.set("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false, &state));
    expect(!invalid, "controlled Atomic V2 evaluation FEN accepted");

    AccumulatorStack accumulator;
    accumulator.reset();
    AccumulatorCaches caches(*network);
    const auto [rawPsqt, rawPositional] = network->evaluate_raw(pos, accumulator, caches);
    const auto [psqt, positional]       = network->evaluate(pos, accumulator, caches);
    expect(rawPsqt == 0 && rawPositional == 11112, "controlled Atomic V2 raw output is (0, 11112)");
    expect(psqt == VALUE_ZERO && positional == Value(694),
           "controlled Atomic V2 public output is (0, 694)");

    const NnueEvalTrace trace      = network->trace_evaluate(pos, accumulator, caches);
    bool                traceExact = trace.correctBucket == 7;
    for (IndexType bucket = 0; bucket < LayerStacks; ++bucket)
        traceExact = traceExact && trace.psqt[bucket] == VALUE_ZERO
                  && trace.positional[bucket] == ExpectedValueByBucket[bucket];
    expect(traceExact, "controlled Atomic V2 trace distinguishes all eight buckets");

    alignas(CacheLineSize) TransformedFeatureType transformed[FeatureTransformer::BufferSize]{};
    NNZInfo<L1>                                   nnzInfo{};
    network->feature_transformer().transform(pos, accumulator, caches, transformed, 7, nnzInfo);
    expect(exact_nnz_groups(nnzInfo),
           "controlled Atomic V2 sparse groups cover SIMD and bitset boundaries exactly");

    struct BucketFixture {
        std::string_view fen;
        int              pieces;
    };
    constexpr std::array<BucketFixture, LayerStacks> BucketFixtures = {{
      {"7k/8/8/8/8/8/P7/K7 w - - 0 1", 3},
      {"7k/8/8/8/8/8/1PPP4/K7 w - - 0 1", 5},
      {"7k/8/8/8/8/8/1PPPPPPP/K7 w - - 0 1", 9},
      {"7k/1pppp3/8/8/8/8/1PPPPPPP/K7 w - - 0 1", 13},
      {"7k/pppppppp/8/8/8/8/1PPPPPPP/K7 w - - 0 1", 17},
      {"7k/pppppppp/8/8/8/8/1PPPPPPP/KNBRQ3 w - - 0 1", 21},
      {"1nbrq2k/pppppppp/8/8/8/8/1PPPPPPP/KNBRQ3 w - - 0 1", 25},
      {"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1", 32},
    }};

    for (IndexType bucket = 0; bucket < LayerStacks; ++bucket)
    {
        Position   bucketPos;
        StateInfo  bucketState{};
        const bool bucketInvalid =
          bool(bucketPos.set(std::string(BucketFixtures[bucket].fen), false, &bucketState));
        bool bucketExact = !bucketInvalid;
        if (!bucketInvalid)
        {
            auto bucketAccumulator = std::make_unique<AccumulatorStack>();
            auto bucketCaches      = std::make_unique<AccumulatorCaches>(*network);
            bucketAccumulator->reset();
            const auto [bucketPsqt, bucketRaw] =
              network->evaluate_raw(bucketPos, *bucketAccumulator, *bucketCaches);
            const NnueEvalTrace bucketTrace =
              network->trace_evaluate(bucketPos, *bucketAccumulator, *bucketCaches);
            bucketExact = bucketPos.count<ALL_PIECES>() == BucketFixtures[bucket].pieces
                       && bucketRaw == ExpectedRawByBucket[bucket]
                       && bucketTrace.correctBucket == bucket;
            for (IndexType stack = 0; stack < LayerStacks; ++stack)
                bucketExact =
                  bucketExact && bucketTrace.positional[stack] == ExpectedValueByBucket[stack];
            (void) bucketPsqt;
        }
        expect(bucketExact,
               "controlled Atomic V2 runtime selects bucket " + std::to_string(bucket));
    }

    {
        std::ofstream output(roundTrip, std::ios::binary | std::ios::trunc);
        expect(network->save(output, loaded.description) && bool(output),
               "controlled Atomic V2 fixture serializes");
    }
    expect(files_equal(fixture, roundTrip), "Atomic V2 load/save is byte exact");
    network.reset();

    expect(copy_with_trailing_byte(fixture, trailing), "trailing-byte fixture created");
    auto       invalidCandidate = std::make_unique<Network>();
    const auto trailingResult   = load_candidate({}, trailing, *invalidCandidate);
    expect(!trailingResult && !invalidCandidate->initialized()
             && trailingResult.error.find("trailing bytes") != std::string::npos,
           "Atomic V2 parser rejects trailing bytes exactly");
    test_strict_headers(*invalidCandidate);

    fs::remove(roundTrip);
    fs::remove(trailing);
}

}  // namespace
}  // namespace Stockfish::Eval::NNUE::AtomicV2

int main(int argc, char* argv[]) {
    using namespace Stockfish::Eval::NNUE::AtomicV2;

    if (argc != 2)
    {
        std::cerr << "usage: atomic-v2-backend-core-tests <controlled-v2.nnue>\n";
        return EXIT_FAILURE;
    }

    test_strict_leb();
    test_controlled_network(argv[1]);
    if (failures != 0)
    {
        std::cerr << "Atomic V2 backend core tests failed: " << failures << '\n';
        return EXIT_FAILURE;
    }

    std::cout << "Atomic V2 backend core tests passed\n";
    return EXIT_SUCCESS;
}
