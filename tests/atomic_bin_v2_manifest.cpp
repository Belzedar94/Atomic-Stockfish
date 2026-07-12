/* Focused tests for the canonical Atomic BIN V2 manifest and publication path. */

#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>

#include "attacks.h"
#include "bitboard.h"
#include "data/atomic_bin_v2_manifest.h"
#include "data/atomic_bin_v2_sink.h"
#include "data/sha256.h"
#include "position.h"
#include "tt.h"

#ifdef _WIN32
    #include <process.h>
#else
    #include <unistd.h>
#endif

using namespace Stockfish;

TTEntry* TranspositionTable::first_entry(Key) const { return nullptr; }

namespace {

constexpr const char*      StartFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
constexpr std::string_view ManifestSchemaSha256 =
  "83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42";
constexpr std::string_view OneRecordSha256 =
  "50cf1665bdc975ea1e2abab21dcb926ec63223e0b3f853ba56862c2c1e83d7a6";
constexpr std::string_view NetworkSha256 =
  "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

int failures = 0;

void expect(bool condition, std::string_view label) {
    if (!condition)
    {
        std::cerr << "FAIL " << label << '\n';
        ++failures;
    }
}

Data::TrainingDataSample sample() {
    return {StartFEN, -123, Move(SQ_E2, SQ_E4), 42, -1, Data::NO_TRAINING_DATA_FLAGS};
}

std::filesystem::path temporary_directory(std::string_view label) {
    static std::atomic<unsigned long long> sequence{0};
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto pid =
#ifdef _WIN32
      ::_getpid();
#else
      ::getpid();
#endif
    return std::filesystem::temp_directory_path()
         / ("atomic-stockfish-v2-manifest-" + std::to_string(pid) + "-" + std::to_string(nonce)
            + "-" + std::to_string(sequence.fetch_add(1)) + "-" + std::string(label));
}

void remove_ignored(const std::filesystem::path& path) {
    std::error_code ignored;
    std::filesystem::remove_all(path, ignored);
}

bool create_test_directory(const std::filesystem::path& path) {
    remove_ignored(path);
    std::error_code error;
    const bool      created = std::filesystem::create_directories(path, error);
    expect(created && !error, "temporary manifest directory creates");
    return created && !error;
}

std::string read_file(const std::filesystem::path& path) {
    std::ifstream      input(path, std::ios::binary);
    std::ostringstream bytes;
    bytes << input.rdbuf();
    return input ? bytes.str() : std::string{};
}

struct FinalizedShard {
    std::filesystem::path path;
    u64                   records = 0;
    u64                   bytes   = 0;
    std::string           sha256;
};

FinalizedShard create_finalized_shard(const std::filesystem::path& path) {
    FinalizedShard result;
    result.path = path;

    Data::AtomicBinV2Sink  sink(path);
    const Data::DataResult appended = sink.append(sample());
    expect(bool(appended), "real manifest shard appends");
    if (!appended)
        return result;

    const Data::DataResult finalized = sink.finalize();
    expect(bool(finalized), "real manifest shard finalizes");
    if (!finalized)
        return result;

    result.records = sink.records_written();
    result.bytes   = sink.finalized_size();
    result.sha256  = sink.sha256_hex();
    expect(result.records == 1 && result.bytes == 160 && result.sha256 == OneRecordSha256,
           "real manifest shard exposes frozen size and SHA-256");

    std::string independentSha;
    u64         independentSize = 0;
    expect(bool(Data::sha256_file(path, independentSha, independentSize))
             && independentSize == result.bytes && independentSha == result.sha256,
           "manifest shard metadata matches an independent path hash");
    return result;
}

Data::AtomicBinV2Manifest make_manifest(const std::filesystem::path& directory,
                                        const FinalizedShard&        shard) {
    Data::AtomicBinV2Manifest manifest;
    manifest.manifestPath  = Data::atomic_bin_v2_manifest_path(shard.path);
    manifest.engineCommit  = "0123456789abcdef0123456789abcdef01234567";
    manifest.engineVersion = "Atomic-Stockfish-test";
    manifest.networkPath   = directory / "atomic.nnue";
    manifest.networkSha256 = std::string(NetworkSha256);
    manifest.resolvedSeed  = 123456789;
    manifest.atomic960     = true;
    manifest.threads       = 2;
    manifest.hashMb        = 512;

    auto& options                        = manifest.options;
    options.searchDepthMin               = 3;
    options.searchDepthMax               = 5;
    options.nodes                        = 1234;
    options.requestedRecords             = 1;
    options.recordsPerShard              = 1;
    options.evalLimit                    = 3000;
    options.evalDiffLimit                = 64000;
    options.randomMoveMinPly             = 1;
    options.randomMoveMaxPly             = 24;
    options.randomMoveCount              = 5;
    options.randomMoveLikeApery          = 0;
    options.randomMultiPv                = 5;
    options.randomMultiPvDiff            = 100;
    options.randomMultiPvDepth           = 5;
    options.writeMinPly                  = 5;
    options.writeMaxPly                  = 400;
    options.keepDraws                    = "0.5";
    options.adjudicateDrawsByScore       = true;
    options.adjudicateInsufficient       = true;
    options.filterCaptures               = true;
    options.filterChecks                 = false;
    options.filterPromotions             = false;
    options.randomFileName               = false;
    options.setRecommendedUciOptionsSeen = true;

    manifest.records = 1;
    manifest.draws   = 0;
    manifest.shards.push_back({shard.path, 0, shard.records, shard.bytes, shard.sha256});
    return manifest;
}

std::string canonical_golden() {
    return R"({"manifest_version":1,"manifest_schema_sha256":")"
           R"(83d63922df3ac4a0c81a21ec9d9fd9e180efe50f26efee62fe01710e09da5b42)"
           R"(","data_schema_sha256":")"
           R"(0352b036f2a140c609e3eb9c9d635dc553e8d77253d8faa92437390f5cf93cb6)"
           R"(","format":"atomic-bin-v2",)"
           R"("engine":{"commit":"0123456789abcdef0123456789abcdef01234567",)"
           R"("version":"Atomic-Stockfish-test"},)"
           R"("network":{"file":"atomic.nnue","sha256":")"
           R"(aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},)"
           R"("book":{"kind":"builtin-startpos","file":null,"sha256":null},)"
           R"("generation":{"resolved_seed":"123456789","atomic960":true,"threads":2,)"
           R"("hash_mb":"512","use_nnue":"pure","options":{)"
           R"("search_depth_min":3,"search_depth_max":5,"nodes":"1234",)"
           R"("requested_records":"1","records_per_shard":"1","eval_limit":3000,)"
           R"("eval_diff_limit":64000,"random_move_min_ply":1,"random_move_max_ply":24,)"
           R"("random_move_count":5,"random_move_like_apery":0,"random_multi_pv":5,)"
           R"("random_multi_pv_diff":100,"random_multi_pv_depth":5,"write_min_ply":5,)"
           R"("write_max_ply":400,"keep_draws":"0.5",)"
           R"("adjudicate_draws_by_score":true,"adjudicate_insufficient":true,)"
           R"("filter_captures":true,"filter_checks":false,"filter_promotions":false,)"
           R"("random_file_name":false,"set_recommended_uci_options_seen":true}},)"
           R"("statistics":{"records":"1","draws":"0"},)"
           R"("shards":[{"index":0,"file":"dataset.atbin","records":"1",)"
           R"("bytes":"160","sha256":")"
           R"(50cf1665bdc975ea1e2abab21dcb926ec63223e0b3f853ba56862c2c1e83d7a6)"
           R"("}]})"
           "\n";
}

void test_canonical_render_and_exclusive_publication() {
    const auto root = temporary_directory("golden");
    if (!create_test_directory(root))
        return;
    const auto manifestPath = Data::atomic_bin_v2_manifest_path(root / "dataset.atbin");
    expect(bool(Data::preflight_atomic_bin_v2_manifest_publication(manifestPath)),
           "manifest publication capability preflights before dataset work");
    expect(!std::filesystem::exists(manifestPath),
           "manifest publication preflight creates no named sidecar");
    bool retainedProbe = false;
    for (const auto& entry : std::filesystem::directory_iterator(root))
        retainedProbe |=
          entry.path().filename().string().find(".atomic-bin-v2-manifest-preflight-") == 0;
    expect(!retainedProbe, "manifest publication preflight removes its private probe");

    const FinalizedShard shard    = create_finalized_shard(root / "dataset.atbin");
    auto                 manifest = make_manifest(root, shard);
    expect(Data::AtomicBinV2ManifestSchemaSha256Hex == ManifestSchemaSha256,
           "manifest schema SHA-256 is frozen");
    expect(manifest.manifestPath == root / "dataset.atbin.manifest.json",
           "manifest path appends the required sidecar suffix");

    std::string json = "must-be-replaced";
    expect(bool(Data::render_atomic_bin_v2_manifest(manifest, json)), "canonical manifest renders");
    const std::string expected = canonical_golden();
    expect(json == expected, "canonical manifest matches exact golden bytes");
    expect(!json.empty() && json.back() == '\n'
             && (json.size() == 1 || json[json.size() - 2] != '\n'),
           "canonical manifest has exactly one trailing LF");

    expect(bool(Data::write_atomic_bin_v2_manifest(manifest)),
           "manifest publishes with exclusive create");
    expect(read_file(manifest.manifestPath) == expected,
           "published manifest bytes match canonical render");

    const Data::DataResult duplicate = Data::write_atomic_bin_v2_manifest(manifest);
    expect(!duplicate && duplicate.error == Data::DataError::OUTPUT_EXISTS,
           "manifest publication refuses overwrite with O_EXCL semantics");
    expect(read_file(manifest.manifestPath) == expected,
           "failed duplicate publication preserves the original sidecar");

    remove_ignored(root);
}

void test_mutated_shard_rejected_without_sidecar() {
    const auto root = temporary_directory("mutation");
    if (!create_test_directory(root))
        return;
    const FinalizedShard shard    = create_finalized_shard(root / "dataset.atbin");
    const auto           manifest = make_manifest(root, shard);

    {
        std::ofstream output(shard.path, std::ios::binary | std::ios::app);
        output.put('X');
        expect(bool(output), "finalized shard mutation fixture writes");
    }

    const Data::DataResult result = Data::write_atomic_bin_v2_manifest(manifest);
    expect(!result && result.error == Data::DataError::INVALID_MANIFEST,
           "manifest rejects a shard mutated after finalization");
    expect(!std::filesystem::exists(manifest.manifestPath),
           "mutated shard rejection creates no sidecar");

    remove_ignored(root);
}

void test_validation_escaping_and_error_reset() {
    const auto root = temporary_directory("validation");
    if (!create_test_directory(root))
        return;
    const FinalizedShard shard = create_finalized_shard(root / "dataset.atbin");
    const auto           base  = make_manifest(root, shard);

    auto escaped          = base;
    escaped.engineVersion = std::string("A\"B\\C\n\t\x01") + "\xC3\xA9";
    std::string json;
    expect(bool(Data::render_atomic_bin_v2_manifest(escaped, json)),
           "manifest accepts escapable engine metadata");
    const std::string escapedFragment =
      std::string(R"("version":"A\"B\\C\n\t\u0001)") + "\xC3\xA9" + '"';
    expect(json.find(escapedFragment) != std::string::npos,
           "manifest escapes JSON controls while preserving UTF-8 bytes");

    auto invalidCommit         = base;
    invalidCommit.engineCommit = "ABC";
    json                       = "must-be-cleared";
    expect(Data::render_atomic_bin_v2_manifest(invalidCommit, json).error
             == Data::DataError::INVALID_MANIFEST,
           "manifest rejects a noncanonical engine commit");
    expect(json.empty(), "failed commit validation resets render output");

    auto invalidUtf8          = base;
    invalidUtf8.engineVersion = std::string("\xC3(", 2);
    json                      = "must-be-cleared";
    expect(Data::render_atomic_bin_v2_manifest(invalidUtf8, json).error
             == Data::DataError::INVALID_MANIFEST,
           "manifest rejects invalid UTF-8 metadata");
    expect(json.empty(), "failed UTF-8 validation resets render output");

    auto invalidPath        = base;
    invalidPath.networkPath = root / "bad:name.nnue";
    json                    = "must-be-cleared";
    expect(Data::render_atomic_bin_v2_manifest(invalidPath, json).error
             == Data::DataError::INVALID_MANIFEST,
           "manifest rejects a nonportable basename");
    expect(json.empty(), "failed filename validation resets render output");

#ifdef _WIN32
    auto         invalidUtf16Path = base;
    std::wstring invalidUtf16Name = L"bad";
    invalidUtf16Name.push_back(wchar_t(0xD800));
    invalidUtf16Name += L".nnue";
    invalidUtf16Path.networkPath = root / std::filesystem::path(invalidUtf16Name);
    json                         = "must-be-cleared";
    expect(Data::render_atomic_bin_v2_manifest(invalidUtf16Path, json).error
             == Data::DataError::INVALID_MANIFEST,
           "manifest rejects an unpaired UTF-16 surrogate in a Windows basename");
    expect(json.empty(), "failed UTF-16 filename validation resets render output");
#endif

    auto invalidShard            = base;
    invalidShard.shards[0].index = 1;
    json                         = "must-be-cleared";
    expect(Data::render_atomic_bin_v2_manifest(invalidShard, json).error
             == Data::DataError::INVALID_MANIFEST,
           "manifest rejects a noncontiguous shard index");
    expect(json.empty(), "failed shard validation resets render output");

    auto emptyStemShard           = base;
    emptyStemShard.shards[0].path = root / ".atbin";
    emptyStemShard.manifestPath = Data::atomic_bin_v2_manifest_path(emptyStemShard.shards[0].path);
    json                        = "must-be-cleared";
    expect(Data::render_atomic_bin_v2_manifest(emptyStemShard, json).error
             == Data::DataError::INVALID_MANIFEST,
           "manifest rejects an empty .atbin basename stem");
    expect(json.empty(), "failed empty-stem validation resets render output");

    remove_ignored(root);
}

}  // namespace

int main() {
    Bitboards::init();
    Attacks::init();
    Position::init();

    test_canonical_render_and_exclusive_publication();
    test_mutated_shard_rejected_without_sidecar();
    test_validation_escaping_and_error_reset();

    if (failures != 0)
    {
        std::cerr << failures << " Atomic BIN V2 manifest test(s) failed\n";
        return 1;
    }

    std::cout << "Atomic BIN V2 manifest tests passed\n";
    return 0;
}
