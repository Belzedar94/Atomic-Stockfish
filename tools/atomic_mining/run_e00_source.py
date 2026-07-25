"""Execute the fixed Atomic E00-SRC paired source battery.

The result-bearing wire in this module is intentionally independent from the
historical ``variantfishtest_new1.py`` text log.  The historical runner may be
bound as an authenticated input, but neither its append-only log nor its
aggregate counters are ever consulted as scientific authority.

The public :func:`run_battery` entry point consumes a schedule that has first
been authenticated by :mod:`tools.atomic_mining.build_e00_schedule`.  Every
pair is executed serially, staged under an isolated temporary identity, and
accepted only when both colour-swapped legs validate.  ``receipt.json`` is the
sole commit marker; its absence means that the battery is not consumable.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import wraps
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Mapping, Protocol, Sequence

from . import (
    atomic_outcome_helper,
    build_atomic_outcome_binding,
    build_e00_schedule,
    common,
    owned_process,
    uci_session,
)


GAME_SCHEMA = "atomic-e00-game-v2"
PAIR_SCHEMA = "atomic-e00-pair-v1"
INVENTORY_SCHEMA = "atomic-e00-inventory-v1"
RECEIPT_SCHEMA = "atomic-e00-execution-receipt-v2"
REJECTION_SCHEMA = "atomic-e00-rejection-v1"
TRAJECTORY_SCHEMA = "atomic-e00-trajectory-v1"
SOURCE_GAME_SCHEMA = "atomic-e00-source-game-v1"
RUNTIME_MANIFEST_SCHEMA = "atomic-e00-runtime-manifest-v2"
ENGINE_EVIDENCE_SCHEMA = "atomic-e00-engine-evidence-v2"
REFEREE_EVIDENCE_SCHEMA = "atomic-e00-native-referee-evidence-v2"
VERIFIER_EVIDENCE_SCHEMA = "atomic-e00-native-verifier-evidence-v2"
SCHEDULE_SCHEMA = "atomic-e00-schedule-v1"
SCHEDULE_RECEIPT_SCHEMA = "atomic-e00-schedule-receipt-v1"
INTERNAL_LEG_REQUEST_SCHEMA = "atomic-e00-internal-leg-request-v2"
INTERNAL_LEG_RESULT_SCHEMA = "atomic-e00-internal-leg-result-v2"
INTERNAL_VERIFY_REQUEST_SCHEMA = "atomic-e00-internal-verify-request-v2"
INTERNAL_VERIFY_RESULT_SCHEMA = "atomic-e00-internal-verify-result-v2"
INTERNAL_RUNTIME_DISCOVERY_REQUEST_SCHEMA = (
    "atomic-e00-internal-runtime-discovery-request-v2"
)
INTERNAL_RUNTIME_DISCOVERY_RESULT_SCHEMA = (
    "atomic-e00-internal-runtime-discovery-result-v2"
)
RUNTIME_DISCOVERY_RECEIPT_SCHEMA = (
    "atomic-e00-runtime-discovery-receipt-v2"
)

RESULTS_WHITE = ("1-0", "0-1", "1/2-1/2")
NETWORK_ROLES = ("current-v3", "run3b")
NETWORK_BACKENDS = {
    "current-v3": "AtomicNNUEV3",
    "run3b": "Legacy Atomic V1",
}
_UCI_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][nbrq]?$")
_CLEAN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class E00RunnerError(RuntimeError):
    """The E00 battery failed a scientific or operational precondition."""


_ACTIVE_ARTIFACT_GUARDS: ContextVar[
    list["_ImmutableFileGuard"] | None
] = ContextVar("atomic_e00_active_artifact_guards", default=None)
_ACTIVE_DIRECTORY_GUARDS: ContextVar[
    list["_ImmutableDirectoryGuard"] | None
] = ContextVar("atomic_e00_active_directory_guards", default=None)
_ACTIVE_WINDOWS_DIRECTORY_LOCKS: ContextVar[
    dict[str, "_SharedWindowsDirectoryLock"] | None
] = ContextVar("atomic_e00_active_windows_directory_locks", default=None)


if os.name == "nt":  # pragma: no branch - production platform
    import ctypes
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _DELETE_ACCESS = 0x00010000
    _FILE_READ_ATTRIBUTES = 0x00000080
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_DELETE = 0x00000004
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _guard_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _guard_kernel32.GetFileAttributesW.argtypes = (wintypes.LPCWSTR,)
    _guard_kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _guard_kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _guard_kernel32.CreateFileW.restype = wintypes.HANDLE
    _guard_kernel32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    )
    _guard_kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _guard_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _guard_kernel32.CloseHandle.restype = wintypes.BOOL


def _windows_filetime(value: object) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _windows_handle_identity(
    handle: object, *, label: str
) -> dict[str, object]:
    information = _BY_HANDLE_FILE_INFORMATION()
    if not _guard_kernel32.GetFileInformationByHandle(
        handle, ctypes.byref(information)
    ):
        raise E00RunnerError(
            f"cannot read immutable identity for {label}: "
            f"{ctypes.get_last_error()}"
        )
    return {
        "schema": "atomic-e00-windows-file-identity-v1",
        "volume_serial_number": int(information.dwVolumeSerialNumber),
        "file_index": (
            int(information.nFileIndexHigh) << 32
        )
        | int(information.nFileIndexLow),
        "number_of_links": int(information.nNumberOfLinks),
        "size_bytes": (
            int(information.nFileSizeHigh) << 32
        )
        | int(information.nFileSizeLow),
        "creation_time_100ns": _windows_filetime(
            information.ftCreationTime
        ),
        "last_write_time_100ns": _windows_filetime(
            information.ftLastWriteTime
        ),
    }


def _windows_directory_identity(
    handle: object, *, label: str
) -> dict[str, object]:
    identity = _windows_handle_identity(handle, label=label)
    return {
        "schema": "atomic-e00-windows-directory-identity-v1",
        "volume_serial_number": identity["volume_serial_number"],
        "file_index": identity["file_index"],
    }


def _open_windows_directory(
    path: Path,
    *,
    label: str,
    lock_namespace: bool = True,
    strict_entries: bool = False,
) -> object:
    attributes = _guard_kernel32.GetFileAttributesW(str(path))
    if (
        attributes == _INVALID_FILE_ATTRIBUTES
        or not attributes & _FILE_ATTRIBUTE_DIRECTORY
        or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise E00RunnerError(f"{label} is not a plain directory")
    handle = _guard_kernel32.CreateFileW(
        str(path),
        (
            _DELETE_ACCESS
            if strict_entries
            else _FILE_READ_ATTRIBUTES
            if lock_namespace
            else 0
        ),
        (
            _FILE_SHARE_READ
            if lock_namespace
            else _FILE_SHARE_READ | _FILE_SHARE_DELETE
        ),
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise E00RunnerError(
            f"cannot acquire namespace lock for {label}: "
            f"{ctypes.get_last_error()}"
        )
    return handle


class _SharedWindowsDirectoryLock:
    """One ref-counted no-delete-share handle per canonical directory path."""

    def __init__(
        self,
        path: Path,
        *,
        label: str,
        registry: dict[str, "_SharedWindowsDirectoryLock"] | None,
        key: str,
    ) -> None:
        self.path = path.absolute()
        self.label = label
        self._registry = registry
        self._key = key
        self._closed = False
        self._references = 1
        self.handle = _open_windows_directory(
            self.path, label=self.label
        )
        try:
            self.identity = _windows_directory_identity(
                self.handle, label=self.label
            )
        except BaseException:
            _guard_kernel32.CloseHandle(self.handle)
            self._closed = True
            raise

    def acquire(self, *, label: str) -> "_SharedWindowsDirectoryLock":
        if self._closed or self._references < 1:
            raise E00RunnerError(
                f"shared namespace lock for {label} is already closed"
            )
        if (
            _windows_directory_identity(self.handle, label=label)
            != self.identity
        ):
            raise E00RunnerError(
                f"shared namespace identity for {label} changed"
            )
        self._references += 1
        return self

    def release(self) -> None:
        if self._closed or self._references < 1:
            return
        self._references -= 1
        if self._references:
            return
        failure = not _guard_kernel32.CloseHandle(self.handle)
        self._closed = True
        if (
            self._registry is not None
            and self._registry.get(self._key) is self
        ):
            del self._registry[self._key]
        if failure:
            raise E00RunnerError(
                f"cannot release shared namespace lock for {self.label}"
            )


def _acquire_windows_directory_lock(
    path: Path, *, label: str
) -> _SharedWindowsDirectoryLock:
    requested = path.absolute()
    key = os.path.normcase(str(requested))
    registry = _ACTIVE_WINDOWS_DIRECTORY_LOCKS.get()
    if registry is not None:
        existing = registry.get(key)
        if existing is not None:
            return existing.acquire(label=label)
    lock = _SharedWindowsDirectoryLock(
        requested,
        label=label,
        registry=registry,
        key=key,
    )
    if registry is not None:
        registry[key] = lock
    return lock


class _ImmutableFileGuard:
    """Hold a no-write/no-delete identity lock for one executed artifact."""

    def __init__(self, path: Path, *, label: str) -> None:
        self.path = path.absolute()
        self.label = label
        self._handle: object | None = None
        self._parent_handle: object | None = None
        self._parent_lock: _SharedWindowsDirectoryLock | None = None
        self._parent_identity: Mapping[str, object] | None = None
        self._parent_lock_delegated = False
        self._descriptor: int | None = None
        self._parent_descriptor: int | None = None
        self._closed = False
        if os.name == "nt":
            self._open_windows()
        else:  # pragma: no cover - production is Windows
            self._open_posix()
        self.identity = self._read_identity()

    def _open_windows(self) -> None:
        assert os.name == "nt"
        self._validate_windows_path()
        self._parent_lock = _acquire_windows_directory_lock(
            self.path.parent, label=f"{self.label} parent directory"
        )
        self._parent_handle = self._parent_lock.handle
        try:
            self._parent_identity = _windows_directory_identity(
                self._parent_handle,
                label=f"{self.label} parent directory",
            )
            self._handle = self._open_windows_path()
        except BaseException:
            self._parent_lock.release()
            self._parent_lock = None
            self._parent_handle = None
            raise

    def _validate_windows_path(self) -> None:
        candidates = (self.path, *self.path.parents)
        for candidate in candidates:
            attributes = _guard_kernel32.GetFileAttributesW(str(candidate))
            if attributes == _INVALID_FILE_ATTRIBUTES:
                raise E00RunnerError(
                    f"cannot authenticate path component for {self.label}"
                )
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise E00RunnerError(
                    f"{self.label} path contains a reparse point"
                )

    def _open_windows_path(self) -> object:
        handle = _guard_kernel32.CreateFileW(
            str(self.path),
            _GENERIC_READ,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            raise E00RunnerError(
                f"cannot acquire immutable lock for {self.label}: "
                f"{ctypes.get_last_error()}"
            )
        return handle

    def _open_posix(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError as error:
            raise E00RunnerError(
                f"cannot open immutable artifact {self.label}"
            ) from error
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            os.close(descriptor)
            raise E00RunnerError(f"{self.label} is not a regular file")
        self._descriptor = descriptor
        parent_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            self._parent_descriptor = os.open(self.path.parent, parent_flags)
        except OSError as error:
            os.close(descriptor)
            self._descriptor = None
            raise E00RunnerError(
                f"cannot lock parent directory for {self.label}"
            ) from error
        parent_status = os.fstat(self._parent_descriptor)
        self._parent_identity = {
            "schema": "atomic-e00-posix-directory-identity-v1",
            "device": int(parent_status.st_dev),
            "inode": int(parent_status.st_ino),
            "ctime_ns": int(parent_status.st_ctime_ns),
            "mtime_ns": int(parent_status.st_mtime_ns),
        }

    def _windows_identity(self, handle: object) -> dict[str, object]:
        return _windows_handle_identity(handle, label=self.label)

    def _read_identity(self) -> dict[str, object]:
        if os.name == "nt":
            return self._windows_identity(self._handle)
        assert self._descriptor is not None
        status = os.fstat(self._descriptor)
        return {
            "schema": "atomic-e00-posix-file-identity-v1",
            "device": int(status.st_dev),
            "inode": int(status.st_ino),
            "size_bytes": int(status.st_size),
            "ctime_ns": int(status.st_ctime_ns),
            "mtime_ns": int(status.st_mtime_ns),
        }

    def verify(self) -> None:
        if self._closed or self._read_identity() != self.identity:
            raise E00RunnerError(
                f"{self.label} immutable identity changed"
            )
        if os.name == "nt":
            if not self._parent_lock_delegated:
                if (
                    _windows_directory_identity(
                        self._parent_handle,
                        label=f"{self.label} parent directory",
                    )
                    != self._parent_identity
                ):
                    raise E00RunnerError(
                        f"{self.label} parent directory identity changed"
                    )
            self._validate_windows_path()
            parent_probe = _open_windows_directory(
                self.path.parent,
                label=f"{self.label} parent directory",
                lock_namespace=False,
            )
            try:
                if (
                    _windows_directory_identity(
                        parent_probe,
                        label=f"{self.label} parent directory",
                    )
                    != self._parent_identity
                ):
                    raise E00RunnerError(
                        f"{self.label} parent directory path changed"
                    )
            finally:
                if not _guard_kernel32.CloseHandle(parent_probe):
                    raise E00RunnerError(
                        f"cannot close parent probe for {self.label}"
                    )
            path_handle = self._open_windows_path()
            close_failed = False
            try:
                if self._windows_identity(path_handle) != self.identity:
                    raise E00RunnerError(
                        f"{self.label} path identity changed"
                    )
            finally:
                if not _guard_kernel32.CloseHandle(path_handle):
                    close_failed = True
            if close_failed:
                raise E00RunnerError(
                    f"cannot close path identity probe for {self.label}"
                )
        elif not self.path.is_file() or self.path.is_symlink():
            raise E00RunnerError(f"{self.label} path identity changed")
        elif self._parent_descriptor is None:
            raise E00RunnerError(
                f"{self.label} parent directory lock is absent"
            )
        elif (
            int(os.fstat(self._parent_descriptor).st_ino)
            != self._parent_identity["inode"]
            or int(os.fstat(self._parent_descriptor).st_dev)
            != self._parent_identity["device"]
        ):
            raise E00RunnerError(
                f"{self.label} parent directory identity changed"
            )

    def delegate_parent_namespace_lock(self) -> None:
        """Hand parent immutability to a strict enclosing namespace seal."""

        if os.name != "nt":  # pragma: no cover - production is Windows
            return
        if self._closed:
            raise E00RunnerError(
                f"cannot delegate closed artifact lock for {self.label}"
            )
        if self._parent_lock_delegated:
            return
        self.verify()
        if self._parent_lock is None:
            raise E00RunnerError(
                f"parent namespace lock is absent for {self.label}"
            )
        self._parent_lock.release()
        self._parent_lock = None
        self._parent_handle = None
        self._parent_lock_delegated = True

    def close(self) -> None:
        if self._closed:
            return
        failure = False
        if os.name == "nt":
            if self._handle is not None and not _guard_kernel32.CloseHandle(
                self._handle
            ):
                failure = True
            if (
                self._parent_lock is not None
            ):
                try:
                    self._parent_lock.release()
                except BaseException:
                    failure = True
                self._parent_lock = None
        elif self._descriptor is not None:  # pragma: no cover
            try:
                os.close(self._descriptor)
            except OSError:
                failure = True
            if self._parent_descriptor is not None:
                try:
                    os.close(self._parent_descriptor)
                except OSError:
                    failure = True
        self._closed = True
        if failure:
            raise E00RunnerError(
                f"cannot release immutable lock for {self.label}"
            )

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass


class _ImmutableDirectoryGuard:
    """Hold no-delete-share handles for one namespace and its parent."""

    def __init__(
        self,
        path: Path,
        *,
        label: str,
        strict_static: bool = False,
    ) -> None:
        self.path = path.absolute()
        self.label = label
        self._strict_static = strict_static
        self._strict_handles: list[
            tuple[Path, object, Mapping[str, object]]
        ] = []
        self._strict_inventory: list[dict[str, object]] | None = None
        self._handle: object | None = None
        self._parent_handle: object | None = None
        self._target_lock: _SharedWindowsDirectoryLock | None = None
        self._parent_lock: _SharedWindowsDirectoryLock | None = None
        self._descriptor: int | None = None
        self._parent_descriptor: int | None = None
        self._closed = False
        if os.name == "nt":
            if self._strict_static:
                self._open_windows_strict()
                return
            self._target_lock = _acquire_windows_directory_lock(
                self.path, label=self.label
            )
            self._handle = self._target_lock.handle
            try:
                self._parent_lock = _acquire_windows_directory_lock(
                    self.path.parent,
                    label=f"{self.label} parent directory",
                )
                self._parent_handle = self._parent_lock.handle
                self.identity = _windows_directory_identity(
                    self._handle, label=self.label
                )
                self.parent_identity = _windows_directory_identity(
                    self._parent_handle,
                    label=f"{self.label} parent directory",
                )
            except BaseException:
                if self._parent_lock is not None:
                    self._parent_lock.release()
                    self._parent_lock = None
                self._target_lock.release()
                self._target_lock = None
                raise
        else:  # pragma: no cover - production is Windows
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                self._descriptor = os.open(self.path, flags)
                self._parent_descriptor = os.open(self.path.parent, flags)
            except OSError as error:
                if self._descriptor is not None:
                    os.close(self._descriptor)
                raise E00RunnerError(
                    f"cannot acquire namespace lock for {self.label}"
                ) from error
            self.identity = self._posix_identity(self._descriptor)
            self.parent_identity = self._posix_identity(
                self._parent_descriptor
            )

    def _open_windows_strict(self) -> None:
        inventory = _directory_inventory(self.path)
        directories = [self.path] + [
            (self.path / str(row["path"])).absolute()
            for row in inventory
            if row["kind"] == "directory"
        ]
        parent_probe = _open_windows_directory(
            self.path.parent,
            label=f"{self.label} parent directory",
            lock_namespace=False,
        )
        try:
            self.parent_identity = _windows_directory_identity(
                parent_probe,
                label=f"{self.label} parent directory",
            )
        finally:
            if not _guard_kernel32.CloseHandle(parent_probe):
                raise E00RunnerError(
                    f"cannot close strict parent probe for {self.label}"
                )
        try:
            for directory in sorted(
                directories,
                key=lambda value: len(value.parts),
                reverse=True,
            ):
                handle = _open_windows_directory(
                    directory,
                    label=f"{self.label} sealed directory",
                    strict_entries=True,
                )
                identity = _windows_directory_identity(
                    handle, label=f"{self.label} sealed directory"
                )
                self._strict_handles.append(
                    (directory, handle, identity)
                )
            root_rows = [
                row
                for path, _handle, row in self._strict_handles
                if path == self.path
            ]
            if len(root_rows) != 1:
                raise E00RunnerError(
                    f"strict namespace root is absent for {self.label}"
                )
            self.identity = dict(root_rows[0])
            self._strict_inventory = inventory
            if _directory_inventory(self.path) != inventory:
                raise E00RunnerError(
                    f"strict namespace changed while sealing {self.label}"
                )
        except BaseException:
            failure = False
            for _path, handle, _identity in reversed(
                self._strict_handles
            ):
                if not _guard_kernel32.CloseHandle(handle):
                    failure = True
            self._strict_handles.clear()
            if failure:
                raise E00RunnerError(
                    f"strict namespace rollback failed for {self.label}"
                )
            raise

    @staticmethod
    def _posix_identity(descriptor: int) -> dict[str, object]:
        status = os.fstat(descriptor)
        return {
            "schema": "atomic-e00-posix-directory-identity-v1",
            "device": int(status.st_dev),
            "inode": int(status.st_ino),
        }

    def verify(self) -> None:
        if self._closed:
            raise E00RunnerError(f"{self.label} namespace lock is closed")
        if os.name == "nt":
            if self._strict_static:
                self._verify_windows_strict()
                return
            if (
                _windows_directory_identity(self._handle, label=self.label)
                != self.identity
                or _windows_directory_identity(
                    self._parent_handle,
                    label=f"{self.label} parent directory",
                )
                != self.parent_identity
            ):
                raise E00RunnerError(
                    f"{self.label} namespace identity changed"
                )
            target_probe = _open_windows_directory(
                self.path, label=self.label, lock_namespace=False
            )
            parent_probe = _open_windows_directory(
                self.path.parent,
                label=f"{self.label} parent directory",
                lock_namespace=False,
            )
            try:
                if (
                    _windows_directory_identity(
                        target_probe, label=self.label
                    )
                    != self.identity
                    or _windows_directory_identity(
                        parent_probe,
                        label=f"{self.label} parent directory",
                    )
                    != self.parent_identity
                ):
                    raise E00RunnerError(
                        f"{self.label} namespace path changed"
                    )
            finally:
                close_failed = (
                    not _guard_kernel32.CloseHandle(target_probe)
                )
                close_failed = (
                    not _guard_kernel32.CloseHandle(parent_probe)
                ) or close_failed
                if close_failed:
                    raise E00RunnerError(
                        f"cannot close namespace probe for {self.label}"
                    )
        else:  # pragma: no cover
            assert self._descriptor is not None
            assert self._parent_descriptor is not None
            if (
                self._posix_identity(self._descriptor) != self.identity
                or self._posix_identity(self._parent_descriptor)
                != self.parent_identity
            ):
                raise E00RunnerError(
                    f"{self.label} namespace identity changed"
                )

    def _verify_windows_strict(self) -> None:
        if self._strict_inventory is None or not self._strict_handles:
            raise E00RunnerError(
                f"strict namespace seal is absent for {self.label}"
            )
        for path, handle, identity in self._strict_handles:
            if (
                _windows_directory_identity(
                    handle, label=f"{self.label} sealed directory"
                )
                != identity
            ):
                raise E00RunnerError(
                    f"strict namespace identity changed for {self.label}"
                )
            probe = _open_windows_directory(
                path,
                label=f"{self.label} sealed directory",
                lock_namespace=False,
            )
            try:
                if (
                    _windows_directory_identity(
                        probe, label=f"{self.label} sealed directory"
                    )
                    != identity
                ):
                    raise E00RunnerError(
                        f"strict namespace path changed for {self.label}"
                    )
            finally:
                if not _guard_kernel32.CloseHandle(probe):
                    raise E00RunnerError(
                        f"strict namespace probe close failed for {self.label}"
                    )
        parent_probe = _open_windows_directory(
            self.path.parent,
            label=f"{self.label} parent directory",
            lock_namespace=False,
        )
        try:
            if (
                _windows_directory_identity(
                    parent_probe,
                    label=f"{self.label} parent directory",
                )
                != self.parent_identity
            ):
                raise E00RunnerError(
                    f"strict namespace parent changed for {self.label}"
                )
        finally:
            if not _guard_kernel32.CloseHandle(parent_probe):
                raise E00RunnerError(
                    f"strict parent probe close failed for {self.label}"
                )
        if _directory_inventory(self.path) != self._strict_inventory:
            raise E00RunnerError(
                f"strict namespace enumeration changed for {self.label}"
            )

    def close(self) -> None:
        if self._closed:
            return
        failure = False
        if os.name == "nt":
            if self._strict_static:
                for _path, handle, _identity in reversed(
                    self._strict_handles
                ):
                    if not _guard_kernel32.CloseHandle(handle):
                        failure = True
                self._strict_handles.clear()
                self._closed = True
                if failure:
                    raise E00RunnerError(
                        f"cannot release strict namespace for {self.label}"
                    )
                return
            for lock_name in ("_parent_lock", "_target_lock"):
                lock = getattr(self, lock_name)
                if lock is None:
                    continue
                try:
                    lock.release()
                except BaseException:
                    failure = True
                setattr(self, lock_name, None)
        else:  # pragma: no cover
            for descriptor in (
                self._descriptor,
                self._parent_descriptor,
            ):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        failure = True
        self._closed = True
        if failure:
            raise E00RunnerError(
                f"cannot release namespace lock for {self.label}"
            )

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass


@dataclass(frozen=True)
class ArtifactBinding:
    """A regular input file authenticated before and after execution."""

    label: str
    path: Path
    sha256: str
    size_bytes: int
    identity: Mapping[str, object]
    _guard: _ImmutableFileGuard = field(
        repr=False, compare=False
    )

    def close(self) -> None:
        self._guard.close()


@dataclass(frozen=True)
class DirectoryBinding:
    label: str
    path: Path
    identity: Mapping[str, object]
    parent_identity: Mapping[str, object]
    _guard: _ImmutableDirectoryGuard = field(
        repr=False, compare=False
    )

    def close(self) -> None:
        self._guard.close()


@dataclass(frozen=True)
class SchedulePair:
    """The exact schedule fields required by the result-bearing runner."""

    experiment_id: str
    battery_id: str
    seed: str
    pair_id: str
    pair_ordinal: int
    stratum: str
    time_control: Mapping[str, int | str]
    book_sha256: str
    book_line: int
    root_fen: str
    root_fen_sha256: str


@dataclass(frozen=True)
class ValidatedSchedule:
    """Authenticated schedule snapshot returned by the schedule validator."""

    pairs: tuple[SchedulePair, ...]
    schedule_sha256: str
    schedule_size_bytes: int
    receipt_sha256: str
    receipt_size_bytes: int


@dataclass(frozen=True)
class GameExecution:
    """Structured result returned by an injected game backend."""

    result_white: str
    time_loss: bool
    terminal_reason: str
    moves: tuple[str, ...]
    stdout: bytes
    stderr: bytes
    engine_evidence: Mapping[str, object]
    referee_evidence: Mapping[str, object]
    process_evidence: Mapping[str, object]
    verifier_evidence: Mapping[str, object]


@dataclass(frozen=True)
class LegRequest:
    """Everything a backend needs to execute one frozen schedule leg."""

    pair: SchedulePair
    leg: int
    white_network_role: str
    black_network_role: str
    engine: Path
    engine_sha256: str
    current_net: Path
    current_net_sha256: str
    teacher_net: Path
    teacher_net_sha256: str
    book: Path
    book_sha256: str
    variant_config: Path
    variant_config_sha256: str
    pyffish: Path
    pyffish_sha256: str
    pyffish_manifest_binding_path: Path
    pyffish_build_manifest: Path
    pyffish_build_manifest_sha256: str
    source_root: Path
    source_commit: str
    helper: Path
    helper_sha256: str
    builder: Path
    builder_sha256: str
    uci_module: Path
    uci_module_sha256: str
    owned_process_module: Path
    owned_process_module_sha256: str
    runtime_package_root: Path
    child_import_inventory: tuple[Mapping[str, object], ...]
    uci_options: tuple[tuple[str, object], ...]
    maximum_plies: int
    command_timeout_seconds: float
    leg_deadline_seconds: float


@dataclass(frozen=True)
class RuntimeDiscoveryRequest:
    """Non-scientific child request for exact runtime import discovery."""

    runtime_package_root: Path
    runtime_modules: tuple[Mapping[str, object], ...]
    pyffish: Path
    pyffish_sha256: str
    pyffish_manifest_binding_path: Path
    pyffish_build_manifest: Path
    pyffish_build_manifest_sha256: str
    source_root: Path
    source_commit: str


@dataclass(frozen=True)
class BatterySummary:
    pairs: int
    games: int
    wins_current: int
    losses_current: int
    draws: int
    time_losses: int
    games_sha256: str
    receipt_sha256: str


@dataclass(frozen=True)
class _PreparedBattery:
    """A fully reconciled battery whose guards have not yet been released."""

    receipt_path: Path
    receipt_payload: bytes
    summary: BatterySummary


class GameBackend(Protocol):
    """Injectable boundary used by unit tests and the real UCI backend."""

    def play(self, request: LegRequest) -> GameExecution:
        """Play exactly one leg or raise; retries are forbidden."""


class SemanticVerifier(Protocol):
    """Independent native replay boundary used before pair acceptance."""

    def verify(
        self, request: LegRequest, execution: GameExecution
    ) -> Mapping[str, object]:
        """Return sealed verifier evidence or raise."""


class RuntimeProbe(Protocol):
    """Capture exact imported/runtime/source identities."""

    def capture(self) -> Mapping[str, object]:
        """Return the canonical runtime contract observed now."""


def _require_clean_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _CLEAN_ID.fullmatch(value) is None:
        raise E00RunnerError(f"{label} is not a clean bounded identifier")
    return value


def _require_int(
    value: object, *, label: str, minimum: int = 0
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise E00RunnerError(f"{label} must be an integer >= {minimum}")
    return value


def _require_finite_positive(value: object, *, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value <= 0
        or value != value
        or value in {float("inf"), float("-inf")}
    ):
        raise E00RunnerError(f"{label} must be finite and positive")
    return float(value)


def _time_control(value: object, *, label: str) -> dict[str, int | str]:
    if not isinstance(value, Mapping):
        raise E00RunnerError(f"{label} must be an object")
    common.require_exact_fields(
        value,
        ("base_ms", "increment_ms", "name"),
        label=label,
    )
    base = _require_int(value["base_ms"], label=f"{label}.base_ms", minimum=1)
    increment = _require_int(
        value["increment_ms"], label=f"{label}.increment_ms", minimum=0
    )
    rendered = _require_clean_id(value["name"], label=f"{label}.name")
    return {"base_ms": base, "increment_ms": increment, "name": rendered}


def _digest_document(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def _load_canonical_json(path: Path, *, label: str) -> tuple[dict[str, object], bytes]:
    payload = common.read_stable_file_bytes(path, label=label)
    if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise E00RunnerError(f"{label} is not canonical UTF-8 JSON")
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise E00RunnerError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise E00RunnerError(f"{label} must contain one JSON object")
    if common.canonical_json_bytes(value) != payload:
        raise E00RunnerError(f"{label} is not canonical JSON")
    return value, payload


def _package_tree(path: Path) -> dict[str, object]:
    root = path.absolute()
    entries: list[dict[str, object]] = []
    for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = candidate.relative_to(root)
        if "__pycache__" in relative.parts or candidate.suffix == ".pyc":
            continue
        if candidate.is_symlink():
            raise E00RunnerError("runtime package tree contains a symlink")
        if not candidate.is_file():
            continue
        payload = common.read_stable_file_bytes(
            candidate, label="runtime package file"
        )
        entries.append(
            {
                "path": relative.as_posix(),
                "sha256": common.sha256_bytes(payload),
                "size_bytes": len(payload),
            }
        )
    if not entries:
        raise E00RunnerError("runtime package tree is empty")
    digest = _digest_document("atomic-e00-package-tree-v1", entries)
    return {
        "root": str(root),
        "sha256": digest,
        "file_count": len(entries),
    }


def _module_identity(module: object, *, label: str) -> dict[str, object]:
    raw_path = getattr(module, "__file__", None)
    if not isinstance(raw_path, str):
        raise E00RunnerError(f"{label} module has no filesystem source")
    path = Path(raw_path).absolute()
    payload = common.read_stable_file_bytes(path, label=f"{label} module")
    return {
        "path": str(path),
        "sha256": common.sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _git_output(root_hint: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root_hint), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30.0,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        UnicodeError,
    ) as error:
        raise E00RunnerError(
            f"cannot authenticate source with git {' '.join(arguments)}"
        ) from error
    return completed.stdout.strip()


def _validate_child_import_inventory(
    value: object,
    *,
    label: str,
) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise E00RunnerError(f"{label} must be a non-empty list")
    normalized: list[dict[str, object]] = []
    prior_key: tuple[str, str] | None = None
    seen_modules: set[str] = set()
    for ordinal, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise E00RunnerError(f"{label} entry must be an object")
        common.require_exact_fields(
            raw,
            ("source", "path", "sha256", "size_bytes", "module_names"),
            label=f"{label} entry {ordinal}",
        )
        source = raw["source"]
        path = raw["path"]
        modules = raw["module_names"]
        if (
            source
            not in {"python-installation", "runtime-package", "pyffish"}
            or not isinstance(path, str)
            or not path
            or type(raw["size_bytes"]) is not int
            or raw["size_bytes"] < 0
            or not isinstance(modules, list)
            or not modules
            or not all(
                isinstance(name, str) and name and "\x00" not in name
                for name in modules
            )
            or modules != sorted(set(modules))
        ):
            raise E00RunnerError(f"{label} entry is malformed")
        if source == "python-installation":
            candidate = Path(path)
            if not candidate.is_absolute():
                raise E00RunnerError(
                    f"{label} Python-installation path is not absolute"
                )
        elif source == "runtime-package":
            candidate = Path(path)
            if (
                candidate.is_absolute()
                or ".." in candidate.parts
                or candidate.as_posix() != path
            ):
                raise E00RunnerError(
                    f"{label} runtime-package path is not canonical"
                )
        elif path != "pyffish":
            raise E00RunnerError(f"{label} pyffish path token differs")
        key = (str(source), path)
        if prior_key is not None and key <= prior_key:
            raise E00RunnerError(
                f"{label} entries are duplicated or not sorted"
            )
        prior_key = key
        overlap = seen_modules.intersection(modules)
        if overlap:
            raise E00RunnerError(
                f"{label} duplicates module names: {sorted(overlap)}"
            )
        seen_modules.update(modules)
        normalized.append(
            {
                "source": source,
                "path": path,
                "sha256": common.require_lower_hex_sha256(
                    raw["sha256"],
                    label=f"{label} entry SHA-256",
                ),
                "size_bytes": raw["size_bytes"],
                "module_names": list(modules),
            }
        )
    return normalized


def _file_backed_module_inventory(
    *,
    runtime_package_root: Path,
    pyffish_path: Path,
) -> list[dict[str, object]]:
    """Return one canonical row per distinct file backing a loaded module."""

    runtime_root = runtime_package_root.absolute()
    pyffish = pyffish_path.absolute()
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    seen_module_names: set[str] = set()
    for module_name, module in sorted(sys.modules.items()):
        if not isinstance(module_name, str) or not module_name:
            raise E00RunnerError("loaded module name is malformed")
        raw_path = getattr(module, "__file__", None)
        if raw_path is None:
            continue
        if not isinstance(raw_path, str) or not raw_path:
            raise E00RunnerError(
                f"loaded module {module_name} has malformed __file__"
            )
        if raw_path.startswith("<") and raw_path.endswith(">"):
            continue
        path = Path(raw_path).absolute()
        if not path.is_file():
            raise E00RunnerError(
                f"loaded module {module_name} backing file is absent"
            )
        try:
            relative = path.relative_to(runtime_root)
        except ValueError:
            if os.path.normcase(str(path)) == os.path.normcase(str(pyffish)):
                source = "pyffish"
                rendered_path = "pyffish"
            else:
                source = "python-installation"
                rendered_path = str(path)
        else:
            source = "runtime-package"
            rendered_path = relative.as_posix()
        key = (source, rendered_path)
        payload = common.read_stable_file_bytes(
            path, label=f"loaded module {module_name}"
        )
        identity = (
            common.sha256_bytes(payload),
            len(payload),
        )
        row = grouped.get(key)
        if row is None:
            row = {
                "source": source,
                "path": rendered_path,
                "sha256": identity[0],
                "size_bytes": identity[1],
                "module_names": [],
            }
            grouped[key] = row
        elif (row["sha256"], row["size_bytes"]) != identity:
            raise E00RunnerError(
                "two loaded module aliases disagree on backing identity"
            )
        if module_name in seen_module_names:
            raise E00RunnerError("loaded module name is duplicated")
        seen_module_names.add(module_name)
        row["module_names"].append(module_name)  # type: ignore[union-attr]
    inventory = []
    for key in sorted(grouped):
        row = grouped[key]
        row["module_names"] = sorted(row["module_names"])  # type: ignore[arg-type]
        inventory.append(row)
    return _validate_child_import_inventory(
        inventory, label="observed child import inventory"
    )


def _assert_child_import_inventory(
    request: "LegRequest",
) -> list[dict[str, object]]:
    observed = _file_backed_module_inventory(
        runtime_package_root=request.runtime_package_root,
        pyffish_path=request.pyffish,
    )
    expected = _validate_child_import_inventory(
        [dict(row) for row in request.child_import_inventory],
        label="expected child import inventory",
    )
    if observed != expected:
        raise E00RunnerError(
            "child imported-module inventory has extras, omissions, "
            "duplicates, or identity drift"
        )
    return observed


class DefaultRuntimeProbe:
    """Runtime inspector used by the production CLI."""

    def __init__(
        self,
        native_rules: Mapping[str, object],
        child_import_inventory: Sequence[Mapping[str, object]],
    ) -> None:
        self._native_rules = dict(native_rules)
        self._child_import_inventory = _validate_child_import_inventory(
            [dict(row) for row in child_import_inventory],
            label="precommitted child import inventory",
        )

    def capture(self) -> Mapping[str, object]:
        executable = Path(sys.executable).absolute()
        executable_payload = common.read_stable_file_bytes(
            executable, label="Python executable"
        )
        source_root = Path(
            _git_output(Path(__file__).parent, "rev-parse", "--show-toplevel")
        ).absolute()
        commit = _git_output(source_root, "rev-parse", "HEAD")
        tree = _git_output(source_root, "rev-parse", "HEAD^{tree}")
        dirty = _git_output(
            source_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(
            r"[0-9a-f]{40}", tree
        ):
            raise E00RunnerError("git returned malformed commit or tree identity")
        tools_module = sys.modules.get("tools")
        mining_module = sys.modules.get("tools.atomic_mining")
        if tools_module is None or mining_module is None:
            raise E00RunnerError("runtime package initializers are absent")
        return {
            "python": {
                "executable": str(executable),
                "executable_sha256": common.sha256_bytes(executable_payload),
                "executable_size_bytes": len(executable_payload),
                "runtime_libraries": (
                    atomic_outcome_helper._python_runtime_artifacts()
                ),
                "child_import_inventory": self._child_import_inventory,
                "version": sys.version,
            },
            "modules": {
                "atomic_mining_init": _module_identity(
                    mining_module, label="atomic_mining_init"
                ),
                "atomic_outcome_helper": _module_identity(
                    atomic_outcome_helper, label="atomic_outcome_helper"
                ),
                "binding_builder": _module_identity(
                    build_atomic_outcome_binding, label="binding_builder"
                ),
                "common": _module_identity(common, label="common"),
                "owned_process": _module_identity(
                    owned_process, label="owned_process"
                ),
                "schedule_builder": _module_identity(
                    build_e00_schedule, label="schedule_builder"
                ),
                "tools_init": _module_identity(
                    tools_module, label="tools_init"
                ),
                "uci_session": _module_identity(
                    uci_session, label="uci_session"
                ),
                "wrapper": _module_identity(
                    sys.modules[__name__], label="wrapper"
                ),
            },
            "native_rules": dict(self._native_rules),
            "source": {
                "root": str(source_root),
                "commit": commit,
                "tree": tree,
                "clean": dirty == "",
            },
        }


def _runtime_manifest(
    path: Path,
    expected_sha256: str,
    *,
    expected_execution: Mapping[str, object],
    expected_inputs: Mapping[str, str],
    runtime_probe: RuntimeProbe,
) -> tuple[dict[str, object], ArtifactBinding, Mapping[str, object]]:
    value, payload = _load_canonical_json(path, label="runtime manifest")
    binding = _artifact("runtime manifest", path, expected_sha256)
    try:
        if binding.size_bytes != len(payload):
            raise E00RunnerError("runtime manifest size changed during load")
        common.require_exact_fields(
            value,
            ("schema", "runtime", "execution", "inputs"),
            label="runtime manifest",
        )
        if value["schema"] != RUNTIME_MANIFEST_SCHEMA:
            raise E00RunnerError("runtime manifest schema differs")
        if value["execution"] != dict(expected_execution):
            raise E00RunnerError("runtime manifest execution arguments differ")
        if value["inputs"] != dict(expected_inputs):
            raise E00RunnerError("runtime manifest input hashes differ")
        runtime = value["runtime"]
        if not isinstance(runtime, Mapping):
            raise E00RunnerError("runtime manifest runtime must be an object")
        source = runtime.get("source")
        if not isinstance(source, Mapping) or source.get("clean") is not True:
            raise E00RunnerError(
                "runtime manifest must precommit a clean source tree"
            )
        observed = runtime_probe.capture()
        if dict(observed) != dict(runtime):
            raise E00RunnerError(
                "observed runtime differs from precommitted manifest"
            )
        return value, binding, observed
    except BaseException:
        binding.close()
        raise


def trajectory_sha256(
    root_fen: str, moves: Sequence[str], result_white: str
) -> str:
    return _digest_document(
        TRAJECTORY_SCHEMA,
        {
            "moves": list(moves),
            "result_white": result_white,
            "root_fen": root_fen,
        },
    )


def source_game_id(
    *,
    experiment_id: str,
    battery_id: str,
    schedule_sha256: str,
    pair_id: str,
    leg: int,
    trajectory_sha256_value: str,
) -> str:
    return _digest_document(
        SOURCE_GAME_SCHEMA,
        {
            "battery_id": battery_id,
            "experiment_id": experiment_id,
            "leg": leg,
            "pair_id": pair_id,
            "schedule_sha256": schedule_sha256,
            "trajectory_sha256": trajectory_sha256_value,
        },
    )


def _artifact(
    label: str, path: Path, expected_sha256: str
) -> ArtifactBinding:
    expected = common.require_lower_hex_sha256(
        expected_sha256, label=f"{label} expected SHA-256"
    )
    requested = Path(path).absolute()
    guard = _ImmutableFileGuard(requested, label=label)
    active_guards = _ACTIVE_ARTIFACT_GUARDS.get()
    if active_guards is not None:
        active_guards.append(guard)
    try:
        payload = common.read_stable_file_bytes(requested, label=label)
        observed = common.sha256_bytes(payload)
        if observed != expected:
            raise E00RunnerError(
                f"{label} SHA-256 differs: expected {expected}, "
                f"observed {observed}"
            )
        guard.verify()
        if guard.identity["size_bytes"] != len(payload):
            raise E00RunnerError(f"{label} locked size differs")
        return ArtifactBinding(
            label,
            requested,
            observed,
            len(payload),
            dict(guard.identity),
            guard,
        )
    except BaseException:
        guard.close()
        raise


def _verify_artifact_unchanged(binding: ArtifactBinding) -> None:
    binding._guard.verify()
    observed = common.read_stable_file_bytes(binding.path, label=binding.label)
    digest = common.sha256_bytes(observed)
    if digest != binding.sha256 or len(observed) != binding.size_bytes:
        raise E00RunnerError(f"{binding.label} changed during E00 execution")
    binding._guard.verify()


def _directory_binding(
    label: str, path: Path, *, strict_static: bool = False
) -> DirectoryBinding:
    requested = Path(path).absolute()
    guard = _ImmutableDirectoryGuard(
        requested, label=label, strict_static=strict_static
    )
    active_guards = _ACTIVE_DIRECTORY_GUARDS.get()
    if active_guards is not None:
        active_guards.append(guard)
    try:
        guard.verify()
        return DirectoryBinding(
            label=label,
            path=requested,
            identity=dict(guard.identity),
            parent_identity=dict(guard.parent_identity),
            _guard=guard,
        )
    except BaseException:
        guard.close()
        raise


def _directory_inventory(path: Path) -> list[dict[str, object]]:
    root = path.absolute()
    entries: list[dict[str, object]] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for candidate in sorted(
            directory.iterdir(),
            key=lambda item: item.name.casefold(),
            reverse=True,
        ):
            if os.name == "nt":
                attributes = _guard_kernel32.GetFileAttributesW(
                    str(candidate)
                )
                if (
                    attributes == _INVALID_FILE_ATTRIBUTES
                    or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise E00RunnerError(
                        "controlled namespace contains a reparse point"
                    )
            elif candidate.is_symlink():  # pragma: no cover
                raise E00RunnerError(
                    "controlled namespace contains a symlink"
                )
            relative = candidate.relative_to(root).as_posix()
            status = candidate.stat()
            if candidate.is_dir():
                entries.append(
                    {
                        "path": relative,
                        "kind": "directory",
                        "device": int(status.st_dev),
                        "inode": int(status.st_ino),
                    }
                )
                pending.append(candidate)
            elif candidate.is_file():
                entries.append(
                    {
                        "path": relative,
                        "kind": "file",
                        "device": int(status.st_dev),
                        "inode": int(status.st_ino),
                        "size_bytes": int(status.st_size),
                    }
                )
            else:
                raise E00RunnerError(
                    "controlled namespace contains a non-file entry"
                )
    return sorted(entries, key=lambda row: str(row["path"]))


def _verify_directory_binding(
    binding: DirectoryBinding,
    *,
    expected_inventory: Sequence[Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    binding._guard.verify()
    observed = _directory_inventory(binding.path)
    binding._guard.verify()
    if expected_inventory is not None and observed != [
        dict(row) for row in expected_inventory
    ]:
        raise E00RunnerError(
            f"{binding.label} namespace enumeration changed"
        )
    return observed


def _close_binding_sets(
    *collections: Mapping[str, ArtifactBinding] | Sequence[ArtifactBinding],
) -> None:
    bindings: list[ArtifactBinding] = []
    seen: set[int] = set()
    for collection in collections:
        values = (
            collection.values()
            if isinstance(collection, Mapping)
            else collection
        )
        for binding in values:
            marker = id(binding._guard)
            if marker not in seen:
                seen.add(marker)
                bindings.append(binding)
    failure: BaseException | None = None
    for binding in reversed(bindings):
        try:
            binding.close()
        except BaseException as error:
            failure = failure or error
    if failure is not None:
        raise E00RunnerError(
            "immutable artifact locks did not close cleanly"
        ) from failure


def _delegate_parent_locks(
    bindings: Mapping[str, ArtifactBinding]
    | Sequence[ArtifactBinding],
) -> None:
    values = (
        bindings.values()
        if isinstance(bindings, Mapping)
        else bindings
    )
    for binding in values:
        binding._guard.delegate_parent_namespace_lock()


def _bind_directory_artifacts(
    label: str, root: Path
) -> tuple[dict[str, ArtifactBinding], list[dict[str, object]]]:
    """Lock every regular file in a pre-existing, result-bearing tree."""

    requested = Path(root).absolute()
    inventory = _directory_inventory(requested)
    bindings: dict[str, ArtifactBinding] = {}
    try:
        for row in inventory:
            if row["kind"] != "file":
                continue
            relative = str(row["path"])
            path = requested / Path(relative)
            payload = common.read_stable_file_bytes(
                path, label=f"{label} {relative}"
            )
            if len(payload) != int(row["size_bytes"]):
                raise E00RunnerError(
                    f"{label} changed while binding {relative}"
                )
            bindings[relative] = _artifact(
                f"{label} {relative}",
                path,
                common.sha256_bytes(payload),
            )
        if _directory_inventory(requested) != inventory:
            raise E00RunnerError(
                f"{label} namespace changed while binding"
            )
        return bindings, inventory
    except BaseException:
        _close_binding_sets(bindings)
        raise


def _close_guard_scope(guards: Sequence[_ImmutableFileGuard]) -> None:
    seen: set[int] = set()
    failure: BaseException | None = None
    for guard in reversed(guards):
        marker = id(guard)
        if marker in seen:
            continue
        seen.add(marker)
        try:
            guard.close()
        except BaseException as error:
            failure = failure or error
    if failure is not None:
        raise E00RunnerError(
            "battery artifact guard scope did not close cleanly"
        ) from failure


def _close_directory_guard_scope(
    guards: Sequence[_ImmutableDirectoryGuard],
) -> None:
    seen: set[int] = set()
    failure: BaseException | None = None
    for guard in reversed(guards):
        marker = id(guard)
        if marker in seen:
            continue
        seen.add(marker)
        try:
            guard.close()
        except BaseException as error:
            failure = failure or error
    if failure is not None:
        raise E00RunnerError(
            "battery namespace guard scope did not close cleanly"
        ) from failure


def _close_all_guard_scopes(
    file_guards: Sequence[_ImmutableFileGuard],
    directory_guards: Sequence[_ImmutableDirectoryGuard],
) -> None:
    """Attempt both cleanup domains even when the first one reports failure."""

    failure: BaseException | None = None
    for close_scope, guards in (
        (_close_guard_scope, file_guards),
        (_close_directory_guard_scope, directory_guards),
    ):
        try:
            close_scope(guards)  # type: ignore[arg-type]
        except BaseException as error:
            failure = failure or error
    if failure is not None:
        raise E00RunnerError(
            "immutable file and namespace guard cleanup was not clean"
        ) from failure


def _artifact_guard_scoped(function: object) -> object:
    """Guarantee fail-closed lock release across every preflight branch."""

    @wraps(function)
    def wrapped(*args: object, **kwargs: object) -> object:
        guards: list[_ImmutableFileGuard] = []
        directory_guards: list[_ImmutableDirectoryGuard] = []
        token = _ACTIVE_ARTIFACT_GUARDS.set(guards)
        directory_token = _ACTIVE_DIRECTORY_GUARDS.set(directory_guards)
        lock_registry = _ACTIVE_WINDOWS_DIRECTORY_LOCKS.get()
        lock_registry_token = None
        owns_lock_registry = lock_registry is None
        if owns_lock_registry:
            lock_registry = {}
            lock_registry_token = _ACTIVE_WINDOWS_DIRECTORY_LOCKS.set(
                lock_registry
            )
        try:
            try:
                result = function(*args, **kwargs)  # type: ignore[operator]
            except BaseException as primary_error:
                try:
                    _close_all_guard_scopes(guards, directory_guards)
                except BaseException as cleanup_error:
                    raise E00RunnerError(
                        "E00 failure also failed immutable lock cleanup: "
                        f"{type(primary_error).__name__}: {primary_error}"
                    ) from cleanup_error
                raise
            _close_all_guard_scopes(guards, directory_guards)
            return result
        finally:
            _ACTIVE_ARTIFACT_GUARDS.reset(token)
            _ACTIVE_DIRECTORY_GUARDS.reset(directory_token)
            if owns_lock_registry:
                assert lock_registry is not None
                leaked = bool(lock_registry)
                assert lock_registry_token is not None
                _ACTIVE_WINDOWS_DIRECTORY_LOCKS.reset(lock_registry_token)
                if leaked:
                    raise E00RunnerError(
                        "shared Windows namespace lock registry leaked"
                    )

    return wrapped


def _bind_artifact_specs(
    specs: Sequence[tuple[str, str, Path, str]],
) -> dict[str, ArtifactBinding]:
    """Acquire a complete binding set or release every partial lock."""

    bindings: dict[str, ArtifactBinding] = {}
    try:
        for key, label, path, digest in specs:
            if key in bindings:
                raise E00RunnerError(f"duplicate artifact binding key {key}")
            bindings[key] = _artifact(label, path, digest)
        return bindings
    except BaseException:
        _close_binding_sets(bindings)
        raise


def _bind_python_runtime(
    runtime: Mapping[str, object],
) -> dict[str, ArtifactBinding]:
    """Lock the executable and native Python runtime files used by children."""

    python = runtime.get("python")
    if not isinstance(python, Mapping):
        raise E00RunnerError("runtime manifest Python identity is absent")
    common.require_exact_fields(
        python,
        (
            "executable",
            "executable_sha256",
            "executable_size_bytes",
            "runtime_libraries",
            "child_import_inventory",
            "version",
        ),
        label="runtime manifest Python identity",
    )
    if (
        python["executable"] != str(Path(sys.executable).absolute())
        or not isinstance(python["version"], str)
        or python["version"] != sys.version
        or type(python["executable_size_bytes"]) is not int
        or python["executable_size_bytes"] < 1
    ):
        raise E00RunnerError(
            "runtime manifest Python executable identity differs"
        )
    specs: list[tuple[str, str, Path, str]] = [
        (
            "python_executable",
            "Python child executable",
            Path(str(python["executable"])),
            common.require_lower_hex_sha256(
                python["executable_sha256"],
                label="Python executable SHA-256",
            ),
        )
    ]
    libraries = python["runtime_libraries"]
    if not isinstance(libraries, Mapping) or not libraries:
        raise E00RunnerError(
            "runtime manifest Python libraries must be non-empty"
        )
    for ordinal, (name, raw) in enumerate(
        sorted(libraries.items()), start=1
    ):
        if not isinstance(name, str) or not name or not isinstance(raw, Mapping):
            raise E00RunnerError("runtime manifest Python library is malformed")
        common.require_exact_fields(
            raw,
            ("bytes", "path", "sha256"),
            label=f"runtime manifest Python library {name}",
        )
        if (
            type(raw["bytes"]) is not int
            or raw["bytes"] < 1
            or not isinstance(raw["path"], str)
            or not raw["path"]
        ):
            raise E00RunnerError(
                f"runtime manifest Python library {name} is malformed"
            )
        specs.append(
            (
                f"python_runtime_library_{ordinal:03d}",
                f"Python runtime library {name}",
                Path(raw["path"]),
                common.require_lower_hex_sha256(
                    raw["sha256"],
                    label=f"Python runtime library {name} SHA-256",
                ),
            )
        )
    inventory = _validate_child_import_inventory(
        python["child_import_inventory"],
        label="runtime manifest child import inventory",
    )
    installation_rows = [
        row for row in inventory if row["source"] == "python-installation"
    ]
    for ordinal, row in enumerate(installation_rows, start=1):
        specs.append(
            (
                f"python_imported_module_{ordinal:03d}",
                "Python imported module "
                + ", ".join(row["module_names"]),  # type: ignore[arg-type]
                Path(str(row["path"])),
                str(row["sha256"]),
            )
        )
    bindings = _bind_artifact_specs(specs)
    expected_sizes = [python["executable_size_bytes"]] + [
        raw["bytes"] for _name, raw in sorted(libraries.items())
    ] + [row["size_bytes"] for row in installation_rows]
    try:
        if [
            binding.size_bytes for binding in bindings.values()
        ] != expected_sizes:
            raise E00RunnerError(
                "locked Python runtime size differs from manifest"
            )
        return bindings
    except BaseException:
        _close_binding_sets(bindings)
        raise


def _snapshot_artifacts(
    artifacts: Mapping[str, ArtifactBinding], output_dir: Path
) -> dict[str, ArtifactBinding]:
    snapshot_root = output_dir / ".input-snapshots"
    snapshot_root.mkdir(parents=False, exist_ok=False)
    snapshots: dict[str, ArtifactBinding] = {}
    try:
        for key, binding in sorted(artifacts.items()):
            payload = common.read_stable_file_bytes(
                binding.path, label=f"{binding.label} snapshot source"
            )
            if (
                common.sha256_bytes(payload) != binding.sha256
                or len(payload) != binding.size_bytes
            ):
                raise E00RunnerError(
                    f"{binding.label} changed before content snapshot"
                )
            suffix = binding.path.suffix
            snapshot_path = snapshot_root / f"{key}-{binding.sha256}{suffix}"
            common.write_new_bytes(snapshot_path, payload)
            try:
                source_mode = binding.path.stat().st_mode
                os.chmod(
                    snapshot_path,
                    source_mode
                    & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH),
                )
            except OSError as error:
                raise E00RunnerError(
                    f"cannot make {binding.label} snapshot read-only"
                ) from error
            snapshots[key] = _artifact(
                f"{binding.label} snapshot", snapshot_path, binding.sha256
            )
        return snapshots
    except BaseException:
        _close_binding_sets(snapshots)
        raise


_RUNTIME_PACKAGE_LAYOUT = {
    "tools_init": Path("tools/__init__.py"),
    "atomic_mining_init": Path("tools/atomic_mining/__init__.py"),
    "common": Path("tools/atomic_mining/common.py"),
    "schedule_builder": Path("tools/atomic_mining/build_e00_schedule.py"),
    "runner": Path("tools/atomic_mining/run_e00_source.py"),
    "uci_session": Path("tools/atomic_mining/uci_session.py"),
    "atomic_outcome_helper": Path(
        "tools/atomic_mining/atomic_outcome_helper.py"
    ),
    "binding_builder": Path(
        "tools/atomic_mining/build_atomic_outcome_binding.py"
    ),
    "owned_process": Path("tools/atomic_mining/owned_process.py"),
}


def _runtime_module_objects() -> dict[str, object]:
    tools_module = sys.modules.get("tools")
    mining_module = sys.modules.get("tools.atomic_mining")
    wrapper_module = sys.modules.get(__name__)
    if (
        tools_module is None
        or mining_module is None
        or wrapper_module is None
    ):
        raise E00RunnerError("runtime package modules are not all loaded")
    return {
        "tools_init": tools_module,
        "atomic_mining_init": mining_module,
        "common": common,
        "schedule_builder": build_e00_schedule,
        "runner": wrapper_module,
        "uci_session": uci_session,
        "atomic_outcome_helper": atomic_outcome_helper,
        "binding_builder": build_atomic_outcome_binding,
        "owned_process": owned_process,
    }


def _runtime_module_inventory(
    runtime_package_root: Path,
) -> list[dict[str, object]]:
    """Hash the exact source tree required by all isolated child modes."""

    root = Path(runtime_package_root).absolute()
    entries: list[dict[str, object]] = []
    for key, relative in sorted(_RUNTIME_PACKAGE_LAYOUT.items()):
        path = (root / relative).absolute()
        payload = common.read_stable_file_bytes(
            path, label=f"runtime discovery module {key}"
        )
        entries.append(
            {
                "key": key,
                "path": str(path),
                "sha256": common.sha256_bytes(payload),
                "size_bytes": len(payload),
            }
        )
    return entries


def build_runtime_discovery_request(
    *,
    runtime_package_root: Path,
    pyffish: Path,
    pyffish_sha256: str,
    pyffish_manifest_binding_path: Path,
    pyffish_build_manifest: Path,
    pyffish_build_manifest_sha256: str,
    source_root: Path,
    source_commit: str,
) -> dict[str, object]:
    """Build the canonical inventory-only child request; no process is run."""

    source_commit = str(source_commit).lower()
    if re.fullmatch(r"[0-9a-f]{40,64}", source_commit) is None:
        raise E00RunnerError("runtime discovery source commit is malformed")
    return {
        "schema": INTERNAL_RUNTIME_DISCOVERY_REQUEST_SCHEMA,
        "runtime_package_root": str(
            Path(runtime_package_root).absolute()
        ),
        "runtime_modules": _runtime_module_inventory(
            Path(runtime_package_root)
        ),
        "pyffish": str(Path(pyffish).absolute()),
        "pyffish_sha256": common.require_lower_hex_sha256(
            pyffish_sha256, label="runtime discovery pyffish SHA-256"
        ),
        "pyffish_manifest_binding_path": str(
            Path(pyffish_manifest_binding_path).absolute()
        ),
        "pyffish_build_manifest": str(
            Path(pyffish_build_manifest).absolute()
        ),
        "pyffish_build_manifest_sha256": (
            common.require_lower_hex_sha256(
                pyffish_build_manifest_sha256,
                label="runtime discovery build manifest SHA-256",
            )
        ),
        "source_root": str(Path(source_root).absolute()),
        "source_commit": source_commit,
    }


def _runtime_discovery_request_from_wire(
    value: Mapping[str, object],
) -> RuntimeDiscoveryRequest:
    common.require_exact_fields(
        value,
        (
            "schema",
            "runtime_package_root",
            "runtime_modules",
            "pyffish",
            "pyffish_sha256",
            "pyffish_manifest_binding_path",
            "pyffish_build_manifest",
            "pyffish_build_manifest_sha256",
            "source_root",
            "source_commit",
        ),
        label="internal runtime discovery request",
    )
    if value["schema"] != INTERNAL_RUNTIME_DISCOVERY_REQUEST_SCHEMA:
        raise E00RunnerError(
            "internal runtime discovery request schema differs"
        )

    def path_field(name: str) -> Path:
        raw = value[name]
        if not isinstance(raw, str) or not raw:
            raise E00RunnerError(
                f"runtime discovery {name} path must be text"
            )
        return Path(raw).absolute()

    runtime_root = path_field("runtime_package_root")
    raw_modules = value["runtime_modules"]
    if not isinstance(raw_modules, list):
        raise E00RunnerError(
            "runtime discovery modules must be an array"
        )
    modules: list[dict[str, object]] = []
    for ordinal, raw in enumerate(raw_modules):
        if not isinstance(raw, Mapping):
            raise E00RunnerError(
                "runtime discovery module entry must be an object"
            )
        common.require_exact_fields(
            raw,
            ("key", "path", "sha256", "size_bytes"),
            label=f"runtime discovery module {ordinal}",
        )
        key = raw["key"]
        if (
            not isinstance(key, str)
            or key not in _RUNTIME_PACKAGE_LAYOUT
            or not isinstance(raw["path"], str)
            or type(raw["size_bytes"]) is not int
            or raw["size_bytes"] < 0
        ):
            raise E00RunnerError(
                "runtime discovery module entry is malformed"
            )
        expected_path = (
            runtime_root / _RUNTIME_PACKAGE_LAYOUT[key]
        ).absolute()
        if Path(raw["path"]).absolute() != expected_path:
            raise E00RunnerError(
                "runtime discovery module escapes runtime package"
            )
        modules.append(
            {
                "key": key,
                "path": str(expected_path),
                "sha256": common.require_lower_hex_sha256(
                    raw["sha256"],
                    label=f"runtime discovery module {key} SHA-256",
                ),
                "size_bytes": raw["size_bytes"],
            }
        )
    if (
        modules
        != sorted(modules, key=lambda row: str(row["key"]))
        or {str(row["key"]) for row in modules}
        != set(_RUNTIME_PACKAGE_LAYOUT)
        or len(modules) != len(_RUNTIME_PACKAGE_LAYOUT)
    ):
        raise E00RunnerError(
            "runtime discovery modules are duplicated, missing, "
            "or not canonical"
        )
    source_commit = value["source_commit"]
    if (
        not isinstance(source_commit, str)
        or re.fullmatch(r"[0-9a-f]{40,64}", source_commit) is None
    ):
        raise E00RunnerError(
            "runtime discovery source commit is malformed"
        )
    return RuntimeDiscoveryRequest(
        runtime_package_root=runtime_root,
        runtime_modules=tuple(modules),
        pyffish=path_field("pyffish"),
        pyffish_sha256=common.require_lower_hex_sha256(
            value["pyffish_sha256"],
            label="runtime discovery pyffish SHA-256",
        ),
        pyffish_manifest_binding_path=path_field(
            "pyffish_manifest_binding_path"
        ),
        pyffish_build_manifest=path_field(
            "pyffish_build_manifest"
        ),
        pyffish_build_manifest_sha256=(
            common.require_lower_hex_sha256(
                value["pyffish_build_manifest_sha256"],
                label="runtime discovery build manifest SHA-256",
            )
        ),
        source_root=path_field("source_root"),
        source_commit=source_commit,
    )


def _runtime_discovery_request_wire(
    request: RuntimeDiscoveryRequest,
) -> dict[str, object]:
    return {
        "schema": INTERNAL_RUNTIME_DISCOVERY_REQUEST_SCHEMA,
        "runtime_package_root": str(request.runtime_package_root),
        "runtime_modules": [
            dict(row) for row in request.runtime_modules
        ],
        "pyffish": str(request.pyffish),
        "pyffish_sha256": request.pyffish_sha256,
        "pyffish_manifest_binding_path": str(
            request.pyffish_manifest_binding_path
        ),
        "pyffish_build_manifest": str(
            request.pyffish_build_manifest
        ),
        "pyffish_build_manifest_sha256": (
            request.pyffish_build_manifest_sha256
        ),
        "source_root": str(request.source_root),
        "source_commit": request.source_commit,
    }


def _validate_discovered_inventory_bindings(
    inventory: object,
    request: RuntimeDiscoveryRequest,
) -> list[dict[str, object]]:
    rows = _validate_child_import_inventory(
        inventory, label="runtime discovery child import inventory"
    )
    expected_runtime = {
        _RUNTIME_PACKAGE_LAYOUT[str(row["key"])].as_posix(): row
        for row in request.runtime_modules
    }
    observed_runtime: set[str] = set()
    observed_pyffish = 0
    for row in rows:
        source = row["source"]
        if source == "runtime-package":
            expected = expected_runtime.get(str(row["path"]))
            if expected is None or (
                row["sha256"],
                row["size_bytes"],
            ) != (
                expected["sha256"],
                expected["size_bytes"],
            ):
                raise E00RunnerError(
                    "runtime discovery module inventory differs"
                )
            observed_runtime.add(str(row["path"]))
        elif source == "pyffish":
            observed_pyffish += 1
            if (
                row["sha256"] != request.pyffish_sha256
                or row["size_bytes"] != request.pyffish.stat().st_size
            ):
                raise E00RunnerError(
                    "runtime discovery pyffish inventory differs"
                )
    if observed_runtime != set(expected_runtime):
        raise E00RunnerError(
            "runtime discovery inventory omits a runtime module"
        )
    if observed_pyffish != 1:
        raise E00RunnerError(
            "runtime discovery inventory must contain one pyffish row"
        )
    return rows


def _runtime_discovery_artifact_specs(
    request: RuntimeDiscoveryRequest,
) -> list[tuple[str, str, Path, str]]:
    specs = [
        (
            f"runtime_module_{row['key']}",
            f"runtime discovery module {row['key']}",
            Path(str(row["path"])),
            str(row["sha256"]),
        )
        for row in request.runtime_modules
    ]
    specs.extend(
        (
            (
                "pyffish",
                "runtime discovery pyffish",
                request.pyffish,
                request.pyffish_sha256,
            ),
            (
                "manifest_pyffish",
                "runtime discovery manifest pyffish",
                request.pyffish_manifest_binding_path,
                request.pyffish_sha256,
            ),
            (
                "pyffish_build_manifest",
                "runtime discovery build manifest",
                request.pyffish_build_manifest,
                request.pyffish_build_manifest_sha256,
            ),
        )
    )
    return specs


def _snapshot_runtime_package(
    snapshots: Mapping[str, ArtifactBinding], output_dir: Path
) -> tuple[Path, dict[str, ArtifactBinding]]:
    """Materialize the exact import tree used by every isolated child."""

    root = output_dir / ".runtime-package"
    root.mkdir(parents=False, exist_ok=False)
    executed: dict[str, ArtifactBinding] = {}
    try:
        for key, relative in _RUNTIME_PACKAGE_LAYOUT.items():
            source = snapshots.get(key)
            if source is None:
                raise E00RunnerError(
                    f"runtime package input {key} is not snapshotted"
                )
            payload = common.read_stable_file_bytes(
                source.path, label=f"runtime package source {key}"
            )
            if (
                common.sha256_bytes(payload) != source.sha256
                or len(payload) != source.size_bytes
            ):
                raise E00RunnerError(
                    f"runtime package source {key} changed before execution copy"
                )
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            common.write_new_bytes(destination, payload)
            try:
                source_mode = source.path.stat().st_mode
                os.chmod(
                    destination,
                    source_mode
                    & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH),
                )
            except OSError as error:
                raise E00RunnerError(
                    f"cannot make executed runtime module {key} read-only"
                ) from error
            executed[key] = _artifact(
                f"executed runtime module {key}",
                destination.absolute(),
                source.sha256,
            )
        return root.absolute(), executed
    except BaseException:
        _close_binding_sets(executed)
        raise


def _validate_import_inventory_bindings(
    inventory: Sequence[Mapping[str, object]],
    *,
    artifacts: Mapping[str, ArtifactBinding],
    snapshots: Mapping[str, ArtifactBinding],
    executed_modules: Mapping[str, ArtifactBinding],
) -> None:
    rows = _validate_child_import_inventory(
        [dict(row) for row in inventory],
        label="bound child import inventory",
    )
    original_by_path = {
        os.path.normcase(str(binding.path)): binding
        for binding in artifacts.values()
    }
    runtime_by_relative = {
        relative.as_posix(): executed_modules[key]
        for key, relative in _RUNTIME_PACKAGE_LAYOUT.items()
    }
    observed_runtime_paths: set[str] = set()
    observed_pyffish = 0
    for row in rows:
        source = row["source"]
        if source == "python-installation":
            binding = original_by_path.get(
                os.path.normcase(str(row["path"]))
            )
            if binding is None:
                raise E00RunnerError(
                    "imported Python module is not held by an original guard"
                )
        elif source == "runtime-package":
            binding = runtime_by_relative.get(str(row["path"]))
            if binding is None:
                raise E00RunnerError(
                    "child import inventory escapes executed runtime package"
                )
            observed_runtime_paths.add(str(row["path"]))
        else:
            binding = snapshots["pyffish"]
            observed_pyffish += 1
        if (
            binding.sha256 != row["sha256"]
            or binding.size_bytes != row["size_bytes"]
        ):
            raise E00RunnerError(
                "child import inventory differs from locked artifact"
            )
    if observed_runtime_paths != set(runtime_by_relative):
        raise E00RunnerError(
            "child import inventory omits an executed runtime module"
        )
    if observed_pyffish != 1:
        raise E00RunnerError(
            "child import inventory must contain exactly one pyffish row"
        )


def _schedule_adapter(
    schedule_path: Path,
    schedule_receipt_path: Path,
    *,
    experiment_id: str,
    battery_id: str,
) -> ValidatedSchedule:
    """Import and adapt the public schedule validator without duplicating it."""

    from . import build_e00_schedule as schedule

    package = schedule.load_schedule(
        Path(schedule_path), Path(schedule_receipt_path)
    )
    if package.receipt is None or package.receipt_sha256 is None:
        raise E00RunnerError("result-bearing execution requires schedule receipt")
    experiment_id = _require_clean_id(
        experiment_id, label="experiment_id"
    )
    battery_id = _require_clean_id(battery_id, label="battery_id")
    pairs: list[SchedulePair] = []
    for index in range(0, len(package.rows), 2):
        pairs.append(
            _adapt_pair(
                package.rows[index],
                package.rows[index + 1],
                expected_ordinal=index // 2 + 1,
                experiment_id=experiment_id,
                battery_id=battery_id,
            )
        )
    receipt_payload = common.read_stable_file_bytes(
        Path(schedule_receipt_path).absolute(), label="schedule receipt"
    )

    return ValidatedSchedule(
        pairs=tuple(pairs),
        schedule_sha256=package.sha256,
        schedule_size_bytes=package.size_bytes,
        receipt_sha256=package.receipt_sha256,
        receipt_size_bytes=len(receipt_payload),
    )


def _adapt_pair(
    first: Mapping[str, object],
    second: Mapping[str, object],
    *,
    expected_ordinal: int,
    experiment_id: str,
    battery_id: str,
) -> SchedulePair:
    """Adapt an already validated schedule pair to the runner's narrow wire."""

    pair_id = common.require_lower_hex_sha256(
        first.get("pair_id"), label="schedule pair_id"
    )
    ordinal = _require_int(
        first.get("pair_ordinal"),
        label="schedule pair ordinal",
        minimum=1,
    )
    if ordinal != expected_ordinal:
        raise E00RunnerError(
            "validated schedule pairs are not in contiguous ordinal order"
        )
    time_control = _time_control(
        first.get("time_control"), label="schedule time_control"
    )
    stratum = str(time_control["name"])
    book_sha256 = common.require_lower_hex_sha256(
        first.get("book_sha256"), label="schedule book_sha256"
    )
    root = first.get("root")
    if not isinstance(root, Mapping):
        raise E00RunnerError("schedule root must be an object")
    book_line = _require_int(
        root.get("book_line"),
        label="schedule book line",
        minimum=1,
    )
    root_fen = root.get("fen")
    if not isinstance(root_fen, str):
        raise E00RunnerError("schedule root_fen must be text")
    common.fen_fields(root_fen)
    root_sha = common.require_lower_hex_sha256(
        root.get("fen_sha256"), label="schedule root_fen_sha256"
    )
    expected_root_sha = common.derive_id("atomic-root-fen-v1", root_fen)
    if root_sha != expected_root_sha:
        raise E00RunnerError("schedule root_fen_sha256 does not recompute")
    if (
        second.get("pair_id") != pair_id
        or second.get("pair_ordinal") != ordinal
        or second.get("root") != root
        or second.get("book_sha256") != book_sha256
        or second.get("time_control") != first.get("time_control")
        or first.get("leg") != 0
        or second.get("leg") != 1
        or (first.get("white_role"), first.get("black_role"))
        != ("current-v3", "run3b")
        or (second.get("white_role"), second.get("black_role"))
        != ("run3b", "current-v3")
    ):
        raise E00RunnerError("validated schedule pair wire is not color-inverted")
    seed = first.get("seed")
    if not isinstance(seed, str) or not seed or "\x00" in seed:
        raise E00RunnerError("schedule seed differs from the validated wire")
    if second.get("seed") != seed:
        raise E00RunnerError("pair legs have different schedule seeds")
    return SchedulePair(
        experiment_id=experiment_id,
        battery_id=battery_id,
        seed=seed,
        pair_id=pair_id,
        pair_ordinal=ordinal,
        stratum=stratum,
        time_control=time_control,
        book_sha256=book_sha256,
        book_line=book_line,
        root_fen=root_fen,
        root_fen_sha256=root_sha,
    )


def _result_current(result_white: str, current_is_white: bool) -> str:
    if result_white == "1/2-1/2":
        return "draw"
    current_won = (result_white == "1-0") == current_is_white
    return "win" if current_won else "loss"


def _validate_engine_evidence(
    evidence: Mapping[str, object], request: LegRequest
) -> None:
    common.require_exact_fields(
        evidence,
        ("schema", "variant_path_policy", "engines"),
        label="engine evidence",
    )
    if evidence["schema"] != ENGINE_EVIDENCE_SCHEMA:
        raise E00RunnerError("engine evidence schema differs")
    policy = evidence["variant_path_policy"]
    if policy not in {"configured", "unavailable-explicit"}:
        raise E00RunnerError("engine evidence VariantPath policy differs")
    engines = evidence["engines"]
    if not isinstance(engines, list) or len(engines) != 2:
        raise E00RunnerError("engine evidence must contain exactly two engines")
    roles = (request.white_network_role, request.black_network_role)
    network_paths = {
        "current-v3": request.current_net,
        "run3b": request.teacher_net,
    }
    advertised_by_engine: list[list[object]] = []
    identifiers: list[dict[str, str]] = []
    for index, (raw, role) in enumerate(zip(engines, roles, strict=True)):
        if not isinstance(raw, Mapping):
            raise E00RunnerError("engine evidence entry must be an object")
        common.require_exact_fields(
            raw,
            (
                "role",
                "id",
                "advertised_options",
                "advertised_options_sha256",
                "configured_options",
                "network_proof",
            ),
            label=f"engine evidence entry {index}",
        )
        if raw["role"] != role:
            raise E00RunnerError("engine evidence role order differs")
        identifier = raw["id"]
        if not isinstance(identifier, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in identifier.items()
        ):
            raise E00RunnerError("engine evidence id must be string mapping")
        if set(identifier) != {"name", "author"} or any(
            not value for value in identifier.values()
        ):
            raise E00RunnerError("engine evidence id is incomplete")
        identifiers.append(dict(identifier))
        common.require_lower_hex_sha256(
            raw["advertised_options_sha256"],
            label="advertised options SHA-256",
        )
        advertised = raw["advertised_options"]
        if not isinstance(advertised, list) or not advertised:
            raise E00RunnerError("advertised UCI options must be non-empty")
        advertised_names: list[str] = []
        for option_index, option in enumerate(advertised):
            if not isinstance(option, Mapping):
                raise E00RunnerError("advertised UCI option must be an object")
            common.require_exact_fields(
                option,
                (
                    "name",
                    "kind",
                    "default",
                    "minimum",
                    "maximum",
                    "choices",
                    "raw",
                ),
                label=f"advertised UCI option {option_index}",
            )
            if (
                not isinstance(option["name"], str)
                or not option["name"]
                or option["kind"]
                not in {"button", "check", "spin", "combo", "string"}
                or not isinstance(option["choices"], list)
                or not isinstance(option["raw"], str)
                or not option["raw"]
            ):
                raise E00RunnerError(
                    "advertised UCI option declaration is malformed"
                )
            advertised_names.append(option["name"].casefold())
        if len(advertised_names) != len(set(advertised_names)):
            raise E00RunnerError("advertised UCI option names are duplicated")
        if advertised_names != sorted(advertised_names):
            raise E00RunnerError("advertised UCI options are not sorted")
        if raw["advertised_options_sha256"] != _digest_document(
            "atomic-e00-advertised-options-v2", advertised
        ):
            raise E00RunnerError(
                "advertised UCI option digest does not recompute"
            )
        expected: list[dict[str, object]] = []
        if policy == "configured":
            expected.append(
                {"name": "VariantPath", "value": str(request.variant_config)}
            )
        expected.extend(
            {"name": name, "value": value}
            for name, value in request.uci_options
        )
        expected.append(
            {"name": "EvalFile", "value": str(network_paths[role])}
        )
        if raw["configured_options"] != expected:
            raise E00RunnerError("engine evidence configured options differ")
        required_names = {entry["name"].casefold() for entry in expected}
        if not required_names.issubset(set(advertised_names)):
            raise E00RunnerError(
                "engine did not advertise every configured UCI option"
            )
        proof = raw["network_proof"]
        if not isinstance(proof, Mapping):
            raise E00RunnerError("engine network proof must be an object")
        common.require_exact_fields(
            proof,
            (
                "backend",
                "evalfile_path",
                "evalfile_sha256",
                "first_go",
                "line",
                "line_sha256",
                "raw_lines",
                "raw_lines_sha256",
            ),
            label=f"engine network proof {index}",
        )
        expected_path = network_paths[role]
        expected_line_prefix = (
            "info string NNUE evaluation using "
            f"{NETWORK_BACKENDS[role]} {expected_path} ("
        )
        if (
            proof["backend"] != NETWORK_BACKENDS[role]
            or proof["evalfile_path"] != str(expected_path)
            or proof["evalfile_sha256"]
            != (
                request.current_net_sha256
                if role == "current-v3"
                else request.teacher_net_sha256
            )
            or proof["first_go"] != "preflight-nodes-1"
            or not isinstance(proof["line"], str)
            or not proof["line"].startswith(expected_line_prefix)
            or not proof["line"].endswith(")")
            or proof["line_sha256"]
            != common.sha256_bytes(proof["line"].encode("utf-8"))
        ):
            raise E00RunnerError(
                "engine network proof differs from the exact snapshot"
            )
        common.require_lower_hex_sha256(
            proof["raw_lines_sha256"],
            label="engine network proof raw-lines SHA-256",
        )
        if (
            not isinstance(proof["raw_lines"], list)
            or not proof["raw_lines"]
            or not all(
                isinstance(line, str) for line in proof["raw_lines"]
            )
            or proof["raw_lines_sha256"]
            != _digest_document(
                "atomic-e00-uci-go-raw-lines-v2",
                proof["raw_lines"],
            )
            or [
                line
                for line in proof["raw_lines"]
                if line.startswith(
                    "info string NNUE evaluation using "
                )
            ]
            != [proof["line"]]
            or any(
                "ERROR:" in line
                or "Classical Atomic evaluation enabled" in line
                for line in proof["raw_lines"]
            )
        ):
            raise E00RunnerError(
                "engine network proof raw journal differs"
            )
        advertised_by_engine.append(advertised)
    if advertised_by_engine[0] != advertised_by_engine[1]:
        raise E00RunnerError(
            "identical engine processes advertised different UCI contracts"
        )
    if identifiers[0] != identifiers[1]:
        raise E00RunnerError(
            "identical engine processes advertised different identities"
        )


def _validate_artifact_evidence(
    value: object,
    *,
    path: Path,
    sha256: str,
    label: str,
) -> None:
    if not isinstance(value, Mapping):
        raise E00RunnerError(f"{label} must be an object")
    common.require_exact_fields(
        value, ("bytes", "path", "sha256"), label=label
    )
    if (
        value["path"] != str(path)
        or value["sha256"] != sha256
        or value["bytes"] != path.stat().st_size
    ):
        raise E00RunnerError(f"{label} differs from executed artifact")


def _validate_native_provenance(
    value: object, request: LegRequest
) -> None:
    if not isinstance(value, Mapping):
        raise E00RunnerError("native provenance must be an object")
    common.require_exact_fields(
        value,
        (
            "native",
            "builder",
            "uci_session",
            "owned_process",
            "manifest_binding_path",
        ),
        label="native provenance",
    )
    native = value["native"]
    if not isinstance(native, Mapping):
        raise E00RunnerError("native helper provenance must be an object")
    if native.get("schema") != atomic_outcome_helper.PROVENANCE_SCHEMA:
        raise E00RunnerError("native helper provenance schema differs")
    _validate_artifact_evidence(
        native.get("pyffish"),
        path=request.pyffish,
        sha256=request.pyffish_sha256,
        label="executed pyffish provenance",
    )
    _validate_artifact_evidence(
        native.get("build_manifest"),
        path=request.pyffish_build_manifest,
        sha256=request.pyffish_build_manifest_sha256,
        label="executed build manifest provenance",
    )
    _validate_artifact_evidence(
        native.get("helper"),
        path=request.helper,
        sha256=request.helper_sha256,
        label="executed helper provenance",
    )
    rules_source = native.get("rules_source")
    if (
        not isinstance(rules_source, Mapping)
        or rules_source.get("root") != str(request.source_root)
        or rules_source.get("commit") != request.source_commit
    ):
        raise E00RunnerError("native rules source provenance differs")
    for key, path, digest in (
        ("builder", request.builder, request.builder_sha256),
        ("uci_session", request.uci_module, request.uci_module_sha256),
        (
            "owned_process",
            request.owned_process_module,
            request.owned_process_module_sha256,
        ),
    ):
        _validate_artifact_evidence(
            value[key], path=path, sha256=digest, label=key
        )
    if value["manifest_binding_path"] != str(
        request.pyffish_manifest_binding_path
    ):
        raise E00RunnerError("manifest binding original path differs")


def _validate_rules_calls(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or not value:
        raise E00RunnerError("native rules call journal must be non-empty")
    calls: list[Mapping[str, object]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise E00RunnerError("native rules call must be an object")
        common.require_exact_fields(
            raw,
            (
                "operation",
                "request",
                "response",
                "request_sha256",
                "response_sha256",
            ),
            label=f"native rules call {index}",
        )
        if not isinstance(raw["operation"], str) or not raw["operation"]:
            raise E00RunnerError("native rules operation is malformed")
        if raw["request_sha256"] != common.sha256_bytes(
            common.canonical_json_bytes(raw["request"])
        ):
            raise E00RunnerError("native rules request digest differs")
        if raw["response_sha256"] != common.sha256_bytes(
            common.canonical_json_bytes(raw["response"])
        ):
            raise E00RunnerError("native rules response digest differs")
        calls.append(raw)
    return calls


def _validate_process_evidence(
    value: object, *, label: str, allow_termination: bool = False
) -> None:
    if not isinstance(value, Mapping):
        raise E00RunnerError(f"{label} must be an object")
    common.require_exact_fields(
        value,
        (
            "schema",
            "containment",
            "kill_on_close",
            "created_suspended_before_assignment",
            "resumed_primary_thread",
            "active_processes_zero",
            "termination_requested",
        ),
        label=label,
    )
    if value["schema"] != "atomic-e00-owned-process-v1":
        raise E00RunnerError(f"{label} schema differs")
    if os.name == "nt" and (
        value["containment"] != "windows-job-object"
        or value["kill_on_close"] is not True
        or value["created_suspended_before_assignment"] is not True
        or value["resumed_primary_thread"] is not True
    ):
        raise E00RunnerError(f"{label} Windows containment differs")
    if value["active_processes_zero"] is not True:
        raise E00RunnerError(f"{label} did not prove active-process-zero")
    if not allow_termination and value["termination_requested"] is not False:
        raise E00RunnerError(f"{label} required forced cleanup")
    for field in (
        "kill_on_close",
        "created_suspended_before_assignment",
        "resumed_primary_thread",
        "active_processes_zero",
        "termination_requested",
    ):
        if type(value[field]) is not bool:
            raise E00RunnerError(f"{label} boolean evidence is malformed")


def _validate_referee_evidence(
    value: object, request: LegRequest, execution: GameExecution
) -> None:
    if not isinstance(value, Mapping):
        raise E00RunnerError("referee evidence must be an object")
    common.require_exact_fields(
        value,
        (
            "schema",
            "provenance",
            "rules_calls",
            "timing_events",
            "child_import_inventory",
        ),
        label="referee evidence",
    )
    if value["schema"] != REFEREE_EVIDENCE_SCHEMA:
        raise E00RunnerError("referee evidence schema differs")
    _validate_native_provenance(value["provenance"], request)
    if _validate_child_import_inventory(
        value["child_import_inventory"],
        label="referee child import inventory",
    ) != [dict(row) for row in request.child_import_inventory]:
        raise E00RunnerError("referee child import inventory differs")
    calls = _validate_rules_calls(value["rules_calls"])
    if calls[0]["operation"] != "outcome":
        raise E00RunnerError("referee did not evaluate the root first")
    events = value["timing_events"]
    if not isinstance(events, list) or not events:
        raise E00RunnerError("referee timing journal must be non-empty")
    expected_clocks = [
        int(request.pair.time_control["base_ms"]) * 1_000_000,
        int(request.pair.time_control["base_ms"]) * 1_000_000,
    ]
    increment_ns = int(
        request.pair.time_control["increment_ms"]
    ) * 1_000_000
    applied_moves: list[str] = []
    expected_operations = ["outcome"]
    for ply, raw in enumerate(events):
        if not isinstance(raw, Mapping):
            raise E00RunnerError("timing event must be an object")
        common.require_exact_fields(
            raw,
            (
                "ply",
                "role",
                "side_index",
                "remaining_before_ns",
                "white_clock_ms",
                "black_clock_ms",
                "go_started_ns",
                "bestmove_completed_ns",
                "elapsed_ns",
                "on_time",
                "bestmove",
                "move_applied",
                "remaining_after_ns",
                "network_marker",
                "network_marker_sha256",
                "network_raw_lines_sha256",
                "network_raw_lines",
            ),
            label=f"timing event {ply}",
        )
        if raw["ply"] != ply:
            raise E00RunnerError("timing event ply order differs")
        side_index = raw["side_index"]
        if side_index not in (0, 1):
            raise E00RunnerError("timing event side differs")
        if side_index != _side_to_move_index(request.pair.root_fen, ply):
            raise E00RunnerError("timing event side does not alternate")
        expected_role = (
            request.white_network_role
            if side_index == 0
            else request.black_network_role
        )
        if raw["role"] != expected_role:
            raise E00RunnerError("timing event role differs")
        network_path = (
            request.current_net
            if expected_role == "current-v3"
            else request.teacher_net
        )
        expected_marker_prefix = (
            "info string NNUE evaluation using "
            f"{NETWORK_BACKENDS[expected_role]} {network_path} ("
        )
        if (
            not isinstance(raw["network_marker"], str)
            or not raw["network_marker"].startswith(expected_marker_prefix)
            or not raw["network_marker"].endswith(")")
            or raw["network_marker_sha256"]
            != common.sha256_bytes(
                raw["network_marker"].encode("utf-8")
            )
        ):
            raise E00RunnerError("timed go NNUE marker differs")
        common.require_lower_hex_sha256(
            raw["network_raw_lines_sha256"],
            label="timed go raw-lines SHA-256",
        )
        if (
            not isinstance(raw["network_raw_lines"], list)
            or not raw["network_raw_lines"]
            or not all(
                isinstance(line, str)
                for line in raw["network_raw_lines"]
            )
            or raw["network_raw_lines_sha256"]
            != _digest_document(
                "atomic-e00-uci-go-raw-lines-v2",
                raw["network_raw_lines"],
            )
            or [
                line
                for line in raw["network_raw_lines"]
                if line.startswith(
                    "info string NNUE evaluation using "
                )
            ]
            != [raw["network_marker"]]
            or any(
                "ERROR:" in line
                or "Classical Atomic evaluation enabled" in line
                for line in raw["network_raw_lines"]
            )
        ):
            raise E00RunnerError("timed go raw NNUE journal differs")
        integer_fields = (
            "remaining_before_ns",
            "white_clock_ms",
            "black_clock_ms",
            "go_started_ns",
            "bestmove_completed_ns",
            "elapsed_ns",
        )
        if any(
            type(raw[field]) is not int or raw[field] < 0
            for field in integer_fields
        ):
            raise E00RunnerError("timing event integer field is malformed")
        elapsed = raw["bestmove_completed_ns"] - raw["go_started_ns"]
        if elapsed != raw["elapsed_ns"]:
            raise E00RunnerError("timing event elapsed interval differs")
        if raw["remaining_before_ns"] != expected_clocks[side_index]:
            raise E00RunnerError("timing event prior clock differs")
        if (
            raw["white_clock_ms"] != expected_clocks[0] // 1_000_000
            or raw["black_clock_ms"]
            != expected_clocks[1] // 1_000_000
        ):
            raise E00RunnerError("UCI millisecond clock conversion differs")
        on_time = elapsed <= raw["remaining_before_ns"]
        if raw["on_time"] is not on_time or raw["move_applied"] is not on_time:
            raise E00RunnerError("timing equality/application policy differs")
        if on_time:
            move = raw["bestmove"]
            if not isinstance(move, str) or _UCI_MOVE.fullmatch(move) is None:
                raise E00RunnerError("applied bestmove is malformed")
            if type(raw["remaining_after_ns"]) is not int:
                raise E00RunnerError("on-time move lacks resulting clock")
            expected_after = (
                expected_clocks[side_index] - elapsed + increment_ns
            )
            if raw["remaining_after_ns"] != expected_after:
                raise E00RunnerError("resulting clock arithmetic differs")
            expected_clocks[side_index] = expected_after
            applied_moves.append(move)
            expected_operations.extend(("legal-moves", "outcome"))
        elif raw["remaining_after_ns"] is not None:
            raise E00RunnerError("flag event changed the remaining clock")
        else:
            expected_operations.extend(
                ("legal-moves", "insufficient-material")
            )
        if not on_time and ply + 1 != len(events):
            raise E00RunnerError("timing journal continues after a flag")
    if tuple(applied_moves) != execution.moves:
        raise E00RunnerError("timing journal moves differ from trajectory")
    if execution.time_loss != (events[-1]["on_time"] is False):
        raise E00RunnerError("time-loss differs from timing journal")
    if execution.time_loss:
        if calls[-1]["operation"] != "insufficient-material":
            raise E00RunnerError("flag result lacks material adjudication")
    elif calls[-1]["operation"] != "outcome":
        raise E00RunnerError("terminal result lacks native outcome call")
    if [call["operation"] for call in calls] != expected_operations:
        raise E00RunnerError("native referee call sequence differs")


def _validate_verifier_evidence(
    value: object, request: LegRequest, execution: GameExecution
) -> None:
    if not isinstance(value, Mapping):
        raise E00RunnerError("verifier evidence must be an object")
    common.require_exact_fields(
        value,
        (
            "schema",
            "process",
            "provenance",
            "rules_calls",
            "verified",
            "child_import_inventory",
        ),
        label="verifier evidence",
    )
    if value["schema"] != VERIFIER_EVIDENCE_SCHEMA:
        raise E00RunnerError("verifier evidence schema differs")
    _validate_process_evidence(value["process"], label="verifier process")
    _validate_native_provenance(value["provenance"], request)
    if _validate_child_import_inventory(
        value["child_import_inventory"],
        label="verifier child import inventory",
    ) != [dict(row) for row in request.child_import_inventory]:
        raise E00RunnerError("verifier child import inventory differs")
    _validate_rules_calls(value["rules_calls"])
    verified = value["verified"]
    expected = {
        "moves": list(execution.moves),
        "result_white": execution.result_white,
        "root_fen": request.pair.root_fen,
        "terminal_reason": execution.terminal_reason,
        "time_loss": execution.time_loss,
        "trajectory_sha256": trajectory_sha256(
            request.pair.root_fen,
            execution.moves,
            execution.result_white,
        ),
    }
    if verified != expected:
        raise E00RunnerError("independent verifier result differs")


def _validate_execution(
    execution: GameExecution,
    request: LegRequest,
    *,
    require_verifier: bool = True,
) -> None:
    if not isinstance(execution, GameExecution):
        raise E00RunnerError("backend returned a non-GameExecution result")
    if execution.result_white not in RESULTS_WHITE:
        raise E00RunnerError("backend returned an invalid White result")
    if not isinstance(execution.time_loss, bool):
        raise E00RunnerError("backend time_loss must be boolean")
    if (
        not isinstance(execution.terminal_reason, str)
        or not execution.terminal_reason
        or any(ord(character) < 32 or ord(character) == 127 for character in execution.terminal_reason)
    ):
        raise E00RunnerError("backend returned an invalid terminal reason")
    time_loss_reasons = {
        "time-loss",
        "time-loss-insufficient-material-draw",
    }
    if execution.time_loss != (execution.terminal_reason in time_loss_reasons):
        raise E00RunnerError(
            "time_loss and its terminal reason must agree exactly"
        )
    if (
        not isinstance(execution.moves, tuple)
        or len(execution.moves) > request.maximum_plies
    ):
        raise E00RunnerError("backend returned an invalid move trajectory")
    if not execution.moves and not execution.time_loss:
        raise E00RunnerError("only a zero-ply flag may have an empty trajectory")
    for index, move in enumerate(execution.moves):
        if not isinstance(move, str) or _UCI_MOVE.fullmatch(move) is None:
            raise E00RunnerError(
                f"backend returned malformed UCI move at ply {index}"
            )
    if not isinstance(execution.stdout, bytes) or not isinstance(
        execution.stderr, bytes
    ):
        raise E00RunnerError("backend transcripts must be exact bytes")
    if execution.stderr:
        raise E00RunnerError("non-empty engine stderr violates E00 policy")
    if not isinstance(execution.engine_evidence, Mapping):
        raise E00RunnerError("backend engine evidence must be an object")
    _validate_engine_evidence(execution.engine_evidence, request)
    _validate_referee_evidence(
        execution.referee_evidence, request, execution
    )
    _validate_process_evidence(
        execution.process_evidence, label="leg process"
    )
    if require_verifier:
        _validate_verifier_evidence(
            execution.verifier_evidence, request, execution
        )
    elif execution.verifier_evidence:
        raise E00RunnerError("backend pre-verification evidence must be empty")


def _leg_roles(leg: int) -> tuple[str, str]:
    if leg == 0:
        return NETWORK_ROLES
    if leg == 1:
        return NETWORK_ROLES[1], NETWORK_ROLES[0]
    raise E00RunnerError("schedule leg must be 0 or 1")


def _game_row(
    pair: SchedulePair,
    leg: int,
    execution: GameExecution,
    *,
    schedule_sha256: str,
    artifacts: Mapping[str, ArtifactBinding],
) -> dict[str, object]:
    white_role, black_role = _leg_roles(leg)
    current_is_white = white_role == "current-v3"
    trajectory = trajectory_sha256(
        pair.root_fen, execution.moves, execution.result_white
    )
    game_id = source_game_id(
        experiment_id=pair.experiment_id,
        battery_id=pair.battery_id,
        schedule_sha256=schedule_sha256,
        pair_id=pair.pair_id,
        leg=leg,
        trajectory_sha256_value=trajectory,
    )
    return {
        "schema": GAME_SCHEMA,
        "experiment_id": pair.experiment_id,
        "battery_id": pair.battery_id,
        "schedule_seed": pair.seed,
        "schedule_sha256": schedule_sha256,
        "pair_id": pair.pair_id,
        "pair_ordinal": pair.pair_ordinal,
        "leg": leg,
        "stratum": pair.stratum,
        "time_control": dict(pair.time_control),
        "book_sha256": pair.book_sha256,
        "book_line": pair.book_line,
        "root_fen": pair.root_fen,
        "root_fen_sha256": pair.root_fen_sha256,
        "engine_sha256": artifacts["engine"].sha256,
        "current_net_sha256": artifacts["current_net"].sha256,
        "teacher_net_sha256": artifacts["teacher_net"].sha256,
        "white_network_role": white_role,
        "black_network_role": black_role,
        "result_white": execution.result_white,
        "result_current": _result_current(
            execution.result_white, current_is_white
        ),
        "time_loss": execution.time_loss,
        "terminal_reason": execution.terminal_reason,
        "moves": list(execution.moves),
        "ply_count": len(execution.moves),
        "trajectory_sha256": trajectory,
        "source_game_id": game_id,
        "stdout_sha256": common.sha256_bytes(execution.stdout),
        "stderr_sha256": common.sha256_bytes(execution.stderr),
        "engine_evidence": dict(execution.engine_evidence),
        "referee_evidence": dict(execution.referee_evidence),
        "process_evidence": dict(execution.process_evidence),
        "verifier_evidence": dict(execution.verifier_evidence),
    }


def _write_pair_staging(
    pair_dir: Path,
    rows: Sequence[dict[str, object]],
    executions: Sequence[GameExecution],
) -> None:
    pair_dir.mkdir(parents=False, exist_ok=False)
    for leg, (row, execution) in enumerate(zip(rows, executions, strict=True)):
        common.write_new_bytes(pair_dir / f"leg-{leg}.stdout.bin", execution.stdout)
        common.write_new_bytes(pair_dir / f"leg-{leg}.stderr.bin", execution.stderr)
        common.write_new_json(pair_dir / f"leg-{leg}.json", row)
    common.write_new_json(
        pair_dir / "pair.json",
        {
            "schema": PAIR_SCHEMA,
            "pair_id": pair_dir.name.split("-", 1)[-1],
            "rows_sha256": common.sha256_bytes(
                b"".join(common.canonical_json_bytes(row) for row in rows)
            ),
            "legs": [0, 1],
        },
    )


def _verify_pair_staging(
    pair_dir: Path,
    rows: Sequence[dict[str, object]],
    executions: Sequence[GameExecution],
) -> None:
    expected_names = {
        "leg-0.json",
        "leg-0.stderr.bin",
        "leg-0.stdout.bin",
        "leg-1.json",
        "leg-1.stderr.bin",
        "leg-1.stdout.bin",
        "pair.json",
    }
    observed_names = {path.name for path in pair_dir.iterdir()}
    if observed_names != expected_names:
        raise E00RunnerError(
            "pair staging contains missing or unexpected artifacts"
        )
    for leg, (row, execution) in enumerate(zip(rows, executions, strict=True)):
        expected_payloads = {
            pair_dir / f"leg-{leg}.json": common.canonical_json_bytes(row),
            pair_dir / f"leg-{leg}.stdout.bin": execution.stdout,
            pair_dir / f"leg-{leg}.stderr.bin": execution.stderr,
        }
        for path, expected in expected_payloads.items():
            observed = common.read_stable_file_bytes(
                path, label=f"pair {leg} staged artifact"
            )
            if observed != expected:
                raise E00RunnerError("pair staging changed before acceptance")


def _inventory_entry(path: Path, root: Path, kind: str) -> dict[str, object]:
    payload = common.read_stable_file_bytes(path, label=f"{kind} artifact")
    return {
        "kind": kind,
        "path": path.relative_to(root).as_posix(),
        "sha256": common.sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _reconcile(
    rows: Sequence[dict[str, object]],
    schedule: ValidatedSchedule,
) -> dict[str, object]:
    expected_games = 2 * len(schedule.pairs)
    if len(rows) != expected_games:
        raise E00RunnerError("accepted row count differs from scheduled games")
    if len({str(row["source_game_id"]) for row in rows}) != len(rows):
        raise E00RunnerError("duplicate source-game ID in accepted rows")

    wins = losses = draws = time_losses = 0
    for pair in schedule.pairs:
        pair_rows = [
            row for row in rows if str(row["pair_id"]) == pair.pair_id
        ]
        if [row["leg"] for row in pair_rows] != [0, 1]:
            raise E00RunnerError("pair does not contain exactly ordered legs 0 and 1")
        if any(row["root_fen"] != pair.root_fen for row in pair_rows):
            raise E00RunnerError("pair legs do not retain the same root")
        roles = [
            (row["white_network_role"], row["black_network_role"])
            for row in pair_rows
        ]
        if roles != [
            ("current-v3", "run3b"),
            ("run3b", "current-v3"),
        ]:
            raise E00RunnerError("pair colour roles are not inverted exactly")
        for row in pair_rows:
            recomputed_trajectory = trajectory_sha256(
                str(row["root_fen"]),
                tuple(str(move) for move in row["moves"]),  # type: ignore[arg-type]
                str(row["result_white"]),
            )
            if row["trajectory_sha256"] != recomputed_trajectory:
                raise E00RunnerError("trajectory SHA-256 does not recompute")
            recomputed_game = source_game_id(
                experiment_id=str(row["experiment_id"]),
                battery_id=str(row["battery_id"]),
                schedule_sha256=str(row["schedule_sha256"]),
                pair_id=str(row["pair_id"]),
                leg=int(row["leg"]),  # type: ignore[arg-type]
                trajectory_sha256_value=recomputed_trajectory,
            )
            if row["source_game_id"] != recomputed_game:
                raise E00RunnerError("source-game ID does not recompute")
            result = row["result_current"]
            wins += int(result == "win")
            losses += int(result == "loss")
            draws += int(result == "draw")
            time_losses += int(bool(row["time_loss"]))
    if wins + losses + draws != expected_games:
        raise E00RunnerError("W/L/D aggregate does not equal accepted row count")
    return {
        "accepted_pairs": len(schedule.pairs),
        "accepted_games": expected_games,
        "wins_current": wins,
        "losses_current": losses,
        "draws": draws,
        "time_losses": time_losses,
        "all_pairs_atomic": True,
        "all_trajectories_legally_replayed": True,
        "all_ids_recomputed": True,
        "zero_extra_duplicate_partial_rows": True,
    }


@_artifact_guard_scoped
def _prepare_battery(
    schedule_path: Path,
    schedule_receipt_path: Path,
    output_dir: Path,
    *,
    experiment_id: str,
    battery_id: str,
    book: Path,
    book_sha256: str,
    engine: Path,
    engine_sha256: str,
    current_net: Path,
    current_net_sha256: str,
    teacher_net: Path,
    teacher_net_sha256: str,
    variant_config: Path,
    variant_config_sha256: str,
    pyffish: Path,
    pyffish_sha256: str,
    pyffish_build_manifest: Path,
    pyffish_build_manifest_sha256: str,
    rules_source_root: Path,
    rules_source_commit: str,
    runner: Path,
    runner_sha256: str,
    tools_init_sha256: str,
    atomic_mining_init_sha256: str,
    common_sha256: str,
    schedule_builder_sha256: str,
    uci_session_sha256: str,
    atomic_outcome_helper_sha256: str,
    binding_builder_sha256: str,
    owned_process_sha256: str,
    runtime_manifest: Path,
    runtime_manifest_sha256: str,
    backend: GameBackend | None = None,
    semantic_verifier: SemanticVerifier | None = None,
    runtime_probe: RuntimeProbe | None = None,
    threads: int = 1,
    maximum_plies: int = 1024,
    command_timeout_seconds: float = 120.0,
    maximum_wall_seconds: float = 14_400.0,
    maximum_game_wall_seconds: float = 1_800.0,
) -> _PreparedBattery:
    """Run and reconcile one schedule without publishing its commit marker."""

    if threads != 1 or isinstance(threads, bool):
        raise E00RunnerError("initial E00 orchestration requires exactly threads=1")
    maximum_plies = _require_int(
        maximum_plies, label="maximum_plies", minimum=1
    )
    command_timeout_seconds = _require_finite_positive(
        command_timeout_seconds, label="command_timeout_seconds"
    )
    maximum_wall_seconds = _require_finite_positive(
        maximum_wall_seconds, label="maximum_wall_seconds"
    )
    maximum_game_wall_seconds = _require_finite_positive(
        maximum_game_wall_seconds, label="maximum_game_wall_seconds"
    )
    output_dir = Path(output_dir).absolute()
    if output_dir.exists():
        raise FileExistsError(f"refusing to reuse E00 output directory: {output_dir}")

    schedule = _schedule_adapter(
        schedule_path,
        schedule_receipt_path,
        experiment_id=experiment_id,
        battery_id=battery_id,
    )
    if not schedule.pairs:
        raise E00RunnerError("E00 schedule must contain at least one pair")
    experiment_ids = {pair.experiment_id for pair in schedule.pairs}
    battery_ids = {pair.battery_id for pair in schedule.pairs}
    if len(experiment_ids) != 1 or len(battery_ids) != 1:
        raise E00RunnerError("schedule must bind one experiment and battery ID")

    wrapper_path = Path(__file__).absolute()
    requested_runner = Path(runner).absolute()
    if requested_runner != wrapper_path:
        raise E00RunnerError("runner must be the executing run_e00_source.py")
    tools_module = sys.modules.get("tools")
    mining_module = sys.modules.get("tools.atomic_mining")
    if tools_module is None or mining_module is None:
        raise E00RunnerError("runtime package initializers are absent")

    def module_path(module: object, *, label: str) -> Path:
        raw = getattr(module, "__file__", None)
        if not isinstance(raw, str) or not raw:
            raise E00RunnerError(f"{label} has no regular source path")
        return Path(raw).absolute()

    source_root = Path(rules_source_root).absolute()
    source_commit = str(rules_source_commit).lower()
    if re.fullmatch(r"[0-9a-f]{40,64}", source_commit) is None:
        raise E00RunnerError("rules source commit is malformed")
    artifact_specs = (
        (
            "atomic_mining_init",
            "atomic mining package initializer",
            module_path(mining_module, label="atomic mining package"),
            atomic_mining_init_sha256,
        ),
        (
            "atomic_outcome_helper",
            "native outcome helper",
            module_path(
                atomic_outcome_helper, label="native outcome helper"
            ),
            atomic_outcome_helper_sha256,
        ),
        (
            "binding_builder",
            "native binding builder",
            module_path(
                build_atomic_outcome_binding,
                label="native binding builder",
            ),
            binding_builder_sha256,
        ),
        ("book", "opening book", book, book_sha256),
        (
            "common",
            "mining common module",
            module_path(common, label="mining common module"),
            common_sha256,
        ),
        ("engine", "engine", engine, engine_sha256),
        (
            "current_net",
            "current network",
            current_net,
            current_net_sha256,
        ),
        (
            "teacher_net",
            "teacher network",
            teacher_net,
            teacher_net_sha256,
        ),
        (
            "variant_config",
            "variant config",
            variant_config,
            variant_config_sha256,
        ),
        (
            "owned_process",
            "owned process module",
            module_path(owned_process, label="owned process module"),
            owned_process_sha256,
        ),
        (
            "pyffish",
            "native pyffish binding",
            pyffish,
            pyffish_sha256,
        ),
        (
            "pyffish_build_manifest",
            "native pyffish build manifest",
            pyffish_build_manifest,
            pyffish_build_manifest_sha256,
        ),
        ("runner", "runner", runner, runner_sha256),
        (
            "schedule",
            "schedule",
            Path(schedule_path),
            schedule.schedule_sha256,
        ),
        (
            "schedule_builder",
            "schedule builder module",
            module_path(build_e00_schedule, label="schedule builder module"),
            schedule_builder_sha256,
        ),
        (
            "schedule_receipt",
            "schedule receipt",
            Path(schedule_receipt_path),
            schedule.receipt_sha256,
        ),
        (
            "tools_init",
            "tools package initializer",
            module_path(tools_module, label="tools package"),
            tools_init_sha256,
        ),
        (
            "uci_session",
            "direct UCI module",
            module_path(uci_session, label="direct UCI module"),
            uci_session_sha256,
        ),
    )
    artifacts: dict[str, ArtifactBinding] = {}
    native_build_artifacts: dict[str, ArtifactBinding] = {}
    snapshots: dict[str, ArtifactBinding] = {}
    executed_modules: dict[str, ArtifactBinding] = {}
    locks_closed = False
    artifacts = _bind_artifact_specs(artifact_specs)
    if artifacts["schedule"].size_bytes != schedule.schedule_size_bytes:
        raise E00RunnerError("schedule size differs from validator snapshot")
    if (
        artifacts["schedule_receipt"].size_bytes
        != schedule.receipt_size_bytes
    ):
        raise E00RunnerError(
            "schedule receipt size differs from validator snapshot"
        )
    if any(pair.book_sha256 != artifacts["book"].sha256 for pair in schedule.pairs):
        raise E00RunnerError("schedule book SHA-256 differs from bound book")
    build_value, _build_wire = _load_canonical_json(
        artifacts["pyffish_build_manifest"].path,
        label="native pyffish build manifest",
    )
    native_rules_runtime = {
        "binding": {
            "path": str(artifacts["pyffish"].path),
            "sha256": artifacts["pyffish"].sha256,
            "size_bytes": artifacts["pyffish"].size_bytes,
        },
        "build_contract": build_value,
        "build_manifest": {
            "path": str(artifacts["pyffish_build_manifest"].path),
            "sha256": artifacts["pyffish_build_manifest"].sha256,
            "size_bytes": artifacts["pyffish_build_manifest"].size_bytes,
        },
        "source_commit": source_commit,
        "source_root": str(source_root),
    }
    atomic_outcome_helper._load_build_manifest(
        artifacts["pyffish_build_manifest"].path,
        artifacts["pyffish_build_manifest"].sha256,
        native_binding={
            "bytes": artifacts["pyffish"].size_bytes,
            "path": str(artifacts["pyffish"].path),
            "sha256": artifacts["pyffish"].sha256,
        },
        source_root=source_root,
        source_commit=source_commit,
        manifest_binding_path=artifacts["pyffish"].path,
    )
    native_build_root = artifacts["pyffish_build_manifest"].path.parent
    try:
        artifacts["pyffish"].path.relative_to(native_build_root)
    except ValueError as error:
        raise E00RunnerError(
            "native pyffish binding is outside its authenticated build root"
        ) from error
    native_build_artifacts, native_build_inventory = (
        _bind_directory_artifacts(
            "native build artifact", native_build_root
        )
    )
    _delegate_parent_locks(
        (
            artifacts["pyffish"],
            artifacts["pyffish_build_manifest"],
        )
    )
    _delegate_parent_locks(native_build_artifacts)
    native_build_namespace = _directory_binding(
        "native build namespace",
        native_build_root,
        strict_static=True,
    )
    _verify_directory_binding(
        native_build_namespace,
        expected_inventory=native_build_inventory,
    )
    uci_options: tuple[tuple[str, object], ...] = (
        ("UCI_Variant", "atomic"),
        ("Threads", 1),
        ("Hash", 512),
        ("MultiPV", 1),
        ("Ponder", False),
        ("SyzygyPath", ""),
        ("SyzygyProbeLimit", 0),
        ("Use NNUE", "true"),
    )
    clock_policy = {
        "charged_interval": (
            "complete-go-write-flush-to-complete-bestmove-newline"
        ),
        "equality": "elapsed-ns-equal-remaining-ns-is-on-time",
        "uci_millisecond_conversion": "floor-nanoseconds",
    }
    expected_execution = {
        "threads": 1,
        "maximum_plies": maximum_plies,
        "command_timeout_seconds": command_timeout_seconds,
        "maximum_wall_seconds": maximum_wall_seconds,
        "maximum_game_wall_seconds": maximum_game_wall_seconds,
        "uci_options": [
            {"name": name, "value": value} for name, value in uci_options
        ],
        "clock_policy": clock_policy,
    }
    expected_inputs = {
        key: binding.sha256 for key, binding in sorted(artifacts.items())
    }
    precommitted_runtime, _runtime_wire = _load_canonical_json(
        runtime_manifest, label="runtime manifest precommit"
    )
    precommitted_runtime_value = precommitted_runtime.get("runtime")
    if not isinstance(precommitted_runtime_value, Mapping):
        raise E00RunnerError("runtime manifest runtime must be an object")
    precommitted_python = precommitted_runtime_value.get("python")
    if not isinstance(precommitted_python, Mapping):
        raise E00RunnerError("runtime manifest Python identity is absent")
    child_import_inventory = _validate_child_import_inventory(
        precommitted_python.get("child_import_inventory"),
        label="runtime manifest child import inventory",
    )
    runtime_probe = runtime_probe or DefaultRuntimeProbe(
        native_rules_runtime,
        child_import_inventory,
    )
    runtime_value, runtime_binding, runtime_before = _runtime_manifest(
        runtime_manifest,
        runtime_manifest_sha256,
        expected_execution=expected_execution,
        expected_inputs=expected_inputs,
        runtime_probe=runtime_probe,
    )
    artifacts["runtime_manifest"] = runtime_binding
    runtime_section = runtime_value["runtime"]
    assert isinstance(runtime_section, Mapping)
    runtime_artifacts = _bind_python_runtime(runtime_section)
    if set(runtime_artifacts).intersection(artifacts):
        _close_binding_sets(runtime_artifacts)
        raise E00RunnerError("Python runtime artifact key collides")
    artifacts.update(runtime_artifacts)
    backend = backend or DirectUciBackend()
    semantic_verifier = semantic_verifier or (
        backend if isinstance(backend, DirectUciBackend) else None
    )
    if semantic_verifier is None:
        raise E00RunnerError(
            "an independent semantic verifier is mandatory"
        )

    output_parent_namespace = _directory_binding(
        "E00 output parent namespace", output_dir.parent
    )
    output_dir.mkdir(parents=False, exist_ok=False)
    output_namespace = _directory_binding(
        "E00 output namespace", output_dir
    )
    snapshots = _snapshot_artifacts(artifacts, output_dir)
    _delegate_parent_locks(snapshots)
    snapshot_namespace = _directory_binding(
        "E00 snapshot namespace",
        output_dir / ".input-snapshots",
        strict_static=True,
    )
    snapshot_inventory = _verify_directory_binding(snapshot_namespace)
    runtime_package_root, executed_modules = _snapshot_runtime_package(
        snapshots, output_dir
    )
    _delegate_parent_locks(executed_modules)
    runtime_namespace = _directory_binding(
        "E00 executed runtime namespace",
        runtime_package_root,
        strict_static=True,
    )
    runtime_inventory = _verify_directory_binding(runtime_namespace)
    _validate_import_inventory_bindings(
        child_import_inventory,
        artifacts=artifacts,
        snapshots=snapshots,
        executed_modules=executed_modules,
    )
    pair_root = output_dir / ".pair-temporaries"
    pair_root.mkdir()
    namespace_bindings = {
        "native_build": native_build_namespace,
        "output_parent": output_parent_namespace,
        "output": output_namespace,
        "snapshots": snapshot_namespace,
        "runtime_package": runtime_namespace,
    }

    def namespace_checkpoint(
        expected_output: Sequence[Mapping[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        _verify_directory_binding(
            native_build_namespace,
            expected_inventory=native_build_inventory,
        )
        output_parent_namespace._guard.verify()
        _verify_directory_binding(
            snapshot_namespace,
            expected_inventory=snapshot_inventory,
        )
        _verify_directory_binding(
            runtime_namespace,
            expected_inventory=runtime_inventory,
        )
        return _verify_directory_binding(
            output_namespace,
            expected_inventory=expected_output,
        )

    accepted: list[dict[str, object]] = []
    battery_started = time.monotonic()
    locks_closed = False
    try:
        for pair in schedule.pairs:
            if time.monotonic() - battery_started >= maximum_wall_seconds:
                raise E00RunnerError(
                    "E00 battery exceeded its frozen maximum wall time"
                )
            pair_dir = pair_root / f"{pair.pair_ordinal:06d}-{pair.pair_id}"
            executions: list[GameExecution] = []
            rows: list[dict[str, object]] = []
            try:
                for leg in (0, 1):
                    if time.monotonic() - battery_started >= maximum_wall_seconds:
                        raise E00RunnerError(
                            "E00 battery exceeded its frozen maximum wall time"
                        )
                    white_role, black_role = _leg_roles(leg)
                    request = LegRequest(
                        pair=pair,
                        leg=leg,
                        white_network_role=white_role,
                        black_network_role=black_role,
                        engine=snapshots["engine"].path,
                        engine_sha256=snapshots["engine"].sha256,
                        current_net=snapshots["current_net"].path,
                        current_net_sha256=snapshots["current_net"].sha256,
                        teacher_net=snapshots["teacher_net"].path,
                        teacher_net_sha256=snapshots["teacher_net"].sha256,
                        book=snapshots["book"].path,
                        book_sha256=snapshots["book"].sha256,
                        variant_config=snapshots["variant_config"].path,
                        variant_config_sha256=(
                            snapshots["variant_config"].sha256
                        ),
                        pyffish=snapshots["pyffish"].path,
                        pyffish_sha256=snapshots["pyffish"].sha256,
                        pyffish_manifest_binding_path=artifacts["pyffish"].path,
                        pyffish_build_manifest=(
                            snapshots["pyffish_build_manifest"].path
                        ),
                        pyffish_build_manifest_sha256=(
                            snapshots["pyffish_build_manifest"].sha256
                        ),
                        source_root=source_root,
                        source_commit=source_commit,
                        helper=executed_modules[
                            "atomic_outcome_helper"
                        ].path,
                        helper_sha256=executed_modules[
                            "atomic_outcome_helper"
                        ].sha256,
                        builder=executed_modules["binding_builder"].path,
                        builder_sha256=executed_modules[
                            "binding_builder"
                        ].sha256,
                        uci_module=executed_modules["uci_session"].path,
                        uci_module_sha256=executed_modules[
                            "uci_session"
                        ].sha256,
                        owned_process_module=executed_modules[
                            "owned_process"
                        ].path,
                        owned_process_module_sha256=executed_modules[
                            "owned_process"
                        ].sha256,
                        runtime_package_root=runtime_package_root,
                        child_import_inventory=tuple(
                            dict(row) for row in child_import_inventory
                        ),
                        uci_options=uci_options,
                        maximum_plies=maximum_plies,
                        command_timeout_seconds=command_timeout_seconds,
                        leg_deadline_seconds=min(
                            maximum_game_wall_seconds,
                            max(
                                0.001,
                                maximum_wall_seconds
                                - (time.monotonic() - battery_started),
                            ),
                        ),
                    )
                    prelaunch_namespace = namespace_checkpoint()
                    execution = backend.play(request)
                    namespace_checkpoint(prelaunch_namespace)
                    if time.monotonic() - battery_started >= maximum_wall_seconds:
                        raise E00RunnerError(
                            "E00 battery exceeded its frozen maximum wall time"
                        )
                    _validate_execution(
                        execution, request, require_verifier=False
                    )
                    preverifier_namespace = namespace_checkpoint()
                    verifier_evidence = semantic_verifier.verify(
                        request, execution
                    )
                    namespace_checkpoint(preverifier_namespace)
                    execution = replace(
                        execution,
                        verifier_evidence=dict(verifier_evidence),
                    )
                    _validate_execution(execution, request)
                    executions.append(execution)
                    rows.append(
                        _game_row(
                            pair,
                            leg,
                            execution,
                            schedule_sha256=schedule.schedule_sha256,
                            artifacts=artifacts,
                        )
                    )
                _write_pair_staging(pair_dir, rows, executions)
                _verify_pair_staging(pair_dir, rows, executions)
                accepted.extend(rows)
                for binding in artifacts.values():
                    _verify_artifact_unchanged(binding)
                for binding in snapshots.values():
                    _verify_artifact_unchanged(binding)
                for binding in executed_modules.values():
                    _verify_artifact_unchanged(binding)
                for binding in native_build_artifacts.values():
                    _verify_artifact_unchanged(binding)
                atomic_outcome_helper._load_build_manifest(
                    artifacts["pyffish_build_manifest"].path,
                    artifacts["pyffish_build_manifest"].sha256,
                    native_binding={
                        "bytes": artifacts["pyffish"].size_bytes,
                        "path": str(artifacts["pyffish"].path),
                        "sha256": artifacts["pyffish"].sha256,
                    },
                    source_root=source_root,
                    source_commit=source_commit,
                    manifest_binding_path=artifacts["pyffish"].path,
                )
            except BaseException as error:
                pair_dir.mkdir(parents=False, exist_ok=pair_dir.exists())
                rejection_path = pair_dir / "rejection.json"
                if not rejection_path.exists():
                    common.write_new_json(
                        rejection_path,
                        {
                            "schema": REJECTION_SCHEMA,
                            "pair_id": pair.pair_id,
                            "pair_ordinal": pair.pair_ordinal,
                            "completed_legs": len(executions),
                            "error_type": type(error).__name__,
                            "error_message": str(error),
                            "retry_performed": False,
                        },
                    )
                raise E00RunnerError(
                    f"E00 pair {pair.pair_ordinal} failed; battery aborted"
                ) from error

        reconciliation = _reconcile(accepted, schedule)
        for binding in artifacts.values():
            _verify_artifact_unchanged(binding)
        for binding in snapshots.values():
            _verify_artifact_unchanged(binding)
        for binding in executed_modules.values():
            _verify_artifact_unchanged(binding)
        for binding in native_build_artifacts.values():
            _verify_artifact_unchanged(binding)
        runtime_after = runtime_probe.capture()
        if dict(runtime_after) != dict(runtime_before):
            raise E00RunnerError("runtime identity changed during E00 execution")
        namespace_checkpoint()
        if time.monotonic() - battery_started >= maximum_wall_seconds:
            raise E00RunnerError(
                "E00 battery exceeded its frozen maximum wall time"
            )
        _close_binding_sets(
            artifacts,
            native_build_artifacts,
            snapshots,
            executed_modules,
        )
        locks_closed = True

        games_path = output_dir / "games.jsonl"
        rejections_path = output_dir / "rejections.jsonl"
        common.write_new_jsonl(games_path, accepted)
        common.write_new_jsonl(rejections_path, ())

        inventory_entries: list[dict[str, object]] = []
        for path, kind in (
            (games_path, "accepted-games"),
            (rejections_path, "rejection-ledger"),
        ):
            inventory_entries.append(_inventory_entry(path, output_dir, kind))
        for pair in schedule.pairs:
            pair_dir = pair_root / f"{pair.pair_ordinal:06d}-{pair.pair_id}"
            for path in sorted(pair_dir.iterdir(), key=lambda item: item.name):
                inventory_entries.append(
                    _inventory_entry(path, output_dir, "pair-staging")
                )
        inventory = {
            "schema": INVENTORY_SCHEMA,
            "entries": inventory_entries,
        }
        inventory_path = output_dir / "inventory.json"
        common.write_new_json(inventory_path, inventory)
        inventory_bytes = common.read_stable_file_bytes(
            inventory_path, label="E00 inventory"
        )
        games_bytes = common.read_stable_file_bytes(
            games_path, label="E00 accepted games"
        )
        rejections_bytes = common.read_stable_file_bytes(
            rejections_path, label="E00 rejection ledger"
        )

        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "committed",
            "experiment_id": next(iter(experiment_ids)),
            "battery_id": next(iter(battery_ids)),
            "schedule": {
                "sha256": schedule.schedule_sha256,
                "size_bytes": schedule.schedule_size_bytes,
                "receipt_sha256": schedule.receipt_sha256,
                "receipt_size_bytes": schedule.receipt_size_bytes,
            },
            "inputs": {
                key: {
                    "path": str(binding.path),
                    "sha256": binding.sha256,
                    "size_bytes": binding.size_bytes,
                    "identity": dict(binding.identity),
                }
                for key, binding in sorted(artifacts.items())
            },
            "input_snapshots": {
                key: {
                    "path": str(binding.path),
                    "sha256": binding.sha256,
                    "size_bytes": binding.size_bytes,
                    "identity": dict(binding.identity),
                }
                for key, binding in sorted(snapshots.items())
            },
            "executed_runtime_package": {
                key: {
                    "path": str(binding.path),
                    "sha256": binding.sha256,
                    "size_bytes": binding.size_bytes,
                    "identity": dict(binding.identity),
                }
                for key, binding in sorted(executed_modules.items())
            },
            "native_build_artifacts": {
                key: {
                    "path": str(binding.path),
                    "sha256": binding.sha256,
                    "size_bytes": binding.size_bytes,
                    "identity": dict(binding.identity),
                }
                for key, binding in sorted(
                    native_build_artifacts.items()
                )
            },
            "runtime": {
                "manifest": runtime_value,
                "observed_pre_and_post_equal": True,
            },
            "namespace_guards": {
                key: {
                    "path": str(binding.path),
                    "identity": dict(binding.identity),
                    "parent_identity": dict(binding.parent_identity),
                    "enumeration": (
                        native_build_inventory
                        if key == "native_build"
                        else snapshot_inventory
                        if key == "snapshots"
                        else runtime_inventory
                        if key == "runtime_package"
                        else None
                    ),
                }
                for key, binding in sorted(namespace_bindings.items())
            },
            "trust_boundary": {
                "hermetic": False,
                "guarded_runtime": (
                    "sys.executable, native Python runtime libraries, "
                    "every precommitted file-backed imported module, "
                    "executed runtime package, pyffish, engine, networks, "
                    "and all other result-bearing file inputs"
                ),
                "child_import_policy": (
                    "canonical exact inventory; no extras, omissions, "
                    "duplicates, identity drift, or late imports"
                ),
                "namespace_policy": (
                    "no-delete-share directory handles, immutable locks for "
                    "every known result-bearing file, exact static "
                    "enumeration checkpoints, and per-child output "
                    "enumeration"
                ),
                "residual_tcb": [
                    "Windows kernel and loader",
                    "Python standard-library code not imported or executed",
                    (
                        "cooperative non-hostile local host; a newly created "
                        "unreferenced namespace entry is detected at the next "
                        "checkpoint but is not kernel-prevented"
                    ),
                    "hardware and firmware",
                ],
            },
            "execution": {
                "threads": 1,
                "retry_policy": "none",
                "pair_order": "schedule-ordinal-then-leg",
                "uci_options": [
                    {"name": name, "value": value}
                    for name, value in uci_options
                ],
                "network_options": {
                    "current-v3": [
                        {
                            "name": "EvalFile",
                            "value": str(snapshots["current_net"].path),
                        }
                    ],
                    "run3b": [
                        {
                            "name": "EvalFile",
                            "value": str(snapshots["teacher_net"].path),
                        }
                    ],
                },
                "maximum_plies": maximum_plies,
                "command_timeout_seconds": command_timeout_seconds,
                "maximum_wall_seconds": maximum_wall_seconds,
                "maximum_game_wall_seconds": maximum_game_wall_seconds,
                "time_loss_rule": (
                    "draw-if-nonflagging-side-has-insufficient-atomic-"
                    "mating-material-v1"
                ),
                "clock_policy": clock_policy,
                "referee_policy": (
                    "fresh-exact-pyffish-root-plus-history-v2"
                ),
                "owned_process_policy": (
                    "windows-job-create-suspended-assign-resume-active-zero-v1"
                ),
                "verifier_policy": (
                    "independent-fresh-pyffish-owned-process-replay-v2"
                ),
            },
            "reconciliation": reconciliation,
            "outputs": {
                "games": {
                    "path": "games.jsonl",
                    "sha256": common.sha256_bytes(games_bytes),
                    "size_bytes": len(games_bytes),
                },
                "rejections": {
                    "path": "rejections.jsonl",
                    "sha256": common.sha256_bytes(rejections_bytes),
                    "size_bytes": len(rejections_bytes),
                },
                "inventory": {
                    "path": "inventory.json",
                    "sha256": common.sha256_bytes(inventory_bytes),
                    "size_bytes": len(inventory_bytes),
                },
            },
        }
        receipt_path = output_dir / "receipt.json"
        receipt_payload = common.canonical_json_bytes(receipt)
        final_namespace = namespace_checkpoint()
        namespace_checkpoint(final_namespace)
        return _PreparedBattery(
            receipt_path=receipt_path,
            receipt_payload=receipt_payload,
            summary=BatterySummary(
                pairs=int(reconciliation["accepted_pairs"]),
                games=int(reconciliation["accepted_games"]),
                wins_current=int(reconciliation["wins_current"]),
                losses_current=int(reconciliation["losses_current"]),
                draws=int(reconciliation["draws"]),
                time_losses=int(reconciliation["time_losses"]),
                games_sha256=common.sha256_bytes(games_bytes),
                receipt_sha256=common.sha256_bytes(receipt_payload),
            ),
        )
    except BaseException:
        # The output directory is intentionally retained for forensic review.
        # In particular, never manufacture a receipt after any failure.
        receipt_path = output_dir / "receipt.json"
        if receipt_path.exists():
            raise E00RunnerError(
                "failed battery unexpectedly contains a commit receipt"
            )
        if not locks_closed:
            try:
                _close_binding_sets(
                    artifacts,
                    native_build_artifacts,
                    snapshots,
                    executed_modules,
                )
                locks_closed = True
            except BaseException as cleanup_error:
                raise E00RunnerError(
                    "failed battery could not release immutable locks"
                ) from cleanup_error
        raise


@wraps(_prepare_battery)
def run_battery(*args: object, **kwargs: object) -> BatterySummary:
    """Publish the sole commit marker only after every guard closes cleanly."""

    prepared = _prepare_battery(*args, **kwargs)
    if not isinstance(prepared, _PreparedBattery):
        raise E00RunnerError("internal E00 battery preparation result is invalid")
    common.write_new_bytes(prepared.receipt_path, prepared.receipt_payload)
    return prepared.summary


def _internal_request_bindings(
    request: LegRequest,
) -> tuple[ArtifactBinding, ...]:
    values = (
        ("engine", "engine", request.engine, request.engine_sha256),
        (
            "current_net",
            "current network",
            request.current_net,
            request.current_net_sha256,
        ),
        (
            "teacher_net",
            "teacher network",
            request.teacher_net,
            request.teacher_net_sha256,
        ),
        ("book", "opening book", request.book, request.book_sha256),
        (
            "variant_config",
            "variant config",
            request.variant_config,
            request.variant_config_sha256,
        ),
        ("pyffish", "pyffish", request.pyffish, request.pyffish_sha256),
        (
            "manifest_pyffish",
            "original manifest pyffish",
            request.pyffish_manifest_binding_path,
            request.pyffish_sha256,
        ),
        (
            "pyffish_build_manifest",
            "pyffish build manifest",
            request.pyffish_build_manifest,
            request.pyffish_build_manifest_sha256,
        ),
        ("helper", "helper", request.helper, request.helper_sha256),
        ("builder", "builder", request.builder, request.builder_sha256),
        (
            "uci_module",
            "UCI module",
            request.uci_module,
            request.uci_module_sha256,
        ),
        (
            "owned_process",
            "owned-process module",
            request.owned_process_module,
            request.owned_process_module_sha256,
        ),
    )
    bindings = _bind_artifact_specs(values)
    return tuple(bindings.values())


def _validate_internal_request_artifacts(request: LegRequest) -> None:
    if request.book_sha256 != request.pair.book_sha256:
        raise E00RunnerError("internal book artifact differs from schedule")
    if re.fullmatch(r"[0-9a-f]{40,64}", request.source_commit) is None:
        raise E00RunnerError("internal source commit is malformed")
    package = request.runtime_package_root.absolute()
    expected_module_paths = {
        "helper": package
        / _RUNTIME_PACKAGE_LAYOUT["atomic_outcome_helper"],
        "builder": package / _RUNTIME_PACKAGE_LAYOUT["binding_builder"],
        "uci_module": package / _RUNTIME_PACKAGE_LAYOUT["uci_session"],
        "owned_process_module": package
        / _RUNTIME_PACKAGE_LAYOUT["owned_process"],
    }
    for field, expected in expected_module_paths.items():
        if getattr(request, field) != expected.absolute():
            raise E00RunnerError(
                f"internal executed module {field} escapes runtime package"
            )
    bindings = _internal_request_bindings(request)
    try:
        for binding in bindings:
            _verify_artifact_unchanged(binding)
    finally:
        _close_binding_sets(bindings)


def _request_wire(request: LegRequest) -> dict[str, object]:
    return {
        "schema": INTERNAL_LEG_REQUEST_SCHEMA,
        "pair": {
            "experiment_id": request.pair.experiment_id,
            "battery_id": request.pair.battery_id,
            "seed": request.pair.seed,
            "pair_id": request.pair.pair_id,
            "pair_ordinal": request.pair.pair_ordinal,
            "stratum": request.pair.stratum,
            "time_control": dict(request.pair.time_control),
            "book_sha256": request.pair.book_sha256,
            "book_line": request.pair.book_line,
            "root_fen": request.pair.root_fen,
            "root_fen_sha256": request.pair.root_fen_sha256,
        },
        "leg": request.leg,
        "white_network_role": request.white_network_role,
        "black_network_role": request.black_network_role,
        "engine": str(request.engine),
        "engine_sha256": request.engine_sha256,
        "current_net": str(request.current_net),
        "current_net_sha256": request.current_net_sha256,
        "teacher_net": str(request.teacher_net),
        "teacher_net_sha256": request.teacher_net_sha256,
        "book": str(request.book),
        "book_sha256": request.book_sha256,
        "variant_config": str(request.variant_config),
        "variant_config_sha256": request.variant_config_sha256,
        "pyffish": str(request.pyffish),
        "pyffish_sha256": request.pyffish_sha256,
        "pyffish_manifest_binding_path": str(
            request.pyffish_manifest_binding_path
        ),
        "pyffish_build_manifest": str(request.pyffish_build_manifest),
        "pyffish_build_manifest_sha256": (
            request.pyffish_build_manifest_sha256
        ),
        "source_root": str(request.source_root),
        "source_commit": request.source_commit,
        "helper": str(request.helper),
        "helper_sha256": request.helper_sha256,
        "builder": str(request.builder),
        "builder_sha256": request.builder_sha256,
        "uci_module": str(request.uci_module),
        "uci_module_sha256": request.uci_module_sha256,
        "owned_process_module": str(request.owned_process_module),
        "owned_process_module_sha256": request.owned_process_module_sha256,
        "runtime_package_root": str(request.runtime_package_root),
        "child_import_inventory": [
            dict(row) for row in request.child_import_inventory
        ],
        "uci_options": [
            {"name": name, "value": value} for name, value in request.uci_options
        ],
        "maximum_plies": request.maximum_plies,
        "command_timeout_seconds": request.command_timeout_seconds,
        "leg_deadline_seconds": request.leg_deadline_seconds,
    }


def _request_from_wire(value: Mapping[str, object]) -> LegRequest:
    common.require_exact_fields(
        value,
        (
            "schema",
            "pair",
            "leg",
            "white_network_role",
            "black_network_role",
            "engine",
            "engine_sha256",
            "current_net",
            "current_net_sha256",
            "teacher_net",
            "teacher_net_sha256",
            "book",
            "book_sha256",
            "variant_config",
            "variant_config_sha256",
            "pyffish",
            "pyffish_sha256",
            "pyffish_manifest_binding_path",
            "pyffish_build_manifest",
            "pyffish_build_manifest_sha256",
            "source_root",
            "source_commit",
            "helper",
            "helper_sha256",
            "builder",
            "builder_sha256",
            "uci_module",
            "uci_module_sha256",
            "owned_process_module",
            "owned_process_module_sha256",
            "runtime_package_root",
            "child_import_inventory",
            "uci_options",
            "maximum_plies",
            "command_timeout_seconds",
            "leg_deadline_seconds",
        ),
        label="internal leg request",
    )
    if value["schema"] != INTERNAL_LEG_REQUEST_SCHEMA:
        raise E00RunnerError("internal leg request schema differs")
    pair_value = value["pair"]
    if not isinstance(pair_value, Mapping):
        raise E00RunnerError("internal leg pair must be an object")
    common.require_exact_fields(
        pair_value,
        (
            "experiment_id",
            "battery_id",
            "seed",
            "pair_id",
            "pair_ordinal",
            "stratum",
            "time_control",
            "book_sha256",
            "book_line",
            "root_fen",
            "root_fen_sha256",
        ),
        label="internal leg pair",
    )
    root_fen = pair_value["root_fen"]
    if not isinstance(root_fen, str):
        raise E00RunnerError("internal leg root FEN must be text")
    common.fen_fields(root_fen)
    roles = (value["white_network_role"], value["black_network_role"])
    leg = _require_int(value["leg"], label="internal leg", minimum=0)
    if leg not in (0, 1) or roles != _leg_roles(leg):
        raise E00RunnerError("internal leg roles differ from color inversion")
    raw_options = value["uci_options"]
    if not isinstance(raw_options, list):
        raise E00RunnerError("internal UCI options must be an array")
    options: list[tuple[str, object]] = []
    for index, raw in enumerate(raw_options):
        if not isinstance(raw, Mapping):
            raise E00RunnerError("internal UCI option must be an object")
        common.require_exact_fields(
            raw, ("name", "value"), label=f"internal UCI option {index}"
        )
        if not isinstance(raw["name"], str):
            raise E00RunnerError("internal UCI option name must be text")
        options.append((raw["name"], raw["value"]))
    expected_options: tuple[tuple[str, object], ...] = (
        ("UCI_Variant", "atomic"),
        ("Threads", 1),
        ("Hash", 512),
        ("MultiPV", 1),
        ("Ponder", False),
        ("SyzygyPath", ""),
        ("SyzygyProbeLimit", 0),
        ("Use NNUE", "true"),
    )
    if tuple(options) != expected_options:
        raise E00RunnerError("internal UCI options differ from frozen contract")

    def path_field(name: str) -> Path:
        raw = value[name]
        if not isinstance(raw, str):
            raise E00RunnerError(f"internal {name} path must be text")
        return Path(raw).absolute()

    pair = SchedulePair(
        experiment_id=_require_clean_id(
            pair_value["experiment_id"], label="internal experiment_id"
        ),
        battery_id=_require_clean_id(
            pair_value["battery_id"], label="internal battery_id"
        ),
        seed=str(pair_value["seed"]),
        pair_id=common.require_lower_hex_sha256(
            pair_value["pair_id"], label="internal pair_id"
        ),
        pair_ordinal=_require_int(
            pair_value["pair_ordinal"],
            label="internal pair ordinal",
            minimum=1,
        ),
        stratum=_require_clean_id(
            pair_value["stratum"], label="internal stratum"
        ),
        time_control=_time_control(
            pair_value["time_control"], label="internal time control"
        ),
        book_sha256=common.require_lower_hex_sha256(
            pair_value["book_sha256"], label="internal book SHA-256"
        ),
        book_line=_require_int(
            pair_value["book_line"], label="internal book line", minimum=1
        ),
        root_fen=root_fen,
        root_fen_sha256=common.require_lower_hex_sha256(
            pair_value["root_fen_sha256"], label="internal root SHA-256"
        ),
    )
    if pair.root_fen_sha256 != common.derive_id(
        "atomic-root-fen-v1", pair.root_fen
    ):
        raise E00RunnerError("internal root hash does not recompute")
    child_import_inventory = _validate_child_import_inventory(
        value["child_import_inventory"],
        label="internal child import inventory",
    )
    request = LegRequest(
        pair=pair,
        leg=leg,
        white_network_role=str(roles[0]),
        black_network_role=str(roles[1]),
        engine=path_field("engine"),
        engine_sha256=common.require_lower_hex_sha256(
            value["engine_sha256"], label="internal engine SHA-256"
        ),
        current_net=path_field("current_net"),
        current_net_sha256=common.require_lower_hex_sha256(
            value["current_net_sha256"],
            label="internal current network SHA-256",
        ),
        teacher_net=path_field("teacher_net"),
        teacher_net_sha256=common.require_lower_hex_sha256(
            value["teacher_net_sha256"],
            label="internal teacher network SHA-256",
        ),
        book=path_field("book"),
        book_sha256=common.require_lower_hex_sha256(
            value["book_sha256"], label="internal book artifact SHA-256"
        ),
        variant_config=path_field("variant_config"),
        variant_config_sha256=common.require_lower_hex_sha256(
            value["variant_config_sha256"],
            label="internal variant config SHA-256",
        ),
        pyffish=path_field("pyffish"),
        pyffish_sha256=common.require_lower_hex_sha256(
            value["pyffish_sha256"], label="internal pyffish SHA-256"
        ),
        pyffish_manifest_binding_path=path_field(
            "pyffish_manifest_binding_path"
        ),
        pyffish_build_manifest=path_field("pyffish_build_manifest"),
        pyffish_build_manifest_sha256=common.require_lower_hex_sha256(
            value["pyffish_build_manifest_sha256"],
            label="internal pyffish build manifest SHA-256",
        ),
        source_root=path_field("source_root"),
        source_commit=str(value["source_commit"]),
        helper=path_field("helper"),
        helper_sha256=common.require_lower_hex_sha256(
            value["helper_sha256"], label="internal helper SHA-256"
        ),
        builder=path_field("builder"),
        builder_sha256=common.require_lower_hex_sha256(
            value["builder_sha256"], label="internal builder SHA-256"
        ),
        uci_module=path_field("uci_module"),
        uci_module_sha256=common.require_lower_hex_sha256(
            value["uci_module_sha256"],
            label="internal UCI module SHA-256",
        ),
        owned_process_module=path_field("owned_process_module"),
        owned_process_module_sha256=common.require_lower_hex_sha256(
            value["owned_process_module_sha256"],
            label="internal owned-process module SHA-256",
        ),
        runtime_package_root=path_field("runtime_package_root"),
        child_import_inventory=tuple(
            dict(row) for row in child_import_inventory
        ),
        uci_options=tuple(options),
        maximum_plies=_require_int(
            value["maximum_plies"], label="internal maximum plies", minimum=1
        ),
        command_timeout_seconds=_require_finite_positive(
            value["command_timeout_seconds"], label="internal command timeout"
        ),
        leg_deadline_seconds=_require_finite_positive(
            value["leg_deadline_seconds"], label="internal leg deadline"
        ),
    )
    _validate_internal_request_artifacts(request)
    return request


def _execution_wire(execution: GameExecution) -> dict[str, object]:
    return {
        "schema": INTERNAL_LEG_RESULT_SCHEMA,
        "result_white": execution.result_white,
        "time_loss": execution.time_loss,
        "terminal_reason": execution.terminal_reason,
        "moves": list(execution.moves),
        "stdout_hex": execution.stdout.hex(),
        "stderr_hex": execution.stderr.hex(),
        "engine_evidence": dict(execution.engine_evidence),
        "referee_evidence": dict(execution.referee_evidence),
    }


def _execution_from_wire(value: Mapping[str, object]) -> GameExecution:
    common.require_exact_fields(
        value,
        (
            "schema",
            "result_white",
            "time_loss",
            "terminal_reason",
            "moves",
            "stdout_hex",
            "stderr_hex",
            "engine_evidence",
            "referee_evidence",
        ),
        label="internal leg result",
    )
    if value["schema"] != INTERNAL_LEG_RESULT_SCHEMA:
        raise E00RunnerError("internal leg result schema differs")
    moves = value["moves"]
    evidence = value["engine_evidence"]
    referee = value["referee_evidence"]
    if not isinstance(moves, list) or not all(
        isinstance(move, str) for move in moves
    ):
        raise E00RunnerError("internal leg moves must be strings")
    if not isinstance(evidence, Mapping):
        raise E00RunnerError("internal engine evidence must be an object")
    if not isinstance(referee, Mapping):
        raise E00RunnerError("internal referee evidence must be an object")
    try:
        stdout = bytes.fromhex(str(value["stdout_hex"]))
        stderr = bytes.fromhex(str(value["stderr_hex"]))
    except ValueError as error:
        raise E00RunnerError("internal transcript hex is malformed") from error
    return GameExecution(
        result_white=str(value["result_white"]),
        time_loss=value["time_loss"],  # type: ignore[arg-type]
        terminal_reason=str(value["terminal_reason"]),
        moves=tuple(moves),
        stdout=stdout,
        stderr=stderr,
        engine_evidence=dict(evidence),
        referee_evidence=dict(referee),
        process_evidence={},
        verifier_evidence={},
    )


def _module_artifact_payload(
    module: object,
    *,
    expected_path: Path,
    expected_sha256: str,
    label: str,
) -> dict[str, object]:
    identity = _module_identity(module, label=label)
    expected = {
        "path": str(expected_path.absolute()),
        "sha256": expected_sha256,
        "size_bytes": expected_path.stat().st_size,
    }
    if identity != expected:
        raise E00RunnerError(f"executed {label} module identity differs")
    return {
        "bytes": expected["size_bytes"],
        "path": expected["path"],
        "sha256": expected["sha256"],
    }


def _native_runtime(
    request: LegRequest,
) -> tuple[object, dict[str, object]]:
    """Load the exact native rules binding and seal all executing modules."""

    _validate_internal_request_artifacts(request)
    builder_artifact = _module_artifact_payload(
        build_atomic_outcome_binding,
        expected_path=request.builder,
        expected_sha256=request.builder_sha256,
        label="binding builder",
    )
    uci_artifact = _module_artifact_payload(
        uci_session,
        expected_path=request.uci_module,
        expected_sha256=request.uci_module_sha256,
        label="UCI session",
    )
    owned_artifact = _module_artifact_payload(
        owned_process,
        expected_path=request.owned_process_module,
        expected_sha256=request.owned_process_module_sha256,
        label="owned process",
    )
    _module_artifact_payload(
        atomic_outcome_helper,
        expected_path=request.helper,
        expected_sha256=request.helper_sha256,
        label="native outcome helper",
    )
    native, native_binding = atomic_outcome_helper._load_native_binding(
        request.pyffish, request.pyffish_sha256
    )
    build_manifest = atomic_outcome_helper._load_build_manifest(
        request.pyffish_build_manifest,
        request.pyffish_build_manifest_sha256,
        native_binding=native_binding,
        source_root=request.source_root,
        source_commit=request.source_commit,
        manifest_binding_path=request.pyffish_manifest_binding_path,
    )
    helper_provenance = atomic_outcome_helper._provenance(
        helper_path=request.helper,
        native_binding=native_binding,
        build_manifest=build_manifest,
        source_root=request.source_root,
        source_commit=request.source_commit,
    )
    provenance = {
        "native": helper_provenance,
        "builder": builder_artifact,
        "uci_session": uci_artifact,
        "owned_process": owned_artifact,
        "manifest_binding_path": str(
            request.pyffish_manifest_binding_path
        ),
    }
    return native, provenance


@_artifact_guard_scoped
def _discover_runtime(
    request: RuntimeDiscoveryRequest,
) -> dict[str, object]:
    """Load the native rules TCB and report imports without engine science."""

    runtime_objects = _runtime_module_objects()
    bindings = _bind_artifact_specs(
        _runtime_discovery_artifact_specs(request)
    )
    try:
        runtime_rows = {
            str(row["key"]): row for row in request.runtime_modules
        }
        for key, module in runtime_objects.items():
            row = runtime_rows[key]
            identity = _module_identity(
                module, label=f"runtime discovery {key}"
            )
            if identity != {
                "path": str(row["path"]),
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
            }:
                raise E00RunnerError(
                    f"executed runtime discovery module {key} differs"
                )
        for key, row in runtime_rows.items():
            binding = bindings[f"runtime_module_{key}"]
            if binding.size_bytes != row["size_bytes"]:
                raise E00RunnerError(
                    f"runtime discovery module {key} size differs"
                )
        native, native_binding = (
            atomic_outcome_helper._load_native_binding(
                request.pyffish, request.pyffish_sha256
            )
        )
        del native
        build_manifest = atomic_outcome_helper._load_build_manifest(
            request.pyffish_build_manifest,
            request.pyffish_build_manifest_sha256,
            native_binding=native_binding,
            source_root=request.source_root,
            source_commit=request.source_commit,
            manifest_binding_path=request.pyffish_manifest_binding_path,
        )
        if not isinstance(build_manifest, Mapping):
            raise E00RunnerError(
                "runtime discovery build manifest validation failed"
            )
        inventory = _file_backed_module_inventory(
            runtime_package_root=request.runtime_package_root,
            pyffish_path=request.pyffish,
        )
        inventory = _validate_discovered_inventory_bindings(
            inventory, request
        )
        result = {
            "schema": INTERNAL_RUNTIME_DISCOVERY_RESULT_SCHEMA,
            "request_sha256": common.sha256_bytes(
                common.canonical_json_bytes(
                    _runtime_discovery_request_wire(request)
                )
            ),
            "runtime_modules_sha256": _digest_document(
                "atomic-e00-runtime-module-inventory-v2",
                [dict(row) for row in request.runtime_modules],
            ),
            "native_validation": {
                "pyffish_path": str(request.pyffish),
                "pyffish_sha256": request.pyffish_sha256,
                "pyffish_size_bytes": bindings[
                    "pyffish"
                ].size_bytes,
                "manifest_binding_path": str(
                    request.pyffish_manifest_binding_path
                ),
                "build_manifest_path": str(
                    request.pyffish_build_manifest
                ),
                "build_manifest_sha256": (
                    request.pyffish_build_manifest_sha256
                ),
                "build_manifest_size_bytes": bindings[
                    "pyffish_build_manifest"
                ].size_bytes,
                "source_root": str(request.source_root),
                "source_commit": request.source_commit,
            },
            "child_import_inventory": inventory,
        }
        if (
            _file_backed_module_inventory(
                runtime_package_root=request.runtime_package_root,
                pyffish_path=request.pyffish,
            )
            != inventory
        ):
            raise E00RunnerError(
                "runtime discovery imported a module after its baseline"
            )
        for binding in bindings.values():
            _verify_artifact_unchanged(binding)
        return result
    finally:
        try:
            for binding in bindings.values():
                _verify_artifact_unchanged(binding)
        finally:
            _close_binding_sets(bindings)


def _rules_call(
    operation: str,
    request: object,
    response: object,
) -> dict[str, object]:
    return {
        "operation": operation,
        "request": request,
        "response": response,
        "request_sha256": common.sha256_bytes(
            common.canonical_json_bytes(request)
        ),
        "response_sha256": common.sha256_bytes(
            common.canonical_json_bytes(response)
        ),
    }


def _native_outcome(
    native: object,
    root_fen: str,
    moves: Sequence[str],
    calls: list[dict[str, object]],
) -> atomic_outcome_helper.AtomicOutcome:
    request = {
        "claim_draw": True,
        "moves": list(moves),
        "root_fen": root_fen,
    }
    outcome = atomic_outcome_helper.evaluate_atomic_outcome(
        native, root_fen, moves, claim_draw=True  # type: ignore[arg-type]
    )
    calls.append(_rules_call("outcome", request, outcome.payload()))
    return outcome


def _native_legal_moves(
    native: object,
    root_fen: str,
    moves: Sequence[str],
    calls: list[dict[str, object]],
) -> tuple[str, ...]:
    function = getattr(native, "legal_moves", None)
    if not callable(function):
        raise E00RunnerError("native binding lacks legal_moves")
    raw = function("atomic", root_fen, list(moves), False)
    if (
        not isinstance(raw, list)
        or any(
            not isinstance(move, str) or _UCI_MOVE.fullmatch(move) is None
            for move in raw
        )
        or len(raw) != len(set(raw))
    ):
        raise E00RunnerError("native legal move set is malformed")
    request = {"moves": list(moves), "root_fen": root_fen}
    response = sorted(raw)
    calls.append(_rules_call("legal-moves", request, response))
    return tuple(response)


def _native_insufficient_material(
    native: object,
    root_fen: str,
    moves: Sequence[str],
    calls: list[dict[str, object]],
) -> tuple[bool, bool]:
    function = getattr(native, "has_insufficient_material", None)
    if not callable(function):
        raise E00RunnerError("native binding lacks has_insufficient_material")
    raw = function("atomic", root_fen, list(moves), False)
    if (
        not isinstance(raw, (tuple, list))
        or len(raw) != 2
        or type(raw[0]) is not bool
        or type(raw[1]) is not bool
    ):
        raise E00RunnerError(
            "native insufficient-material result is malformed"
        )
    response = {"white": raw[0], "black": raw[1]}
    request = {"moves": list(moves), "root_fen": root_fen}
    calls.append(_rules_call("insufficient-material", request, response))
    return raw[0], raw[1]


def _side_to_move_index(root_fen: str, ply: int) -> int:
    _board, side, _castling, _ep, _halfmove, _fullmove = common.fen_fields(
        root_fen
    )
    root = 0 if side == "w" else 1
    return root if ply % 2 == 0 else 1 - root


def _advertised_options(engine: uci_session.UciEngine) -> list[dict[str, object]]:
    return [
        {
            "name": spec.name,
            "kind": spec.kind,
            "default": spec.default,
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "choices": list(spec.choices),
            "raw": spec.raw,
        }
        for spec in sorted(
            engine.option_specs.values(), key=lambda value: value.name.casefold()
        )
    ]


def _uci_settings(
    request: LegRequest,
    *,
    role: str,
    variant_path: bool,
) -> tuple[uci_session.UciOptionSetting, ...]:
    network_paths = {
        "current-v3": request.current_net,
        "run3b": request.teacher_net,
    }
    settings: list[uci_session.UciOptionSetting] = []
    if variant_path:
        settings.append(
            uci_session.UciOptionSetting(
                "VariantPath", str(request.variant_config)
            )
        )
    settings.extend(
        uci_session.UciOptionSetting(name, value)
        for name, value in request.uci_options
    )
    settings.append(
        uci_session.UciOptionSetting(
            "EvalFile", str(network_paths[role])
        )
    )
    return tuple(settings)


def _network_marker(
    raw_lines: Sequence[str],
    *,
    role: str,
    evalfile: Path,
) -> dict[str, object]:
    backend = NETWORK_BACKENDS[role]
    prefix = (
        "info string NNUE evaluation using "
        f"{backend} {evalfile} ("
    )
    candidates = [
        line
        for line in raw_lines
        if line.startswith("info string NNUE evaluation using ")
    ]
    if (
        len(candidates) != 1
        or not candidates[0].startswith(prefix)
        or not candidates[0].endswith(")")
        or any(
            "ERROR:" in line
            or "Classical Atomic evaluation enabled" in line
            for line in raw_lines
        )
    ):
        raise E00RunnerError(
            "go did not prove the exact NNUE backend and EvalFile"
        )
    marker = candidates[0]
    return {
        "backend": backend,
        "evalfile_path": str(evalfile),
        "line": marker,
        "line_sha256": common.sha256_bytes(marker.encode("utf-8")),
        "raw_lines": list(raw_lines),
        "raw_lines_sha256": _digest_document(
            "atomic-e00-uci-go-raw-lines-v2", list(raw_lines)
        ),
    }


@_artifact_guard_scoped
def _play_direct(request: LegRequest) -> GameExecution:  # noqa: C901
    """Play one leg with two persistent direct-UCI engines and native rules."""

    bindings = _internal_request_bindings(request)
    native, provenance = _native_runtime(request)
    child_import_inventory = _assert_child_import_inventory(request)
    calls: list[dict[str, object]] = []
    root_outcome = _native_outcome(
        native, request.pair.root_fen, (), calls
    )
    if root_outcome.terminal:
        raise E00RunnerError("scheduled root is already terminal")

    roles = (request.white_network_role, request.black_network_role)
    engines: list[uci_session.UciEngine] = []
    evidence_entries: list[dict[str, object]] = []
    timing_events: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    variant_policies: list[str] = []
    moves: list[str] = []
    result_white: str | None = None
    terminal_reason: str | None = None
    time_loss = False
    base_ns = int(request.pair.time_control["base_ms"]) * 1_000_000
    increment_ms = int(request.pair.time_control["increment_ms"])
    increment_ns = increment_ms * 1_000_000
    clocks_ns = [base_ns, base_ns]
    network_paths = {
        "current-v3": request.current_net,
        "run3b": request.teacher_net,
    }
    network_hashes = {
        "current-v3": request.current_net_sha256,
        "run3b": request.teacher_net_sha256,
    }

    try:
        with ExitStack() as stack:
            for role in roles:
                engine = stack.enter_context(
                    uci_session.UciEngine(
                        [str(request.engine)],
                        options=(),
                        startup_timeout=request.command_timeout_seconds,
                        command_timeout=request.command_timeout_seconds,
                        shutdown_timeout=min(
                            10.0, request.command_timeout_seconds
                        ),
                    )
                )
                engines.append(engine)
                option_names = {
                    name.casefold() for name in engine.option_specs
                }
                has_variant_path = "variantpath" in option_names
                variant_policies.append(
                    "configured"
                    if has_variant_path
                    else "unavailable-explicit"
                )
                advertised = _advertised_options(engine)
                settings = _uci_settings(
                    request,
                    role=role,
                    variant_path=has_variant_path,
                )
                engine.set_options(
                    settings, timeout=request.command_timeout_seconds
                )
                preflight = engine.search(
                    request.pair.root_fen,
                    nodes=1,
                    timeout=request.command_timeout_seconds,
                )
                if (
                    preflight.bestmove is None
                    or _UCI_MOVE.fullmatch(preflight.bestmove) is None
                ):
                    raise E00RunnerError(
                        "NNUE preflight did not return a move"
                    )
                proof = _network_marker(
                    preflight.raw_lines,
                    role=role,
                    evalfile=network_paths[role],
                )
                engine.new_game(timeout=request.command_timeout_seconds)
                if engine.applied_options != settings:
                    raise E00RunnerError(
                        "direct UCI option acknowledgement vector differs"
                    )
                evidence_entries.append(
                    {
                        "role": role,
                        "id": dict(sorted(engine.engine_ids.items())),
                        "advertised_options": advertised,
                        "advertised_options_sha256": _digest_document(
                            "atomic-e00-advertised-options-v2",
                            advertised,
                        ),
                        "configured_options": [
                            {"name": setting.name, "value": setting.value}
                            for setting in settings
                        ],
                        "network_proof": {
                            **proof,
                            "evalfile_sha256": network_hashes[role],
                            "first_go": "preflight-nodes-1",
                        },
                    }
                )
                diagnostics.append(
                    {
                        "event": "network-preflight",
                        "role": role,
                        "bestmove": preflight.bestmove,
                        "proof": dict(evidence_entries[-1]["network_proof"]),
                    }
                )
            if len(set(variant_policies)) != 1:
                raise E00RunnerError(
                    "identical engine binary disagrees on VariantPath support"
                )

            for ply in range(request.maximum_plies):
                side_index = _side_to_move_index(
                    request.pair.root_fen, len(moves)
                )
                legal = _native_legal_moves(
                    native, request.pair.root_fen, moves, calls
                )
                if not legal:
                    raise E00RunnerError(
                        "native root/history became terminal without outcome"
                    )
                remaining_before_ns = clocks_ns[side_index]
                white_clock_ms = clocks_ns[0] // 1_000_000
                black_clock_ms = clocks_ns[1] // 1_000_000
                play = engines[side_index].play_clocked(
                    request.pair.root_fen,
                    moves=moves,
                    white_clock_ms=white_clock_ms,
                    black_clock_ms=black_clock_ms,
                    white_increment_ms=increment_ms,
                    black_increment_ms=increment_ms,
                    timeout=request.command_timeout_seconds,
                )
                elapsed_ns = play.elapsed_ns
                on_time = elapsed_ns <= remaining_before_ns
                bestmove = play.bestmove
                timed_network_proof = _network_marker(
                    play.raw_lines,
                    role=roles[side_index],
                    evalfile=network_paths[roles[side_index]],
                )
                remaining_after_ns: int | None = None
                if on_time:
                    if (
                        bestmove is None
                        or _UCI_MOVE.fullmatch(bestmove) is None
                        or bestmove not in legal
                    ):
                        raise E00RunnerError(
                            "engine returned no move or an illegal Atomic move"
                        )
                    remaining_after_ns = (
                        remaining_before_ns - elapsed_ns + increment_ns
                    )
                    clocks_ns[side_index] = remaining_after_ns
                    moves.append(bestmove)
                timing_events.append(
                    {
                        "ply": ply,
                        "role": roles[side_index],
                        "side_index": side_index,
                        "remaining_before_ns": remaining_before_ns,
                        "white_clock_ms": white_clock_ms,
                        "black_clock_ms": black_clock_ms,
                        "go_started_ns": play.go_started_ns,
                        "bestmove_completed_ns": play.bestmove_completed_ns,
                        "elapsed_ns": elapsed_ns,
                        "on_time": on_time,
                        "bestmove": bestmove,
                        "move_applied": on_time,
                        "remaining_after_ns": remaining_after_ns,
                        "network_marker": timed_network_proof["line"],
                        "network_marker_sha256": (
                            timed_network_proof["line_sha256"]
                        ),
                        "network_raw_lines_sha256": (
                            timed_network_proof["raw_lines_sha256"]
                        ),
                        "network_raw_lines": timed_network_proof["raw_lines"],
                    }
                )
                diagnostics.append(
                    {
                        "ply": ply,
                        "raw_lines": list(play.raw_lines),
                        "timing": dict(timing_events[-1]),
                    }
                )
                if not on_time:
                    time_loss = True
                    insufficient = _native_insufficient_material(
                        native, request.pair.root_fen, moves, calls
                    )
                    nonflagging = 1 - side_index
                    if insufficient[nonflagging]:
                        result_white = "1/2-1/2"
                        terminal_reason = (
                            "time-loss-insufficient-material-draw"
                        )
                    else:
                        result_white = (
                            "0-1" if side_index == 0 else "1-0"
                        )
                        terminal_reason = "time-loss"
                    break
                outcome = _native_outcome(
                    native, request.pair.root_fen, moves, calls
                )
                if outcome.terminal:
                    result_white = outcome.result_white
                    terminal_reason = outcome.termination
                    break
            else:
                raise E00RunnerError(
                    "game exceeded the frozen maximum ply count"
                )

        if any(engine.stderr for engine in engines):
            raise E00RunnerError("UCI engine emitted non-empty stderr")
        for engine, evidence in zip(
            engines, evidence_entries, strict=True
        ):
            expected_applied = tuple(
                uci_session.UciOptionSetting(
                    row["name"], row["value"]
                )
                for row in evidence["configured_options"]  # type: ignore[index]
            )
            if engine.applied_options != expected_applied:
                raise E00RunnerError(
                    "direct UCI options changed during the game"
                )
        if result_white is None or terminal_reason is None:
            raise E00RunnerError("direct referee finished without a result")
        if _assert_child_import_inventory(request) != child_import_inventory:
            raise E00RunnerError(
                "direct referee imported a module after its frozen baseline"
            )
        for binding in bindings:
            _verify_artifact_unchanged(binding)
        evidence = {
            "schema": ENGINE_EVIDENCE_SCHEMA,
            "variant_path_policy": variant_policies[0],
            "engines": evidence_entries,
        }
        referee = {
            "schema": REFEREE_EVIDENCE_SCHEMA,
            "provenance": provenance,
            "rules_calls": calls,
            "timing_events": timing_events,
            "child_import_inventory": child_import_inventory,
        }
        stdout = b"".join(
            common.canonical_json_bytes(row) + b"\n"
            for row in diagnostics
        )
        return GameExecution(
            result_white=result_white,
            time_loss=time_loss,
            terminal_reason=terminal_reason,
            moves=tuple(moves),
            stdout=stdout,
            stderr=b"",
            engine_evidence=evidence,
            referee_evidence=referee,
            process_evidence={},
            verifier_evidence={},
        )
    finally:
        try:
            for binding in bindings:
                _verify_artifact_unchanged(binding)
        finally:
            _close_binding_sets(bindings)


@_artifact_guard_scoped
def _verify_direct(
    request: LegRequest, execution: GameExecution
) -> dict[str, object]:
    """Replay the accepted trajectory in a fresh native-only child."""

    bindings = _internal_request_bindings(request)
    try:
        native, provenance = _native_runtime(request)
        child_import_inventory = _assert_child_import_inventory(request)
        calls: list[dict[str, object]] = []
        outcome = _native_outcome(
            native, request.pair.root_fen, (), calls
        )
        if outcome.terminal:
            raise E00RunnerError("verifier found a terminal scheduled root")
        replayed: list[str] = []
        for ply, move in enumerate(execution.moves):
            legal = _native_legal_moves(
                native, request.pair.root_fen, replayed, calls
            )
            if move not in legal:
                raise E00RunnerError(
                    f"verifier rejected move at ply {ply}"
                )
            replayed.append(move)
            outcome = _native_outcome(
                native, request.pair.root_fen, replayed, calls
            )
            if outcome.terminal and ply + 1 != len(execution.moves):
                raise E00RunnerError(
                    "verifier found moves after a terminal position"
                )
        if execution.time_loss:
            if outcome.terminal:
                raise E00RunnerError(
                    "verifier found a terminal position before the flag"
                )
            side_index = _side_to_move_index(
                request.pair.root_fen, len(replayed)
            )
            insufficient = _native_insufficient_material(
                native, request.pair.root_fen, replayed, calls
            )
            nonflagging = 1 - side_index
            expected_result = (
                "1/2-1/2"
                if insufficient[nonflagging]
                else ("0-1" if side_index == 0 else "1-0")
            )
            expected_reason = (
                "time-loss-insufficient-material-draw"
                if insufficient[nonflagging]
                else "time-loss"
            )
        else:
            if not outcome.terminal:
                raise E00RunnerError(
                    "verifier found a non-terminal accepted result"
                )
            expected_result = outcome.result_white
            expected_reason = outcome.termination
        if (
            execution.result_white != expected_result
            or execution.terminal_reason != expected_reason
        ):
            raise E00RunnerError(
                "verifier result differs from direct referee"
            )
        verified = {
            "moves": list(execution.moves),
            "result_white": execution.result_white,
            "root_fen": request.pair.root_fen,
            "terminal_reason": execution.terminal_reason,
            "time_loss": execution.time_loss,
            "trajectory_sha256": trajectory_sha256(
                request.pair.root_fen,
                execution.moves,
                execution.result_white,
            ),
        }
        if _assert_child_import_inventory(request) != child_import_inventory:
            raise E00RunnerError(
                "independent verifier imported a module after its "
                "frozen baseline"
            )
        return {
            "schema": VERIFIER_EVIDENCE_SCHEMA,
            "provenance": provenance,
            "rules_calls": calls,
            "verified": verified,
            "child_import_inventory": child_import_inventory,
        }
    finally:
        try:
            for binding in bindings:
                _verify_artifact_unchanged(binding)
        finally:
            _close_binding_sets(bindings)


def _verification_request_wire(
    request: LegRequest, execution: GameExecution
) -> dict[str, object]:
    return {
        "schema": INTERNAL_VERIFY_REQUEST_SCHEMA,
        "request": _request_wire(request),
        "execution": _execution_wire(execution),
    }


def _verification_result_wire(
    evidence: Mapping[str, object],
) -> dict[str, object]:
    common.require_exact_fields(
        evidence,
        (
            "schema",
            "provenance",
            "rules_calls",
            "verified",
            "child_import_inventory",
        ),
        label="internal verifier evidence",
    )
    return {
        "schema": INTERNAL_VERIFY_RESULT_SCHEMA,
        "provenance": evidence["provenance"],
        "rules_calls": evidence["rules_calls"],
        "verified": evidence["verified"],
        "child_import_inventory": evidence["child_import_inventory"],
    }


_ISOLATED_BOOTSTRAP = (
    "import runpy,sys;"
    "root=sys.argv.pop(1);"
    "sys.path.insert(0,root);"
    "sys.argv[0]='tools.atomic_mining.run_e00_source';"
    "runpy.run_module('tools.atomic_mining.run_e00_source',"
    "run_name='__main__',alter_sys=True)"
)


class DirectUciBackend:
    """Owned isolated direct-UCI referee and independent native verifier."""

    def _run_child(
        self,
        *,
        mode: str,
        request_wire: Mapping[str, object],
        result_label: str,
        deadline_seconds: float,
    ) -> tuple[dict[str, object], dict[str, object]]:
        deadline = _require_finite_positive(
            deadline_seconds, label="isolated child deadline"
        )
        with tempfile.TemporaryDirectory(
            prefix="atomic-e00-isolated-"
        ) as directory:
            root = Path(directory)
            request_path = root / "request.json"
            result_path = root / "result.json"
            stdin_path = root / "outer.stdin.bin"
            stdout_path = root / "outer.stdout.bin"
            stderr_path = root / "outer.stderr.bin"
            common.write_new_json(request_path, dict(request_wire))
            common.write_new_bytes(stdin_path, b"")
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            env["PYTHONNOUSERSITE"] = "1"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            runtime_root = (
                request_wire.get("runtime_package_root")
                if "runtime_package_root" in request_wire
                else (
                    request_wire["request"].get("runtime_package_root")
                    if isinstance(request_wire.get("request"), Mapping)
                    else None
                )
            )
            if not isinstance(runtime_root, str):
                raise E00RunnerError(
                    "isolated child runtime package root is absent"
                )
            command = [
                sys.executable,
                "-I",
                "-c",
                _ISOLATED_BOOTSTRAP,
                runtime_root,
                mode,
                str(request_path),
                str(result_path),
            ]
            with stdin_path.open("rb") as stdin_stream, stdout_path.open(
                "wb"
            ) as stdout_stream, stderr_path.open("wb") as stderr_stream:
                owner = owned_process.OwnedProcess.launch(
                    command,
                    stdin=stdin_stream,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    cwd=runtime_root,
                    env=env,
                )
                try:
                    try:
                        return_code = owner.process.wait(timeout=deadline)
                    except (subprocess.TimeoutExpired, TimeoutError) as error:
                        owner.terminate_all(
                            timeout=min(10.0, max(0.001, deadline))
                        )
                        raise E00RunnerError(
                            f"{result_label} exceeded its owned wall deadline"
                        ) from error
                    owner.prove_natural_zero(
                        timeout=min(10.0, max(0.001, deadline))
                    )
                    process_evidence = owner.evidence().payload()
                finally:
                    owner.close()
            outer_stdout = common.read_stable_file_bytes(
                stdout_path, label=f"{result_label} wrapper stdout"
            )
            outer_stderr = common.read_stable_file_bytes(
                stderr_path, label=f"{result_label} wrapper stderr"
            )
            if outer_stdout or outer_stderr:
                raise E00RunnerError(
                    f"{result_label} wrapper emitted output"
                )
            if return_code != 0:
                raise E00RunnerError(
                    f"{result_label} wrapper exited with code {return_code}"
                )
            value, _payload = _load_canonical_json(
                result_path, label=result_label
            )
            return value, process_evidence

    @_artifact_guard_scoped
    def discover_runtime(
        self,
        request_wire: Mapping[str, object],
        *,
        deadline_seconds: float,
    ) -> dict[str, object]:
        """Run the inventory-only child with the production child launcher."""

        request = _runtime_discovery_request_from_wire(request_wire)
        canonical_request = _runtime_discovery_request_wire(request)
        bindings = _bind_artifact_specs(
            _runtime_discovery_artifact_specs(request)
        )
        try:
            value, process = self._run_child(
                mode="--internal-discover-runtime",
                request_wire=canonical_request,
                result_label="runtime discovery result",
                deadline_seconds=deadline_seconds,
            )
            common.require_exact_fields(
                value,
                (
                    "schema",
                    "request_sha256",
                    "runtime_modules_sha256",
                    "native_validation",
                    "child_import_inventory",
                ),
                label="internal runtime discovery result",
            )
            if (
                value["schema"]
                != INTERNAL_RUNTIME_DISCOVERY_RESULT_SCHEMA
                or value["request_sha256"]
                != common.sha256_bytes(
                    common.canonical_json_bytes(canonical_request)
                )
                or value["runtime_modules_sha256"]
                != _digest_document(
                    "atomic-e00-runtime-module-inventory-v2",
                    [dict(row) for row in request.runtime_modules],
                )
            ):
                raise E00RunnerError(
                    "runtime discovery result binding differs"
                )
            expected_native = {
                "pyffish_path": str(request.pyffish),
                "pyffish_sha256": request.pyffish_sha256,
                "pyffish_size_bytes": bindings["pyffish"].size_bytes,
                "manifest_binding_path": str(
                    request.pyffish_manifest_binding_path
                ),
                "build_manifest_path": str(
                    request.pyffish_build_manifest
                ),
                "build_manifest_sha256": (
                    request.pyffish_build_manifest_sha256
                ),
                "build_manifest_size_bytes": bindings[
                    "pyffish_build_manifest"
                ].size_bytes,
                "source_root": str(request.source_root),
                "source_commit": request.source_commit,
            }
            if value["native_validation"] != expected_native:
                raise E00RunnerError(
                    "runtime discovery native validation differs"
                )
            inventory = _validate_discovered_inventory_bindings(
                value["child_import_inventory"],
                request,
            )
            for binding in bindings.values():
                _verify_artifact_unchanged(binding)
            return {
                "schema": RUNTIME_DISCOVERY_RECEIPT_SCHEMA,
                "request": canonical_request,
                "result": {
                    **dict(value),
                    "child_import_inventory": inventory,
                },
                "process": process,
            }
        finally:
            try:
                for binding in bindings.values():
                    _verify_artifact_unchanged(binding)
            finally:
                _close_binding_sets(bindings)

    def play(self, request: LegRequest) -> GameExecution:
        value, process = self._run_child(
            mode="--internal-play-leg",
            request_wire=_request_wire(request),
            result_label="leg result",
            deadline_seconds=request.leg_deadline_seconds,
        )
        execution = _execution_from_wire(value)
        return replace(execution, process_evidence=process)

    def verify(
        self, request: LegRequest, execution: GameExecution
    ) -> Mapping[str, object]:
        value, process = self._run_child(
            mode="--internal-verify-game",
            request_wire=_verification_request_wire(request, execution),
            result_label="verifier result",
            deadline_seconds=request.leg_deadline_seconds,
        )
        common.require_exact_fields(
            value,
            (
                "schema",
                "provenance",
                "rules_calls",
                "verified",
                "child_import_inventory",
            ),
            label="internal verifier result",
        )
        if value["schema"] != INTERNAL_VERIFY_RESULT_SCHEMA:
            raise E00RunnerError("internal verifier result schema differs")
        return {
            "schema": VERIFIER_EVIDENCE_SCHEMA,
            "process": process,
            "provenance": value["provenance"],
            "rules_calls": value["rules_calls"],
            "verified": value["verified"],
            "child_import_inventory": value["child_import_inventory"],
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fixed, paired Atomic E00-SRC battery"
    )
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--schedule-receipt", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--battery-id", required=True)
    parser.add_argument("--runtime-manifest", required=True, type=Path)
    parser.add_argument("--runtime-manifest-sha256", required=True)
    for name in (
        "book",
        "engine",
        "current-net",
        "teacher-net",
        "variant-config",
        "pyffish",
        "pyffish-build-manifest",
        "runner",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--rules-source-root", required=True, type=Path)
    parser.add_argument("--rules-source-commit", required=True)
    for name in (
        "tools-init",
        "atomic-mining-init",
        "common",
        "schedule-builder",
        "uci-session",
        "atomic-outcome-helper",
        "binding-builder",
        "owned-process",
    ):
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--maximum-plies", type=int, default=1024)
    parser.add_argument("--command-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--maximum-wall-seconds", type=float, default=14_400.0)
    parser.add_argument(
        "--maximum-game-wall-seconds", type=float, default=1_800.0
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        summary = run_battery(
            arguments.schedule,
            arguments.schedule_receipt,
            arguments.output_dir,
            experiment_id=arguments.experiment_id,
            battery_id=arguments.battery_id,
            book=arguments.book,
            book_sha256=arguments.book_sha256,
            runtime_manifest=arguments.runtime_manifest,
            runtime_manifest_sha256=arguments.runtime_manifest_sha256,
            engine=arguments.engine,
            engine_sha256=arguments.engine_sha256,
            current_net=arguments.current_net,
            current_net_sha256=arguments.current_net_sha256,
            teacher_net=arguments.teacher_net,
            teacher_net_sha256=arguments.teacher_net_sha256,
            variant_config=arguments.variant_config,
            variant_config_sha256=arguments.variant_config_sha256,
            pyffish=arguments.pyffish,
            pyffish_sha256=arguments.pyffish_sha256,
            pyffish_build_manifest=arguments.pyffish_build_manifest,
            pyffish_build_manifest_sha256=(
                arguments.pyffish_build_manifest_sha256
            ),
            rules_source_root=arguments.rules_source_root,
            rules_source_commit=arguments.rules_source_commit,
            runner=arguments.runner,
            runner_sha256=arguments.runner_sha256,
            tools_init_sha256=arguments.tools_init_sha256,
            atomic_mining_init_sha256=arguments.atomic_mining_init_sha256,
            common_sha256=arguments.common_sha256,
            schedule_builder_sha256=arguments.schedule_builder_sha256,
            uci_session_sha256=arguments.uci_session_sha256,
            atomic_outcome_helper_sha256=(
                arguments.atomic_outcome_helper_sha256
            ),
            binding_builder_sha256=arguments.binding_builder_sha256,
            owned_process_sha256=arguments.owned_process_sha256,
            threads=arguments.threads,
            maximum_plies=arguments.maximum_plies,
            command_timeout_seconds=arguments.command_timeout_seconds,
            maximum_wall_seconds=arguments.maximum_wall_seconds,
            maximum_game_wall_seconds=arguments.maximum_game_wall_seconds,
        )
    except (E00RunnerError, FileExistsError, common.MiningArtifactError) as error:
        print(f"E00-SRC NO-GO: {error}", file=sys.stderr)
        return 2
    print(common.canonical_json_line(summary.__dict__).rstrip())
    return 0


def _internal_play_leg_main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        return 64
    request_path = Path(argv[0])
    result_path = Path(argv[1])
    try:
        value, _payload = _load_canonical_json(
            request_path, label="internal leg request"
        )
        request = _request_from_wire(value)
        execution = _play_direct(request)
        common.write_new_json(result_path, _execution_wire(execution))
        return 0
    except BaseException:
        # The parent records the terminal failure against this exact pair.
        # Never print paths, engine diagnostics or other environment details.
        return 70


def _internal_verify_game_main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        return 64
    request_path = Path(argv[0])
    result_path = Path(argv[1])
    try:
        value, _payload = _load_canonical_json(
            request_path, label="internal verifier request"
        )
        common.require_exact_fields(
            value,
            ("schema", "request", "execution"),
            label="internal verifier request",
        )
        if value["schema"] != INTERNAL_VERIFY_REQUEST_SCHEMA:
            raise E00RunnerError("internal verifier request schema differs")
        request_wire = value["request"]
        execution_wire = value["execution"]
        if not isinstance(request_wire, Mapping) or not isinstance(
            execution_wire, Mapping
        ):
            raise E00RunnerError(
                "internal verifier request payloads must be objects"
            )
        request = _request_from_wire(request_wire)
        execution = _execution_from_wire(execution_wire)
        evidence = _verify_direct(request, execution)
        common.write_new_json(
            result_path, _verification_result_wire(evidence)
        )
        return 0
    except BaseException:
        return 70


def _internal_discover_runtime_main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        return 64
    request_path = Path(argv[0])
    result_path = Path(argv[1])
    try:
        value, _payload = _load_canonical_json(
            request_path, label="internal runtime discovery request"
        )
        request = _runtime_discovery_request_from_wire(value)
        result = _discover_runtime(request)
        common.write_new_json(result_path, result)
        return 0
    except BaseException:
        return 70


def _entrypoint(argv: Sequence[str]) -> int:
    if argv and argv[0] == "--internal-discover-runtime":
        return _internal_discover_runtime_main(argv[1:])
    if argv and argv[0] == "--internal-play-leg":
        return _internal_play_leg_main(argv[1:])
    if argv and argv[0] == "--internal-verify-game":
        return _internal_verify_game_main(argv[1:])
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(_entrypoint(sys.argv[1:]))
