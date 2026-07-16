/*
  Atomic-Stockfish AtomicNNUEV3 trajectory producer
  Copyright (C) 2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_v3_trajectory.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstring>
#include <fstream>
#include <limits>
#include <queue>
#include <system_error>
#include <vector>

#ifdef _WIN32
    #include <fcntl.h>
    #include <io.h>
    #include <sys/stat.h>
#else
    #include <fcntl.h>
    #include <sys/stat.h>
    #include <unistd.h>
#endif

#include "api/atomic_outcome.h"
#include "atomic_bin_v2_wire.h"
#include "movegen.h"
#include "nnue/atomic_v3/full_refresh.h"
#include "position.h"
#include "sha256.h"

namespace Stockfish::Data {
namespace {

constexpr std::array<u8, 8> TrajectoryMagic = {'A', 'T', 'T', 'R', 'A', 'J', '1', 0};
constexpr usize             HeaderSize      = 160;
constexpr usize             EntrySize       = 112;

constexpr char  SplitGroupDomain[]      = "atomic-split-group-v1\0";
constexpr char  PartitionDomain[]       = "atomic-split-v1\0";
constexpr char  FeatureInputDomain[]    = "atomic-v3-feature-input-v1\0";
constexpr usize ExternalSortMemoryBytes = usize(64) * 1024 * 1024;
constexpr usize ExternalSortMergeFanIn  = 64;
constexpr usize ExternalSortKeySize     = 32;

static_assert(ExternalSortMergeFanIn > 1);

DataResult invalid(std::string message) {
    return DataResult::failure(DataError::INVALID_RECORD, std::move(message));
}

DataResult io_failure(std::string message) {
    return DataResult::failure(DataError::WRITE_FAILED, std::move(message));
}

void write_u16_le(u8* output, u16 value) noexcept {
    output[0] = u8(value);
    output[1] = u8(value >> 8);
}

void write_u32_le(u8* output, u32 value) noexcept {
    for (unsigned i = 0; i < 4; ++i)
        output[i] = u8(value >> (8 * i));
}

void write_u64_le(u8* output, u64 value) noexcept {
    for (unsigned i = 0; i < 8; ++i)
        output[i] = u8(value >> (8 * i));
}

u64 read_u64_le(const u8* input) noexcept {
    u64 value = 0;
    for (unsigned i = 0; i < 8; ++i)
        value |= u64(input[i]) << (8 * i);
    return value;
}

bool add_overflows(u64 lhs, u64 rhs) noexcept {
    return lhs > std::numeric_limits<u64>::max() - rhs;
}

bool multiply_overflows(u64 lhs, u64 rhs) noexcept {
    return rhs != 0 && lhs > std::numeric_limits<u64>::max() / rhs;
}

std::FILE* open_exclusive(const std::filesystem::path& path) {
#ifdef _WIN32
    const int descriptor =
      ::_wopen(path.c_str(), _O_CREAT | _O_EXCL | _O_WRONLY | _O_BINARY, _S_IREAD | _S_IWRITE);
#else
    const int descriptor = ::open(path.c_str(), O_CREAT | O_EXCL | O_WRONLY, 0600);
#endif
    if (descriptor == -1)
        return nullptr;
    std::FILE* file = ::fdopen(descriptor, "wb");
    if (!file)
    {
        const int error = errno;
#ifdef _WIN32
        ::_close(descriptor);
#else
        ::close(descriptor);
#endif
        errno = error;
    }
    return file;
}

std::FILE* open_read(const std::filesystem::path& path) {
#ifdef _WIN32
    return ::_wfopen(path.c_str(), L"rb");
#else
    return std::fopen(path.c_str(), "rb");
#endif
}

DataResult write_exact(std::FILE* output, const void* data, usize size, std::string_view what) {
    if (size == 0 || std::fwrite(data, 1, size, output) == size)
        return DataResult::success();
    return io_failure("Cannot write Atomic V3 " + std::string(what) + ": " + std::strerror(errno));
}

DataResult sync_file(std::FILE* file, std::string_view what) {
    if (std::fflush(file) != 0)
        return io_failure("Cannot flush Atomic V3 " + std::string(what) + ": "
                          + std::strerror(errno));
#ifdef _WIN32
    if (::_commit(::_fileno(file)) != 0)
#else
    if (::fsync(::fileno(file)) != 0)
#endif
        return io_failure("Cannot synchronize Atomic V3 " + std::string(what) + ": "
                          + std::strerror(errno));
    return DataResult::success();
}

DataResult close_file(std::FILE*& file, std::string_view what) {
    if (!file)
        return DataResult::success();
    std::FILE* owned = file;
    file             = nullptr;
    if (std::fclose(owned) == 0)
        return DataResult::success();
    return DataResult::failure(DataError::CLOSE_FAILED, "Cannot close Atomic V3 "
                                                          + std::string(what) + ": "
                                                          + std::strerror(errno));
}

bool decode_lower_hex(std::string_view text, u8* output, usize size) noexcept {
    if (text.size() != size * 2)
        return false;
    const auto nibble = [](unsigned char character) -> int {
        if (character >= '0' && character <= '9')
            return character - '0';
        if (character >= 'a' && character <= 'f')
            return character - 'a' + 10;
        return -1;
    };
    for (usize i = 0; i < size; ++i)
    {
        const int high = nibble(text[i * 2]);
        const int low  = nibble(text[i * 2 + 1]);
        if (high < 0 || low < 0)
            return false;
        output[i] = u8((high << 4) | low);
    }
    return true;
}

DataResult copy_stream(std::FILE* input, std::FILE* output, std::string_view what) {
    std::array<u8, 64 * 1024> buffer{};
    while (true)
    {
        const usize read = std::fread(buffer.data(), 1, buffer.size(), input);
        if (read && std::fwrite(buffer.data(), 1, read, output) != read)
            return io_failure("Cannot write Atomic V3 " + std::string(what) + ": "
                              + std::strerror(errno));
        if (read != buffer.size())
        {
            if (std::ferror(input))
                return DataResult::failure(DataError::READ_FAILED, "Cannot read Atomic V3 "
                                                                     + std::string(what) + ": "
                                                                     + std::strerror(errno));
            return DataResult::success();
        }
    }
}

std::optional<AtomicV3StopReason> stop_reason(Atomic::Termination termination) noexcept {
    switch (termination)
    {
    case Atomic::Termination::AtomicExplosion :
        return AtomicV3StopReason::ATOMIC_EXPLOSION;
    case Atomic::Termination::Checkmate :
        return AtomicV3StopReason::CHECKMATE;
    case Atomic::Termination::Stalemate :
        return AtomicV3StopReason::STALEMATE;
    case Atomic::Termination::InsufficientMaterial :
        return AtomicV3StopReason::INSUFFICIENT_MATERIAL;
    case Atomic::Termination::FiftyMoveRule :
        return AtomicV3StopReason::FIFTY_MOVE_RULE;
    case Atomic::Termination::ThreefoldRepetition :
        return AtomicV3StopReason::THREEFOLD_REPETITION;
    case Atomic::Termination::Ongoing :
        return std::nullopt;
    }
    return std::nullopt;
}

i8 white_result(const Atomic::Outcome& outcome) noexcept {
    return !outcome.winner ? 0 : (*outcome.winner == WHITE ? 1 : -1);
}

template<typename Emission>
void append_physical_indices(const Emission& emission, std::vector<u32>& indices) {
    for (u32 i = 0; i < emission.size; ++i)
        indices.push_back(u32(emission.features[i].physicalIndex));
}

DataResult remove_paths(const std::vector<std::filesystem::path>& paths) {
    DataResult first = DataResult::success();
    for (const auto& path : paths)
    {
        std::error_code ec;
        std::filesystem::remove(path, ec);
        if (ec && first)
            first = DataResult::failure(DataError::ABORT_FAILED,
                                        "Cannot remove Atomic V3 external-sort temporary "
                                          + path.string() + ": " + ec.message());
    }
    return first;
}

}  // namespace

AtomicV3SplitGroupId atomic_v3_split_group_id(const AtomicBinV2Position& root,
                                              bool                       atomic960,
                                              const std::vector<u32>&    playedMoves) noexcept {
    Sha256 hash;
    hash.update(SplitGroupDomain, sizeof(SplitGroupDomain) - 1);
    hash.update(root.data(), root.size());
    const u8 flag = atomic960 ? 1 : 0;
    hash.update(&flag, 1);
    std::array<u8, 8> count{};
    write_u64_le(count.data(), u64(playedMoves.size()));
    hash.update(count.data(), count.size());
    std::array<u8, 4> wire{};
    for (u32 move : playedMoves)
    {
        write_u32_le(wire.data(), move);
        hash.update(wire.data(), wire.size());
    }
    return hash.digest();
}

u64 atomic_v3_partition_hash(u64 splitSeed, const AtomicV3SplitGroupId& group) noexcept {
    Sha256 hash;
    hash.update(PartitionDomain, sizeof(PartitionDomain) - 1);
    std::array<u8, 8> seed{};
    write_u64_le(seed.data(), splitSeed);
    hash.update(seed.data(), seed.size());
    hash.update(group.data(), group.size());
    const auto digest = hash.digest();
    return read_u64_le(digest.data());
}

AtomicV3DatasetRole atomic_v3_partition_role(u64                         splitSeed,
                                             u64                         validationThreshold,
                                             const AtomicV3SplitGroupId& group) noexcept {
    return atomic_v3_partition_hash(splitSeed, group) < validationThreshold
           ? AtomicV3DatasetRole::VALIDATION
           : AtomicV3DatasetRole::TRAIN;
}

DataResult atomic_v3_feature_input_key(const TrainingDataSample& sample,
                                       AtomicV3FeatureInputKey&  key) {
    key = {};
    if ((sample.flags & ~TRAINING_DATA_CHESS960) != 0)
        return invalid("Atomic V3 feature identity received unsupported sample flags");

    StateInfo state{};
    Position  position;
    if (const auto setError =
          position.set(sample.fen, bool(sample.flags & TRAINING_DATA_CHESS960), &state))
        return invalid(std::string("Atomic V3 feature identity FEN is invalid: ")
                       + setError->what());
    if (position.fen() != sample.fen || !position.has_king(WHITE) || !position.has_king(BLACK))
        return invalid("Atomic V3 feature identity requires a canonical evaluable FEN");

    using namespace Eval::NNUE::AtomicV3;
    const Color                               stm = position.side_to_move();
    std::array<FullRefreshEmission, COLOR_NB> emissions{};
    for (usize i = 0; i < emissions.size(); ++i)
    {
        const Color perspective = i == 0 ? stm : ~stm;
        const auto  result      = emit_full_refresh(position, perspective, emissions[i]);
        if (result != FullRefreshError::None)
            return invalid(std::string("Cannot compute Atomic V3 feature identity: ")
                           + full_refresh_error_message(result));
    }
    if (emissions[0].hm.networkBucket != emissions[1].hm.networkBucket)
        return invalid("Atomic V3 perspectives selected different shared network buckets");

    std::array<std::vector<u32>, COLOR_NB> active;
    for (usize i = 0; i < active.size(); ++i)
    {
        active[i].reserve(emissions[i].active_feature_count());
        append_physical_indices(emissions[i].hm, active[i]);
        append_physical_indices(emissions[i].capturePairs, active[i]);
        append_physical_indices(emissions[i].kingBlastEp, active[i]);
        append_physical_indices(emissions[i].blastRing, active[i]);
        std::sort(active[i].begin(), active[i].end());
        if (std::adjacent_find(active[i].begin(), active[i].end()) != active[i].end())
            return invalid("Atomic V3 full refresh emitted a duplicate physical feature index");
    }

    std::array<u8, 32> schema{};
    if (!decode_lower_hex(AtomicV3FeatureSchemaSha256Hex, schema.data(), schema.size()))
        return DataResult::failure(DataError::SCHEMA_MISMATCH,
                                   "Atomic V3 feature-schema pin is invalid");
    Sha256 hash;
    hash.update(FeatureInputDomain, sizeof(FeatureInputDomain) - 1);
    hash.update(schema.data(), schema.size());
    std::array<u8, 4> wire{};
    for (const auto& perspective : active)
    {
        write_u32_le(wire.data(), u32(perspective.size()));
        hash.update(wire.data(), wire.size());
        for (u32 index : perspective)
        {
            write_u32_le(wire.data(), index);
            hash.update(wire.data(), wire.size());
        }
    }
    const u8 bucket = u8(emissions[0].hm.networkBucket);
    hash.update(&bucket, 1);
    key = hash.digest();
    return DataResult::success();
}

namespace {

std::filesystem::path
external_sort_run_path(const std::filesystem::path& output, usize pass, usize index) {
    return output.parent_path()
         / (output.filename().string() + ".atomic-v3-sort-p" + std::to_string(pass) + "-"
            + std::to_string(index) + ".partial");
}

DataResult cleanup_external_sort_failure(DataResult                                primary,
                                         const std::vector<std::filesystem::path>& current,
                                         const std::vector<std::filesystem::path>& next = {}) {
    const DataResult currentCleanup = remove_paths(current);
    const DataResult nextCleanup    = remove_paths(next);
    if (!currentCleanup)
        return currentCleanup;
    if (!nextCleanup)
        return nextCleanup;
    return primary;
}

DataResult merge_atomic_v3_key_runs(const std::vector<std::filesystem::path>& inputs,
                                    const std::filesystem::path&              output,
                                    bool                                      rejectDuplicates,
                                    u64&                                      emittedRecords) {
    emittedRecords = 0;
    if (inputs.empty())
        return invalid("Atomic V3 external-sort merge requires at least one input run");

    // Create the output first so this helper owns at most inputs.size() + 1
    // descriptors. The production caller bounds inputs to ExternalSortMergeFanIn.
    std::FILE* destination = open_exclusive(output);
    if (!destination)
        return DataResult::failure(errno == EEXIST ? DataError::OUTPUT_EXISTS
                                                   : DataError::OPEN_FAILED,
                                   "Cannot create Atomic V3 external-sort merge output");

    struct Cursor {
        AtomicV3FeatureInputKey key{};
        usize                   run = 0;
    };
    const auto greater = [](const Cursor& lhs, const Cursor& rhs) {
        if (lhs.key != rhs.key)
            return lhs.key > rhs.key;
        return lhs.run > rhs.run;
    };
    std::priority_queue<Cursor, std::vector<Cursor>, decltype(greater)> queue(greater);
    std::vector<std::ifstream>                                          streams;
    streams.reserve(inputs.size());

    DataResult result = DataResult::success();
    for (usize i = 0; i < inputs.size() && result; ++i)
    {
        streams.emplace_back(inputs[i], std::ios::binary);
        if (!streams.back().is_open())
        {
            result = DataResult::failure(DataError::OPEN_FAILED,
                                         "Cannot open Atomic V3 external-sort merge input");
            break;
        }
        Cursor cursor{{}, i};
        streams.back().read(reinterpret_cast<char*>(cursor.key.data()), ExternalSortKeySize);
        if (streams.back().gcount() != std::streamsize(ExternalSortKeySize))
        {
            result = DataResult::failure(DataError::READ_FAILED,
                                         "Cannot prime Atomic V3 external-sort merge");
            break;
        }
        queue.push(cursor);
    }

    AtomicV3FeatureInputKey previous{};
    bool                    havePrevious = false;
    while (!queue.empty() && result)
    {
        const Cursor cursor = queue.top();
        queue.pop();
        const bool duplicate = havePrevious && cursor.key == previous;
        if (duplicate && rejectDuplicates)
        {
            result = invalid("Atomic V3 identity must be unique within its dataset role");
            break;
        }
        if (!duplicate)
        {
            result = write_exact(destination, cursor.key.data(), cursor.key.size(),
                                 "external-sort merge output");
            if (!result)
                break;
            previous     = cursor.key;
            havePrevious = true;
            ++emittedRecords;
        }

        Cursor next{{}, cursor.run};
        auto&  stream = streams[cursor.run];
        stream.read(reinterpret_cast<char*>(next.key.data()), ExternalSortKeySize);
        if (stream.gcount() == std::streamsize(ExternalSortKeySize))
            queue.push(next);
        else if (stream.gcount() != 0 || !stream.eof())
            result = DataResult::failure(DataError::READ_FAILED,
                                         "Cannot merge Atomic V3 external-sort input");
    }

    // All readers and the synchronized writer are closed before the caller is
    // allowed to delete any input run, which also makes Windows cleanup exact.
    for (auto& stream : streams)
    {
        stream.clear();
        stream.close();
        if (stream.fail() && result)
            result = DataResult::failure(DataError::CLOSE_FAILED,
                                         "Cannot close Atomic V3 external-sort merge input");
    }
    if (result)
        result = sync_file(destination, "external-sort merge output");
    if (DataResult closed = close_file(destination, "external-sort merge output");
        !closed && result)
        result = closed;

    if (!result)
    {
        const DataResult cleanup = remove_paths({output});
        return cleanup ? result : cleanup;
    }
    return DataResult::success();
}

DataResult sort_unique_atomic_v3_keys_impl(const std::filesystem::path& input,
                                           const std::filesystem::path& output,
                                           u64                          expectedRecords,
                                           bool                         rejectDuplicates,
                                           u64&                         uniqueRecords,
                                           usize                        chunkRecords,
                                           usize                        mergeFanIn) {
    uniqueRecords = 0;
    if (expectedRecords == 0 || multiply_overflows(expectedRecords, ExternalSortKeySize))
        return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                   "Atomic V3 external-sort record count is invalid");
    if (chunkRecords == 0 || chunkRecords > ExternalSortMemoryBytes / ExternalSortKeySize
        || mergeFanIn <= 1 || mergeFanIn > ExternalSortMergeFanIn)
        return invalid("Atomic V3 external-sort limits are invalid");
    std::error_code ec;
    const u64       inputBytes = std::filesystem::file_size(input, ec);
    if (ec || inputBytes != expectedRecords * ExternalSortKeySize)
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Atomic V3 external-sort input size is inconsistent");
    if (std::filesystem::exists(output, ec) || ec)
        return DataResult::failure(
          ec ? DataError::OPEN_FAILED : DataError::OUTPUT_EXISTS,
          "Atomic V3 external-sort output already exists or is inaccessible");

    std::ifstream source(input, std::ios::binary);
    if (!source)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot open Atomic V3 external-sort input");

    std::vector<std::filesystem::path> chunks;
    u64                                remaining = expectedRecords;
    while (remaining != 0)
    {
        const usize count = usize(std::min<u64>(remaining, u64(chunkRecords)));
        std::vector<AtomicV3FeatureInputKey> keys(count);
        source.read(reinterpret_cast<char*>(keys.data()),
                    std::streamsize(count * ExternalSortKeySize));
        if (source.gcount() != std::streamsize(count * ExternalSortKeySize))
        {
            return cleanup_external_sort_failure(
              DataResult::failure(DataError::READ_FAILED,
                                  "Cannot read complete Atomic V3 external-sort chunk"),
              chunks);
        }
        std::sort(keys.begin(), keys.end());
        const auto duplicate = std::adjacent_find(keys.begin(), keys.end());
        if (rejectDuplicates && duplicate != keys.end())
            return cleanup_external_sort_failure(
              invalid("Atomic V3 identity must be unique within its dataset role"), chunks);
        keys.erase(std::unique(keys.begin(), keys.end()), keys.end());

        const auto chunk = external_sort_run_path(output, 0, chunks.size());
        std::FILE* file  = open_exclusive(chunk);
        if (!file)
            return cleanup_external_sort_failure(
              DataResult::failure(errno == EEXIST ? DataError::OUTPUT_EXISTS
                                                  : DataError::OPEN_FAILED,
                                  "Cannot create Atomic V3 external-sort chunk"),
              chunks);
        DataResult result =
          write_exact(file, keys.data(), keys.size() * ExternalSortKeySize, "external-sort chunk");
        if (result)
            result = sync_file(file, "external-sort chunk");
        if (DataResult closed = close_file(file, "external-sort chunk"); !closed && result)
            result = closed;
        if (!result)
        {
            const DataResult chunkCleanup = remove_paths({chunk});
            if (!chunkCleanup)
                result = chunkCleanup;
            return cleanup_external_sort_failure(result, chunks);
        }
        chunks.push_back(chunk);
        remaining -= count;
    }
    char trailing = 0;
    if (source.read(&trailing, 1) || !source.eof())
        return cleanup_external_sort_failure(
          DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                              "Atomic V3 external-sort input has trailing bytes"),
          chunks);
    source.clear();
    source.close();
    if (source.fail())
        return cleanup_external_sort_failure(
          DataResult::failure(DataError::CLOSE_FAILED,
                              "Cannot close Atomic V3 external-sort input"),
          chunks);

    usize pass = 1;
    while (chunks.size() > mergeFanIn)
    {
        std::vector<std::filesystem::path> merged;
        merged.reserve(chunks.size() / mergeFanIn + usize(chunks.size() % mergeFanIn != 0));
        for (usize first = 0, index = 0; first < chunks.size(); ++index)
        {
            const usize last = first + std::min(mergeFanIn, chunks.size() - first);
            const std::vector<std::filesystem::path> inputs(chunks.begin() + first,
                                                            chunks.begin() + last);
            const auto destination    = external_sort_run_path(output, pass, index);
            u64        ignoredRecords = 0;
            if (DataResult result =
                  merge_atomic_v3_key_runs(inputs, destination, rejectDuplicates, ignoredRecords);
                !result)
                return cleanup_external_sort_failure(result, chunks, merged);
            merged.push_back(destination);
            first = last;
        }

        // Every next-pass run is already synchronized and closed. Retire the
        // previous pass only after the complete next pass exists.
        if (DataResult cleanup = remove_paths(chunks); !cleanup)
            return cleanup_external_sort_failure(cleanup, chunks, merged);
        chunks = std::move(merged);
        ++pass;
    }

    u64 finalRecords = 0;
    if (DataResult result =
          merge_atomic_v3_key_runs(chunks, output, rejectDuplicates, finalRecords);
        !result)
        return cleanup_external_sort_failure(result, chunks);

    if (DataResult cleanup = remove_paths(chunks); !cleanup)
    {
        const DataResult outputCleanup = remove_paths({output});
        return outputCleanup ? cleanup : outputCleanup;
    }
    uniqueRecords = finalRecords;
    return DataResult::success();
}

}  // namespace

DataResult sort_unique_atomic_v3_keys(const std::filesystem::path& input,
                                      const std::filesystem::path& output,
                                      u64                          expectedRecords,
                                      bool                         rejectDuplicates,
                                      u64&                         uniqueRecords) {
    return sort_unique_atomic_v3_keys_impl(
      input, output, expectedRecords, rejectDuplicates, uniqueRecords,
      ExternalSortMemoryBytes / ExternalSortKeySize, ExternalSortMergeFanIn);
}

#ifdef ATOMIC_V3_EXTERNAL_SORT_TEST_HOOKS
DataResult sort_unique_atomic_v3_keys_with_limits_for_testing(const std::filesystem::path& input,
                                                              const std::filesystem::path& output,
                                                              u64   expectedRecords,
                                                              bool  rejectDuplicates,
                                                              u64&  uniqueRecords,
                                                              usize chunkRecords,
                                                              usize mergeFanIn) {
    return sort_unique_atomic_v3_keys_impl(input, output, expectedRecords, rejectDuplicates,
                                           uniqueRecords, chunkRecords, mergeFanIn);
}
#endif

DataResult validate_atomic_v3_trajectory(const AtomicV3Trajectory& trajectory,
                                         std::optional<u32>        expectedMaximumPly) {
    if (trajectory.playedMoves.empty())
        return invalid("Atomic V3 trajectory must contain at least one played move");
    if (trajectory.samples.empty())
        return invalid("Atomic V3 trajectory must contain at least one retained sample");
    if (trajectory.playedMoves.size() > std::numeric_limits<u32>::max()
        || trajectory.samples.size() > std::numeric_limits<u32>::max())
        return invalid("Atomic V3 trajectory exceeds its uint32 entry domain");
    if (trajectory.terminalResult < -1 || trajectory.terminalResult > 1)
        return invalid("Atomic V3 terminal result must be -1, 0, or 1");
    if (trajectory.stopReason == AtomicV3StopReason::SCORE_DRAW_ADJUDICATION
        || trajectory.stopReason == AtomicV3StopReason::EVALUATION_RESIGNATION)
        return invalid("Atomic V3 release producer forbids score-draw and resignation stops");

    StateInfo rootState{};
    Position  position;
    if (const auto setError = position.set(trajectory.rootFen, trajectory.atomic960, &rootState))
        return invalid(std::string("Atomic V3 root FEN is invalid: ") + setError->what());
    if (!position.has_king(WHITE) || !position.has_king(BLACK))
        return invalid("Atomic V3 root must contain both kings");
    if (position.fen() != trajectory.rootFen)
        return invalid("Atomic V3 root FEN is not canonical");

    AtomicBinV2Position canonicalRoot{};
    if (DataResult encoded = encode_atomic_bin_v2_position(position, canonicalRoot); !encoded)
        return encoded;
    if (canonicalRoot != trajectory.rootPosition)
        return invalid("Atomic V3 root bytes do not match the canonical root FEN");

    usize previousPly = 0;
    bool  firstSample = true;
    for (const auto& sample : trajectory.samples)
    {
        if (sample.ply < 0 || u64(sample.ply) >= trajectory.playedMoves.size())
            return invalid("Atomic V3 retained sample ply is outside the played trajectory");
        const usize ply = usize(sample.ply);
        if (!firstSample && ply <= previousPly)
            return invalid("Atomic V3 retained sample plies must be strictly increasing");
        firstSample = false;
        previousPly = ply;
    }

    std::vector<StateInfo> states(trajectory.playedMoves.size());
    usize                  sampleIndex = 0;
    for (usize ply = 0; ply < trajectory.playedMoves.size(); ++ply)
    {
        const Atomic::Outcome preMoveOutcome = Atomic::outcome(position, true, 0);
        const bool            ignoredInsufficient =
          preMoveOutcome.termination == Atomic::Termination::InsufficientMaterial
          && !trajectory.adjudicateInsufficient;
        if (preMoveOutcome.terminal() && !ignoredInsufficient)
            return invalid("Atomic V3 trajectory contains a move after a claimable terminal");

        if (sampleIndex < trajectory.samples.size()
            && usize(trajectory.samples[sampleIndex].ply) == ply)
        {
            const auto& sample = trajectory.samples[sampleIndex++];
            if (sample.fen != position.fen())
                return invalid(
                  "Atomic V3 retained sample does not match its replayed pre-move position");
            const u32 expectedFlags =
              trajectory.atomic960 ? TRAINING_DATA_CHESS960 : NO_TRAINING_DATA_FLAGS;
            if (sample.flags != expectedFlags)
                return invalid("Atomic V3 retained sample has inconsistent Atomic960 flags");
            const int expectedResult =
              trajectory.terminalResult == 0
                ? 0
                : (position.side_to_move() == WHITE ? trajectory.terminalResult
                                                    : -trajectory.terminalResult);
            if (sample.result != expectedResult)
                return invalid("Atomic V3 retained sample result disagrees with terminal result");
            if (!MoveList<LEGAL>(position).contains(sample.move))
                return invalid("Atomic V3 retained searched move is not legal");
            AtomicBinV2Record encoded{};
            if (DataResult result = encode_atomic_bin_v2(sample, encoded); !result)
                return result;
        }

        Move played = Move::none();
        if (DataResult decoded = decode_atomic_bin_v2_move(trajectory.playedMoves[ply], played);
            !decoded)
            return decoded;
        if (!MoveList<LEGAL>(position).contains(played))
            return invalid("Atomic V3 played move is not legal during replay");
        position.do_move(played, states[ply], nullptr);
    }
    if (sampleIndex != trajectory.samples.size())
        return invalid("Atomic V3 retained sample mapping is incomplete");

    const Atomic::Outcome outcome = Atomic::outcome(position, true, 0);
    if (trajectory.stopReason == AtomicV3StopReason::MAXIMUM_PLY_DRAW)
    {
        if (!expectedMaximumPly || trajectory.playedMoves.size() != *expectedMaximumPly)
            return invalid(
              "Atomic V3 maximum-ply stop is not bound to the authenticated write_max_ply");
        const bool ignoredInsufficient =
          outcome.termination == Atomic::Termination::InsufficientMaterial
          && !trajectory.adjudicateInsufficient;
        if ((outcome.terminal() && !ignoredInsufficient) || trajectory.terminalResult != 0)
            return invalid("Atomic V3 maximum-ply stop must be a nonterminal draw label");
    }
    else
    {
        if (trajectory.stopReason == AtomicV3StopReason::INSUFFICIENT_MATERIAL
            && !trajectory.adjudicateInsufficient)
            return invalid(
              "Atomic V3 trajectory stopped on disabled insufficient-material adjudication");
        const auto expectedStop = stop_reason(outcome.termination);
        if (!expectedStop || *expectedStop != trajectory.stopReason)
            return invalid("Atomic V3 stop reason disagrees with replayed Atomic outcome");
        if (white_result(outcome) != trajectory.terminalResult)
            return invalid("Atomic V3 terminal result disagrees with replayed Atomic outcome");
    }
    return DataResult::success();
}

AtomicV3TrajectoryLedgerStager::AtomicV3TrajectoryLedgerStager(
  std::filesystem::path privateDirectory_,
  std::string           basename,
  AtomicV3DatasetRole   role_,
  u64                   splitSeed_,
  u64                   validationThreshold_,
  u32                   expectedMaximumPly_) :
    privateDirectory(std::move(privateDirectory_)),
    entriesPath(privateDirectory / (basename + ".entries.partial")),
    movesPath(privateDirectory / (basename + ".moves.partial")),
    groupsPath(privateDirectory / (basename + ".groups.partial")),
    sortedGroupsPath(privateDirectory / (basename + ".groups.sorted.partial")),
    role(role_),
    splitSeed(splitSeed_),
    validationThreshold(validationThreshold_),
    expectedMaximumPly(expectedMaximumPly_) {}

AtomicV3TrajectoryLedgerStager::~AtomicV3TrajectoryLedgerStager() {
    if (!finalized)
        (void) abort();
}

DataResult AtomicV3TrajectoryLedgerStager::open_staging() {
    if (entriesFile && movesFile && groupsFile)
        return DataResult::success();
    if (finalized || aborted)
        return DataResult::failure(DataError::SINK_CLOSED,
                                   "Cannot reopen a closed Atomic V3 trajectory ledger");

    entriesFile = open_exclusive(entriesPath);
    if (!entriesFile)
        return DataResult::failure(errno == EEXIST ? DataError::OUTPUT_EXISTS
                                                   : DataError::OPEN_FAILED,
                                   "Cannot create Atomic V3 trajectory-entry staging file: "
                                     + std::string(std::strerror(errno)));
    movesFile = open_exclusive(movesPath);
    if (!movesFile)
    {
        const int error = errno;
        (void) close_file(entriesFile, "trajectory-entry staging file");
        std::error_code ec;
        std::filesystem::remove(entriesPath, ec);
        return DataResult::failure(
          error == EEXIST ? DataError::OUTPUT_EXISTS : DataError::OPEN_FAILED,
          "Cannot create Atomic V3 move staging file: " + std::string(std::strerror(error)));
    }
    groupsFile = open_exclusive(groupsPath);
    if (!groupsFile)
    {
        const int error = errno;
        (void) close_file(entriesFile, "trajectory-entry staging file");
        (void) close_file(movesFile, "move staging file");
        std::error_code ec;
        std::filesystem::remove(entriesPath, ec);
        ec.clear();
        std::filesystem::remove(movesPath, ec);
        return DataResult::failure(
          error == EEXIST ? DataError::OUTPUT_EXISTS : DataError::OPEN_FAILED,
          "Cannot create Atomic V3 split-group staging file: " + std::string(std::strerror(error)));
    }
    return DataResult::success();
}

DataResult AtomicV3TrajectoryLedgerStager::append(const AtomicV3Trajectory& trajectory,
                                                  u64                       expectedFirstRecord) {
    if (expectedFirstRecord != recordCount)
        return invalid("Atomic V3 ledger record range is not contiguous with its dataset");
    if (DataResult valid = validate_atomic_v3_trajectory(
          trajectory, expectedMaximumPly ? std::optional<u32>(expectedMaximumPly) : std::nullopt);
        !valid)
        return valid;

    const auto group = atomic_v3_split_group_id(trajectory.rootPosition, trajectory.atomic960,
                                                trajectory.playedMoves);
    if (atomic_v3_partition_role(splitSeed, validationThreshold, group) != role)
        return invalid("Atomic V3 trajectory was offered to the wrong content-hash role");
    if (add_overflows(recordCount, u64(trajectory.samples.size()))
        || add_overflows(moveCount, u64(trajectory.playedMoves.size()))
        || trajectoryCount == std::numeric_limits<u64>::max())
        return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                   "Atomic V3 trajectory ledger count overflow");
    if (DataResult opened = open_staging(); !opened)
        return opened;

    std::array<u8, EntrySize> entry{};
    std::copy(group.begin(), group.end(), entry.begin());
    std::copy(trajectory.rootPosition.begin(), trajectory.rootPosition.end(), entry.begin() + 32);
    write_u64_le(entry.data() + 80, recordCount);
    write_u32_le(entry.data() + 88, u32(trajectory.samples.size()));
    write_u32_le(entry.data() + 92, u32(trajectory.playedMoves.size()));
    write_u64_le(entry.data() + 96, moveCount);
    entry[104] = trajectory.terminalResult == -1 ? 0xFF : u8(trajectory.terminalResult);
    entry[105] = trajectory.atomic960 ? 1 : 0;
    entry[106] = u8(trajectory.stopReason);

    if (DataResult written =
          write_exact(groupsFile, group.data(), group.size(), "split-group identity");
        !written)
        return written;
    if (DataResult written =
          write_exact(entriesFile, entry.data(), entry.size(), "trajectory entry");
        !written)
        return written;
    std::array<u8, 4> encodedMove{};
    for (u32 move : trajectory.playedMoves)
    {
        write_u32_le(encodedMove.data(), move);
        if (DataResult written =
              write_exact(movesFile, encodedMove.data(), encodedMove.size(), "trajectory move");
            !written)
            return written;
    }

    recordCount += u64(trajectory.samples.size());
    moveCount += u64(trajectory.playedMoves.size());
    ++trajectoryCount;
    return DataResult::success();
}

DataResult AtomicV3TrajectoryLedgerStager::close_staging() {
    DataResult first = DataResult::success();
    if (entriesFile)
    {
        if (DataResult synced = sync_file(entriesFile, "trajectory-entry staging file"); !synced)
            first = synced;
        if (DataResult closed = close_file(entriesFile, "trajectory-entry staging file");
            !closed && first)
            first = closed;
    }
    if (movesFile)
    {
        if (DataResult synced = sync_file(movesFile, "move staging file"); !synced && first)
            first = synced;
        if (DataResult closed = close_file(movesFile, "move staging file"); !closed && first)
            first = closed;
    }
    if (groupsFile)
    {
        if (DataResult synced = sync_file(groupsFile, "split-group staging file"); !synced && first)
            first = synced;
        if (DataResult closed = close_file(groupsFile, "split-group staging file");
            !closed && first)
            first = closed;
    }
    return first;
}

DataResult AtomicV3TrajectoryLedgerStager::finalize(const std::filesystem::path& finalPath,
                                                    std::string_view             manifestSha256,
                                                    AtomicV3LedgerMetadata&      metadata) {
    metadata = {};
    if (finalized || aborted)
        return DataResult::failure(DataError::SINK_CLOSED,
                                   "Cannot finalize a closed Atomic V3 trajectory ledger");
    if (recordCount == 0 || trajectoryCount == 0 || moveCount == 0)
        return DataResult::failure(DataError::EMPTY_DATASET,
                                   "Atomic V3 trajectory ledgers cannot be empty");
    if (multiply_overflows(trajectoryCount, EntrySize) || multiply_overflows(moveCount, usize(4))
        || add_overflows(HeaderSize, trajectoryCount * EntrySize)
        || add_overflows(HeaderSize + trajectoryCount * EntrySize, moveCount * 4))
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Atomic V3 trajectory ledger size overflows uint64");
    if (DataResult closed = close_staging(); !closed)
        return closed;

    u64 uniqueGroups = 0;
    if (DataResult unique = sort_unique_atomic_v3_keys(groupsPath, sortedGroupsPath,
                                                       trajectoryCount, true, uniqueGroups);
        !unique)
        return unique;
    if (uniqueGroups != trajectoryCount)
        return invalid("Atomic V3 split_group_id identities are not unique");

    std::error_code sizeError;
    const u64       entriesBytes = std::filesystem::file_size(entriesPath, sizeError);
    if (sizeError || entriesBytes != trajectoryCount * EntrySize)
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Atomic V3 staged trajectory-entry size is inconsistent");
    const u64 movesBytes = std::filesystem::file_size(movesPath, sizeError);
    if (sizeError || movesBytes != moveCount * 4)
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Atomic V3 staged move-stream size is inconsistent");

    std::array<u8, 32> manifestDigest{};
    std::array<u8, 32> schemaDigest{};
    if (!decode_lower_hex(manifestSha256, manifestDigest.data(), manifestDigest.size()))
        return DataResult::failure(DataError::SCHEMA_MISMATCH,
                                   "Atomic V3 manifest SHA-256 must be canonical lowercase hex");
    if (!decode_lower_hex(AtomicV3TrajectorySchemaSha256Hex, schemaDigest.data(),
                          schemaDigest.size()))
        return DataResult::failure(DataError::SCHEMA_MISMATCH,
                                   "Atomic V3 trajectory schema pin is invalid");

    std::array<u8, HeaderSize> header{};
    std::copy(TrajectoryMagic.begin(), TrajectoryMagic.end(), header.begin());
    write_u16_le(header.data() + 8, 1);
    write_u16_le(header.data() + 10, u16(HeaderSize));
    write_u32_le(header.data() + 12, 0x01020304U);
    write_u32_le(header.data() + 16, u32(EntrySize));
    write_u32_le(header.data() + 20, u32(role));
    std::copy(schemaDigest.begin(), schemaDigest.end(), header.begin() + 24);
    std::copy(manifestDigest.begin(), manifestDigest.end(), header.begin() + 56);
    const auto& dataSchema = atomic_bin_v2_schema_sha256();
    std::copy(dataSchema.begin(), dataSchema.end(), header.begin() + 88);
    write_u64_le(header.data() + 120, recordCount);
    write_u64_le(header.data() + 128, trajectoryCount);
    write_u64_le(header.data() + 136, moveCount);
    write_u64_le(header.data() + 144, HeaderSize);
    write_u64_le(header.data() + 152, HeaderSize + trajectoryCount * EntrySize);

    std::FILE* output = open_exclusive(finalPath);
    if (!output)
        return DataResult::failure(
          errno == EEXIST ? DataError::OUTPUT_EXISTS : DataError::OPEN_FAILED,
          "Cannot create Atomic V3 trajectory ledger: " + std::string(std::strerror(errno)));

    DataResult result  = write_exact(output, header.data(), header.size(), "trajectory header");
    std::FILE* entries = nullptr;
    std::FILE* moves   = nullptr;
    if (result)
    {
        entries = open_read(entriesPath);
        if (!entries)
            result = DataResult::failure(DataError::OPEN_FAILED,
                                         "Cannot reopen Atomic V3 trajectory entries");
    }
    if (result)
        result = copy_stream(entries, output, "trajectory entries");
    if (entries)
        (void) std::fclose(entries);
    if (result)
    {
        moves = open_read(movesPath);
        if (!moves)
            result = DataResult::failure(DataError::OPEN_FAILED,
                                         "Cannot reopen Atomic V3 trajectory moves");
    }
    if (result)
        result = copy_stream(moves, output, "trajectory moves");
    if (moves)
        (void) std::fclose(moves);
    if (result)
        result = sync_file(output, "trajectory ledger");
    if (DataResult closed = close_file(output, "trajectory ledger"); !closed && result)
        result = closed;

    if (!result)
    {
        std::error_code ec;
        std::filesystem::remove(finalPath, ec);
        return result;
    }

    const u64   expectedSize = HeaderSize + trajectoryCount * EntrySize + moveCount * 4;
    std::string checksum;
    u64         bytes = 0;
    if (DataResult hashed = sha256_file(finalPath, checksum, bytes); !hashed)
    {
        std::error_code ec;
        std::filesystem::remove(finalPath, ec);
        return hashed;
    }
    if (bytes != expectedSize)
    {
        std::error_code ec;
        std::filesystem::remove(finalPath, ec);
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Atomic V3 trajectory ledger size is inconsistent");
    }

    finalizedPath = finalPath;
    finalized     = true;
    metadata = {finalPath, recordCount, trajectoryCount, moveCount, bytes, std::move(checksum)};
    // Finalization owns all private staging files. Keep only the authenticated
    // public ledger on success; explicit abort() remains able to remove that
    // ledger when a higher-level multi-artifact publication rolls back.
    for (const auto& path : {entriesPath, movesPath, groupsPath, sortedGroupsPath})
    {
        std::error_code removeError;
        if (!std::filesystem::remove(path, removeError) || removeError)
        {
            finalized = false;
            metadata  = {};
            return DataResult::failure(DataError::ABORT_FAILED,
                                       "Cannot remove Atomic V3 trajectory staging file "
                                         + path.string() + ": " + removeError.message());
        }
    }
    return DataResult::success();
}

DataResult AtomicV3TrajectoryLedgerStager::abort() {
    if (aborted)
        return DataResult::success();
    DataResult first = DataResult::success();
    if (DataResult closed = close_file(entriesFile, "trajectory-entry staging file"); !closed)
        first = closed;
    if (DataResult closed = close_file(movesFile, "move staging file"); !closed && first)
        first = closed;
    if (DataResult closed = close_file(groupsFile, "split-group staging file"); !closed && first)
        first = closed;

    for (const auto& path : {entriesPath, movesPath, groupsPath, sortedGroupsPath, finalizedPath})
        if (!path.empty())
        {
            std::error_code ec;
            std::filesystem::remove(path, ec);
            if (ec && first)
                first = DataResult::failure(DataError::ABORT_FAILED,
                                            "Cannot remove Atomic V3 staged output " + path.string()
                                              + ": " + ec.message());
        }
    aborted = true;
    return first;
}

}  // namespace Stockfish::Data
