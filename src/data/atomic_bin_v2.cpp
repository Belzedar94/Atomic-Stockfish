/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_bin_v2.h"

#include <array>
#include <cstdint>
#include <limits>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>

#include "movegen.h"
#include "position.h"

namespace Stockfish::Data {
namespace {

static_assert(std::numeric_limits<int>::digits >= 31,
              "Atomic BIN V2 TrainingDataSample adapter requires an int of at least 32 bits");

bool split_fen_fields(const std::string& fen, std::array<std::string, 6>& fields) {
    std::istringstream input(fen);
    for (auto& field : fields)
        if (!(input >> field))
            return false;
    std::string extra;
    return !(input >> extra);
}

bool parse_decimal_field(std::string_view text, u64 maximum, u64& value) {
    if (text.empty())
        return false;
    value = 0;
    for (unsigned char character : text)
    {
        if (character < '0' || character > '9')
            return false;
        const u64 digit = character - '0';
        if (value > (maximum - digit) / 10)
            return false;
        value = value * 10 + digit;
    }
    return true;
}

DataResult unsupported(std::string message) {
    return DataResult::failure(DataError::UNSUPPORTED_POSITION, std::move(message));
}

u8 piece_to_wire(Piece piece) {
    switch (piece)
    {
    case NO_PIECE :
        return ATOMIC_BIN_V2_EMPTY;
    case W_PAWN :
        return ATOMIC_BIN_V2_WHITE_PAWN;
    case W_KNIGHT :
        return ATOMIC_BIN_V2_WHITE_KNIGHT;
    case W_BISHOP :
        return ATOMIC_BIN_V2_WHITE_BISHOP;
    case W_ROOK :
        return ATOMIC_BIN_V2_WHITE_ROOK;
    case W_QUEEN :
        return ATOMIC_BIN_V2_WHITE_QUEEN;
    case W_KING :
        return ATOMIC_BIN_V2_WHITE_KING;
    case B_PAWN :
        return ATOMIC_BIN_V2_BLACK_PAWN;
    case B_KNIGHT :
        return ATOMIC_BIN_V2_BLACK_KNIGHT;
    case B_BISHOP :
        return ATOMIC_BIN_V2_BLACK_BISHOP;
    case B_ROOK :
        return ATOMIC_BIN_V2_BLACK_ROOK;
    case B_QUEEN :
        return ATOMIC_BIN_V2_BLACK_QUEEN;
    case B_KING :
        return ATOMIC_BIN_V2_BLACK_KING;
    default :
        return 0xFF;
    }
}

char piece_to_fen(u8 piece) {
    constexpr std::array<char, 13> PieceChar = {'1', 'P', 'N', 'B', 'R', 'Q', 'K',
                                                'p', 'n', 'b', 'r', 'q', 'k'};
    return piece < PieceChar.size() ? PieceChar[piece] : '?';
}

u8 promotion_to_wire(PieceType type) {
    switch (type)
    {
    case KNIGHT :
        return ATOMIC_BIN_V2_PROMOTE_KNIGHT;
    case BISHOP :
        return ATOMIC_BIN_V2_PROMOTE_BISHOP;
    case ROOK :
        return ATOMIC_BIN_V2_PROMOTE_ROOK;
    case QUEEN :
        return ATOMIC_BIN_V2_PROMOTE_QUEEN;
    default :
        return 0xFF;
    }
}

PieceType promotion_from_wire(u8 promotion) {
    switch (promotion)
    {
    case ATOMIC_BIN_V2_PROMOTE_KNIGHT :
        return KNIGHT;
    case ATOMIC_BIN_V2_PROMOTE_BISHOP :
        return BISHOP;
    case ATOMIC_BIN_V2_PROMOTE_ROOK :
        return ROOK;
    case ATOMIC_BIN_V2_PROMOTE_QUEEN :
        return QUEEN;
    default :
        return NO_PIECE_TYPE;
    }
}

DataResult move_to_wire(Move move, AtomicBinV2MoveFields& fields) {
    fields = {};
    if (!move.is_ok() || move.from_sq() == move.to_sq())
        return DataResult::failure(DataError::INVALID_MOVE,
                                   "Atomic BIN V2 requires a non-null move");
    fields.from = u8(move.from_sq());
    fields.to   = u8(move.to_sq());
    switch (move.type_of())
    {
    case NORMAL :
        fields.type = ATOMIC_BIN_V2_NORMAL;
        break;
    case PROMOTION :
        fields.type      = ATOMIC_BIN_V2_PROMOTION;
        fields.promotion = promotion_to_wire(move.promotion_type());
        if (fields.promotion == 0xFF)
            return DataResult::failure(DataError::INVALID_MOVE,
                                       "Atomic BIN V2 promotion piece is unsupported");
        break;
    case EN_PASSANT :
        fields.type = ATOMIC_BIN_V2_EN_PASSANT;
        break;
    case CASTLING :
        fields.type = ATOMIC_BIN_V2_CASTLING;
        break;
    default :
        return DataResult::failure(DataError::INVALID_MOVE,
                                   "Atomic BIN V2 move type is unsupported");
    }
    return DataResult::success();
}

DataResult move_from_wire(const AtomicBinV2MoveFields& fields, Move& move) {
    const Square from = Square(fields.from);
    const Square to   = Square(fields.to);
    switch (fields.type)
    {
    case ATOMIC_BIN_V2_NORMAL :
        move = Move(from, to);
        break;
    case ATOMIC_BIN_V2_PROMOTION : {
        const PieceType promotion = promotion_from_wire(fields.promotion);
        if (promotion == NO_PIECE_TYPE)
            return DataResult::failure(DataError::INVALID_MOVE,
                                       "Atomic BIN V2 promotion code is unsupported");
        move = Move::make<PROMOTION>(from, to, promotion);
        break;
    }
    case ATOMIC_BIN_V2_EN_PASSANT :
        move = Move::make<EN_PASSANT>(from, to);
        break;
    case ATOMIC_BIN_V2_CASTLING :
        move = Move::make<CASTLING>(from, to);
        break;
    default :
        return DataResult::failure(DataError::INVALID_MOVE,
                                   "Atomic BIN V2 move type is unsupported");
    }
    return DataResult::success();
}

bool legal_move(const Position& position, Move move) {
    for (Move legal : MoveList<LEGAL>(position))
        if (legal == move)
            return true;
    return false;
}

DataResult position_to_fields(const Position&            position,
                              u64                        rule50,
                              u64                        fullmove,
                              AtomicBinV2PositionFields& fields) {
    fields = {};
    fields.castlingRookOrigins.fill(AtomicBinV2NoSquare);
    fields.enPassantSquare = AtomicBinV2NoSquare;

    for (unsigned square = 0; square < 64; ++square)
    {
        fields.board[square] = piece_to_wire(position.piece_on(Square(square)));
        if (fields.board[square] == 0xFF)
            return unsupported("Atomic BIN V2 encountered an unsupported piece type");
    }
    switch (position.side_to_move())
    {
    case WHITE :
        fields.sideToMove = ATOMIC_BIN_V2_WHITE_TO_MOVE;
        break;
    case BLACK :
        fields.sideToMove = ATOMIC_BIN_V2_BLACK_TO_MOVE;
        break;
    default :
        return unsupported("Atomic BIN V2 encountered an invalid engine Color");
    }

    constexpr std::array<CastlingRights, 4> Rights = {WHITE_OO, WHITE_OOO, BLACK_OO, BLACK_OOO};
    for (unsigned index = 0; index < Rights.size(); ++index)
        if (position.can_castle(Rights[index]))
        {
            fields.castlingRights |= u8(1U << index);
            const Square origin = position.castling_rook_square(Rights[index]);
            if (!is_ok(origin))
                return unsupported("Atomic BIN V2 encountered an invalid castling origin");
            fields.castlingRookOrigins[index] = u8(origin);
        }

    if (position.ep_square() != SQ_NONE)
    {
        if (!is_ok(position.ep_square()))
            return unsupported("Atomic BIN V2 encountered an invalid en-passant square");
        fields.enPassantSquare = u8(position.ep_square());
    }
    fields.rule50   = u16(rule50);
    fields.fullmove = u32(fullmove);
    return DataResult::success();
}

std::string fields_to_fen(const AtomicBinV2PositionFields& fields, bool atomic960) {
    std::ostringstream fen;
    for (int rank = 7; rank >= 0; --rank)
    {
        int empty = 0;
        for (int file = 0; file < 8; ++file)
        {
            const u8 piece = fields.board[std::size_t(rank * 8 + file)];
            if (piece == ATOMIC_BIN_V2_EMPTY)
                ++empty;
            else
            {
                if (empty)
                    fen << empty;
                empty = 0;
                fen << piece_to_fen(piece);
            }
        }
        if (empty)
            fen << empty;
        if (rank)
            fen << '/';
    }

    fen << (fields.sideToMove == ATOMIC_BIN_V2_WHITE_TO_MOVE ? " w " : " b ");
    if (!fields.castlingRights)
        fen << '-';
    else
    {
        constexpr std::array<char, 4> Orthodox = {'K', 'Q', 'k', 'q'};
        for (unsigned index = 0; index < 4; ++index)
            if (fields.castlingRights & u8(1U << index))
            {
                if (!atomic960)
                    fen << Orthodox[index];
                else
                {
                    char origin = char('a' + fields.castlingRookOrigins[index] % 8);
                    fen << (index < 2 ? char(origin - 'a' + 'A') : origin);
                }
            }
    }
    fen << ' ';
    if (fields.enPassantSquare == AtomicBinV2NoSquare)
        fen << '-';
    else
        fen << char('a' + fields.enPassantSquare % 8) << char('1' + fields.enPassantSquare / 8);
    fen << ' ' << fields.rule50 << ' ' << fields.fullmove;
    return fen.str();
}

}  // namespace

DataResult encode_atomic_bin_v2(const TrainingDataSample& sample, AtomicBinV2Record& record) {
    record.fill(0);
    if (sample.flags & ~u32(TRAINING_DATA_CHESS960))
        return unsupported("Atomic BIN V2 sample contains unknown format-neutral flags");
    if (sample.score < AtomicBinV2MinScore || sample.score > AtomicBinV2MaxScore)
        return DataResult::failure(DataError::SCORE_OUT_OF_RANGE,
                                   "Atomic BIN V2 score is outside the initial domain");
    if (sample.ply < 0
        || std::uintmax_t(sample.ply) > std::uintmax_t(std::numeric_limits<u32>::max()))
        return DataResult::failure(DataError::PLY_OUT_OF_RANGE,
                                   "Atomic BIN V2 ply must fit an unsigned 32-bit integer");
    if (sample.result < -1 || sample.result > 1)
        return DataResult::failure(DataError::RESULT_OUT_OF_RANGE,
                                   "Atomic BIN V2 result must be -1, 0, or 1");

    std::array<std::string, 6> requestedFields;
    if (!split_fen_fields(sample.fen, requestedFields))
        return unsupported("Atomic BIN V2 requires exactly six FEN fields");
    u64 rule50   = 0;
    u64 fullmove = 0;
    if (!parse_decimal_field(requestedFields[4], AtomicBinV2MaxRule50, rule50)
        || !parse_decimal_field(requestedFields[5], AtomicBinV2MaxFullmove, fullmove)
        || fullmove == 0)
        return DataResult::failure(DataError::POSITION_CLOCK_OUT_OF_RANGE,
                                   "Atomic BIN V2 FEN clocks are outside engine-origin limits");

    const bool atomic960 = bool(sample.flags & TRAINING_DATA_CHESS960);
    Position   position;
    StateInfo  state{};
    if (const auto error = position.set(sample.fen, atomic960, &state))
        return unsupported(std::string("Cannot encode Atomic BIN V2 FEN: ") + error->what());

    std::array<std::string, 6> canonicalFields;
    if (!split_fen_fields(position.fen(), canonicalFields) || canonicalFields != requestedFields)
        return unsupported("Atomic BIN V2 refuses FEN fields normalized by the Atomic parser");
    if (position.count<KING>(WHITE) != 1 || position.count<KING>(BLACK) != 1)
        return unsupported("Atomic BIN V2 requires exactly one king per color");
    if (!legal_move(position, sample.move))
        return DataResult::failure(DataError::INVALID_MOVE,
                                   "Atomic BIN V2 move is not legal in its position");

    AtomicBinV2RecordFields fields{};
    if (DataResult packed = position_to_fields(position, rule50, fullmove, fields.position);
        !packed)
        return packed;
    if (DataResult mapped = move_to_wire(sample.move, fields.move); !mapped)
        return mapped;
    fields.score  = i32(sample.score);
    fields.ply    = u32(sample.ply);
    fields.result = i8(sample.result);
    fields.flags  = atomic960 ? ATOMIC_BIN_V2_ATOMIC960 : ATOMIC_BIN_V2_NO_FLAGS;
    return encode_atomic_bin_v2_record_structural(fields, record);
}

DataResult decode_atomic_bin_v2(const AtomicBinV2Record& record, TrainingDataSample& sample) {
    sample = {};
    AtomicBinV2RecordFields fields{};
    if (DataResult decoded = decode_atomic_bin_v2_record_structural(record, fields); !decoded)
        return decoded;
    if (std::uintmax_t(fields.ply) > std::uintmax_t(std::numeric_limits<int>::max()))
        return DataResult::failure(DataError::PLY_OUT_OF_RANGE,
                                   "Atomic BIN V2 ply does not fit TrainingDataSample");

    const bool        atomic960 = bool(fields.flags & ATOMIC_BIN_V2_ATOMIC960);
    const std::string fen       = fields_to_fen(fields.position, atomic960);
    Position          position;
    StateInfo         state{};
    if (const auto error = position.set(fen, atomic960, &state))
        return unsupported(std::string("Cannot decode Atomic BIN V2 FEN: ") + error->what());
    if (position.fen() != fen)
        return unsupported("Atomic BIN V2 wire position is not canonical");

    Move move = Move::none();
    if (DataResult mapped = move_from_wire(fields.move, move); !mapped)
        return mapped;
    if (!legal_move(position, move))
        return DataResult::failure(DataError::INVALID_MOVE,
                                   "Atomic BIN V2 move is not legal in its position");

    sample.fen    = fen;
    sample.score  = int(fields.score);
    sample.move   = move;
    sample.ply    = int(fields.ply);
    sample.result = int(fields.result);
    sample.flags  = atomic960 ? TRAINING_DATA_CHESS960 : NO_TRAINING_DATA_FLAGS;
    return DataResult::success();
}

}  // namespace Stockfish::Data
