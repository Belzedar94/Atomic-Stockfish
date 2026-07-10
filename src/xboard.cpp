/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "xboard.h"

#include <algorithm>
#include <charconv>
#include <cctype>
#include <cstdint>
#include <iostream>
#include <limits>
#include <type_traits>
#include <utility>

#include "engine.h"
#include "misc.h"
#include "movegen.h"
#include "position.h"
#include "score.h"
#include "uci_move.h"
#include "ucioption.h"

namespace Stockfish {

namespace {

constexpr std::string_view AtomicStartFEN =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

bool looks_like_move(const std::string& token) {
    if (token.size() != 4 && token.size() != 5)
        return false;

    auto isSquare = [](char file, char rank) {
        return file >= 'a' && file <= 'h' && rank >= '1' && rank <= '8';
    };

    if (!isSquare(token[0], token[1]) || !isSquare(token[2], token[3]))
        return false;

    return token.size() == 4 || std::string_view("qrbn").find(token[4]) != std::string_view::npos;
}

bool parse_integer(std::string_view token, int minimum, int maximum, int& value) {
    if (token.empty())
        return false;

    int parsed              = 0;
    const auto [end, error] = std::from_chars(token.data(), token.data() + token.size(), parsed);
    if (error != std::errc{} || end != token.data() + token.size() || parsed < minimum
        || parsed > maximum)
        return false;

    value = parsed;
    return true;
}

bool read_integer(std::istringstream& input, int minimum, int maximum, int& value) {
    std::string token;
    return bool(input >> token) && parse_integer(token, minimum, maximum, value);
}

bool integer_option(std::string name) {
    name = UCI::to_lower(std::move(name));
    return name == "threads" || name == "hash" || name == "multipv" || name == "skill level"
        || name == "move overhead" || name == "nodestime" || name == "syzygyprobedepth"
        || name == "syzygyprobelimit";
}

bool seconds_to_milliseconds(std::int64_t seconds, TimePoint& milliseconds) {
    constexpr auto Maximum = std::numeric_limits<TimePoint>::max();
    if (seconds < 0 || static_cast<TimePoint>(seconds) > Maximum / 1000)
        return false;

    milliseconds = static_cast<TimePoint>(seconds) * 1000;
    return true;
}

bool centiseconds_to_milliseconds(int centiseconds, TimePoint& milliseconds) {
    constexpr auto Maximum = std::numeric_limits<TimePoint>::max();
    if (centiseconds < 0 || static_cast<TimePoint>(centiseconds) > Maximum / 10)
        return false;

    milliseconds = static_cast<TimePoint>(centiseconds) * 10;
    return true;
}

bool parse_base_time(std::string_view token, TimePoint& milliseconds) {
    const auto colon   = token.find(':');
    int        minutes = 0;
    int        seconds = 0;

    if (colon == std::string_view::npos)
    {
        if (!parse_integer(token, 0, std::numeric_limits<int>::max() / 60, minutes))
            return false;
    }
    else
    {
        if (token.find(':', colon + 1) != std::string_view::npos
            || !parse_integer(token.substr(0, colon), 0, std::numeric_limits<int>::max() / 60,
                              minutes)
            || !parse_integer(token.substr(colon + 1), 0, 59, seconds))
            return false;
    }

    return seconds_to_milliseconds(std::int64_t(minutes) * 60 + seconds, milliseconds);
}

}  // namespace

XBoardProtocol::XBoardProtocol(Engine& engine_) :
    engine(engine_),
    initialFen(AtomicStartFEN),
    ponderEnabled(bool(int(engine.get_options()["Ponder"]))) {
    configure_callbacks();
}

XBoardProtocol::~XBoardProtocol() { stop_search(true); }

void XBoardProtocol::configure_callbacks() {
    engine.set_on_iter([](const Search::InfoIteration&) {});
    engine.set_on_update_no_moves(
      [this](const Search::InfoShort& info) { on_update_no_moves(info); });
    engine.set_on_update_full([this](const Search::InfoFull& info) { on_update_full(info); });
    engine.set_on_bestmove([this](std::string_view bestmove, std::string_view ponder) {
        on_bestmove(bestmove, ponder);
    });
    engine.set_on_verify_network([](std::string_view message) {
        for (const auto& line : split(message, "\n"))
            if (!is_whitespace(line))
                sync_cout << "# " << line << sync_endl;
    });
}

void XBoardProtocol::loop() {
    std::thread inputReader([this] {
        for (std::string line; std::getline(std::cin, line);)
        {
            std::string token;
            std::istringstream(line) >> token;
            {
                std::lock_guard<std::mutex> lock(eventMutex);
                commandQueue.push_back(std::move(line));
            }
            eventReady.notify_one();
            if (token == "quit")
                break;
        }

        {
            std::lock_guard<std::mutex> lock(eventMutex);
            inputClosed = true;
        }
        eventReady.notify_one();
    });

    bool quit = false;
    while (!quit)
    {
        std::string line;
        bool        searchFinished = false;
        {
            std::unique_lock<std::mutex> lock(eventMutex);
            eventReady.wait(lock, [this] {
                return completedSearch.has_value() || !commandQueue.empty() || inputClosed;
            });

            // Reap a completed search before later protocol commands. In
            // particular, a ping queued just after bestmove must observe the
            // played move and any autonomous ponder already initialized.
            searchFinished = completedSearch.has_value();
            if (!searchFinished && !commandQueue.empty())
            {
                line = std::move(commandQueue.front());
                commandQueue.pop_front();
            }
            else if (!searchFinished && inputClosed)
                quit = true;
        }

        if (searchFinished)
        {
            handle_search_completion();
            continue;
        }
        if (quit)
            break;

        std::istringstream is(line);
        std::string        token;
        is >> token;

        if (token.empty())
            continue;

        if (token == "quit")
        {
            stop_search(true);
            quit = true;
            continue;
        }

        process_command(token, is);
    }

    stop_search(true);
    if (inputReader.joinable())
        inputReader.join();
}

void XBoardProtocol::send_features() const {
    sync_cout << "feature setboard=1 usermove=1 time=1 memory=1 smp=1 colors=0 draw=0 "
                 "analyze=1 sigint=0 ping=1 reuse=1 san=0 name=0 egt=\"syzygy\" myname=\""
              << engine_version_info() << "\" variants=\"atomic\"" << sync_endl;

    sync_cout << "feature option=\"Use NNUE -combo true /// false /// pure\"" << sync_endl;
    sync_cout << "feature option=\"EvalFile -file " << std::string(engine.get_options()["EvalFile"])
              << "\"" << sync_endl;
    const std::string syzygyPath = engine.get_options()["SyzygyPath"];
    sync_cout << "feature option=\"SyzygyPath -path "
              << (syzygyPath.empty() ? "<empty>" : syzygyPath) << "\"" << sync_endl;
    sync_cout << "feature option=\"UCI_Chess960 -check 0\"" << sync_endl;
    sync_cout << "feature option=\"Ponder -check 0\"" << sync_endl;
    sync_cout << "feature done=1" << sync_endl;
}

void XBoardProtocol::reset_game() {
    stop_search(true);
    analysisMode = false;
    initialFen   = std::string(AtomicStartFEN);
    moves.clear();
    expectedPonder.clear();
    hintMove.clear();

    if (auto error = engine.set_position(initialFen, moves))
        sync_cout << "Error (invalid starting position): " << error->what() << sync_endl;

    engine.search_clear();
    engineColor = BLACK;
}

void XBoardProtocol::setboard(const std::string& fen) {
    stop_search(true);
    expectedPonder.clear();
    hintMove.clear();

    const std::string              oldFen   = initialFen;
    const std::vector<std::string> oldMoves = moves;

    initialFen = trim(fen);
    moves.clear();

    if (auto error = engine.set_position(initialFen, moves))
    {
        sync_cout << "Error (invalid FEN): " << error->what() << sync_endl;
        initialFen = oldFen;
        moves      = oldMoves;
        engine.set_position(initialFen, moves);
        return;
    }

    if (analysisMode)
        start_search(true);
    else
        maybe_start_engine();
}

bool XBoardProtocol::apply_move(const std::string& move, bool reportIllegal) {
    std::vector<std::string> candidate = moves;
    candidate.push_back(UCI::to_lower(move));

    if (auto error = engine.set_position(initialFen, candidate))
    {
        engine.set_position(initialFen, moves);
        if (reportIllegal)
            sync_cout << "Illegal move: " << move << " (" << error->what() << ")" << sync_endl;
        return false;
    }

    moves = std::move(candidate);
    return true;
}

void XBoardProtocol::start_search(bool analysis) {
    stop_search(true);

    // Search workers read OptionsMap without locking. Keep the engine's UCI
    // option in sync only after the preceding search has been joined, never
    // directly from a hard/easy command while a worker may be reading it.
    set_option("Ponder", ponderEnabled ? "true" : "false");

    Search::LimitsType searchLimits = analysis ? Search::LimitsType{} : limits;
    searchLimits.startTime          = now();
    searchLimits.ponderMode         = false;

    if (analysis)
        searchLimits.infinite = 1;

    acceptBestmove.store(!analysis, std::memory_order_release);
    searching.store(true, std::memory_order_release);
    searchMode.store(analysis ? SearchMode::Analyzing : SearchMode::Playing,
                     std::memory_order_release);
    if (!analysis)
    {
        expectedPonder.clear();
        hintMove.clear();
    }

    if (!engine.go(searchLimits))
    {
        acceptBestmove.store(false, std::memory_order_release);
        searching.store(false, std::memory_order_release);
        searchMode.store(SearchMode::Idle, std::memory_order_release);
    }
}

void XBoardProtocol::start_ponder() {
    if (!ponderEnabled || hintMove.empty()
        || searchMode.load(std::memory_order_acquire) != SearchMode::Idle)
        return;

    // start_ponder() is only entered while idle, so this is a safe point to
    // apply a hard command that may have arrived during the previous search.
    set_option("Ponder", "true");

    const std::string        predicted    = UCI::to_lower(hintMove);
    std::vector<std::string> hypothetical = moves;
    hypothetical.push_back(predicted);
    if (auto error = engine.set_position(initialFen, hypothetical))
    {
        restore_actual_position();
        hintMove.clear();
        return;
    }

    Search::LimitsType ponderLimits = limits;
    ponderLimits.startTime          = now();
    ponderLimits.ponderMode         = true;

    expectedPonder = predicted;
    acceptBestmove.store(true, std::memory_order_release);
    searching.store(true, std::memory_order_release);
    searchMode.store(SearchMode::Pondering, std::memory_order_release);

    if (!engine.go(ponderLimits))
    {
        acceptBestmove.store(false, std::memory_order_release);
        searching.store(false, std::memory_order_release);
        searchMode.store(SearchMode::Idle, std::memory_order_release);
        expectedPonder.clear();
        restore_actual_position();
    }
}

void XBoardProtocol::stop_search(bool discardMove) {
    const SearchMode mode = searchMode.load(std::memory_order_acquire);

    if (discardMove)
        acceptBestmove.store(false, std::memory_order_release);

    if (mode != SearchMode::Idle || searching.load(std::memory_order_acquire))
        engine.stop();

    engine.wait_for_search_finished();
    searching.store(false, std::memory_order_release);
    searchMode.store(SearchMode::Idle, std::memory_order_release);

    std::optional<SearchCompletion> completion;
    if (discardMove)
    {
        std::lock_guard<std::mutex> lock(eventMutex);
        completedSearch.reset();
    }
    else
    {
        std::lock_guard<std::mutex> lock(eventMutex);
        completion = std::exchange(completedSearch, std::nullopt);
    }

    if (mode == SearchMode::Pondering)
    {
        expectedPonder.clear();
        restore_actual_position();
    }

    if (!discardMove && completion && completion->accepted && mode == SearchMode::Playing)
        finish_playing_search(*completion);
}

void XBoardProtocol::handle_search_completion() {
    std::optional<SearchCompletion> completion;
    {
        std::lock_guard<std::mutex> lock(eventMutex);
        completion = std::exchange(completedSearch, std::nullopt);
    }
    if (!completion)
        return;

    const SearchMode mode = searchMode.load(std::memory_order_acquire);
    engine.wait_for_search_finished();
    searching.store(false, std::memory_order_release);
    searchMode.store(SearchMode::Idle, std::memory_order_release);

    if (!completion->accepted)
        return;

    if (mode == SearchMode::Playing)
        finish_playing_search(*completion);
    else if (mode == SearchMode::Pondering)
    {
        // A terminal hypothetical reply can finish before ponderhit. It must
        // never leak as a real move/result; restore the last played position.
        expectedPonder.clear();
        restore_actual_position();
    }
}

void XBoardProtocol::finish_playing_search(const SearchCompletion& completion) {
    if (!completion.bestmove.empty() && completion.bestmove != "(none)"
        && completion.bestmove != "0000")
    {
        if (!apply_move(completion.bestmove, false))
        {
            sync_cout << "Error (internal best move was illegal): " << completion.bestmove
                      << sync_endl;
            return;
        }

        hintMove = completion.ponder;
        sync_cout << "move " << completion.bestmove << sync_endl;
        start_ponder();
        return;
    }

    hintMove.clear();
    if (const auto result = terminal_result())
        sync_cout << *result << sync_endl;
    else
        sync_cout << "resign" << sync_endl;
}

void XBoardProtocol::restore_actual_position() {
    if (auto error = engine.set_position(initialFen, moves))
        sync_cout << "Error (could not restore XBoard position): " << error->what() << sync_endl;
}

void XBoardProtocol::set_pondering(bool enabled) {
    ponderEnabled = enabled;

    SearchMode mode = searchMode.load(std::memory_order_acquire);
    if (!enabled && mode == SearchMode::Pondering)
    {
        stop_search(true);
        mode = SearchMode::Idle;
    }

    // OptionsMap is shared with the search workers and is intentionally not a
    // concurrently mutable container. If hard/easy arrives during a playing
    // or analysis search, remember it in ponderEnabled and apply it at the
    // next idle search boundary instead of racing TimeManagement::init().
    if (mode == SearchMode::Idle)
        set_option("Ponder", enabled ? "true" : "false");

    if (enabled && mode == SearchMode::Idle)
        start_ponder();
}

void XBoardProtocol::process_move(const std::string& move) {
    const std::string normalized = UCI::to_lower(move);
    const SearchMode  mode       = searchMode.load(std::memory_order_acquire);

    if (mode == SearchMode::Pondering && normalized == expectedPonder)
    {
        // Engine::Position already contains this hypothetical move. Commit it
        // to protocol history and turn the running ponder into a timed search.
        moves.push_back(normalized);
        expectedPonder.clear();
        hintMove.clear();
        searchMode.store(SearchMode::Playing, std::memory_order_release);
        engine.set_ponderhit(false);
        return;
    }

    if (mode != SearchMode::Idle)
        stop_search(true);

    if (apply_move(normalized))
    {
        hintMove.clear();
        if (analysisMode)
            start_search(true);
        else
            maybe_start_engine();
    }
}

void XBoardProtocol::maybe_start_engine() {
    if (engineColor && side_to_move() == *engineColor)
        start_search(false);
}

void XBoardProtocol::set_option(const std::string& name, const std::string& value) {
    if (!engine.get_options().count(name))
    {
        sync_cout << "Error (unknown option): " << name << sync_endl;
        return;
    }

    std::istringstream option("name " + name + " value " + value);
    engine.get_options().setoption(option);
}

Color XBoardProtocol::side_to_move() const {
    std::istringstream fen(engine.fen());
    std::string        board, side;
    fen >> board >> side;
    return side == "b" ? BLACK : WHITE;
}

std::optional<std::string> XBoardProtocol::terminal_result() const {
    StateInfo state{};
    Position  position;
    if (position.set(engine.fen(), engine.get_options()["UCI_Chess960"], &state))
        return std::nullopt;

    const bool whiteKing = position.has_king(WHITE);
    const bool blackKing = position.has_king(BLACK);

    if (!whiteKing && blackKing)
        return "0-1 {Black wins by atomic explosion}";
    if (whiteKing && !blackKing)
        return "1-0 {White wins by atomic explosion}";
    if (!whiteKing || !blackKing || MoveList<LEGAL>(position).size())
        return std::nullopt;

    if (!position.atomic_in_check(position.side_to_move()))
        return "1/2-1/2 {Stalemate}";

    return position.side_to_move() == WHITE ? "0-1 {Black mates}" : "1-0 {White mates}";
}

void XBoardProtocol::process_command(const std::string& token, std::istringstream& is) {
    if (token == "protover")
        send_features();
    else if (token == "accepted" || token == "rejected" || token == "random" || token == "computer"
             || token == "name" || token == "rating")
    {}
    else if (!token.empty() && token[0] == '#')
    {}
    else if (token == "ping")
    {
        std::string id;
        is >> id;
        sync_cout << "pong" << (id.empty() ? "" : " " + id) << sync_endl;
    }
    else if (token == "new")
        reset_game();
    else if (token == "variant")
    {
        std::string variant;
        is >> variant;
        if (UCI::to_lower(variant) != "atomic")
            sync_cout << "Error (unsupported variant): " << variant << sync_endl;
        else
            reset_game();
    }
    else if (token == "force" || token == "result")
    {
        stop_search(true);
        analysisMode = false;
        engineColor.reset();
        expectedPonder.clear();
        hintMove.clear();
    }
    else if (token == "go")
    {
        stop_search(true);
        analysisMode = false;
        engineColor  = side_to_move();
        start_search(false);
    }
    else if (token == "playother")
    {
        stop_search(true);
        analysisMode = false;
        engineColor  = ~side_to_move();
        expectedPonder.clear();
        hintMove.clear();
    }
    else if (token == "?")
    {
        const bool playing = searchMode.load(std::memory_order_acquire) == SearchMode::Playing;
        stop_search(!playing);
    }
    else if (token == "setboard")
    {
        std::string fen;
        std::getline(is >> std::ws, fen);
        if (trim(fen).empty())
            sync_cout << "Error (invalid FEN): missing position" << sync_endl;
        else
            setboard(fen);
    }
    else if (token == "usermove")
    {
        std::string move;
        is >> move;
        process_move(move);
    }
    else if (looks_like_move(UCI::to_lower(token)))
        process_move(token);
    else if (token == "undo" || token == "remove")
    {
        stop_search(true);
        const usize count = token == "remove" ? 2 : 1;
        if (moves.size() >= count)
            moves.resize(moves.size() - count);
        engine.set_position(initialFen, moves);
        expectedPonder.clear();
        hintMove.clear();
        if (analysisMode)
            start_search(true);
    }
    else if (token == "level")
    {
        int           movesToGo = 0;
        int           increment = 0;
        std::string   base;
        TimePoint     baseMilliseconds      = 0;
        TimePoint     incrementMilliseconds = 0;
        constexpr int MaxSeconds            = std::numeric_limits<int>::max();

        if (!read_integer(is, 0, std::numeric_limits<int>::max(), movesToGo) || !(is >> base)
            || !read_integer(is, 0, MaxSeconds, increment)
            || !parse_base_time(base, baseMilliseconds)
            || !seconds_to_milliseconds(increment, incrementMilliseconds))
            sync_cout << "Error (invalid level): expected MPS MINUTES[:SECONDS] INCREMENT"
                      << sync_endl;
        else
        {
            limits.movestogo   = movesToGo;
            limits.movetime    = 0;
            limits.time[WHITE] = limits.time[BLACK] = baseMilliseconds;
            limits.inc[WHITE] = limits.inc[BLACK] = incrementMilliseconds;
        }
    }
    else if (token == "st")
    {
        int           seconds      = 0;
        constexpr int MaxSeconds   = std::numeric_limits<int>::max();
        TimePoint     milliseconds = 0;
        if (!read_integer(is, 0, MaxSeconds, seconds)
            || !seconds_to_milliseconds(seconds, milliseconds))
            sync_cout << "Error (invalid st): expected non-negative seconds" << sync_endl;
        else
        {
            limits.movetime    = milliseconds;
            limits.time[WHITE] = limits.time[BLACK] = 0;
            limits.inc[WHITE] = limits.inc[BLACK] = 0;
            limits.movestogo                      = 0;
        }
    }
    else if (token == "sd")
    {
        int depth = 0;
        if (!read_integer(is, 1, MAX_PLY - 1, depth))
            sync_cout << "Error (invalid sd): expected depth from 1 to " << MAX_PLY - 1
                      << sync_endl;
        else
            limits.depth = depth;
    }
    else if (token == "time" || token == "otim")
    {
        int           centiseconds    = 0;
        constexpr int MaxCentiseconds = std::numeric_limits<int>::max() / 10;
        TimePoint     milliseconds    = 0;
        if (!read_integer(is, 0, MaxCentiseconds, centiseconds)
            || !centiseconds_to_milliseconds(centiseconds, milliseconds))
            sync_cout << "Error (invalid " << token << "): expected non-negative centiseconds"
                      << sync_endl;
        else
        {
            const Color us                          = engineColor.value_or(side_to_move());
            limits.time[token == "time" ? us : ~us] = milliseconds;
        }
    }
    else if (token == "cores" || token == "memory")
    {
        int value = 0;
        if (!read_integer(is, 1, std::numeric_limits<int>::max(), value))
            sync_cout << "Error (invalid " << token << "): expected a positive integer"
                      << sync_endl;
        else
        {
            stop_search(true);
            set_option(token == "cores" ? "Threads" : "Hash", std::to_string(value));
        }
    }
    else if (token == "egtpath")
    {
        std::string tablebaseType;
        std::string path;
        is >> tablebaseType;
        std::getline(is >> std::ws, path);
        path = trim(path);

        if (tablebaseType.empty())
            sync_cout << "Error (invalid egtpath): expected TYPE PATH" << sync_endl;
        else if (UCI::to_lower(tablebaseType) != "syzygy")
            sync_cout << "Error (unsupported tablebase type): " << tablebaseType << sync_endl;
        else if (path.empty())
            sync_cout << "Error (invalid egtpath): missing Syzygy path" << sync_endl;
        else
        {
            stop_search(true);
            set_option("SyzygyPath", path);
        }
    }
    else if (token == "hard" || token == "easy")
        set_pondering(token == "hard");
    else if (token == "option")
    {
        std::string setting;
        std::getline(is >> std::ws, setting);
        const auto equals = setting.find('=');
        const auto name   = trim(setting.substr(0, equals));
        auto value = equals == std::string::npos ? std::string{} : trim(setting.substr(equals + 1));
        if ((name == "Ponder" || name == "UCI_Chess960") && (value == "0" || value == "1"))
            value = value == "1" ? "true" : "false";
        int numericValue = 0;
        if (name.empty())
            sync_cout << "Error (invalid option): missing name" << sync_endl;
        else if (integer_option(name)
                 && !parse_integer(value, std::numeric_limits<int>::min(),
                                   std::numeric_limits<int>::max(), numericValue))
            sync_cout << "Error (invalid option " << name << "): expected an integer" << sync_endl;
        else if (name == "Ponder" && (value == "true" || value == "false"))
            set_pondering(value == "true");
        else
        {
            stop_search(true);
            set_option(name, value);
        }
    }
    else if (token == "analyze")
    {
        stop_search(true);
        analysisMode = true;
        engineColor.reset();
        start_search(true);
    }
    else if (token == "exit")
    {
        stop_search(true);
        analysisMode = false;
    }
    else if (token == "post" || token == "nopost")
        postThinking.store(token == "post", std::memory_order_release);
    else if (token == "hint")
    {
        if (!hintMove.empty())
            sync_cout << "Hint: " << hintMove << sync_endl;
    }
    else if (token == "d")
    {
        stop_search(true);
        sync_cout << engine.visualize() << sync_endl;
    }
    else if (token == "eval")
    {
        stop_search(true);
        engine.trace_eval();
    }
    else if (token == "perft")
    {
        int depth = 0;
        if (!read_integer(is, 0, MAX_PLY - 1, depth))
            sync_cout << "Error (invalid perft): expected depth from 0 to " << MAX_PLY - 1
                      << sync_endl;
        else
        {
            stop_search(true);
            sync_cout << "Nodes searched: "
                      << engine.perft(engine.fen(), depth, engine.get_options()["UCI_Chess960"])
                      << sync_endl;
        }
    }
    else
        sync_cout << "Error (unknown command): " << token << sync_endl;
}

int XBoardProtocol::xboard_score(const Score& score) {
    return score.visit([](const auto& value) -> int {
        using T = std::decay_t<decltype(value)>;
        if constexpr (std::is_same_v<T, Score::Mate>)
            return value.plies > 0 ? 100000 - value.plies : -100000 - value.plies;
        else if constexpr (std::is_same_v<T, Score::Tablebase>)
            return (value.win ? 20000 : -20000) - value.plies;
        else
            return value.value;
    });
}

void XBoardProtocol::on_update_no_moves(const Search::InfoShort& info) const {
    if (postThinking.load(std::memory_order_acquire))
        sync_cout << info.depth << " " << xboard_score(info.score) << " 0 0" << sync_endl;
}

void XBoardProtocol::on_update_full(const Search::InfoFull& info) const {
    if (!postThinking.load(std::memory_order_acquire))
        return;

    sync_cout << info.depth << " " << xboard_score(info.score) << " " << info.timeMs / 10 << " "
              << info.nodes << (info.pv.empty() ? "" : " " + std::string(info.pv)) << sync_endl;
}

void XBoardProtocol::on_bestmove(std::string_view bestmove, std::string_view ponder) {
    {
        std::lock_guard<std::mutex> lock(eventMutex);
        completedSearch =
          SearchCompletion{acceptBestmove.exchange(false, std::memory_order_acq_rel),
                           std::string(bestmove), std::string(ponder)};
    }
    searching.store(false, std::memory_order_release);
    eventReady.notify_one();
}

std::string XBoardProtocol::trim(std::string value) {
    const auto first =
      std::find_if_not(value.begin(), value.end(), [](unsigned char c) { return std::isspace(c); });
    const auto last = std::find_if_not(value.rbegin(), value.rend(), [](unsigned char c) {
                          return std::isspace(c);
                      }).base();
    return first < last ? std::string(first, last) : std::string{};
}

}  // namespace Stockfish
