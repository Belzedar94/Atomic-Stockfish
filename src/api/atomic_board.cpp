/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_board.h"

#include <algorithm>
#include <cctype>
#include <sstream>
#include <stdexcept>
#include <utility>

#include "../atomic_init.h"
#include "../movegen.h"
#include "../position.h"
#include "../uci_move.h"
#include "atomic_fen.h"
#include "atomic_notation.h"
#include "atomic_outcome.h"

namespace Stockfish::Atomic {
namespace {

constexpr std::string_view PieceToChar(" PNBRQK  pnbrqk");

std::string trim_and_lower(std::string_view value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string_view::npos)
        return {};
    const auto last = value.find_last_not_of(" \t\r\n");

    std::string normalized(value.substr(first, last - first + 1));
    std::transform(normalized.begin(), normalized.end(), normalized.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return normalized;
}

u64 perft_impl(Position& position, int depth) {
    if (depth == 0)
        return 1;

    const MoveList<LEGAL> moves(position);
    if (depth == 1)
        return moves.size();

    u64 nodes = 0;
    for (Move move : moves)
    {
        StateInfo state{};
        position.do_move(move, state);
        nodes += perft_impl(position, depth - 1);
        position.undo_move(move);
    }
    return nodes;
}

std::vector<std::string> split_words(std::string_view text) {
    std::istringstream       input{std::string(text)};
    std::vector<std::string> words;
    for (std::string word; input >> word;)
        words.push_back(std::move(word));
    return words;
}

}  // namespace

void initialize() { initialize_atomic_core(); }

Board::Board(std::string variant, std::string fen, bool isChess960) :
    variantName(normalize_variant(variant)),
    chess960(isChess960) {
    initialize();
    set_position_transactional(fen.empty() || fen == "startpos" ? std::string(StartFEN) : fen);
}

Board::~Board() = default;

bool Board::supports_variant(std::string_view variant) {
    const std::string normalized = trim_and_lower(variant);
    return normalized.empty() || normalized == "atomic";
}

std::string Board::normalize_variant(std::string_view variant) {
    if (!supports_variant(variant))
        throw std::invalid_argument("Atomic-Stockfish supports only the 'atomic' variant");
    return "atomic";
}

int Board::validate_fen(std::string_view fenValue, bool isChess960) {
    initialize();
    return Atomic::validate_fen(fenValue, isChess960);
}

const Position& Board::position() const { return *pos; }
Position&       Board::position() { return *pos; }

std::string Board::variant() const { return variantName; }
bool        Board::is_chess960() const { return chess960; }
std::string Board::fen() const { return pos->fen(); }

void Board::set_position_transactional(const std::string& fenValue) {
    const int validation = Board::validate_fen(fenValue, chess960);
    if (validation != FEN_OK)
        throw PositionSetError("Atomic FEN validation failed with code "
                               + std::to_string(validation));

    auto nextStates = std::make_unique<std::deque<StateInfo>>(1);
    auto nextPos    = std::make_unique<Position>();

    if (const auto error = nextPos->set(fenValue, chess960, &nextStates->back()))
        throw PositionSetError(error->what());

    states = std::move(nextStates);
    pos    = std::move(nextPos);
    moveStack.clear();
}

void Board::set_fen(const std::string& fenValue) {
    set_position_transactional(fenValue.empty() || fenValue == "startpos" ? std::string(StartFEN)
                                                                          : fenValue);
}

void Board::reset() { set_position_transactional(std::string(StartFEN)); }

std::vector<std::string> Board::legal_moves() const {
    std::vector<std::string> result;
    const MoveList<LEGAL>    moves(*pos);
    result.reserve(moves.size());
    for (Move move : moves)
        result.push_back(UCI::move(move, chess960));
    return result;
}

std::vector<std::string> Board::legal_moves_san() {
    std::vector<std::string> result;
    const MoveList<LEGAL>    moves(*pos);
    result.reserve(moves.size());
    for (Move move : moves)
        result.push_back(to_san(*pos, move));
    return result;
}

std::size_t Board::number_legal_moves() const { return MoveList<LEGAL>(*pos).size(); }

void Board::do_move(Move move) {
    states->emplace_back();
    pos->do_move(move, states->back());
    moveStack.push_back(move);
}

bool Board::push(const std::string& uciMove) {
    const Move move = UCI::to_move(*pos, uciMove);
    if (move == Move::none())
        return false;
    do_move(move);
    return true;
}

bool Board::push_san(const std::string& sanMove) {
    const Move move = parse_san(*pos, sanMove);
    if (move == Move::none())
        return false;
    do_move(move);
    return true;
}

std::string Board::pop() {
    if (moveStack.empty())
        throw std::out_of_range("cannot pop from an empty Atomic move stack");

    const Move        move = moveStack.back();
    const std::string uci  = UCI::move(move, chess960);
    pos->undo_move(move);
    moveStack.pop_back();
    states->pop_back();
    return uci;
}

void Board::push_moves(const std::string& uciMoves) {
    for (const std::string& move : split_words(uciMoves))
        if (!push(move))
            throw std::invalid_argument("invalid Atomic UCI move: " + move);
}

void Board::push_san_moves(const std::string& sanMoves) {
    for (const std::string& move : split_words(sanMoves))
        if (!push_san(move))
            throw std::invalid_argument("invalid Atomic SAN move: " + move);
}

std::string Board::san_move(const std::string& uciMove) {
    const Move move = UCI::to_move(*pos, uciMove);
    return move == Move::none() ? std::string{} : to_san(*pos, move);
}

std::string Board::variation_san(const std::string& uciMoves, bool moveNumbers) const {
    Board       board("atomic", fen(), chess960);
    std::string result;
    bool        first = true;

    for (const std::string& uciMove : split_words(uciMoves))
    {
        const Move move = UCI::to_move(board.position(), uciMove);
        if (move == Move::none())
            return {};

        if (!first)
            result += ' ';

        if (moveNumbers && (first || board.turn()))
        {
            result += std::to_string(board.fullmove_number());
            result += board.turn() ? "." : "...";
            if (first && board.turn())
                result += ' ';
        }

        result += to_san(board.position(), move);
        board.do_move(move);
        first = false;
    }

    return result;
}

std::string Board::move_stack() const {
    std::string result;
    for (Move move : moveStack)
    {
        if (!result.empty())
            result += ' ';
        result += UCI::move(move, chess960);
    }
    return result;
}

bool Board::turn() const { return pos->side_to_move() == WHITE; }
int  Board::fullmove_number() const { return pos->game_ply() / 2 + 1; }
int  Board::halfmove_clock() const { return pos->rule50_count(); }
int  Board::game_ply() const { return pos->game_ply(); }

bool Board::is_check() const { return checked_squares(*pos) != 0; }

std::string Board::checked_pieces() const {
    Bitboard    checked = checked_squares(*pos);
    std::string result;
    while (checked)
    {
        if (!result.empty())
            result += ' ';
        result += UCI::square(pop_lsb(checked));
    }
    return result;
}

bool Board::is_capture(const std::string& uciMove) const {
    const Move move = UCI::to_move(*pos, uciMove);
    return move != Move::none() && pos->capture(move);
}

bool Board::gives_check(const std::string& uciMove) const {
    Board      copy("atomic", fen(), chess960);
    const Move move = UCI::to_move(copy.position(), uciMove);
    if (move == Move::none())
        return false;
    copy.do_move(move);
    return copy.position().has_king(copy.position().side_to_move())
        && checked_squares(copy.position()) != 0;
}

bool Board::has_insufficient_material(bool white) const {
    return Atomic::has_insufficient_material(white ? WHITE : BLACK, *pos);
}

bool Board::is_insufficient_material() const {
    return Atomic::has_insufficient_material(WHITE, *pos)
        && Atomic::has_insufficient_material(BLACK, *pos);
}

bool Board::is_game_over(bool claimDraw) const { return outcome(*pos, claimDraw).terminal(); }
std::string Board::result(bool claimDraw) const { return outcome(*pos, claimDraw).result(); }

std::uint64_t Board::perft(int depth) const {
    if (depth < 0)
        throw std::invalid_argument("perft depth must be non-negative");
    Board copy("atomic", fen(), chess960);
    return perft_impl(copy.position(), depth);
}

std::string Board::to_string() const {
    std::string result;
    for (Rank rank = RANK_8;; --rank)
    {
        for (File file = FILE_A; file <= FILE_H; ++file)
        {
            if (file != FILE_A)
                result += ' ';
            const Piece piece = pos->piece_on(make_square(file, rank));
            result += piece == NO_PIECE ? '.' : PieceToChar[piece];
        }
        if (rank == RANK_1)
            break;
        result += '\n';
    }
    return result;
}

std::string Board::to_verbose_string() const {
    std::ostringstream output;
    output << *pos;
    return output.str();
}

}  // namespace Stockfish::Atomic
