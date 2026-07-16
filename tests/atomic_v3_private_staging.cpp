/*
  Atomic-Stockfish training-data generator tests
  Copyright (C) 2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <system_error>

#include "data/atomic_v3_private_staging.h"

using namespace Stockfish::Data;

int main() {
    const auto fail = [](const char* message) {
        std::cerr << "FAIL Atomic V3 private staging cleanup: " << message << '\n';
        return 1;
    };
    const auto suffix = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto root   = std::filesystem::temp_directory_path()
                    / ("atomic-v3-private-staging-test-" + std::to_string(suffix));
    std::filesystem::create_directories(root);
    std::ofstream(root / "private.bin", std::ios::binary).put('x');

    bool             injected = false;
    const DataResult failed   = cleanup_atomic_v3_private_staging(
      root, [&](const std::filesystem::path& path, std::error_code& error) {
          injected = path == root;
          error    = std::make_error_code(std::errc::permission_denied);
      });
    if (!injected)
        return fail("fault operation did not receive the owned directory");
    if (failed || failed.error != DataError::ABORT_FAILED)
        return fail("injected remove_all failure was not propagated");
    if (failed.message.find("private staging directory") == std::string::npos)
        return fail("injected failure has no private-staging diagnostic");
    if (!std::filesystem::exists(root / "private.bin"))
        return fail("fault injection unexpectedly removed private data");

    const DataResult removed = cleanup_atomic_v3_private_staging(root);
    if (!removed)
        return fail("real remove_all failed after fault injection");
    if (std::filesystem::exists(root))
        return fail("real remove_all left the private directory behind");

    std::cout << "Atomic V3 private staging cleanup tests passed\n";
    return 0;
}
