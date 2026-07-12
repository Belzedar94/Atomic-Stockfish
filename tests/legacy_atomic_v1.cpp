/*
  Focused tests for the frozen Legacy Atomic V1 training-data writer.
*/

#include <chrono>
#include <array>
#include <filesystem>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

#include "attacks.h"
#include "bitboard.h"
#include "data/legacy_atomic_v1.h"
#include "data/training_data_generator.h"
#include "position.h"
#include "tt.h"

using namespace Stockfish;

// Position::do_move() only prefetches through a non-null TT pointer. These
// codec tests never provide one, but the standalone test link still needs the
// out-of-line symbol without pulling in the complete threaded TT subsystem.
TTEntry* TranspositionTable::first_entry(Key) const { return nullptr; }

namespace {

constexpr const char* StartFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

int failures = 0;

void expect(bool condition, const char* label) {
    if (!condition)
    {
        std::cerr << "FAIL " << label << '\n';
        ++failures;
    }
}

Data::TrainingDataSample sample(Move move = Move(SQ_E2, SQ_E4)) {
    return {StartFEN, -123, move, 42, -1};
}

Data::DataResult encode(const Data::TrainingDataSample& value, Data::LegacyAtomicV1Record& record) {
    const Data::DataResult result = Data::encode_legacy_atomic_v1(value, record);
    if (!result)
        std::cerr << "encode diagnostic: " << result.message << '\n';
    return result;
}

int hex_digit(char c) {
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

std::vector<u8> decode_hex(std::string_view text) {
    std::vector<u8> bytes;
    if (text.size() % 2 != 0)
        return bytes;
    bytes.reserve(text.size() / 2);
    for (usize i = 0; i < text.size(); i += 2)
    {
        const int high = hex_digit(text[i]);
        const int low  = hex_digit(text[i + 1]);
        if (high < 0 || low < 0)
            return {};
        bytes.push_back(u8((high << 4) | low));
    }
    return bytes;
}

void test_fairy_oracle_fixture() {
    const std::array<Data::TrainingDataSample, 7> samples = {{
      {StartFEN, 42, Move(SQ_E2, SQ_E4), 321, 1},
      {"r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", 0, Move::make<CASTLING>(SQ_E1, SQ_H1), 1, 0},
      {"4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", 0, Move::make<EN_PASSANT>(SQ_E5, SQ_D6), 3, 0},
      {"4k3/P7/8/8/8/8/8/4K3 w - - 0 1", 0, Move::make<PROMOTION>(SQ_A7, SQ_A8, KNIGHT), 4, 0},
      {"4k3/P7/8/8/8/8/8/4K3 w - - 0 1", 0, Move::make<PROMOTION>(SQ_A7, SQ_A8, BISHOP), 5, 0},
      {"4k3/P7/8/8/8/8/8/4K3 w - - 0 1", 0, Move::make<PROMOTION>(SQ_A7, SQ_A8, ROOK), 6, 0},
      {"4k3/P7/8/8/8/8/8/4K3 w - - 0 1", 0, Move::make<PROMOTION>(SQ_A7, SQ_A8, QUEEN), 7, 0},
    }};

    constexpr std::string_view FairyFixtureHex =
      "08BC732CD3723CC3300CC3300C010000008220088220088EA1488AE10000000000000080070400000000000000000000000000000000000000000000000000002A001C0341010100"
      "08BC139C000000000000073800000000000000E00101000000000000000000000000000000000000000000000000000000000000000000000000000000000000000007C101000000"
      "083C000000C2000000000000000000000000802B200000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002B8903000000"
      "083C40000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000384C04000000"
      "083C40000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000385C05000000"
      "083C40000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000386C06000000"
      "083C40000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000387C07000000";

    std::vector<u8> actual;
    actual.reserve(samples.size() * Data::LegacyAtomicV1RecordSize);
    for (const auto& value : samples)
    {
        Data::LegacyAtomicV1Record record{};
        expect(bool(encode(value, record)), "Fairy oracle record encodes");
        actual.insert(actual.end(), record.begin(), record.end());
    }

    const std::vector<u8> expected = decode_hex(FairyFixtureHex);
    expect(expected.size() == 7 * Data::LegacyAtomicV1RecordSize, "Fairy oracle fixture length");
    expect(actual == expected, "full 504-byte Fairy fixture (SHA-256 C8F5C7...B229B2AA)");
}

void test_capability_and_layout() {
    expect(
      Data::atomic_data_schema_json()
        == R"({"schema_sha256":"acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1","formats":{"legacy-atomic-v1":{"read":false,"write":true,"record_size":72}}})",
      "schema capability");
    expect(
      Data::atomic_data_schemas_json()
        == R"({"capability_version":2,"formats":{"legacy-atomic-v1":{"schema_sha256":"acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1","read":false,"write":true,"header_size":0,"record_size":72},"atomic-bin-v2":{"schema_sha256":"0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6","read":false,"write":false,"header_size":96,"record_size":64}}})",
      "plural schema capability preserves V1 and advertises V2 codec-only");
    static_assert(Data::LegacyAtomicV1PackedPositionSize == 64);
    static_assert(Data::LegacyAtomicV1RecordSize == 72);
    static_assert(Data::LegacyAtomicV1MaxRule50 == 127);
    static_assert(Data::legacy_atomic_v1_rule50_fits(127));
    static_assert(!Data::legacy_atomic_v1_rule50_fits(128));

    Data::LegacyAtomicV1Record record{};
    expect(bool(encode(sample(), record)), "start position encodes");
    expect(record[64] == 0x85 && record[65] == 0xFF, "score int16 little-endian");
    expect(record[66] == 0x1C && record[67] == 0x03, "raw move uint16 little-endian");
    expect(record[68] == 0x2A && record[69] == 0x00, "ply uint16 little-endian");
    expect(record[70] == 0xFF, "result int8");
    expect(record[71] == 0, "padding zero");
}

void test_generator_draw_projection() {
    expect(!Data::legacy_atomic_v1_draw_game_fits(0, 99, 100, 1000, 0.01),
           "whole drawn game projected before retention");
    expect(Data::legacy_atomic_v1_draw_game_fits(0, 99, 100, 100, 0.01),
           "only records fitting target are projected");
    expect(Data::legacy_atomic_v1_draw_game_fits(1, 100, 1, 1000, 0.02),
           "draw game within retention accepted");
    expect(!Data::legacy_atomic_v1_draw_game_fits(0, 100, 1, 100, 1.0),
           "full target rejects another draw game");
}

void test_generator_resolution_precedence() {
    using Data::TrainingResolutionSource;

    expect(Data::training_resolution_source(true, false, true, true)
             == TrainingResolutionSource::OUTCOME,
           "decisive outcome precedes max-ply draw");
    expect(Data::training_resolution_source(true, true, false, true)
             == TrainingResolutionSource::MAX_PLY,
           "ignored insufficient outcome falls through to max ply");
    expect(Data::training_resolution_source(false, false, true, true)
             == TrainingResolutionSource::MAX_PLY,
           "nonterminal max ply is a draw");
    expect(Data::training_resolution_source(false, false, true, false)
             == TrainingResolutionSource::NONE,
           "nonterminal position below max ply continues");
}

void expect_move_wire(const char* fen, Move move, u16 wire, const char* label) {
    Data::LegacyAtomicV1Record record{};
    auto                       value = sample(move);
    value.fen                        = fen;
    expect(bool(encode(value, record)), label);
    expect(u16(record[66] | (u16(record[67]) << 8)) == wire, label);
}

void test_raw_move_wire() {
    expect_move_wire("4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", Move::make<CASTLING>(SQ_E1, SQ_H1), 0xC107,
                     "castling move wire");
    expect_move_wire("7k/8/8/3pP3/8/8/8/K7 w - d6 0 1", Move::make<EN_PASSANT>(SQ_E5, SQ_D6),
                     0x892B, "en-passant move wire");
    expect_move_wire("7k/4P3/8/8/8/8/8/K7 w - - 0 1", Move::make<PROMOTION>(SQ_E7, SQ_E8, KNIGHT),
                     0x4D3C, "knight promotion move wire");
    expect_move_wire("7k/4P3/8/8/8/8/8/K7 w - - 0 1", Move::make<PROMOTION>(SQ_E7, SQ_E8, QUEEN),
                     0x7D3C, "queen promotion move wire");
}

void test_rejections() {
    Data::LegacyAtomicV1Record record{};

    auto invalidMove = sample(Move::none());
    expect(Data::encode_legacy_atomic_v1(invalidMove, record).error
             == Data::DataError::INVALID_MOVE,
           "MOVE_NONE rejected");

    auto mismatchedMove = sample(Move(SQ_E2, SQ_E5));
    expect(Data::encode_legacy_atomic_v1(mismatchedMove, record).error
             == Data::DataError::INVALID_MOVE,
           "move mismatched with FEN rejected");
    bool allZero = true;
    for (u8 byte : record)
        allZero &= byte == 0;
    expect(allZero, "failed encode zero-fills record");

    auto chess960  = sample();
    chess960.flags = Data::TRAINING_DATA_CHESS960;
    expect(Data::encode_legacy_atomic_v1(chess960, record).error
             == Data::DataError::UNSUPPORTED_CHESS960,
           "Atomic960 rejected");

    auto missingKing = sample(Move(SQ_A1, SQ_A2));
    missingKing.fen  = "8/8/8/8/8/8/8/K7 w - - 0 1";
    expect(Data::encode_legacy_atomic_v1(missingKing, record).error
             == Data::DataError::UNSUPPORTED_POSITION,
           "missing king rejected");

    auto rule50 = sample(Move(SQ_A1, SQ_A2));
    rule50.fen  = "7k/8/8/8/8/8/8/K7 w - - 128 1";
    expect(Data::encode_legacy_atomic_v1(rule50, record).error
             == Data::DataError::POSITION_CLOCK_OUT_OF_RANGE,
           "seven-bit rule50 enforced");

    auto badScore  = sample();
    badScore.score = 32768;
    expect(Data::encode_legacy_atomic_v1(badScore, record).error
             == Data::DataError::SCORE_OUT_OF_RANGE,
           "int16 score enforced");

    auto badResult   = sample();
    badResult.result = 2;
    expect(Data::encode_legacy_atomic_v1(badResult, record).error
             == Data::DataError::RESULT_OUT_OF_RANGE,
           "result domain enforced");

    auto shortFen = sample();
    shortFen.fen  = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -";
    expect(Data::encode_legacy_atomic_v1(shortFen, record).error
             == Data::DataError::UNSUPPORTED_POSITION,
           "four-field FEN rejected");

    auto invalidClock = sample();
    invalidClock.fen  = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - nope 1";
    expect(Data::encode_legacy_atomic_v1(invalidClock, record).error
             == Data::DataError::POSITION_CLOCK_OUT_OF_RANGE,
           "non-decimal FEN clock rejected");

    auto zeroFullmove = sample();
    zeroFullmove.fen  = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 0";
    expect(Data::encode_legacy_atomic_v1(zeroFullmove, record).error
             == Data::DataError::POSITION_CLOCK_OUT_OF_RANGE,
           "zero fullmove rejected");

    auto normalizedCastling = sample(Move(SQ_E1, SQ_E2));
    normalizedCastling.fen  = "4k3/8/8/8/8/8/8/4K3 w K - 0 1";
    expect(Data::encode_legacy_atomic_v1(normalizedCastling, record).error
             == Data::DataError::UNSUPPORTED_POSITION,
           "silently normalized castling rights rejected");

    auto normalizedEp = sample(Move(SQ_H8, SQ_H7));
    normalizedEp.fen  = "7k/8/8/8/4P3/8/8/K7 b - e3 0 1";
    expect(Data::encode_legacy_atomic_v1(normalizedEp, record).error
             == Data::DataError::UNSUPPORTED_POSITION,
           "silently normalized en-passant square rejected");
}

void test_exclusive_sink() {
    namespace fs = std::filesystem;

    const auto     nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path output =
      fs::temp_directory_path() / ("atomic-stockfish-legacy-v1-" + std::to_string(nonce) + ".bin");
    const fs::path  emptyOutput   = output.string() + ".empty";
    const fs::path  partialOutput = output.string() + ".partial";
    const fs::path  scopedOutput  = output.string() + ".scoped";
    std::error_code ignored;
    fs::remove(output, ignored);
    fs::remove(emptyOutput, ignored);
    fs::remove(partialOutput, ignored);
    fs::remove(scopedOutput, ignored);

    {
        Data::LegacyAtomicV1Sink empty(emptyOutput);
        expect(empty.finalize().error == Data::DataError::EMPTY_DATASET, "empty dataset rejected");
        expect(!fs::exists(emptyOutput), "empty sink creates no invalid file");
        expect(bool(empty.abort()), "empty sink aborts");
    }

    {
        Data::LegacyAtomicV1Sink sink(output);
        expect(bool(sink.append(sample())), "first exclusive write");
        expect(sink.records_written() == 1, "record count");
        expect(bool(sink.finalize()), "written sink finalizes");
        expect(sink.append(sample()).error == Data::DataError::SINK_CLOSED,
               "closed sink rejects writes");
        expect(sink.abort().error == Data::DataError::SINK_CLOSED,
               "finalized dataset cannot be aborted");
    }

    expect(fs::file_size(output) == Data::LegacyAtomicV1RecordSize, "one exact record on disk");

    {
        Data::LegacyAtomicV1Sink partial(partialOutput);
        expect(bool(partial.append(sample())), "partial sink writes");
        expect(fs::exists(partialOutput), "partial file exists before abort");
        expect(bool(partial.abort()), "partial sink aborts");
        expect(!fs::exists(partialOutput), "abort removes partial file");
    }

    {
        Data::LegacyAtomicV1Sink scoped(scopedOutput);
        expect(bool(scoped.append(sample())), "scoped partial sink writes");
        expect(fs::exists(scopedOutput), "scoped partial file exists before destruction");
    }
    expect(!fs::exists(scopedOutput), "sink destructor removes unfinalized partial file");

    {
        Data::LegacyAtomicV1Sink duplicate(output);
        expect(duplicate.append(sample()).error == Data::DataError::OUTPUT_EXISTS,
               "overwrite and append rejected");
    }
    expect(fs::file_size(output) == Data::LegacyAtomicV1RecordSize,
           "failed duplicate leaves finalized dataset intact");

    fs::remove(output, ignored);
    fs::remove(emptyOutput, ignored);
    fs::remove(partialOutput, ignored);
    fs::remove(scopedOutput, ignored);
}

}  // namespace

int main() {
    Bitboards::init();
    Attacks::init();
    Position::init();

    test_capability_and_layout();
    test_generator_draw_projection();
    test_generator_resolution_precedence();
    test_fairy_oracle_fixture();
    test_raw_move_wire();
    test_rejections();
    test_exclusive_sink();

    if (failures)
    {
        std::cerr << failures << " Legacy Atomic V1 test(s) failed\n";
        return 1;
    }

    std::cout << "Legacy Atomic V1 tests passed\n";
    return 0;
}
