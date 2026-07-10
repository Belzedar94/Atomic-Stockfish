/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include "benchmark.h"
#include "numa.h"
#include "misc.h"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <vector>

namespace {

const std::vector<std::string> AtomicDefaults = {
  "setoption name UCI_Chess960 value false",
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "rn2kb1r/1pp1p2p/p2q1pp1/3P4/2P3b1/4PN2/PP3PPP/R2QKB1R b KQkq - 0 1",
  "rn1qkb1r/p5pp/2p5/3p4/N3P3/5P2/PPP4P/R1BQK3 w Qkq - 0 1",
  "r4b1r/2kb1N2/p2Bpnp1/8/2Pp3p/1P1PPP2/P5PP/R3K2R b KQ - 0 1",
  "4K1R1/RR6/8/8/8/8/qqq3p1/7k b - - 0 1",
  "7k/8/8/3pP3/8/8/8/K7 w - d6 0 1",
  "6kr/6P1/8/8/8/8/8/K7 w - - 0 1",
  "6rk/8/8/8/8/8/8/K5R1 w - - 0 1",
  "5BBB/8/8/8/8/8/6k1/7K w - - 0 1",
  "8/8/8/8/8/8/NkN5/1K6 w - - 0 1",
  "setoption name UCI_Chess960 value true",
  "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w HFhf - 0 1",
  "Rr2k1rR/3K4/3p4/8/8/8/7P/8 w kq - 0 1",
  "1R4kr/4K3/8/8/8/8/8/8 b k - 0 1",
  "setoption name UCI_Chess960 value false"
};

const std::vector<std::vector<std::string>> AtomicBenchmarkPositions = {
  {
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
    "rn2kb1r/1pp1p2p/p2q1pp1/3P4/2P3b1/4PN2/PP3PPP/R2QKB1R b KQkq - 0 1",
    "rn1qkb1r/p5pp/2p5/3p4/N3P3/5P2/PPP4P/R1BQK3 w Qkq - 0 1",
    "r4b1r/2kb1N2/p2Bpnp1/8/2Pp3p/1P1PPP2/P5PP/R3K2R b KQ - 0 1"
  },
  {
    "4K1R1/RR6/8/8/8/8/qqq3p1/7k b - - 0 1",
    "7k/8/8/3pP3/8/8/8/K7 w - d6 0 1",
    "6kr/6P1/8/8/8/8/8/K7 w - - 0 1",
    "6rk/8/8/8/8/8/8/K5R1 w - - 0 1",
    "5BBB/8/8/8/8/8/6k1/7K w - - 0 1",
    "8/8/8/8/8/8/NkN5/1K6 w - - 0 1"
  }
};

}  // namespace

namespace Stockfish::Benchmark {

// Builds a list of UCI commands to be run by bench. There
// are five parameters: TT size in MB, number of search threads that
// should be used, the limit value spent for each position, a file name
// where to look for positions in FEN format, and the type of the limit:
// depth, perft, nodes and movetime (in milliseconds). Examples:
//
// bench                            : search default positions up to depth 13
// bench 64 1 15                    : search default positions up to depth 15 (TT = 64MB)
// bench 64 1 100000 default nodes  : search default positions for 100K nodes each
// bench 64 4 5000 current movetime : search current position with 4 threads for 5 sec
// bench 16 1 5 blah perft          : run a perft 5 on positions in file "blah"
std::vector<std::string> setup_bench(const std::string& currentFen, std::istream& is) {

    std::vector<std::string> fens, list;
    std::string              go, token;

    // Assign default values to missing arguments
    std::string ttSize    = (is >> token) ? token : "16";
    std::string threads   = (is >> token) ? token : "1";
    std::string limit     = (is >> token) ? token : "13";
    std::string fenFile   = (is >> token) ? token : "default";
    std::string limitType = (is >> token) ? token : "depth";

    go = limitType == "eval" ? "eval" : "go " + limitType + " " + limit;

    if (fenFile == "default")
        fens = AtomicDefaults;

    else if (fenFile == "current")
        fens.push_back(currentFen);

    else
    {
        std::string   fen;
        std::ifstream file(fenFile);

        if (!file.is_open())
        {
            std::cerr << "Unable to open file " << fenFile << std::endl;
            exit(EXIT_FAILURE);
        }

        while (getline(file, fen))
            if (!fen.empty())
                fens.push_back(fen);

        file.close();
    }

    list.emplace_back("setoption name Threads value " + threads);
    list.emplace_back("setoption name Hash value " + ttSize);
    list.emplace_back("ucinewgame");

    for (const std::string& fen : fens)
        if (fen.find("setoption") != std::string::npos)
            list.emplace_back(fen);
        else
        {
            list.emplace_back("position fen " + fen);
            list.emplace_back(go);
        }

    return list;
}

BenchmarkSetup setup_benchmark(std::istream& is) {
    // TT_SIZE_PER_THREAD is chosen such that roughly half of the hash is used all positions
    // for the current sequence have been searched.
    static constexpr int TT_SIZE_PER_THREAD = 128;

    static constexpr int DEFAULT_DURATION_S = 150;

    BenchmarkSetup setup{};

    // Assign default values to missing arguments
    int desiredTimeS;

    if (!(is >> setup.threads))
        setup.threads = int(get_hardware_concurrency());
    else
        setup.originalInvocation += std::to_string(setup.threads);

    if (!(is >> setup.ttSize))
        setup.ttSize = TT_SIZE_PER_THREAD * setup.threads;
    else
        setup.originalInvocation += " " + std::to_string(setup.ttSize);

    if (!(is >> desiredTimeS))
        desiredTimeS = DEFAULT_DURATION_S;
    else
        setup.originalInvocation += " " + std::to_string(desiredTimeS);

    setup.filledInvocation += std::to_string(setup.threads) + " " + std::to_string(setup.ttSize)
                            + " " + std::to_string(desiredTimeS);

    auto getCorrectedTime = [&](int ply) {
        // time per move is fit roughly based on LTC games
        // seconds = 50/{ply+15}
        // ms = 50000/{ply+15}
        // with this fit 10th move gets 2000ms
        // adjust for desired 10th move time
        return 50000.0 / (static_cast<double>(ply) + 15.0);
    };

    float totalTime = 0;
    for (const auto& game : AtomicBenchmarkPositions)
        for (usize i = 0; i < game.size(); ++i)
            totalTime += float(getCorrectedTime(i + 1));

    float timeScaleFactor = static_cast<float>(desiredTimeS * 1000) / totalTime;

    for (const auto& game : AtomicBenchmarkPositions)
    {
        setup.commands.emplace_back("ucinewgame");
        int ply = 1;
        for (const std::string& fen : game)
        {
            setup.commands.emplace_back("position fen " + fen);
            const int correctedTime = static_cast<int>(getCorrectedTime(ply++) * timeScaleFactor);
            setup.commands.emplace_back("go movetime " + std::to_string(correctedTime));
        }
    }

    return setup;
}

}  // namespace Stockfish
