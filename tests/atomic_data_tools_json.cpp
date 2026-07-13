/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>

#include "data/atomic_data_tools_json.h"

namespace {

void check(bool condition, const char* message) {
    if (!condition)
    {
        std::cerr << "Atomic data-tools JSON test failed: " << message << '\n';
        std::exit(EXIT_FAILURE);
    }
}

}  // namespace

int main() {
    constexpr Stockfish::u64 AboveJsSafeInteger = 9007199254740993ULL;
    constexpr Stockfish::u64 Uint64Max          = std::numeric_limits<Stockfish::u64>::max();

    const Stockfish::Data::AtomicDataToolsValidationStats stats{
      100000, AboveJsSafeInteger, Uint64Max, AboveJsSafeInteger, Uint64Max, AboveJsSafeInteger};
    const std::string rendered = Stockfish::Data::render_atomic_data_tools_validation_json(stats);
    const std::string expected =
      R"({"type":"atomic-data-tools-validation","contract_version":1,"status":"ok","format":"atomic-bin-v2","entrypoint":"manifest","shards":100000,"records":"9007199254740993","side_to_move_wins":"18446744073709551615","draws":"9007199254740993","side_to_move_losses":"18446744073709551615","atomic960_records":"9007199254740993"})"
      "\n";

    check(rendered == expected, "uint64 counters or numeric shard encoding changed");
    check(rendered.find(R"("shards":"100000")") == std::string::npos,
          "manifest-bounded shard count became a string");
    check(rendered.find('\r') == std::string::npos, "canonical response contains CR");

    Stockfish::Data::AtomicBinV2DecodedRecord record{};
    record.globalIndex = Uint64Max;
    record.shardIndex  = AboveJsSafeInteger;
    record.localIndex  = Uint64Max;
    record.sample.fen  = "7k/8/8/8/8/8/4P3/K7 w - - 0 1";
    record.fields.position.castlingRookOrigins.fill(Stockfish::Data::AtomicBinV2NoSquare);
    record.fields.position.enPassantSquare = Stockfish::Data::AtomicBinV2NoSquare;
    record.fields.position.fullmove        = 1;
    record.fields.move.from                = Stockfish::u8(Stockfish::SQ_E2);
    record.fields.move.to                  = Stockfish::u8(Stockfish::SQ_E3);
    record.fields.ply                      = std::numeric_limits<Stockfish::u32>::max();
    const std::string decoded = Stockfish::Data::render_atomic_data_tools_decode_record(record);
    check(decoded.find(R"("global_index":"18446744073709551615")") != std::string::npos,
          "decode global index is not a lossless decimal string");
    check(decoded.find(R"("shard_index":"9007199254740993")") != std::string::npos,
          "decode shard index is not a lossless decimal string");
    check(decoded.find(R"("local_index":"18446744073709551615")") != std::string::npos,
          "decode local index is not a lossless decimal string");
    check(decoded.find(R"("ply":"4294967295")") != std::string::npos,
          "decode UINT32_MAX ply is not a lossless decimal string");
    check(decoded.find(R"("wire":"1292")") != std::string::npos,
          "decode move wire is not a lossless decimal string");
    check(decoded.find('\r') == std::string::npos && decoded.back() == '\n',
          "decode record is not canonical LF JSONL");

    const std::string footer =
      Stockfish::Data::render_atomic_data_tools_decode_footer(stats, Uint64Max, 4096);
    check(footer.find(R"("offset":"18446744073709551615")") != std::string::npos,
          "decode footer offset is not a lossless decimal string");
    check(footer.find(R"("limit":4096,"records":"4096")") != std::string::npos,
          "decode footer slice summary changed");
    std::cout << "Atomic data-tools JSON tests passed\n";
    return EXIT_SUCCESS;
}
