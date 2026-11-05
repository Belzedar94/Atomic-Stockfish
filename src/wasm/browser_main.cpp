/*
  Atomic-Stockfish, a UCI Atomic chess playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Atomic-Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

// Entry point of the browser Web Worker build (tests/wasm-browser/build.py).
// It replaces main.cpp: there is no stdin, so the worker hands UCI command
// lines to atomic_uci() one at a time, and the engine's output is delivered
// line by line to the Emscripten print/printErr callbacks.
//
// Output path. Search threads are pthreads, and an ordinary stdout write from
// a pthread is proxied synchronously to the main runtime thread (the worker).
// That would make every info line wait for the worker's event loop, and would
// deadlock whenever the worker itself waits for the search. Instead std::cout
// and std::cerr write into an in-memory line queue. A pthread that queues a
// line schedules one asynchronous drain on the main runtime thread and never
// waits for it; the main runtime thread drains the queue itself after every
// command, so lines keep their order across threads.

#ifndef __EMSCRIPTEN__
    #error "browser_main.cpp is only part of the Emscripten browser-worker build"
#endif

#include <atomic>
#include <cstring>
#include <deque>
#include <iostream>
#include <memory>
#include <mutex>
#include <sstream>
#include <streambuf>
#include <string>
#include <string_view>
#include <utility>

#include <emscripten.h>
#include <emscripten/proxying.h>
#include <emscripten/threading.h>

#include "../atomic_init.h"
#include "../misc.h"
#include "../tune.h"
#include "../uci.h"

using namespace Stockfish;

namespace {

enum OutputStream : int {
    StdOut = 1,
    StdErr = 2
};

struct QueuedLine {
    int         stream;
    std::string text;
};

std::mutex             queueMutex;
std::deque<QueuedLine> lineQueue;
std::atomic<bool>      drainScheduled{false};

}  // namespace

// Runs on the main runtime thread. `out` and `err` are the Emscripten wrappers
// of Module.print and Module.printErr, which the embedder configures.
// clang-format off
EM_JS(void, atomic_emit_line, (int stream, const char* text, int length), {
    const line = UTF8ToString(text, length);
    if (stream === 2)
        err(line);
    else
        out(line);
});
// clang-format on

namespace {

void drain_queue() {
    drainScheduled.store(false, std::memory_order_release);

    std::deque<QueuedLine> lines;
    {
        std::lock_guard<std::mutex> lock(queueMutex);
        lines.swap(lineQueue);
    }

    for (const auto& line : lines)
        atomic_emit_line(line.stream, line.text.data(), int(line.text.size()));
}

void drain_task(void*) { drain_queue(); }

void queue_line(int stream, std::string&& text) {
    {
        std::lock_guard<std::mutex> lock(queueMutex);
        lineQueue.push_back({stream, std::move(text)});
    }

    if (emscripten_is_main_runtime_thread())
        drain_queue();
    else if (!drainScheduled.exchange(true, std::memory_order_acq_rel))
        emscripten_proxy_async(emscripten_proxy_get_system_queue(),
                               emscripten_main_runtime_thread_id(), drain_task, nullptr);
}

// A stream buffer that cuts the character stream into lines.
class LineBuffer final: public std::streambuf {
   public:
    explicit LineBuffer(int s) :
        stream(s) {}

   protected:
    int_type overflow(int_type c) override {
        if (!traits_type::eq_int_type(c, traits_type::eof()))
        {
            std::lock_guard<std::mutex> lock(mutex);
            put(traits_type::to_char_type(c));
        }
        return traits_type::not_eof(c);
    }

    std::streamsize xsputn(const char* s, std::streamsize n) override {
        std::lock_guard<std::mutex> lock(mutex);
        for (std::streamsize i = 0; i < n; ++i)
            put(s[i]);
        return n;
    }

   private:
    void put(char c) {
        if (c != '\n')
        {
            pending.push_back(c);
            return;
        }
        if (!pending.empty() && pending.back() == '\r')
            pending.pop_back();
        queue_line(stream, std::move(pending));
        pending = std::string();
    }

    int         stream;
    std::mutex  mutex;
    std::string pending;
};

LineBuffer stdoutBuffer(StdOut);
LineBuffer stderrBuffer(StdErr);

char  programName[] = "atomic-stockfish";
char* programArgv[] = {programName, nullptr};

std::unique_ptr<UCIEngine> uci;

}  // namespace

extern "C" {

// Process one UCI command line. Output is delivered through Module.print
// (stdout) and Module.printErr (stderr). Search commands return immediately;
// their info and bestmove lines follow asynchronously from the search threads.
// After 'quit' the engine is destroyed and further commands are ignored.
EMSCRIPTEN_KEEPALIVE
void atomic_uci(const char* command) {
    if (!uci || command == nullptr)
        return;

    const std::string_view line(command);

    std::istringstream is{std::string(line)};
    std::string        token;
    is >> token;

    uci->execute(line);

    // Only 'quit' ends the browser session. The XBoard switch is answered with
    // an error by the UCI-only protocol shim and leaves the engine running.
    if (token == "quit")
        uci.reset();  // Joins every search thread; their pthreads return to the pool.

    drain_queue();
}

}  // extern "C"

int main() {
    std::cout.rdbuf(&stdoutBuffer);
    std::cerr.rdbuf(&stderrBuffer);

    std::cout << engine_info() << std::endl;

    initialize_atomic_core();

    uci = std::make_unique<UCIEngine>(CommandLine(1, programArgv));

    Tune::init(uci->engine_options());

    drain_queue();

    // Returning keeps the runtime alive (NO_EXIT_RUNTIME); the worker drives the
    // engine through atomic_uci() from now on.
    return 0;
}
