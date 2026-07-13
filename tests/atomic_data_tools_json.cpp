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
    std::cout << "Atomic data-tools JSON tests passed\n";
    return EXIT_SUCCESS;
}
