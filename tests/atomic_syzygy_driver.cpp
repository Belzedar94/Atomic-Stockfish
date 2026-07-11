/*
  Standalone integration driver for tests/atomic_syzygy.py.

  This deliberately remains a separate executable from the production engine.
  It lets the tablebase decoder be exercised directly, independently from the
  UCI/search integration tested by the black-box protocol suite.
*/

#include <cstdlib>
#include <iostream>
#include <string>

#include "attacks.h"
#include "bitboard.h"
#include "movegen.h"
#include "position.h"
#include "search.h"
#include "syzygy/tbprobe.h"
#include "tt.h"
#include "uci_move.h"
#include "ucioption.h"

using namespace Stockfish;

// Position's production object references these helpers from cold/debug paths.
// The driver never supplies a TT to do_move(), so a null test stub is sufficient
// and avoids linking the complete search/thread/engine stack.
TTEntry* TranspositionTable::first_entry(const Key) const { return nullptr; }

std::string UCI::square(Square s) {
    return std::string{char('a' + file_of(s)), char('1' + rank_of(s))};
}

namespace {

std::string move_string(Move move) {
    const auto square = [](Square s) {
        std::string text = "a1";
        text[0] += char(file_of(s));
        text[1] += char(rank_of(s));
        return text;
    };

    std::string text = square(move.from_sq()) + square(move.to_sq());
    if (move.type_of() == PROMOTION)
        text += " pnbrqk"[move.promotion_type()];
    return text;
}

void print_root_result(const char* label, bool ok, const Search::RootMoves& moves) {
    std::cout << label << " ok=" << int(ok);
    for (const auto& move : moves)
        std::cout << ' ' << move_string(move.pv[0]) << ':' << move.tbRank << ':'
                  << int(move.tbScore);
    std::cout << '\n';
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc != 3)
    {
        std::cerr << "usage: atomic-syzygy-driver TABLE_PATH FEN\n";
        return EXIT_FAILURE;
    }

    Bitboards::init();
    Attacks::init();
    Position::init();
    Tablebases::init(argv[1]);

    StateInfo state;
    Position  pos;
    if (const auto error = pos.set(argv[2], false, &state))
    {
        std::cerr << error->what() << '\n';
        return EXIT_FAILURE;
    }

    Tablebases::ProbeState wdlState;
    const auto             wdl = Tablebases::probe_wdl(pos, &wdlState);
    Tablebases::ProbeState dtzState;
    const int              dtz = Tablebases::probe_dtz(pos, &dtzState);

    std::cout << "probe wdl=" << int(wdl) << " wdl_state=" << int(wdlState)
              << " dtz=" << dtz << " dtz_state=" << int(dtzState) << '\n';

    Search::RootMoves roots;
    for (const Move move : MoveList<LEGAL>(pos))
    {
        auto& root   = roots.emplace_back(move);
        root.tbScore = VALUE_DRAW;
    }

    auto withoutRule50 = roots;
    print_root_result("root_no_rule50",
                      Tablebases::root_probe(pos, withoutRule50, false, true, [] { return false; }),
                      withoutRule50);

    auto withRule50 = roots;
    print_root_result("root_rule50",
                      Tablebases::root_probe(pos, withRule50, true, true, [] { return false; }),
                      withRule50);

    auto wdlRoots = roots;
    print_root_result("root_wdl", Tablebases::root_probe_wdl(pos, wdlRoots, true), wdlRoots);

    OptionsMap options;
    options.add("Syzygy50MoveRule", Option(true));
    options.add("SyzygyProbeDepth", Option(1, 0, 100));
    options.add("SyzygyProbeLimit", Option(6, 0, 6));

    auto rankedRoots = roots;
    const auto config = Tablebases::rank_root_moves(options, pos, rankedRoots, true);
    std::cout << "rank_root root_in_tb=" << int(config.rootInTB)
              << " cardinality=" << config.cardinality
              << " use_rule50=" << int(config.useRule50)
              << " probe_depth=" << config.probeDepth << '\n';

    return EXIT_SUCCESS;
}
