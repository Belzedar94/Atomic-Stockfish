/* Focused tests for the strict canonical Atomic BIN V2 manifest reader. */

#include <filesystem>
#include <iostream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "data/atomic_bin_v2_manifest.h"
#include "data/atomic_bin_v2_wire.h"

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

Data::AtomicBinV2Manifest fixture() {
    Data::AtomicBinV2Manifest manifest;
    manifest.manifestPath               = "dataset.atbin.manifest.json";
    manifest.engineCommit               = "0123456789abcdef0123456789abcdef01234567";
    manifest.engineVersion              = "Atomic-Stockfish reader fixture";
    manifest.networkPath                = "atomic.nnue";
    manifest.networkSha256              = std::string(64, '1');
    manifest.resolvedSeed               = 7;
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
    manifest.options.requestedRecords   = 1;
    manifest.options.recordsPerShard    = 1;
    manifest.options.keepDraws          = "0.5";
    manifest.records                    = 1;
    manifest.draws                      = 0;
    Data::AtomicBinV2ManifestShard shard;
    shard.path    = "dataset.atbin";
    shard.records = 1;
    shard.bytes   = Data::AtomicBinV2HeaderSize + Data::AtomicBinV2RecordSize;
    shard.sha256  = std::string(64, '2');
    manifest.shards.push_back(std::move(shard));
    return manifest;
}

std::string replace_once(std::string input, std::string_view from, std::string_view to) {
    const auto position = input.find(from);
    if (position == std::string::npos)
        return {};
    input.replace(position, from.size(), to);
    return input;
}

void rejects(std::string                  bytes,
             std::string_view             name,
             const std::filesystem::path& path = "dataset.atbin.manifest.json") {
    Data::AtomicBinV2Manifest output = fixture();
    const Data::DataResult    result = Data::parse_atomic_bin_v2_manifest(bytes, path, output);
    check(!result, name);
    check(output.shards.empty(), std::string(name) + " resets output");
}

void accepts(std::string                  bytes,
             std::string_view             name,
             const std::filesystem::path& path = "dataset.atbin.manifest.json") {
    Data::AtomicBinV2Manifest output;
    check(bool(Data::parse_atomic_bin_v2_manifest(bytes, path, output)), name);
    check(output.shards.size() == 1, std::string(name) + " preserves shard metadata");
}

}  // namespace

int main() {
    double      normalizedEffective = 0.0;
    std::string normalizedKeepDraws;
    check(bool(Data::normalize_atomic_keep_draws("5e-1", normalizedEffective, normalizedKeepDraws))
            && normalizedEffective == 0.5 && normalizedKeepDraws == "0.5",
          "shared keep_draws normalizer canonicalizes exact exponent input");
    normalizedEffective = 1.0;
    normalizedKeepDraws = "stale";
    check(!Data::normalize_atomic_keep_draws(std::string(4097, '0'), normalizedEffective,
                                             normalizedKeepDraws)
            && normalizedEffective == 0.0 && normalizedKeepDraws.empty(),
          "shared keep_draws normalizer rejects over-4096 input transactionally");

    Data::AtomicBinV2Manifest source = fixture();
    std::string               canonical;
    check(bool(Data::render_atomic_bin_v2_manifest(source, canonical)), "render fixture");

    Data::AtomicBinV2Manifest parsed;
    check(bool(Data::parse_atomic_bin_v2_manifest(canonical, source.manifestPath, parsed)),
          "parse canonical fixture");
    check(parsed.engineCommit == source.engineCommit && parsed.shards.size() == 1
            && parsed.shards.front().path == source.shards.front().path,
          "canonical values preserved");

    rejects(canonical, "raw atbin entrypoint", "dataset.atbin");
    rejects("\xEF\xBB\xBF" + canonical, "UTF-8 BOM");
    rejects(replace_once(canonical, "\n", "\r\n"), "CRLF");
    rejects(" " + canonical, "leading whitespace");
    rejects(replace_once(canonical, "\"format\":", "\"unknown\":0,\"format\":"),
            "unknown property");
    rejects(replace_once(canonical, "\"format\":\"atomic-bin-v2\",", ""), "missing property");
    rejects(replace_once(canonical, "\"manifest_version\":1,\"manifest_schema_sha256\"",
                         "\"manifest_schema_sha256\""),
            "changed key order");
    rejects(replace_once(canonical, "\"format\":", "\"format\":\"atomic-bin-v2\",\"format\":"),
            "duplicate property");
    rejects(
      replace_once(canonical, "\"data_schema_sha256\":\"0352", "\"data_schema_sha256\":\"1352"),
      "data schema mismatch");
    rejects(replace_once(canonical, "\"manifest_schema_sha256\":\"83d6",
                         "\"manifest_schema_sha256\":\"93d6"),
            "manifest schema mismatch");
    rejects(replace_once(canonical, std::string(64, '1'), std::string(64, 'A')),
            "uppercase SHA-256");
    rejects(replace_once(canonical, "\"threads\":1", "\"threads\":4294967296"), "uint32 overflow");
    rejects(replace_once(canonical, "\"search_depth_min\":3", "\"search_depth_min\":0"),
            "producer depth domain");
    rejects(replace_once(canonical, "\"write_min_ply\":5,\"write_max_ply\":400",
                         "\"write_min_ply\":400,\"write_max_ply\":400"),
            "producer ply ordering");
    rejects(
      replace_once(canonical, "\"keep_draws\":\"0.5\"", "\"keep_draws\":\"0.10000000000000001\""),
      "keep_draws floating-point round-trip mismatch");
    rejects(replace_once(canonical, "\"keep_draws\":\"0.5\"", "\"keep_draws\":\"1e-4097\""),
            "keep_draws canonical expansion over 4096 bytes");
    rejects(replace_once(canonical, "\"keep_draws\":\"0.5\"",
                         "\"keep_draws\":\"1e" + std::string(4088, '9') + "\""),
            "keep_draws huge exponent fails without integer overflow");
    rejects(replace_once(canonical, "\"keep_draws\":\"0.5\"", "\"keep_draws\":\"5e-1\""),
            "keep_draws noncanonical exponent spelling");
    rejects(replace_once(canonical, "\"resolved_seed\":\"7\"",
                         "\"resolved_seed\":\"18446744073709551616\""),
            "uint64 overflow");
    rejects(replace_once(canonical, "\"file\":\"atomic.nnue\"", "\"file\":\"../atomic.nnue\""),
            "path traversal");
    rejects(replace_once(canonical, "\"file\":\"atomic.nnue\"", "\"file\":\"C:\\\\atomic.nnue\""),
            "absolute path");
    rejects(replace_once(canonical, "\"file\":\"dataset.atbin\"", "\"file\":\"sub/dataset.atbin\""),
            "shard separator");
    accepts(replace_once(canonical, "\"file\":\"atomic.nnue\"", "\"file\":\"CON.nnue\""),
            "schema-declared Windows device-like network basename");
    accepts(replace_once(canonical, "\"file\":\"dataset.atbin\"", "\"file\":\"LPT9.atbin\""),
            "schema-declared Windows device-like shard basename", "LPT9.atbin.manifest.json");
    const std::string superscriptDevice = std::string("\"file\":\"COM") + "\xC2\xB9" + ".nnue\"";
    accepts(replace_once(canonical, "\"file\":\"atomic.nnue\"", superscriptDevice),
            "schema-declared superscript device-like basename");
    accepts(replace_once(canonical, "\"file\":\"atomic.nnue\"",
                         std::string("\"file\":\"") + std::string(256, 'a') + "\""),
            "schema has no undeclared 255-byte basename cap");
    accepts(replace_once(canonical, "\"file\":\"atomic.nnue\"", "\"file\":\"atomic.nnue.\""),
            "schema-declared trailing dot basename");
    accepts(replace_once(canonical, "\"file\":\"atomic.nnue\"", "\"file\":\"atomic.nnue \""),
            "schema-declared trailing space basename");
    accepts(replace_once(canonical, "atomic.nnue", "atomic\\u0001.nnue"),
            "schema-declared non-NUL ASCII control in basename");
    rejects(replace_once(canonical, "atomic.nnue", "atomic\\u0000.nnue"),
            "schema-forbidden NUL in basename");
    std::string delBasename = canonical;
    const auto  networkName = delBasename.find("atomic.nnue");
    if (networkName != std::string::npos)
        delBasename.insert(networkName + 6, 1, char(0x7F));
    accepts(std::move(delBasename), "schema-declared DEL in basename");
    accepts(replace_once(canonical, "\"file\":\"dataset.atbin\"", "\"file\":\"CON.atbin\""),
            "schema-declared device-like manifest/shard basename", "CON.atbin.manifest.json");
    rejects(replace_once(canonical, "atomic.nnue", "atomic\\u002ennue"),
            "noncanonical Unicode escape");

    std::string invalidUtf8 = canonical;
    invalidUtf8.insert(invalidUtf8.begin() + 1, char(0xC0));
    rejects(std::move(invalidUtf8), "invalid UTF-8");

    if (failures)
    {
        std::cerr << failures << " Atomic BIN V2 manifest reader test(s) failed\n";
        return 1;
    }
    std::cout << "Atomic BIN V2 canonical manifest reader tests passed\n";
    return 0;
}
