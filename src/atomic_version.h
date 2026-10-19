/*
  Atomic-Stockfish version

  The source tree carries no release number. A release build passes its
  version on the compiler command line as ATOMIC_VERSION=MAJOR.MINOR.PATCH
  (the Makefile, setup.py and the WebAssembly builds all take it from an
  ATOMIC_VERSION variable). Every other build is a development build and names
  its commit instead: dev-YYYYMMDD-<sha>, see engine_version_info() in misc.cpp.
*/

#ifndef ATOMIC_VERSION_H_INCLUDED
#define ATOMIC_VERSION_H_INCLUDED

#include <cstddef>
#include <string_view>

#define ATOMIC_VERSION_STRINGIFY2(x) #x
#define ATOMIC_VERSION_STRINGIFY(x) ATOMIC_VERSION_STRINGIFY2(x)

namespace Stockfish {

#ifdef ATOMIC_VERSION
inline constexpr std::string_view AtomicVersionString = ATOMIC_VERSION_STRINGIFY(ATOMIC_VERSION);
#else
inline constexpr std::string_view AtomicVersionString = "dev";
#endif

inline constexpr bool AtomicIsRelease = AtomicVersionString != "dev";

namespace AtomicVersionDetail {

struct ReleaseNumber {
    bool valid;
    int  parts[3];
};

// Splits MAJOR.MINOR.PATCH (decimal, no leading zeros) into its components.
constexpr ReleaseNumber parse(std::string_view text) {
    constexpr ReleaseNumber invalid{false, {0, 0, 0}};
    int                     parts[3] = {0, 0, 0};
    int                     part     = 0;
    std::size_t             digits   = 0;

    for (char c : text)
    {
        if (c == '.')
        {
            if (digits == 0 || part == 2)
                return invalid;
            ++part;
            digits = 0;
        }
        else if (c >= '0' && c <= '9')
        {
            if ((digits == 1 && parts[part] == 0) || digits == 9)
                return invalid;
            parts[part] = parts[part] * 10 + (c - '0');
            ++digits;
        }
        else
            return invalid;
    }

    if (part != 2 || digits == 0)
        return invalid;

    return {true, {parts[0], parts[1], parts[2]}};
}

inline constexpr ReleaseNumber Release =
  AtomicIsRelease ? parse(AtomicVersionString) : ReleaseNumber{true, {0, 0, 0}};

static_assert(Release.valid, "ATOMIC_VERSION must be MAJOR.MINOR.PATCH");

}  // namespace AtomicVersionDetail

// Components of the release version; all zero in development builds.
inline constexpr int AtomicVersionMajor = AtomicVersionDetail::Release.parts[0];
inline constexpr int AtomicVersionMinor = AtomicVersionDetail::Release.parts[1];
inline constexpr int AtomicVersionPatch = AtomicVersionDetail::Release.parts[2];

}  // namespace Stockfish

#endif  // ATOMIC_VERSION_H_INCLUDED
