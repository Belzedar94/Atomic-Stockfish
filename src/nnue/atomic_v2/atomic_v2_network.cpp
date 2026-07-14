/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "network.h"

#include <algorithm>
#include <fstream>
#include <limits>
#include <vector>

#include "../../position.h"
#include "../nnue_misc.h"
#include "io.h"
#include "nnz_helper.h"

namespace Stockfish::Eval::NNUE::AtomicV2 {

namespace fs = std::filesystem;

namespace {

constexpr u32 MaxDescriptionSize = 1U << 20;

template<typename Component>
bool read_component(std::istream&    stream,
                    Component&       component,
                    u32              expectedHash,
                    std::string&     error,
                    std::string_view label) {
    u32 actualHash = 0;
    if (!IO::read_little_endian(stream, actualHash))
    {
        error = "truncated " + std::string(label) + " hash";
        return false;
    }
    if (actualHash != expectedHash)
    {
        error = "incompatible " + std::string(label) + " hash";
        return false;
    }
    if (!component.read_parameters(stream))
    {
        error = "invalid or truncated " + std::string(label) + " parameters";
        return false;
    }
    return true;
}

template<typename Component>
bool write_component(std::ostream& stream, const Component& component) {
    return IO::write_little_endian(stream, Component::get_hash_value())
        && component.write_parameters(stream);
}

}  // namespace

RawNetworkOutput Network::evaluate_raw(const Position&    pos,
                                       AccumulatorStack&  accumulatorStack,
                                       AccumulatorCaches& cache) const {
    assert(initialized_);

    alignas(CacheLineSize) TransformedFeatureType transformed[FeatureTransformer::BufferSize];
    ASSERT_ALIGNED(transformed, CacheLineSize);

    NNZInfo<L1> nnzInfo{};
    const int   bucket =
      std::clamp((pos.count<ALL_PIECES>() - 1) / 4, 0, static_cast<int>(LayerStacks) - 1);
    const i32 psqt =
      featureTransformer_.transform(pos, accumulatorStack, cache, transformed, bucket, nnzInfo);
    const i32 positional = networks_[bucket].propagate(transformed, nnzInfo);
    return {psqt, positional};
}

NetworkOutput Network::evaluate(const Position&    pos,
                                AccumulatorStack&  accumulatorStack,
                                AccumulatorCaches& cache) const {
    const auto [psqt, positional] = evaluate_raw(pos, accumulatorStack, cache);
    return {static_cast<Value>(psqt / OutputScale), static_cast<Value>(positional / OutputScale)};
}

NnueEvalTrace Network::trace_evaluate(const Position&    pos,
                                      AccumulatorStack&  accumulatorStack,
                                      AccumulatorCaches& cache) const {
    assert(initialized_);

    alignas(CacheLineSize) TransformedFeatureType transformed[FeatureTransformer::BufferSize];
    ASSERT_ALIGNED(transformed, CacheLineSize);

    NnueEvalTrace trace{};
    trace.correctBucket =
      std::clamp((pos.count<ALL_PIECES>() - 1) / 4, 0, static_cast<int>(LayerStacks) - 1);
    for (IndexType bucket = 0; bucket < LayerStacks; ++bucket)
    {
        NNZInfo<L1> nnzInfo{};
        const i32   psqt =
          featureTransformer_.transform(pos, accumulatorStack, cache, transformed, bucket, nnzInfo);
        const i32 positional = networks_[bucket].propagate(transformed, nnzInfo);

        trace.psqt[bucket]       = static_cast<Value>(psqt / OutputScale);
        trace.positional[bucket] = static_cast<Value>(positional / OutputScale);
    }

    return trace;
}

usize Network::get_content_hash() const noexcept {
    if (!initialized_)
        return 0;

    usize hash = 0;
    hash_combine(hash, static_cast<usize>(FileVersion));
    hash_combine(hash, static_cast<usize>(NetworkHash));
    hash_combine(hash, featureTransformer_);
    for (const auto& architecture : networks_)
        hash_combine(hash, architecture);
    return hash;
}

bool Network::read_parameters(std::istream& stream, std::string& description, std::string& error) {
    u32 version = 0;
    u32 hash    = 0;
    u32 size    = 0;
    if (!IO::read_little_endian(stream, version) || !IO::read_little_endian(stream, hash)
        || !IO::read_little_endian(stream, size))
    {
        error = "truncated Atomic V2 header";
        return false;
    }
    if (version != FileVersion)
    {
        error = "incompatible Atomic V2 file version";
        return false;
    }
    if (hash != NetworkHash)
    {
        error = "incompatible Atomic V2 network hash";
        return false;
    }
    if (size > MaxDescriptionSize)
    {
        error = "Atomic V2 description exceeds 1 MiB";
        return false;
    }

    description.resize(size);
    if (size != 0)
        stream.read(description.data(), std::streamsize(size));
    if (!stream)
    {
        error = "truncated Atomic V2 description";
        return false;
    }

    if (!read_component(stream, featureTransformer_, FeatureTransformerHash, error,
                        "feature transformer"))
        return false;

    for (usize bucket = 0; bucket < LayerStacks; ++bucket)
        if (!read_component(stream, networks_[bucket], ArchitectureHash, error,
                            "architecture bucket " + std::to_string(bucket)))
            return false;

    char trailing;
    if (stream.get(trailing))
    {
        error = "trailing bytes after Atomic V2 network";
        return false;
    }
    if (!stream.eof() || stream.bad())
    {
        error = "I/O error while checking Atomic V2 end of file";
        return false;
    }

    initialized_ = true;
    return true;
}

bool Network::write_parameters(std::ostream& stream, std::string_view description) const {
    if (description.size() > MaxDescriptionSize
        || description.size() > std::numeric_limits<u32>::max())
        return false;

    if (!IO::write_little_endian(stream, FileVersion)
        || !IO::write_little_endian(stream, NetworkHash)
        || !IO::write_little_endian(stream, static_cast<u32>(description.size())))
        return false;

    if (!description.empty())
        stream.write(description.data(), std::streamsize(description.size()));
    if (!stream || !write_component(stream, featureTransformer_))
        return false;

    for (const auto& architecture : networks_)
        if (!write_component(stream, architecture))
            return false;

    return bool(stream);
}

bool Network::save(std::ostream& stream, std::string_view description) const {
    return initialized_ && write_parameters(stream, description);
}

LoadResult load_candidate(std::istream& stream, Network& destination) {
    LoadResult result;
    destination.initialized_ = false;
    result.success = destination.read_parameters(stream, result.description, result.error);
    return result;
}

LoadResult
load_candidate(const fs::path& rootDirectory, const fs::path& evalfilePath, Network& destination) {
    LoadResult result;
    if (evalfilePath.empty())
    {
        result.error = "empty Atomic V2 network path";
        return result;
    }

    std::vector<fs::path> candidates;
    if (evalfilePath.is_absolute())
        candidates.push_back(evalfilePath);
    else
    {
        candidates.push_back(evalfilePath);
        const fs::path rooted = rootDirectory / evalfilePath;
        if (rooted.lexically_normal() != evalfilePath.lexically_normal())
            candidates.push_back(rooted);
    }

    std::string lastError;
    for (const fs::path& candidate : candidates)
    {
        std::ifstream stream(candidate, std::ios::binary);
        if (!stream)
            continue;

        result              = load_candidate(stream, destination);
        result.resolvedPath = candidate;
        if (result)
            return result;
        lastError = result.error;
    }

    result.success = false;
    result.error   = lastError.empty() ? "Atomic V2 network file was not found" : lastError;
    return result;
}

}  // namespace Stockfish::Eval::NNUE::AtomicV2
