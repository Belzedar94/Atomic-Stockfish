/*
  Atomic-Stockfish, a UCI chess variant playing engine derived from Stockfish
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Atomic-Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#include "atomic_bin_v2_sink.h"

#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <limits>
#include <string>
#include <system_error>
#include <utility>

#include "sha256.h"

#ifdef _WIN32
    #ifndef NOMINMAX
        #define NOMINMAX
    #endif
    #ifndef WIN32_LEAN_AND_MEAN
        #define WIN32_LEAN_AND_MEAN
    #endif
    #include <windows.h>

    #include <fcntl.h>
    #include <io.h>
    #include <sys/stat.h>
#else
    #include <fcntl.h>
    #include <sys/stat.h>
    #include <unistd.h>
#endif

namespace Stockfish::Data {
namespace {

std::string system_error_message(int error) {
    return std::generic_category().message(error ? error : EIO);
}

#ifdef _WIN32
std::string windows_error_message(unsigned long error) {
    return std::system_category().message(int(error));
}
#endif

}  // namespace

AtomicBinV2Sink::AtomicBinV2Sink(std::filesystem::path path) :
    outputPath(std::move(path)) {}

AtomicBinV2Sink::~AtomicBinV2Sink() {
    if (!finalized && !aborted)
    {
        int closeError  = 0;
        int removeError = 0;
        cleanup_partial(closeError, removeError);
    }
}

DataResult AtomicBinV2Sink::open_exclusively() {
    if (fileDescriptor != -1)
        return DataResult::success();

    errno = 0;
#ifdef _WIN32
    // CREATE_NEW is the Win32 O_EXCL equivalent. Keeping DELETE access on the
    // owned handle lets cleanup delete that exact file by handle, while the
    // sharing mask admits readers but explicitly excludes competing writers
    // and renames for the full append/finalize/hash transaction.
    const HANDLE handle =
      ::CreateFileW(outputPath.c_str(), GENERIC_READ | GENERIC_WRITE | DELETE, FILE_SHARE_READ,
                    nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle != INVALID_HANDLE_VALUE)
    {
        fileDescriptor =
          ::_open_osfhandle(reinterpret_cast<std::intptr_t>(handle), _O_BINARY | _O_RDWR);
        if (fileDescriptor == -1)
        {
            FILE_DISPOSITION_INFO disposition{TRUE};
            ::SetFileInformationByHandle(handle, FileDispositionInfo, &disposition,
                                         sizeof(disposition));
            ::CloseHandle(handle);
        }
    }
    else
    {
        const unsigned long nativeError = ::GetLastError();
        return DataResult::failure(
          nativeError == ERROR_FILE_EXISTS || nativeError == ERROR_ALREADY_EXISTS
            ? DataError::OUTPUT_EXISTS
            : DataError::OPEN_FAILED,
          "Cannot create Atomic BIN V2 output exclusively: " + windows_error_message(nativeError));
    }
#else
    int flags = O_RDWR | O_CREAT | O_EXCL;
    #ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
    #endif
    fileDescriptor = ::open(outputPath.c_str(), flags, 0666);
#endif
    if (fileDescriptor != -1)
    {
        created             = true;
        DataResult identity = capture_identity();
        if (identity)
            return identity;
        accepting = false;
        return identity;
    }

    const int error = errno;
    return DataResult::failure(error == EEXIST ? DataError::OUTPUT_EXISTS : DataError::OPEN_FAILED,
                               "Cannot create Atomic BIN V2 output exclusively: "
                                 + system_error_message(error));
}

DataResult AtomicBinV2Sink::capture_identity() {
    identityKnown = false;
#ifdef _WIN32
    const auto native = ::_get_osfhandle(fileDescriptor);
    if (native == -1)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot get Atomic BIN V2 native file handle");

    BY_HANDLE_FILE_INFORMATION information{};
    if (!::GetFileInformationByHandle(reinterpret_cast<HANDLE>(native), &information))
    {
        const unsigned long error = ::GetLastError();
        return DataResult::failure(DataError::OPEN_FAILED, "Cannot identify Atomic BIN V2 output: "
                                                             + windows_error_message(error));
    }
    identityDomain = u64(information.dwVolumeSerialNumber);
    identityFile   = (u64(information.nFileIndexHigh) << 32) | information.nFileIndexLow;
#else
    struct stat status{};
    errno = 0;
    if (::fstat(fileDescriptor, &status) != 0)
    {
        const int error = errno;
        return DataResult::failure(DataError::OPEN_FAILED, "Cannot identify Atomic BIN V2 output: "
                                                             + system_error_message(error));
    }
    identityDomain = u64(status.st_dev);
    identityFile   = u64(status.st_ino);
#endif
    identityKnown = true;
    return DataResult::success();
}

AtomicBinV2Sink::OwnedPathState AtomicBinV2Sink::inspect_owned_path(int& error) const noexcept {
    error = 0;
    if (!identityKnown)
    {
        error = EIO;
        return OwnedPathState::INSPECTION_ERROR;
    }

#ifdef _WIN32
    const HANDLE handle = ::CreateFileW(outputPath.c_str(), FILE_READ_ATTRIBUTES,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
    {
        const unsigned long nativeError = ::GetLastError();
        if (nativeError == ERROR_FILE_NOT_FOUND || nativeError == ERROR_PATH_NOT_FOUND)
            return OwnedPathState::MISSING;
        error = EACCES;
        return OwnedPathState::INSPECTION_ERROR;
    }

    BY_HANDLE_FILE_INFORMATION information{};
    const bool                 inspected = bool(::GetFileInformationByHandle(handle, &information));
    ::CloseHandle(handle);
    if (!inspected)
    {
        error = EACCES;
        return OwnedPathState::INSPECTION_ERROR;
    }

    const u64 domain = u64(information.dwVolumeSerialNumber);
    const u64 file   = (u64(information.nFileIndexHigh) << 32) | information.nFileIndexLow;
#else
    struct stat status{};
    errno = 0;
    if (::lstat(outputPath.c_str(), &status) != 0)
    {
        if (errno == ENOENT)
            return OwnedPathState::MISSING;
        error = errno;
        return OwnedPathState::INSPECTION_ERROR;
    }
    const u64 domain = u64(status.st_dev);
    const u64 file   = u64(status.st_ino);
#endif

    return domain == identityDomain && file == identityFile ? OwnedPathState::MATCHES
                                                            : OwnedPathState::DIFFERENT;
}

DataResult AtomicBinV2Sink::write_bytes(const u8* data, std::size_t size, const char* description) {
    std::size_t written = 0;
    while (written < size)
    {
        errno = 0;
#ifdef _WIN32
        const unsigned request = unsigned(
          std::min<std::size_t>(size - written, std::size_t(std::numeric_limits<unsigned>::max())));
        const int count = ::_write(fileDescriptor, data + written, request);
#else
        const ssize_t count = ::write(fileDescriptor, data + written, size - written);
#endif
        if (count > 0)
        {
            written += std::size_t(count);
            continue;
        }
        if (count < 0 && errno == EINTR)
            continue;

        const int error = count == 0 ? EIO : errno;
        return DataResult::failure(DataError::WRITE_FAILED,
                                   std::string("Cannot write Atomic BIN V2 ") + description + ": "
                                     + system_error_message(error));
    }
    return DataResult::success();
}

DataResult AtomicBinV2Sink::seek_to_start() {
    errno = 0;
#ifdef _WIN32
    if (::_lseeki64(fileDescriptor, 0, SEEK_SET) == 0)
#else
    if (::lseek(fileDescriptor, 0, SEEK_SET) == 0)
#endif
        return DataResult::success();

    const int error = errno;
    return DataResult::failure(DataError::WRITE_FAILED,
                               "Cannot seek Atomic BIN V2 output: " + system_error_message(error));
}

DataResult AtomicBinV2Sink::inspect_size(u64& size) const {
    errno = 0;
#ifdef _WIN32
    struct _stati64 status{};
    const int       result = ::_fstati64(fileDescriptor, &status);
#else
    struct stat status{};
    const int   result = ::fstat(fileDescriptor, &status);
#endif
    if (result != 0)
    {
        const int error = errno;
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Cannot inspect Atomic BIN V2 output size: "
                                     + system_error_message(error));
    }
    if (status.st_size < 0)
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Atomic BIN V2 output has a negative size");

    size = u64(status.st_size);
    return DataResult::success();
}

DataResult AtomicBinV2Sink::synchronize() {
    errno = 0;
#ifdef _WIN32
    const int result = ::_commit(fileDescriptor);
#else
    int result;
    do
    {
        result = ::fsync(fileDescriptor);
    } while (result != 0 && errno == EINTR);
#endif
    if (result == 0)
        return DataResult::success();

    const int error = errno;
    return DataResult::failure(DataError::WRITE_FAILED, "Cannot synchronize Atomic BIN V2 output: "
                                                          + system_error_message(error));
}

DataResult AtomicBinV2Sink::close_output() {
    if (fileDescriptor == -1)
        return DataResult::failure(DataError::CLOSE_FAILED,
                                   "Atomic BIN V2 output descriptor is already closed");

    const int descriptor = fileDescriptor;
    fileDescriptor       = -1;
    errno                = 0;
#ifdef _WIN32
    const int result = ::_close(descriptor);
#else
    const int result = ::close(descriptor);
#endif
    if (result == 0)
        return DataResult::success();

    const int error = errno;
    return DataResult::failure(DataError::CLOSE_FAILED,
                               "Cannot close Atomic BIN V2 output: " + system_error_message(error));
}

DataResult AtomicBinV2Sink::append(const TrainingDataSample& sample) {
    if (!accepting)
        return DataResult::failure(DataError::SINK_CLOSED,
                                   "Cannot write to a closed Atomic BIN V2 sink");
    if (recordsWritten >= AtomicBinV2MaxRecordCount)
    {
        accepting = false;
        return DataResult::failure(DataError::RECORD_COUNT_OUT_OF_RANGE,
                                   "Atomic BIN V2 record count exceeds the file-size domain");
    }

    // Encode first so an invalid first sample cannot leave even a provisional
    // header on disk.
    AtomicBinV2Record record{};
    if (DataResult encoded = encode_atomic_bin_v2(sample, record); !encoded)
        return encoded;

    if (!created)
    {
        if (DataResult opened = open_exclusively(); !opened)
            return opened;

        AtomicBinV2Header provisional{};
        if (DataResult encodedHeader = encode_atomic_bin_v2_header(0, provisional); !encodedHeader)
        {
            accepting = false;
            return encodedHeader;
        }
        if (DataResult written = write_bytes(provisional.data(), provisional.size(), "header");
            !written)
        {
            accepting = false;
            return written;
        }
    }

    if (DataResult written = write_bytes(record.data(), record.size(), "record"); !written)
    {
        accepting = false;
        return written;
    }

    ++recordsWritten;
    return DataResult::success();
}

DataResult AtomicBinV2Sink::finalize() {
    if (finalized)
        return DataResult::success();
    if (aborted || !accepting)
        return DataResult::failure(DataError::SINK_CLOSED,
                                   "Cannot finalize an aborted or failed Atomic BIN V2 sink");

    accepting = false;
    if (recordsWritten == 0)
        return DataResult::failure(DataError::EMPTY_DATASET,
                                   "Atomic BIN V2 forbids an empty dataset");
    if (!created || fileDescriptor == -1)
        return DataResult::failure(DataError::CLOSE_FAILED,
                                   "Atomic BIN V2 output disappeared before finalization");

    AtomicBinV2Header finalHeader{};
    if (DataResult encoded = encode_atomic_bin_v2_header(recordsWritten, finalHeader); !encoded)
        return encoded;
    if (DataResult seek = seek_to_start(); !seek)
        return seek;
    if (DataResult written = write_bytes(finalHeader.data(), finalHeader.size(), "final header");
        !written)
        return written;

    u64 size = 0;
    if (DataResult inspected = inspect_size(size); !inspected)
        return inspected;
    if (DataResult validSize = validate_atomic_bin_v2_file_size(recordsWritten, size); !validSize)
        return validSize;

    std::string checksum;
    if (DataResult hashed = sha256_file_descriptor(fileDescriptor, size, checksum); !hashed)
        return hashed;
    u64 sizeAfterHash = 0;
    if (DataResult inspected = inspect_size(sizeAfterHash); !inspected)
        return inspected;
    if (sizeAfterHash != size)
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Atomic BIN V2 output size changed while hashing");
    int                  identityError = 0;
    const OwnedPathState pathState     = inspect_owned_path(identityError);
    if (pathState == OwnedPathState::MISSING || pathState == OwnedPathState::DIFFERENT)
        return DataResult::failure(DataError::FILE_SIZE_MISMATCH,
                                   "Atomic BIN V2 output path changed while hashing");
    if (pathState == OwnedPathState::INSPECTION_ERROR)
        return DataResult::failure(DataError::OPEN_FAILED,
                                   "Cannot revalidate Atomic BIN V2 output identity: "
                                     + system_error_message(identityError));
    if (DataResult synced = synchronize(); !synced)
        return synced;
    if (DataResult closed = close_output(); !closed)
        return closed;

    finalizedSize   = size;
    finalizedSha256 = std::move(checksum);
    finalized       = true;
    return DataResult::success();
}

void AtomicBinV2Sink::cleanup_partial(int& closeError, int& removeError) noexcept {
    closeError  = 0;
    removeError = 0;
    accepting   = false;

#ifdef _WIN32
    if (created && fileDescriptor != -1)
    {
        const auto native = ::_get_osfhandle(fileDescriptor);
        if (native == -1)
            removeError = EACCES;
        else
        {
            FILE_DISPOSITION_INFO disposition{TRUE};
            if (!::SetFileInformationByHandle(reinterpret_cast<HANDLE>(native), FileDispositionInfo,
                                              &disposition, sizeof(disposition)))
                removeError = EACCES;
            else
            {
                created       = false;
                identityKnown = false;
            }
        }
    }
    else if (created)
        removeError = EACCES;
#else
    OwnedPathState pathState = OwnedPathState::MISSING;
    if (created)
    {
        pathState = inspect_owned_path(removeError);
        if (pathState == OwnedPathState::MISSING)
            created = false;
        else if (pathState == OwnedPathState::DIFFERENT)
            removeError = EACCES;
    }

    // Unlink while the owned descriptor is still open. The inode remains valid
    // until close, and the identity check above prevents ordinary replacement
    // of the output path from deleting an unrelated file. POSIX has no portable
    // unlink-if-inode primitive, so a directory writable by a hostile concurrent
    // process is outside this sink's threat model.
    if (created && pathState == OwnedPathState::MATCHES)
    {
        errno = 0;
        if (::unlink(outputPath.c_str()) != 0 && errno != ENOENT)
            removeError = errno;
        else
        {
            created       = false;
            identityKnown = false;
        }
    }
#endif

    if (fileDescriptor != -1)
    {
        const int descriptor = fileDescriptor;
        fileDescriptor       = -1;
        errno                = 0;
#ifdef _WIN32
        if (::_close(descriptor) != 0)
#else
        if (::close(descriptor) != 0)
#endif
            closeError = errno;
    }

    // A failed unlink remains retryable. An identity mismatch is deliberately
    // never removed: preserving an unexpected replacement is safer than
    // claiming cleanup success for a path the sink no longer owns.
    aborted = !created;
}

DataResult AtomicBinV2Sink::abort() {
    if (aborted)
        return DataResult::success();
    if (finalized)
        return DataResult::failure(DataError::SINK_CLOSED,
                                   "Cannot abort a finalized Atomic BIN V2 sink");

    int closeError  = 0;
    int removeError = 0;
    cleanup_partial(closeError, removeError);
    if (closeError || removeError)
        return DataResult::failure(
          DataError::ABORT_FAILED,
          "Cannot fully remove partial Atomic BIN V2 output: "
            + system_error_message(removeError ? removeError : closeError));

    return DataResult::success();
}

DataResult AtomicBinV2Sink::remove_finalized_owned() {
    if (!finalized)
        return DataResult::failure(DataError::SINK_CLOSED,
                                   "Cannot remove an unfinished Atomic BIN V2 output as finalized");
    if (!created)
        return DataResult::success();

#ifdef _WIN32
    const HANDLE handle = ::CreateFileW(outputPath.c_str(), FILE_READ_ATTRIBUTES | DELETE,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
    {
        const unsigned long error = ::GetLastError();
        if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND)
        {
            created       = false;
            identityKnown = false;
            return DataResult::success();
        }
        return DataResult::failure(DataError::ABORT_FAILED,
                                   "Cannot open finalized Atomic BIN V2 output for removal: "
                                     + windows_error_message(error));
    }

    BY_HANDLE_FILE_INFORMATION information{};
    if (!::GetFileInformationByHandle(handle, &information))
    {
        const unsigned long error = ::GetLastError();
        ::CloseHandle(handle);
        return DataResult::failure(DataError::ABORT_FAILED,
                                   "Cannot identify finalized Atomic BIN V2 output: "
                                     + windows_error_message(error));
    }
    const u64 domain = u64(information.dwVolumeSerialNumber);
    const u64 file   = (u64(information.nFileIndexHigh) << 32) | information.nFileIndexLow;
    if (domain != identityDomain || file != identityFile)
    {
        ::CloseHandle(handle);
        return DataResult::failure(
          DataError::ABORT_FAILED,
          "Cannot remove finalized Atomic BIN V2 output because its path was replaced");
    }

    FILE_DISPOSITION_INFO disposition{TRUE};
    if (!::SetFileInformationByHandle(handle, FileDispositionInfo, &disposition,
                                      sizeof(disposition)))
    {
        const unsigned long error = ::GetLastError();
        ::CloseHandle(handle);
        return DataResult::failure(DataError::ABORT_FAILED,
                                   "Cannot remove finalized Atomic BIN V2 output: "
                                     + windows_error_message(error));
    }
    if (!::CloseHandle(handle))
    {
        const unsigned long error = ::GetLastError();
        return DataResult::failure(DataError::ABORT_FAILED,
                                   "Cannot close finalized Atomic BIN V2 delete handle: "
                                     + windows_error_message(error));
    }

    created       = false;
    identityKnown = false;
    return DataResult::success();
#else
    int                  identityError = 0;
    const OwnedPathState pathState     = inspect_owned_path(identityError);
    if (pathState == OwnedPathState::MISSING)
    {
        created       = false;
        identityKnown = false;
        return DataResult::success();
    }
    if (pathState == OwnedPathState::DIFFERENT)
        return DataResult::failure(
          DataError::ABORT_FAILED,
          "Cannot remove finalized Atomic BIN V2 output because its path was replaced");
    if (pathState == OwnedPathState::INSPECTION_ERROR)
        return DataResult::failure(DataError::ABORT_FAILED,
                                   "Cannot identify finalized Atomic BIN V2 output: "
                                     + system_error_message(identityError));

    errno = 0;
    #ifdef _WIN32
    const int result = ::_wunlink(outputPath.c_str());
    #else
    const int result = ::unlink(outputPath.c_str());
    #endif
    if (result != 0 && errno != ENOENT)
    {
        const int error = errno;
        return DataResult::failure(DataError::ABORT_FAILED,
                                   "Cannot remove finalized Atomic BIN V2 output: "
                                     + system_error_message(error));
    }

    created       = false;
    identityKnown = false;
    return DataResult::success();
#endif
}

}  // namespace Stockfish::Data
