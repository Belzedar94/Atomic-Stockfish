/*
  Atomic-Stockfish JavaScript/WebAssembly bindings
  Copyright (C) 2026 The Atomic-Stockfish developers

  The public API is compatible with ffish.js from Fairy-Stockfish,
  Copyright (C) 2022 Fabian Fichter and Johannes Czech.

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <emscripten/bind.h>
#include <emscripten/heap.h>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "api/atomic_board.h"
#include "api/atomic_notation.h"
#include "api/atomic_outcome.h"
#include "atomic_version.h"
#include "position.h"
#include "uci_move.h"

namespace Stockfish::Atomic::Js {
namespace {

enum class Notation {
    Default,
    San,
    Lan
};

int LiveBoards = 0;

std::string trim(std::string_view value) {
    const usize first = value.find_first_not_of(" \t\r\n");
    if (first == std::string_view::npos)
        return {};
    const usize last = value.find_last_not_of(" \t\r\n");
    return std::string(value.substr(first, last - first + 1));
}

std::string lower(std::string_view value) {
    std::string result(value);
    std::transform(result.begin(), result.end(), result.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return result;
}

std::vector<std::string> split_words(std::string_view value) {
    std::istringstream       input{std::string(value)};
    std::vector<std::string> words;
    for (std::string word; input >> word;)
        words.push_back(std::move(word));
    return words;
}

std::string join(const std::vector<std::string>& values) {
    std::string result;
    for (const std::string& value : values)
    {
        if (!result.empty())
            result += ' ';
        result += value;
    }
    return result;
}

std::string require_variant(std::string_view variant) {
    return Board::normalize_variant(variant);
}

std::string notation_for_move(Board& board, const std::string& uci, Notation notation) {
    const Move move = UCI::to_move(board.position(), uci);
    if (move == Move::none())
        return {};
    return notation == Notation::Lan ? to_lan(board.position(), move)
                                     : to_san(board.position(), move);
}

bool push_with_notation(Board& board, const std::string& text, Notation notation) {
    if (notation != Notation::Lan)
        return board.push_san(text);

    const Move move = parse_lan(board.position(), text);
    return move != Move::none() && board.push(UCI::move(move, board.is_chess960()));
}

std::string format_variation(const Board& source,
                             const std::string& uciMoves,
                             Notation           notation,
                             bool               moveNumbers) {
    Board       board("atomic", source.fen(), source.is_chess960());
    std::string result;
    bool        first = true;

    for (const std::string& uci : split_words(uciMoves))
    {
        const std::string formatted = notation_for_move(board, uci, notation);
        if (formatted.empty())
            return {};

        if (!first)
            result += ' ';
        if (moveNumbers && (first || board.turn()))
        {
            result += std::to_string(board.fullmove_number());
            if (board.turn())
            {
                result += '.';
                result += ' ';
            }
            else
                result += "...";
        }
        result += formatted;
        if (!board.push(uci))
            return {};
        first = false;
    }
    return result;
}

class JsBoard final {
   public:
    JsBoard() : board("atomic") { ++LiveBoards; }
    explicit JsBoard(std::string variant) : board(require_variant(variant)) { ++LiveBoards; }
    JsBoard(std::string variant, std::string fen) :
        board(require_variant(variant), std::move(fen)) {
        ++LiveBoards;
    }
    JsBoard(std::string variant, std::string fen, bool chess960) :
        board(require_variant(variant), std::move(fen), chess960) {
        ++LiveBoards;
    }
    ~JsBoard() { --LiveBoards; }

    std::string legal_moves() const { return join(board.legal_moves()); }
    std::string legal_moves_san() { return join(board.legal_moves_san()); }
    int         number_legal_moves() const { return static_cast<int>(board.number_legal_moves()); }

    bool push(const std::string& move) { return board.push(move); }
    bool push_san(const std::string& move) { return board.push_san(move); }
    bool push_san(const std::string& move, Notation notation) {
        return push_with_notation(board, move, notation);
    }
    void pop() { static_cast<void>(board.pop()); }
    void        reset() { board.reset(); }

    bool        is_960() const { return board.is_chess960(); }
    std::string fen() const { return board.fen(); }
    std::string fen(bool) const { return board.fen(); }
    std::string fen(bool, int) const { return board.fen(); }
    void        set_fen(const std::string& fenValue) { board.set_fen(fenValue); }

    std::string san_move(const std::string& uci) {
        return notation_for_move(board, uci, Notation::San);
    }
    std::string san_move(const std::string& uci, Notation notation) {
        return notation_for_move(board, uci, notation);
    }
    std::string variation_san(const std::string& moves) const {
        return format_variation(board, moves, Notation::San, true);
    }
    std::string variation_san(const std::string& moves, Notation notation) const {
        return format_variation(board, moves, notation, true);
    }
    std::string variation_san(const std::string& moves, Notation notation, bool moveNumbers) const {
        return format_variation(board, moves, notation, moveNumbers);
    }

    bool turn() const { return board.turn(); }
    int  fullmove_number() const { return board.fullmove_number(); }
    int  halfmove_clock() const { return board.halfmove_clock(); }
    int  game_ply() const { return board.game_ply(); }

    bool has_insufficient_material(bool white) const {
        return board.has_insufficient_material(white);
    }
    bool is_insufficient_material() const { return board.is_insufficient_material(); }
    bool is_game_over() const { return board.is_game_over(); }
    bool is_game_over(bool claimDraw) const { return board.is_game_over(claimDraw); }
    std::string result() const { return board.result(); }
    std::string result(bool claimDraw) const { return board.result(claimDraw); }
    std::string checked_pieces() const { return board.checked_pieces(); }
    bool        is_check() const { return board.is_check(); }
    bool        is_capture(const std::string& move) const { return board.is_capture(move); }
    bool        gives_check(const std::string& move) const { return board.gives_check(move); }
    std::string move_stack() const { return board.move_stack(); }

    // Validate every token on a disposable board first. A bad bulk request is
    // therefore atomic from the caller's perspective and never leaves a
    // partially modified JS Board.
    void push_moves(const std::string& moves) {
        Board candidate("atomic", board.fen(), board.is_chess960());
        candidate.push_moves(moves);
        board.push_moves(moves);
    }
    void push_san_moves(const std::string& moves) {
        push_san_moves(moves, Notation::San);
    }
    void push_san_moves(const std::string& moves, Notation notation) {
        Board candidate("atomic", board.fen(), board.is_chess960());
        for (const std::string& move : split_words(moves))
            if (!push_with_notation(candidate, move, notation))
                throw std::invalid_argument("invalid Atomic notation in bulk move list: " + move);
        for (const std::string& move : split_words(moves))
            if (!push_with_notation(board, move, notation))
                throw std::logic_error("validated Atomic move failed during commit");
    }

    double      perft(int depth) const { return static_cast<double>(board.perft(depth)); }
    std::string to_string() const { return board.to_string(); }
    std::string to_verbose_string() const { return board.to_verbose_string(); }
    std::string variant() const { return board.variant(); }

   private:
    Board board;
};

std::string info() {
    return "Atomic-Stockfish " + std::string(Stockfish::AtomicVersionString) + " JS/WASM";
}
std::string variants() { return "atomic"; }
int         debug_live_boards() { return LiveBoards; }
double      wasm_heap_bytes() { return static_cast<double>(emscripten_get_heap_size()); }
bool        two_boards(const std::string& variant) {
    require_variant(variant);
    return false;
}
bool captures_to_hand(const std::string& variant) {
    require_variant(variant);
    return false;
}
std::string starting_fen(const std::string& variant) {
    require_variant(variant);
    return std::string(StartFEN);
}

int validate_fen(const std::string& fen) { return Board::validate_fen(fen); }
int validate_fen(const std::string& fen, const std::string& variant) {
    require_variant(variant);
    return Board::validate_fen(fen);
}
int validate_fen(const std::string& fen, const std::string& variant, bool chess960) {
    require_variant(variant);
    return Board::validate_fen(fen, chess960);
}

void set_option(const std::string& name, const std::string& value) {
    const std::string normalized = lower(trim(name));
    if (normalized == "uci_variant")
    {
        require_variant(value);
        return;
    }
    if (normalized == "use nnue")
    {
        const std::string mode = lower(trim(value));
        if (mode == "false" || mode == "true" || mode == "pure")
            return;
        throw std::invalid_argument("Use NNUE must be false, true, or pure");
    }
    throw std::invalid_argument("unsupported Atomic JS string option: " + name);
}

void set_option_int(const std::string& name, int value) {
    const std::string normalized = lower(trim(name));
    if ((normalized == "threads" || normalized == "hash") && value >= 1)
        return;
    if (normalized == "move overhead" && value >= 0)
        return;
    throw std::invalid_argument("unsupported or invalid Atomic JS integer option: " + name);
}

void set_option_bool(const std::string& name, bool) {
    const std::string normalized = lower(trim(name));
    if (normalized == "uci_chess960" || normalized == "ponder")
        return;
    throw std::invalid_argument("unsupported Atomic JS boolean option: " + name);
}

class Game final {
   public:
    Game() = default;
    Game(const Game&) = default;
    Game& operator=(const Game&) = default;
    Game(Game&&) noexcept = default;
    Game& operator=(Game&&) noexcept = default;

    std::string header_keys() const {
        std::vector<std::string> keys;
        keys.reserve(headers.size());
        for (const auto& [key, value] : headers)
        {
            static_cast<void>(value);
            keys.push_back(key);
        }
        return join(keys);
    }

    std::string header(const std::string& key) const {
        for (const auto& [candidate, value] : headers)
            if (candidate == key)
                return value;
        return {};
    }

    std::string mainline_moves() const { return mainline; }

    static Game parse(const std::string& pgn);

   private:
    std::vector<std::pair<std::string, std::string>> headers;
    std::string                                      mainline;
};

std::string strip_movetext_noise(std::string_view text) {
    std::string result;
    int         variationDepth = 0;
    bool        braceComment   = false;
    bool        lineComment    = false;

    for (usize index = 0; index < text.size(); ++index)
    {
        const char c = text[index];
        if (lineComment)
        {
            if (c == '\n')
            {
                lineComment = false;
                result += ' ';
            }
            continue;
        }
        if (braceComment)
        {
            if (c == '}')
            {
                braceComment = false;
                result += ' ';
            }
            continue;
        }
        if (c == ';')
        {
            lineComment = true;
            continue;
        }
        if (c == '{')
        {
            braceComment = true;
            continue;
        }
        if (c == '(')
        {
            ++variationDepth;
            continue;
        }
        if (c == ')')
        {
            if (variationDepth == 0)
                throw std::invalid_argument("unmatched ')' in Atomic PGN");
            --variationDepth;
            continue;
        }
        if (variationDepth == 0)
            result += c;
    }

    if (braceComment)
        throw std::invalid_argument("unterminated comment in Atomic PGN");
    if (variationDepth)
        throw std::invalid_argument("unterminated variation in Atomic PGN");
    return result;
}

std::string remove_move_number(std::string token) {
    usize digitEnd = 0;
    while (digitEnd < token.size() && std::isdigit(static_cast<unsigned char>(token[digitEnd])))
        ++digitEnd;
    if (digitEnd == 0 || digitEnd == token.size() || token[digitEnd] != '.')
        return token;
    usize moveStart = digitEnd;
    while (moveStart < token.size() && token[moveStart] == '.')
        ++moveStart;
    return token.substr(moveStart);
}

Game Game::parse(const std::string& pgn) {
    Game        game;
    std::string movetext;
    bool        readingHeaders = true;
    std::istringstream input(pgn);

    for (std::string line; std::getline(input, line);)
    {
        const std::string cleaned = trim(line);
        if (readingHeaders && !cleaned.empty() && cleaned.front() == '[')
        {
            if (cleaned.back() != ']')
                throw std::invalid_argument("malformed Atomic PGN header");
            const usize separator = cleaned.find_first_of(" \t", 1);
            const usize firstQuote = cleaned.find('"', separator);
            const usize lastQuote  = cleaned.rfind('"');
            if (separator == std::string::npos || firstQuote == std::string::npos
                || lastQuote == firstQuote)
                throw std::invalid_argument("malformed Atomic PGN header");
            const std::string key   = cleaned.substr(1, separator - 1);
            const std::string value = cleaned.substr(firstQuote + 1, lastQuote - firstQuote - 1);
            bool              replaced = false;
            for (auto& [existingKey, existingValue] : game.headers)
                if (existingKey == key)
                {
                    existingValue = value;
                    replaced      = true;
                    break;
                }
            if (!replaced)
                game.headers.emplace_back(key, value);
            continue;
        }

        readingHeaders = false;
        movetext += line;
        movetext += '\n';
    }

    const std::string variantHeader = lower(trim(game.header("Variant")));
    bool              chess960      = false;
    if (!variantHeader.empty())
    {
        if (variantHeader == "atomic960" || variantHeader == "atomic 960")
            chess960 = true;
        else if (variantHeader != "atomic" && variantHeader != "atomic chess")
            throw std::invalid_argument("Atomic-Stockfish cannot read non-Atomic PGN");
    }

    const std::string initialFen = game.header("FEN");
    JsBoard           board("atomic", initialFen, chess960);
    const std::string cleanMovetext = strip_movetext_noise(movetext);
    for (std::string token : split_words(cleanMovetext))
    {
        token = remove_move_number(std::move(token));
        if (token.empty() || token.front() == '$' || token == "*" || token == "1-0"
            || token == "0-1" || token == "1/2-1/2")
            continue;
        while (!token.empty() && (token.back() == '!' || token.back() == '?'))
            token.pop_back();
        if (token.empty())
            continue;
        if (token == "0-0")
            token = "O-O";
        else if (token == "0-0-0")
            token = "O-O-O";
        if (!board.push_san(token))
            throw std::invalid_argument("invalid Atomic PGN move: " + token);
    }
    game.mainline = board.move_stack();
    return game;
}

Game read_game_pgn(const std::string& pgn) { return Game::parse(pgn); }

}  // namespace
}  // namespace Stockfish::Atomic::Js

EMSCRIPTEN_BINDINGS(atomic_stockfish_js) {
    using namespace emscripten;
    using namespace Stockfish::Atomic;
    using namespace Stockfish::Atomic::Js;

    class_<JsBoard>("Board")
      .constructor<>()
      .constructor<std::string>()
      .constructor<std::string, std::string>()
      .constructor<std::string, std::string, bool>()
      .function("legalMoves", &JsBoard::legal_moves)
      .function("legalMovesSan", &JsBoard::legal_moves_san)
      .function("numberLegalMoves", &JsBoard::number_legal_moves)
      .function("push", &JsBoard::push)
      .function("pushSan", select_overload<bool(const std::string&)>(&JsBoard::push_san))
      .function("pushSan",
                select_overload<bool(const std::string&, Notation)>(&JsBoard::push_san))
      .function("pop", &JsBoard::pop)
      .function("reset", &JsBoard::reset)
      .function("is960", &JsBoard::is_960)
      .function("fen", select_overload<std::string() const>(&JsBoard::fen))
      .function("fen", select_overload<std::string(bool) const>(&JsBoard::fen))
      .function("fen", select_overload<std::string(bool, int) const>(&JsBoard::fen))
      .function("setFen", &JsBoard::set_fen)
      .function("sanMove", select_overload<std::string(const std::string&)>(&JsBoard::san_move))
      .function("sanMove",
                select_overload<std::string(const std::string&, Notation)>(&JsBoard::san_move))
      .function("variationSan",
                select_overload<std::string(const std::string&) const>(&JsBoard::variation_san))
      .function("variationSan",
                select_overload<std::string(const std::string&, Notation) const>(
                  &JsBoard::variation_san))
      .function("variationSan",
                select_overload<std::string(const std::string&, Notation, bool) const>(
                  &JsBoard::variation_san))
      .function("turn", &JsBoard::turn)
      .function("fullmoveNumber", &JsBoard::fullmove_number)
      .function("halfmoveClock", &JsBoard::halfmove_clock)
      .function("gamePly", &JsBoard::game_ply)
      .function("hasInsufficientMaterial", &JsBoard::has_insufficient_material)
      .function("isInsufficientMaterial", &JsBoard::is_insufficient_material)
      .function("isGameOver", select_overload<bool() const>(&JsBoard::is_game_over))
      .function("isGameOver", select_overload<bool(bool) const>(&JsBoard::is_game_over))
      .function("result", select_overload<std::string() const>(&JsBoard::result))
      .function("result", select_overload<std::string(bool) const>(&JsBoard::result))
      .function("checkedPieces", &JsBoard::checked_pieces)
      .function("isCheck", &JsBoard::is_check)
      .function("isCapture", &JsBoard::is_capture)
      .function("givesCheck", &JsBoard::gives_check)
      .function("moveStack", &JsBoard::move_stack)
      .function("pushMoves", &JsBoard::push_moves)
      .function("pushSanMoves",
                select_overload<void(const std::string&)>(&JsBoard::push_san_moves))
      .function("pushSanMoves",
                select_overload<void(const std::string&, Notation)>(&JsBoard::push_san_moves))
      .function("perft", &JsBoard::perft)
      .function("toString", &JsBoard::to_string)
      .function("toVerboseString", &JsBoard::to_verbose_string)
      .function("variant", &JsBoard::variant);

    class_<Game>("Game")
      .function("headerKeys", &Game::header_keys)
      .function("headers", &Game::header)
      .function("mainlineMoves", &Game::mainline_moves);

    enum_<Notation>("Notation")
      .value("DEFAULT", Notation::Default)
      .value("SAN", Notation::San)
      .value("LAN", Notation::Lan);

    enum_<Termination>("Termination")
      .value("ONGOING", Termination::Ongoing)
      .value("ATOMIC_EXPLOSION", Termination::AtomicExplosion)
      .value("CHECKMATE", Termination::Checkmate)
      .value("STALEMATE", Termination::Stalemate)
      .value("INSUFFICIENT_MATERIAL", Termination::InsufficientMaterial)
      .value("FIFTY_MOVE_RULE", Termination::FiftyMoveRule)
      .value("THREEFOLD_REPETITION", Termination::ThreefoldRepetition);

    function("info", &info);
    function("setOption", &set_option);
    function("setOptionInt", &set_option_int);
    function("setOptionBool", &set_option_bool);
    function("readGamePGN", &read_game_pgn);
    function("variants", &variants);
    function("debugLiveBoards", &debug_live_boards);
    function("wasmHeapBytes", &wasm_heap_bytes);
    function("twoBoards", &two_boards);
    function("capturesToHand", &captures_to_hand);
    function("startingFen", &starting_fen);
    function("validateFen", select_overload<int(const std::string&)>(&validate_fen));
    function("validateFen",
             select_overload<int(const std::string&, const std::string&)>(&validate_fen));
    function("validateFen",
             select_overload<int(const std::string&, const std::string&, bool)>(&validate_fen));
}
