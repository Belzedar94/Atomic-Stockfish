/*
  Atomic-Stockfish training-data generator
  Copyright (C) 2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef DATA_ATOMIC_V3_PRIVATE_STAGING_H_INCLUDED
#define DATA_ATOMIC_V3_PRIVATE_STAGING_H_INCLUDED

#include <filesystem>
#include <string>
#include <system_error>
#include <utility>

#include "training_data.h"

namespace Stockfish::Data {

// remove_all() failures must be part of the producer result: private feature
// audits and role staging files can contain unpublished training data. Keeping
// the operation injectable makes the failure contract portable to test on
// filesystems where an open file can still be unlinked.
template<typename RemoveAll>
DataResult cleanup_atomic_v3_private_staging(const std::filesystem::path& directory,
                                             RemoveAll&&                  removeAll) {
    std::error_code error;
    std::forward<RemoveAll>(removeAll)(directory, error);
    if (error)
        return DataResult::failure(DataError::ABORT_FAILED,
                                   "Cannot remove Atomic V3 private staging directory: "
                                     + error.message());
    return DataResult::success();
}

inline DataResult cleanup_atomic_v3_private_staging(const std::filesystem::path& directory) {
    return cleanup_atomic_v3_private_staging(
      directory, [](const std::filesystem::path& path, std::error_code& error) {
          (void) std::filesystem::remove_all(path, error);
      });
}

}  // namespace Stockfish::Data

#endif  // DATA_ATOMIC_V3_PRIVATE_STAGING_H_INCLUDED
