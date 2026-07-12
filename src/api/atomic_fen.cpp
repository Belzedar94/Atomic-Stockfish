/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)
*/

#include "atomic_fen.h"

#include <array>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "../position.h"

namespace Stockfish::Atomic {
namespace {

int classify_error(std::string_view message) {
    if (message.find("side to move") != std::string_view::npos)
        return FEN_INVALID_SIDE_TO_MOVE;
    if (message.find("castling") != std::string_view::npos)
        return FEN_INVALID_CASTLING_INFO;
    if (message.find("en-passant") != std::string_view::npos)
        return FEN_INVALID_EN_PASSANT_SQ;
    if (message.find("king") != std::string_view::npos)
        return FEN_INVALID_NUMBER_OF_KINGS;
    if (message.find("Rule50") != std::string_view::npos)
        return FEN_INVALID_HALF_MOVE_COUNTER;
    if (message.find("Game ply") != std::string_view::npos)
        return FEN_INVALID_MOVE_COUNTER;
    if (message.find("Invalid piece") != std::string_view::npos)
        return FEN_INVALID_CHAR;
    if (message.find("end of stream") != std::string_view::npos
        || message.find("Expected whitespace") != std::string_view::npos)
        return FEN_INVALID_NB_PARTS;
    return FEN_INVALID_BOARD_GEOMETRY;
}

int active_castling_rights(const Position& pos) {
    int count = 0;
    for (CastlingRights right : {WHITE_OO, WHITE_OOO, BLACK_OO, BLACK_OOO})
        count += pos.can_castle(right) ? 1 : 0;
    return count;
}

bool is_unsigned_decimal(std::string_view field) {
    if (field.empty())
        return false;

    for (const char character : field)
        if (character < '0' || character > '9')
            return false;

    return true;
}

}  // namespace

int validate_fen(std::string_view fen, bool chess960) {
    if (fen.empty())
        return FEN_EMPTY;

    std::istringstream       input{std::string(fen)};
    std::vector<std::string> fields;
    for (std::string field; input >> field;)
        fields.push_back(std::move(field));
    if (fields.size() != 6)
        return FEN_INVALID_NB_PARTS;

    StateInfo state{};
    Position  position;
    if (const auto error = position.set(std::string(fen), chess960, &state))
        return classify_error(error->what());

    // Position::set intentionally sanitizes stale GUI castling flags. Public
    // validation is stricter: every requested flag must map to a real right.
    const std::string& castling = fields[2];
    if (castling != "-")
    {
        if (castling.empty() || castling.size() > 4
            || active_castling_rights(position) != static_cast<int>(castling.size()))
            return FEN_INVALID_CASTLING_INFO;
    }

    // Preserve field-order precedence: a malformed board, castling right, or
    // other earlier FEN field must not be hidden by a later invalid counter.
    if (!is_unsigned_decimal(fields[4]))
        return FEN_INVALID_HALF_MOVE_COUNTER;
    if (!is_unsigned_decimal(fields[5]))
        return FEN_INVALID_MOVE_COUNTER;

    return FEN_OK;
}

}  // namespace Stockfish::Atomic
