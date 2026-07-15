/*
  Atomic-Stockfish, a UCI chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <sstream>
#include <streambuf>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#ifdef _WIN32
    #include <fcntl.h>
    #include <io.h>
    #include <sys/stat.h>
#else
    #include <fcntl.h>
    #include <sys/stat.h>
    #include <unistd.h>
#endif

#include "../numeric.h"
#include "../wire_io.h"
#include "../wire_network.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

namespace fs = std::filesystem;

int failures = 0;

inline constexpr std::array<std::size_t, 11> BiasSelectedIndices{
  {0, 1, 7, 8, 15, 16, 31, 32, 511, 512, 1023}};
inline constexpr std::array<std::size_t, 9> HmSelectedIndices{
  {0, 1, 7, 8, 15, 16, 1023, 1024, HmWeightCount - 1}};
inline constexpr std::array<std::size_t, 10> CapturePairSelectedIndices{
  {0, 1, 2, 7, 8, 15, 16, 1023, 1024, CapturePairWeightCount - 1}};
inline constexpr std::array<std::size_t, 9> KingBlastEpSelectedIndices{
  {0, 1, 7, 8, 15, 16, 1023, 1024, KingBlastEpWeightCount - 1}};
inline constexpr std::array<std::size_t, 9> BlastRingSelectedIndices{
  {0, 1, 7, 8, 15, 16, 1023, 1024, BlastRingWeightCount - 1}};
inline constexpr std::array<std::size_t, 32> HmPsqtSelectedIndices{{
  0,   1,   2,   3,   4,   5,   6,   7,   8,   9,   10,  11,  12,  13,  14,  15,
  240, 241, 242, 243, 244, 245, 246, 247, 248, 249, 250, 251, 252, 253, 254, 255,
}};
inline constexpr std::array<std::size_t, 4>  DenseFc0BiasSelectedIndices{{0, 29, 30, 31}};
inline constexpr std::array<std::size_t, 6>  DenseFc0WeightSelectedIndices{
   {Fc0Inputs, Fc0Inputs + 7, Fc0Inputs + 8, Fc0Inputs + 15, 2 * Fc0Inputs + 16,
    2 * Fc0Inputs + 31}};
inline constexpr std::array<std::size_t, 2> DenseFc1BiasSelectedIndices{{0, 31}};
inline constexpr std::array<std::size_t, 6> DenseFc1WeightSelectedIndices{
  {Fc1Inputs, Fc1Inputs + 7, Fc1Inputs + 8, Fc1Inputs + 15, 2 * Fc1Inputs + 16,
   2 * Fc1Inputs + 31}};
inline constexpr std::array<std::size_t, 1> DenseFc2BiasSelectedIndices{{0}};
inline constexpr std::array<std::size_t, 2> DenseFc2WeightSelectedIndices{{0, Fc2Inputs - 1}};

void expect(bool condition, std::string_view label) {
    if (condition)
        std::cout << "PASS " << label << '\n';
    else
    {
        std::cerr << "FAIL " << label << '\n';
        ++failures;
    }
}

void expect_failed(const LoadResult& result, WireError expected, std::string_view label) {
    expect(!result && result.code == expected && !result.network && result.description.empty()
             && !result.error.empty(),
           label);
}

template<typename IntType, std::size_t BlockBytes, std::size_t Count>
void print_permuted_selected(std::string_view                      name,
                             const IntType*                        values,
                             const std::array<std::size_t, Count>& indices) {
    for (const std::size_t canonicalIndex : indices)
    {
        const std::size_t internalIndex =
          WireIO::internal_index_from_canonical<IntType, BlockBytes>(canonicalIndex);
        std::cout << "selected." << name << '[' << canonicalIndex
                  << "]=" << static_cast<long long>(values[internalIndex]) << '\n';
    }
}

template<typename IntType, std::size_t Count>
void print_canonical_selected(std::string_view                      name,
                              const IntType*                        values,
                              const std::array<std::size_t, Count>& indices) {
    for (const std::size_t index : indices)
        std::cout << "selected." << name << '[' << index
                  << "]=" << static_cast<long long>(values[index]) << '\n';
}

void print_selected_parameters(const Network& network) {
    print_permuted_selected<i16, 16>("biases", network.biases(), BiasSelectedIndices);
    print_permuted_selected<i16, 16>("hm", network.hm_weights(), HmSelectedIndices);
    print_permuted_selected<i8, 8>("capture_pair", network.capture_pair_weights(),
                                   CapturePairSelectedIndices);
    print_permuted_selected<i16, 16>("king_blast_ep", network.king_blast_ep_weights(),
                                     KingBlastEpSelectedIndices);
    print_permuted_selected<i8, 8>("blast_ring", network.blast_ring_weights(),
                                   BlastRingSelectedIndices);
    print_canonical_selected("hm_psqt", network.hm_psqt_weights(), HmPsqtSelectedIndices);

    const auto& stacks = network.dense_stacks();
    for (std::size_t bucket = 0; bucket < stacks.size(); ++bucket)
    {
        const auto& stack  = stacks[bucket];
        const auto  prefix = "dense." + std::to_string(bucket) + ".";
        print_canonical_selected(prefix + "fc0_biases", stack.fc0Biases.data(),
                                 DenseFc0BiasSelectedIndices);
        print_canonical_selected(prefix + "fc0_weights", stack.fc0Weights.data(),
                                 DenseFc0WeightSelectedIndices);
        print_canonical_selected(prefix + "fc1_biases", stack.fc1Biases.data(),
                                 DenseFc1BiasSelectedIndices);
        print_canonical_selected(prefix + "fc1_weights", stack.fc1Weights.data(),
                                 DenseFc1WeightSelectedIndices);
        print_canonical_selected(prefix + "fc2_bias", stack.fc2Biases.data(),
                                 DenseFc2BiasSelectedIndices);
        print_canonical_selected(prefix + "fc2_weights", stack.fc2Weights.data(),
                                 DenseFc2WeightSelectedIndices);
    }
}

std::string header_bytes(u32              version,
                         u32              networkHash,
                         u32              descriptionSize,
                         std::string_view description,
                         bool             includeTransformerHash,
                         u32              transformerHash = FeatureTransformerHash) {
    std::ostringstream stream(std::ios::binary | std::ios::out);
    WireIO::write_little_endian(stream, version);
    WireIO::write_little_endian(stream, networkHash);
    WireIO::write_little_endian(stream, descriptionSize);
    WireIO::write_exact(stream, description.data(), description.size());
    if (includeTransformerHash)
        WireIO::write_little_endian(stream, transformerHash);
    return stream.str();
}

LoadResult load_bytes(const std::string& bytes) {
    std::istringstream stream(bytes, std::ios::binary | std::ios::in);
    return load_candidate(stream);
}

void test_contract_hashes_and_shapes() {
    expect(fnv1a_32(HmDescriptor) == 0xA34A8666u && fnv1a_32(CapturePairDescriptor) == 0x9AEDB186u
             && fnv1a_32(KingBlastEpDescriptor) == 0xF5172BC0u
             && fnv1a_32(BlastRingDescriptor) == 0x38377946u,
           "all four exact ASCII slice descriptors retain their frozen FNV hashes");
    expect(fold_slice_hashes(SliceHashes) == 0xA3FBDBE8u,
           "slice hashes fold in the exact HM, CapturePair, KingBlastEP, BlastRing order");
    expect(TransformerDescriptor.size() == 799 && fnv1a_32(TransformerDescriptor) == 0xCC31067Au,
           "global transformer descriptor is exactly 799 ASCII bytes with frozen hash");
    expect(FileVersion == 0xA70C0003u && FeatureTransformerHash == 0x6FCAD592u
             && ArchitectureHash == 0x63337116u && NetworkHash == 0x0CF9A484u,
           "file, feature-transformer, architecture and network header identities are frozen");
    expect(FeatureParameterBytes == 103036928 && DenseStackWireBytes == 35204 && LayerStacks == 8,
           "mixed transformer and eight canonical dense-stack dimensions are exact");
}

template<typename IntType, std::size_t Count>
bool sleb_roundtrip(const std::array<IntType, Count>& input) {
    std::ostringstream output(std::ios::binary | std::ios::out);
    if (!WireIO::write_signed_leb(output, input.data(), input.size()))
        return false;
    std::array<IntType, Count> decoded{};
    std::istringstream         encoded(output.str(), std::ios::binary | std::ios::in);
    return WireIO::read_signed_leb(encoded, decoded.data(), decoded.size()) && decoded == input;
}

void test_sleb_codec() {
    constexpr std::array<i16, 15> I16Values{{
      std::numeric_limits<i16>::min(),
      -16385,
      -129,
      -128,
      -65,
      -64,
      -1,
      0,
      1,
      63,
      64,
      127,
      128,
      16383,
      std::numeric_limits<i16>::max(),
    }};
    constexpr std::array<i32, 15> I32Values{{
      std::numeric_limits<i32>::min(),
      -268435457,
      -129,
      -128,
      -65,
      -64,
      -1,
      0,
      1,
      63,
      64,
      127,
      128,
      268435455,
      std::numeric_limits<i32>::max(),
    }};
    expect(sleb_roundtrip(I16Values), "canonical i16 SLEB round trip covers every byte boundary");
    expect(sleb_roundtrip(I32Values), "canonical i32 SLEB round trip covers every byte boundary");

    std::ostringstream overlong(std::ios::binary | std::ios::out);
    WireIO::write_exact(overlong, WireIO::Leb128Magic, WireIO::Leb128MagicLength);
    WireIO::write_little_endian(overlong, u32(2));
    const std::array<u8, 2> overlongZero{{0x80, 0x00}};
    WireIO::write_exact(overlong, overlongZero.data(), overlongZero.size());
    i16                value = 17;
    std::istringstream overlongInput(overlong.str(), std::ios::binary | std::ios::in);
    expect(!WireIO::read_signed_leb(overlongInput, &value, 1),
           "non-canonical overlong SLEB zero is rejected");

    std::ostringstream unterminated(std::ios::binary | std::ios::out);
    WireIO::write_exact(unterminated, WireIO::Leb128Magic, WireIO::Leb128MagicLength);
    WireIO::write_little_endian(unterminated, u32(3));
    const std::array<u8, 3> continuation{{0x80, 0x80, 0x80}};
    WireIO::write_exact(unterminated, continuation.data(), continuation.size());
    std::istringstream unterminatedInput(unterminated.str(), std::ios::binary | std::ios::in);
    expect(!WireIO::read_signed_leb(unterminatedInput, &value, 1),
           "unterminated maximum-length SLEB value is rejected");

    std::string wrongMagic = overlong.str();
    wrongMagic[0] ^= 0x20;
    std::istringstream wrongMagicInput(wrongMagic, std::ios::binary | std::ios::in);
    expect(!WireIO::read_signed_leb(wrongMagicInput, &value, 1),
           "wrong compressed-field magic is rejected");
}

void test_permutation_wire_mapping() {
    std::array<i16, 64> canonicalI16{};
    for (std::size_t index = 0; index < canonicalI16.size(); ++index)
        canonicalI16[index] = static_cast<i16>(i32(index) * 257 - 8096);
    auto internalI16 = canonicalI16;
    expect(WireIO::permute_parameters<i16, 16>(internalI16.data(), internalI16.size()),
           "one complete i16 parameter chunk accepts the active ISA permutation");

    constexpr std::size_t I16ValuesPerBlock = 16 / sizeof(i16);
    bool                  exactI16Blocks    = true;
    for (std::size_t block = 0; block < WireIO::ParameterBlockOrder.size(); ++block)
        for (std::size_t offset = 0; offset < I16ValuesPerBlock; ++offset)
            exactI16Blocks =
              exactI16Blocks
              && internalI16[block * I16ValuesPerBlock + offset]
                   == canonicalI16[WireIO::ParameterBlockOrder[block] * I16ValuesPerBlock + offset];
    expect(exactI16Blocks, "i16 runtime blocks directly match the frozen active-ISA block order");

    bool canonicalI16Mapping = true;
    for (std::size_t canonicalIndex = 0; canonicalIndex < canonicalI16.size(); ++canonicalIndex)
        canonicalI16Mapping =
          canonicalI16Mapping
          && internalI16[WireIO::internal_index_from_canonical<i16, 16>(canonicalIndex)]
               == canonicalI16[canonicalIndex];
    expect(canonicalI16Mapping,
           "i16 canonical indices map directly into post-permutation runtime storage");

    std::ostringstream  sleb(std::ios::binary | std::ios::out);
    std::array<i16, 64> decodedI16{};
    const bool          wroteSleb =
      WireIO::write_signed_leb_unpermuted<i16, 16>(sleb, internalI16.data(), internalI16.size());
    std::istringstream slebInput(sleb.str(), std::ios::binary | std::ios::in);
    expect(wroteSleb && WireIO::read_signed_leb(slebInput, decodedI16.data(), decodedI16.size())
             && decodedI16 == canonicalI16,
           "i16 serializer recovers canonical order from block-permuted runtime storage");

    std::array<i8, 64> canonicalI8{};
    for (std::size_t index = 0; index < canonicalI8.size(); ++index)
        canonicalI8[index] = static_cast<i8>(u8(index * 37));
    auto internalI8 = canonicalI8;
    expect(WireIO::permute_parameters<i8, 8>(internalI8.data(), internalI8.size()),
           "one complete raw-i8 parameter chunk accepts the active ISA permutation");

    constexpr std::size_t I8ValuesPerBlock = 8 / sizeof(i8);
    bool                  exactI8Blocks    = true;
    for (std::size_t block = 0; block < WireIO::ParameterBlockOrder.size(); ++block)
        for (std::size_t offset = 0; offset < I8ValuesPerBlock; ++offset)
            exactI8Blocks =
              exactI8Blocks
              && internalI8[block * I8ValuesPerBlock + offset]
                   == canonicalI8[WireIO::ParameterBlockOrder[block] * I8ValuesPerBlock + offset];
    expect(exactI8Blocks, "raw-i8 runtime blocks directly match the frozen active-ISA block order");

    bool canonicalI8Mapping = true;
    for (std::size_t canonicalIndex = 0; canonicalIndex < canonicalI8.size(); ++canonicalIndex)
        canonicalI8Mapping =
          canonicalI8Mapping
          && internalI8[WireIO::internal_index_from_canonical<i8, 8>(canonicalIndex)]
               == canonicalI8[canonicalIndex];
    expect(canonicalI8Mapping,
           "raw-i8 canonical indices map directly into post-permutation runtime storage");

    std::ostringstream raw(std::ios::binary | std::ios::out);
    const bool         wroteRaw =
      WireIO::write_raw_unpermuted<i8, 8>(raw, internalI8.data(), internalI8.size());
    const std::string rawBytes = raw.str();
    expect(wroteRaw && rawBytes.size() == canonicalI8.size()
             && std::equal(rawBytes.begin(), rawBytes.end(),
                           reinterpret_cast<const char*>(canonicalI8.data())),
           "raw i8 serializer preserves every signed two's-complement byte canonically");

    expect(!WireIO::permute_parameters<i16, 16>(internalI16.data(), internalI16.size() - 1),
           "mis-sized i16 parameter region fails closed");
    expect(!WireIO::write_raw_unpermuted<i8, 8>(raw, internalI8.data(), internalI8.size() - 1),
           "mis-sized raw-i8 parameter region fails closed");
}

void test_numeric_loader_gates() {
    std::vector<i32> psqt(HmPsqtWeightCount, 0);
    psqt[0] = std::numeric_limits<i32>::max();
    HmPsqtBounds bounds{};
    expect(validate_hm_psqt_weights(psqt.data(), psqt.size(), bounds) == NumericError::None
             && bounds.maximumAbsoluteSums[0] == std::numeric_limits<i32>::max(),
           "PSQT loader gate accepts the exact top-32 INT32_MAX boundary");
    psqt[HmPsqtBuckets] = 1;
    expect(validate_hm_psqt_weights(psqt.data(), psqt.size(), bounds)
             == NumericError::PsqtAccumulatorOverflow,
           "PSQT loader gate rejects the exact boundary plus one");
    psqt[0]             = std::numeric_limits<i32>::min();
    psqt[HmPsqtBuckets] = 0;
    expect(validate_hm_psqt_weights(psqt.data(), psqt.size(), bounds)
             == NumericError::PsqtWeightMagnitudeOverflow,
           "PSQT loader gate rejects INT32_MIN before magnitude arithmetic");

    constexpr std::array<i8, 1> One{{1}};
    AffineOutputBounds          affine{};
    const i32 exactBias = std::numeric_limits<i32>::max() - i32(DenseInputMaximum);
    expect(affine_output_bounds(exactBias, One.data(), One.size(), affine) == NumericError::None
             && affine.canonicalMagnitude == std::numeric_limits<i32>::max(),
           "affine loader gate accepts exact canonical INT32_MAX magnitude");
    expect(affine_output_bounds(exactBias + 1, One.data(), One.size(), affine)
             == NumericError::AffineAccumulatorOverflow,
           "affine loader gate rejects canonical magnitude boundary plus one");

    constexpr i64  Tail = 1517555112LL;
    SignedInterval raw{};
    expect(
      validate_forward_interval({std::numeric_limits<i32>::min(), std::numeric_limits<i32>::max()},
                                {0, Tail}, {0, Tail}, raw)
          == NumericError::None
        && raw.lower == RawOutputMinimum && raw.upper == RawOutputMaximum,
      "global skip gate accepts both exact scalable raw boundaries");
    expect(
      validate_forward_interval({0, std::numeric_limits<i32>::max()}, {0, Tail + 1}, {0, 0}, raw)
        == NumericError::RawOutputOverflow,
      "global skip gate rejects the positive raw boundary plus one");
    expect(
      validate_forward_interval({std::numeric_limits<i32>::min(), 0}, {0, 0}, {0, Tail + 1}, raw)
        == NumericError::RawOutputOverflow,
      "global skip gate rejects the negative raw boundary minus one");
}

void test_header_corruption_and_fail_closed() {
    expect_failed(load_bytes(""), WireError::TruncatedHeader,
                  "empty input fails closed as a truncated header");

    std::istringstream badStream(std::string{}, std::ios::binary | std::ios::in);
    badStream.setstate(std::ios::badbit);
    expect_failed(load_candidate(badStream), WireError::IoError,
                  "hard stream failure is distinguished from ordinary truncation");

    std::istringstream failedStream(std::string{}, std::ios::binary | std::ios::in);
    failedStream.setstate(std::ios::failbit);
    expect_failed(load_candidate(failedStream), WireError::IoError,
                  "failbit without EOF is classified as an I/O error");

    expect_failed(load_bytes(header_bytes(FileVersion ^ 1U, NetworkHash, 0, {}, false)),
                  WireError::WrongFileVersion, "wrong file version fails before allocation");
    expect_failed(load_bytes(header_bytes(FileVersion, NetworkHash ^ 1U, 0, {}, false)),
                  WireError::WrongNetworkHash, "wrong network hash fails before allocation");
    expect_failed(
      load_bytes(header_bytes(FileVersion, NetworkHash, MaximumDescriptionBytes + 1, {}, false)),
      WireError::DescriptionTooLarge, "description above one MiB fails before allocation");
    expect_failed(load_bytes(header_bytes(FileVersion, NetworkHash, 4, "abc", false)),
                  WireError::TruncatedDescription, "truncated description fails closed");
    expect_failed(load_bytes(header_bytes(FileVersion, NetworkHash, 0, {}, false)),
                  WireError::TruncatedHeader,
                  "physically truncated feature-transformer hash is a truncated header");
    expect_failed(
      load_bytes(header_bytes(FileVersion, NetworkHash, 0, {}, true, FeatureTransformerHash ^ 1U)),
      WireError::WrongTransformerHash,
      "wrong feature-transformer hash fails before parameter allocation");
}

bool files_equal(const fs::path& leftPath, const fs::path& rightPath) {
    std::error_code      ec;
    const std::uintmax_t leftSize = fs::file_size(leftPath, ec);
    if (ec)
        return false;
    const std::uintmax_t rightSize = fs::file_size(rightPath, ec);
    if (ec || leftSize != rightSize)
        return false;

    std::ifstream left(leftPath, std::ios::binary);
    std::ifstream right(rightPath, std::ios::binary);
    if (!left || !right)
        return false;

    std::array<char, 65536> leftBytes{};
    std::array<char, 65536> rightBytes{};
    while (left)
    {
        left.read(leftBytes.data(), static_cast<std::streamsize>(leftBytes.size()));
        right.read(rightBytes.data(), static_cast<std::streamsize>(rightBytes.size()));
        if (left.gcount() != right.gcount()
            || !std::equal(leftBytes.begin(), leftBytes.begin() + left.gcount(),
                           rightBytes.begin()))
            return false;
    }
    return left.eof() && right.eof();
}

bool write_network_file(const Network& network, const fs::path& path) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output)
        return false;
    const SaveResult saved = network.save(output);
    output.close();
    return saved && output;
}

enum class ExclusiveCreateResult {
    Created,
    Collision,
    Error
};

class ExclusiveFile final {
   public:
    ExclusiveFile() = default;

    ExclusiveFile(const ExclusiveFile&)            = delete;
    ExclusiveFile& operator=(const ExclusiveFile&) = delete;

    ~ExclusiveFile() { close(); }

    ExclusiveCreateResult create(const fs::path& path) noexcept {
        if (descriptor_ != -1)
            return ExclusiveCreateResult::Error;

        errno = 0;
#ifdef _WIN32
        descriptor_ =
          ::_wopen(path.c_str(), _O_BINARY | _O_WRONLY | _O_CREAT | _O_EXCL | _O_NOINHERIT,
                   _S_IREAD | _S_IWRITE);
#else
        int flags = O_WRONLY | O_CREAT | O_EXCL;
    #ifdef O_CLOEXEC
        flags |= O_CLOEXEC;
    #endif
        descriptor_ = ::open(path.c_str(), flags, 0666);
#endif
        if (descriptor_ != -1)
            return ExclusiveCreateResult::Created;
        return errno == EEXIST ? ExclusiveCreateResult::Collision : ExclusiveCreateResult::Error;
    }

    bool write(const char* bytes, std::size_t size) noexcept {
        std::size_t written = 0;
        while (written < size)
        {
            errno = 0;
#ifdef _WIN32
            const unsigned request = static_cast<unsigned>(
              std::min<std::size_t>(size - written, std::numeric_limits<unsigned>::max()));
            const int count = ::_write(descriptor_, bytes + written, request);
#else
            const ssize_t count = ::write(descriptor_, bytes + written, size - written);
#endif
            if (count > 0)
            {
                written += static_cast<std::size_t>(count);
                continue;
            }
            if (count < 0 && errno == EINTR)
                continue;
            return false;
        }
        return true;
    }

    bool synchronize() noexcept {
        if (descriptor_ == -1)
            return false;

        errno = 0;
#ifdef _WIN32
        return ::_commit(descriptor_) == 0;
#else
        int result = 0;
        do
        {
            result = ::fsync(descriptor_);
        } while (result != 0 && errno == EINTR);
        return result == 0;
#endif
    }

    bool close() noexcept {
        if (descriptor_ == -1)
            return true;

        const int descriptor = descriptor_;
        descriptor_          = -1;
#ifdef _WIN32
        return ::_close(descriptor) == 0;
#else
        return ::close(descriptor) == 0;
#endif
    }

   private:
    int descriptor_ = -1;
};

class ExclusiveFileBuffer final: public std::streambuf {
   public:
    explicit ExclusiveFileBuffer(ExclusiveFile& file) noexcept :
        file_(file) {
        setp(buffer_.data(), buffer_.data() + buffer_.size());
    }

   protected:
    int sync() override { return flush_buffer() ? 0 : -1; }

    int_type overflow(int_type character) override {
        if (!flush_buffer())
            return traits_type::eof();
        if (!traits_type::eq_int_type(character, traits_type::eof()))
        {
            *pptr() = traits_type::to_char_type(character);
            pbump(1);
        }
        return traits_type::not_eof(character);
    }

    std::streamsize xsputn(const char* bytes, std::streamsize count) override {
        if (count <= 0)
            return 0;

        std::streamsize accepted = 0;
        while (accepted < count)
        {
            if (pptr() == epptr() && !flush_buffer())
                break;
            const auto available = static_cast<std::streamsize>(epptr() - pptr());
            const auto chunk     = std::min(available, count - accepted);
            std::memcpy(pptr(), bytes + accepted, static_cast<std::size_t>(chunk));
            pbump(static_cast<int>(chunk));
            accepted += chunk;
        }
        return accepted;
    }

   private:
    bool flush_buffer() noexcept {
        const auto pending = static_cast<std::size_t>(pptr() - pbase());
        if (pending != 0 && !file_.write(pbase(), pending))
            return false;
        setp(buffer_.data(), buffer_.data() + buffer_.size());
        return true;
    }

    ExclusiveFile&          file_;
    std::array<char, 65536> buffer_{};
};

bool patch_u32(const fs::path& path, std::uintmax_t offset, u32 value) {
    std::fstream file(path, std::ios::binary | std::ios::in | std::ios::out);
    if (!file)
        return false;
    file.seekp(static_cast<std::streamoff>(offset));
    return WireIO::write_little_endian(file, value);
}

bool patch_byte(const fs::path& path, std::uintmax_t offset, u8 value, u8* original = nullptr) {
    std::fstream file(path, std::ios::binary | std::ios::in | std::ios::out);
    if (!file)
        return false;
    file.seekg(static_cast<std::streamoff>(offset));
    char previous = 0;
    if (!file.get(previous))
        return false;
    if (original)
        *original = static_cast<u8>(previous);
    file.seekp(static_cast<std::streamoff>(offset));
    file.put(static_cast<char>(value));
    return bool(file);
}

struct FixtureWireOffsets {
    std::uintmax_t psqtPayload;
    std::uintmax_t firstArchitectureHash;
};

std::optional<FixtureWireOffsets> fixture_wire_offsets(const fs::path& path,
                                                       std::size_t     descriptionSize) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream)
        return std::nullopt;
    stream.seekg(static_cast<std::streamoff>(12 + descriptionSize + sizeof(u32)));

    auto skipSleb = [&](std::uintmax_t* payloadOffset) {
        std::array<char, WireIO::Leb128MagicLength> magic{};
        u32                                         byteCount = 0;
        if (!WireIO::read_exact(stream, magic.data(), magic.size())
            || std::memcmp(magic.data(), WireIO::Leb128Magic, magic.size()) != 0
            || !WireIO::read_little_endian(stream, byteCount))
            return false;
        if (payloadOffset)
        {
            const std::streampos payload = stream.tellg();
            if (payload == std::streampos(-1))
                return false;
            *payloadOffset = static_cast<std::uintmax_t>(static_cast<std::streamoff>(payload));
        }
        stream.seekg(static_cast<std::streamoff>(byteCount), std::ios::cur);
        return bool(stream);
    };

    if (!skipSleb(nullptr) || !skipSleb(nullptr))
        return std::nullopt;
    stream.seekg(static_cast<std::streamoff>(CapturePairWeightCount), std::ios::cur);
    if (!stream || !skipSleb(nullptr))
        return std::nullopt;
    stream.seekg(static_cast<std::streamoff>(BlastRingWeightCount), std::ios::cur);
    std::uintmax_t psqtPayload = 0;
    if (!stream || !skipSleb(&psqtPayload))
        return std::nullopt;

    const std::streampos offset = stream.tellg();
    if (offset == std::streampos(-1))
        return std::nullopt;
    return FixtureWireOffsets{psqtPayload,
                              static_cast<std::uintmax_t>(static_cast<std::streamoff>(offset))};
}

struct TemporaryFiles {
    fs::path first;
    fs::path second;

    ~TemporaryFiles() {
        std::error_code ec;
        fs::remove(first, ec);
        ec.clear();
        fs::remove(second, ec);
    }
};

void test_path_classification() {
    const auto     stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path base =
      fs::temp_directory_path() / ("atomic-v3-wire-path-" + std::to_string(stamp));
    const fs::path  missing = base.string() + "-missing.nnue";
    std::error_code ec;
    fs::remove(missing, ec);
    expect_failed(load_candidate(missing), WireError::FileNotFound,
                  "a nonexistent network path is classified as file-not-found");

    ec.clear();
    const bool created = fs::create_directory(base, ec);
    expect(created && !ec, "portable non-regular network-path fixture is created");
    if (created && !ec)
        expect_failed(load_candidate(base), WireError::IoError,
                      "an existing directory network path is classified as an I/O error");
    ec.clear();
    fs::remove(base, ec);
}

void test_fixture_roundtrip_and_corruption(const fs::path& fixture) {
    LoadResult loaded = load_candidate(fixture);
    expect(loaded && loaded.network->simd_permuted()
             && loaded.network->description() == loaded.description,
           "fixture loads transactionally and only then exposes SIMD-permuted parameters");
    if (!loaded)
    {
        std::cerr << "fixture load error: " << loaded.error << '\n';
        return;
    }

    const auto aligned = [](const void* pointer) {
        return reinterpret_cast<std::uintptr_t>(pointer) % ParameterAlignment == 0;
    };
    expect(aligned(loaded.network->biases()) && aligned(loaded.network->hm_weights())
             && aligned(loaded.network->capture_pair_weights())
             && aligned(loaded.network->king_blast_ep_weights())
             && aligned(loaded.network->blast_ring_weights())
             && aligned(loaded.network->hm_psqt_weights()),
           "every mixed feature parameter tensor has stable 64-byte runtime alignment");

    std::ostringstream oversizedOutput(std::ios::binary | std::ios::out);
    const std::string  oversizedDescription(MaximumDescriptionBytes + 1, 'x');
    const SaveResult   oversized = loaded.network->save(oversizedOutput, oversizedDescription);
    expect(!oversized && oversized.code == WireError::OutputDescriptionTooLarge
             && oversizedOutput.str().empty(),
           "oversized save description fails before emitting a partial header");

    const auto     stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path tempRoot =
      fs::temp_directory_path() / ("atomic-v3-wire-core-" + std::to_string(stamp));
    TemporaryFiles temporary{tempRoot.string() + "-a.nnue", tempRoot.string() + "-b.nnue"};

    expect(write_network_file(*loaded.network, temporary.first)
             && files_equal(fixture, temporary.first),
           "fixture load-save round trip is byte exact");
    expect(write_network_file(*loaded.network, temporary.second)
             && files_equal(fixture, temporary.second)
             && files_equal(temporary.first, temporary.second),
           "a second save is byte exact and does not mutate the live network");

    if (!fs::exists(temporary.first))
        return;

    const std::uintmax_t originalSize = fs::file_size(temporary.first);
    {
        std::ofstream append(temporary.first, std::ios::binary | std::ios::app);
        append.put('\0');
    }
    expect_failed(load_candidate(temporary.first), WireError::TrailingBytes,
                  "one trailing byte is rejected after all eight dense stacks");
    fs::resize_file(temporary.first, originalSize);

    expect(patch_u32(temporary.first, 0, FileVersion ^ 1U),
           "fixture version corruption patch is applied");
    expect_failed(load_candidate(temporary.first), WireError::WrongFileVersion,
                  "fixture wrong version remains fail closed");
    patch_u32(temporary.first, 0, FileVersion);

    expect(patch_u32(temporary.first, 4, NetworkHash ^ 1U),
           "fixture network-hash corruption patch is applied");
    expect_failed(load_candidate(temporary.first), WireError::WrongNetworkHash,
                  "fixture wrong network hash remains fail closed");
    patch_u32(temporary.first, 4, NetworkHash);

    const std::uintmax_t transformerHashOffset = 12 + loaded.description.size();
    expect(patch_u32(temporary.first, transformerHashOffset, FeatureTransformerHash ^ 1U),
           "fixture transformer-hash corruption patch is applied");
    expect_failed(load_candidate(temporary.first), WireError::WrongTransformerHash,
                  "fixture wrong transformer hash remains fail closed");
    patch_u32(temporary.first, transformerHashOffset, FeatureTransformerHash);

    const auto wireOffsets = fixture_wire_offsets(temporary.first, loaded.description.size());
    expect(wireOffsets.has_value(),
           "fixture PSQT and dense offsets are derived from canonical framing");
    if (wireOffsets)
    {
        u8 originalPsqtByte = 0;
        expect(patch_byte(temporary.first, wireOffsets->psqtPayload, 0x81, &originalPsqtByte)
                 && originalPsqtByte == 0x80,
               "fixture exact-boundary PSQT sentinel is incremented canonically by one");
        expect_failed(load_candidate(temporary.first), WireError::PsqtRangeExceeded,
                      "PSQT top-32 INT32_MAX plus one is rejected before permutation");
        patch_byte(temporary.first, wireOffsets->psqtPayload, originalPsqtByte);

        const std::uintmax_t architectureOffset = wireOffsets->firstArchitectureHash;
        expect(patch_u32(temporary.first, architectureOffset, ArchitectureHash ^ 1U),
               "fixture bucket-zero architecture corruption patch is applied");
        expect_failed(load_candidate(temporary.first), WireError::WrongArchitectureHash,
                      "wrong bucket-zero architecture hash fails closed");
        patch_u32(temporary.first, architectureOffset, ArchitectureHash);

        const std::uintmax_t bucketSevenOffset =
          architectureOffset + 7 * (sizeof(u32) + DenseStackWireBytes);
        expect(patch_u32(temporary.first, bucketSevenOffset, ArchitectureHash ^ 1U),
               "fixture bucket-seven architecture corruption patch is applied");
        expect_failed(load_candidate(temporary.first), WireError::WrongArchitectureHash,
                      "wrong bucket-seven architecture hash fails closed");
        patch_u32(temporary.first, bucketSevenOffset, ArchitectureHash);

        const std::uintmax_t firstFc0Weight =
          architectureOffset + sizeof(u32) + Fc0Outputs * sizeof(i32);
        u8 originalFc0Weight = 0;
        expect(patch_byte(temporary.first, firstFc0Weight, 1, &originalFc0Weight)
                 && originalFc0Weight == 0,
               "fixture exact-boundary fc0 row receives one positive weight");
        expect_failed(load_candidate(temporary.first), WireError::AffineRangeExceeded,
                      "affine INT32_MAX magnitude plus 127 is rejected before permutation");
        patch_byte(temporary.first, firstFc0Weight, originalFc0Weight);

        constexpr u32        SkipBiasOutsideRawEnvelope = 1600000000U;
        const std::uintmax_t fc0Output30Bias = architectureOffset + sizeof(u32) + 30 * sizeof(i32);
        expect(patch_u32(temporary.first, fc0Output30Bias, SkipBiasOutsideRawEnvelope),
               "fixture fc0[30] skip bias is raised within its individual i32 envelope");
        expect_failed(load_candidate(temporary.first), WireError::RawOutputRangeExceeded,
                      "globally unsafe fc2 plus fc0[30] composition is rejected");
        patch_u32(temporary.first, fc0Output30Bias, 0);
    }

    char finalByte = 0;
    {
        std::ifstream input(temporary.first, std::ios::binary);
        input.seekg(-1, std::ios::end);
        input.get(finalByte);
    }
    fs::resize_file(temporary.first, originalSize - 1);
    expect_failed(load_candidate(temporary.first), WireError::TruncatedArchitecture,
                  "truncating the final dense byte fails closed");
    {
        std::ofstream append(temporary.first, std::ios::binary | std::ios::app);
        append.put(finalByte);
    }

    LoadResult restored = load_candidate(temporary.first);
    expect(restored && files_equal(fixture, temporary.first),
           "restoring every corruption yields the original valid fixture");
}

void print_hash_contract() {
    const auto flags    = std::cout.flags();
    const char fill     = std::cout.fill();
    auto       printHex = [](std::string_view key, u32 value) {
        std::cout << key << "=0x" << std::uppercase << std::hex << std::setw(8) << std::setfill('0')
                  << value << std::dec << '\n';
    };
    printHex("file_version", FileVersion);
    printHex("hm_hash", HmHash);
    printHex("capture_pair_hash", CapturePairHash);
    printHex("king_blast_ep_hash", KingBlastEpHash);
    printHex("blast_ring_hash", BlastRingHash);
    printHex("feature_hash", FeatureHash);
    std::cout << "descriptor_bytes=" << TransformerDescriptor.size() << '\n';
    printHex("descriptor_hash", TransformerDescriptorHash);
    printHex("feature_transformer_hash", FeatureTransformerHash);
    printHex("architecture_hash", ArchitectureHash);
    printHex("network_hash", NetworkHash);
    std::cout.flags(flags);
    std::cout.fill(fill);
}

int inspect(const fs::path& path) {
    LoadResult loaded = load_candidate(path);
    if (!loaded)
    {
        std::cerr << "wire_error=" << static_cast<unsigned>(loaded.code) << '\n'
                  << "error=" << loaded.error << '\n';
        return EXIT_FAILURE;
    }
    std::cout << "description_size=" << loaded.description.size() << '\n'
              << "simd_permuted=" << (loaded.network->simd_permuted() ? "true" : "false") << '\n'
              << "layer_stacks=" << LayerStacks << '\n';
    print_selected_parameters(*loaded.network);
    return EXIT_SUCCESS;
}

int roundtrip(const fs::path& inputPath, const fs::path& outputPath) {
    LoadResult loaded = load_candidate(inputPath);
    if (!loaded)
    {
        std::cerr << "error=" << loaded.error << '\n';
        return EXIT_FAILURE;
    }

    ExclusiveFile outputFile;
    const auto    created = outputFile.create(outputPath);
    if (created == ExclusiveCreateResult::Collision)
    {
        std::cerr << "error=roundtrip output already exists\n";
        return EXIT_FAILURE;
    }
    if (created != ExclusiveCreateResult::Created)
    {
        std::cerr << "error=roundtrip output creation failed\n";
        return EXIT_FAILURE;
    }

    bool serialized = false;
    {
        ExclusiveFileBuffer buffer(outputFile);
        std::ostream        output(&buffer);
        const SaveResult    saved = loaded.network->save(output);
        output.flush();
        serialized = bool(saved) && bool(output);
    }
    const bool synchronized = serialized && outputFile.synchronize();
    const bool closed       = outputFile.close();
    if (!serialized || !synchronized || !closed || !files_equal(inputPath, outputPath))
    {
        // Never unlink by pathname here: another process may have replaced the
        // entry after our exclusive create.  Leaving an owned partial output is
        // safer than deleting a potentially foreign destination.
        std::cerr << "error=roundtrip output differs from canonical input\n";
        return EXIT_FAILURE;
    }
    std::cout << "roundtrip=ok\n";
    return EXIT_SUCCESS;
}

int selftest(const fs::path& fixture) {
    test_contract_hashes_and_shapes();
    test_sleb_codec();
    test_permutation_wire_mapping();
    test_numeric_loader_gates();
    test_header_corruption_and_fail_closed();
    test_path_classification();
    test_fixture_roundtrip_and_corruption(fixture);

    if (failures != 0)
    {
        std::cerr << failures << " AtomicNNUEV3 wire self-test(s) failed\n";
        return EXIT_FAILURE;
    }
    std::cout << "All AtomicNNUEV3 wire self-tests passed\n";
    return EXIT_SUCCESS;
}

void usage(const char* executable) {
    std::cerr
      << "usage: " << executable
      << " hash-contract | inspect <network> | roundtrip <input> <output> | selftest <fixture>\n";
}

}  // namespace
}  // namespace Stockfish::Eval::NNUE::AtomicV3

int main(int argc, char** argv) {
    using namespace Stockfish::Eval::NNUE::AtomicV3;

    if (argc == 2 && std::string_view(argv[1]) == "hash-contract")
    {
        print_hash_contract();
        return EXIT_SUCCESS;
    }
    if (argc == 3 && std::string_view(argv[1]) == "inspect")
        return inspect(argv[2]);
    if (argc == 4 && std::string_view(argv[1]) == "roundtrip")
        return roundtrip(argv[2], argv[3]);
    if (argc == 3 && std::string_view(argv[1]) == "selftest")
        return selftest(argv[2]);

    usage(argv[0]);
    return EXIT_FAILURE;
}
