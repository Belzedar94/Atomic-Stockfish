/*
  Atomic-Stockfish release version

  Keep every public surface on one semantic version. Python packaging reads
  the three integer constants directly from this header; native, XBoard and
  Python bindings include it at compile time.
*/

#ifndef ATOMIC_VERSION_H_INCLUDED
#define ATOMIC_VERSION_H_INCLUDED

#include <string_view>

namespace Stockfish {

inline constexpr int AtomicVersionMajor = 1;
inline constexpr int AtomicVersionMinor = 0;
inline constexpr int AtomicVersionPatch = 2;

inline constexpr std::string_view AtomicVersionString = "1.0.2";

}  // namespace Stockfish

#endif  // ATOMIC_VERSION_H_INCLUDED
