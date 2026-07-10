/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef XBOARD_H_INCLUDED
#define XBOARD_H_INCLUDED

#include <atomic>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "search.h"
#include "types.h"

namespace Stockfish {

class Engine;
class Score;

// XBoardProtocol implements CECP protocol version 2 on top of the same Engine
// object used by UCI. It intentionally owns protocol state, not chess state:
// positions are always rebuilt through Engine::set_position(), keeping native,
// Python, JavaScript, WASM, UCI, and XBoard on one rules/search core.
class XBoardProtocol {
   public:
    explicit XBoardProtocol(Engine& engine);
    ~XBoardProtocol();

    XBoardProtocol(const XBoardProtocol&)            = delete;
    XBoardProtocol(XBoardProtocol&&)                 = delete;
    XBoardProtocol& operator=(const XBoardProtocol&) = delete;
    XBoardProtocol& operator=(XBoardProtocol&&)      = delete;

    void loop();

   private:
    enum class SearchMode : u8 {
        Idle,
        Playing,
        Pondering,
        Analyzing
    };

    struct SearchCompletion {
        bool        accepted = false;
        std::string bestmove;
        std::string ponder;
    };

    Engine& engine;

    std::string              initialFen;
    std::vector<std::string> moves;
    Search::LimitsType       limits;
    std::optional<Color>     engineColor;

    std::atomic_bool        searching{false};
    std::atomic_bool        acceptBestmove{false};
    std::atomic_bool        postThinking{true};
    std::atomic<SearchMode> searchMode{SearchMode::Idle};
    bool                    analysisMode  = false;
    bool                    ponderEnabled = false;

    std::mutex                      eventMutex;
    std::condition_variable         eventReady;
    std::deque<std::string>         commandQueue;
    std::optional<SearchCompletion> completedSearch;
    bool                            inputClosed = false;

    std::string expectedPonder;
    std::string hintMove;

    void configure_callbacks();
    void process_command(const std::string& token, std::istringstream& is);
    void send_features() const;

    void reset_game();
    void setboard(const std::string& fen);
    bool apply_move(const std::string& move, bool reportIllegal = true);
    void process_move(const std::string& move);

    void start_search(bool analysis);
    void start_ponder();
    void stop_search(bool discardMove);
    void handle_search_completion();
    void finish_playing_search(const SearchCompletion& completion);
    void restore_actual_position();
    void set_pondering(bool enabled);
    void maybe_start_engine();

    void                       set_option(const std::string& name, const std::string& value);
    Color                      side_to_move() const;
    std::optional<std::string> terminal_result() const;

    void on_update_no_moves(const Search::InfoShort& info) const;
    void on_update_full(const Search::InfoFull& info) const;
    void on_bestmove(std::string_view bestmove, std::string_view ponder);

    static int         xboard_score(const Score& score);
    static std::string trim(std::string value);
};

}  // namespace Stockfish

#endif  // XBOARD_H_INCLUDED
