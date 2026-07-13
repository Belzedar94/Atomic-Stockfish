/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_data_tools_json.h"

#include <string>

namespace Stockfish::Data {

namespace {

void append_decimal_string(std::string& output, u64 value) {
    output.push_back('"');
    output += std::to_string(value);
    output.push_back('"');
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

}  // namespace Stockfish::Data
