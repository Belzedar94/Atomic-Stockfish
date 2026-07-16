/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "wire_network.h"

#include <algorithm>
#include <cassert>
#include <fstream>
#include <new>
#include <utility>

#include "numeric.h"
#include "wire_io.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

void set_error(WireError code, std::string message, WireError& resultCode, std::string& result) {
    resultCode = code;
    result     = std::move(message);
}

bool hard_read_failure(const std::istream& stream) noexcept {
    return stream.bad() || (stream.fail() && !stream.eof());
}

void set_read_error(std::istream& stream,
                    WireError     sectionCode,
                    std::string   sectionMessage,
                    WireError&    resultCode,
                    std::string&  result) {
    if (hard_read_failure(stream))
        set_error(WireError::IoError, wire_error_message(WireError::IoError), resultCode, result);
    else
        set_error(sectionCode, std::move(sectionMessage), resultCode, result);
}

bool read_dense_biases(std::istream& stream, i32* values, std::size_t count) {
    for (std::size_t index = 0; index < count; ++index)
        if (!WireIO::read_little_endian(stream, values[index]))
            return false;
    return true;
}

bool write_dense_biases(std::ostream& stream, const i32* values, std::size_t count) {
    for (std::size_t index = 0; index < count; ++index)
        if (!WireIO::write_little_endian(stream, values[index]))
            return false;
    return true;
}

std::string dense_label(std::size_t bucket, std::string_view layer, std::size_t output) {
    return "Atomic V3 dense bucket " + std::to_string(bucket) + " " + std::string(layer)
         + " output " + std::to_string(output);
}

bool validate_affine_output(i32                 bias,
                            const i8*           weights,
                            std::size_t         count,
                            AffineOutputBounds& bounds,
                            std::size_t         bucket,
                            std::string_view    layer,
                            std::size_t         output,
                            WireError&          code,
                            std::string&        error) {
    const NumericError numeric = affine_output_bounds(bias, weights, count, bounds);
    if (numeric == NumericError::None)
        return true;

    set_error(WireError::AffineRangeExceeded,
              dense_label(bucket, layer, output) + ": " + numeric_error_message(numeric), code,
              error);
    return false;
}

SaveResult save_error(WireError code, std::string message) {
    SaveResult result;
    result.code  = code;
    result.error = std::move(message);
    return result;
}

void hash_content(u64& hash, const void* data, std::size_t size) noexcept {
    const auto* bytes = static_cast<const unsigned char*>(data);
    for (std::size_t index = 0; index < size; ++index)
    {
        hash ^= bytes[index];
        hash *= 1099511628211ULL;
    }
}

}  // namespace

const char* wire_error_message(WireError error) noexcept {
    switch (error)
    {
    case WireError::None :
        return "none";
    case WireError::TruncatedHeader :
        return "truncated Atomic V3 header";
    case WireError::WrongFileVersion :
        return "incompatible Atomic V3 file version";
    case WireError::WrongNetworkHash :
        return "incompatible Atomic V3 network hash";
    case WireError::DescriptionTooLarge :
        return "Atomic V3 description exceeds 1 MiB";
    case WireError::TruncatedDescription :
        return "truncated Atomic V3 description";
    case WireError::WrongTransformerHash :
        return "incompatible Atomic V3 feature transformer hash";
    case WireError::AllocationFailure :
        return "unable to allocate Atomic V3 parameters";
    case WireError::InvalidBiases :
        return "invalid or truncated Atomic V3 biases";
    case WireError::InvalidHmWeights :
        return "invalid or truncated Atomic V3 HM weights";
    case WireError::TruncatedCapturePair :
        return "truncated Atomic V3 CapturePair weights";
    case WireError::InvalidKingBlastEp :
        return "invalid or truncated Atomic V3 KingBlastEP weights";
    case WireError::TruncatedBlastRing :
        return "truncated Atomic V3 BlastRing weights";
    case WireError::InvalidPsqt :
        return "invalid or truncated Atomic V3 HM PSQT weights";
    case WireError::WrongArchitectureHash :
        return "incompatible Atomic V3 dense architecture hash";
    case WireError::TruncatedArchitecture :
        return "truncated Atomic V3 dense architecture";
    case WireError::PsqtRangeExceeded :
        return "Atomic V3 HM PSQT numeric envelope exceeded";
    case WireError::AffineRangeExceeded :
        return "Atomic V3 affine numeric envelope exceeded";
    case WireError::RawOutputRangeExceeded :
        return "Atomic V3 raw output numeric envelope exceeded";
    case WireError::InvalidPermutationShape :
        return "Atomic V3 parameter tensor cannot be permuted canonically";
    case WireError::TrailingBytes :
        return "trailing bytes after Atomic V3 network";
    case WireError::IoError :
        return "I/O error while reading Atomic V3 network";
    case WireError::NotInitialized :
        return "Atomic V3 network is not initialized";
    case WireError::OutputDescriptionTooLarge :
        return "Atomic V3 output description exceeds 1 MiB";
    case WireError::OutputError :
        return "I/O error while writing Atomic V3 network";
    case WireError::FileNotFound :
        return "Atomic V3 network file was not found";
    }
    return "unknown Atomic V3 wire error";
}

bool Network::allocate_parameters() noexcept { return true; }

bool Network::read_feature_parameters(std::istream& stream, WireError& code, std::string& error) {
    if (!WireIO::read_signed_leb(stream, biases_.values, BiasCount))
    {
        set_read_error(stream, WireError::InvalidBiases,
                       wire_error_message(WireError::InvalidBiases), code, error);
        return false;
    }
    if (!WireIO::read_signed_leb(stream, hmWeights_.values, HmWeightCount))
    {
        set_read_error(stream, WireError::InvalidHmWeights,
                       wire_error_message(WireError::InvalidHmWeights), code, error);
        return false;
    }
    if (!WireIO::read_exact(stream, capturePairWeights_.values, CapturePairWeightCount))
    {
        set_read_error(stream, WireError::TruncatedCapturePair,
                       wire_error_message(WireError::TruncatedCapturePair), code, error);
        return false;
    }
    if (!WireIO::read_signed_leb(stream, kingBlastEpWeights_.values, KingBlastEpWeightCount))
    {
        set_read_error(stream, WireError::InvalidKingBlastEp,
                       wire_error_message(WireError::InvalidKingBlastEp), code, error);
        return false;
    }
    if (!WireIO::read_exact(stream, blastRingWeights_.values, BlastRingWeightCount))
    {
        set_read_error(stream, WireError::TruncatedBlastRing,
                       wire_error_message(WireError::TruncatedBlastRing), code, error);
        return false;
    }
    if (!WireIO::read_signed_leb(stream, hmPsqtWeights_.values, HmPsqtWeightCount))
    {
        set_read_error(stream, WireError::InvalidPsqt, wire_error_message(WireError::InvalidPsqt),
                       code, error);
        return false;
    }
    return true;
}

bool Network::read_dense_parameters(std::istream& stream, WireError& code, std::string& error) {
    for (std::size_t bucket = 0; bucket < denseStacks_.size(); ++bucket)
    {
        u32 actualHash = 0;
        if (!WireIO::read_little_endian(stream, actualHash))
        {
            set_read_error(stream, WireError::TruncatedArchitecture,
                           "truncated Atomic V3 architecture hash at bucket "
                             + std::to_string(bucket),
                           code, error);
            return false;
        }
        if (actualHash != ArchitectureHash)
        {
            set_error(WireError::WrongArchitectureHash,
                      "incompatible Atomic V3 architecture hash at bucket "
                        + std::to_string(bucket),
                      code, error);
            return false;
        }

        auto& stack = denseStacks_[bucket];
        if (!read_dense_biases(stream, stack.fc0Biases.data(), stack.fc0Biases.size())
            || !WireIO::read_exact(stream, stack.fc0Weights.data(), stack.fc0Weights.size())
            || !read_dense_biases(stream, stack.fc1Biases.data(), stack.fc1Biases.size())
            || !WireIO::read_exact(stream, stack.fc1Weights.data(), stack.fc1Weights.size())
            || !read_dense_biases(stream, stack.fc2Biases.data(), stack.fc2Biases.size())
            || !WireIO::read_exact(stream, stack.fc2Weights.data(), stack.fc2Weights.size()))
        {
            set_read_error(stream, WireError::TruncatedArchitecture,
                           "truncated Atomic V3 dense parameters at bucket "
                             + std::to_string(bucket),
                           code, error);
            return false;
        }
    }
    return true;
}

bool Network::validate_numeric(WireError& code, std::string& error) const {
    HmPsqtBounds       psqtBounds{};
    const NumericError psqt =
      validate_hm_psqt_weights(hmPsqtWeights_.values, HmPsqtWeightCount, psqtBounds);
    if (psqt != NumericError::None)
    {
        set_error(WireError::PsqtRangeExceeded,
                  std::string(wire_error_message(WireError::PsqtRangeExceeded)) + ": "
                    + numeric_error_message(psqt),
                  code, error);
        return false;
    }

    for (std::size_t bucket = 0; bucket < denseStacks_.size(); ++bucket)
    {
        const auto&                                stack = denseStacks_[bucket];
        std::array<AffineOutputBounds, Fc0Outputs> fc0Bounds{};
        std::array<AffineOutputBounds, Fc1Outputs> fc1Bounds{};
        AffineOutputBounds                         fc2Bounds{};

        for (std::size_t output = 0; output < fc0Bounds.size(); ++output)
            if (!validate_affine_output(stack.fc0Biases[output],
                                        stack.fc0Weights.data() + output * Fc0Inputs, Fc0Inputs,
                                        fc0Bounds[output], bucket, "fc0", output, code, error))
                return false;

        for (std::size_t output = 0; output < fc1Bounds.size(); ++output)
            if (!validate_affine_output(stack.fc1Biases[output],
                                        stack.fc1Weights.data() + output * Fc1Inputs, Fc1Inputs,
                                        fc1Bounds[output], bucket, "fc1", output, code, error))
                return false;

        if (!validate_affine_output(stack.fc2Biases[0], stack.fc2Weights.data(), Fc2Inputs,
                                    fc2Bounds, bucket, "fc2", 0, code, error))
            return false;

        SignedInterval     rawBounds{};
        const NumericError raw = validate_forward_interval(
          fc2Bounds.signedRange, fc0Bounds[30].signedRange, fc0Bounds[31].signedRange, rawBounds);
        if (raw != NumericError::None)
        {
            set_error(WireError::RawOutputRangeExceeded,
                      "Atomic V3 dense bucket " + std::to_string(bucket) + ": "
                        + numeric_error_message(raw),
                      code, error);
            return false;
        }
    }
    return true;
}

bool Network::permute_feature_parameters() noexcept {
    if (!WireIO::permute_parameters<i16, 16>(biases_.values, BiasCount)
        || !WireIO::permute_parameters<i16, 16>(hmWeights_.values, HmWeightCount)
        || !WireIO::permute_parameters<i8, 8>(capturePairWeights_.values, CapturePairWeightCount)
        || !WireIO::permute_parameters<i16, 16>(kingBlastEpWeights_.values, KingBlastEpWeightCount)
        || !WireIO::permute_parameters<i8, 8>(blastRingWeights_.values, BlastRingWeightCount))
        return false;

    simdPermuted_ = true;
    return true;
}

void Network::set_description(std::string_view description) noexcept {
    assert(description.size() <= description_.size());
    description_.fill('\0');
    std::copy(description.begin(), description.end(), description_.begin());
    descriptionSize_ = static_cast<u32>(description.size());
}

void Network::compute_content_hash() noexcept {
    u64 hash = 1469598103934665603ULL;
    hash_content(hash, &FileVersion, sizeof(FileVersion));
    hash_content(hash, &NetworkHash, sizeof(NetworkHash));
    hash_content(hash, description_.data(), descriptionSize_);
    hash_content(hash, biases_.values, sizeof(biases_.values));
    hash_content(hash, hmWeights_.values, sizeof(hmWeights_.values));
    hash_content(hash, capturePairWeights_.values, sizeof(capturePairWeights_.values));
    hash_content(hash, kingBlastEpWeights_.values, sizeof(kingBlastEpWeights_.values));
    hash_content(hash, blastRingWeights_.values, sizeof(blastRingWeights_.values));
    hash_content(hash, hmPsqtWeights_.values, sizeof(hmPsqtWeights_.values));
    hash_content(hash, denseStacks_.data(), sizeof(denseStacks_));
    contentHash_ = static_cast<usize>(hash);
    if (contentHash_ == 0)
        contentHash_ = 1;
}

SaveResult Network::write_parameters(std::ostream& stream, std::string_view description) const {
    if (!simdPermuted_)
        return save_error(WireError::NotInitialized, wire_error_message(WireError::NotInitialized));
    if (description.size() > MaximumDescriptionBytes)
        return save_error(WireError::OutputDescriptionTooLarge,
                          wire_error_message(WireError::OutputDescriptionTooLarge));

    if (!WireIO::write_little_endian(stream, FileVersion)
        || !WireIO::write_little_endian(stream, NetworkHash)
        || !WireIO::write_little_endian(stream, static_cast<u32>(description.size()))
        || !WireIO::write_exact(stream, description.data(), description.size())
        || !WireIO::write_little_endian(stream, FeatureTransformerHash)
        || !WireIO::write_signed_leb_unpermuted<i16, 16>(stream, biases_.values, BiasCount)
        || !WireIO::write_signed_leb_unpermuted<i16, 16>(stream, hmWeights_.values, HmWeightCount)
        || !WireIO::write_raw_unpermuted<i8, 8>(stream, capturePairWeights_.values,
                                                CapturePairWeightCount)
        || !WireIO::write_signed_leb_unpermuted<i16, 16>(stream, kingBlastEpWeights_.values,
                                                         KingBlastEpWeightCount)
        || !WireIO::write_raw_unpermuted<i8, 8>(stream, blastRingWeights_.values,
                                                BlastRingWeightCount)
        || !WireIO::write_signed_leb(stream, hmPsqtWeights_.values, HmPsqtWeightCount))
        return save_error(WireError::OutputError, wire_error_message(WireError::OutputError));

    for (const auto& stack : denseStacks_)
        if (!WireIO::write_little_endian(stream, ArchitectureHash)
            || !write_dense_biases(stream, stack.fc0Biases.data(), stack.fc0Biases.size())
            || !WireIO::write_exact(stream, stack.fc0Weights.data(), stack.fc0Weights.size())
            || !write_dense_biases(stream, stack.fc1Biases.data(), stack.fc1Biases.size())
            || !WireIO::write_exact(stream, stack.fc1Weights.data(), stack.fc1Weights.size())
            || !write_dense_biases(stream, stack.fc2Biases.data(), stack.fc2Biases.size())
            || !WireIO::write_exact(stream, stack.fc2Weights.data(), stack.fc2Weights.size()))
            return save_error(WireError::OutputError, wire_error_message(WireError::OutputError));

    if (!stream)
        return save_error(WireError::OutputError, wire_error_message(WireError::OutputError));
    return {};
}

SaveResult Network::save(std::ostream& stream) const {
    return write_parameters(stream, description());
}

SaveResult Network::save(std::ostream& stream, std::string_view description) const {
    return write_parameters(stream, description);
}

LoadResult load_candidate(std::istream& stream) {
    LoadResult result;

    u32 version         = 0;
    u32 networkHash     = 0;
    u32 descriptionSize = 0;
    if (!WireIO::read_little_endian(stream, version)
        || !WireIO::read_little_endian(stream, networkHash)
        || !WireIO::read_little_endian(stream, descriptionSize))
    {
        result.code  = hard_read_failure(stream) ? WireError::IoError : WireError::TruncatedHeader;
        result.error = wire_error_message(result.code);
        return result;
    }
    if (version != FileVersion)
    {
        result.code  = WireError::WrongFileVersion;
        result.error = wire_error_message(result.code);
        return result;
    }
    if (networkHash != NetworkHash)
    {
        result.code  = WireError::WrongNetworkHash;
        result.error = wire_error_message(result.code);
        return result;
    }
    if (descriptionSize > MaximumDescriptionBytes)
    {
        result.code  = WireError::DescriptionTooLarge;
        result.error = wire_error_message(result.code);
        return result;
    }

    std::string description(descriptionSize, '\0');
    if (!WireIO::read_exact(stream, description.data(), description.size()))
    {
        result.code =
          hard_read_failure(stream) ? WireError::IoError : WireError::TruncatedDescription;
        result.error = wire_error_message(result.code);
        return result;
    }

    u32 transformerHash = 0;
    if (!WireIO::read_little_endian(stream, transformerHash))
    {
        result.code  = hard_read_failure(stream) ? WireError::IoError : WireError::TruncatedHeader;
        result.error = result.code == WireError::IoError
                       ? wire_error_message(result.code)
                       : "truncated Atomic V3 feature transformer hash";
        return result;
    }
    if (transformerHash != FeatureTransformerHash)
    {
        result.code  = WireError::WrongTransformerHash;
        result.error = wire_error_message(result.code);
        return result;
    }

    std::unique_ptr<Network> staging(new (std::nothrow) Network());
    if (!staging || !staging->allocate_parameters())
    {
        result.code  = WireError::AllocationFailure;
        result.error = wire_error_message(result.code);
        return result;
    }

    if (!staging->read_feature_parameters(stream, result.code, result.error)
        || !staging->read_dense_parameters(stream, result.code, result.error))
        return result;

    char trailing = 0;
    if (stream.get(trailing))
    {
        result.code  = WireError::TrailingBytes;
        result.error = wire_error_message(result.code);
        return result;
    }
    if (stream.bad() || !stream.eof())
    {
        result.code  = WireError::IoError;
        result.error = wire_error_message(result.code);
        return result;
    }

    if (!staging->validate_numeric(result.code, result.error))
        return result;
    if (!staging->permute_feature_parameters())
    {
        result.code  = WireError::InvalidPermutationShape;
        result.error = wire_error_message(result.code);
        return result;
    }

    staging->set_description(description);
    staging->compute_content_hash();
    result.description = std::move(description);
    result.network     = std::move(staging);
    result.code        = WireError::None;
    return result;
}

LoadResult load_candidate(const std::filesystem::path& path) {
    LoadResult result;
    if (path.empty())
    {
        result.code  = WireError::FileNotFound;
        result.error = wire_error_message(result.code);
        return result;
    }

    std::error_code statusError;
    const bool      exists = std::filesystem::exists(path, statusError);
    if (statusError)
    {
        result.code  = WireError::IoError;
        result.error = wire_error_message(result.code);
        return result;
    }
    if (!exists)
    {
        result.code  = WireError::FileNotFound;
        result.error = wire_error_message(result.code);
        return result;
    }

    const bool regular = std::filesystem::is_regular_file(path, statusError);
    if (statusError || !regular)
    {
        result.code  = WireError::IoError;
        result.error = wire_error_message(result.code);
        return result;
    }

    std::ifstream stream(path, std::ios::binary);
    if (!stream)
    {
        statusError.clear();
        const bool stillExists = std::filesystem::exists(path, statusError);
        result.code  = !statusError && !stillExists ? WireError::FileNotFound : WireError::IoError;
        result.error = wire_error_message(result.code);
        return result;
    }

    result              = load_candidate(stream);
    result.resolvedPath = path;
    return result;
}

}  // namespace Stockfish::Eval::NNUE::AtomicV3
