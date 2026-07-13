/*
  Atomic-Stockfish, a specialized Atomic Chess engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include <charconv>
#include <filesystem>
#include <iostream>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#ifdef _WIN32
    #include <cstdio>
    #include <fcntl.h>
    #include <io.h>
    #include <windows.h>
#endif

#include "atomic_data_tools_json.h"
#include "atomic_bin_v2_manifest.h"
#include "atomic_bin_v2_reader.h"
#include "atomic_bin_v2_wire.h"
#include "tt.h"

namespace Stockfish {

// Position only uses a non-null table for an optional prefetch. The data-tools
// executable intentionally excludes the playing engine's transposition table,
// search and thread objects.
TTEntry* TranspositionTable::first_entry(const Key) const { return nullptr; }

namespace {

using Data::AtomicBinV2DatasetReader;
using Data::AtomicBinV2DecodedRecord;
using Data::DataError;
using Data::DataResult;

constexpr int SuccessExit       = 0;
constexpr int ContractErrorExit = 2;
constexpr int DatasetErrorExit  = 3;

std::size_t utf8_sequence_length(std::string_view value, std::size_t offset) {
    const auto byte = [&](std::size_t index) { return static_cast<unsigned char>(value[index]); };
    const auto continuation = [&](std::size_t index) {
        return index < value.size() && (byte(index) & 0xC0) == 0x80;
    };

    const unsigned char first = byte(offset);
    if (first < 0x80)
        return 1;
    if (first >= 0xC2 && first <= 0xDF && continuation(offset + 1))
        return 2;
    if (first == 0xE0 && offset + 2 < value.size() && byte(offset + 1) >= 0xA0
        && byte(offset + 1) <= 0xBF && continuation(offset + 2))
        return 3;
    if (((first >= 0xE1 && first <= 0xEC) || (first >= 0xEE && first <= 0xEF))
        && continuation(offset + 1) && continuation(offset + 2))
        return 3;
    if (first == 0xED && offset + 2 < value.size() && byte(offset + 1) >= 0x80
        && byte(offset + 1) <= 0x9F && continuation(offset + 2))
        return 3;
    if (first == 0xF0 && offset + 3 < value.size() && byte(offset + 1) >= 0x90
        && byte(offset + 1) <= 0xBF && continuation(offset + 2) && continuation(offset + 3))
        return 4;
    if (first >= 0xF1 && first <= 0xF3 && continuation(offset + 1) && continuation(offset + 2)
        && continuation(offset + 3))
        return 4;
    if (first == 0xF4 && offset + 3 < value.size() && byte(offset + 1) >= 0x80
        && byte(offset + 1) <= 0x8F && continuation(offset + 2) && continuation(offset + 3))
        return 4;
    return 0;
}

bool valid_utf8(std::string_view value) {
    for (std::size_t offset = 0; offset < value.size();)
    {
        const std::size_t length = utf8_sequence_length(value, offset);
        if (length == 0)
            return false;
        offset += length;
    }
    return true;
}

std::string json_string(std::string_view value) {
    constexpr char Hex[] = "0123456789abcdef";
    std::string    output;
    output.reserve(value.size() + 2);
    output.push_back('"');
    for (std::size_t offset = 0; offset < value.size();)
    {
        const unsigned char character = static_cast<unsigned char>(value[offset]);
        if (character >= 0x80)
        {
            const std::size_t length = utf8_sequence_length(value, offset);
            if (length == 0)
            {
                output += "\\ufffd";
                ++offset;
            }
            else
            {
                output.append(value, offset, length);
                offset += length;
            }
            continue;
        }

        switch (character)
        {
        case '"' :
            output += "\\\"";
            break;
        case '\\' :
            output += "\\\\";
            break;
        case '\b' :
            output += "\\b";
            break;
        case '\f' :
            output += "\\f";
            break;
        case '\n' :
            output += "\\n";
            break;
        case '\r' :
            output += "\\r";
            break;
        case '\t' :
            output += "\\t";
            break;
        default :
            if (character < 0x20)
            {
                output += "\\u00";
                output.push_back(Hex[character >> 4]);
                output.push_back(Hex[character & 0x0F]);
            }
            else
                output.push_back(static_cast<char>(character));
        }
        ++offset;
    }
    output.push_back('"');
    return output;
}

std::string_view error_code(DataError error) {
    switch (error)
    {
    case DataError::NONE :
        return "none";
    case DataError::UNSUPPORTED_BYTE_ORDER :
        return "unsupported_byte_order";
    case DataError::UNSUPPORTED_CHESS960 :
        return "unsupported_chess960";
    case DataError::UNSUPPORTED_POSITION :
        return "unsupported_position";
    case DataError::INVALID_MOVE :
        return "invalid_move";
    case DataError::SCORE_OUT_OF_RANGE :
        return "score_out_of_range";
    case DataError::PLY_OUT_OF_RANGE :
        return "ply_out_of_range";
    case DataError::RESULT_OUT_OF_RANGE :
        return "result_out_of_range";
    case DataError::POSITION_CLOCK_OUT_OF_RANGE :
        return "position_clock_out_of_range";
    case DataError::PACKED_POSITION_OVERFLOW :
        return "packed_position_overflow";
    case DataError::OUTPUT_EXISTS :
        return "output_exists";
    case DataError::OPEN_FAILED :
        return "open_failed";
    case DataError::WRITE_FAILED :
        return "write_failed";
    case DataError::CLOSE_FAILED :
        return "close_failed";
    case DataError::EMPTY_DATASET :
        return "empty_dataset";
    case DataError::ABORT_FAILED :
        return "abort_failed";
    case DataError::SINK_CLOSED :
        return "sink_closed";
    case DataError::INVALID_HEADER :
        return "invalid_header";
    case DataError::INVALID_RECORD :
        return "invalid_record";
    case DataError::INVALID_MANIFEST :
        return "invalid_manifest";
    case DataError::SCHEMA_MISMATCH :
        return "schema_mismatch";
    case DataError::RECORD_COUNT_OUT_OF_RANGE :
        return "record_count_out_of_range";
    case DataError::FILE_SIZE_MISMATCH :
        return "file_size_mismatch";
    case DataError::READ_FAILED :
        return "read_failed";
    case DataError::FILE_IDENTITY_MISMATCH :
        return "file_identity_mismatch";
    }
    return "unknown_data_error";
}

int emit_error(std::string_view                  operation,
               const std::optional<std::string>& format,
               std::string_view                  code,
               std::string_view                  message,
               int                               exitCode) {
    std::cerr
      << R"({"type":"atomic-data-tools-error","contract_version":1,"status":"error","operation":)"
      << json_string(operation) << R"(,"format":)" << (format ? json_string(*format) : "null")
      << R"(,"code":)" << json_string(code) << R"(,"message":)" << json_string(message) << "}\n";
    return exitCode;
}

int emit_contract_error(std::string_view                  operation,
                        const std::optional<std::string>& format,
                        std::string_view                  code,
                        std::string_view                  message) {
    return emit_error(operation, format, code, message, ContractErrorExit);
}

int emit_dataset_error(std::string_view   operation,
                       const std::string& format,
                       const DataResult&  result) {
    return emit_error(operation, format, error_code(result.error), result.message,
                      DatasetErrorExit);
}

int capabilities(const std::vector<std::string>& arguments) {
    if (arguments.size() != 2)
        return emit_contract_error("capabilities", std::nullopt, "unexpected_argument",
                                   "capabilities accepts no arguments");

    std::cout
      << R"({"type":"atomic-data-tools-capabilities","contract_version":1,"formats":{"atomic-bin-v2":{"data_schema_sha256":")"
      << Data::AtomicBinV2SchemaSha256Hex << R"(","manifest_schema_sha256":")"
      << Data::AtomicBinV2ManifestSchemaSha256Hex << R"(","decode_schema_sha256":")"
      << Data::AtomicDataToolsDecodeSchemaSha256Hex
      << R"(","entrypoint":"manifest","read":true,"write":false,"operations":["validate","decode"]}}})"
      << '\n';
    return SuccessExit;
}

int validate(const std::vector<std::string>& arguments) {
    std::optional<std::string> format;
    std::optional<std::string> manifest;

    const auto is_validate_option = [](std::string_view argument) {
        return argument == "--format" || argument == "--manifest";
    };

    for (std::size_t index = 2; index < arguments.size(); ++index)
    {
        const std::string_view      argument    = arguments[index];
        std::optional<std::string>* destination = nullptr;
        if (argument == "--format")
            destination = &format;
        else if (argument == "--manifest")
            destination = &manifest;
        else
            return emit_contract_error("validate", format, "unknown_argument",
                                       "unknown validate argument: " + std::string(argument));

        if (*destination)
            return emit_contract_error("validate", format, "duplicate_argument",
                                       "duplicate validate argument: " + std::string(argument));
        if (index + 1 >= arguments.size() || is_validate_option(arguments[index + 1]))
            return emit_contract_error("validate", format, "missing_value",
                                       "missing value for validate argument: "
                                         + std::string(argument));
        *destination = arguments[++index];
    }

    if (!format)
        return emit_contract_error("validate", std::nullopt, "missing_format",
                                   "validate requires --format atomic-bin-v2");
    if (*format != "atomic-bin-v2")
        return emit_contract_error("validate", format, "unsupported_format",
                                   "unsupported data format: " + *format);
    if (!manifest)
        return emit_contract_error("validate", format, "missing_manifest",
                                   "validate requires --manifest <dataset.atbin.manifest.json>");
    if (manifest->empty())
        return emit_contract_error("validate", format, "empty_manifest",
                                   "--manifest must not be empty");

    std::unique_ptr<AtomicBinV2DatasetReader> reader;
    if (DataResult opened =
          AtomicBinV2DatasetReader::open(std::filesystem::u8path(*manifest), reader);
        !opened)
        return emit_dataset_error("validate", *format, opened);

    u64 records          = 0;
    u64 sideToMoveWins   = 0;
    u64 draws            = 0;
    u64 sideToMoveLosses = 0;
    u64 atomic960Records = 0;
    while (true)
    {
        AtomicBinV2DecodedRecord decoded;
        bool                     hasRecord = false;
        if (DataResult read = reader->next(decoded, hasRecord); !read)
            return emit_dataset_error("validate", *format, read);
        if (!hasRecord)
            break;

        ++records;
        sideToMoveWins += decoded.sample.result > 0;
        draws += decoded.sample.result == 0;
        sideToMoveLosses += decoded.sample.result < 0;
        atomic960Records += bool(decoded.fields.flags & Data::ATOMIC_BIN_V2_ATOMIC960);
    }

    const Data::AtomicDataToolsValidationStats stats{u32(reader->manifest().shards.size()),
                                                     records,
                                                     sideToMoveWins,
                                                     draws,
                                                     sideToMoveLosses,
                                                     atomic960Records};
    std::cout << Data::render_atomic_data_tools_validation_json(stats);
    return SuccessExit;
}

bool parse_unsigned(std::string_view text, u64& output) {
    output = 0;
    if (text.empty())
        return false;
    const char* first  = text.data();
    const char* last   = first + text.size();
    const auto  parsed = std::from_chars(first, last, output, 10);
    return parsed.ec == std::errc{} && parsed.ptr == last;
}

int decode(const std::vector<std::string>& arguments) {
    std::optional<std::string> format;
    std::optional<std::string> manifest;
    std::optional<std::string> offsetToken;
    std::optional<std::string> limitToken;

    const auto is_decode_option = [](std::string_view argument) {
        return argument == "--format" || argument == "--manifest" || argument == "--offset"
            || argument == "--limit";
    };

    for (std::size_t index = 2; index < arguments.size(); ++index)
    {
        const std::string_view      argument = arguments[index];
        std::optional<std::string>* destination;
        if (argument == "--format")
            destination = &format;
        else if (argument == "--manifest")
            destination = &manifest;
        else if (argument == "--offset")
            destination = &offsetToken;
        else if (argument == "--limit")
            destination = &limitToken;
        else
            return emit_contract_error("decode", format, "unknown_argument",
                                       "unknown decode argument: " + std::string(argument));

        if (*destination)
            return emit_contract_error("decode", format, "duplicate_argument",
                                       "duplicate decode argument: " + std::string(argument));
        if (index + 1 >= arguments.size() || is_decode_option(arguments[index + 1]))
            return emit_contract_error("decode", format, "missing_value",
                                       "missing value for decode argument: "
                                         + std::string(argument));
        *destination = arguments[++index];
    }

    if (!format)
        return emit_contract_error("decode", std::nullopt, "missing_format",
                                   "decode requires --format atomic-bin-v2");
    if (*format != "atomic-bin-v2")
        return emit_contract_error("decode", format, "unsupported_format",
                                   "unsupported data format: " + *format);
    if (!manifest)
        return emit_contract_error("decode", format, "missing_manifest",
                                   "decode requires --manifest <dataset.atbin.manifest.json>");
    if (manifest->empty())
        return emit_contract_error("decode", format, "empty_manifest",
                                   "--manifest must not be empty");
    if (!limitToken)
        return emit_contract_error("decode", format, "missing_limit",
                                   "decode requires --limit <1..4096>");

    u64 offset = 0;
    if (offsetToken && !parse_unsigned(*offsetToken, offset))
        return emit_contract_error("decode", format, "invalid_offset",
                                   "--offset must be an unsigned 64-bit decimal integer");
    u64 parsedLimit = 0;
    if (!parse_unsigned(*limitToken, parsedLimit))
        return emit_contract_error("decode", format, "invalid_limit",
                                   "--limit must be an unsigned decimal integer in 1..4096");
    if (parsedLimit == 0 || parsedLimit > 4096)
        return emit_contract_error("decode", format, "limit_out_of_range",
                                   "--limit must be in 1..4096");
    const u32 limit = u32(parsedLimit);

    std::unique_ptr<AtomicBinV2DatasetReader> reader;
    if (DataResult opened =
          AtomicBinV2DatasetReader::open(std::filesystem::u8path(*manifest), reader);
        !opened)
        return emit_dataset_error("decode", *format, opened);
    if (offset > reader->manifest().records || u64(limit) > reader->manifest().records - offset)
        return emit_contract_error("decode", format, "range_out_of_bounds",
                                   "decode slice must fit entirely inside the dataset");

    // Buffer at most 4096 rendered records. Nothing reaches stdout until the
    // reader authenticates and round-trips every record in every shard.
    std::vector<std::string> renderedRecords;
    renderedRecords.reserve(limit);
    u64 records          = 0;
    u64 sideToMoveWins   = 0;
    u64 draws            = 0;
    u64 sideToMoveLosses = 0;
    u64 atomic960Records = 0;
    while (true)
    {
        AtomicBinV2DecodedRecord decoded;
        bool                     hasRecord = false;
        if (DataResult read = reader->next(decoded, hasRecord); !read)
            return emit_dataset_error("decode", *format, read);
        if (!hasRecord)
            break;

        if (decoded.globalIndex >= offset && decoded.globalIndex - offset < limit)
            renderedRecords.push_back(Data::render_atomic_data_tools_decode_record(decoded));
        ++records;
        sideToMoveWins += decoded.sample.result > 0;
        draws += decoded.sample.result == 0;
        sideToMoveLosses += decoded.sample.result < 0;
        atomic960Records += bool(decoded.fields.flags & Data::ATOMIC_BIN_V2_ATOMIC960);
    }

    if (renderedRecords.size() != limit)
        return emit_error("decode", *format, "range_out_of_bounds",
                          "decode slice did not produce its complete requested range",
                          DatasetErrorExit);

    const Data::AtomicDataToolsValidationStats stats{u32(reader->manifest().shards.size()),
                                                     records,
                                                     sideToMoveWins,
                                                     draws,
                                                     sideToMoveLosses,
                                                     atomic960Records};
    std::string                                output =
      Data::render_atomic_data_tools_decode_header(reader->manifest(), offset, limit);
    for (const std::string& record : renderedRecords)
        output += record;
    output += Data::render_atomic_data_tools_decode_footer(stats, offset, limit);
    std::cout << output;
    return SuccessExit;
}

int run(const std::vector<std::string>& arguments) {
    for (const std::string& argument : arguments)
        if (!valid_utf8(argument))
            return emit_contract_error("cli", std::nullopt, "invalid_utf8_argument",
                                       "all command-line arguments must be valid UTF-8");

    if (arguments.size() < 2)
        return emit_contract_error("cli", std::nullopt, "missing_command",
                                   "expected capabilities, validate, or decode");

    const std::string_view command = arguments[1];
    if (command == "capabilities")
        return capabilities(arguments);
    if (command == "validate")
        return validate(arguments);
    if (command == "decode")
        return decode(arguments);
    return emit_contract_error("cli", std::nullopt, "unknown_command",
                               "unknown command: " + std::string(command));
}

#ifdef _WIN32
std::optional<std::string> wide_to_utf8(std::wstring_view value) {
    if (value.empty())
        return std::string{};
    if (value.size() > std::size_t(std::numeric_limits<int>::max()))
        return std::nullopt;
    const int size = ::WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                                           int(value.size()), nullptr, 0, nullptr, nullptr);
    if (size <= 0)
        return std::nullopt;
    std::string output(std::size_t(size), '\0');
    if (::WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), int(value.size()),
                              output.data(), size, nullptr, nullptr)
        != size)
        return std::nullopt;
    return output;
}

void configure_binary_output() {
    // Canonical CLI responses require one LF on every platform. The Windows
    // CRT otherwise rewrites each LF sent to cout/cerr into CRLF.
    _setmode(_fileno(stdout), _O_BINARY);
    _setmode(_fileno(stderr), _O_BINARY);
}
#endif

}  // namespace
}  // namespace Stockfish

#ifdef _WIN32
int wmain(int argc, wchar_t* argv[]);

int wmain(int argc, wchar_t* argv[]) {
    Stockfish::configure_binary_output();
    std::vector<std::string> arguments;
    arguments.reserve(std::size_t(argc));
    for (int index = 0; index < argc; ++index)
    {
        std::optional<std::string> argument = Stockfish::wide_to_utf8(argv[index]);
        if (!argument)
            return Stockfish::emit_contract_error("cli", std::nullopt, "invalid_unicode_argument",
                                                  "command line contains invalid UTF-16");
        arguments.push_back(std::move(*argument));
    }
    return Stockfish::run(arguments);
}
#else
int main(int argc, char* argv[]) {
    return Stockfish::run(std::vector<std::string>(argv, argv + argc));
}
#endif
