/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "nnue_dispatcher.h"

#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "../misc.h"

namespace Stockfish::Eval::NNUE {

namespace fs = std::filesystem;

namespace {

fs::path requested_path(fs::path path) {
    if (path.empty())
        path = EvalFile::defaultName;
    return path;
}

void report_incompatible(const std::function<void(std::string_view)>& onVerify,
                         const fs::path&                              path) {
    if (!onVerify)
        return;

    const std::string message =
      "ERROR: Use NNUE is enabled, but a compatible Legacy Atomic V1, AtomicNNUEV2, or "
      "AtomicNNUEV3 "
      "network is not available.\n"
      "ERROR: The network file "
      + path.string()
      + " was not loaded successfully.\n"
        "ERROR: The UCI option EvalFile might need to specify the full path, including the "
        "directory name, to the network file.\n"
        "ERROR: Search was not started; the engine remains available.\n";
    onVerify(message);
}

std::vector<fs::path> requested_candidates(const fs::path& rootDirectory,
                                           const fs::path& evalfilePath) {
    std::vector<fs::path> candidates;
    if (evalfilePath.empty())
        return candidates;

    if (evalfilePath.is_absolute())
        candidates.push_back(evalfilePath);
    else
    {
        candidates.push_back(evalfilePath);
        const fs::path rooted = rootDirectory / evalfilePath;
        if (rooted.lexically_normal() != evalfilePath.lexically_normal())
            candidates.push_back(rooted);
    }
    return candidates;
}

}  // namespace

void AnyNetwork::activate_legacy() noexcept {
    switch (backend_)
    {
    case NetworkBackend::LegacyAtomicV1 :
        storage_.legacy.~Network();
        break;
    case NetworkBackend::AtomicNNUEV2 :
        storage_.atomicV2.~Network();
        break;
    case NetworkBackend::AtomicNNUEV3 :
        storage_.atomicV3.~Network();
        break;
    }
    ::new (static_cast<void*>(&storage_.legacy)) LegacyAtomicV1::Network{};
    backend_ = NetworkBackend::LegacyAtomicV1;
}

void AnyNetwork::activate_v2() noexcept {
    switch (backend_)
    {
    case NetworkBackend::LegacyAtomicV1 :
        storage_.legacy.~Network();
        break;
    case NetworkBackend::AtomicNNUEV2 :
        storage_.atomicV2.~Network();
        break;
    case NetworkBackend::AtomicNNUEV3 :
        storage_.atomicV3.~Network();
        break;
    }
    ::new (static_cast<void*>(&storage_.atomicV2)) AtomicV2::Network{};
    backend_ = NetworkBackend::AtomicNNUEV2;
}

void AnyNetwork::activate_v3() noexcept {
    switch (backend_)
    {
    case NetworkBackend::LegacyAtomicV1 :
        storage_.legacy.~Network();
        break;
    case NetworkBackend::AtomicNNUEV2 :
        storage_.atomicV2.~Network();
        break;
    case NetworkBackend::AtomicNNUEV3 :
        storage_.atomicV3.~Network();
        break;
    }
    ::new (static_cast<void*>(&storage_.atomicV3)) AtomicV3::Network{};
    backend_ = NetworkBackend::AtomicNNUEV3;
}

bool AnyNetwork::load(const fs::path& rootDirectory,
                      const fs::path& evalfilePath,
                      EvalFile&       evalFile) {
    const fs::path requested = requested_path(evalfilePath);

    // Modern networks are external-only. Preserve the historical path order:
    // probe every supported backend at the requested path before moving on to
    // the binary-root-relative path. Otherwise a rooted V3 could incorrectly
    // shadow a cwd V2 (or Legacy V1) with the same relative EvalFile name.
    if (!evalfilePath.empty())
    {
        for (const fs::path& candidate : requested_candidates(rootDirectory, evalfilePath))
        {
            activate_v3();
            auto v3 = AtomicV3::load_candidate(candidate, storage_.atomicV3);
            if (v3)
            {
                evalFile.current        = requested;
                evalFile.netDescription = std::move(v3.description);
                return true;
            }

            activate_v2();
            std::ifstream v2Stream(candidate, std::ios::binary);
            if (v2Stream)
            {
                auto v2 = AtomicV2::load_candidate(v2Stream, storage_.atomicV2);
                if (v2)
                {
                    evalFile.current        = requested;
                    evalFile.netDescription = std::move(v2.description);
                    return true;
                }
            }

            activate_legacy();
            std::ifstream legacyStream(candidate, std::ios::binary);
            EvalFile      legacyFile{std::nullopt, ""};
            if (legacyStream
                && storage_.legacy.load_authenticated(legacyStream, requested, legacyFile))
            {
                evalFile = std::move(legacyFile);
                return true;
            }
        }
    }

    activate_legacy();
    EvalFile candidateFile{std::nullopt, ""};
    storage_.legacy.load(rootDirectory, evalfilePath, candidateFile);
    if (candidateFile.current != requested)
        return false;

    evalFile = std::move(candidateFile);
    return true;
}

bool AnyNetwork::load_authenticated(std::istream&   stream,
                                    const fs::path& logicalPath,
                                    EvalFile&       evalFile) {
    // Both parsers require the complete stream and are intentionally tried on
    // independent snapshots. This method never reopens logicalPath.
    std::ostringstream captured(std::ios::binary);
    captured << stream.rdbuf();
    if (!stream.eof() && stream.fail())
        return false;
    const std::string bytes = std::move(captured).str();

    activate_v3();
    std::istringstream v3Stream(bytes, std::ios::binary);
    auto               v3 = AtomicV3::load_candidate(v3Stream, storage_.atomicV3);
    if (v3)
    {
        evalFile.current        = logicalPath;
        evalFile.netDescription = std::move(v3.description);
        return true;
    }

    activate_v2();
    std::istringstream v2Stream(bytes, std::ios::binary);
    auto               v2 = AtomicV2::load_candidate(v2Stream, storage_.atomicV2);
    if (v2)
    {
        evalFile.current        = logicalPath;
        evalFile.netDescription = std::move(v2.description);
        return true;
    }

    activate_legacy();
    std::istringstream legacyStream(bytes, std::ios::binary);
    EvalFile           candidateFile{std::nullopt, ""};
    if (!storage_.legacy.load_authenticated(legacyStream, logicalPath, candidateFile))
        return false;
    evalFile = std::move(candidateFile);
    return true;
}

bool AnyNetwork::save(const EvalFile& evalFile, const std::optional<fs::path>& filename) const {
    if (backend_ == NetworkBackend::LegacyAtomicV1)
        return storage_.legacy.save(evalFile, filename);

    if (!evalFile.current.has_value())
    {
        sync_cout << "Failed to export a net. No network file is currently loaded. "
                     "Please load a network file first."
                  << sync_endl;
        return false;
    }

    if (!filename.has_value() && evalFile.current != evalFile.defaultName)
    {
        sync_cout << "Failed to export a net. A non-embedded net can only be saved if the "
                     "filename is specified"
                  << sync_endl;
        return false;
    }

    const fs::path actualFilename = filename.value_or(evalFile.defaultName);
    std::ofstream  stream(actualFilename, std::ios::binary);
    bool           saved = false;
    if (stream)
    {
        if (backend_ == NetworkBackend::AtomicNNUEV2)
            saved = storage_.atomicV2.save(stream, evalFile.netDescription);
        else
            saved = bool(storage_.atomicV3.save(stream, evalFile.netDescription));
    }
    stream.flush();
    saved = saved && bool(stream);

    sync_cout << (saved ? "Network saved successfully to " + actualFilename.string()
                        : "Failed to export a net")
              << sync_endl;
    return saved;
}

usize AnyNetwork::get_content_hash() const {
    usize hash = 0;
    hash_combine(hash, static_cast<usize>(backend_));
    switch (backend_)
    {
    case NetworkBackend::LegacyAtomicV1 :
        hash_combine(hash, storage_.legacy.get_content_hash());
        break;
    case NetworkBackend::AtomicNNUEV2 :
        hash_combine(hash, storage_.atomicV2.get_content_hash());
        break;
    case NetworkBackend::AtomicNNUEV3 :
        hash_combine(hash, storage_.atomicV3.get_content_hash());
        break;
    }
    return hash;
}

bool AnyNetwork::verify(const std::function<void(std::string_view)>& onVerify,
                        const EvalFile&                              evalFile,
                        fs::path                                     evalfilePath) const {
    evalfilePath = requested_path(std::move(evalfilePath));
    if (evalFile.current != evalfilePath)
    {
        report_incompatible(onVerify, evalfilePath);
        return false;
    }

    if (backend_ == NetworkBackend::LegacyAtomicV1)
    {
        auto reportLegacy = [&onVerify](std::string_view message) {
            if (!onVerify)
                return;
            constexpr std::string_view prefix = "NNUE evaluation using ";
            if (message.substr(0, prefix.size()) == prefix)
                onVerify(std::string(prefix) + "Legacy Atomic V1 "
                         + std::string(message.substr(prefix.size())));
            else
                onVerify(message);
        };
        return storage_.legacy.verify(reportLegacy, evalFile, evalfilePath);
    }

    if (backend_ == NetworkBackend::AtomicNNUEV2 && !storage_.atomicV2.initialized())
    {
        report_incompatible(onVerify, evalfilePath);
        return false;
    }

    if (backend_ == NetworkBackend::AtomicNNUEV2 && onVerify)
    {
        const auto shape = AtomicV2::Network::shape();
        onVerify("NNUE evaluation using AtomicNNUEV2 " + evalfilePath.string() + " ("
                 + std::to_string(sizeof(AtomicV2::Network) / (1024 * 1024)) + "MiB, ("
                 + std::to_string(shape.featureDimensions) + ", "
                 + std::to_string(shape.transformedDimensions) + ", "
                 + std::to_string(shape.fc0Outputs) + ", " + std::to_string(shape.fc1Outputs)
                 + ", 1))");
    }
    if (backend_ == NetworkBackend::AtomicNNUEV2)
        return true;

    if (!storage_.atomicV3.simd_permuted() || !storage_.atomicV3.dense_runtime_ready()
        || storage_.atomicV3.get_content_hash() == 0)
    {
        report_incompatible(onVerify, evalfilePath);
        return false;
    }

    if (onVerify)
    {
        constexpr usize FeatureDimensions =
          AtomicV3::HmPhysicalDimensions + AtomicV3::CapturePairPhysicalDimensions
          + AtomicV3::KingBlastEpPhysicalDimensions + AtomicV3::BlastRingPhysicalDimensions;
        onVerify("NNUE evaluation using AtomicNNUEV3 " + evalfilePath.string() + " ("
                 + std::to_string(sizeof(AtomicV3::Network) / (1024 * 1024)) + "MiB, ("
                 + std::to_string(FeatureDimensions) + ", "
                 + std::to_string(AtomicV3::AccumulatorDimensions) + ", "
                 + std::to_string(AtomicV3::Fc0Outputs) + ", "
                 + std::to_string(AtomicV3::Fc1Outputs) + ", 1))");
    }
    return true;
}

}  // namespace Stockfish::Eval::NNUE
