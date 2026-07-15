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
#include <cstddef>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>
#include <string_view>
#include <type_traits>

#include "../incremental_simd_kernels.h"

namespace Stockfish::Eval::NNUE::AtomicV3 {
namespace {

constexpr u64 FnvOffset = 1469598103934665603ULL;
constexpr u64 FnvPrime  = 1099511628211ULL;

inline constexpr std::array<std::size_t, 13> TailCounts{
  {0, 1, 3, 4, 7, 8, 9, 15, 16, 17, 1023, 1024, 1025}};
inline constexpr std::size_t MaximumCount = TailCounts.back();

constexpr i64 DestinationLeftCanary  = 0x13579BDF2468ACELL;
constexpr i64 DestinationRightCanary = -0x02468ACE13579BDLL;
constexpr i16 SourceLeftCanary       = 0x5A5A;
constexpr i16 SourceRightCanary      = -0x5A5A;

using Destination = std::array<i64, MaximumCount + 2>;
using Source      = std::array<i16, MaximumCount + 2>;

[[noreturn]] void die(const std::string& detail) {
    std::cerr << "AtomicNNUEV3 incremental SIMD kernel gate FAILED\n" << detail << '\n';
    std::exit(EXIT_FAILURE);
}

bool parse_isa(std::string_view text, SimdIsa& isa) {
    if (text == "scalar")
        isa = SimdIsa::Scalar;
    else if (text == "sse41")
        isa = SimdIsa::Sse41;
    else if (text == "avx2")
        isa = SimdIsa::Avx2;
    else
        return false;
    return true;
}

template<typename Integer>
void fingerprint_integer(u64& hash, Integer value) {
    using Unsigned      = std::make_unsigned_t<Integer>;
    const Unsigned bits = static_cast<Unsigned>(value);
    for (std::size_t byte = 0; byte < sizeof(Integer); ++byte)
    {
        hash ^= static_cast<u8>(bits >> (byte * 8));
        hash *= FnvPrime;
    }
}

i16 source_value(std::size_t index) noexcept {
    if (index == 0)
        return std::numeric_limits<i16>::min();
    if (index == 1)
        return std::numeric_limits<i16>::max();
    const unsigned bits = static_cast<unsigned>((index * 40503U + 97U) & 0xFFFFU);
    return static_cast<i16>(static_cast<int>(bits) - 32768);
}

i64 base_value(std::size_t index) noexcept {
    const i64 magnitude =
      0x100000000LL + static_cast<i64>(index) * 1000003LL + static_cast<i64>(index % 17) * 65537LL;
    return (index & 1U) == 0 ? magnitude : -magnitude;
}

void prepare(std::size_t count, Destination& destination, Source& source) {
    destination.fill(0x112233445566778LL);
    source.fill(1234);
    destination[0]         = DestinationLeftCanary;
    destination[count + 1] = DestinationRightCanary;
    source[0]              = SourceLeftCanary;
    source[count + 1]      = SourceRightCanary;
    for (std::size_t index = 0; index < count; ++index)
    {
        destination[index + 1] = base_value(index);
        source[index + 1]      = source_value(index);
    }
}

using Kernel = bool (*)(SimdIsa, HmDeltaOperation, i64*, const i16*, std::size_t) noexcept;

bool dispatcher(SimdIsa          isa,
                HmDeltaOperation operation,
                i64*             destination,
                const i16*       source,
                std::size_t      count) noexcept {
    const HmDeltaKernelResult result =
      apply_hm_delta_kernel(isa, operation, destination, source, count);
    return result && result.executedIsa == isa;
}

// Exercise the stable noinline symbols directly. Scalar has no separate ISA
// symbol, so its exact public dispatcher is the stable scalar entry point.
bool stable_path(SimdIsa          isa,
                 HmDeltaOperation operation,
                 i64*             destination,
                 const i16*       source,
                 std::size_t      count) noexcept {
    switch (isa)
    {
    case SimdIsa::Scalar : {
        const HmDeltaKernelResult result =
          apply_hm_delta_kernel(isa, operation, destination, source, count);
        return result && result.executedIsa == isa;
    }
    case SimdIsa::Sse41 :
#if defined(USE_SSE41) || defined(USE_AVX2) || defined(USE_AVX512)
        return destination && source
            && (operation == HmDeltaOperation::Add
                  ? atomic_v3_add_i16_i64_sse41_kernel(destination, source, count)
                  : atomic_v3_sub_i16_i64_sse41_kernel(destination, source, count))
                 == isa;
#else
        return false;
#endif
    case SimdIsa::Avx2 :
#if defined(USE_AVX2) || defined(USE_AVX512)
        return destination && source
            && (operation == HmDeltaOperation::Add
                  ? atomic_v3_add_i16_i64_avx2_kernel(destination, source, count)
                  : atomic_v3_sub_i16_i64_avx2_kernel(destination, source, count))
                 == isa;
#else
        return false;
#endif
    }
    return false;
}

bool exact_single(Kernel kernel, SimdIsa isa, HmDeltaOperation operation, std::size_t count) {
    Destination destination{};
    Source      source{};
    prepare(count, destination, source);
    Destination  expected = destination;
    const Source original = source;
    for (std::size_t index = 0; index < count; ++index)
    {
        const i64 delta = static_cast<i64>(source[index + 1]);
        expected[index + 1] += operation == HmDeltaOperation::Add ? delta : -delta;
    }
    const bool accepted = kernel(isa, operation, destination.data() + 1, source.data() + 1, count);
    return accepted && destination == expected && source == original;
}

bool exact_restore(Kernel kernel, SimdIsa isa, std::size_t count) {
    Destination destination{};
    Source      source{};
    prepare(count, destination, source);
    const Destination originalDestination = destination;
    const Source      originalSource      = source;
    const bool        added =
      kernel(isa, HmDeltaOperation::Add, destination.data() + 1, source.data() + 1, count);
    const bool removed =
      kernel(isa, HmDeltaOperation::Remove, destination.data() + 1, source.data() + 1, count);
    return added && removed && destination == originalDestination && source == originalSource;
}

void run_wraparound_probes(SimdIsa isa) {
    for (const Kernel kernel : {dispatcher, stable_path})
    {
        std::array<i64, 8> destination{};
        std::array<i16, 8> source{};
        source.fill(1);

        destination.fill(std::numeric_limits<i64>::max());
        if (!kernel(isa, HmDeltaOperation::Add, destination.data(), source.data(), source.size())
            || !std::all_of(destination.begin(), destination.end(),
                            [](i64 value) { return value == std::numeric_limits<i64>::min(); }))
            die("i64 add wraparound was not exact or executed by the requested ISA");

        destination.fill(std::numeric_limits<i64>::min());
        if (!kernel(isa, HmDeltaOperation::Remove, destination.data(), source.data(), source.size())
            || !std::all_of(destination.begin(), destination.end(),
                            [](i64 value) { return value == std::numeric_limits<i64>::max(); }))
            die("i64 remove wraparound was not exact or executed by the requested ISA");
    }
}

void append_case_fingerprint(u64& hash, std::size_t count) {
    fingerprint_integer(hash, static_cast<u64>(count));
    for (std::size_t index = 0; index < count; ++index)
    {
        const i16 source = source_value(index);
        const i64 base   = base_value(index);
        fingerprint_integer(hash, source);
        fingerprint_integer(hash, base);
        fingerprint_integer(hash, base + static_cast<i64>(source));
        fingerprint_integer(hash, base - static_cast<i64>(source));
    }
}

void print_tail_counts() {
    std::cout << "tail_counts=";
    for (std::size_t index = 0; index < TailCounts.size(); ++index)
    {
        if (index)
            std::cout << ',';
        std::cout << TailCounts[index];
    }
    std::cout << '\n';
}

std::size_t run_null_pointer_probes(SimdIsa isa) {
    Destination destination{};
    Source      source{};
    prepare(1, destination, source);
    const Destination original = destination;

    if (apply_hm_delta_kernel(isa, HmDeltaOperation::Add, nullptr, source.data() + 1, 1))
        die("null destination was accepted");
    if (apply_hm_delta_kernel(isa, HmDeltaOperation::Remove, destination.data() + 1, nullptr, 1)
        || destination != original)
        die("null source was accepted or mutated the destination");
    if (apply_hm_delta_kernel(isa, HmDeltaOperation::Add, nullptr, nullptr, 0))
        die("two null pointers were accepted for a zero-length request");
    return 3;
}

void unavailable_probe(SimdIsa isa, HmDeltaOperation operation) {
    Destination destination{};
    Source      source{};
    prepare(17, destination, source);
    const Destination         originalDestination = destination;
    const Source              originalSource      = source;
    const HmDeltaKernelResult accepted =
      apply_hm_delta_kernel(isa, operation, destination.data() + 1, source.data() + 1, 17);
    if (accepted || destination != originalDestination || source != originalSource)
        die("an unavailable ISA was accepted, mutated state, or fell back");
}

std::size_t run_unavailable_probes() {
    std::size_t probes = 0;
    for (const SimdIsa isa : {SimdIsa::Sse41, SimdIsa::Avx2})
        if (!simd_isa_available(isa))
            for (const HmDeltaOperation operation :
                 {HmDeltaOperation::Add, HmDeltaOperation::Remove})
            {
                unavailable_probe(isa, operation);
                ++probes;
            }

    for (const HmDeltaOperation operation : {HmDeltaOperation::Add, HmDeltaOperation::Remove})
    {
        unavailable_probe(static_cast<SimdIsa>(255), operation);
        ++probes;
    }
    return probes;
}

SimdIsa parse_options(int argc, char* argv[]) {
    SimdIsa required = SimdIsa::Scalar;
    bool    haveIsa  = false;
    for (int index = 1; index < argc; ++index)
    {
        const std::string_view argument = argv[index];
        if (argument == "--require-isa" && index + 1 < argc && !haveIsa)
        {
            if (!parse_isa(argv[++index], required))
                die("--require-isa expects scalar, sse41 or avx2");
            haveIsa = true;
        }
        else
            die("unknown, duplicate or incomplete argument: " + std::string(argument));
    }
    if (!haveIsa)
        die("usage: atomic-v3-incremental-simd-kernel-tests --require-isa "
            "scalar|sse41|avx2");
    return required;
}

int run(SimdIsa requiredIsa) {
    if (!simd_isa_available(requiredIsa))
        die("required ISA is not compiled into this binary");

    std::cout << "record=incremental_simd_kernel_identity\n"
              << "isa.requested=" << simd_isa_name(requiredIsa) << '\n'
              << "isa.maximum=" << simd_isa_name(maximum_simd_isa()) << '\n';
    print_tail_counts();
    std::cout << "end_identity=1\n";

    u64         fingerprint      = FnvOffset;
    std::size_t nonzeroBaseLanes = 0;
    for (std::size_t caseIndex = 0; caseIndex < TailCounts.size(); ++caseIndex)
    {
        const std::size_t count = TailCounts[caseIndex];
        const bool        dispatcherAdd =
          exact_single(dispatcher, requiredIsa, HmDeltaOperation::Add, count);
        const bool dispatcherRemove =
          exact_single(dispatcher, requiredIsa, HmDeltaOperation::Remove, count);
        const bool dispatcherRestore = exact_restore(dispatcher, requiredIsa, count);
        const bool stableAdd = exact_single(stable_path, requiredIsa, HmDeltaOperation::Add, count);
        const bool stableRemove =
          exact_single(stable_path, requiredIsa, HmDeltaOperation::Remove, count);
        const bool stableRestore = exact_restore(stable_path, requiredIsa, count);
        if (!(dispatcherAdd && dispatcherRemove && dispatcherRestore && stableAdd && stableRemove
              && stableRestore))
            die("tail case " + std::to_string(caseIndex) + " diverged");

        for (std::size_t index = 0; index < count; ++index)
            if (base_value(index) == 0)
                die("zero base entered the nonzero-base corpus");
        nonzeroBaseLanes += count;
        append_case_fingerprint(fingerprint, count);

        std::cout << "record=incremental_simd_kernel_tail\n"
                  << "case=" << caseIndex << '\n'
                  << "count=" << count << '\n'
                  << "isa.executed=" << simd_isa_name(requiredIsa) << '\n'
                  << "dispatcher.add.exact=1\n"
                  << "dispatcher.remove.exact=1\n"
                  << "dispatcher.restore.exact=1\n"
                  << "stable.add.exact=1\n"
                  << "stable.remove.exact=1\n"
                  << "stable.restore.exact=1\n"
                  << "destination.canaries=2\n"
                  << "source.canaries=2\n"
                  << "source.immutable=1\n"
                  << "source.minimum_covered=" << (count >= 1 ? 1 : 0) << '\n'
                  << "source.maximum_covered=" << (count >= 2 ? 1 : 0) << '\n'
                  << "nonzero_bases=" << count << '\n'
                  << "comparison.exact=1\n"
                  << "end_case=" << caseIndex << '\n';
    }

    const std::size_t nullPointerProbes = run_null_pointer_probes(requiredIsa);
    const std::size_t unavailableProbes = run_unavailable_probes();
    run_wraparound_probes(requiredIsa);
    constexpr std::size_t CallsPerTailCase = 4;
    constexpr std::size_t WrapCallsPerPath = 2;
    constexpr std::size_t DispatcherCalls = TailCounts.size() * CallsPerTailCase + WrapCallsPerPath;
    constexpr std::size_t StableCalls     = TailCounts.size() * CallsPerTailCase + WrapCallsPerPath;
    constexpr std::size_t CanaryChecks    = TailCounts.size() * 6 * 4;

    std::cout << "record=incremental_simd_kernel_summary\n"
              << "isa.requested=" << simd_isa_name(requiredIsa) << '\n'
              << "isa.executed=" << simd_isa_name(requiredIsa) << '\n'
              << "tail_cases=" << TailCounts.size() << '\n'
              << "dispatcher_calls=" << DispatcherCalls << '\n'
              << "stable_calls=" << StableCalls << '\n'
              << "null_pointer_probes=" << nullPointerProbes << '\n'
              << "unavailable_probes=" << unavailableProbes << '\n'
              << "fallback_calls=0\n"
              << "canary_checks=" << CanaryChecks << '\n'
              << "source.minimum_covered=1\n"
              << "source.maximum_covered=1\n"
              << "nonzero_base_lanes=" << nonzeroBaseLanes << '\n'
              << "fingerprint=0x" << std::hex << std::uppercase << std::setfill('0')
              << std::setw(16) << fingerprint << std::dec << std::setfill(' ') << '\n'
              << "end_summary=1\n"
              << "AtomicNNUEV3 incremental SIMD kernel gate passed: requested="
              << simd_isa_name(requiredIsa) << " executed=" << simd_isa_name(requiredIsa)
              << " tail-cases=" << TailCounts.size() << " dispatcher-calls=" << DispatcherCalls
              << " stable-calls=" << StableCalls << " null-pointer-probes=" << nullPointerProbes
              << " unavailable-probes=" << unavailableProbes
              << " fallback-calls=0 canary-checks=" << CanaryChecks
              << " nonzero-base-lanes=" << nonzeroBaseLanes << " fingerprint=0x" << std::hex
              << std::uppercase << std::setfill('0') << std::setw(16) << fingerprint << std::dec
              << std::setfill(' ') << '\n';
    return EXIT_SUCCESS;
}

}  // namespace
}  // namespace Stockfish::Eval::NNUE::AtomicV3

int main(int argc, char* argv[]) {
    using namespace Stockfish::Eval::NNUE::AtomicV3;
    return run(parse_options(argc, argv));
}
