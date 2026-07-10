/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <Python.h>

#include <algorithm>
#include <cctype>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "api/atomic_board.h"
#include "api/atomic_fen.h"
#include "api/atomic_notation.h"
#include "api/atomic_outcome.h"
#include "misc.h"
#include "position.h"
#include "tt.h"
#include "types.h"
#include "uci_move.h"

namespace {

using Stockfish::Atomic::Board;

constexpr int NotationDefault = 0;
constexpr int NotationSan     = 1;
constexpr int NotationLan     = 2;

struct PythonError final {};

class PyOwned final {
   public:
    explicit PyOwned(PyObject* object = nullptr) :
        value(object) {}
    ~PyOwned() { Py_XDECREF(value); }

    PyOwned(const PyOwned&)            = delete;
    PyOwned& operator=(const PyOwned&) = delete;

    PyObject* get() const { return value; }
    PyObject* release() {
        PyObject* result = value;
        value            = nullptr;
        return result;
    }

   private:
    PyObject* value;
};

std::string lower_trim(std::string value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos)
        return {};
    const auto last = value.find_last_not_of(" \t\r\n");
    value           = value.substr(first, last - first + 1);
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return value;
}

void require_atomic_variant(std::string_view variant) {
    if (lower_trim(std::string(variant)) != "atomic")
        throw std::invalid_argument("Atomic-Stockfish supports only the 'atomic' variant");
}

std::string utf8(PyObject* object) {
    PyOwned bytes(PyUnicode_AsEncodedString(object, "utf-8", "strict"));
    if (!bytes.get())
        throw PythonError{};

    char*      data = nullptr;
    Py_ssize_t size = 0;
    if (PyBytes_AsStringAndSize(bytes.get(), &data, &size) < 0)
        throw PythonError{};
    return std::string(data, static_cast<std::size_t>(size));
}

PyObject* python_string(std::string_view value) {
    return PyUnicode_FromStringAndSize(value.data(), static_cast<Py_ssize_t>(value.size()));
}

PyObject* python_strings(const std::vector<std::string>& values) {
    PyOwned result(PyList_New(static_cast<Py_ssize_t>(values.size())));
    if (!result.get())
        throw PythonError{};

    for (std::size_t i = 0; i < values.size(); ++i)
    {
        PyObject* item = python_string(values[i]);
        if (!item)
            throw PythonError{};
        // PyList_SetItem steals item, including on its error paths.
        if (PyList_SetItem(result.get(), static_cast<Py_ssize_t>(i), item) < 0)
            throw PythonError{};
    }
    return result.release();
}

std::vector<std::string> move_list(PyObject* list) {
    if (!PyList_Check(list))
    {
        PyErr_SetString(PyExc_TypeError, "movelist must be a list of UCI move strings");
        throw PythonError{};
    }

    const Py_ssize_t size = PyList_Size(list);
    if (size < 0)
        throw PythonError{};

    std::vector<std::string> result;
    result.reserve(static_cast<std::size_t>(size));
    for (Py_ssize_t i = 0; i < size; ++i)
    {
        PyObject* item = PyList_GetItem(list, i);  // Borrowed reference.
        if (!item)
            throw PythonError{};
        result.push_back(utf8(item));
    }
    return result;
}

std::unique_ptr<Board>
board_after(const char* variant, const char* fen, PyObject* moves, bool chess960) {
    require_atomic_variant(variant);
    auto board = std::make_unique<Board>(variant, fen, chess960);
    for (const std::string& move : move_list(moves))
        if (!board->push(move))
            throw std::invalid_argument("invalid Atomic UCI move: " + move);
    return board;
}

std::string formatted_move(Board& board, const std::string& uciMove, int notation) {
    if (notation != NotationDefault && notation != NotationSan && notation != NotationLan)
        throw std::invalid_argument("Atomic-Stockfish supports only SAN and LAN notation");

    const Stockfish::Move move = Stockfish::UCI::to_move(board.position(), uciMove);
    if (move == Stockfish::Move::none())
        throw std::invalid_argument("invalid Atomic UCI move: " + uciMove);

    return notation == NotationLan ? Stockfish::Atomic::to_lan(board.position(), move)
                                   : Stockfish::Atomic::to_san(board.position(), move);
}

template<typename Function>
PyObject* translated(Function&& function) {
    try
    { return std::forward<Function>(function)(); } catch (const PythonError&)
    { return nullptr; } catch (const std::invalid_argument& error)
    {
        PyErr_SetString(PyExc_ValueError, error.what());
        return nullptr;
    } catch (const std::out_of_range& error)
    {
        PyErr_SetString(PyExc_IndexError, error.what());
        return nullptr;
    } catch (const std::exception& error)
    {
        PyErr_SetString(PyExc_ValueError, error.what());
        return nullptr;
    } catch (...)
    {
        PyErr_SetString(PyExc_RuntimeError, "unknown Atomic-Stockfish binding error");
        return nullptr;
    }
}

struct BindingOptions {
    std::mutex                         mutex;
    std::map<std::string, std::string> values = {
      {"evalfile", ""},          {"hash", "16"},
      {"move overhead", "10"},   {"multipv", "1"},
      {"ponder", "false"},       {"syzygy50moverule", "true"},
      {"syzygypath", ""},        {"syzygyprobedepth", "1"},
      {"syzygyprobelimit", "6"}, {"threads", "1"},
      {"uci_chess960", "false"}, {"uci_variant", "atomic"},
      {"use nnue", "true"},      {"variantpath", ""},
    };
};

BindingOptions Options;

long parse_integer(const std::string& value, const char* option, long minimum, long maximum) {
    std::size_t consumed = 0;
    long        parsed;
    try
    { parsed = std::stol(value, &consumed); } catch (const std::exception&)
    { throw std::invalid_argument(std::string(option) + " requires an integer value"); }
    if (consumed != value.size() || parsed < minimum || parsed > maximum)
        throw std::invalid_argument(std::string(option) + " value is out of range");
    return parsed;
}

std::string parse_boolean(std::string value, const char* option) {
    value = lower_trim(std::move(value));
    if (value == "true" || value == "1" || value == "yes" || value == "on")
        return "true";
    if (value == "false" || value == "0" || value == "no" || value == "off")
        return "false";
    throw std::invalid_argument(std::string(option) + " requires a boolean value");
}

void set_binding_option(std::string name, std::string value) {
    name = lower_trim(std::move(name));

    std::scoped_lock lock(Options.mutex);
    const auto       found = Options.values.find(name);
    if (found == Options.values.end())
        throw std::invalid_argument("no such Atomic-Stockfish option: " + name);

    if (name == "uci_variant")
    {
        require_atomic_variant(value);
        value = "atomic";
    }
    else if (name == "uci_chess960" || name == "ponder" || name == "syzygy50moverule")
        value = parse_boolean(std::move(value), name.c_str());
    else if (name == "threads")
        value = std::to_string(parse_integer(value, "Threads", 1, 1024));
    else if (name == "hash")
        value = std::to_string(parse_integer(value, "Hash", 1, 33554432));
    else if (name == "multipv")
        value = std::to_string(parse_integer(value, "MultiPV", 1, 256));
    else if (name == "move overhead")
        value = std::to_string(parse_integer(value, "Move Overhead", 0, 5000));
    else if (name == "syzygyprobedepth")
        value = std::to_string(parse_integer(value, "SyzygyProbeDepth", 1, 100));
    else if (name == "syzygyprobelimit")
        value = std::to_string(parse_integer(value, "SyzygyProbeLimit", 0, 6));
    else if (name == "use nnue")
    {
        value = lower_trim(std::move(value));
        if (value != "false" && value != "true" && value != "pure")
            throw std::invalid_argument("Use NNUE must be false, true, or pure");
    }

    found->second = std::move(value);
}

PyObject* py_version(PyObject*, PyObject*) { return Py_BuildValue("(iii)", 0, 1, 0); }

PyObject* py_info(PyObject*, PyObject*) {
    return translated([] { return python_string(Stockfish::engine_info()); });
}

PyObject* py_variants(PyObject*, PyObject*) {
    return translated([] { return python_strings({"atomic"}); });
}

PyObject* py_set_option(PyObject*, PyObject* args) {
    const char* name  = nullptr;
    PyObject*   value = nullptr;
    if (!PyArg_ParseTuple(args, "sO:set_option", &name, &value))
        return nullptr;

    return translated([&] {
        PyOwned valueString(PyObject_Str(value));
        if (!valueString.get())
            throw PythonError{};
        set_binding_option(name, utf8(valueString.get()));
        Py_RETURN_NONE;
    });
}

PyObject* py_start_fen(PyObject*, PyObject* args) {
    const char* variant = nullptr;
    if (!PyArg_ParseTuple(args, "s:start_fen", &variant))
        return nullptr;
    return translated([&] {
        require_atomic_variant(variant);
        return python_string(Stockfish::Atomic::StartFEN);
    });
}

PyObject* py_two_boards(PyObject*, PyObject* args) {
    const char* variant = nullptr;
    if (!PyArg_ParseTuple(args, "s:two_boards", &variant))
        return nullptr;
    return translated([&] {
        require_atomic_variant(variant);
        return PyBool_FromLong(0);
    });
}

PyObject* py_captures_to_hand(PyObject*, PyObject* args) {
    const char* variant = nullptr;
    if (!PyArg_ParseTuple(args, "s:captures_to_hand", &variant))
        return nullptr;
    return translated([&] {
        require_atomic_variant(variant);
        return PyBool_FromLong(0);
    });
}

PyObject* py_get_san(PyObject*, PyObject* args) {
    const char* variant  = nullptr;
    const char* fen      = nullptr;
    const char* move     = nullptr;
    int         chess960 = 0;
    int         notation = NotationDefault;
    if (!PyArg_ParseTuple(args, "sss|pi:get_san", &variant, &fen, &move, &chess960, &notation))
        return nullptr;

    return translated([&] {
        require_atomic_variant(variant);
        Board board(variant, fen, chess960 != 0);
        return python_string(formatted_move(board, move, notation));
    });
}

PyObject* py_get_san_moves(PyObject*, PyObject* args) {
    const char* variant  = nullptr;
    const char* fen      = nullptr;
    PyObject*   moves    = nullptr;
    int         chess960 = 0;
    int         notation = NotationDefault;
    if (!PyArg_ParseTuple(args, "ssO|pi:get_san_moves", &variant, &fen, &moves, &chess960,
                          &notation))
        return nullptr;

    return translated([&] {
        require_atomic_variant(variant);
        Board                    board(variant, fen, chess960 != 0);
        std::vector<std::string> result;
        for (const std::string& move : move_list(moves))
        {
            result.push_back(formatted_move(board, move, notation));
            if (!board.push(move))
                throw std::invalid_argument("invalid Atomic UCI move: " + move);
        }
        return python_strings(result);
    });
}

PyObject* py_legal_moves(PyObject*, PyObject* args) {
    const char* variant  = nullptr;
    const char* fen      = nullptr;
    PyObject*   moves    = nullptr;
    int         chess960 = 0;
    if (!PyArg_ParseTuple(args, "ssO|p:legal_moves", &variant, &fen, &moves, &chess960))
        return nullptr;

    return translated([&] {
        auto board = board_after(variant, fen, moves, chess960 != 0);
        return python_strings(board->legal_moves());
    });
}

PyObject* py_get_fen(PyObject*, PyObject* args) {
    const char* variant      = nullptr;
    const char* fen          = nullptr;
    PyObject*   moves        = nullptr;
    int         chess960     = 0;
    int         sfen         = 0;
    int         showPromoted = 0;
    int         countStarted = 0;
    if (!PyArg_ParseTuple(args, "ssO|pppi:get_fen", &variant, &fen, &moves, &chess960, &sfen,
                          &showPromoted, &countStarted))
        return nullptr;

    return translated([&] {
        // These compatibility flags describe removed variant encodings. Atomic
        // FEN has one canonical representation, so accepting them is a no-op.
        (void) sfen;
        (void) showPromoted;
        (void) countStarted;
        auto board = board_after(variant, fen, moves, chess960 != 0);
        return python_string(board->fen());
    });
}

PyObject* py_gives_check(PyObject*, PyObject* args) {
    const char* variant  = nullptr;
    const char* fen      = nullptr;
    PyObject*   moves    = nullptr;
    int         chess960 = 0;
    if (!PyArg_ParseTuple(args, "ssO|p:gives_check", &variant, &fen, &moves, &chess960))
        return nullptr;

    return translated([&] {
        auto board = board_after(variant, fen, moves, chess960 != 0);
        return PyBool_FromLong(board->is_check());
    });
}

PyObject* py_is_capture(PyObject*, PyObject* args) {
    const char* variant  = nullptr;
    const char* fen      = nullptr;
    PyObject*   moves    = nullptr;
    const char* move     = nullptr;
    int         chess960 = 0;
    if (!PyArg_ParseTuple(args, "ssOs|p:is_capture", &variant, &fen, &moves, &move, &chess960))
        return nullptr;

    return translated([&] {
        auto board = board_after(variant, fen, moves, chess960 != 0);
        if (Stockfish::UCI::to_move(board->position(), move) == Stockfish::Move::none())
            throw std::invalid_argument(std::string("invalid Atomic UCI move: ") + move);
        return PyBool_FromLong(board->is_capture(move));
    });
}

PyObject* py_game_result(PyObject*, PyObject* args) {
    const char* variant  = nullptr;
    const char* fen      = nullptr;
    PyObject*   moves    = nullptr;
    int         chess960 = 0;
    if (!PyArg_ParseTuple(args, "ssO|p:game_result", &variant, &fen, &moves, &chess960))
        return nullptr;

    return translated([&] {
        auto                             board  = board_after(variant, fen, moves, chess960 != 0);
        const Stockfish::Atomic::Outcome result = Stockfish::Atomic::outcome(board->position());
        if (!result.terminal())
            throw std::invalid_argument("game_result requires a terminal Atomic position");
        return PyLong_FromLong(result.value);
    });
}

PyObject* py_is_immediate_game_end(PyObject*, PyObject* args) {
    const char* variant  = nullptr;
    const char* fen      = nullptr;
    PyObject*   moves    = nullptr;
    int         chess960 = 0;
    if (!PyArg_ParseTuple(args, "ssO|p:is_immediate_game_end", &variant, &fen, &moves, &chess960))
        return nullptr;

    return translated([&] {
        auto                             board  = board_after(variant, fen, moves, chess960 != 0);
        const Stockfish::Atomic::Outcome result = Stockfish::Atomic::outcome(board->position());
        const bool                       immediate =
          result.termination == Stockfish::Atomic::Termination::AtomicExplosion;
        return Py_BuildValue("(Ni)", PyBool_FromLong(immediate), immediate ? int(result.value) : 0);
    });
}

PyObject* py_is_optional_game_end(PyObject*, PyObject* args) {
    const char* variant      = nullptr;
    const char* fen          = nullptr;
    PyObject*   moves        = nullptr;
    int         chess960     = 0;
    int         countStarted = 0;
    if (!PyArg_ParseTuple(args, "ssO|pi:is_optional_game_end", &variant, &fen, &moves, &chess960,
                          &countStarted))
        return nullptr;

    return translated([&] {
        (void) countStarted;
        auto                             board = board_after(variant, fen, moves, chess960 != 0);
        const Stockfish::Atomic::Outcome result =
          Stockfish::Atomic::outcome(board->position(), true);
        const bool optional =
          result.termination == Stockfish::Atomic::Termination::FiftyMoveRule
          || result.termination == Stockfish::Atomic::Termination::ThreefoldRepetition;
        return Py_BuildValue("(Ni)", PyBool_FromLong(optional), optional ? int(result.value) : 0);
    });
}

PyObject* py_has_insufficient_material(PyObject*, PyObject* args) {
    const char* variant  = nullptr;
    const char* fen      = nullptr;
    PyObject*   moves    = nullptr;
    int         chess960 = 0;
    if (!PyArg_ParseTuple(args, "ssO|p:has_insufficient_material", &variant, &fen, &moves,
                          &chess960))
        return nullptr;

    return translated([&] {
        auto board = board_after(variant, fen, moves, chess960 != 0);
        return Py_BuildValue("(NN)", PyBool_FromLong(board->has_insufficient_material(true)),
                             PyBool_FromLong(board->has_insufficient_material(false)));
    });
}

PyObject* py_validate_fen(PyObject*, PyObject* args) {
    const char* fen      = nullptr;
    const char* variant  = nullptr;
    int         chess960 = 0;
    if (!PyArg_ParseTuple(args, "ss|p:validate_fen", &fen, &variant, &chess960))
        return nullptr;

    return translated([&] {
        require_atomic_variant(variant);
        return PyLong_FromLong(Stockfish::Atomic::validate_fen(fen, chess960 != 0));
    });
}

PyObject* py_perft(PyObject*, PyObject* args) {
    const char* variant  = nullptr;
    const char* fen      = nullptr;
    int         depth    = 0;
    int         chess960 = 0;
    PyObject*   moves    = nullptr;
    if (!PyArg_ParseTuple(args, "ssi|pO:perft", &variant, &fen, &depth, &chess960, &moves))
        return nullptr;

    return translated([&] {
        require_atomic_variant(variant);
        std::string position = fen;
        if (position.compare(0, 4, "fen ") == 0)
            position.erase(0, 4);
        Board board(variant, position, chess960 != 0);
        if (moves && moves != Py_None)
            for (const std::string& move : move_list(moves))
                if (!board.push(move))
                    throw std::invalid_argument("invalid Atomic UCI move: " + move);
        return PyLong_FromUnsignedLongLong(board.perft(depth));
    });
}

PyMethodDef Methods[] = {
  {"version", py_version, METH_NOARGS, "Return the pyffish semantic version tuple."},
  {"info", py_info, METH_NOARGS, "Return Atomic-Stockfish build information."},
  {"variants", py_variants, METH_NOARGS, "Return the singleton Atomic variant list."},
  {"set_option", py_set_option, METH_VARARGS, "Validate and set an Atomic option."},
  {"start_fen", py_start_fen, METH_VARARGS, "Return the canonical Atomic start FEN."},
  {"two_boards", py_two_boards, METH_VARARGS, "Return false for Atomic."},
  {"captures_to_hand", py_captures_to_hand, METH_VARARGS, "Return false for Atomic."},
  {"get_san", py_get_san, METH_VARARGS, "Format an Atomic UCI move as SAN or LAN."},
  {"get_san_moves", py_get_san_moves, METH_VARARGS, "Format an Atomic UCI move list."},
  {"legal_moves", py_legal_moves, METH_VARARGS, "Return legal Atomic UCI moves."},
  {"get_fen", py_get_fen, METH_VARARGS, "Return the Atomic FEN after a move list."},
  {"gives_check", py_gives_check, METH_VARARGS, "Return the resulting Atomic check state."},
  {"is_capture", py_is_capture, METH_VARARGS, "Classify an Atomic move as a capture."},
  {"game_result", py_game_result, METH_VARARGS,
   "Return a terminal result relative to side to move."},
  {"is_immediate_game_end", py_is_immediate_game_end, METH_VARARGS,
   "Return whether an Atomic explosion ended the game."},
  {"is_optional_game_end", py_is_optional_game_end, METH_VARARGS,
   "Return whether a draw can be claimed."},
  {"has_insufficient_material", py_has_insufficient_material, METH_VARARGS,
   "Return per-color Atomic insufficient-material flags."},
  {"validate_fen", py_validate_fen, METH_VARARGS, "Validate an Atomic FEN."},
  {"perft", py_perft, METH_VARARGS, "Count Atomic legal move tree leaves."},
  {nullptr, nullptr, 0, nullptr},
};

PyModuleDef Module = {
  PyModuleDef_HEAD_INIT,
  "pyffish",
  "Atomic-Stockfish rules binding.",
  -1,
  Methods,
  nullptr,
  nullptr,
  nullptr,
  nullptr,
};

bool add_int(PyObject* module, const char* name, long value) {
    PyObject* object = PyLong_FromLong(value);
    if (!object)
        return false;
    if (PyModule_AddObject(module, name, object) < 0)
    {
        Py_DECREF(object);
        return false;
    }
    return true;
}

}  // namespace

// The rules-only binding never supplies a transposition table to Position.
// This minimal definition keeps Position's optional prefetch hook linkable
// without dragging the search, thread-pool and large-page allocator into the
// stable-ABI wheel.
namespace Stockfish {
TTEntry* TranspositionTable::first_entry(const Key) const { return nullptr; }
}  // namespace Stockfish

PyMODINIT_FUNC PyInit_pyffish() {
    PyObject* module = PyModule_Create(&Module);
    if (!module)
        return nullptr;

    if (!add_int(module, "VALUE_MATE", Stockfish::VALUE_MATE)
        || !add_int(module, "VALUE_DRAW", Stockfish::VALUE_DRAW)
        || !add_int(module, "NOTATION_DEFAULT", NotationDefault)
        || !add_int(module, "NOTATION_SAN", NotationSan)
        || !add_int(module, "NOTATION_LAN", NotationLan)
        || !add_int(module, "FEN_INVALID_COUNTING_RULE",
                    Stockfish::Atomic::FEN_INVALID_COUNTING_RULE)
        || !add_int(module, "FEN_INVALID_CHECK_COUNT", Stockfish::Atomic::FEN_INVALID_CHECK_COUNT)
        || !add_int(module, "FEN_INVALID_PROMOTED_PIECE",
                    Stockfish::Atomic::FEN_INVALID_PROMOTED_PIECE)
        || !add_int(module, "FEN_INVALID_NB_PARTS", Stockfish::Atomic::FEN_INVALID_NB_PARTS)
        || !add_int(module, "FEN_INVALID_CHAR", Stockfish::Atomic::FEN_INVALID_CHAR)
        || !add_int(module, "FEN_TOUCHING_KINGS", Stockfish::Atomic::FEN_TOUCHING_KINGS)
        || !add_int(module, "FEN_INVALID_BOARD_GEOMETRY",
                    Stockfish::Atomic::FEN_INVALID_BOARD_GEOMETRY)
        || !add_int(module, "FEN_INVALID_POCKET_INFO", Stockfish::Atomic::FEN_INVALID_POCKET_INFO)
        || !add_int(module, "FEN_INVALID_SIDE_TO_MOVE", Stockfish::Atomic::FEN_INVALID_SIDE_TO_MOVE)
        || !add_int(module, "FEN_INVALID_CASTLING_INFO",
                    Stockfish::Atomic::FEN_INVALID_CASTLING_INFO)
        || !add_int(module, "FEN_INVALID_EN_PASSANT_SQ",
                    Stockfish::Atomic::FEN_INVALID_EN_PASSANT_SQ)
        || !add_int(module, "FEN_INVALID_NUMBER_OF_KINGS",
                    Stockfish::Atomic::FEN_INVALID_NUMBER_OF_KINGS)
        || !add_int(module, "FEN_INVALID_HALF_MOVE_COUNTER",
                    Stockfish::Atomic::FEN_INVALID_HALF_MOVE_COUNTER)
        || !add_int(module, "FEN_INVALID_MOVE_COUNTER", Stockfish::Atomic::FEN_INVALID_MOVE_COUNTER)
        || !add_int(module, "FEN_EMPTY", Stockfish::Atomic::FEN_EMPTY)
        || !add_int(module, "FEN_OK", Stockfish::Atomic::FEN_OK))
    {
        Py_DECREF(module);
        return nullptr;
    }

    Stockfish::Atomic::initialize();
    return module;
}
