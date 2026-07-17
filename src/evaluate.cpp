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

#include "evaluate.h"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>

#include "misc.h"
#include "nnue/nnue_dispatcher.h"
#include "nnue/nnue_misc.h"
#include "position.h"
#include "types.h"
#include "nnue/nnue_accumulator.h"

namespace Stockfish {

namespace {

constexpr Value CorneredBishop         = Value(50);
constexpr Value LegacyAtomicRoyalValue = Value(700);

Value classical_atomic(const Position& pos) {
    int material = PawnValue * (pos.count<PAWN>(WHITE) - pos.count<PAWN>(BLACK))
                 + KnightValue * (pos.count<KNIGHT>(WHITE) - pos.count<KNIGHT>(BLACK))
                 + BishopValue * (pos.count<BISHOP>(WHITE) - pos.count<BISHOP>(BLACK))
                 + RookValue * (pos.count<ROOK>(WHITE) - pos.count<ROOK>(BLACK))
                 + QueenValue * (pos.count<QUEEN>(WHITE) - pos.count<QUEEN>(BLACK));

    return pos.side_to_move() == WHITE ? Value(material) : Value(-material);
}

// Fisher Random correction used by the legacy Fairy evaluator. Atomic960 is a
// public mode, so preserving this small cornered-bishop term is part of V1
// evaluation compatibility.
Value fix_frc(const Position& pos) {
    constexpr Bitboard Corners =
      square_bb(SQ_A1) | square_bb(SQ_H1) | square_bb(SQ_A8) | square_bb(SQ_H8);

    if (!(pos.pieces(BISHOP) & Corners))
        return VALUE_ZERO;

    int correction = 0;

    if (pos.piece_on(SQ_A1) == W_BISHOP && pos.piece_on(SQ_B2) == W_PAWN)
        correction += !pos.empty(SQ_B3) ? -CorneredBishop * 4 : -CorneredBishop * 3;
    if (pos.piece_on(SQ_H1) == W_BISHOP && pos.piece_on(SQ_G2) == W_PAWN)
        correction += !pos.empty(SQ_G3) ? -CorneredBishop * 4 : -CorneredBishop * 3;
    if (pos.piece_on(SQ_A8) == B_BISHOP && pos.piece_on(SQ_B7) == B_PAWN)
        correction += !pos.empty(SQ_B6) ? CorneredBishop * 4 : CorneredBishop * 3;
    if (pos.piece_on(SQ_H8) == B_BISHOP && pos.piece_on(SQ_G7) == B_PAWN)
        correction += !pos.empty(SQ_G6) ? CorneredBishop * 4 : CorneredBishop * 3;

    return pos.side_to_move() == WHITE ? Value(correction) : Value(-correction);
}

Value damp_for_atomic_rule50(Value value, const Position& pos) {
    // Fairy Atomic uses nMoveRule=50, i.e. a draw at 100 reversible plies.
    // Imported or composed FENs may legally carry a larger halfmove clock.
    // Once the draw boundary is reached the evaluation stays neutral; it must
    // never cross through zero and reverse sign.
    const int remaining = std::max(0, 100 - pos.rule50_count());
    return Value(int(value) * remaining / 100);
}

}  // namespace

Value Eval::Detail::atomic_nnue_value_from_scaled(i64 scaled) noexcept {
    constexpr i64 Minimum = i64(VALUE_TB_LOSS_IN_MAX_PLY) + 1;
    constexpr i64 Maximum = i64(VALUE_TB_WIN_IN_MAX_PLY) - 1;
    return Value(std::clamp(scaled, Minimum, Maximum));
}

Value Eval::Detail::atomic_nnue_value_from_raw(i32 rawPsqt, i32 rawPositional) noexcept {
    // V3's authenticated numeric domain permits the complete i32 range for
    // each raw component. Sum in i64, then enter the ordinary evaluation
    // domain before Chess960/rule-50 corrections so neither the addition nor
    // damp_for_atomic_rule50() can overflow.
    const i64 scaled = (i64(rawPsqt) + i64(rawPositional)) / 16;
    return atomic_nnue_value_from_scaled(scaled);
}

// Evaluate is the evaluator for the outer world. It returns a static evaluation
// of the position from the point of view of the side to move.
Value Eval::evaluate(const Eval::NNUE::AnyNetwork& network,
                     const Position&               pos,
                     Eval::NNUE::AnyAccumulator&   accumulator,
                     int                           optimism,
                     UseNNUEMode                   mode) {

    if (!pos.has_king(pos.side_to_move()))
        return -VALUE_MATE;
    if (!pos.has_king(~pos.side_to_move()))
        return VALUE_MATE;

    assert(!pos.checkers());

    // Modern Stockfish optimism belongs to its current orthodox net/search
    // calibration and must not leak into the legacy Atomic V1 contract.
    (void) optimism;

    Value v;

    if (mode == UseNNUEMode::False)
        v = damp_for_atomic_rule50(classical_atomic(pos), pos);
    else
    {
        const auto [rawPsqt, rawPositional] = network.evaluate_raw(pos, accumulator);

        if (mode == UseNNUEMode::Pure)
            // Pure is the raw selected network result: no compatibility
            // scaling, Chess960 correction, or fifty-move damping. It remains
            // a data-generation-only mode.
            v = Detail::atomic_nnue_value_from_raw(rawPsqt, rawPositional);
        else if (network.backend() == NNUE::NetworkBackend::AtomicNNUEV2
                 || network.backend() == NNUE::NetworkBackend::AtomicNNUEV3)
        {
            // Modern Atomic networks are trained directly in Atomic engine
            // units. The Legacy COMMONER material proxy, entertainment blend,
            // and V1 calibration scale must never leak into these backends.
            v = Detail::atomic_nnue_value_from_raw(rawPsqt, rawPositional);

            if (pos.is_chess960())
                v += fix_frc(pos);

            v = damp_for_atomic_rule50(v, pos);
        }
        else
        {
            const int deltaNpm =
              std::abs(int(pos.non_pawn_material(WHITE) - pos.non_pawn_material(BLACK)));
            const int entertainment = deltaNpm <= BishopValue - KnightValue ? 7 : 0;
            const int blendedRaw =
              ((128 - entertainment) * rawPsqt + (128 + entertainment) * rawPositional) / 128;

            // Fairy's Atomic net was trained with its royal piece represented
            // as COMMONER, whose middlegame value (700) contributed to NPM.
            // Keep KING zero-valued in the specialized search, but restore that
            // historical material proxy locally for Legacy Atomic V1 scaling.
            const int legacyNpm =
              int(pos.non_pawn_material()) + LegacyAtomicRoyalValue * pos.count<KING>();
            const int scale = 903 + 32 * pos.count<PAWN>() + 32 * legacyNpm / 1024;
            v               = Value((blendedRaw / 16) * scale / 1024);

            if (pos.is_chess960())
                v += fix_frc(pos);

            v = damp_for_atomic_rule50(v, pos);
        }
    }

    // Guarantee evaluation does not hit the tablebase range
    v = std::clamp(v, VALUE_TB_LOSS_IN_MAX_PLY + 1, VALUE_TB_WIN_IN_MAX_PLY - 1);

    return v;
}

// Like evaluate(), but instead of returning a value, it returns
// a string (suitable for outputting to stdout) that contains the detailed
// descriptions and values of each evaluation term. Useful for debugging.
// Trace scores are from white's point of view
std::string Eval::trace(Position& pos, const Eval::NNUE::AnyNetwork& network, UseNNUEMode mode) {

    if (pos.is_atomic_terminal())
        return "Final evaluation: none (Atomic terminal)";

    if (pos.checkers())
        return "Final evaluation: none (in check)";

    auto accumulator = std::make_unique<Eval::NNUE::AnyAccumulator>(network);

    std::stringstream ss;
    ss << std::showpoint << std::noshowpos << std::fixed << std::setprecision(2);
    if (mode != UseNNUEMode::False)
        ss << '\n' << NNUE::trace(pos, network, *accumulator) << '\n';

    ss << std::showpoint << std::showpos << std::fixed << std::setprecision(2) << std::setw(15);

    Value v;
    if (mode != UseNNUEMode::False)
    {
        auto [rawPsqt, rawPositional] = network.evaluate_raw(pos, *accumulator);
        v                             = Detail::atomic_nnue_value_from_raw(rawPsqt, rawPositional);
        ss << "NNUE evaluation          " << v << " (side to move, internal units)\n";
        v = pos.side_to_move() == WHITE ? v : -v;
        ss << "NNUE evaluation        " << double(v) / PawnValue << " (white side)\n";
    }

    v = evaluate(network, pos, *accumulator, VALUE_ZERO, mode);
    v = pos.side_to_move() == WHITE ? v : -v;

    ss << "Final evaluation      ";
    ss << double(v) / PawnValue << " (white side)";
    ss << " [Use NNUE="
       << (mode == UseNNUEMode::False  ? "false"
           : mode == UseNNUEMode::Pure ? "pure"
                                       : "true")
       << "]\n";

    return ss.str();
}

}  // namespace Stockfish
