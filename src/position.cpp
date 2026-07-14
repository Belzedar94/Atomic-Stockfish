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

#include "position.h"

#include <algorithm>
#include <array>
#include <cassert>
#include <cctype>
#include <cstddef>
#include <cstring>
#include <initializer_list>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string_view>
#include <utility>

#include "bitboard.h"
#include "history.h"
#include "misc.h"
#include "movegen.h"
#include "syzygy/tbprobe.h"
#include "tt.h"
#include "uci_move.h"

using std::string;

namespace Stockfish {

using namespace Attacks;

namespace Zobrist {

Key psq[PIECE_NB][SQUARE_NB];
Key enpassant[FILE_NB];
Key castling[CASTLING_RIGHT_NB];
Key side, noPawns;

}

namespace {

constexpr std::string_view PieceToChar(" PNBRQK  pnbrqk");

static constexpr Piece Pieces[] = {W_PAWN, W_KNIGHT, W_BISHOP, W_ROOK, W_QUEEN, W_KING,
                                   B_PAWN, B_KNIGHT, B_BISHOP, B_ROOK, B_QUEEN, B_KING};
}  // namespace


// Returns an ASCII representation of the position
std::ostream& operator<<(std::ostream& os, const Position& pos) {

    os << "\n +---+---+---+---+---+---+---+---+\n";

    for (Rank r = RANK_8;; --r)
    {
        for (File f = FILE_A; f <= FILE_H; ++f)
            os << " | " << PieceToChar[pos.piece_on(make_square(f, r))];

        os << " | " << (1 + r) << "\n +---+---+---+---+---+---+---+---+\n";

        if (r == RANK_1)
            break;
    }

    os << "   a   b   c   d   e   f   g   h\n"
       << "\nFen: " << pos.fen() << "\nKey: " << std::hex << std::uppercase << std::setfill('0')
       << std::setw(16) << pos.key() << std::setfill(' ') << std::dec << "\nCheckers: ";

    for (Bitboard b = pos.checkers(); b;)
        os << UCI::square(pop_lsb(b)) << " ";

    if (Tablebases::MaxCardinality >= popcount(pos.pieces()) && !pos.can_castle(ANY_CASTLING))
    {
        StateInfo st;

        Position p;
        p.set(pos.fen(), pos.is_chess960(), &st);
        Tablebases::ProbeState s1, s2;
        Tablebases::WDLScore   wdl = Tablebases::probe_wdl(p, &s1);
        int                    dtz = Tablebases::probe_dtz(p, &s2);
        os << "\nTablebases WDL: " << std::setw(4) << wdl << " (" << s1 << ")"
           << "\nTablebases DTZ: " << std::setw(4) << dtz << " (" << s2 << ")";
    }

    return os;
}


// Implements Marcel van Kervinck's cuckoo algorithm to detect repetition of positions
// for 3-fold repetition draws. The algorithm uses two hash tables with Zobrist hashes
// to allow fast detection of recurring positions. For details see:
// http://web.archive.org/web/20201107002606/https://marcelk.net/2013-04-06/paper/upcoming-rep-v2.pdf

// First and second hash functions for indexing the cuckoo tables
inline int H1(Key h) { return h & 0x1fff; }
inline int H2(Key h) { return (h >> 16) & 0x1fff; }

// Cuckoo tables with Zobrist hashes of valid reversible moves, and the moves themselves
std::array<Key, 8192>  cuckoo;
std::array<Move, 8192> cuckooMove;

// Initializes at startup the various arrays used to compute hash keys
void Position::init() {

    PRNG rng(1070372);

    for (Piece pc : Pieces)
        for (Square s = SQ_A1; s <= SQ_H8; ++s)
            Zobrist::psq[pc][s] = rng.rand<Key>();
    // pawns on these squares will promote
    std::fill_n(Zobrist::psq[W_PAWN] + SQ_A8, 8, 0);
    std::fill_n(Zobrist::psq[B_PAWN], 8, 0);

    for (File f = FILE_A; f <= FILE_H; ++f)
        Zobrist::enpassant[f] = rng.rand<Key>();

    for (int cr = NO_CASTLING; cr <= ANY_CASTLING; ++cr)
        Zobrist::castling[cr] = rng.rand<Key>();

    Zobrist::side    = rng.rand<Key>();
    Zobrist::noPawns = rng.rand<Key>();

    // Prepare the cuckoo tables
    cuckoo.fill(0);
    cuckooMove.fill(Move::none());
    [[maybe_unused]] int count = 0;
    for (Piece pc : Pieces)
        for (Square s1 = SQ_A1; s1 <= SQ_H8; ++s1)
            for (Square s2 = Square(s1 + 1); s2 <= SQ_H8; ++s2)
                if ((type_of(pc) != PAWN) && (attacks_bb(type_of(pc), s1, 0) & s2))
                {
                    Move move = Move(s1, s2);
                    Key  key  = Zobrist::psq[pc][s1] ^ Zobrist::psq[pc][s2] ^ Zobrist::side;
                    int  i    = H1(key);
                    while (true)
                    {
                        std::swap(cuckoo[i], key);
                        std::swap(cuckooMove[i], move);
                        if (move == Move::none())  // Arrived at empty slot?
                            break;
                        i = (i == H1(key)) ? H2(key) : H1(key);  // Push victim to alternative slot
                    }
                    count++;
                }
    assert(count == 3668);
}


// Initializes the position object with the given FEN string.
// The FEN string is strictly validated; if it is invalid or inconsistent,
// a PositionSetError describing the problem is returned, otherwise std::nullopt.
std::optional<PositionSetError>
Position::set(const string& fenStr, bool isChess960, StateInfo* si) {
    /*
   A FEN string defines a particular position using only the ASCII character set.

   A FEN string contains six fields separated by a space. The fields are:

   1) Piece placement (from white's perspective). Each rank is described, starting
      with rank 8 and ending with rank 1. Within each rank, the contents of each
      square are described from file A through file H. Following the Standard
      Algebraic Notation (SAN), each piece is identified by a single letter taken
      from the standard English names. White pieces are designated using upper-case
      letters ("PNBRQK") whilst Black uses lowercase ("pnbrqk"). Blank squares are
      noted using digits 1 through 8 (the number of blank squares), and "/"
      separates ranks.

   2) Active color. "w" means white moves next, "b" means black.

   3) Castling availability. If neither side can castle, this is "-". Otherwise,
      this has one or more letters: "K" (White can castle kingside), "Q" (White
      can castle queenside), "k" (Black can castle kingside), and/or "q" (Black
      can castle queenside).

   4) En passant target square (in algebraic notation). If there's no en passant
      target square, this is "-". If a pawn has just made a 2-square move, this
      is the position "behind" the pawn. Following X-FEN standard, this is recorded
      only if there is a pawn in position to make an en passant capture, and if
      there really is a pawn that might have advanced two squares.

   5) Halfmove clock. This is the number of halfmoves since the last pawn advance
      or capture. This is used to determine if a draw can be claimed under the
      fifty-move rule.

   6) Fullmove number. The number of the full move. It starts at 1, and is
      incremented after Black's move.
*/

    unsigned char      token;
    std::istringstream ss(fenStr);

    std::memset(reinterpret_cast<char*>(this), 0, sizeof(Position));
    std::memset(si, 0, sizeof(StateInfo));
    st = si;

    ss >> std::noskipws;

    int numPieces = 0;
    int file      = FILE_A;
    int rank      = RANK_8;

    // 1. Piece placement
    for (;;)
    {
        if (!(ss >> token))
            return PositionSetError("Invalid FEN. Unexpected end of stream.");

        if (isspace(token))
            break;

        if (isdigit(token))
        {
            const int diff = (token - '0');
            if (diff < 1 || diff > 8)
                return PositionSetError("Invalid FEN. Invalid number of squares to skip.");

            file += diff;
            if (file > FILE_NB)
                return PositionSetError("Invalid FEN. Invalid file reached.");
        }
        else if (token == '/')
        {
            if (file != FILE_NB)
                return PositionSetError(
                  "Invalid FEN. Trying to end rank when not at the end of it.");

            --rank;
            file = FILE_A;

            if (rank < RANK_1)
                return PositionSetError("Invalid FEN. Invalid rank reached.");
        }
        else
        {
            if (file >= FILE_NB)
                return PositionSetError("Invalid FEN. Invalid file reached.");

            const usize idx = PieceToChar.find(token);
            if (idx == string::npos)
                return PositionSetError(std::string("Invalid FEN. Invalid piece: ")
                                        + std::string(1, token));

            if (++numPieces > 32)
                return PositionSetError("Invalid FEN. More than 32 pieces on the board.");

            const Square sq = make_square(File(file), Rank(rank));
            put_piece(Piece(idx), sq);

            ++file;
        }
    }

    if (rank != RANK_1 || file != FILE_NB)
        return PositionSetError("Invalid FEN. Board state encoding ended but cursor not at end.");

    if (pieces(PAWN) & (Rank1BB | Rank8BB))
        return PositionSetError("Unsupported position. Pawns on the first or eighth rank.");

    if (count<KING>(WHITE) > 1 || count<KING>(BLACK) > 1
        || (count<KING>(WHITE) == 0 && count<KING>(BLACK) == 0))
        return PositionSetError("Unsupported Atomic position. Incorrect number of kings.");

    for (Color c : {WHITE, BLACK})
    {
        if (count<PAWN>(c) > 8)
            return PositionSetError(std::string("Unsupported position. ")
                                    + (c == WHITE ? "WHITE" : "BLACK") + " has more than 8 pawns.");

        int additional = std::max(count<KNIGHT>(c) - 2, 0) + std::max(count<BISHOP>(c) - 2, 0)
                       + std::max(count<ROOK>(c) - 2, 0) + std::max(count<QUEEN>(c) - 1, 0);
        if (additional > 8 - count<PAWN>(c))
            return PositionSetError(std::string("Unsupported position. Too many pieces for ")
                                    + (c == WHITE ? "WHITE." : "BLACK."));
    }

    // 2. Active color
    if (!(ss >> token))
        return PositionSetError("Invalid FEN. Unexpected end of stream.");
    if (token != 'w' && token != 'b')
        return PositionSetError(std::string("Invalid FEN. Invalid side to move: ")
                                + std::string(1, token));
    sideToMove = (token == 'w' ? WHITE : BLACK);
    if (!(ss >> token) || !isspace(token) || ss.eof())
        return PositionSetError("Invalid FEN. Expected whitespace after side to move.");

    // 3. Castling availability. Compatible with 3 standards: Normal FEN standard,
    // Shredder-FEN that uses the letters of the columns on which the rooks began
    // the game instead of KQkq and also X-FEN standard that, in case of Chess960,
    // if an inner rook is associated with the castling right, the castling tag is
    // replaced by the file letter of the involved rook, as for the Shredder-FEN.
    //
    // NOTE: Due to the prevalnce of incorrect (or missing) castling rights the
    // validation is less strict. However, incorrect castling rights are still sanitized.
    int num_castling_rights = 0;
    for (;;)
    {
        if (!(ss >> token))
            break;

        if (isspace(token))
            break;

        if (num_castling_rights == 0 && token == '-')
        {
            ss >> std::ws;
            break;
        }

        if (++num_castling_rights > 4)
            return PositionSetError("Invalid FEN. Maximum of 4 castling rights can be specified.");

        Square rsq  = SQ_NONE;
        Square ksq  = SQ_NONE;
        Color  c    = islower(token) ? BLACK : WHITE;
        Piece  rook = make_piece(c, ROOK);
        Piece  king = make_piece(c, KING);

        token = char(toupper(token));

        if (token == 'K' || token == 'Q')
        {
            const int dir = token == 'K' ? -1 : 1;
            Square    sq  = relative_square(c, token == 'K' ? SQ_H1 : SQ_A1);
            // Look for a rook and a king for the castling. King must come later.
            // Only the first rook is noted.
            // If the castling rights are available the king must always be between files 2 and 7 inclusive
            // so there is no need to check the last square.
            for (int i = 0; i < 7; ++i, sq = Square(sq + dir))
            {
                const Piece pc = piece_on(sq);
                if (pc == king)
                {
                    ksq = sq;
                    break;
                }
                else if (pc == rook && rsq == SQ_NONE)
                {
                    rsq = sq;
                }
            }
        }
        else if (token >= 'A' && token <= 'H')
        {
            const Square rsqCandidate = make_square(File(token - 'A'), relative_rank(c, RANK_1));
            if (piece_on(rsqCandidate) == rook)
                rsq = rsqCandidate;

            // If the castling rights are available the king must always be between files 2 and 7 inclusive.
            Square sq = relative_square(c, SQ_B1);
            for (int i = 0; i < 6; ++i, ++sq)
            {
                if (piece_on(sq) == king)
                    ksq = sq;
            }
        }
        else
        {
            return PositionSetError(std::string("Invalid FEN. Expected castling rights. Got: ")
                                    + std::string(1, token));
        }

        // Only apply castling rights if they can be valid.
        if (ksq != SQ_NONE && rsq != SQ_NONE)
            set_castling_right(c, rsq);
    }

    // 4. En passant square.
    // Ignore if square is invalid or not on side to move relative rank 6.
    bool          enpassant = false;
    unsigned char col       = '-', row;
    ss >> col;
    if (col != '-')
    {
        if (!(ss >> row))
            return PositionSetError("Invalid FEN. Unexpected end of stream.");

        if ((col >= 'a' && col <= 'h') && (row == (sideToMove == WHITE ? '6' : '3')))
        {
            st->epSquare = make_square(File(col - 'a'), Rank(row - '1'));

            Bitboard pawns = attacks_bb<PAWN>(st->epSquare, ~sideToMove) & pieces(sideToMove, PAWN);
            Bitboard target = (pieces(~sideToMove, PAWN) & (st->epSquare + pawn_push(~sideToMove)));

            // En passant square will be considered only if
            // a) side to move have a pawn threatening epSquare
            // b) there is an enemy pawn in front of epSquare
            // c) there is no piece on epSquare or behind epSquare
            enpassant = pawns && target
                     && !(pieces() & (st->epSquare | (st->epSquare + pawn_push(sideToMove))));
        }
        else
            return PositionSetError("Invalid FEN. Invalid en-passant square.");
    }

    if (!enpassant)
        st->epSquare = SQ_NONE;

    // 5-6. Halfmove clock and fullmove number
    ss >> std::skipws >> st->rule50 >> gamePly;

    // Normally values larger than 99 would be pointless but we do support ignoring 50 move rule for TB purposes.
    // Limit at 2**15 as it's used multiplicativly with position evaluation during search.
    if (st->rule50 < 0 || st->rule50 > 32767)
        return PositionSetError("Unsupported position. Rule50 counter out of range.");

    if (gamePly < 0 || gamePly > 100000)
        return PositionSetError("Unsupported position. Game ply out of range.");

    // Convert from fullmove starting from 1 to gamePly starting from 0,
    // handle also common incorrect FEN with fullmove = 0.
    gamePly = std::max(2 * (gamePly - 1), 0) + (sideToMove == BLACK);

    chess960 = isChess960;
    set_state();
    st->atomicOpponentInCheck = atomic_in_check(~sideToMove);

    assert(pos_is_ok());

    return std::nullopt;
}


// Helper function used to set castling
// rights given the corresponding color and the rook starting square.
void Position::set_castling_right(Color c, Square rfrom) {

    Square         kfrom = square<KING>(c);
    CastlingRights cr    = c & (kfrom < rfrom ? KING_SIDE : QUEEN_SIDE);

    st->castlingRights |= cr;
    castlingRightsMask[kfrom] |= cr;
    castlingRightsMask[rfrom] |= cr;
    castlingRookSquare[cr] = rfrom;

    Square kto = relative_square(c, cr & KING_SIDE ? SQ_G1 : SQ_C1);
    Square rto = relative_square(c, cr & KING_SIDE ? SQ_F1 : SQ_D1);

    castlingPath[cr] = (between_bb(rfrom, rto) | between_bb(kfrom, kto)) & ~(kfrom | rfrom);
}


// Sets king attacks to detect if a move gives check
void Position::set_check_info() const {

    update_slider_blockers(WHITE);
    update_slider_blockers(BLACK);

    st->checkSquares.fill(0);

    // Atomic legality is checked against the complete post-move position.
    // The orthodox check-square shortcut cannot model explosions or adjacent kings.
    if (!has_king(~sideToMove))
        return;

    Square ksq = square<KING>(~sideToMove);

    st->checkSquares[StateInfo::check_square_index(PAWN)]   = attacks_bb<PAWN>(ksq, ~sideToMove);
    st->checkSquares[StateInfo::check_square_index(KNIGHT)] = attacks_bb<KNIGHT>(ksq);
    st->checkSquares[StateInfo::check_square_index(BISHOP)] = attacks_bb<BISHOP>(ksq, pieces());
    st->checkSquares[StateInfo::check_square_index(ROOK)]   = attacks_bb<ROOK>(ksq, pieces());
    st->checkSquares[StateInfo::check_square_index(QUEEN)] =
      st->checkSquares[StateInfo::check_square_index(BISHOP)]
      | st->checkSquares[StateInfo::check_square_index(ROOK)];
}


// Computes the hash keys of the position, and other
// data that once computed is updated incrementally as moves are made.
// The function is only used when a new position is set up
void Position::set_state() const {

    st->key               = 0;
    st->minorPieceKey     = 0;
    st->nonPawnKey[WHITE] = st->nonPawnKey[BLACK] = 0;
    st->pawnKey                                   = Zobrist::noPawns;
    st->nonPawnMaterial[WHITE] = st->nonPawnMaterial[BLACK] = VALUE_ZERO;
    set_check_info();

    for (Bitboard b = pieces(); b;)
    {
        Square s  = pop_lsb(b);
        Piece  pc = piece_on(s);
        st->key ^= Zobrist::psq[pc][s];

        if (type_of(pc) == PAWN)
            st->pawnKey ^= Zobrist::psq[pc][s];

        else
        {
            st->nonPawnKey[color_of(pc)] ^= Zobrist::psq[pc][s];

            if (type_of(pc) != KING)
            {
                st->nonPawnMaterial[color_of(pc)] += PieceValue[pc];

                if (type_of(pc) <= BISHOP)
                    st->minorPieceKey ^= Zobrist::psq[pc][s];
            }
        }
    }

    if (st->epSquare != SQ_NONE)
        st->key ^= Zobrist::enpassant[file_of(st->epSquare)];

    if (sideToMove == BLACK)
        st->key ^= Zobrist::side;

    st->key ^= Zobrist::castling[st->castlingRights];
    st->materialKey = compute_material_key();
}

Key Position::compute_material_key() const {
    Key k = 0;
    for (Piece pc : Pieces)
        for (int cnt = 0; cnt < pieceCount[pc]; ++cnt)
            k ^= Zobrist::psq[pc][8 + cnt];
    return k;
}


// Overload to initialize the position object with the given endgame code string
// like "KBPKN". It's mainly a helper to get the material key out of an endgame code.
std::optional<PositionSetError> Position::set(const string& code, Color c, StateInfo* si) {

    assert(code[0] == 'K');

    string sides[] = {code.substr(code.find('K', 1)),                                // Weak
                      code.substr(0, std::min(code.find('v'), code.find('K', 1)))};  // Strong

    assert(sides[0].length() > 0 && sides[0].length() < 8);
    assert(sides[1].length() > 0 && sides[1].length() < 8);

    std::transform(sides[c].begin(), sides[c].end(), sides[c].begin(), tolower);

    string fenStr = "8/" + sides[0] + char(8 - sides[0].length() + '0') + "/8/8/8/8/" + sides[1]
                  + char(8 - sides[1].length() + '0') + "/8 w - - 0 10";

    return set(fenStr, false, si);
}


// Returns a FEN representation of the position. In case of
// Chess960 the Shredder-FEN notation is used. This is mainly a debugging function.
string Position::fen() const {

    int                emptyCnt;
    std::ostringstream ss;

    for (Rank r = RANK_8;; --r)
    {
        for (File f = FILE_A; f <= FILE_H; ++f)
        {
            for (emptyCnt = 0; f <= FILE_H && empty(make_square(f, r)); ++f)
                ++emptyCnt;

            if (emptyCnt)
                ss << emptyCnt;

            if (f <= FILE_H)
                ss << PieceToChar[piece_on(make_square(f, r))];
        }

        if (r == RANK_1)
            break;
        ss << '/';
    }

    ss << (sideToMove == WHITE ? " w " : " b ");

    if (can_castle(WHITE_OO))
        ss << (chess960 ? char('A' + file_of(castling_rook_square(WHITE_OO))) : 'K');

    if (can_castle(WHITE_OOO))
        ss << (chess960 ? char('A' + file_of(castling_rook_square(WHITE_OOO))) : 'Q');

    if (can_castle(BLACK_OO))
        ss << (chess960 ? char('a' + file_of(castling_rook_square(BLACK_OO))) : 'k');

    if (can_castle(BLACK_OOO))
        ss << (chess960 ? char('a' + file_of(castling_rook_square(BLACK_OOO))) : 'q');

    if (!can_castle(ANY_CASTLING))
        ss << '-';

    ss << (ep_square() == SQ_NONE ? " - " : " " + UCI::square(ep_square()) + " ") << st->rule50
       << " " << 1 + (gamePly - (sideToMove == BLACK)) / 2;

    return ss.str();
}

// Calculates the pieces preventing the king of color c from being in check.
void Position::update_slider_blockers(Color c) const {

    st->blockersForKing[c] = 0;

    if (!has_king(c))
        return;

    Square ksq = square<KING>(c);

    // Snipers are sliders that attack 's' when a piece and other snipers are removed
    Bitboard snipers = ((attacks_bb<ROOK>(ksq) & pieces(QUEEN, ROOK))
                        | (attacks_bb<BISHOP>(ksq) & pieces(QUEEN, BISHOP)))
                     & pieces(~c);
    Bitboard occupancy = pieces() ^ snipers;

    while (snipers)
    {
        Square   sniperSq = pop_lsb(snipers);
        Bitboard b        = between_bb(ksq, sniperSq) & occupancy;

        if (b && !more_than_one(b))
            st->blockersForKing[c] |= b;
    }
}


// Computes a bitboard of all pieces which attack a given square.
// Slider attacks use the occupied bitboard to indicate occupancy.
Bitboard Position::attackers_to(Square s, Bitboard occupied) const {

    return (attacks_bb<ROOK>(s, occupied) & pieces(ROOK, QUEEN))
         | (attacks_bb<BISHOP>(s, occupied) & pieces(BISHOP, QUEEN))
         | (attacks_bb<PAWN>(s, BLACK) & pieces(WHITE, PAWN))
         | (attacks_bb<PAWN>(s, WHITE) & pieces(BLACK, PAWN))
         | (attacks_bb<KNIGHT>(s) & pieces(KNIGHT)) | (attacks_bb<KING>(s) & pieces(KING));
}

bool Position::attackers_to_exist(Square s, Bitboard occupied, Color c) const {

    return (attacks_bb<ROOK>(s, occupied) & pieces(c, ROOK, QUEEN))
        || (attacks_bb<BISHOP>(s, occupied) & pieces(c, BISHOP, QUEEN))
        || (attacks_bb<PAWN>(s, ~c) & pieces(c, PAWN))
        || (attacks_bb<KNIGHT>(s) & pieces(c, KNIGHT)) || (attacks_bb<KING>(s) & pieces(c, KING));
}

// Tests whether a pseudo-legal move is legal
bool Position::legal(Move m) const {

    assert(m.is_ok());

    Color  us   = sideToMove;
    Color  them = ~us;
    Square from = m.from_sq();
    Square to   = m.to_sq();
    Piece  pc   = moved_piece(m);

    assert(color_of(pc) == us);
    assert(has_king(us) && has_king(them));

    if (m.type_of() == CASTLING)
    {
        const Square    rfrom        = to;
        const Square    kto          = relative_square(us, rfrom > from ? SQ_G1 : SQ_C1);
        const Square    rto          = relative_square(us, rfrom > from ? SQ_F1 : SQ_D1);
        const Direction step         = kto > from ? EAST : WEST;
        const Square    theirKing    = square<KING>(them);
        const Bitboard  pathOccupied = pieces() ^ from;

        if (!(attacks_bb<KING>(from) & theirKing) && (attackers_to(from) & pieces(them)))
            return false;

        // Check the initial and intermediate king squares before moving the
        // rook. The final square is checked against the final castled board,
        // because in Chess960 that rook can block an attack on the king.
        if (from != kto)
            for (Square s = from + step; s != kto; s += step)
                if (!(attacks_bb<KING>(s) & theirKing)
                    && (attackers_to(s, pathOccupied) & pieces(them) & pathOccupied))
                    return false;

        const Bitboard occupied = (pieces() ^ from ^ rfrom) | kto | rto;
        if (attacks_bb<KING>(kto) & theirKing)
            return true;

        return !(attackers_to(kto, occupied) & pieces(them) & occupied);
    }

    Bitboard occupied = (pieces() ^ from) | to;

    if (m.type_of() == EN_PASSANT)
        occupied ^= to - pawn_push(us);

    if (capture(m))
    {
        // The capturing piece and the captured piece always disappear. Pawns
        // on adjacent squares are immune; every adjacent non-pawn explodes.
        const Bitboard blast = to | (attacks_bb<KING>(to) & (pieces() ^ pieces(PAWN)));
        occupied &= ~blast;
    }

    const Square ourKing   = type_of(pc) == KING ? to : square<KING>(us);
    const Square theirKing = square<KING>(them);

    // A capture by the king, or any capture next to our king, is a forbidden
    // self-explosion.
    if (!(occupied & ourKing))
        return false;

    // Exploding the opposing king ends the game immediately.
    if (!(occupied & theirKing))
        return true;

    // Adjacent kings are mutually immune in Atomic chess: capturing either
    // king would explode the attacker as well.
    if (attacks_bb<KING>(ourKing) & theirKing)
        return true;

    return !(attackers_to(ourKing, occupied) & pieces(them) & occupied);
}

bool Position::atomic_in_check(Color c) const {

    if (!has_king(c) || !has_king(~c))
        return false;

    const Square ourKing   = square<KING>(c);
    const Square theirKing = square<KING>(~c);

    if (attacks_bb<KING>(ourKing) & theirKing)
        return false;

    return bool(attackers_to(ourKing) & pieces(~c));
}

bool Position::atomic_wins(Move m) const {

    if (!capture(m) || !has_king(~sideToMove))
        return false;

    const Square blastCenter = m.to_sq();
    const Square theirKing   = square<KING>(~sideToMove);

    return blastCenter == theirKing || bool(attacks_bb<KING>(blastCenter) & theirKing);
}


// Takes a random move and tests whether the move is
// pseudo-legal. It is used to validate moves from TT that can be corrupted
// due to SMP concurrent access or hash position key aliasing.
bool Position::pseudo_legal(const Move m) const {

    Color  us   = sideToMove;
    Square from = m.from_sq();
    Square to   = m.to_sq();
    Piece  pc   = moved_piece(m);

    // Use a slower but simpler function for uncommon cases
    // yet we skip the legality check of MoveList<LEGAL>().
    if (m.type_of() != NORMAL)
        return checkers() ? MoveList<EVASIONS>(*this).contains(m)
                          : MoveList<NON_EVASIONS>(*this).contains(m);

    // Is not a promotion, so the promotion piece must be empty
    assert(m.promotion_type() - KNIGHT == NO_PIECE_TYPE);

    // If the 'from' square is not occupied by a piece belonging to the side to
    // move, the move is obviously not legal.
    if (pc == NO_PIECE || color_of(pc) != us)
        return false;

    // The destination square cannot be occupied by a friendly piece
    if (pieces(us) & to)
        return false;

    // Handle the special case of a pawn move
    if (type_of(pc) == PAWN)
    {
        // We have already handled promotion moves, so destination cannot be on the 8th/1st rank
        if ((Rank8BB | Rank1BB) & to)
            return false;

        // Check if it's a valid capture, single push, or double push
        const bool isCapture    = bool(attacks_bb<PAWN>(from, us) & pieces(~us) & to);
        const bool isSinglePush = (from + pawn_push(us) == to) && empty(to);
        const bool isDoublePush = (from + 2 * pawn_push(us) == to)
                               && (relative_rank(us, from) == RANK_2) && empty(to)
                               && empty(to - pawn_push(us));

        if (!(isCapture || isSinglePush || isDoublePush))
            return false;
    }
    else if (!(attacks_bb(type_of(pc), from, pieces()) & to))
        return false;

    if (checkers())
        return MoveList<EVASIONS>(*this).contains(m);

    return true;
}


// Tests whether a pseudo-legal move gives an Atomic check.
bool Position::gives_check(Move m) const {

    assert(m.is_ok());
    assert(color_of(moved_piece(m)) == sideToMove);

    const Color us   = sideToMove;
    const Color them = ~us;

    if (!has_king(them))
        return false;

    const Square from           = m.from_sq();
    const Square to             = m.to_sq();
    const Piece  pc             = moved_piece(m);
    const Square theirKing      = square<KING>(them);
    const Square currentOurKing = square<KING>(us);
    const bool   kingsAdjacent  = bool(attacks_bb<KING>(currentOurKing) & theirKing);

    // The common case is a normal quiet. Existing check-squares detect its
    // direct checks, and only a blocker move (or a king leaving mutual
    // immunity) needs the full hypothetical-occupancy calculation below.
    if (m.type_of() == NORMAL && !capture(m))
    {
        if (kingsAdjacent)
        {
            if (type_of(pc) != KING || (attacks_bb<KING>(to) & theirKing))
                return false;
        }
        else
        {
            if (type_of(pc) != KING && (check_squares(type_of(pc)) & to))
                return true;
            if (!st->atomicOpponentInCheck && !(blockers_for_king(them) & from))
                return false;
        }
    }

    Bitboard occupied;
    Bitboard vacated = square_bb(from);
    Square   ourKing;
    Square   checkerSquare;
    Piece    checker;

    if (m.type_of() == CASTLING)
    {
        const Square rfrom = to;
        const Square kto   = relative_square(us, rfrom > from ? SQ_G1 : SQ_C1);
        const Square rto   = relative_square(us, rfrom > from ? SQ_F1 : SQ_D1);

        occupied = (pieces() ^ from ^ rfrom) | kto | rto;
        vacated |= rfrom;
        ourKing       = kto;
        checker       = make_piece(us, ROOK);
        checkerSquare = rto;
    }
    else
    {
        occupied = (pieces() ^ from) | to;

        if (m.type_of() == EN_PASSANT)
            occupied ^= to - pawn_push(us);

        ourKing       = type_of(pc) == KING ? to : square<KING>(us);
        checker       = m.type_of() == PROMOTION ? make_piece(us, m.promotion_type()) : pc;
        checkerSquare = to;

        if (capture(m))
        {
            // The capturing piece always disappears. Adjacent pawns survive;
            // every adjacent non-pawn is removed before check is evaluated.
            const Bitboard blast = to | (attacks_bb<KING>(to) & (pieces() ^ pieces(PAWN)));
            occupied &= ~blast;
            checker = NO_PIECE;
        }
    }

    // A missing opposing king is a terminal explosion, handled separately by
    // atomic_wins(). A missing friendly king is an illegal self-explosion.
    if (!(occupied & theirKing) || !(occupied & ourKing))
        return false;

    // Adjacent kings are mutually immune in Atomic chess, including from all
    // other nominal attacks on either king.
    if (attacks_bb<KING>(ourKing) & theirKing)
        return false;

    // Existing pieces can give a discovered check after the move or blast.
    // Mask the moved origins explicitly because Chess960 can reoccupy one of
    // those squares with the other castling piece.
    if (attackers_to(theirKing, occupied) & pieces(us) & occupied & ~vacated)
        return true;

    // The moved piece is absent from the pre-move piece bitboards, so test its
    // final square separately. Atomic captures set checker to NO_PIECE because
    // the capturer has exploded.
    return checker != NO_PIECE && (occupied & checkerSquare)
        && (attacks_bb(checker, checkerSquare, occupied) & theirKing);
}


// Makes a move, and saves all information necessary
// to a StateInfo object. The move is assumed to be legal. Pseudo-legal
// moves should be filtered out before this function is called.
// If a pointer to the TT table is passed, the entry for the new position
// will be prefetched, and likewise for shared history.
void Position::do_move(Move                      m,
                       StateInfo&                newSt,
                       [[maybe_unused]] bool     givesCheck,
                       DirtyPiece&               dp,
                       const TranspositionTable* tt      = nullptr,
                       const SharedHistories*    history = nullptr) {

    assert(m.is_ok());
    assert(&newSt != st);

    Key k = st->key ^ Zobrist::side;

    // Copy some fields of the old state to our new StateInfo object except the
    // ones which are going to be recalculated from scratch anyway and then switch
    // our state pointer to point to the new (ready to be updated) state.
    std::memcpy(&newSt, st, offsetof(StateInfo, key));
    newSt.previous            = st;
    st                        = &newSt;
    st->atomicBlastCount      = 0;
    st->atomicOpponentInCheck = false;

    // Increment ply counters. In particular, rule50 will be reset to zero later on
    // in case of a capture or a pawn move.
    ++gamePly;
    ++st->rule50;
    ++st->pliesFromNull;

    Color  us       = sideToMove;
    Color  them     = ~us;
    Square from     = m.from_sq();
    Square to       = m.to_sq();
    Piece  pc       = piece_on(from);
    Piece  captured = m.type_of() == EN_PASSANT ? make_piece(them, PAWN) : piece_on(to);

    dp.pc              = pc;
    dp.from            = from;
    dp.to              = to;
    dp.add_sq          = SQ_NONE;
    dp.requiresRefresh = false;
    dp.atomicBlast     = {};

    assert(color_of(pc) == us);
    assert(captured == NO_PIECE || color_of(captured) == (m.type_of() != CASTLING ? them : us));

    if (m.type_of() == CASTLING)
    {
        assert(pc == make_piece(us, KING));
        assert(captured == make_piece(us, ROOK));

        Square rfrom, rto;
        do_castling<true>(us, from, to, rfrom, rto, &dp);

        k ^= Zobrist::psq[captured][rfrom] ^ Zobrist::psq[captured][rto];
        st->nonPawnKey[us] ^= Zobrist::psq[captured][rfrom] ^ Zobrist::psq[captured][rto];
        captured = NO_PIECE;
    }
    else if (captured)
    {
        Square capsq = to;

        // If the captured piece is a pawn, update pawn hash key, otherwise
        // update non-pawn material.
        if (type_of(captured) == PAWN)
        {
            if (m.type_of() == EN_PASSANT)
            {
                capsq -= pawn_push(us);

                assert(pc == make_piece(us, PAWN));
                assert(to == st->epSquare);
                assert(relative_rank(us, to) == RANK_6);
                assert(piece_on(to) == NO_PIECE);
                assert(piece_on(capsq) == make_piece(them, PAWN));

                // Update board and piece lists in ep case, normal captures are updated later
                remove_piece(capsq);
            }

            st->pawnKey ^= Zobrist::psq[captured][capsq];
        }
        else
        {
            st->nonPawnMaterial[them] -= PieceValue[captured];
            st->nonPawnKey[them] ^= Zobrist::psq[captured][capsq];

            if (type_of(captured) <= BISHOP)
                st->minorPieceKey ^= Zobrist::psq[captured][capsq];
        }

        dp.remove_pc = captured;
        dp.remove_sq = capsq;

        k ^= Zobrist::psq[captured][capsq];
        st->materialKey ^=
          Zobrist::psq[captured][8 + pieceCount[captured] - (m.type_of() != EN_PASSANT)];

        // Reset rule 50 counter
        st->rule50 = 0;
    }
    else
        dp.remove_sq = SQ_NONE;

    // Update hash key
    k ^= Zobrist::psq[pc][from] ^ Zobrist::psq[pc][to];

    // Reset en passant square
    if (st->epSquare != SQ_NONE)
    {
        k ^= Zobrist::enpassant[file_of(st->epSquare)];
        st->epSquare = SQ_NONE;
    }

    // Update castling rights.
    k ^= Zobrist::castling[st->castlingRights];
    st->castlingRights &= ~(castlingRightsMask[from] | castlingRightsMask[to]);
    k ^= Zobrist::castling[st->castlingRights];

    // If the moving piece is a pawn do some special extra work
    if (type_of(pc) == PAWN)
    {
        // Check if the en passant square needs to be set. Accurate e.p. info is needed
        // for correct zobrist key generation and 3-fold checking.
        if ((int(to) ^ int(from)) == 16)
        {
            Square   epSquare = to - pawn_push(us);
            Bitboard pawns    = attacks_bb<PAWN>(epSquare, us) & pieces(them, PAWN);

            // Fairy-Stockfish records the Atomic en-passant square whenever an
            // opposing pawn attacks it. Full legality is checked when the move
            // is generated, including the resulting explosion.
            if (pawns)
            {
                st->epSquare = epSquare;
                k ^= Zobrist::enpassant[file_of(epSquare)];
            }
        }

        else if (m.type_of() == PROMOTION)
        {
            PieceType pt        = m.promotion_type();
            Piece     promotion = make_piece(us, pt);

            assert(relative_rank(us, to) == RANK_8);
            assert(pt >= KNIGHT && pt <= QUEEN);

            dp.add_pc = promotion;
            dp.add_sq = to;
            dp.to     = SQ_NONE;

            // Update hash keys
            // Zobrist::psq[pc][to] is zero, so we don't need to clear it
            k ^= Zobrist::psq[promotion][to];
            st->materialKey ^= Zobrist::psq[promotion][8 + pieceCount[promotion]]
                             ^ Zobrist::psq[pc][8 + pieceCount[pc] - 1];
            st->nonPawnKey[us] ^= Zobrist::psq[promotion][to];

            if (pt <= BISHOP)
                st->minorPieceKey ^= Zobrist::psq[promotion][to];

            // Update material
            st->nonPawnMaterial[us] += PieceValue[promotion];
        }

        // Update pawn hash key
        st->pawnKey ^= Zobrist::psq[pc][from] ^ Zobrist::psq[pc][to];

        // Reset rule 50 draw counter
        st->rule50 = 0;
    }

    else
    {
        st->nonPawnKey[us] ^= Zobrist::psq[pc][from] ^ Zobrist::psq[pc][to];

        if (type_of(pc) <= BISHOP)
            st->minorPieceKey ^= Zobrist::psq[pc][from] ^ Zobrist::psq[pc][to];
    }

    // Move the piece. The tricky Chess960 castling is handled earlier
    if (m.type_of() != CASTLING)
    {
        Piece toPc = pc;
        if (m.type_of() == PROMOTION)
            toPc = make_piece(us, m.promotion_type());

        if (captured && m.type_of() != EN_PASSANT)
        {
            remove_piece(from);
            swap_piece(to, toPc);
        }
        else if (pc == toPc)
            move_piece(from, to);
        else
        {
            remove_piece(from);
            put_piece(toPc, to);
        }
    }

    // Atomic captures remove the capturing piece, the captured piece (already
    // handled above), and every adjacent non-pawn. Store a compact fixed-size
    // delta so undo does not need a generic variant state.
    if (captured)
    {
        Bitboard blast = (to | (attacks_bb<KING>(to) & (pieces() ^ pieces(PAWN)))) & pieces();

        assert(!(blast & pieces(us, KING)) && "Atomic capture may not explode own king");

        while (blast)
        {
            const Square bsq = pop_lsb(blast);
            const Piece  bpc = piece_on(bsq);
            const Color  bc  = color_of(bpc);

            assert(st->atomicBlastCount < StateInfo::MAX_ATOMIC_BLAST_PIECES);
            st->atomicBlast[st->atomicBlastCount++] = {bpc, bsq};
            dp.atomicBlast.push_back({bpc, bsq});

            k ^= Zobrist::psq[bpc][bsq];
            st->materialKey ^= Zobrist::psq[bpc][8 + pieceCount[bpc] - 1];

            if (type_of(bpc) == PAWN)
                st->pawnKey ^= Zobrist::psq[bpc][bsq];
            else
            {
                st->nonPawnKey[bc] ^= Zobrist::psq[bpc][bsq];
                if (type_of(bpc) != KING)
                    st->nonPawnMaterial[bc] -= PieceValue[bpc];
                if (type_of(bpc) <= BISHOP)
                    st->minorPieceKey ^= Zobrist::psq[bpc][bsq];
            }

            if (st->castlingRights & castlingRightsMask[bsq])
            {
                k ^= Zobrist::castling[st->castlingRights];
                st->castlingRights &= ~castlingRightsMask[bsq];
                k ^= Zobrist::castling[st->castlingRights];
            }

            // The modern FullThreats accumulator cannot represent a nine-piece
            // Atomic delta. Its replacement is introduced with LegacyAtomicV1;
            // avoid overflowing the orthodox dirty-threat buffer meanwhile.
            remove_piece(bsq);
        }
    }

    // The blast changes the position key, material keys, and correction-history
    // keys after the ordinary move update, so publish and prefetch only once the
    // complete Atomic position has been formed.
    if (tt)
        prefetch(tt->first_entry(adjust_key50(k)));

    st->key = k;

    if (history)
    {
        prefetch(&history->pawn_entry(*this)[pc][to]);
        prefetch(&history->pawn_correction_entry(*this));
        prefetch(&history->minor_piece_correction_entry(*this));
        prefetch(&history->nonpawn_correction_entry<WHITE>(*this));
        prefetch(&history->nonpawn_correction_entry<BLACK>(*this));
    }

    // Set capture piece
    st->capturedPiece = captured;

    sideToMove = ~sideToMove;

    // Update king attacks used for fast check detection
    set_check_info();

    // Calculate the repetition info. It is the ply distance from the previous
    // occurrence of the same position, negative in the 3-fold case, or zero
    // if the position was not repeated.
    st->repetition = 0;
    int end        = std::min(st->rule50, st->pliesFromNull);
    if (end >= 4)
    {
        StateInfo* stp = st->previous->previous;
        for (int i = 4; i <= end; i += 2)
        {
            stp = stp->previous->previous;
            if (stp->key == st->key)
            {
                st->repetition = stp->repetition ? -i : i;
                break;
            }
        }
    }

    assert(pos_is_ok());

    assert(dp.pc != NO_PIECE);
    assert(!(bool(captured) || m.type_of() == CASTLING) ^ (dp.remove_sq != SQ_NONE));
    assert(dp.from != SQ_NONE);
    assert(!(dp.add_sq != SQ_NONE) ^ (m.type_of() == PROMOTION || m.type_of() == CASTLING));
}


// Unmakes a move. When it returns, the position should
// be restored to exactly the same state as before the move was made.
void Position::undo_move(Move m) {

    assert(m.is_ok());

    sideToMove = ~sideToMove;

    Color  us   = sideToMove;
    Square from = m.from_sq();
    Square to   = m.to_sq();
    Piece  pc   = piece_on(to);

    assert(empty(from) || m.type_of() == CASTLING);

    // Restore every by-catch piece first. This also restores the capturing
    // piece on the destination square, allowing the orthodox undo path below
    // to move it back and then restore the originally captured piece.
    for (u8 i = 0; i < st->atomicBlastCount; ++i)
    {
        const auto& blasted = st->atomicBlast[i];
        assert(empty(blasted.square));
        put_piece(blasted.piece, blasted.square);
    }

    if (st->atomicBlastCount)
        pc = piece_on(to);

    if (m.type_of() == PROMOTION)
    {
        assert(relative_rank(us, to) == RANK_8);
        assert(type_of(pc) == m.promotion_type());
        assert(type_of(pc) >= KNIGHT && type_of(pc) <= QUEEN);

        pc = make_piece(us, PAWN);
        swap_piece(to, pc);
    }

    if (m.type_of() == CASTLING)
    {
        Square rfrom, rto;
        do_castling<false>(us, from, to, rfrom, rto);
    }
    else
    {
        move_piece(to, from);  // Put the piece back at the source square

        if (st->capturedPiece)
        {
            Square capsq = to;

            if (m.type_of() == EN_PASSANT)
            {
                capsq -= pawn_push(us);

                assert(type_of(pc) == PAWN);
                assert(to == st->previous->epSquare);
                assert(relative_rank(us, to) == RANK_6);
                assert(piece_on(capsq) == NO_PIECE);
                assert(st->capturedPiece == make_piece(~us, PAWN));
            }

            put_piece(st->capturedPiece, capsq);  // Restore the captured piece
        }
    }

    // Finally point our state pointer back to the previous state
    st = st->previous;
    --gamePly;

    assert(pos_is_ok());
}

Key Position::prefetch_key(Move m) const {
    Square from     = m.from_sq();
    Square to       = m.to_sq();
    Piece  pc       = piece_on(from);
    Piece  captured = piece_on(to);
    Key    k        = st->key ^ Zobrist::side;

    k ^= Zobrist::psq[captured][to] ^ Zobrist::psq[pc][to] ^ Zobrist::psq[pc][from];

    if (captured || type_of(pc) == PAWN)
        return k;

    return adjust_key50<true>(k);
}

// Helper used to do/undo a castling move. This is a bit
// tricky in Chess960 where from/to squares can overlap.
template<bool Do>
void Position::do_castling(Color               us,
                           Square              from,
                           Square&             to,
                           Square&             rfrom,
                           Square&             rto,
                           DirtyPiece* const   dp) {

    bool kingSide = to > from;
    rfrom         = to;  // Castling is encoded as "king captures friendly rook"
    rto           = relative_square(us, kingSide ? SQ_F1 : SQ_D1);
    to            = relative_square(us, kingSide ? SQ_G1 : SQ_C1);

    assert(!Do || dp);

    if (Do)
    {
        dp->to        = to;
        dp->remove_pc = dp->add_pc = make_piece(us, ROOK);
        dp->remove_sq              = rfrom;
        dp->add_sq                 = rto;
    }

    // Remove both pieces first since squares could overlap in Chess960
    remove_piece(Do ? from : to);
    remove_piece(Do ? rfrom : rto);
    put_piece(make_piece(us, KING), Do ? to : from);
    put_piece(make_piece(us, ROOK), Do ? rto : rfrom);
}


// Used to do a "null move": it flips
// the side to move without executing any move on the board.
void Position::do_null_move(StateInfo& newSt) {

    assert(!checkers());
    assert(&newSt != st);

    std::memcpy(&newSt, st, sizeof(StateInfo));

    newSt.previous            = st;
    st                        = &newSt;
    st->atomicOpponentInCheck = false;

    if (st->epSquare != SQ_NONE)
    {
        st->key ^= Zobrist::enpassant[file_of(st->epSquare)];
        st->epSquare = SQ_NONE;
    }

    st->key ^= Zobrist::side;

    st->pliesFromNull = 0;

    st->capturedPiece = NO_PIECE;

    sideToMove = ~sideToMove;

    set_check_info();

    st->repetition = 0;

    assert(pos_is_ok());
}


// Must be used to undo a "null move"
void Position::undo_null_move() {

    assert(!checkers());

    st         = st->previous;
    sideToMove = ~sideToMove;
}


// Tests if the Atomic SEE (Static Exchange Evaluation) value of the move is
// greater or equal to the given threshold. A capture ends the exchange by
// exploding the capturing piece, the captured piece, and every adjacent
// non-pawn. For quiet moves, the opponent may choose whether to make the
// explosive capture, so only a negative result is relevant.
bool Position::see_ge(Move m, int threshold) const {

    assert(m.is_ok());

    // Only deal with normal moves, assume others pass a simple SEE
    if (m.type_of() != NORMAL)
        return VALUE_ZERO >= threshold;

    const Square from = m.from_sq();
    const Square to   = m.to_sq();
    const Piece  pc   = piece_on(from);

    assert(pc != NO_PIECE);
    assert(color_of(pc) == sideToMove);

    const Color    us        = color_of(pc);
    const Bitboard fromTo    = from | to;
    Bitboard       blast     = ((attacks_bb<KING>(to) & ~pieces(PAWN)) | fromTo) & pieces();
    int            result    = 0;
    const bool     isCapture = capture(m);

    // A quiet move may be captured explosively. Use the least valuable legal
    // non-king attacker; an attacker inside the blast has no material cost.
    if (!isCapture)
    {
        Bitboard attackers   = attackers_to(to, pieces() ^ fromTo) & pieces(~us) & ~pieces(KING);
        int      minAttacker = VALUE_INFINITE;

        while (attackers)
        {
            const Square s = pop_lsb(attackers);
            minAttacker =
              std::min(minAttacker, (blast & s) ? 0 : int(AtomicCapturePieceValue[piece_on(s)]));
        }

        if (minAttacker == VALUE_INFINITE)
            return VALUE_ZERO >= threshold;

        result += minAttacker;
    }

    bool explodesOurKing   = false;
    bool explodesTheirKing = false;

    while (blast)
    {
        const Piece blastPiece = piece_on(pop_lsb(blast));

        if (type_of(blastPiece) == KING)
        {
            if (color_of(blastPiece) == us)
                explodesOurKing = true;
            else
                explodesTheirKing = true;
        }

        result += color_of(blastPiece) == us ? -int(AtomicCapturePieceValue[blastPiece])
                                             : int(AtomicCapturePieceValue[blastPiece]);
    }

    if (isCapture)
    {
        if (explodesOurKing)
            result = -VALUE_MATE;
        else if (explodesTheirKing)
            result = VALUE_MATE;
        else
            --result;
    }
    else
    {
        // The opponent can decline a quiet capture. Exploding both kings is
        // therefore neutral, while a capture exploding our king is decisive.
        if (explodesOurKing && !explodesTheirKing)
            result = -VALUE_MATE;
        else if (explodesTheirKing)
            result = 0;
        else
            result = std::min(result, 0);
    }

    return result >= threshold;
}

// Tests whether the position is drawn by 50-move rule
// or by repetition. It does not detect stalemates.
bool Position::is_draw(int ply) const {

    if (st->rule50 > 99 && (!atomic_in_check(sideToMove) || has_legal_move()))
        return true;

    return is_repetition(ply);
}

bool Position::has_legal_quiet() const {

    if (is_atomic_terminal())
        return false;

    const Color     us           = sideToMove;
    const Direction up           = pawn_push(us);
    const Bitboard  emptySquares = ~pieces();
    const Bitboard  seventhRank  = us == WHITE ? Rank7BB : Rank2BB;
    const Bitboard  thirdRank    = us == WHITE ? Rank3BB : Rank6BB;
    const Bitboard  pawnsOn7     = pieces(us, PAWN) & seventhRank;
    const Bitboard  pawnsNotOn7  = pieces(us, PAWN) & ~seventhRank;
    const Bitboard  blockers     = blockers_for_king(us);
    const bool      inCheck      = atomic_in_check(us);

    // Outside check, moving a non-king that is not the sole blocker of a
    // slider cannot expose our king. Such a pseudo-legal quiet is therefore
    // already a proof of mobility and avoids a full post-move attack scan in
    // the overwhelmingly common qsearch case.
    const auto legalNonKing = [&](Move move) {
        return (!inCheck && !(blockers & move.from_sq())) || legal(move);
    };

    Bitboard singlePushes = pawn_single_push_bb(us, pawnsNotOn7) & emptySquares;
    Bitboard doublePushes = pawn_single_push_bb(us, singlePushes & thirdRank) & emptySquares;

    while (singlePushes)
    {
        const Square to = pop_lsb(singlePushes);
        if (legalNonKing(Move(to - up, to)))
            return true;
    }

    while (doublePushes)
    {
        const Square to = pop_lsb(doublePushes);
        if (legalNonKing(Move(to - up - up, to)))
            return true;
    }

    Bitboard promotions = pawn_single_push_bb(us, pawnsOn7) & emptySquares;
    while (promotions)
    {
        const Square to = pop_lsb(promotions);
        // Promotion type cannot change friendly-king safety: all four choices
        // remove and occupy the same squares. One queen probe proves mobility.
        if (legalNonKing(Move::make<PROMOTION>(to - up, to, QUEEN)))
            return true;
    }

    for (PieceType pt : {KNIGHT, BISHOP, ROOK, QUEEN, KING})
    {
        Bitboard movers = pieces(us, pt);
        while (movers)
        {
            const Square from    = pop_lsb(movers);
            Bitboard     targets = attacks_bb(pt, from, pieces()) & emptySquares;
            while (targets)
                if (const Move move(from, pop_lsb(targets));
                    (pt != KING && legalNonKing(move)) || (pt == KING && legal(move)))
                    return true;
        }
    }

    if (can_castle(us & ANY_CASTLING))
        for (CastlingRights cr : {us & KING_SIDE, us & QUEEN_SIDE})
            if (!castling_impeded(cr) && can_castle(cr)
                && legal(Move::make<CASTLING>(square<KING>(us), castling_rook_square(cr))))
                return true;

    return false;
}

bool Position::has_legal_move() const {

    if (has_legal_quiet())
        return true;

    for (Move move : MoveList<CAPTURES>(*this))
        if (legal(move))
            return true;

    return false;
}

// Return a draw score if a position repeats once earlier but strictly
// after the root, or repeats twice before or at the root.
bool Position::is_repetition(int ply) const { return st->repetition && st->repetition < ply; }

// Tests whether there has been at least one repetition
// of positions since the last capture or pawn move.
bool Position::has_repeated() const {

    StateInfo* stc = st;
    int        end = std::min(st->rule50, st->pliesFromNull);
    while (end-- >= 4)
    {
        if (stc->repetition)
            return true;

        stc = stc->previous;
    }
    return false;
}


// Tests if the position has a move which draws by repetition.
// This function accurately matches the outcome of is_draw() over all legal moves.
bool Position::upcoming_repetition(int ply) const {

    int j;

    int end = std::min(st->rule50, st->pliesFromNull);

    if (end < 3)
        return false;

    Key        originalKey = st->key;
    StateInfo* stp         = st->previous;
    Key        other       = originalKey ^ stp->key ^ Zobrist::side;

    for (int i = 3; i <= end; i += 2)
    {
        stp = stp->previous;
        other ^= stp->key ^ stp->previous->key ^ Zobrist::side;
        stp = stp->previous;

        if (other != 0)
            continue;

        Key moveKey = originalKey ^ stp->key;
        if ((j = H1(moveKey), cuckoo[j] == moveKey) || (j = H2(moveKey), cuckoo[j] == moveKey))
        {
            Move   move = cuckooMove[j];
            Square s1   = move.from_sq();
            Square s2   = move.to_sq();

            if (!((between_bb(s1, s2) ^ s2) & pieces()))
            {
                if (ply > i)
                    return true;

                // For nodes before or at the root, check that the move is a
                // repetition rather than a move to the current position.
                if (stp->repetition)
                    return true;
            }
        }
    }
    return false;
}


// Flips position with the white and black sides reversed. This
// is only useful for debugging e.g. for finding evaluation symmetry bugs.
void Position::flip() {

    string            f, token;
    std::stringstream ss(fen());

    for (Rank r = RANK_8;; --r)  // Piece placement
    {
        std::getline(ss, token, r > RANK_1 ? '/' : ' ');
        f.insert(0, token + (f.empty() ? " " : "/"));

        if (r == RANK_1)
            break;
    }

    ss >> token;                        // Active color
    f += (token == "w" ? "B " : "W ");  // Will be lowercased later

    ss >> token;  // Castling availability
    f += token + " ";

    std::transform(f.begin(), f.end(), f.begin(),
                   [](char c) { return char(islower(c) ? toupper(c) : tolower(c)); });

    ss >> token;  // En passant square
    f += (token == "-" ? token : token.replace(1, 1, token[1] == '3' ? "6" : "3"));

    std::getline(ss, token);  // Half and full moves
    f += token;

    set(f, is_chess960(), st);

    assert(pos_is_ok());
}


bool Position::material_key_is_ok() const { return compute_material_key() == st->materialKey; }


// Performs some consistency checks for the position object
// and raise an assert if something wrong is detected.
// This is meant to be helpful when debugging.
bool Position::pos_is_ok() const {

    constexpr bool Fast = false;  // fast or full check?

    if ((sideToMove != WHITE && sideToMove != BLACK)
        || (has_king(WHITE) && piece_on(square<KING>(WHITE)) != W_KING)
        || (has_king(BLACK) && piece_on(square<KING>(BLACK)) != B_KING)
        || (!has_king(WHITE) && !has_king(BLACK))
        || (ep_square() != SQ_NONE && relative_rank(sideToMove, ep_square()) != RANK_6))
        assert(0 && "pos_is_ok: Default");

    if (Fast)
        return true;

    if (pieceCount[W_KING] > 1 || pieceCount[B_KING] > 1)
        assert(0 && "pos_is_ok: Kings");

    if ((pieces(PAWN) & (Rank1BB | Rank8BB)) || pieceCount[W_PAWN] > 8 || pieceCount[B_PAWN] > 8)
        assert(0 && "pos_is_ok: Pawns");


    if (ep_square() != SQ_NONE && has_king(sideToMove))
    {
        Bitboard captured = (ep_square() + pawn_push(~sideToMove)) & pieces(~sideToMove, PAWN);
        Bitboard pawns    = attacks_bb<PAWN>(ep_square(), ~sideToMove) & pieces(sideToMove, PAWN);

        if (!captured || !pawns || !empty(ep_square()))
            assert(0 && "pos_is_ok: En passant square");
    }

    if ((pieces(WHITE) & pieces(BLACK)) || (pieces(WHITE) | pieces(BLACK)) != pieces()
        || popcount(pieces(WHITE)) > 16 || popcount(pieces(BLACK)) > 16)
        assert(0 && "pos_is_ok: Bitboards");

    for (PieceType p1 = PAWN; p1 <= KING; ++p1)
        for (PieceType p2 = PAWN; p2 <= KING; ++p2)
            if (p1 != p2 && (pieces(p1) & pieces(p2)))
                assert(0 && "pos_is_ok: Bitboards");


    for (Piece pc : Pieces)
        if (pieceCount[pc] != popcount(pieces(color_of(pc), type_of(pc)))
            || pieceCount[pc] != std::count(board.begin(), board.end(), pc))
            assert(0 && "pos_is_ok: Pieces");

    for (Color c : {WHITE, BLACK})
        for (CastlingRights cr : {c & KING_SIDE, c & QUEEN_SIDE})
        {
            if (!can_castle(cr))
                continue;

            if (piece_on(castlingRookSquare[cr]) != make_piece(c, ROOK)
                || castlingRightsMask[castlingRookSquare[cr]] != cr || !has_king(c)
                || (castlingRightsMask[square<KING>(c)] & cr) != cr)
                assert(0 && "pos_is_ok: Castling");
        }

    assert(material_key_is_ok() && "pos_is_ok: materialKey");

    return true;
}

}  // namespace Stockfish
