/* End-to-end tests for the authenticated streaming Atomic BIN V2 reader. */

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include "data/atomic_bin_v2_reader.h"
#include "data/sha256.h"
#include "movegen.h"
#include "position.h"
#include "tt.h"

#ifndef _WIN32
    #include <signal.h>
    #include <sys/stat.h>
    #include <sys/wait.h>
    #include <unistd.h>
#endif

using namespace Stockfish;

// Position::do_move() only prefetches through a non-null TT pointer. These
// standalone reader tests never provide one, so keep the test link isolated
// from the complete threaded TT subsystem.
TTEntry* TranspositionTable::first_entry(Key) const { return nullptr; }

namespace {

int failures = 0;

void check(bool condition, std::string_view name) {
    if (!condition)
    {
        ++failures;
        std::cerr << "FAIL: " << name << '\n';
    }
}

std::vector<u8> decode_hex(std::string_view value) {
    const auto nibble = [](char byte) -> int {
        if (byte >= '0' && byte <= '9')
            return byte - '0';
        if (byte >= 'a' && byte <= 'f')
            return byte - 'a' + 10;
        if (byte >= 'A' && byte <= 'F')
            return byte - 'A' + 10;
        return -1;
    };
    std::vector<u8> bytes;
    if (value.size() % 2)
        return bytes;
    bytes.reserve(value.size() / 2);
    for (std::size_t index = 0; index < value.size(); index += 2)
    {
        const int high = nibble(value[index]);
        const int low  = nibble(value[index + 1]);
        if (high < 0 || low < 0)
            return {};
        bytes.push_back(u8((high << 4) | low));
    }
    return bytes;
}

struct TempDirectory {
    std::filesystem::path path;
    explicit TempDirectory(std::string_view name) {
        const auto nonce = std::chrono::high_resolution_clock::now().time_since_epoch().count();
        path             = std::filesystem::temp_directory_path()
             / ("atomic-bin-v2-reader-" + std::string(name) + "-" + std::to_string(nonce));
        std::filesystem::create_directories(path);
    }
    ~TempDirectory() {
        std::error_code error;
        std::filesystem::remove_all(path, error);
    }
};

struct CurrentDirectoryGuard {
    std::filesystem::path original = std::filesystem::current_path();
    ~CurrentDirectoryGuard() {
        std::error_code error;
        std::filesystem::current_path(original, error);
    }
};

struct StationaryFixture {
    std::string legalFen;
    std::string illegalFen;
    std::string move;
    std::string flags;
};

StationaryFixture load_stationary_fixture() {
    const std::vector<std::filesystem::path> candidates = {
      "tests/fixtures/atomic-bin-v2/stationary-king-castling.txt",
      "../tests/fixtures/atomic-bin-v2/stationary-king-castling.txt"};
    std::ifstream input;
    for (const auto& candidate : candidates)
    {
        input.open(candidate);
        if (input)
            break;
        input.clear();
    }
    StationaryFixture fixture;
    std::string       line;
    while (std::getline(input, line))
    {
        const auto separator = line.find('=');
        if (separator == std::string::npos)
            continue;
        const std::string key   = line.substr(0, separator);
        const std::string value = line.substr(separator + 1);
        if (key == "legal_fen")
            fixture.legalFen = value;
        else if (key == "illegal_in_check_fen")
            fixture.illegalFen = value;
        else if (key == "move")
            fixture.move = value;
        else if (key == "flags")
            fixture.flags = value;
    }
    return fixture;
}

Data::TrainingDataSample ordinary_sample(int result = 1) {
    Data::TrainingDataSample sample;
    sample.fen    = "7k/8/8/8/8/8/4P3/K7 w - - 0 1";
    sample.move   = Move(SQ_E2, SQ_E3);
    sample.score  = 31;
    sample.ply    = 8;
    sample.result = result;
    return sample;
}

Data::AtomicBinV2Manifest metadata_for(const std::filesystem::path& manifestPath,
                                       const std::filesystem::path& shardPath,
                                       u64                          records,
                                       u64                          draws,
                                       std::string                  hash) {
    Data::AtomicBinV2Manifest manifest;
    manifest.manifestPath               = manifestPath;
    manifest.engineCommit               = "0123456789abcdef0123456789abcdef01234567";
    manifest.engineVersion              = "Atomic-Stockfish reader test";
    manifest.networkPath                = manifestPath.parent_path() / "atomic.nnue";
    manifest.networkSha256              = std::string(64, '1');
    manifest.resolvedSeed               = 9;
    manifest.threads                    = 1;
    manifest.hashMb                     = 16;
    manifest.options.searchDepthMin     = 3;
    manifest.options.searchDepthMax     = 3;
    manifest.options.evalLimit          = 3000;
    manifest.options.evalDiffLimit      = 64000;
    manifest.options.randomMoveMinPly   = 1;
    manifest.options.randomMoveMaxPly   = 24;
    manifest.options.randomMoveCount    = 5;
    manifest.options.randomMultiPv      = 5;
    manifest.options.randomMultiPvDiff  = 100;
    manifest.options.randomMultiPvDepth = 3;
    manifest.options.writeMinPly        = 5;
    manifest.options.writeMaxPly        = 400;
    manifest.options.requestedRecords   = records;
    manifest.options.recordsPerShard    = records;
    manifest.options.keepDraws          = "0.5";
    manifest.records                    = records;
    manifest.draws                      = draws;
    Data::AtomicBinV2ManifestShard shard;
    shard.path    = shardPath;
    shard.records = records;
    shard.bytes   = Data::AtomicBinV2HeaderSize + records * Data::AtomicBinV2RecordSize;
    shard.sha256  = std::move(hash);
    manifest.shards.push_back(std::move(shard));
    return manifest;
}

bool write_bytes(const std::filesystem::path& path, const std::vector<u8>& bytes) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.write(reinterpret_cast<const char*>(bytes.data()), std::streamsize(bytes.size()));
    return bool(output);
}

bool write_manifest(const Data::AtomicBinV2Manifest& manifest) {
    std::string bytes;
    if (!Data::render_atomic_bin_v2_manifest(manifest, bytes))
        return false;
    std::ofstream output(manifest.manifestPath, std::ios::binary | std::ios::trunc);
    output.write(bytes.data(), std::streamsize(bytes.size()));
    return bool(output);
}

struct Dataset {
    std::filesystem::path     shard;
    std::filesystem::path     manifest;
    std::vector<u8>           bytes;
    Data::AtomicBinV2Manifest metadata;
};

Dataset create_dataset(const std::filesystem::path&    directory,
                       std::string_view                stem,
                       const Data::TrainingDataSample& sample = ordinary_sample()) {
    Dataset dataset;
    dataset.shard    = directory / (std::string(stem) + ".atbin");
    dataset.manifest = directory / (std::string(stem) + ".atbin.manifest.json");
    Data::AtomicBinV2Header header{};
    Data::AtomicBinV2Record record{};
    check(bool(Data::encode_atomic_bin_v2_header(1, header)), "encode fixture header");
    check(bool(Data::encode_atomic_bin_v2(sample, record)), "encode fixture record");
    dataset.bytes.insert(dataset.bytes.end(), header.begin(), header.end());
    dataset.bytes.insert(dataset.bytes.end(), record.begin(), record.end());
    check(write_bytes(dataset.shard, dataset.bytes), "write fixture shard");
    std::string hash;
    u64         size = 0;
    check(bool(Data::sha256_file(dataset.shard, hash, size)), "hash fixture shard");
    dataset.metadata =
      metadata_for(dataset.manifest, dataset.shard, 1, sample.result == 0 ? 1 : 0, hash);
    dataset.metadata.atomic960 = bool(sample.flags & Data::TRAINING_DATA_CHESS960);
    check(write_manifest(dataset.metadata), "write fixture manifest");
    return dataset;
}

Dataset create_dataset_records(const std::filesystem::path&                 directory,
                               std::string_view                             stem,
                               const std::vector<Data::TrainingDataSample>& samples) {
    Dataset dataset;
    dataset.shard    = directory / (std::string(stem) + ".atbin");
    dataset.manifest = directory / (std::string(stem) + ".atbin.manifest.json");
    Data::AtomicBinV2Header header{};
    check(bool(Data::encode_atomic_bin_v2_header(samples.size(), header)),
          "encode multi-record fixture header");
    dataset.bytes.insert(dataset.bytes.end(), header.begin(), header.end());
    u64 draws = 0;
    for (const auto& sample : samples)
    {
        Data::AtomicBinV2Record record{};
        check(bool(Data::encode_atomic_bin_v2(sample, record)), "encode multi-record fixture");
        dataset.bytes.insert(dataset.bytes.end(), record.begin(), record.end());
        draws += sample.result == 0;
    }
    check(write_bytes(dataset.shard, dataset.bytes), "write multi-record fixture shard");
    std::string hash;
    u64         size = 0;
    check(bool(Data::sha256_file(dataset.shard, hash, size)), "hash multi-record fixture shard");
    dataset.metadata = metadata_for(dataset.manifest, dataset.shard, samples.size(), draws, hash);
    dataset.metadata.atomic960 =
      !samples.empty() && bool(samples.front().flags & Data::TRAINING_DATA_CHESS960);
    check(write_manifest(dataset.metadata), "write multi-record fixture manifest");
    return dataset;
}

void expect_open_failure(const std::filesystem::path& manifest, std::string_view name) {
    std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
    const Data::DataResult result = Data::AtomicBinV2DatasetReader::open(manifest, reader);
    check(!result, name);
    check(!reader, std::string(name) + " resets output");
}

Data::DataResult
stream_dataset(const std::filesystem::path& manifest, u64& delivered, std::string_view name) {
    delivered = 0;
    std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
    Data::DataResult result = Data::AtomicBinV2DatasetReader::open(manifest, reader);
    check(bool(result) && bool(reader), std::string(name) + " opens manifest lazily");
    if (!result || !reader)
        return result;
    for (;;)
    {
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        result                                   = reader->next(decoded, hasRecord);
        if (!result || !hasRecord)
            return result;
        ++delivered;
    }
}

void expect_stream_failure(const std::filesystem::path& manifest, std::string_view name) {
    u64                    delivered = 0;
    const Data::DataResult result    = stream_dataset(manifest, delivered, name);
    check(!result, name);
}

#ifndef _WIN32
// Re-exec the test binary so the timeout probe has an independent lifetime.
// A fork-only child must use _exit() and would make inherited allocations look
// live to Valgrind even when the reader itself releases everything correctly.
bool child_completes(const char*                  executable,
                     const char*                  mode,
                     const std::filesystem::path& argument) {
    const pid_t child = ::fork();
    if (child == 0)
    {
        ::execl(executable, executable, mode, argument.c_str(), static_cast<char*>(nullptr));
        ::_exit(127);
    }
    if (child < 0)
        return false;
    for (int attempt = 0; attempt < 200; ++attempt)
    {
        int   status = 0;
        pid_t waited;
        do
        {
            waited = ::waitpid(child, &status, WNOHANG);
        } while (waited < 0 && errno == EINTR);
        if (waited == child)
            return WIFEXITED(status) && WEXITSTATUS(status) == 0;
        if (waited < 0)
            return false;
        ::usleep(10000);
    }
    ::kill(child, SIGKILL);
    int status = 0;
    while (::waitpid(child, &status, 0) < 0 && errno == EINTR)
    {}
    return false;
}
#endif

void rewrite_authentication(Dataset& dataset) {
    std::string hash;
    u64         size = 0;
    check(bool(Data::sha256_file(dataset.shard, hash, size)), "rehash mutated shard");
    dataset.metadata.shards.front().sha256 = hash;
    check(write_manifest(dataset.metadata), "rewrite mutated manifest");
}

}  // namespace

int main(int argc, char* argv[]) {
#ifdef _WIN32
    (void) argv;
#else
    if (argc == 3 && std::string_view(argv[1]) == "--probe-fifo-manifest")
    {
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        const Data::DataResult result = Data::AtomicBinV2DatasetReader::open(argv[2], reader);
        return !result && !reader ? 0 : 1;
    }
    if (argc == 3 && std::string_view(argv[1]) == "--probe-fifo-shard")
    {
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        if (!Data::AtomicBinV2DatasetReader::open(argv[2], reader) || !reader)
            return 1;
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        return !reader->next(decoded, hasRecord) && !hasRecord ? 0 : 1;
    }
#endif
    if (argc != 1)
        return 2;

    // This fixture is pure frozen wire data: constructing it does not touch
    // Position. The first public reader call must initialize the Atomic core
    // itself before performing semantic validation.
    {
        TempDirectory temporary("standalone-init");
        Dataset       dataset;
        dataset.shard    = temporary.path / "dataset.atbin";
        dataset.manifest = temporary.path / "dataset.atbin.manifest.json";
        Data::AtomicBinV2Header header{};
        check(bool(Data::encode_atomic_bin_v2_header(1, header)),
              "encode standalone reader header");
        const std::vector<u8> record =
          decode_hex("245336421111111100000000000000000000000000000000777777778AB99CA8"
                     "000F07003F38FF00000001000000000085FFFFFF0C0700002A000000FF000000");
        check(record.size() == Data::AtomicBinV2RecordSize, "load standalone reader frozen record");
        dataset.bytes.insert(dataset.bytes.end(), header.begin(), header.end());
        dataset.bytes.insert(dataset.bytes.end(), record.begin(), record.end());
        check(write_bytes(dataset.shard, dataset.bytes), "write standalone reader shard");
        std::string hash;
        u64         size = 0;
        check(bool(Data::sha256_file(dataset.shard, hash, size)), "hash standalone reader shard");
        dataset.metadata = metadata_for(dataset.manifest, dataset.shard, 1, 0, hash);
        check(write_manifest(dataset.metadata), "write standalone reader manifest");

        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        check(bool(Data::AtomicBinV2DatasetReader::open(dataset.manifest, reader)) && bool(reader),
              "standalone reader opens before Atomic core initialization");
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        check(reader && bool(reader->next(decoded, hasRecord)) && hasRecord
                && decoded.sample.fen == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
              "standalone reader initializes Atomic core on first semantic record");
    }

    {
        TempDirectory temporary("valid");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        const Data::DataResult                          openResult =
          Data::AtomicBinV2DatasetReader::open(dataset.manifest, reader);
        if (!openResult)
            std::cerr << "open valid manifest lazily: " << openResult.message << '\n';
        check(bool(openResult), "open valid manifest lazily");
        if (!reader)
            return 1;
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        check(reader && bool(reader->next(decoded, hasRecord)) && hasRecord,
              "stream first decoded record");
        check(decoded.shardIndex == 0 && decoded.localIndex == 0 && decoded.globalIndex == 0
                && decoded.sample.fen == ordinary_sample().fen && decoded.sample.score == 31,
              "stream indexes and semantic sample");
        check(bool(reader->next(decoded, hasRecord)) && !hasRecord, "clean dataset EOF");
        check(bool(reader->rewind()) && bool(reader->next(decoded, hasRecord)) && hasRecord,
              "rewind authenticated descriptors");
        expect_open_failure(dataset.shard, "raw .atbin entrypoint rejected");

        const auto old = temporary.path / "old.atbin";
        std::filesystem::rename(dataset.shard, old);
        std::filesystem::copy_file(old, dataset.shard);
        check(bool(reader->rewind()), "rewind remains manifest-only after pathname replacement");
        check(!reader->next(decoded, hasRecord) && !hasRecord,
              "replaced shard pathname rejected before another record");
    }
    {
        TempDirectory temporary("in-place");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        check(bool(Data::AtomicBinV2DatasetReader::open(dataset.manifest, reader)),
              "open dataset before in-place mutation");
        dataset.bytes[Data::AtomicBinV2HeaderSize + 48] ^= 1;
        check(write_bytes(dataset.shard, dataset.bytes), "write same-size in-place mutation");
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        check(reader && !reader->next(decoded, hasRecord) && !hasRecord,
              "same-identity same-size mutation rejected before streaming");
        const Data::DataResult poisoned = reader->next(decoded, hasRecord);
        check(!poisoned && !hasRecord
                && poisoned.message.find("cannot continue") != std::string::npos,
              "failed stream is permanently poisoned");
    }
    {
        TempDirectory temporary("snapshot");
        auto          second = ordinary_sample(0);
        second.score         = 77;
        Dataset dataset =
          create_dataset_records(temporary.path, "dataset", {ordinary_sample(1), second});
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        check(bool(Data::AtomicBinV2DatasetReader::open(dataset.manifest, reader)),
              "open dataset before snapshot isolation test");
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        check(reader && bool(reader->next(decoded, hasRecord)) && hasRecord
                && decoded.sample.score == 31,
              "stage authenticated snapshot and stream first record");

        dataset.bytes[Data::AtomicBinV2HeaderSize + Data::AtomicBinV2RecordSize + 48] ^= 1;
        check(write_bytes(dataset.shard, dataset.bytes),
              "mutate source after private snapshot staging");
        check(bool(reader->next(decoded, hasRecord)) && hasRecord && decoded.sample.score == 77,
              "staged authenticated bytes survive later source mutation");
        check(bool(reader->next(decoded, hasRecord)) && !hasRecord,
              "snapshot stream reaches authenticated EOF");
    }
    {
        TempDirectory temporary("stream-eof");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        check(bool(Data::AtomicBinV2DatasetReader::open(dataset.manifest, reader)),
              "open dataset for streamed EOF accounting test");
        // Simulate a post-open consumer metadata bug to exercise the public
        // EOF guard independently from the identical open-time audit guard.
        const_cast<Data::AtomicBinV2Manifest&>(reader->manifest()).draws = 1;
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        check(bool(reader->next(decoded, hasRecord)) && hasRecord,
              "stream record before indexed EOF mismatch");
        const Data::DataResult eof = reader->next(decoded, hasRecord);
        check(!eof && !hasRecord
                && eof.message.find("shard=0 local=1 global=1") != std::string::npos
                && eof.message.find("EOF") != std::string::npos,
              "streamed totals fail at indexed EOF");
    }
    {
        TempDirectory         temporary("cwd");
        Dataset               dataset = create_dataset(temporary.path, "dataset");
        CurrentDirectoryGuard cwd;
        std::filesystem::current_path(temporary.path);
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        check(bool(Data::AtomicBinV2DatasetReader::open(dataset.manifest.filename(), reader)),
              "open relative sidecar from captured CWD");
        check(reader && reader->manifest().manifestPath.is_absolute()
                && reader->manifest().shards.front().path.is_absolute(),
              "manifest and shard paths captured as absolute");
        std::filesystem::current_path(cwd.original);
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        check(reader && bool(reader->next(decoded, hasRecord)) && hasRecord,
              "CWD change cannot rebind staged shard source");
    }
    {
        TempDirectory temporary("multi");
        Dataset       first  = create_dataset(temporary.path, "dataset", ordinary_sample(1));
        Dataset       second = create_dataset(temporary.path, "dataset_1", ordinary_sample(0));
        first.metadata.options.requestedRecords = 2;
        first.metadata.options.recordsPerShard  = 1;
        first.metadata.records                  = 2;
        first.metadata.draws                    = 1;
        auto secondShard                        = second.metadata.shards.front();
        secondShard.index                       = 1;
        first.metadata.shards.push_back(secondShard);
        check(write_manifest(first.metadata), "write valid multi-shard manifest");

        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        check(bool(Data::AtomicBinV2DatasetReader::open(first.manifest, reader)),
              "open valid multi-shard dataset");
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        check(reader && bool(reader->next(decoded, hasRecord)) && hasRecord
                && decoded.shardIndex == 0 && decoded.localIndex == 0 && decoded.globalIndex == 0
                && decoded.sample.result == 1,
              "stream first shard indexes");
        check(bool(reader->next(decoded, hasRecord)) && hasRecord && decoded.shardIndex == 1
                && decoded.localIndex == 0 && decoded.globalIndex == 1
                && decoded.sample.result == 0,
              "stream second shard indexes and draw");
        check(bool(reader->next(decoded, hasRecord)) && !hasRecord, "multi-shard EOF");
        check(bool(reader->rewind()) && bool(reader->next(decoded, hasRecord)) && hasRecord
                && decoded.globalIndex == 0,
              "multi-shard rewind");

        reader.reset();
        second.bytes[Data::AtomicBinV2HeaderSize + 62] = 1;
        check(write_bytes(second.shard, second.bytes), "write second-shard corruption");
        std::string secondHash;
        u64         secondSize = 0;
        check(bool(Data::sha256_file(second.shard, secondHash, secondSize)),
              "hash second-shard corruption");
        first.metadata.shards[1].sha256 = secondHash;
        check(write_manifest(first.metadata), "write authenticated second-shard corruption");
        u64                    delivered = 0;
        const Data::DataResult indexedFailure =
          stream_dataset(first.manifest, delivered, "late second-shard corruption");
        check(!indexedFailure && delivered == 1
                && indexedFailure.message.find("shard=1 local=0 global=1") != std::string::npos,
              "late second-shard failure preserves first-record latency and indexed diagnostics");
    }

    {
        TempDirectory temporary("reserved");
        Dataset       dataset                           = create_dataset(temporary.path, "dataset");
        dataset.bytes[Data::AtomicBinV2HeaderSize + 62] = 1;
        check(write_bytes(dataset.shard, dataset.bytes), "write reserved-byte corruption");
        rewrite_authentication(dataset);
        expect_stream_failure(dataset.manifest, "reserved record bytes rejected");
    }
    {
        TempDirectory temporary("position");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        dataset.bytes[Data::AtomicBinV2HeaderSize] =
          u8((dataset.bytes[Data::AtomicBinV2HeaderSize] & 0xF0) | 0x0F);
        check(write_bytes(dataset.shard, dataset.bytes), "write reserved piece corruption");
        rewrite_authentication(dataset);
        expect_stream_failure(dataset.manifest, "invalid position piece code rejected");
    }
    {
        TempDirectory temporary("illegal");
        Dataset       dataset     = create_dataset(temporary.path, "dataset");
        const auto    offset      = Data::AtomicBinV2HeaderSize + 52;
        dataset.bytes[offset]     = 0x41;  // b1-b2 from an empty source square
        dataset.bytes[offset + 1] = 0x02;
        check(write_bytes(dataset.shard, dataset.bytes), "write illegal move corruption");
        rewrite_authentication(dataset);
        expect_stream_failure(dataset.manifest, "Atomic-illegal move rejected");
    }
    {
        TempDirectory temporary("header");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        dataset.bytes[0] ^= 1;
        check(write_bytes(dataset.shard, dataset.bytes), "write header corruption");
        rewrite_authentication(dataset);
        expect_stream_failure(dataset.manifest, "invalid header rejected");
    }
    {
        TempDirectory           temporary("count");
        Dataset                 dataset = create_dataset(temporary.path, "dataset");
        Data::AtomicBinV2Header header{};
        check(bool(Data::encode_atomic_bin_v2_header(2, header)), "encode mismatched header");
        std::copy(header.begin(), header.end(), dataset.bytes.begin());
        check(write_bytes(dataset.shard, dataset.bytes), "write count mismatch");
        rewrite_authentication(dataset);
        expect_stream_failure(dataset.manifest, "header/manifest count mismatch rejected");
    }
    {
        TempDirectory temporary("size");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        dataset.bytes.pop_back();
        check(write_bytes(dataset.shard, dataset.bytes), "write truncated shard");
        expect_stream_failure(dataset.manifest, "truncated shard rejected");
        dataset.bytes.push_back(0);
        dataset.bytes.push_back(0);
        check(write_bytes(dataset.shard, dataset.bytes), "write trailing-byte shard");
        expect_stream_failure(dataset.manifest, "trailing shard bytes rejected");
    }
    {
        TempDirectory temporary("sha");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        dataset.bytes.back() ^= 1;
        check(write_bytes(dataset.shard, dataset.bytes), "write SHA mismatch");
        expect_stream_failure(dataset.manifest, "shard SHA mismatch rejected");
    }
    {
        TempDirectory temporary("stats");
        Dataset       dataset  = create_dataset(temporary.path, "dataset");
        dataset.metadata.draws = 1;
        check(write_manifest(dataset.metadata), "write incorrect statistics");
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        check(bool(Data::AtomicBinV2DatasetReader::open(dataset.manifest, reader)) && bool(reader),
              "incorrect statistics remain lazy until EOF");
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        check(reader && bool(reader->next(decoded, hasRecord)) && hasRecord,
              "record streams before aggregate EOF statistics check");
        const Data::DataResult result = reader->next(decoded, hasRecord);
        check(!result && !hasRecord
                && result.message.find("shard=0 local=1 global=1") != std::string::npos
                && result.message.find("EOF") != std::string::npos,
              "streamed draw totals fail at indexed EOF");
    }
    {
        TempDirectory temporary("missing");
        const auto    shard    = temporary.path / "missing.atbin";
        const auto    manifest = temporary.path / "missing.atbin.manifest.json";
        auto          metadata = metadata_for(manifest, shard, 1, 0, std::string(64, '3'));
        check(write_manifest(metadata), "write missing-shard manifest");
        expect_stream_failure(manifest, "missing shard rejected lazily");
        std::filesystem::create_directory(shard);
        expect_stream_failure(manifest, "directory shard rejected lazily");
    }
    {
        TempDirectory   temporary("hardlink");
        Dataset         dataset = create_dataset(temporary.path, "dataset");
        const auto      alias   = temporary.path / "dataset-000001.atbin";
        std::error_code error;
        std::filesystem::create_hard_link(dataset.shard, alias, error);
        check(!error, "create hardlink duplicate fixture");
        if (!error)
        {
            dataset.metadata.options.requestedRecords = 2;
            dataset.metadata.options.recordsPerShard  = 1;
            dataset.metadata.records                  = 2;
            auto second                               = dataset.metadata.shards.front();
            second.index                              = 1;
            second.path                               = alias;
            dataset.metadata.shards.push_back(second);
            check(write_manifest(dataset.metadata), "write hardlink duplicate manifest");
            u64                    delivered = 0;
            const Data::DataResult result =
              stream_dataset(dataset.manifest, delivered, "hardlink duplicate shard identity");
            check(!result && delivered == 1,
                  "hardlink duplicate shard identity rejected at second shard");
        }
    }
#ifndef _WIN32
    {
        TempDirectory temporary("symlink");
        Dataset       dataset      = create_dataset(temporary.path, "target");
        const auto    linkShard    = temporary.path / "link.atbin";
        const auto    linkManifest = temporary.path / "link.atbin.manifest.json";
        std::filesystem::create_symlink(dataset.shard.filename(), linkShard);
        auto metadata =
          metadata_for(linkManifest, linkShard, 1, 0, dataset.metadata.shards.front().sha256);
        check(write_manifest(metadata), "write symlink shard manifest");
        expect_stream_failure(linkManifest, "symlink shard rejected lazily");
        const auto manifestLink = temporary.path / "sidecar.atbin.manifest.json";
        std::filesystem::create_symlink(dataset.manifest.filename(), manifestLink);
        expect_open_failure(manifestLink, "symlink manifest rejected");
    }
    {
        TempDirectory temporary("fifo-manifest");
        const auto    manifest = temporary.path / "fifo.atbin.manifest.json";
        check(::mkfifo(manifest.c_str(), 0600) == 0, "create FIFO sidecar fixture");
        check(child_completes(argv[0], "--probe-fifo-manifest", manifest),
              "FIFO sidecar is rejected without blocking before regular-file inspection");
    }
    {
        TempDirectory temporary("fifo-shard");
        const auto    shard    = temporary.path / "fifo.atbin";
        const auto    manifest = temporary.path / "fifo.atbin.manifest.json";
        check(::mkfifo(shard.c_str(), 0600) == 0, "create FIFO shard fixture");
        auto metadata = metadata_for(manifest, shard, 1, 0, std::string(64, '3'));
        check(write_manifest(metadata), "write FIFO shard manifest");
        check(child_completes(argv[0], "--probe-fifo-shard", manifest),
              "FIFO shard is rejected lazily without blocking before regular-file inspection");
    }
#endif

    {
        const StationaryFixture fixture = load_stationary_fixture();
        const bool fixtureLoaded        = !fixture.legalFen.empty() && !fixture.illegalFen.empty()
                                && fixture.move == "c1b1" && fixture.flags == "atomic960";
        check(fixtureLoaded, "load stationary-king Atomic960 fixture file");
        if (!fixtureLoaded)
            return 1;
        Data::TrainingDataSample rejected;
        rejected.fen    = fixture.illegalFen;
        rejected.move   = Move::make<CASTLING>(SQ_C1, SQ_B1);
        rejected.result = 1;
        rejected.flags  = Data::TRAINING_DATA_CHESS960;
        Position  inCheck;
        StateInfo inCheckState{};
        check(!inCheck.set(rejected.fen, true, &inCheckState),
              "archived Atomic960 stationary-king fixture parses");
        check(!MoveList<LEGAL>(inCheck).contains(rejected.move),
              "archived Atomic960 stationary-king castling through check is illegal");

        // Atomic BIN V2 requires the canonical file-letter spelling emitted
        // by Position for Atomic960 castling rights.
        rejected.fen = inCheck.fen();
        Data::AtomicBinV2Record record{};
        const Data::DataResult  rejection = Data::encode_atomic_bin_v2(rejected, record);
        check(!rejection && rejection.error == Data::DataError::INVALID_MOVE,
              "Atomic960 stationary-king castling through check rejected");

        Data::TrainingDataSample accepted = rejected;
        accepted.fen                      = fixture.legalFen;
        check(bool(Data::encode_atomic_bin_v2(accepted, record)),
              "legal Atomic960 stationary-king castling preserved");
        Data::TrainingDataSample roundTrip;
        check(bool(Data::decode_atomic_bin_v2(record, roundTrip)) && roundTrip.move == accepted.move
                && (roundTrip.flags & Data::TRAINING_DATA_CHESS960),
              "stationary-king castling byte round trip");

        TempDirectory temporary("atomic960");
        Dataset       dataset = create_dataset(temporary.path, "dataset", accepted);
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        check(bool(Data::AtomicBinV2DatasetReader::open(dataset.manifest, reader)),
              "open legal Atomic960 stationary-castling dataset");
        Data::AtomicBinV2DecodedRecord decoded;
        bool                           hasRecord = false;
        check(reader && bool(reader->next(decoded, hasRecord)) && hasRecord
                && (decoded.sample.flags & Data::TRAINING_DATA_CHESS960)
                && decoded.sample.move == accepted.move,
              "stream legal Atomic960 stationary-castling record");
    }

    if (failures)
    {
        std::cerr << failures << " Atomic BIN V2 reader test(s) failed\n";
        return 1;
    }
    std::cout << "Atomic BIN V2 authenticated streaming reader tests passed\n";
    return 0;
}
