/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Atomic-Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with Atomic-Stockfish.  If not, see <http://www.gnu.org/licenses/>.
*/

#include "legacy_atomic_v1.h"

#include <cerrno>
#include <limits>
#include <sstream>
#include <system_error>
#include <utility>

#include "movegen.h"
#include "position.h"

#ifdef _WIN32
    #include <fcntl.h>
    #include <io.h>
    #include <sys/stat.h>
#else
    #include <fcntl.h>
    #include <unistd.h>
#endif

namespace Stockfish::Data {
namespace {

constexpr std::string_view SchemaJson =
  R"({"schema_sha256":"acca0f551f1c012c31a6c727dedccaebb7b5ebbc46810edb87e31bb208d5abe1","formats":{"legacy-atomic-v1":{"read":false,"write":true,"record_size":72}}})";

class BitWriter {
   public:
    BitWriter(u8* storage, std::size_t bytes) :
        data(storage),
        capacity(bytes * 8) {}

    bool write(u32 value, unsigned bits) {
        if (bits > 32 || cursor > capacity || bits > capacity - cursor)
            return false;

        for (unsigned bit = 0; bit < bits; ++bit, ++cursor)
            if (value & (u32(1) << bit))
                data[cursor / 8] |= u8(1U << (cursor & 7));

        return true;
    }

   private:
    u8*         data;
    std::size_t capacity;
    std::size_t cursor = 0;
};

bool native_byte_order_is_little_endian() {
    constexpr u16 value = 1;
    return *reinterpret_cast<const u8*>(&value) == 1;
}

void write_u16_le(LegacyAtomicV1Record& record, std::size_t offset, u16 value) {
    record[offset]     = u8(value & 0xFF);
    record[offset + 1] = u8(value >> 8);
}

DataResult packed_position_error(std::string message) {
    return DataResult::failure(DataError::PACKED_POSITION_OVERFLOW, std::move(message));
}

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
    for (unsigned char c : text)
    {
        if (c < '0' || c > '9')
            return false;
        const u64 digit = c - '0';
        if (value > (maximum - digit) / 10)
            return false;
        value = value * 10 + digit;
    }
    return true;
}

bool has_standard_castling_rook_origins(const Position& pos) {
    return (!pos.can_castle(WHITE_OO) || pos.castling_rook_square(WHITE_OO) == SQ_H1)
        && (!pos.can_castle(WHITE_OOO) || pos.castling_rook_square(WHITE_OOO) == SQ_A1)
        && (!pos.can_castle(BLACK_OO) || pos.castling_rook_square(BLACK_OO) == SQ_H8)
        && (!pos.can_castle(BLACK_OOO) || pos.castling_rook_square(BLACK_OOO) == SQ_A8);
}

DataResult pack_position(const Position& pos, LegacyAtomicV1Record& record) {
    if (pos.is_chess960() || !has_standard_castling_rook_origins(pos))
        return DataResult::failure(DataError::UNSUPPORTED_CHESS960,
                                   "Legacy Atomic V1 cannot encode Atomic960 castling origins");

    if (pos.count<KING>(WHITE) != 1 || pos.count<KING>(BLACK) != 1)
        return DataResult::failure(DataError::UNSUPPORTED_POSITION,
                                   "Legacy Atomic V1 requires exactly one king per color");

    const int rule50 = pos.rule50_count();
    if (rule50 < 0 || rule50 > 127)
        return DataResult::failure(DataError::POSITION_CLOCK_OUT_OF_RANGE,
                                   "Legacy Atomic V1 rule50 must fit in seven bits");

    const int gamePly  = pos.game_ply();
    const int fullmove = 1 + (gamePly - (pos.side_to_move() == BLACK)) / 2;
    if (gamePly < 0 || fullmove < 0 || fullmove > std::numeric_limits<u16>::max())
        return DataResult::failure(DataError::POSITION_CLOCK_OUT_OF_RANGE,
                                   "Legacy Atomic V1 fullmove must fit in sixteen bits");

    BitWriter bits(record.data(), LegacyAtomicV1PackedPositionSize);
    if (!bits.write(pos.side_to_move(), 1) || !bits.write(pos.square<KING>(WHITE), 7)
        || !bits.write(pos.square<KING>(BLACK), 7))
        return packed_position_error("Legacy Atomic V1 king header exceeds 64 bytes");

    constexpr std::array<u8, 6> PieceCode = {0, 1, 3, 5, 7, 9};
    constexpr std::array<u8, 6> PieceBits = {1, 5, 5, 5, 5, 5};

    for (Rank rank = RANK_8;; --rank)
    {
        for (File file = FILE_A; file <= FILE_H; ++file)
        {
            const Piece piece = pos.piece_on(make_square(file, rank));
            if (piece != NO_PIECE && type_of(piece) == KING)
                continue;

            const PieceType type = piece == NO_PIECE ? NO_PIECE_TYPE : type_of(piece);
            if (type > QUEEN)
                return DataResult::failure(
                  DataError::UNSUPPORTED_POSITION,
                  "Legacy Atomic V1 encountered an unsupported piece type");

            if (!bits.write(PieceCode[type], PieceBits[type])
                || (piece != NO_PIECE && !bits.write(color_of(piece), 1)))
                return packed_position_error("Legacy Atomic V1 board exceeds 64 bytes");
        }

        if (rank == RANK_1)
            break;
    }

    // Historical Fairy writes one five-bit hand count for each of the six
    // pieces of each color. Atomic has no pockets, so all twelve are zero.
    for (int field = 0; field < 12; ++field)
        if (!bits.write(0, 5))
            return packed_position_error("Legacy Atomic V1 hand fields exceed 64 bytes");

    if (!bits.write(pos.can_castle(WHITE_OO), 1) || !bits.write(pos.can_castle(WHITE_OOO), 1)
        || !bits.write(pos.can_castle(BLACK_OO), 1) || !bits.write(pos.can_castle(BLACK_OOO), 1))
        return packed_position_error("Legacy Atomic V1 castling fields exceed 64 bytes");

    const Square ep = pos.ep_square();
    if (ep == SQ_NONE)
    {
        if (!bits.write(0, 1))
            return packed_position_error("Legacy Atomic V1 en-passant field exceeds 64 bytes");
    }
    else
    {
        if (!is_ok(ep))
            return DataResult::failure(DataError::UNSUPPORTED_POSITION,
                                       "Legacy Atomic V1 en-passant square is invalid");
        if (!bits.write(1, 1) || !bits.write(ep, 7))
            return packed_position_error("Legacy Atomic V1 en-passant field exceeds 64 bytes");
    }

    // Clock tail order is frozen by atomic-schema.json for compatibility:
    // rule50-low-6, fullmove-low-8, fullmove-high-8, rule50-high-1.
    if (!bits.write(u32(rule50), 6) || !bits.write(u32(fullmove), 8)
        || !bits.write(u32(fullmove) >> 8, 8) || !bits.write(u32(rule50) >> 6, 1))
        return packed_position_error("Legacy Atomic V1 clock tail exceeds 64 bytes");

    return DataResult::success();
}

std::string system_error_message(int error) { return std::generic_category().message(error); }

}  // namespace

std::string_view atomic_data_schema_json() noexcept { return SchemaJson; }

DataResult encode_legacy_atomic_v1(const TrainingDataSample& sample, LegacyAtomicV1Record& record) {
    record.fill(0);

    if (!native_byte_order_is_little_endian())
        return DataResult::failure(DataError::UNSUPPORTED_BYTE_ORDER,
                                   "Legacy Atomic V1 requires a little-endian host");

    if (!sample.move.is_ok() || sample.move.raw() == 0
        || sample.move.from_sq() == sample.move.to_sq())
        return DataResult::failure(DataError::INVALID_MOVE,
                                   "Legacy Atomic V1 requires a non-null 16-bit move");

    if (sample.score < std::numeric_limits<i16>::min()
        || sample.score > std::numeric_limits<i16>::max())
        return DataResult::failure(DataError::SCORE_OUT_OF_RANGE,
                                   "Legacy Atomic V1 score must fit in int16");

    if (sample.ply < 0 || sample.ply > std::numeric_limits<u16>::max())
        return DataResult::failure(DataError::PLY_OUT_OF_RANGE,
                                   "Legacy Atomic V1 ply must fit in uint16");

    if (sample.result < -1 || sample.result > 1)
        return DataResult::failure(DataError::RESULT_OUT_OF_RANGE,
                                   "Legacy Atomic V1 result must be -1, 0, or 1");

    if (sample.flags != NO_TRAINING_DATA_FLAGS)
        return DataResult::failure(
          sample.flags & TRAINING_DATA_CHESS960 ? DataError::UNSUPPORTED_CHESS960
                                                : DataError::UNSUPPORTED_POSITION,
          "Legacy Atomic V1 cannot encode this sample's format-neutral flags");

    std::array<std::string, 6> requestedFields;
    if (!split_fen_fields(sample.fen, requestedFields))
        return DataResult::failure(DataError::UNSUPPORTED_POSITION,
                                   "Legacy Atomic V1 requires exactly six FEN fields");

    u64 rule50   = 0;
    u64 fullmove = 0;
    if (!parse_decimal_field(requestedFields[4], 127, rule50)
        || !parse_decimal_field(requestedFields[5], std::numeric_limits<u16>::max(), fullmove)
        || fullmove == 0)
        return DataResult::failure(
          DataError::POSITION_CLOCK_OUT_OF_RANGE,
          "Legacy Atomic V1 requires decimal rule50/fullmove fields in wire range");

    Position  position;
    StateInfo state{};
    if (const auto error = position.set(sample.fen, false, &state))
        return DataResult::failure(DataError::UNSUPPORTED_POSITION,
                                   std::string("Cannot encode training-data FEN: ")
                                     + error->what());

    std::array<std::string, 6> canonicalFields;
    const std::string          canonicalFen = position.fen();
    if (!split_fen_fields(canonicalFen, canonicalFields) || canonicalFields != requestedFields)
        return DataResult::failure(
          DataError::UNSUPPORTED_POSITION,
          "Legacy Atomic V1 refuses FEN fields normalized by the Atomic parser");

    if (DataResult packed = pack_position(position, record); !packed)
    {
        record.fill(0);
        return packed;
    }

    bool moveIsLegal = false;
    for (Move legalMove : MoveList<LEGAL>(position))
        if (legalMove == sample.move)
        {
            moveIsLegal = true;
            break;
        }

    if (!moveIsLegal)
    {
        record.fill(0);
        return DataResult::failure(
          DataError::INVALID_MOVE,
          "Legacy Atomic V1 move is illegal or does not match the sample FEN");
    }

    write_u16_le(record, 64, u16(i16(sample.score)));
    write_u16_le(record, 66, sample.move.raw());
    write_u16_le(record, 68, u16(sample.ply));
    record[70] = u8(i8(sample.result));
    record[71] = 0;
    return DataResult::success();
}

LegacyAtomicV1Sink::LegacyAtomicV1Sink(std::filesystem::path path) :
    outputPath(std::move(path)) {}

LegacyAtomicV1Sink::~LegacyAtomicV1Sink() {
    if (!finalized && !aborted)
    {
        int closeError  = 0;
        int removeError = 0;
        cleanup_partial(closeError, removeError);
    }
}

DataResult LegacyAtomicV1Sink::open_exclusively() {
    if (fileDescriptor != -1)
        return DataResult::success();

    errno = 0;
#ifdef _WIN32
    fileDescriptor =
      ::_wopen(outputPath.c_str(), _O_BINARY | _O_WRONLY | _O_CREAT | _O_EXCL | _O_NOINHERIT,
               _S_IREAD | _S_IWRITE);
#else
    int flags = O_WRONLY | O_CREAT | O_EXCL;
    #ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
    #endif
    fileDescriptor = ::open(outputPath.c_str(), flags, 0666);
#endif
    if (fileDescriptor != -1)
    {
        created = true;
        return DataResult::success();
    }

    const int error = errno;
    return DataResult::failure(error == EEXIST ? DataError::OUTPUT_EXISTS : DataError::OPEN_FAILED,
                               "Cannot create training-data output exclusively: "
                                 + system_error_message(error));
}

DataResult LegacyAtomicV1Sink::write_record(const LegacyAtomicV1Record& record) {
    std::size_t written = 0;
    while (written < record.size())
    {
        errno = 0;
#ifdef _WIN32
        const int count =
          ::_write(fileDescriptor, record.data() + written, unsigned(record.size() - written));
#else
        const ssize_t count =
          ::write(fileDescriptor, record.data() + written, record.size() - written);
#endif
        if (count > 0)
        {
            written += std::size_t(count);
            continue;
        }

        if (count < 0 && errno == EINTR)
            continue;

        const int error = count == 0 ? EIO : errno;
        return DataResult::failure(DataError::WRITE_FAILED, "Cannot write Legacy Atomic V1 record: "
                                                              + system_error_message(error));
    }

    return DataResult::success();
}

DataResult LegacyAtomicV1Sink::append(const TrainingDataSample& sample) {
    if (!accepting)
        return DataResult::failure(DataError::SINK_CLOSED,
                                   "Cannot write to a closed training-data sink");

    LegacyAtomicV1Record record{};
    if (DataResult encoded = encode_legacy_atomic_v1(sample, record); !encoded)
        return encoded;

    if (DataResult opened = open_exclusively(); !opened)
        return opened;

    if (DataResult written = write_record(record); !written)
    {
        accepting = false;
        return written;
    }

    ++recordsWritten;
    return DataResult::success();
}

DataResult LegacyAtomicV1Sink::finalize() {
    if (finalized)
        return DataResult::success();

    if (aborted || !accepting)
        return DataResult::failure(DataError::SINK_CLOSED,
                                   "Cannot finalize an aborted or failed training-data sink");

    accepting = false;
    if (recordsWritten == 0)
        return DataResult::failure(DataError::EMPTY_DATASET,
                                   "Legacy Atomic V1 forbids an empty dataset");

    if (fileDescriptor == -1)
        return DataResult::failure(DataError::CLOSE_FAILED,
                                   "Training-data output disappeared before finalization");

    const int descriptor = fileDescriptor;
    fileDescriptor       = -1;

    errno = 0;
#ifdef _WIN32
    const int result = ::_close(descriptor);
#else
    const int result = ::close(descriptor);
#endif
    if (result == 0)
    {
        finalized = true;
        return DataResult::success();
    }

    const int error = errno;
    return DataResult::failure(DataError::CLOSE_FAILED, "Cannot close Legacy Atomic V1 output: "
                                                          + system_error_message(error));
}

void LegacyAtomicV1Sink::cleanup_partial(int& closeError, int& removeError) noexcept {
    closeError  = 0;
    removeError = 0;

    accepting = false;
    if (fileDescriptor != -1)
    {
        const int descriptor = fileDescriptor;
        fileDescriptor       = -1;
        errno                = 0;
#ifdef _WIN32
        if (::_close(descriptor) != 0)
#else
        if (::close(descriptor) != 0)
#endif
            closeError = errno;
    }

    if (created)
    {
        errno = 0;
#ifdef _WIN32
        if (::_wunlink(outputPath.c_str()) != 0 && errno != ENOENT)
#else
        if (::unlink(outputPath.c_str()) != 0 && errno != ENOENT)
#endif
            removeError = errno;
        else
            created = false;
    }

    // A failed unlink remains retryable by abort() and by the destructor.
    aborted = !created;
}

DataResult LegacyAtomicV1Sink::abort() {
    if (aborted)
        return DataResult::success();

    if (finalized)
        return DataResult::failure(DataError::SINK_CLOSED,
                                   "Cannot abort a finalized training-data sink");

    int closeError  = 0;
    int removeError = 0;
    cleanup_partial(closeError, removeError);
    if (closeError || removeError)
        return DataResult::failure(
          DataError::ABORT_FAILED,
          std::string("Cannot fully remove partial training-data output: ")
            + system_error_message(removeError ? removeError : closeError));

    return DataResult::success();
}

}  // namespace Stockfish::Data
