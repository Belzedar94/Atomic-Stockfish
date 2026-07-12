/* End-to-end tests for the authenticated streaming Atomic BIN V2 reader. */

#include <algorithm>
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

using namespace Stockfish;

namespace {

int failures = 0;

void check(bool condition, std::string_view name) {
    if (!condition)
    {
        ++failures;
        std::cerr << "FAIL: " << name << '\n';
    }
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

void expect_open_failure(const std::filesystem::path& manifest, std::string_view name) {
    std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
    const Data::DataResult result = Data::AtomicBinV2DatasetReader::open(manifest, reader);
    check(!result, name);
    check(!reader, std::string(name) + " resets output");
}

void rewrite_authentication(Dataset& dataset) {
    std::string hash;
    u64         size = 0;
    check(bool(Data::sha256_file(dataset.shard, hash, size)), "rehash mutated shard");
    dataset.metadata.shards.front().sha256 = hash;
    check(write_manifest(dataset.metadata), "rewrite mutated manifest");
}

}  // namespace

int main() {
    {
        TempDirectory temporary("valid");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        std::unique_ptr<Data::AtomicBinV2DatasetReader> reader;
        check(bool(Data::AtomicBinV2DatasetReader::open(dataset.manifest, reader)),
              "open valid authenticated dataset");
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
        check(!reader->rewind(), "replaced shard pathname rejected");
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
        const Data::DataResult indexedFailure =
          Data::AtomicBinV2DatasetReader::open(first.manifest, reader);
        check(!indexedFailure
                && indexedFailure.message.find("shard=1 local=0 global=1") != std::string::npos,
              "second-shard failure reports shard/local/global indexes");
    }

    {
        TempDirectory temporary("reserved");
        Dataset       dataset                           = create_dataset(temporary.path, "dataset");
        dataset.bytes[Data::AtomicBinV2HeaderSize + 62] = 1;
        check(write_bytes(dataset.shard, dataset.bytes), "write reserved-byte corruption");
        rewrite_authentication(dataset);
        expect_open_failure(dataset.manifest, "reserved record bytes rejected");
    }
    {
        TempDirectory temporary("position");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        dataset.bytes[Data::AtomicBinV2HeaderSize] =
          u8((dataset.bytes[Data::AtomicBinV2HeaderSize] & 0xF0) | 0x0F);
        check(write_bytes(dataset.shard, dataset.bytes), "write reserved piece corruption");
        rewrite_authentication(dataset);
        expect_open_failure(dataset.manifest, "invalid position piece code rejected");
    }
    {
        TempDirectory temporary("illegal");
        Dataset       dataset     = create_dataset(temporary.path, "dataset");
        const auto    offset      = Data::AtomicBinV2HeaderSize + 52;
        dataset.bytes[offset]     = 0x41;  // b1-b2 from an empty source square
        dataset.bytes[offset + 1] = 0x02;
        check(write_bytes(dataset.shard, dataset.bytes), "write illegal move corruption");
        rewrite_authentication(dataset);
        expect_open_failure(dataset.manifest, "Atomic-illegal move rejected");
    }
    {
        TempDirectory temporary("header");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        dataset.bytes[0] ^= 1;
        check(write_bytes(dataset.shard, dataset.bytes), "write header corruption");
        rewrite_authentication(dataset);
        expect_open_failure(dataset.manifest, "invalid header rejected");
    }
    {
        TempDirectory           temporary("count");
        Dataset                 dataset = create_dataset(temporary.path, "dataset");
        Data::AtomicBinV2Header header{};
        check(bool(Data::encode_atomic_bin_v2_header(2, header)), "encode mismatched header");
        std::copy(header.begin(), header.end(), dataset.bytes.begin());
        check(write_bytes(dataset.shard, dataset.bytes), "write count mismatch");
        rewrite_authentication(dataset);
        expect_open_failure(dataset.manifest, "header/manifest count mismatch rejected");
    }
    {
        TempDirectory temporary("size");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        dataset.bytes.pop_back();
        check(write_bytes(dataset.shard, dataset.bytes), "write truncated shard");
        expect_open_failure(dataset.manifest, "truncated shard rejected");
        dataset.bytes.push_back(0);
        dataset.bytes.push_back(0);
        check(write_bytes(dataset.shard, dataset.bytes), "write trailing-byte shard");
        expect_open_failure(dataset.manifest, "trailing shard bytes rejected");
    }
    {
        TempDirectory temporary("sha");
        Dataset       dataset = create_dataset(temporary.path, "dataset");
        dataset.bytes.back() ^= 1;
        check(write_bytes(dataset.shard, dataset.bytes), "write SHA mismatch");
        expect_open_failure(dataset.manifest, "shard SHA mismatch rejected");
    }
    {
        TempDirectory temporary("stats");
        Dataset       dataset  = create_dataset(temporary.path, "dataset");
        dataset.metadata.draws = 1;
        check(write_manifest(dataset.metadata), "write incorrect statistics");
        expect_open_failure(dataset.manifest, "audited draw statistics mismatch rejected");
    }
    {
        TempDirectory temporary("missing");
        const auto    shard    = temporary.path / "missing.atbin";
        const auto    manifest = temporary.path / "missing.atbin.manifest.json";
        auto          metadata = metadata_for(manifest, shard, 1, 0, std::string(64, '3'));
        check(write_manifest(metadata), "write missing-shard manifest");
        expect_open_failure(manifest, "missing shard rejected");
        std::filesystem::create_directory(shard);
        expect_open_failure(manifest, "directory shard rejected");
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
            expect_open_failure(dataset.manifest, "hardlink duplicate shard identity rejected");
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
        expect_open_failure(linkManifest, "symlink shard rejected");
        const auto manifestLink = temporary.path / "sidecar.atbin.manifest.json";
        std::filesystem::create_symlink(dataset.manifest.filename(), manifestLink);
        expect_open_failure(manifestLink, "symlink manifest rejected");
    }
#endif

    {
        Data::TrainingDataSample rejected;
        rejected.fen    = "7k/8/8/8/8/8/2PP4/1RK4q w Q - 0 1";
        rejected.move   = Move::make<CASTLING>(SQ_C1, SQ_B1);
        rejected.result = 1;
        rejected.flags  = Data::TRAINING_DATA_CHESS960;
        Data::AtomicBinV2Record record{};
        const Data::DataResult  rejection = Data::encode_atomic_bin_v2(rejected, record);
        check(!rejection && rejection.error == Data::DataError::INVALID_MOVE,
              "Atomic960 stationary-king castling through check rejected");

        Data::TrainingDataSample accepted = rejected;
        accepted.fen                      = "7k/8/8/8/8/8/8/1RK5 w B - 0 1";
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
