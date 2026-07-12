/* Focused tests for the frozen Atomic BIN V2 wire and Position adapter. */

#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <limits>
#include <string>
#include <string_view>
#include <vector>

#include "attacks.h"
#include "bitboard.h"
#include "data/atomic_bin_v2.h"
#include "position.h"
#include "tt.h"

using namespace Stockfish;

TTEntry* TranspositionTable::first_entry(Key) const { return nullptr; }

namespace {

constexpr const char* StartFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

int failures = 0;

void expect(bool condition, std::string_view label) {
    if (!condition)
    {
        std::cerr << "FAIL " << label << '\n';
        ++failures;
    }
}

int hex_digit(char value) {
    if (value >= '0' && value <= '9')
        return value - '0';
    if (value >= 'a' && value <= 'f')
        return value - 'a' + 10;
    if (value >= 'A' && value <= 'F')
        return value - 'A' + 10;
    return -1;
}

std::vector<u8> decode_hex(std::string_view text) {
    std::vector<u8> bytes;
    if (text.size() % 2)
        return bytes;
    bytes.reserve(text.size() / 2);
    for (std::size_t index = 0; index < text.size(); index += 2)
    {
        const int high = hex_digit(text[index]);
        const int low  = hex_digit(text[index + 1]);
        if (high < 0 || low < 0)
            return {};
        bytes.push_back(u8((high << 4) | low));
    }
    return bytes;
}

void write_u32(Data::AtomicBinV2Record& record, std::size_t offset, u32 value) {
    for (unsigned byte = 0; byte < 4; ++byte)
        record[offset + byte] = u8(value >> (byte * 8));
}

void write_u64(Data::AtomicBinV2Header& header, std::size_t offset, u64 value) {
    for (unsigned byte = 0; byte < 8; ++byte)
        header[offset + byte] = u8(value >> (byte * 8));
}

u32 read_u32(const Data::AtomicBinV2Record& record, std::size_t offset) {
    u32 value = 0;
    for (unsigned byte = 0; byte < 4; ++byte)
        value |= u32(record[offset + byte]) << (byte * 8);
    return value;
}

Data::TrainingDataSample sample(Move move = Move(SQ_E2, SQ_E4)) {
    return {StartFEN, -123, move, 42, -1, Data::NO_TRAINING_DATA_FLAGS};
}

Data::AtomicBinV2Record encoded(const Data::TrainingDataSample& value, std::string_view label) {
    Data::AtomicBinV2Record record{};
    const Data::DataResult  result = Data::encode_atomic_bin_v2(value, record);
    if (!result)
        std::cerr << "encode diagnostic for " << label << ": " << result.message << '\n';
    expect(bool(result), label);
    return record;
}

void expect_decode_error(Data::AtomicBinV2Record record,
                         Data::DataError         error,
                         std::string_view        label) {
    Data::AtomicBinV2RecordFields fields{};
    const Data::DataResult result = Data::decode_atomic_bin_v2_record_structural(record, fields);
    expect(!result && result.error == error, label);
}

void test_layout_and_header_golden() {
    static_assert(Data::AtomicBinV2HeaderSize == 96);
    static_assert(Data::AtomicBinV2PositionSize == 48);
    static_assert(Data::AtomicBinV2RecordSize == 64);
    static_assert(Data::AtomicBinV2Version == 2);

    expect(Data::AtomicBinV2SchemaSha256Hex
             == "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6",
           "schema digest spelling");

    Data::AtomicBinV2Header header{};
    expect(bool(Data::encode_atomic_bin_v2_header(2, header)), "header encodes");
    const std::vector<u8> expected =
      decode_hex("415442494E56320002006000040302014000000000000000"
                 "0352B036F2A140C609E3EB9C9D635DC553E8D77253D8FAA92437390F5CF93CB6"
                 "0200000000000000"
                 "0000000000000000000000000000000000000000000000000000000000000000");
    expect(expected.size() == header.size(), "golden header length");
    expect(std::equal(header.begin(), header.end(), expected.begin(), expected.end()),
           "96-byte golden header");

    u64 count = 0;
    expect(bool(Data::decode_atomic_bin_v2_header(header, count)) && count == 2,
           "header round trip");

    Data::AtomicBinV2Header provisional{};
    expect(bool(Data::encode_atomic_bin_v2_header(0, provisional)),
           "provisional zero-count header can be encoded");
    expect(Data::decode_atomic_bin_v2_header(provisional, count).error
             == Data::DataError::EMPTY_DATASET,
           "zero-count final header rejected");

    u64 size = 0;
    expect(bool(Data::atomic_bin_v2_file_size(2, size)) && size == 224, "file-size formula");
    expect(bool(Data::validate_atomic_bin_v2_file_size(2, 224)), "exact file size accepted");
    expect(Data::validate_atomic_bin_v2_file_size(2, 223).error
             == Data::DataError::FILE_SIZE_MISMATCH,
           "truncated file rejected");
    expect(Data::validate_atomic_bin_v2_file_size(0, 96).error == Data::DataError::EMPTY_DATASET,
           "empty final dataset rejected");
    constexpr u64           MaximumCount  = Data::AtomicBinV2MaxRecordCount;
    constexpr u64           OverflowCount = MaximumCount + 1;
    Data::AtomicBinV2Header maximumHeader{};
    expect(bool(Data::encode_atomic_bin_v2_header(MaximumCount, maximumHeader)),
           "maximum representable record count encodes");
    count = 0;
    expect(bool(Data::decode_atomic_bin_v2_header(maximumHeader, count)) && count == MaximumCount,
           "maximum representable record count decodes");
    expect(bool(Data::atomic_bin_v2_file_size(MaximumCount, size))
             && bool(Data::validate_atomic_bin_v2_file_size(MaximumCount, size)),
           "maximum representable record count has a valid file size");

    Data::AtomicBinV2Header rejectedHeader;
    rejectedHeader.fill(0xA5);
    expect(Data::encode_atomic_bin_v2_header(OverflowCount, rejectedHeader).error
             == Data::DataError::RECORD_COUNT_OUT_OF_RANGE,
           "maximum record count plus one is rejected on encode");
    expect(
      std::all_of(rejectedHeader.begin(), rejectedHeader.end(), [](u8 byte) { return byte == 0; }),
      "failed header encode zero-fills output");

    auto corruptCount = maximumHeader;
    write_u64(corruptCount, 56, OverflowCount);
    count = 123;
    expect(Data::decode_atomic_bin_v2_header(corruptCount, count).error
               == Data::DataError::RECORD_COUNT_OUT_OF_RANGE
             && count == 0,
           "maximum record count plus one is rejected on decode and resets output");
    size = 123;
    expect(Data::atomic_bin_v2_file_size(OverflowCount, size).error
               == Data::DataError::RECORD_COUNT_OUT_OF_RANGE
             && size == 0,
           "maximum record count plus one is rejected by file-size calculation");

    for (std::size_t offset : {std::size_t(0), std::size_t(8), std::size_t(10), std::size_t(12),
                               std::size_t(16), std::size_t(20), std::size_t(24), std::size_t(64)})
    {
        auto corrupt = header;
        corrupt[offset] ^= 1;
        count = 0;
        expect(!Data::decode_atomic_bin_v2_header(corrupt, count),
               "corrupted header field rejected");
    }
}

void test_start_record_golden_and_round_trip() {
    auto                  record   = encoded(sample(), "start record encodes");
    const std::vector<u8> expected = decode_hex("2453364211111111"
                                                "00000000000000000000000000000000"
                                                "777777778AB99CA8"
                                                "000F07003F38FF00"
                                                "0000010000000000"
                                                "85FFFFFF0C0700002A000000FF000000");
    expect(expected.size() == record.size(), "golden record length");
    expect(std::equal(record.begin(), record.end(), expected.begin(), expected.end()),
           "64-byte golden start record");
    expect(read_u32(record, 52) == 0x0000070C, "e2e4 independent move wire");

    Data::TrainingDataSample decodedSample;
    expect(bool(Data::decode_atomic_bin_v2(record, decodedSample)), "start record decodes");
    expect(decodedSample.fen == StartFEN && decodedSample.score == -123
             && decodedSample.move == Move(SQ_E2, SQ_E4) && decodedSample.ply == 42
             && decodedSample.result == -1 && decodedSample.flags == Data::NO_TRAINING_DATA_FLAGS,
           "adapter round trip fields");

    auto lowerEndpoint  = sample();
    lowerEndpoint.score = std::numeric_limits<int>::min() + 1;
    expect(bool(Data::encode_atomic_bin_v2(lowerEndpoint, record)),
           "int32-min-plus-one score accepted");
    auto legacyMinimum  = sample();
    legacyMinimum.score = -32768;
    expect(bool(Data::encode_atomic_bin_v2(legacyMinimum, record)),
           "legacy int16 minimum converts losslessly");
}

void test_special_move_vectors() {
    auto ep       = sample(Move::make<EN_PASSANT>(SQ_E5, SQ_D6));
    ep.fen        = "7k/8/8/3pP3/8/8/8/K7 w - d6 0 1";
    auto epRecord = encoded(ep, "en-passant record encodes");
    expect(read_u32(epRecord, 52) == 0x00002AE4, "e5d6 en-passant wire 0x2AE4");
    auto occupiedPawnStart = epRecord;
    occupiedPawnStart[25] =
      u8((occupiedPawnStart[25] & 0x0F) | (Data::ATOMIC_BIN_V2_WHITE_KNIGHT << 4));
    expect_decode_error(occupiedPawnStart, Data::DataError::INVALID_RECORD,
                        "en-passant vacated pawn start square must be empty");

    auto promotion       = sample(Move::make<PROMOTION>(SQ_A7, SQ_A8, QUEEN));
    promotion.fen        = "7k/P7/8/8/8/8/8/K7 w - - 0 1";
    auto promotionRecord = encoded(promotion, "promotion record encodes");
    expect(read_u32(promotionRecord, 52) == 0x00041E30, "a7a8q wire 0x41E30");

    constexpr std::array<PieceType, 4> Promotions = {KNIGHT, BISHOP, ROOK, QUEEN};
    for (PieceType piece : Promotions)
    {
        promotion.move = Move::make<PROMOTION>(SQ_A7, SQ_A8, piece);
        expect(bool(Data::encode_atomic_bin_v2(promotion, promotionRecord)),
               "all promotion enums encode");
    }

    auto castle       = sample(Move::make<CASTLING>(SQ_E1, SQ_H1));
    castle.fen        = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1";
    auto castleRecord = encoded(castle, "orthodox castling record encodes");
    expect(read_u32(castleRecord, 52) == 0x000031C4, "orthodox castling targets rook origin");
}

void test_atomic960_and_no_rights_mode() {
    auto atomic960  = sample(Move::make<CASTLING>(SQ_C1, SQ_B1));
    atomic960.fen   = "7k/8/8/8/8/8/8/1RK5 w B - 0 1";
    atomic960.flags = Data::TRAINING_DATA_CHESS960;
    auto record     = encoded(atomic960, "Atomic960 c1b1 record encodes");
    expect(read_u32(record, 52) == 0x00003042, "Atomic960 c1b1 wire 0x3042");
    expect(record[33] == 2 && record[35] == SQ_B1 && record[61] == 1,
           "Atomic960 queenside rook origin and flag");

    Data::TrainingDataSample roundTrip;
    expect(bool(Data::decode_atomic_bin_v2(record, roundTrip)), "Atomic960 record decodes");
    expect(roundTrip.fen == atomic960.fen && roundTrip.move == atomic960.move
             && roundTrip.flags == Data::TRAINING_DATA_CHESS960,
           "Atomic960 adapter round trip");

    auto noRights  = sample(Move(SQ_C1, SQ_D1));
    noRights.fen   = "7k/8/8/8/8/8/8/1RK5 w - - 0 1";
    noRights.flags = Data::TRAINING_DATA_CHESS960;
    record         = encoded(noRights, "Atomic960 no-rights record encodes");
    expect(record[33] == 0 && record[34] == 0xFF && record[35] == 0xFF && record[36] == 0xFF
             && record[37] == 0xFF && record[61] == 1,
           "Atomic960 no-rights origins remain absent");
    expect(bool(Data::decode_atomic_bin_v2(record, roundTrip)), "Atomic960 no-rights mode decodes");
}

void test_corrupt_position_rejections() {
    const auto golden = encoded(sample(), "corruption base record encodes");

    auto corrupt = golden;
    corrupt[39]  = 1;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD,
                        "position reserved byte rejected");
    corrupt     = golden;
    corrupt[46] = 1;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD,
                        "position tail reserved byte rejected");
    corrupt     = golden;
    corrupt[62] = 1;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "record reserved byte rejected");

    corrupt    = golden;
    corrupt[8] = u8((corrupt[8] & 0xF0) | 13);
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "reserved piece enum rejected");
    corrupt     = golden;
    corrupt[32] = 2;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "side enum rejected");
    corrupt     = golden;
    corrupt[33] = 0x10;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD,
                        "castling reserved rights rejected");
    corrupt     = golden;
    corrupt[34] = SQ_G1;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD,
                        "bad castling rook origin rejected");
    corrupt     = golden;
    corrupt[33] = 0;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD,
                        "rook origins without rights rejected");

    corrupt     = golden;
    corrupt[38] = SQ_D6;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "bad en-passant state rejected");
    corrupt     = golden;
    corrupt[40] = 0;
    corrupt[41] = 0x80;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "rule50 engine limit rejected");
    corrupt     = golden;
    corrupt[42] = corrupt[43] = corrupt[44] = corrupt[45] = 0;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "zero fullmove rejected");
    corrupt = golden;
    write_u32(corrupt, 42, 100001);
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "fullmove engine limit rejected");

    corrupt = golden;
    corrupt[2] &= 0xF0;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "missing white king rejected");
    corrupt    = golden;
    corrupt[8] = u8((corrupt[8] & 0xF0) | Data::ATOMIC_BIN_V2_WHITE_KING);
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "duplicate white king rejected");
}

void test_corrupt_move_and_scalar_rejections() {
    const auto golden  = encoded(sample(), "move corruption base record encodes");
    auto       corrupt = golden;
    write_u32(corrupt, 52, 0x00100000 | 0x70C);
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "move reserved bits rejected");
    corrupt = golden;
    write_u32(corrupt, 52, u32(SQ_E2) | (u32(SQ_E4) << 6) | (4U << 12));
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "move type enum rejected");
    corrupt = golden;
    write_u32(corrupt, 52, u32(SQ_E2) | (u32(SQ_E4) << 6) | (5U << 16));
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "promotion enum rejected");
    corrupt = golden;
    write_u32(corrupt, 52, u32(SQ_E2) | (u32(SQ_E4) << 6) | (1U << 16));
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD,
                        "normal move with promotion rejected");
    corrupt = golden;
    write_u32(corrupt, 52, u32(SQ_E2) | (u32(SQ_E4) << 6) | (1U << 12));
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD,
                        "promotion move without piece rejected");
    corrupt = golden;
    write_u32(corrupt, 52, 0);
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD,
                        "forbidden zero move wire rejected");
    corrupt = golden;
    write_u32(corrupt, 52, u32(SQ_E2) | (u32(SQ_E2) << 6));
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "from equals to rejected");
    corrupt = golden;
    write_u32(corrupt, 52, u32(SQ_E3) | (u32(SQ_E4) << 6));
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD, "empty move source rejected");

    corrupt = golden;
    write_u32(corrupt, 48, 0x80000000U);
    Data::AtomicBinV2RecordFields fields{};
    expect(Data::decode_atomic_bin_v2_record_structural(corrupt, fields).error
             == Data::DataError::SCORE_OUT_OF_RANGE,
           "INT32_MIN score sentinel rejected without UB");
    corrupt     = golden;
    corrupt[60] = 2;
    expect(Data::decode_atomic_bin_v2_record_structural(corrupt, fields).error
             == Data::DataError::RESULT_OUT_OF_RANGE,
           "result enum rejected");
    corrupt     = golden;
    corrupt[61] = 2;
    expect_decode_error(corrupt, Data::DataError::INVALID_RECORD,
                        "record flags reserved bits rejected");
}

void test_structural_record_and_atomic_legality_boundary() {
    const auto golden = encoded(sample(), "structural boundary base record encodes");

    Data::AtomicBinV2RecordFields fields{};
    expect(bool(Data::decode_atomic_bin_v2_record_structural(golden, fields)),
           "structural decoder accepts the valid base record");
    fields.move.from      = u8(SQ_E2);
    fields.move.to        = u8(SQ_E5);
    fields.move.type      = Data::ATOMIC_BIN_V2_NORMAL;
    fields.move.promotion = Data::ATOMIC_BIN_V2_NO_PROMOTION;

    Data::AtomicBinV2Record structurallyValid{};
    expect(bool(Data::encode_atomic_bin_v2_record_structural(fields, structurallyValid)),
           "raw record encoder intentionally performs structural validation only");
    Data::AtomicBinV2RecordFields structuralRoundTrip{};
    expect(
      bool(Data::decode_atomic_bin_v2_record_structural(structurallyValid, structuralRoundTrip))
        && structuralRoundTrip.move.from == SQ_E2 && structuralRoundTrip.move.to == SQ_E5,
      "raw record decoder intentionally accepts a structurally plausible move");

    Data::TrainingDataSample semanticOutput = sample();
    expect(Data::decode_atomic_bin_v2(structurallyValid, semanticOutput).error
             == Data::DataError::INVALID_MOVE,
           "public adapter rejects the same record when the move is not Atomic-legal");
    expect(semanticOutput.fen.empty() && semanticOutput.move == Move::none()
             && semanticOutput.score == 0 && semanticOutput.ply == 0 && semanticOutput.result == 0
             && semanticOutput.flags == 0,
           "failed semantic decode resets its output");
}

void test_adapter_fail_closed_and_zero_output() {
    Data::AtomicBinV2Record record;
    record.fill(0xA5);
    auto invalid = sample(Move(SQ_E2, SQ_E5));
    expect(Data::encode_atomic_bin_v2(invalid, record).error == Data::DataError::INVALID_MOVE,
           "illegal move rejected by MoveList LEGAL");
    expect(std::all_of(record.begin(), record.end(), [](u8 byte) { return byte == 0; }),
           "failed adapter encode zero-fills output");

    auto missingKing = sample(Move(SQ_A1, SQ_A2));
    missingKing.fen  = "8/8/8/8/8/8/8/K7 w - - 0 1";
    expect(!Data::encode_atomic_bin_v2(missingKing, record), "missing king adapter rejection");

    auto normalizedRights = sample(Move(SQ_E1, SQ_E2));
    normalizedRights.fen  = "4k3/8/8/8/8/8/8/4K3 w K - 0 1";
    expect(!Data::encode_atomic_bin_v2(normalizedRights, record),
           "sanitized castling rights rejected");

    auto normalizedEp = sample(Move(SQ_H8, SQ_H7));
    normalizedEp.fen  = "7k/8/8/8/4P3/8/8/K7 b - e3 0 1";
    expect(!Data::encode_atomic_bin_v2(normalizedEp, record),
           "sanitized en-passant square rejected");

    auto unknownFlags  = sample();
    unknownFlags.flags = 2;
    expect(!Data::encode_atomic_bin_v2(unknownFlags, record), "unknown sample flag rejected");

    auto scoreSentinel  = sample();
    scoreSentinel.score = std::numeric_limits<int>::min();
    expect(Data::encode_atomic_bin_v2(scoreSentinel, record).error
             == Data::DataError::SCORE_OUT_OF_RANGE,
           "INT32_MIN score sentinel rejected");

    auto clockBoundary = sample();
    clockBoundary.fen  = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 32767 100000";
    expect(bool(Data::encode_atomic_bin_v2(clockBoundary, record)),
           "rule50/fullmove maxima accepted");
    clockBoundary.fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 32767 100001";
    expect(Data::encode_atomic_bin_v2(clockBoundary, record).error
             == Data::DataError::POSITION_CLOCK_OUT_OF_RANGE,
           "fullmove above engine limit rejected");

    auto adjacentKings = sample(Move(SQ_E1, SQ_D1));
    adjacentKings.fen  = "8/8/8/8/8/8/4k3/4K3 w - - 0 1";
    expect(bool(Data::encode_atomic_bin_v2(adjacentKings, record)),
           "Atomic-adjacent kings are supported");

    const auto good                        = encoded(sample(), "decode reset base record encodes");
    auto       bad                         = good;
    bad[62]                                = 1;
    Data::TrainingDataSample decodedSample = sample();
    expect(!Data::decode_atomic_bin_v2(bad, decodedSample), "corrupt adapter decode fails");
    expect(decodedSample.fen.empty() && decodedSample.move == Move::none()
             && decodedSample.score == 0 && decodedSample.ply == 0 && decodedSample.result == 0
             && decodedSample.flags == 0,
           "failed adapter decode resets output");
}

void test_failed_wire_decode_resets_outputs() {
    auto record = encoded(sample(), "wire reset base record encodes");

    Data::AtomicBinV2MoveFields move{};
    move.from = 63;
    move.to   = 62;
    move.type = Data::ATOMIC_BIN_V2_CASTLING;
    expect(!Data::decode_atomic_bin_v2_move(0x00100000U, move), "corrupt move wire fails");
    expect(move.from == 0 && move.to == 0 && move.type == Data::ATOMIC_BIN_V2_NORMAL
             && move.promotion == Data::ATOMIC_BIN_V2_NO_PROMOTION,
           "failed move decode resets output");

    Data::AtomicBinV2Position rawPosition{};
    std::copy(record.begin(), record.begin() + Data::AtomicBinV2PositionSize, rawPosition.begin());
    rawPosition[39] = 1;
    Data::AtomicBinV2PositionFields position{};
    position.sideToMove = Data::ATOMIC_BIN_V2_BLACK_TO_MOVE;
    position.fullmove   = 99;
    expect(!Data::decode_atomic_bin_v2_position(rawPosition, false, position),
           "corrupt position wire fails");
    expect(position.sideToMove == Data::ATOMIC_BIN_V2_WHITE_TO_MOVE && position.fullmove == 1
             && position.castlingRookOrigins[0] == Data::AtomicBinV2NoSquare,
           "failed position decode resets output");

    record[62] = 1;
    Data::AtomicBinV2RecordFields fields{};
    fields.score             = 99;
    fields.position.fullmove = 99;
    expect(!Data::decode_atomic_bin_v2_record_structural(record, fields),
           "corrupt structural record wire fails");
    expect(fields.score == 0 && fields.position.fullmove == 1 && fields.flags == 0,
           "failed record decode resets output");
}

}  // namespace

int main() {
    Bitboards::init();
    Attacks::init();
    Position::init();

    test_layout_and_header_golden();
    test_start_record_golden_and_round_trip();
    test_special_move_vectors();
    test_atomic960_and_no_rights_mode();
    test_corrupt_position_rejections();
    test_corrupt_move_and_scalar_rejections();
    test_structural_record_and_atomic_legality_boundary();
    test_adapter_fail_closed_and_zero_output();
    test_failed_wire_decode_resets_outputs();

    if (failures)
    {
        std::cerr << failures << " Atomic BIN V2 test(s) failed\n";
        return 1;
    }
    std::cout << "Atomic BIN V2 codec tests passed\n";
    return 0;
}
