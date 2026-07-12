/* Focused tests for the Atomic BIN V2 exclusive sink and SHA-256 helper. */

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <string>
#include <string_view>
#include <utility>

#include "attacks.h"
#include "bitboard.h"
#include "data/atomic_bin_v2_sink.h"
#include "data/sha256.h"
#include "position.h"
#include "tt.h"

#ifdef _WIN32
    #ifndef NOMINMAX
        #define NOMINMAX
    #endif
    #ifndef WIN32_LEAN_AND_MEAN
        #define WIN32_LEAN_AND_MEAN
    #endif
    #include <process.h>
    #include <windows.h>
#else
    #include <unistd.h>
#endif

using namespace Stockfish;

TTEntry* TranspositionTable::first_entry(Key) const { return nullptr; }

namespace {

constexpr const char*      StartFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
constexpr std::string_view OneRecordSha256 =
  "50cf1665bdc975ea1e2abab21dcb926ec63223e0b3f853ba56862c2c1e83d7a6";

int failures = 0;

void expect(bool condition, std::string_view label) {
    if (!condition)
    {
        std::cerr << "FAIL " << label << '\n';
        ++failures;
    }
}

Data::TrainingDataSample sample(Move move = Move(SQ_E2, SQ_E4)) {
    return {StartFEN, -123, move, 42, -1, Data::NO_TRAINING_DATA_FLAGS};
}

std::filesystem::path temporary_path(std::string_view suffix) {
    static std::atomic<u64> sequence{0};
#ifdef _WIN32
    const auto process = ::_getpid();
#else
    const auto process = ::getpid();
#endif
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    return std::filesystem::temp_directory_path()
         / ("atomic-stockfish-v2-sink-" + std::to_string(process) + "-" + std::to_string(nonce)
            + "-" + std::to_string(sequence.fetch_add(1, std::memory_order_relaxed))
            + std::string(suffix));
}

void remove_ignored(const std::filesystem::path& path) {
    std::error_code ignored;
    std::filesystem::remove(path, ignored);
}

bool read_bytes(const std::filesystem::path& path, u64 offset, void* bytes, std::size_t size) {
#ifdef _WIN32
    const HANDLE handle = ::CreateFileW(path.c_str(), GENERIC_READ,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        return false;
    LARGE_INTEGER position{};
    position.QuadPart  = static_cast<LONGLONG>(offset);
    DWORD      read    = 0;
    const bool success = size <= std::numeric_limits<DWORD>::max()
                      && ::SetFilePointerEx(handle, position, nullptr, FILE_BEGIN)
                      && ::ReadFile(handle, bytes, DWORD(size), &read, nullptr)
                      && std::size_t(read) == size;
    ::CloseHandle(handle);
    return success;
#else
    std::ifstream input(path, std::ios::binary);
    input.seekg(std::streamoff(offset));
    return bool(input.read(reinterpret_cast<char*>(bytes), std::streamsize(size)));
#endif
}

bool visible_file_size(const std::filesystem::path& path, u64& size) {
    size = 0;
#ifdef _WIN32
    const HANDLE handle = ::CreateFileW(path.c_str(), GENERIC_READ,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        return false;
    LARGE_INTEGER value{};
    const bool    success = ::GetFileSizeEx(handle, &value) && value.QuadPart >= 0;
    ::CloseHandle(handle);
    if (success)
        size = u64(value.QuadPart);
    return success;
#else
    std::error_code error;
    const auto      value = std::filesystem::file_size(path, error);
    if (error)
        return false;
    size = u64(value);
    return true;
#endif
}

bool read_header(const std::filesystem::path& path, Data::AtomicBinV2Header& header) {
    return read_bytes(path, 0, header.data(), header.size());
}

bool read_record(const std::filesystem::path& path, Data::AtomicBinV2Record& record) {
    return read_bytes(path, Data::AtomicBinV2HeaderSize, record.data(), record.size());
}

void test_sha256_vectors_and_file() {
    Data::Sha256 empty;
    expect(empty.hex_digest() == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
           "SHA-256 empty vector");

    Data::Sha256 abc;
    abc.update("abc");
    expect(abc.hex_digest() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
           "SHA-256 abc vector");
    abc.update("def");
    expect(abc.hex_digest() == "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721",
           "SHA-256 digest finalizes a copy and update continues");

    constexpr std::array<std::pair<std::size_t, std::string_view>, 5> PaddingVectors = {{
      {55, "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"},
      {56, "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"},
      {63, "7d3e74a05d7db15bce4ad9ec0658ea98e3f06eeecf16b4c6fff2da457ddc2f34"},
      {64, "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"},
      {65, "635361c48bb9eab14198e76ea8ab7f1a41685d6ad62aa9146d301d4f17eb0ae0"},
    }};
    for (const auto& [length, expected] : PaddingVectors)
    {
        Data::Sha256      boundary;
        const std::string input(length, 'a');
        boundary.update(input);
        expect(boundary.hex_digest() == expected, "SHA-256 padding boundary vector");
    }

    Data::Sha256      millionA;
    const std::string thousandA(1000, 'a');
    for (int index = 0; index < 1000; ++index)
        millionA.update(thousandA);
    expect(millionA.hex_digest()
             == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0",
           "SHA-256 million-a vector");

    const auto file = temporary_path("-hash.bin");
    remove_ignored(file);
    {
        std::ofstream output(file, std::ios::binary | std::ios::trunc);
        output.write("abc", 3);
        expect(bool(output), "SHA-256 fixture writes");
    }

    std::string hex    = "must-be-cleared";
    u64         size   = 999;
    const auto  result = Data::sha256_file(file, hex, size);
    expect(bool(result), "SHA-256 path helper succeeds");
    expect(size == 3, "SHA-256 path helper reports size");
    expect(hex == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
           "SHA-256 path helper digest");
    expect(std::all_of(hex.begin(), hex.end(),
                       [](char value) {
                           return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f');
                       }),
           "SHA-256 helper emits lowercase hex");

    remove_ignored(file);
    const auto missing = Data::sha256_file(file, hex, size);
    expect(!missing && missing.error == Data::DataError::OPEN_FAILED,
           "SHA-256 missing file rejected");
    expect(hex.empty() && size == 0, "failed SHA-256 path output resets");

    hex = "must-be-cleared";
    expect(Data::sha256_file_descriptor(-1, 0, hex).error == Data::DataError::OPEN_FAILED,
           "SHA-256 invalid descriptor rejected");
    expect(hex.empty(), "failed SHA-256 descriptor output resets");
}

void test_empty_and_invalid_create_nothing() {
    const auto emptyPath   = temporary_path("-empty.atbin");
    const auto invalidPath = temporary_path("-invalid.atbin");
    remove_ignored(emptyPath);
    remove_ignored(invalidPath);

    {
        Data::AtomicBinV2Sink empty(emptyPath);
        expect(empty.finalize().error == Data::DataError::EMPTY_DATASET,
               "empty V2 dataset rejected");
        expect(!std::filesystem::exists(emptyPath), "empty V2 sink creates no file");
        expect(empty.append(sample()).error == Data::DataError::SINK_CLOSED,
               "empty finalization closes sink");
        expect(bool(empty.abort()), "empty V2 sink aborts");
    }

    {
        Data::AtomicBinV2Sink invalid(invalidPath);
        auto                  bad = sample(Move::none());
        expect(invalid.append(bad).error == Data::DataError::INVALID_MOVE,
               "invalid first V2 sample rejected");
        expect(!std::filesystem::exists(invalidPath), "invalid first V2 sample creates no file");
        expect(bool(invalid.append(sample())), "sink remains usable after input rejection");
        expect(std::filesystem::exists(invalidPath), "later valid V2 sample creates file");
        expect(bool(invalid.abort()), "valid sample after rejection aborts");
        expect(!std::filesystem::exists(invalidPath), "abort removes recovered partial output");
    }

    remove_ignored(emptyPath);
    remove_ignored(invalidPath);
}

void test_finalize_header_size_and_hash() {
    const auto output = temporary_path("-golden.atbin");
    remove_ignored(output);

    {
        Data::AtomicBinV2Sink sink(output);
        expect(sink.output_path() == output, "V2 sink exposes output path");
        expect(bool(sink.append(sample())), "V2 sink appends golden record");
        expect(sink.records_written() == 1, "V2 sink record count before finalize");
        expect(sink.finalized_size() == 0 && sink.sha256_hex().empty(),
               "finalized metadata absent before finalize");
        u64 provisionalSize = 0;
        expect(visible_file_size(output, provisionalSize)
                 && provisionalSize == Data::AtomicBinV2HeaderSize + Data::AtomicBinV2RecordSize,
               "provisional V2 file has exact size");

        Data::AtomicBinV2Header provisional{};
        u64                     count = 999;
        expect(read_header(output, provisional), "provisional V2 header readable");
        expect(Data::decode_atomic_bin_v2_header(provisional, count).error
                 == Data::DataError::EMPTY_DATASET,
               "owned provisional V2 header has zero count");

        expect(bool(sink.finalize()), "V2 sink finalizes");
        expect(sink.records_written() == 1, "V2 finalized record count");
        expect(sink.finalized_size() == 160, "V2 finalized size getter");
        expect(sink.sha256_hex() == OneRecordSha256, "V2 finalized golden SHA-256");
        expect(bool(sink.finalize()), "V2 finalize is idempotent");
        expect(sink.append(sample()).error == Data::DataError::SINK_CLOSED,
               "finalized V2 sink rejects append");
        expect(sink.abort().error == Data::DataError::SINK_CLOSED,
               "finalized V2 sink rejects abort");
    }

    Data::AtomicBinV2Header finalHeader{};
    u64                     finalCount = 0;
    expect(read_header(output, finalHeader), "final V2 header readable");
    expect(bool(Data::decode_atomic_bin_v2_header(finalHeader, finalCount)) && finalCount == 1,
           "final V2 header contains exact count");
    expect(bool(Data::validate_atomic_bin_v2_file_size(finalCount,
                                                       u64(std::filesystem::file_size(output)))),
           "final V2 file size validates");

    Data::AtomicBinV2Record  record{};
    Data::TrainingDataSample decoded;
    expect(read_record(output, record), "final V2 record readable");
    expect(bool(Data::decode_atomic_bin_v2(record, decoded)), "final V2 record decodes");
    expect(decoded.fen == StartFEN && decoded.score == -123 && decoded.move == Move(SQ_E2, SQ_E4)
             && decoded.ply == 42 && decoded.result == -1
             && decoded.flags == Data::NO_TRAINING_DATA_FLAGS,
           "final V2 record fields preserved");

    std::string independentHash;
    u64         independentSize = 0;
    expect(bool(Data::sha256_file(output, independentHash, independentSize)),
           "final V2 file rehashes by path");
    expect(independentSize == 160 && independentHash == OneRecordSha256,
           "same-descriptor sink hash matches independent path hash");

    {
        Data::AtomicBinV2Sink duplicate(output);
        expect(duplicate.append(sample()).error == Data::DataError::OUTPUT_EXISTS,
               "V2 overwrite and append refused atomically");
    }
    expect(std::filesystem::file_size(output) == 160,
           "failed V2 duplicate leaves finalized file intact");
    expect(bool(Data::sha256_file(output, independentHash, independentSize))
             && independentHash == OneRecordSha256,
           "failed V2 duplicate leaves bytes intact");

    remove_ignored(output);
}

void test_abort_destructor_and_open_error() {
    const auto partial           = temporary_path("-partial.atbin");
    const auto scoped            = temporary_path("-scoped.atbin");
    const auto finalizedRollback = temporary_path("-finalized-rollback.atbin");
    const auto finalizedReplaced = temporary_path("-finalized-replaced.atbin");
    const auto finalizedMovedOwn = temporary_path("-finalized-moved-own.atbin");
    const auto missingParent     = temporary_path("-missing-parent");
    const auto missingOutput     = missingParent / "output.atbin";
#ifndef _WIN32
    const auto replaced = temporary_path("-replaced.atbin");
    const auto movedOwn = temporary_path("-moved-own.atbin");
#endif
    remove_ignored(partial);
    remove_ignored(scoped);
    remove_ignored(finalizedRollback);
    remove_ignored(finalizedReplaced);
    remove_ignored(finalizedMovedOwn);
    remove_ignored(missingParent);
#ifndef _WIN32
    remove_ignored(replaced);
    remove_ignored(movedOwn);
#endif

    {
        Data::AtomicBinV2Sink sink(partial);
        expect(bool(sink.append(sample())), "partial V2 sink appends");
        expect(std::filesystem::exists(partial), "partial V2 file exists while owned");
        expect(bool(sink.abort()), "partial V2 sink aborts");
        expect(!std::filesystem::exists(partial), "V2 abort removes owned partial file");
        expect(bool(sink.abort()), "V2 abort is idempotent");
        expect(sink.finalize().error == Data::DataError::SINK_CLOSED,
               "aborted V2 sink cannot finalize");
    }

    {
        Data::AtomicBinV2Sink sink(scoped);
        expect(bool(sink.append(sample())), "scoped V2 sink appends");
        expect(std::filesystem::exists(scoped), "scoped V2 file exists before destruction");
    }
    expect(!std::filesystem::exists(scoped),
           "V2 sink destructor removes its unfinished owned file");

    {
        Data::AtomicBinV2Sink sink(finalizedRollback);
        expect(bool(sink.append(sample())) && bool(sink.finalize()),
               "V2 rollback fixture finalizes");
        expect(bool(sink.remove_finalized_owned()), "finalized V2 owned shard rolls back");
        expect(!std::filesystem::exists(finalizedRollback),
               "finalized V2 rollback removes owned shard");
        expect(bool(sink.remove_finalized_owned()), "finalized V2 rollback is idempotent");
    }

    {
        Data::AtomicBinV2Sink sink(missingOutput);
        expect(sink.append(sample()).error == Data::DataError::OPEN_FAILED,
               "V2 sink rejects missing output directory");
        expect(!std::filesystem::exists(missingOutput), "V2 open failure creates no output");
    }

#ifndef _WIN32
    {
        Data::AtomicBinV2Sink sink(replaced);
        expect(bool(sink.append(sample())), "replacement-guard V2 sink appends");
        std::error_code renameError;
        std::filesystem::rename(replaced, movedOwn, renameError);
        expect(!renameError, "replacement-guard moves owned inode");
        {
            std::ofstream replacement(replaced, std::ios::binary | std::ios::trunc);
            replacement << "foreign replacement";
        }
        expect(sink.abort().error == Data::DataError::ABORT_FAILED,
               "V2 abort refuses to remove a replacement path");
        expect(std::filesystem::exists(replaced), "foreign replacement survives V2 abort");
        expect(std::filesystem::exists(movedOwn), "moved owned inode is not confused with path");
    }

#endif

    {
        Data::AtomicBinV2Sink sink(finalizedReplaced);
        expect(bool(sink.append(sample())) && bool(sink.finalize()),
               "finalized replacement-guard fixture finalizes");
        std::error_code renameError;
        std::filesystem::rename(finalizedReplaced, finalizedMovedOwn, renameError);
        expect(!renameError, "finalized replacement-guard moves owned inode");
        {
            std::ofstream replacement(finalizedReplaced, std::ios::binary | std::ios::trunc);
            replacement << "foreign finalized replacement";
        }
        expect(sink.remove_finalized_owned().error == Data::DataError::ABORT_FAILED,
               "finalized V2 rollback refuses replacement path");
        expect(std::filesystem::exists(finalizedReplaced),
               "foreign finalized replacement survives rollback");
        expect(std::filesystem::exists(finalizedMovedOwn),
               "finalized moved owned inode is not confused with path");
    }

    remove_ignored(partial);
    remove_ignored(scoped);
    remove_ignored(finalizedRollback);
    remove_ignored(finalizedReplaced);
    remove_ignored(finalizedMovedOwn);
    remove_ignored(missingParent);
#ifndef _WIN32
    remove_ignored(replaced);
    remove_ignored(movedOwn);
#endif
}

}  // namespace

int main() {
    Bitboards::init();
    Attacks::init();
    Position::init();

    test_sha256_vectors_and_file();
    test_empty_and_invalid_create_nothing();
    test_finalize_header_size_and_hash();
    test_abort_destructor_and_open_error();

    if (failures != 0)
    {
        std::cerr << failures << " Atomic BIN V2 sink test(s) failed\n";
        return 1;
    }

    std::cout << "Atomic BIN V2 sink tests passed\n";
    return 0;
}
