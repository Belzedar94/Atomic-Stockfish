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

#ifndef TRAINING_DATA_H_INCLUDED
#define TRAINING_DATA_H_INCLUDED

#include <cstdint>
#include <string>
#include <utility>

#include "types.h"

namespace Stockfish::Data {

enum TrainingDataFlags : u32 {
    NO_TRAINING_DATA_FLAGS = 0,
    TRAINING_DATA_CHESS960 = 1U << 0
};

// A format-neutral training sample. Score and result are both relative to the
// side to move in fen. The sample owns its position because generators buffer
// samples until the game result is known, long after the search Position has
// moved on. Flags leave room for versioned formats to preserve semantics such
// as Atomic960 without coupling the sample to any one wire layout.
struct TrainingDataSample {
    std::string fen;
    int         score  = 0;
    Move        move   = Move::none();
    int         ply    = 0;
    int         result = 0;
    u32         flags  = NO_TRAINING_DATA_FLAGS;
};

enum class DataError {
    NONE,
    UNSUPPORTED_BYTE_ORDER,
    UNSUPPORTED_CHESS960,
    UNSUPPORTED_POSITION,
    INVALID_MOVE,
    SCORE_OUT_OF_RANGE,
    PLY_OUT_OF_RANGE,
    RESULT_OUT_OF_RANGE,
    POSITION_CLOCK_OUT_OF_RANGE,
    PACKED_POSITION_OVERFLOW,
    OUTPUT_EXISTS,
    OPEN_FAILED,
    WRITE_FAILED,
    CLOSE_FAILED,
    EMPTY_DATASET,
    ABORT_FAILED,
    SINK_CLOSED,
    INVALID_HEADER,
    INVALID_RECORD,
    INVALID_MANIFEST,
    SCHEMA_MISMATCH,
    RECORD_COUNT_OUT_OF_RANGE,
    FILE_SIZE_MISMATCH
};

struct DataResult {
    DataError   error = DataError::NONE;
    std::string message;

    explicit operator bool() const { return error == DataError::NONE; }

    static DataResult success() { return {}; }
    static DataResult failure(DataError failureError, std::string message) {
        return {failureError, std::move(message)};
    }
};

using TrainingSample = TrainingDataSample;

class DatasetSink {
   public:
    virtual ~DatasetSink() = default;

    virtual DataResult append(const TrainingDataSample& sample) = 0;
    virtual DataResult finalize()                               = 0;
    virtual DataResult abort()                                  = 0;
};

using TrainingDataSink = DatasetSink;

}  // namespace Stockfish::Data

#endif  // #ifndef TRAINING_DATA_H_INCLUDED
