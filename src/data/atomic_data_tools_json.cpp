/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_data_tools_json.h"

#include <array>
#include <filesystem>
#include <string>
#include <string_view>

namespace Stockfish::Data {

namespace {

void append_decimal_string(std::string& output, u64 value) {
    output.push_back('"');
    output += std::to_string(value);
    output.push_back('"');
}

std::string json_string(std::string_view value) {
    constexpr char Hex[] = "0123456789abcdef";
    std::string    output;
    output.reserve(value.size() + 2);
    output.push_back('"');
    for (const unsigned char character : value)
    {
        switch (character)
        {
        case '"' :
            output += "\\\"";
            break;
        case '\\' :
            output += "\\\\";
            break;
        case '\b' :
            output += "\\b";
            break;
        case '\f' :
            output += "\\f";
            break;
        case '\n' :
            output += "\\n";
            break;
        case '\r' :
            output += "\\r";
            break;
        case '\t' :
            output += "\\t";
            break;
        default :
            if (character < 0x20)
            {
                output += "\\u00";
                output.push_back(Hex[character >> 4]);
                output.push_back(Hex[character & 0x0F]);
            }
            else
                output.push_back(char(character));
        }
    }
    output.push_back('"');
    return output;
}

void append_bool(std::string& output, bool value) { output += value ? "true" : "false"; }

std::string filename_utf8(const std::filesystem::path& path) { return path.filename().u8string(); }

std::string square_name(u8 square) { return {char('a' + square % 8), char('1' + square / 8)}; }

void append_optional_square(std::string& output, u8 square) {
    if (square == AtomicBinV2NoSquare)
        output += "null";
    else
        output += json_string(square_name(square));
}

std::string castling_token(const AtomicBinV2RecordFields& fields) {
    if (!fields.position.castlingRights)
        return "-";

    const bool                    atomic960 = bool(fields.flags & ATOMIC_BIN_V2_ATOMIC960);
    constexpr std::array<char, 4> Orthodox  = {'K', 'Q', 'k', 'q'};
    std::string                   token;
    for (unsigned index = 0; index < 4; ++index)
        if (fields.position.castlingRights & u8(1U << index))
        {
            if (!atomic960)
                token.push_back(Orthodox[index]);
            else
            {
                char origin = char('a' + fields.position.castlingRookOrigins[index] % 8);
                token.push_back(index < 2 ? char(origin - 'a' + 'A') : origin);
            }
        }
    return token;
}

std::string_view move_type_name(u8 type) {
    constexpr std::array<std::string_view, 4> Names = {"normal", "promotion", "en-passant",
                                                       "castling"};
    return Names[type];
}

std::string_view promotion_name(u8 promotion) {
    constexpr std::array<std::string_view, 5> Names = {"none", "knight", "bishop", "rook", "queen"};
    return Names[promotion];
}

void append_manifest_options(std::string& output, const AtomicBinV2ManifestOptions& options) {
    output += R"({"search_depth_min":)" + std::to_string(options.searchDepthMin);
    output += R"(,"search_depth_max":)" + std::to_string(options.searchDepthMax);
    output += R"(,"nodes":)";
    append_decimal_string(output, options.nodes);
    output += R"(,"requested_records":)";
    append_decimal_string(output, options.requestedRecords);
    output += R"(,"records_per_shard":)";
    append_decimal_string(output, options.recordsPerShard);
    output += R"(,"eval_limit":)" + std::to_string(options.evalLimit);
    output += R"(,"eval_diff_limit":)" + std::to_string(options.evalDiffLimit);
    output += R"(,"random_move_min_ply":)" + std::to_string(options.randomMoveMinPly);
    output += R"(,"random_move_max_ply":)" + std::to_string(options.randomMoveMaxPly);
    output += R"(,"random_move_count":)" + std::to_string(options.randomMoveCount);
    output += R"(,"random_move_like_apery":)" + std::to_string(options.randomMoveLikeApery);
    output += R"(,"random_multi_pv":)" + std::to_string(options.randomMultiPv);
    output += R"(,"random_multi_pv_diff":)" + std::to_string(options.randomMultiPvDiff);
    output += R"(,"random_multi_pv_depth":)" + std::to_string(options.randomMultiPvDepth);
    output += R"(,"write_min_ply":)" + std::to_string(options.writeMinPly);
    output += R"(,"write_max_ply":)" + std::to_string(options.writeMaxPly);
    output += R"(,"keep_draws":)" + json_string(options.keepDraws);
    output += R"(,"adjudicate_draws_by_score":)";
    append_bool(output, options.adjudicateDrawsByScore);
    output += R"(,"adjudicate_insufficient":)";
    append_bool(output, options.adjudicateInsufficient);
    output += R"(,"filter_captures":)";
    append_bool(output, options.filterCaptures);
    output += R"(,"filter_checks":)";
    append_bool(output, options.filterChecks);
    output += R"(,"filter_promotions":)";
    append_bool(output, options.filterPromotions);
    output += R"(,"random_file_name":)";
    append_bool(output, options.randomFileName);
    output += R"(,"set_recommended_uci_options_seen":)";
    append_bool(output, options.setRecommendedUciOptionsSeen);
    output.push_back('}');
}

}  // namespace

std::string render_atomic_data_tools_validation_json(const AtomicDataToolsValidationStats& stats) {
    std::string output =
      R"({"type":"atomic-data-tools-validation","contract_version":1,"status":"ok","format":"atomic-bin-v2","entrypoint":"manifest","shards":)";
    output += std::to_string(stats.shards);
    output += R"(,"records":)";
    append_decimal_string(output, stats.records);
    output += R"(,"side_to_move_wins":)";
    append_decimal_string(output, stats.sideToMoveWins);
    output += R"(,"draws":)";
    append_decimal_string(output, stats.draws);
    output += R"(,"side_to_move_losses":)";
    append_decimal_string(output, stats.sideToMoveLosses);
    output += R"(,"atomic960_records":)";
    append_decimal_string(output, stats.atomic960RecordCount);
    output += "}\n";
    return output;
}

std::string
render_atomic_data_tools_decode_header(const AtomicBinV2Manifest& manifest, u64 offset, u32 limit) {
    std::string output =
      R"({"type":"atomic-data-tools-decode-header","contract_version":1,"status":"ok","format":"atomic-bin-v2","entrypoint":"manifest","decode_schema_sha256":)";
    output += json_string(AtomicDataToolsDecodeSchemaSha256Hex);
    output += R"(,"data_schema_sha256":)" + json_string(AtomicBinV2SchemaSha256Hex);
    output += R"(,"manifest_schema_sha256":)" + json_string(AtomicBinV2ManifestSchemaSha256Hex);
    output += R"(,"slice":{"offset":)";
    append_decimal_string(output, offset);
    output += R"(,"limit":)" + std::to_string(limit) + "}";
    output += R"(,"dataset":{"records":)";
    append_decimal_string(output, manifest.records);
    output += R"(,"shards":)" + std::to_string(manifest.shards.size());
    output += R"(,"atomic960":)";
    append_bool(output, manifest.atomic960);
    output += R"(},"provenance":{"engine":{"commit":)" + json_string(manifest.engineCommit);
    output += R"(,"version":)" + json_string(manifest.engineVersion) + "}";
    output += R"(,"network":{"file":)" + json_string(filename_utf8(manifest.networkPath));
    output += R"(,"sha256":)" + json_string(manifest.networkSha256) + "}";
    output +=
      R"(,"book":{"kind":)" + json_string(manifest.bookIsFile ? "file" : "builtin-startpos");
    output += R"(,"file":)";
    if (manifest.bookIsFile)
        output += json_string(filename_utf8(manifest.bookPath));
    else
        output += "null";
    output += R"(,"sha256":)";
    if (manifest.bookIsFile)
        output += json_string(manifest.bookSha256);
    else
        output += "null";
    output += R"(},"generation":{"resolved_seed":)";
    append_decimal_string(output, manifest.resolvedSeed);
    output += R"(,"atomic960":)";
    append_bool(output, manifest.atomic960);
    output += R"(,"threads":)" + std::to_string(manifest.threads);
    output += R"(,"hash_mb":)";
    append_decimal_string(output, manifest.hashMb);
    output += R"(,"use_nnue":"pure","options":)";
    append_manifest_options(output, manifest.options);
    output += "}}}\n";
    return output;
}

std::string render_atomic_data_tools_decode_record(const AtomicBinV2DecodedRecord& record) {
    const auto& fields    = record.fields;
    const auto& position  = fields.position;
    const bool  atomic960 = bool(fields.flags & ATOMIC_BIN_V2_ATOMIC960);
    const u32   moveWire  = u32(fields.move.from) | (u32(fields.move.to) << 6)
                       | (u32(fields.move.type) << 12) | (u32(fields.move.promotion) << 16);

    std::string output =
      R"({"type":"atomic-data-tools-decode-record","contract_version":1,"global_index":)";
    append_decimal_string(output, record.globalIndex);
    output += R"(,"shard_index":)";
    append_decimal_string(output, record.shardIndex);
    output += R"(,"local_index":)";
    append_decimal_string(output, record.localIndex);
    output += R"(,"position":{"fen":)" + json_string(record.sample.fen);
    output += R"(,"fen_notation":)" + json_string(atomic960 ? "shredder-fen" : "fen");
    output += R"(,"side_to_move":)"
            + json_string(position.sideToMove == ATOMIC_BIN_V2_WHITE_TO_MOVE ? "white" : "black");
    output += R"(,"rule50":)" + std::to_string(position.rule50);
    output += R"(,"fullmove":)" + std::to_string(position.fullmove);
    output += R"(,"castling_rights":{"wire":)" + std::to_string(position.castlingRights);
    output += R"(,"fen":)" + json_string(castling_token(fields)) + "}";
    output += R"(,"castling_rook_origins":{"white_kingside":)";
    append_optional_square(output, position.castlingRookOrigins[0]);
    output += R"(,"white_queenside":)";
    append_optional_square(output, position.castlingRookOrigins[1]);
    output += R"(,"black_kingside":)";
    append_optional_square(output, position.castlingRookOrigins[2]);
    output += R"(,"black_queenside":)";
    append_optional_square(output, position.castlingRookOrigins[3]);
    output += R"(},"en_passant":)";
    append_optional_square(output, position.enPassantSquare);
    output += R"(},"score_stm":)" + std::to_string(fields.score);
    output += R"(,"ply":)";
    append_decimal_string(output, fields.ply);
    output += R"(,"result_stm":)" + std::to_string(int(fields.result));
    output += R"(,"flags":)" + std::to_string(fields.flags);
    output += R"(,"atomic960":)";
    append_bool(output, atomic960);
    output += R"(,"move":{"wire":)";
    append_decimal_string(output, moveWire);
    output += R"(,"from":)" + json_string(square_name(fields.move.from));
    // Atomic BIN V2 intentionally stores a castling rook origin in `to`, not
    // the king's orthodox UCI destination square.
    output += R"(,"to":)" + json_string(square_name(fields.move.to));
    output += R"(,"type":)" + json_string(move_type_name(fields.move.type));
    output += R"(,"promotion":)" + json_string(promotion_name(fields.move.promotion));
    output += "}}\n";
    return output;
}

std::string render_atomic_data_tools_decode_footer(const AtomicDataToolsValidationStats& stats,
                                                   u64                                   offset,
                                                   u32                                   limit) {
    std::string output =
      R"({"type":"atomic-data-tools-decode-footer","contract_version":1,"status":"ok","format":"atomic-bin-v2","slice":{"offset":)";
    append_decimal_string(output, offset);
    output += R"(,"limit":)" + std::to_string(limit) + R"(,"records":)";
    append_decimal_string(output, limit);
    output += R"(},"validation":{"status":"ok","shards":)" + std::to_string(stats.shards);
    output += R"(,"records":)";
    append_decimal_string(output, stats.records);
    output += R"(,"side_to_move_wins":)";
    append_decimal_string(output, stats.sideToMoveWins);
    output += R"(,"draws":)";
    append_decimal_string(output, stats.draws);
    output += R"(,"side_to_move_losses":)";
    append_decimal_string(output, stats.sideToMoveLosses);
    output += R"(,"atomic960_records":)";
    append_decimal_string(output, stats.atomic960RecordCount);
    output += "}}\n";
    return output;
}

}  // namespace Stockfish::Data
