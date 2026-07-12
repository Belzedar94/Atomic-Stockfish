/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef ATOMIC_BIN_V2_WIRE_H_INCLUDED
#define ATOMIC_BIN_V2_WIRE_H_INCLUDED

#include <array>
#include <cstddef>
#include <limits>
#include <string_view>

#include "training_data.h"

namespace Stockfish::Data {

inline constexpr std::size_t AtomicBinV2HeaderSize   = 96;
inline constexpr std::size_t AtomicBinV2PositionSize = 48;
inline constexpr std::size_t AtomicBinV2RecordSize   = 64;
inline constexpr u16         AtomicBinV2Version      = 2;
inline constexpr u32         AtomicBinV2EndianMarker = 0x01020304;
inline constexpr u8          AtomicBinV2NoSquare     = 0xFF;
inline constexpr i32         AtomicBinV2MinScore     = -2147483647;
inline constexpr i32         AtomicBinV2MaxScore     = 2147483647;
inline constexpr u16         AtomicBinV2MaxRule50    = 32767;
inline constexpr u32         AtomicBinV2MaxFullmove  = 100000;
inline constexpr u64         AtomicBinV2MaxRecordCount =
  (std::numeric_limits<u64>::max() - u64(AtomicBinV2HeaderSize)) / u64(AtomicBinV2RecordSize);

inline constexpr std::array<u8, 8> AtomicBinV2Magic = {'A', 'T', 'B', 'I', 'N', 'V', '2', 0};

inline constexpr std::string_view AtomicBinV2SchemaSha256Hex =
  "0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6";

using AtomicBinV2Header   = std::array<u8, AtomicBinV2HeaderSize>;
using AtomicBinV2Position = std::array<u8, AtomicBinV2PositionSize>;
using AtomicBinV2Record   = std::array<u8, AtomicBinV2RecordSize>;

enum AtomicBinV2PieceCode : u8 {
    ATOMIC_BIN_V2_EMPTY        = 0,
    ATOMIC_BIN_V2_WHITE_PAWN   = 1,
    ATOMIC_BIN_V2_WHITE_KNIGHT = 2,
    ATOMIC_BIN_V2_WHITE_BISHOP = 3,
    ATOMIC_BIN_V2_WHITE_ROOK   = 4,
    ATOMIC_BIN_V2_WHITE_QUEEN  = 5,
    ATOMIC_BIN_V2_WHITE_KING   = 6,
    ATOMIC_BIN_V2_BLACK_PAWN   = 7,
    ATOMIC_BIN_V2_BLACK_KNIGHT = 8,
    ATOMIC_BIN_V2_BLACK_BISHOP = 9,
    ATOMIC_BIN_V2_BLACK_ROOK   = 10,
    ATOMIC_BIN_V2_BLACK_QUEEN  = 11,
    ATOMIC_BIN_V2_BLACK_KING   = 12
};

enum AtomicBinV2Side : u8 {
    ATOMIC_BIN_V2_WHITE_TO_MOVE = 0,
    ATOMIC_BIN_V2_BLACK_TO_MOVE = 1
};

enum AtomicBinV2MoveType : u8 {
    ATOMIC_BIN_V2_NORMAL     = 0,
    ATOMIC_BIN_V2_PROMOTION  = 1,
    ATOMIC_BIN_V2_EN_PASSANT = 2,
    ATOMIC_BIN_V2_CASTLING   = 3
};

enum AtomicBinV2Promotion : u8 {
    ATOMIC_BIN_V2_NO_PROMOTION   = 0,
    ATOMIC_BIN_V2_PROMOTE_KNIGHT = 1,
    ATOMIC_BIN_V2_PROMOTE_BISHOP = 2,
    ATOMIC_BIN_V2_PROMOTE_ROOK   = 3,
    ATOMIC_BIN_V2_PROMOTE_QUEEN  = 4
};

enum AtomicBinV2RecordFlags : u8 {
    ATOMIC_BIN_V2_NO_FLAGS  = 0,
    ATOMIC_BIN_V2_ATOMIC960 = 1
};

struct AtomicBinV2PositionFields {
    std::array<u8, 64> board{};
    u8                 sideToMove          = ATOMIC_BIN_V2_WHITE_TO_MOVE;
    u8                 castlingRights      = 0;
    std::array<u8, 4>  castlingRookOrigins = {AtomicBinV2NoSquare, AtomicBinV2NoSquare,
                                              AtomicBinV2NoSquare, AtomicBinV2NoSquare};
    u8                 enPassantSquare     = AtomicBinV2NoSquare;
    u16                rule50              = 0;
    u32                fullmove            = 1;
};

struct AtomicBinV2MoveFields {
    u8 from      = 0;
    u8 to        = 0;
    u8 type      = ATOMIC_BIN_V2_NORMAL;
    u8 promotion = ATOMIC_BIN_V2_NO_PROMOTION;
};

struct AtomicBinV2RecordFields {
    AtomicBinV2PositionFields position;
    i32                       score = 0;
    AtomicBinV2MoveFields     move;
    u32                       ply    = 0;
    i8                        result = 0;
    u8                        flags  = ATOMIC_BIN_V2_NO_FLAGS;
};

// The schema digest is binary on wire. The hex spelling is exposed only for
// capability handshakes and audit logs.
const std::array<u8, 32>& atomic_bin_v2_schema_sha256() noexcept;

// A zero recordCount is permitted only while a future exclusive sink owns an
// unfinished placeholder header. Decode and final file-size validation reject
// that value because an empty finalized dataset is invalid. Header encode and
// decode also reject counts for which 96 + count * 64 cannot fit in uint64.
DataResult encode_atomic_bin_v2_header(u64 recordCount, AtomicBinV2Header& header);
DataResult decode_atomic_bin_v2_header(const AtomicBinV2Header& header, u64& recordCount);

DataResult atomic_bin_v2_file_size(u64 recordCount, u64& fileSize);
DataResult validate_atomic_bin_v2_file_size(u64 recordCount, u64 fileSize);

DataResult encode_atomic_bin_v2_position(const AtomicBinV2PositionFields& fields,
                                         bool                             atomic960,
                                         AtomicBinV2Position&             position);
DataResult decode_atomic_bin_v2_position(const AtomicBinV2Position& position,
                                         bool                       atomic960,
                                         AtomicBinV2PositionFields& fields);

DataResult encode_atomic_bin_v2_move(const AtomicBinV2MoveFields& move, u32& wire);
DataResult decode_atomic_bin_v2_move(u32 wire, AtomicBinV2MoveFields& move);

// These wire-layer helpers validate representation and local field
// relationships only. In particular, a structurally plausible move is not
// proven Atomic-legal here. Dataset producers and consumers must use the
// high-level encode_atomic_bin_v2()/decode_atomic_bin_v2() adapter, which
// round-trips through Position and MoveList<LEGAL>.
DataResult encode_atomic_bin_v2_record_structural(const AtomicBinV2RecordFields& fields,
                                                  AtomicBinV2Record&             record);
DataResult decode_atomic_bin_v2_record_structural(const AtomicBinV2Record& record,
                                                  AtomicBinV2RecordFields& fields);

}  // namespace Stockfish::Data

#endif  // #ifndef ATOMIC_BIN_V2_WIRE_H_INCLUDED
