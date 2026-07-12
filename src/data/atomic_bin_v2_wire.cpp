/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_bin_v2_wire.h"

#include <algorithm>
#include <cstdlib>
#include <limits>
#include <string>
#include <utility>

namespace Stockfish::Data {
namespace {

constexpr std::array<u8, 32> SchemaSha256 = {
  0x03, 0x52, 0xb0, 0x36, 0xf2, 0xa1, 0x40, 0xc6, 0x09, 0xe3, 0xeb, 0x9c, 0x9d, 0x63, 0x5d, 0xc5,
  0x53, 0xe8, 0xd7, 0x72, 0x53, 0xd8, 0xfa, 0xa9, 0x24, 0x37, 0x39, 0x0f, 0x5c, 0xf9, 0x3c, 0xb6};

constexpr bool valid_piece_code(u8 piece) { return piece <= ATOMIC_BIN_V2_BLACK_KING; }

constexpr bool is_white_piece(u8 piece) {
    return piece >= ATOMIC_BIN_V2_WHITE_PAWN && piece <= ATOMIC_BIN_V2_WHITE_KING;
}

constexpr bool is_black_piece(u8 piece) {
    return piece >= ATOMIC_BIN_V2_BLACK_PAWN && piece <= ATOMIC_BIN_V2_BLACK_KING;
}

constexpr bool is_side_piece(u8 piece, u8 side) {
    return side == ATOMIC_BIN_V2_WHITE_TO_MOVE ? is_white_piece(piece) : is_black_piece(piece);
}

constexpr u8 pawn_code(u8 side) {
    return side == ATOMIC_BIN_V2_WHITE_TO_MOVE ? ATOMIC_BIN_V2_WHITE_PAWN
                                               : ATOMIC_BIN_V2_BLACK_PAWN;
}

constexpr u8 rook_code(u8 side) {
    return side == ATOMIC_BIN_V2_WHITE_TO_MOVE ? ATOMIC_BIN_V2_WHITE_ROOK
                                               : ATOMIC_BIN_V2_BLACK_ROOK;
}

constexpr u8 king_code(u8 side) {
    return side == ATOMIC_BIN_V2_WHITE_TO_MOVE ? ATOMIC_BIN_V2_WHITE_KING
                                               : ATOMIC_BIN_V2_BLACK_KING;
}

DataResult invalid_header(std::string message) {
    return DataResult::failure(DataError::INVALID_HEADER, std::move(message));
}

DataResult invalid_record(std::string message) {
    return DataResult::failure(DataError::INVALID_RECORD, std::move(message));
}

void write_u16_le(u8* output, u16 value) {
    output[0] = u8(value & 0xFF);
    output[1] = u8(value >> 8);
}

void write_u32_le(u8* output, u32 value) {
    for (unsigned byte = 0; byte < 4; ++byte)
        output[byte] = u8(value >> (byte * 8));
}

void write_u64_le(u8* output, u64 value) {
    for (unsigned byte = 0; byte < 8; ++byte)
        output[byte] = u8(value >> (byte * 8));
}

u16 read_u16_le(const u8* input) { return u16(input[0]) | (u16(input[1]) << 8); }

u32 read_u32_le(const u8* input) {
    u32 value = 0;
    for (unsigned byte = 0; byte < 4; ++byte)
        value |= u32(input[byte]) << (byte * 8);
    return value;
}

u64 read_u64_le(const u8* input) {
    u64 value = 0;
    for (unsigned byte = 0; byte < 8; ++byte)
        value |= u64(input[byte]) << (byte * 8);
    return value;
}

i32 decode_i32(u32 wire) {
    if (wire <= u32(std::numeric_limits<i32>::max()))
        return i32(wire);
    return i32(std::int64_t(wire) - (std::int64_t(1) << 32));
}

bool all_zero(const u8* first, const u8* last) {
    return std::all_of(first, last, [](u8 byte) { return byte == 0; });
}

int find_piece(const AtomicBinV2PositionFields& fields, u8 piece) {
    int square = -1;
    for (int index = 0; index < 64; ++index)
        if (fields.board[std::size_t(index)] == piece)
        {
            if (square != -1)
                return -2;
            square = index;
        }
    return square;
}

DataResult validate_castling(const AtomicBinV2PositionFields& fields, bool atomic960) {
    if (fields.castlingRights & ~u8(0x0F))
        return invalid_record("Atomic BIN V2 castling rights contain reserved bits");

    const int                   whiteKing    = find_piece(fields, ATOMIC_BIN_V2_WHITE_KING);
    const int                   blackKing    = find_piece(fields, ATOMIC_BIN_V2_BLACK_KING);
    constexpr std::array<u8, 4> StandardRook = {7, 0, 63, 56};

    for (unsigned index = 0; index < 4; ++index)
    {
        const bool enabled = bool(fields.castlingRights & u8(1U << index));
        const u8   origin  = fields.castlingRookOrigins[index];
        if (!enabled)
        {
            if (origin != AtomicBinV2NoSquare)
                return invalid_record(
                  "Atomic BIN V2 stores a castling rook origin without its right");
            continue;
        }

        if (origin >= 64)
            return invalid_record("Atomic BIN V2 castling rook origin is out of range");

        const u8  side = index >= 2 ? ATOMIC_BIN_V2_BLACK_TO_MOVE : ATOMIC_BIN_V2_WHITE_TO_MOVE;
        const int kingSquare = side == ATOMIC_BIN_V2_WHITE_TO_MOVE ? whiteKing : blackKing;
        const int homeRank   = side == ATOMIC_BIN_V2_WHITE_TO_MOVE ? 0 : 7;
        if (kingSquare < 0 || kingSquare / 8 != homeRank || int(origin) / 8 != homeRank)
            return invalid_record("Atomic BIN V2 castling pieces are not on their home rank");
        if (fields.board[origin] != rook_code(side))
            return invalid_record("Atomic BIN V2 castling origin does not contain the right rook");

        const bool kingSide = index % 2 == 0;
        if ((kingSide && int(origin) % 8 <= kingSquare % 8)
            || (!kingSide && int(origin) % 8 >= kingSquare % 8))
            return invalid_record("Atomic BIN V2 castling rook is on the wrong side of its king");

        if (!atomic960
            && (origin != StandardRook[index]
                || kingSquare != (side == ATOMIC_BIN_V2_WHITE_TO_MOVE ? 4 : 60)))
            return invalid_record(
              "Atomic BIN V2 non-960 castling must use orthodox king and rook origins");
    }

    if (fields.castlingRookOrigins[0] != AtomicBinV2NoSquare
        && fields.castlingRookOrigins[0] == fields.castlingRookOrigins[1])
        return invalid_record("Atomic BIN V2 white castling origins are duplicated");
    if (fields.castlingRookOrigins[2] != AtomicBinV2NoSquare
        && fields.castlingRookOrigins[2] == fields.castlingRookOrigins[3])
        return invalid_record("Atomic BIN V2 black castling origins are duplicated");

    return DataResult::success();
}

DataResult validate_en_passant(const AtomicBinV2PositionFields& fields) {
    const u8 ep = fields.enPassantSquare;
    if (ep == AtomicBinV2NoSquare)
        return DataResult::success();
    if (ep >= 64)
        return invalid_record("Atomic BIN V2 en-passant square is out of range");

    const int expectedRank = fields.sideToMove == ATOMIC_BIN_V2_WHITE_TO_MOVE ? 5 : 2;
    if (int(ep) / 8 != expectedRank || fields.board[ep] != ATOMIC_BIN_V2_EMPTY)
        return invalid_record("Atomic BIN V2 en-passant square has invalid rank or occupancy");

    const int capturedSquare =
      int(ep) + (fields.sideToMove == ATOMIC_BIN_V2_WHITE_TO_MOVE ? -8 : 8);
    const u8 capturedPawn = pawn_code(fields.sideToMove ^ 1U);
    if (capturedSquare < 0 || capturedSquare >= 64
        || fields.board[std::size_t(capturedSquare)] != capturedPawn)
        return invalid_record("Atomic BIN V2 en-passant target has no capturable pawn");

    const int sourceRank =
      expectedRank + (fields.sideToMove == ATOMIC_BIN_V2_WHITE_TO_MOVE ? -1 : 1);
    bool hasCapturer = false;
    for (int fileDelta : {-1, 1})
    {
        const int file = int(ep) % 8 + fileDelta;
        if (file >= 0 && file < 8
            && fields.board[std::size_t(sourceRank * 8 + file)] == pawn_code(fields.sideToMove))
            hasCapturer = true;
    }
    if (!hasCapturer)
        return invalid_record("Atomic BIN V2 en-passant target has no capturing pawn");

    const int vacatedSquare = int(ep) + (fields.sideToMove == ATOMIC_BIN_V2_WHITE_TO_MOVE ? 8 : -8);
    if (vacatedSquare < 0 || vacatedSquare >= 64
        || fields.board[std::size_t(vacatedSquare)] != ATOMIC_BIN_V2_EMPTY)
        return invalid_record("Atomic BIN V2 en-passant pawn start square is not empty");

    return DataResult::success();
}

DataResult validate_position_fields(const AtomicBinV2PositionFields& fields, bool atomic960) {
    for (u8 piece : fields.board)
        if (!valid_piece_code(piece))
            return invalid_record("Atomic BIN V2 position contains a reserved piece code");

    if (fields.sideToMove > ATOMIC_BIN_V2_BLACK_TO_MOVE)
        return invalid_record("Atomic BIN V2 side-to-move is outside its enum domain");

    if (find_piece(fields, ATOMIC_BIN_V2_WHITE_KING) < 0
        || find_piece(fields, ATOMIC_BIN_V2_BLACK_KING) < 0)
        return invalid_record("Atomic BIN V2 requires exactly one king per color");

    if (fields.fullmove == 0)
        return invalid_record("Atomic BIN V2 fullmove must be at least one");
    if (fields.rule50 > AtomicBinV2MaxRule50 || fields.fullmove > AtomicBinV2MaxFullmove)
        return invalid_record("Atomic BIN V2 position clocks exceed engine-origin limits");

    if (DataResult castling = validate_castling(fields, atomic960); !castling)
        return castling;
    return validate_en_passant(fields);
}

DataResult validate_move_fields(const AtomicBinV2MoveFields& move) {
    if (move.from >= 64 || move.to >= 64 || move.from == move.to)
        return invalid_record("Atomic BIN V2 move squares are invalid");
    if (move.type > ATOMIC_BIN_V2_CASTLING)
        return invalid_record("Atomic BIN V2 move type is outside its enum domain");
    if (move.promotion > ATOMIC_BIN_V2_PROMOTE_QUEEN)
        return invalid_record("Atomic BIN V2 promotion is outside its enum domain");
    if ((move.type == ATOMIC_BIN_V2_PROMOTION) != (move.promotion != ATOMIC_BIN_V2_NO_PROMOTION))
        return invalid_record("Atomic BIN V2 move type and promotion are inconsistent");
    return DataResult::success();
}

DataResult validate_move_structure_for_position(const AtomicBinV2MoveFields&     move,
                                                const AtomicBinV2PositionFields& position) {
    const u8 moving = position.board[move.from];
    const u8 target = position.board[move.to];
    if (moving == ATOMIC_BIN_V2_EMPTY || !is_side_piece(moving, position.sideToMove))
        return invalid_record("Atomic BIN V2 move source has no side-to-move piece");
    if (target != ATOMIC_BIN_V2_EMPTY && is_side_piece(target, position.sideToMove))
    {
        if (move.type != ATOMIC_BIN_V2_CASTLING)
            return invalid_record("Atomic BIN V2 move captures a friendly piece");
    }

    const int fromRank = move.from / 8;
    const int toRank   = move.to / 8;
    const int fromFile = move.from % 8;
    const int toFile   = move.to % 8;
    const int forward  = position.sideToMove == ATOMIC_BIN_V2_WHITE_TO_MOVE ? 1 : -1;

    switch (move.type)
    {
    case ATOMIC_BIN_V2_NORMAL :
        if (moving == pawn_code(position.sideToMove) && (toRank == 0 || toRank == 7))
            return invalid_record("Atomic BIN V2 last-rank pawn move must be a promotion");
        break;
    case ATOMIC_BIN_V2_PROMOTION :
        if (moving != pawn_code(position.sideToMove) || toRank - fromRank != forward
            || (toRank != 0 && toRank != 7) || std::abs(toFile - fromFile) > 1)
            return invalid_record("Atomic BIN V2 promotion geometry is invalid");
        break;
    case ATOMIC_BIN_V2_EN_PASSANT :
        if (moving != pawn_code(position.sideToMove) || move.to != position.enPassantSquare
            || target != ATOMIC_BIN_V2_EMPTY || toRank - fromRank != forward
            || std::abs(toFile - fromFile) != 1)
            return invalid_record("Atomic BIN V2 en-passant move is inconsistent with position");
        break;
    case ATOMIC_BIN_V2_CASTLING : {
        if (moving != king_code(position.sideToMove) || target != rook_code(position.sideToMove))
            return invalid_record("Atomic BIN V2 castling move must target its own rook");
        const unsigned first = position.sideToMove == ATOMIC_BIN_V2_WHITE_TO_MOVE ? 0 : 2;
        bool           found = false;
        for (unsigned index = first; index < first + 2; ++index)
            found |= bool(position.castlingRights & u8(1U << index))
                  && position.castlingRookOrigins[index] == move.to;
        if (!found)
            return invalid_record("Atomic BIN V2 castling move has no matching right");
        break;
    }
    default :
        return invalid_record("Atomic BIN V2 move type is invalid");
    }

    return DataResult::success();
}

}  // namespace

const std::array<u8, 32>& atomic_bin_v2_schema_sha256() noexcept { return SchemaSha256; }

DataResult encode_atomic_bin_v2_header(u64 recordCount, AtomicBinV2Header& header) {
    header.fill(0);
    if (recordCount > AtomicBinV2MaxRecordCount)
        return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                   "Atomic BIN V2 record count overflows file size");
    std::copy(AtomicBinV2Magic.begin(), AtomicBinV2Magic.end(), header.begin());
    write_u16_le(header.data() + 8, AtomicBinV2Version);
    write_u16_le(header.data() + 10, u16(AtomicBinV2HeaderSize));
    write_u32_le(header.data() + 12, AtomicBinV2EndianMarker);
    write_u32_le(header.data() + 16, u32(AtomicBinV2RecordSize));
    write_u32_le(header.data() + 20, 0);
    std::copy(SchemaSha256.begin(), SchemaSha256.end(), header.begin() + 24);
    write_u64_le(header.data() + 56, recordCount);
    return DataResult::success();
}

DataResult decode_atomic_bin_v2_header(const AtomicBinV2Header& header, u64& recordCount) {
    recordCount = 0;
    if (!std::equal(AtomicBinV2Magic.begin(), AtomicBinV2Magic.end(), header.begin()))
        return invalid_header("Atomic BIN V2 magic mismatch");
    if (read_u16_le(header.data() + 8) != AtomicBinV2Version)
        return invalid_header("Atomic BIN V2 version mismatch");
    if (read_u16_le(header.data() + 10) != AtomicBinV2HeaderSize)
        return invalid_header("Atomic BIN V2 header-size mismatch");
    if (read_u32_le(header.data() + 12) != AtomicBinV2EndianMarker)
        return invalid_header("Atomic BIN V2 endian marker mismatch");
    if (read_u32_le(header.data() + 16) != AtomicBinV2RecordSize)
        return invalid_header("Atomic BIN V2 record-size mismatch");
    if (read_u32_le(header.data() + 20) != 0)
        return invalid_header("Atomic BIN V2 header flags are nonzero");
    if (!std::equal(SchemaSha256.begin(), SchemaSha256.end(), header.begin() + 24))
        return DataResult::failure(DataError::SCHEMA_MISMATCH,
                                   "Atomic BIN V2 schema SHA-256 mismatch");
    if (!all_zero(header.data() + 64, header.data() + AtomicBinV2HeaderSize))
        return invalid_header("Atomic BIN V2 header reserved bytes are nonzero");
    const u64 decodedCount = read_u64_le(header.data() + 56);
    if (decodedCount > AtomicBinV2MaxRecordCount)
        return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                   "Atomic BIN V2 record count overflows file size");
    if (decodedCount == 0)
        return DataResult::failure(DataError::EMPTY_DATASET,
                                   "Atomic BIN V2 finalized datasets cannot be empty");
    recordCount = decodedCount;
    return DataResult::success();
}

DataResult atomic_bin_v2_file_size(u64 recordCount, u64& fileSize) {
    fileSize = 0;
    if (recordCount > AtomicBinV2MaxRecordCount)
        return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                   "Atomic BIN V2 record count overflows file size");
    fileSize = AtomicBinV2HeaderSize + recordCount * AtomicBinV2RecordSize;
    return DataResult::success();
}

DataResult validate_atomic_bin_v2_file_size(u64 recordCount, u64 fileSize) {
    if (recordCount == 0)
        return DataResult::failure(DataError::EMPTY_DATASET,
                                   "Atomic BIN V2 finalized datasets cannot be empty");
    u64 expected = 0;
    if (DataResult size = atomic_bin_v2_file_size(recordCount, expected); !size)
        return size;
    if (fileSize != expected)
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Atomic BIN V2 file size does not match record count");
    return DataResult::success();
}

DataResult encode_atomic_bin_v2_position(const AtomicBinV2PositionFields& fields,
                                         bool                             atomic960,
                                         AtomicBinV2Position&             position) {
    position.fill(0);
    if (DataResult valid = validate_position_fields(fields, atomic960); !valid)
        return valid;

    for (unsigned square = 0; square < 64; ++square)
    {
        const unsigned byte  = square / 2;
        const unsigned shift = (square & 1U) * 4;
        position[byte] |= u8(fields.board[square] << shift);
    }
    position[32] = fields.sideToMove;
    position[33] = fields.castlingRights;
    std::copy(fields.castlingRookOrigins.begin(), fields.castlingRookOrigins.end(),
              position.begin() + 34);
    position[38] = fields.enPassantSquare;
    write_u16_le(position.data() + 40, fields.rule50);
    write_u32_le(position.data() + 42, fields.fullmove);
    return DataResult::success();
}

DataResult decode_atomic_bin_v2_position(const AtomicBinV2Position& position,
                                         bool                       atomic960,
                                         AtomicBinV2PositionFields& fields) {
    fields = {};
    AtomicBinV2PositionFields decoded{};
    decoded.castlingRookOrigins.fill(AtomicBinV2NoSquare);
    decoded.enPassantSquare = AtomicBinV2NoSquare;
    decoded.fullmove        = 1;

    if (position[39] != 0 || position[46] != 0 || position[47] != 0)
        return invalid_record("Atomic BIN V2 position reserved bytes are nonzero");

    for (unsigned square = 0; square < 64; ++square)
        decoded.board[square] = u8((position[square / 2] >> ((square & 1U) * 4)) & 0x0F);
    decoded.sideToMove     = position[32];
    decoded.castlingRights = position[33];
    std::copy(position.begin() + 34, position.begin() + 38, decoded.castlingRookOrigins.begin());
    decoded.enPassantSquare = position[38];
    decoded.rule50          = read_u16_le(position.data() + 40);
    decoded.fullmove        = read_u32_le(position.data() + 42);
    if (DataResult valid = validate_position_fields(decoded, atomic960); !valid)
        return valid;
    fields = decoded;
    return DataResult::success();
}

DataResult encode_atomic_bin_v2_move(const AtomicBinV2MoveFields& move, u32& wire) {
    wire = 0;
    if (DataResult valid = validate_move_fields(move); !valid)
        return valid;
    wire =
      u32(move.from) | (u32(move.to) << 6) | (u32(move.type) << 12) | (u32(move.promotion) << 16);
    return DataResult::success();
}

DataResult decode_atomic_bin_v2_move(u32 wire, AtomicBinV2MoveFields& move) {
    move = {};
    if (wire >> 20)
        return invalid_record("Atomic BIN V2 move reserved bits are nonzero");
    AtomicBinV2MoveFields decoded{};
    decoded.from      = u8(wire & 0x3F);
    decoded.to        = u8((wire >> 6) & 0x3F);
    decoded.type      = u8((wire >> 12) & 0x0F);
    decoded.promotion = u8((wire >> 16) & 0x0F);
    if (DataResult valid = validate_move_fields(decoded); !valid)
        return valid;
    move = decoded;
    return DataResult::success();
}

DataResult encode_atomic_bin_v2_record_structural(const AtomicBinV2RecordFields& fields,
                                                  AtomicBinV2Record&             record) {
    record.fill(0);
    if (fields.score < AtomicBinV2MinScore || fields.score > AtomicBinV2MaxScore)
        return DataResult::failure(DataError::SCORE_OUT_OF_RANGE,
                                   "Atomic BIN V2 score is outside the initial domain");
    if (fields.result < -1 || fields.result > 1)
        return DataResult::failure(DataError::RESULT_OUT_OF_RANGE,
                                   "Atomic BIN V2 result must be -1, 0, or 1");
    if (fields.flags & ~u8(ATOMIC_BIN_V2_ATOMIC960))
        return invalid_record("Atomic BIN V2 record flags contain reserved bits");

    AtomicBinV2Position position{};
    if (DataResult encoded = encode_atomic_bin_v2_position(
          fields.position, bool(fields.flags & ATOMIC_BIN_V2_ATOMIC960), position);
        !encoded)
        return encoded;
    u32 move = 0;
    if (DataResult encoded = encode_atomic_bin_v2_move(fields.move, move); !encoded)
        return encoded;
    if (DataResult valid = validate_move_structure_for_position(fields.move, fields.position);
        !valid)
        return valid;

    std::copy(position.begin(), position.end(), record.begin());
    write_u32_le(record.data() + 48, u32(fields.score));
    write_u32_le(record.data() + 52, move);
    write_u32_le(record.data() + 56, fields.ply);
    record[60] = fields.result == -1 ? 0xFF : u8(fields.result);
    record[61] = fields.flags;
    return DataResult::success();
}

DataResult decode_atomic_bin_v2_record_structural(const AtomicBinV2Record& record,
                                                  AtomicBinV2RecordFields& fields) {
    fields = {};
    AtomicBinV2RecordFields decodedFields{};
    if (record[62] != 0 || record[63] != 0)
        return invalid_record("Atomic BIN V2 record reserved bytes are nonzero");
    if (record[61] & ~u8(ATOMIC_BIN_V2_ATOMIC960))
        return invalid_record("Atomic BIN V2 record flags contain reserved bits");

    decodedFields.flags = record[61];
    AtomicBinV2Position position{};
    std::copy(record.begin(), record.begin() + AtomicBinV2PositionSize, position.begin());
    if (DataResult decoded = decode_atomic_bin_v2_position(
          position, bool(decodedFields.flags & ATOMIC_BIN_V2_ATOMIC960), decodedFields.position);
        !decoded)
        return decoded;

    decodedFields.score = decode_i32(read_u32_le(record.data() + 48));
    if (decodedFields.score < AtomicBinV2MinScore || decodedFields.score > AtomicBinV2MaxScore)
        return DataResult::failure(DataError::SCORE_OUT_OF_RANGE,
                                   "Atomic BIN V2 score is outside the initial domain");
    if (DataResult decoded =
          decode_atomic_bin_v2_move(read_u32_le(record.data() + 52), decodedFields.move);
        !decoded)
        return decoded;
    if (DataResult valid =
          validate_move_structure_for_position(decodedFields.move, decodedFields.position);
        !valid)
        return valid;

    decodedFields.ply = read_u32_le(record.data() + 56);
    if (record[60] == 0xFF)
        decodedFields.result = -1;
    else if (record[60] <= 1)
        decodedFields.result = i8(record[60]);
    else
        return DataResult::failure(DataError::RESULT_OUT_OF_RANGE,
                                   "Atomic BIN V2 result must be -1, 0, or 1");
    fields = decodedFields;
    return DataResult::success();
}

}  // namespace Stockfish::Data
