/*
  Atomic-Stockfish training-data generator
  Copyright (C) 2026 The Atomic-Stockfish developers

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#ifndef DATA_RANDOM_SEED_H_INCLUDED
#define DATA_RANDOM_SEED_H_INCLUDED

#include <algorithm>
#include <cassert>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <limits>
#include <string>
#include <string_view>

namespace Stockfish::Data {

// This intentionally matches the historical variant-nnue-tools seed and
// xorshift64* behavior. A run prints the resolved decimal seed so an omitted
// or textual seed can always be replayed exactly.
class ReplayablePRNG {
   public:
    static constexpr std::uint64_t ZeroSeedFallback = 0x9E3779B97F4A7C15ULL;

    explicit ReplayablePRNG(std::uint64_t seed) :
        state(seed ? seed : ZeroSeedFallback) {}

    template<typename T = std::uint64_t>
    T rand() {
        return T(next());
    }

    std::uint64_t rand(std::uint64_t bound) {
        assert(bound != 0);
        return next() % bound;
    }

    std::uint64_t seed() const { return state; }

    std::uint64_t next_seed() {
        const std::uint64_t value = next();
        return value ? value : ZeroSeedFallback;
    }

    static std::uint64_t resolve(std::string_view text) {
        if (text.empty())
        {
            const auto value =
              std::uint64_t(std::chrono::system_clock::now().time_since_epoch().count());
            return value ? value : ZeroSeedFallback;
        }

        const bool decimal = std::all_of(text.begin(), text.end(),
                                         [](unsigned char c) { return std::isdigit(c) != 0; });
        if (decimal)
        {
            std::uint64_t value = 0;
            for (const unsigned char c : text)
            {
                const std::uint64_t digit = c - '0';
                if (value > (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
                    return normalized_hash(text);
                value = value * 10 + digit;
            }
            return value ? value : ZeroSeedFallback;
        }

        return normalized_hash(text);
    }

   private:
    std::uint64_t state;

    std::uint64_t next() {
        state ^= state >> 12;
        state ^= state << 25;
        state ^= state >> 27;
        return state * 2685821657736338717ULL;
    }

    static std::uint64_t normalized_hash(std::string_view text) {
        std::uint64_t hash = 525201411107845655ULL;
        for (const unsigned char c : text)
        {
            hash ^= std::uint64_t(c);
            hash *= 0x5bd1e9955bd1e995ULL;
            hash ^= hash >> 47;
        }
        return hash ? hash : ZeroSeedFallback;
    }
};

}  // namespace Stockfish::Data

#endif  // DATA_RANDOM_SEED_H_INCLUDED
