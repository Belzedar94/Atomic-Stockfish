/* Build-only protocol shim for the Node WebAssembly engine. */

#ifndef XBOARD_H_INCLUDED
#define XBOARD_H_INCLUDED

#include <iostream>

namespace Stockfish {

class Engine;

// uci.cpp shares one native source with the XBoard-enabled binary. The Node
// artifact intentionally exposes only its stdin/stdout UCI surface, so this
// build-only class rejects the otherwise recognized protocol switch without
// linking the CECP implementation.
class XBoardProtocol final {
   public:
    explicit XBoardProtocol(Engine&) {}
    void loop() {
        std::cout << "Error (unsupported protocol): this WebAssembly artifact is UCI-only"
                  << std::endl;
    }
};

}  // namespace Stockfish

#endif  // XBOARD_H_INCLUDED
