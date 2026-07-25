"""Authenticate committed E00 games and extract every pre-move position.

The E00 execution receipt is the only accepted commit marker.  This consumer
recomputes its complete output/inventory/staging chain, reconciles both legs of
every pair, recomputes trajectory and source-game identities, and then replays
every ply through an injected Atomic-capable engine.  It never deduplicates:
pair, game and trajectory identities remain attached to every emitted row so a
later splitter can enforce leakage isolation explicitly.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Callable, ContextManager, Mapping, Protocol, Sequence

from . import common

if os.name == "nt":  # pragma: no branch - official replay platform
    import ctypes
    from ctypes import wintypes

    _SNAPSHOT_GENERIC_READ = 0x80000000
    _SNAPSHOT_DELETE_ACCESS = 0x00010000
    _SNAPSHOT_FILE_SHARE_READ = 0x00000001
    _SNAPSHOT_FILE_SHARE_DELETE = 0x00000004
    _SNAPSHOT_OPEN_EXISTING = 3
    _SNAPSHOT_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _SNAPSHOT_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _SNAPSHOT_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _SNAPSHOT_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _SNAPSHOT_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
    _SNAPSHOT_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _SNAPSHOT_BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
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

    _snapshot_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _snapshot_kernel32.GetFileAttributesW.argtypes = (wintypes.LPCWSTR,)
    _snapshot_kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _snapshot_kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _snapshot_kernel32.CreateFileW.restype = wintypes.HANDLE
    _snapshot_kernel32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_SNAPSHOT_BY_HANDLE_FILE_INFORMATION),
    )
    _snapshot_kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _snapshot_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _snapshot_kernel32.CloseHandle.restype = wintypes.BOOL


GAME_SCHEMA = "atomic-e00-game-v2"
EXECUTION_RECEIPT_SCHEMA = "atomic-e00-execution-receipt-v2"
INVENTORY_SCHEMA = "atomic-e00-inventory-v1"
PAIR_SCHEMA = "atomic-e00-pair-v1"
POSITION_SCHEMA = "atomic-e00-position-v1"
EXTRACTION_RECEIPT_SCHEMA = "atomic-e00-position-extraction-receipt-v1"
RUNTIME_MANIFEST_SCHEMA = "atomic-e00-runtime-manifest-v2"
ENGINE_EVIDENCE_SCHEMA = "atomic-e00-engine-evidence-v2"
REFEREE_EVIDENCE_SCHEMA = "atomic-e00-native-referee-evidence-v2"
VERIFIER_EVIDENCE_SCHEMA = "atomic-e00-native-verifier-evidence-v2"
OWNED_PROCESS_SCHEMA = "atomic-e00-owned-process-v1"
TRAJECTORY_SCHEMA = "atomic-e00-trajectory-v1"
SOURCE_GAME_SCHEMA = "atomic-e00-source-game-v1"
POSITION_ID_SCHEMA = "atomic-e00-position-id-v1"
POSITION_HISTORY_SCHEMA = "atomic-e00-position-history-v1"
REPLAY_SNAPSHOT_POLICY = (
    "exclusive-content-addressed-create-new-read-only-live-guarded-v2"
)

RESULTS_WHITE = frozenset({"1-0", "0-1", "1/2-1/2"})
RESULTS_ROLE = frozenset({"win", "loss", "draw"})
NETWORK_ROLES = ("current-v3", "run3b")
_UCI_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][nbrq]?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CLEAN_TEXT = re.compile(r"^[^\x00-\x1f\x7f]+$")

_GAME_FIELDS = (
    "schema",
    "experiment_id",
    "battery_id",
    "schedule_seed",
    "schedule_sha256",
    "pair_id",
    "pair_ordinal",
    "leg",
    "stratum",
    "time_control",
    "book_sha256",
    "book_line",
    "root_fen",
    "root_fen_sha256",
    "engine_sha256",
    "current_net_sha256",
    "teacher_net_sha256",
    "white_network_role",
    "black_network_role",
    "result_white",
    "result_current",
    "time_loss",
    "terminal_reason",
    "moves",
    "ply_count",
    "trajectory_sha256",
    "source_game_id",
    "stdout_sha256",
    "stderr_sha256",
    "engine_evidence",
    "referee_evidence",
    "process_evidence",
    "verifier_evidence",
)


class E00ExtractionError(RuntimeError):
    """A committed E00 source or replay violates the extraction contract."""


class InspectionLike(Protocol):
    fen: str
    key: str | None
    checkers: Sequence[str]


class EngineLike(Protocol):
    def inspect_fen(
        self,
        root_fen: str,
        moves: Sequence[str] = (),
        *,
        timeout: float | None = None,
    ) -> InspectionLike: ...

    def legal_moves(
        self,
        root_fen: str,
        moves: Sequence[str] = (),
        *,
        timeout: float | None = None,
    ) -> Sequence[str]: ...


@dataclass(frozen=True)
class ReplayEngineBinding:
    """Immutable identity expected for the engine used during replay.

    Library callers, including synthetic test backends, must inject this
    binding explicitly.  The production CLI derives it from the same
    ``--engine`` path that it launches and requires the independently supplied
    ``--engine-sha256`` value.
    """

    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ReplayEngineObservation:
    payload: bytes
    identity: tuple[int, int, int, int, int]
    mode: int


def _snapshot_windows_handle_identity(
    handle: object, *, label: str, directory: bool
) -> tuple[int, ...]:
    information = _SNAPSHOT_BY_HANDLE_FILE_INFORMATION()
    if not _snapshot_kernel32.GetFileInformationByHandle(
        handle, ctypes.byref(information)
    ):
        raise E00ExtractionError(
            f"cannot read live identity for {label}: "
            f"{ctypes.get_last_error()}"
        )
    base = (
        int(information.dwVolumeSerialNumber),
        (int(information.nFileIndexHigh) << 32)
        | int(information.nFileIndexLow),
    )
    if directory:
        return base
    return (
        *base,
        int(information.nNumberOfLinks),
        (int(information.nFileSizeHigh) << 32)
        | int(information.nFileSizeLow),
        (int(information.ftCreationTime.dwHighDateTime) << 32)
        | int(information.ftCreationTime.dwLowDateTime),
        (int(information.ftLastWriteTime.dwHighDateTime) << 32)
        | int(information.ftLastWriteTime.dwLowDateTime),
    )


def _validate_snapshot_windows_path(path: Path, *, label: str) -> None:
    for candidate in (path, *path.parents):
        attributes = _snapshot_kernel32.GetFileAttributesW(str(candidate))
        if attributes == _SNAPSHOT_INVALID_FILE_ATTRIBUTES:
            raise E00ExtractionError(
                f"cannot authenticate path component for {label}"
            )
        if attributes & _SNAPSHOT_FILE_ATTRIBUTE_REPARSE_POINT:
            raise E00ExtractionError(
                f"{label} path contains a reparse point"
            )


def _open_snapshot_windows_file(path: Path, *, label: str) -> object:
    attributes = _snapshot_kernel32.GetFileAttributesW(str(path))
    if (
        attributes == _SNAPSHOT_INVALID_FILE_ATTRIBUTES
        or attributes & _SNAPSHOT_FILE_ATTRIBUTE_DIRECTORY
        or attributes & _SNAPSHOT_FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise E00ExtractionError(f"{label} is not a plain regular file")
    handle = _snapshot_kernel32.CreateFileW(
        str(path),
        _SNAPSHOT_GENERIC_READ,
        _SNAPSHOT_FILE_SHARE_READ,
        None,
        _SNAPSHOT_OPEN_EXISTING,
        _SNAPSHOT_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _SNAPSHOT_INVALID_HANDLE_VALUE:
        raise E00ExtractionError(
            f"cannot acquire live file guard for {label}: "
            f"{ctypes.get_last_error()}"
        )
    return handle


def _open_snapshot_windows_directory(path: Path, *, label: str) -> object:
    attributes = _snapshot_kernel32.GetFileAttributesW(str(path))
    if (
        attributes == _SNAPSHOT_INVALID_FILE_ATTRIBUTES
        or not attributes & _SNAPSHOT_FILE_ATTRIBUTE_DIRECTORY
        or attributes & _SNAPSHOT_FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise E00ExtractionError(f"{label} is not a plain directory")
    # DELETE desired-access plus no FILE_SHARE_DELETE is a post-construction
    # namespace seal: it prevents rename/delete of this directory and
    # create/delete/replace activity in the sealed namespace while allowing
    # the engine loader to read the already committed executable.
    handle = _snapshot_kernel32.CreateFileW(
        str(path),
        _SNAPSHOT_DELETE_ACCESS,
        _SNAPSHOT_FILE_SHARE_READ,
        None,
        _SNAPSHOT_OPEN_EXISTING,
        (
            _SNAPSHOT_FILE_FLAG_BACKUP_SEMANTICS
            | _SNAPSHOT_FILE_FLAG_OPEN_REPARSE_POINT
        ),
        None,
    )
    if handle == _SNAPSHOT_INVALID_HANDLE_VALUE:
        raise E00ExtractionError(
            f"cannot acquire live namespace seal for {label}: "
            f"{ctypes.get_last_error()}"
        )
    return handle


class _ReplaySnapshotGuard:
    """Live no-write/no-delete guard held through replay clean-close."""

    def __init__(self, directory: Path, executable: Path) -> None:
        if os.name != "nt":  # pragma: no cover - official replay is Windows
            raise E00ExtractionError(
                "official replay snapshot live guards require Windows"
            )
        self.directory = directory.absolute()
        self.executable = executable.absolute()
        self._file_handle: object | None = None
        self._directory_handle: object | None = None
        self._closed = False
        _validate_snapshot_windows_path(
            self.directory, label="replay snapshot directory"
        )
        _validate_snapshot_windows_path(
            self.executable, label="replay snapshot executable"
        )
        executable_metadata = self.executable.lstat()
        if (
            _is_link_or_reparse(executable_metadata)
            or not stat.S_ISREG(executable_metadata.st_mode)
        ):
            raise E00ExtractionError(
                "replay snapshot executable is not a plain regular file"
            )
        self.mode = stat.S_IMODE(executable_metadata.st_mode)
        try:
            self._file_handle = _open_snapshot_windows_file(
                self.executable, label="replay snapshot executable"
            )
            self.file_identity = _snapshot_windows_handle_identity(
                self._file_handle,
                label="replay snapshot executable",
                directory=False,
            )
            self._directory_handle = _open_snapshot_windows_directory(
                self.directory, label="replay snapshot directory"
            )
            self.directory_identity = _snapshot_windows_handle_identity(
                self._directory_handle,
                label="replay snapshot directory",
                directory=True,
            )
            self.verify()
        except BaseException:
            self.close()
            raise

    def verify(self) -> None:
        if (
            self._closed
            or self._file_handle is None
            or self._directory_handle is None
        ):
            raise E00ExtractionError("replay snapshot live guard is closed")
        if (
            _snapshot_windows_handle_identity(
                self._file_handle,
                label="replay snapshot executable",
                directory=False,
            )
            != self.file_identity
            or _snapshot_windows_handle_identity(
                self._directory_handle,
                label="replay snapshot directory",
                directory=True,
            )
            != self.directory_identity
        ):
            raise E00ExtractionError(
                "replay snapshot live identity changed"
            )
        _validate_snapshot_windows_path(
            self.directory, label="replay snapshot directory"
        )
        _validate_snapshot_windows_path(
            self.executable, label="replay snapshot executable"
        )
        metadata = self.executable.lstat()
        if (
            _is_link_or_reparse(metadata)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != self.mode
        ):
            raise E00ExtractionError(
                "replay snapshot executable mode changed"
            )
        file_probe = _open_snapshot_windows_file(
            self.executable, label="replay snapshot executable"
        )
        directory_probe: object | None = None
        close_failed = False
        try:
            if (
                _snapshot_windows_handle_identity(
                    file_probe,
                    label="replay snapshot executable",
                    directory=False,
                )
                != self.file_identity
            ):
                raise E00ExtractionError(
                    "replay snapshot executable path identity changed"
                )
            # A zero-access probe can coexist with the strict DELETE handle;
            # opening a second strict handle would conflict with itself.
            directory_probe = _snapshot_kernel32.CreateFileW(
                str(self.directory),
                0,
                (
                    _SNAPSHOT_FILE_SHARE_READ
                    | _SNAPSHOT_FILE_SHARE_DELETE
                ),
                None,
                _SNAPSHOT_OPEN_EXISTING,
                (
                    _SNAPSHOT_FILE_FLAG_BACKUP_SEMANTICS
                    | _SNAPSHOT_FILE_FLAG_OPEN_REPARSE_POINT
                ),
                None,
            )
            if directory_probe == _SNAPSHOT_INVALID_HANDLE_VALUE:
                raise E00ExtractionError(
                    "cannot reopen replay snapshot directory path"
                )
            if (
                _snapshot_windows_handle_identity(
                    directory_probe,
                    label="replay snapshot directory",
                    directory=True,
                )
                != self.directory_identity
            ):
                raise E00ExtractionError(
                    "replay snapshot directory path identity changed"
                )
            entries = tuple(sorted(row.name for row in self.directory.iterdir()))
            if entries != (self.executable.name,):
                raise E00ExtractionError(
                    "replay snapshot namespace inventory changed"
                )
        finally:
            if not _snapshot_kernel32.CloseHandle(file_probe):
                close_failed = True
            if (
                directory_probe is not None
                and directory_probe != _SNAPSHOT_INVALID_HANDLE_VALUE
                and not _snapshot_kernel32.CloseHandle(directory_probe)
            ):
                close_failed = True
            if close_failed:
                raise E00ExtractionError(
                    "cannot close replay snapshot identity probe"
                )

    def close(self) -> None:
        if self._closed:
            return
        failure = False
        # Release the strict directory seal before the file guard so the
        # subsequent authenticated cleanup can remove the committed namespace.
        for name in ("_directory_handle", "_file_handle"):
            handle = getattr(self, name)
            if (
                handle is not None
                and not _snapshot_kernel32.CloseHandle(handle)
            ):
                failure = True
            setattr(self, name, None)
        self._closed = True
        if failure:
            raise E00ExtractionError(
                "cannot release replay snapshot live guard"
            )

    @property
    def closed(self) -> bool:
        return self._closed

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass


@dataclass(frozen=True)
class ReplayEngineSnapshot:
    original: ReplayEngineBinding
    original_identity: tuple[int, int, int, int, int]
    original_mode: int
    executed: ReplayEngineBinding
    directory: Path
    identity: tuple[int, int, int, int, int]
    mode: int
    read_only_applied: bool
    guard: _ReplaySnapshotGuard


@dataclass(frozen=True)
class AuthenticatedE00:
    root: Path
    receipt: Mapping[str, object]
    receipt_sha256: str
    receipt_size_bytes: int
    games: tuple[dict[str, object], ...]
    games_sha256: str
    games_size_bytes: int
    inventory_sha256: str
    inventory_size_bytes: int
    pairs: int


@dataclass(frozen=True)
class ExtractionSummary:
    pairs: int
    games: int
    positions: int
    source_receipt_sha256: str
    output_sha256: str
    receipt_sha256: str


@dataclass(frozen=True)
class PreparedExtraction:
    source: AuthenticatedE00
    rows: tuple[dict[str, object], ...]
    output_payload: bytes


def _error_from_common(error: common.MiningArtifactError) -> E00ExtractionError:
    return E00ExtractionError(str(error))


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def _read_bound_replay_engine(
    binding: ReplayEngineBinding,
    *,
    phase: str,
    expected_identity: tuple[int, int, int, int, int] | None = None,
) -> ReplayEngineObservation:
    if not isinstance(binding, ReplayEngineBinding):
        raise TypeError("replay_engine_binding must be ReplayEngineBinding")
    path = Path(binding.path).absolute()
    expected_sha256 = _sha(
        binding.sha256, label="replay engine binding SHA-256"
    )
    expected_size = _integer(
        binding.size_bytes, label="replay engine binding size"
    )
    if expected_size < 0:
        raise E00ExtractionError("replay engine binding size must be nonnegative")
    try:
        before = path.lstat()
    except OSError as error:
        raise E00ExtractionError(
            f"cannot inspect replay engine during {phase}: {path}"
        ) from error
    if _is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode):
        raise E00ExtractionError("replay engine must be a non-symlink regular file")
    try:
        payload = common.read_stable_file_bytes(
            path, label=f"replay engine during {phase}"
        )
    except common.MiningArtifactError as error:
        raise _error_from_common(error) from error
    try:
        after = path.lstat()
    except OSError as error:
        raise E00ExtractionError(
            f"cannot re-inspect replay engine during {phase}: {path}"
        ) from error
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if (
        _is_link_or_reparse(after)
        or not stat.S_ISREG(after.st_mode)
        or identity_before != identity_after
    ):
        raise E00ExtractionError(f"replay engine changed during {phase}")
    if expected_identity is not None and identity_after != expected_identity:
        raise E00ExtractionError(f"replay engine identity differs during {phase}")
    if len(payload) != expected_size or common.sha256_bytes(payload) != expected_sha256:
        raise E00ExtractionError(
            f"replay engine hash or size differs during {phase}"
        )
    return ReplayEngineObservation(
        payload=payload,
        identity=identity_after,
        mode=stat.S_IMODE(after.st_mode),
    )


def _create_replay_engine_snapshot(
    original: ReplayEngineBinding,
) -> ReplayEngineSnapshot:
    observation = _read_bound_replay_engine(
        original, phase="original pre-snapshot authentication"
    )
    directory = Path(
        tempfile.mkdtemp(prefix="atomic-e00-replay-snapshot-")
    ).absolute()
    try:
        directory_metadata = directory.lstat()
    except OSError as error:
        raise E00ExtractionError(
            "cannot inspect replay snapshot directory"
        ) from error
    if (
        _is_link_or_reparse(directory_metadata)
        or not stat.S_ISDIR(directory_metadata.st_mode)
    ):
        raise E00ExtractionError("replay snapshot directory is unsafe")
    suffix = Path(original.path).suffix
    snapshot_path = directory / f"engine-{original.sha256}{suffix}"
    guard: _ReplaySnapshotGuard | None = None
    try:
        common.write_new_bytes(snapshot_path, observation.payload)
        executable_bits = observation.mode & (
            stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        desired_mode = (
            observation.mode
            | stat.S_IRUSR
            | executable_bits
        ) & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        os.chmod(snapshot_path, desired_mode)
        executed = ReplayEngineBinding(
            path=snapshot_path,
            sha256=original.sha256,
            size_bytes=original.size_bytes,
        )
        sealed = _read_bound_replay_engine(
            executed, phase="snapshot post-publication authentication"
        )
        read_only = not bool(
            sealed.mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        )
        if not read_only:
            raise E00ExtractionError("replay engine snapshot remains writable")
        guard = _ReplaySnapshotGuard(directory, snapshot_path)
        guard.verify()
        return ReplayEngineSnapshot(
            original=original,
            original_identity=observation.identity,
            original_mode=observation.mode,
            executed=executed,
            directory=directory,
            identity=sealed.identity,
            mode=sealed.mode,
            read_only_applied=True,
            guard=guard,
        )
    except BaseException:
        try:
            if guard is not None:
                guard.close()
            if snapshot_path.exists():
                os.chmod(snapshot_path, stat.S_IREAD | stat.S_IWRITE)
                snapshot_path.unlink()
            directory.rmdir()
        except OSError:
            pass
        raise


def _remove_replay_engine_snapshot(snapshot: ReplayEngineSnapshot) -> None:
    if not snapshot.guard.closed:
        raise E00ExtractionError(
            "replay snapshot live guard remains open before cleanup"
        )
    _read_bound_replay_engine(
        snapshot.executed,
        phase="snapshot pre-removal authentication",
        expected_identity=snapshot.identity,
    )
    try:
        os.chmod(
            snapshot.executed.path,
            snapshot.mode | stat.S_IWUSR,
        )
        snapshot.executed.path.unlink()
        snapshot.directory.rmdir()
    except OSError as error:
        raise E00ExtractionError(
            "could not remove replay snapshot after clean engine close"
        ) from error
    if snapshot.executed.path.exists() or snapshot.directory.exists():
        raise E00ExtractionError(
            "replay snapshot remains after clean engine close"
        )


def _verify_replay_engine_clean_close(engine: object) -> None:
    if getattr(engine, "_closed", None) is not True:
        raise E00ExtractionError("replay engine did not report a clean close")
    process = getattr(engine, "_process", None)
    if process is None or not callable(getattr(process, "poll", None)):
        raise E00ExtractionError("replay engine process identity is unavailable")
    if process.poll() is None:
        raise E00ExtractionError("replay engine process remains alive after close")
    for name in ("_stdout_thread", "_stderr_thread"):
        thread = getattr(engine, name, None)
        if thread is not None and callable(getattr(thread, "is_alive", None)):
            if thread.is_alive():
                raise E00ExtractionError(
                    "replay engine protocol thread remains alive after close"
                )


def _verify_replay_engine_launch_target(
    engine: object,
    snapshot_path: Path,
) -> None:
    command = getattr(engine, "command", None)
    expected = (os.fspath(snapshot_path),)
    if not isinstance(command, (tuple, list)) or tuple(command) != expected:
        raise E00ExtractionError(
            "replay engine command does not target exactly the authenticated snapshot"
        )


def _finalize_replay_engine_snapshot(
    engine: object,
    snapshot: ReplayEngineSnapshot,
) -> None:
    try:
        _verify_replay_engine_clean_close(engine)
        snapshot.guard.verify()
        closed_observation = _read_bound_replay_engine(
            snapshot.executed,
            phase="snapshot post-clean-close authentication",
            expected_identity=snapshot.identity,
        )
        if closed_observation.identity != snapshot.identity:
            raise E00ExtractionError(
                "replay snapshot identity changed before cleanup"
            )
    except BaseException:
        # Verification failures retain the content-addressed snapshot for
        # forensic inspection but release our process-local live handles.
        snapshot.guard.close()
        raise
    snapshot.guard.close()
    _remove_replay_engine_snapshot(snapshot)


def _require_exact(
    value: Mapping[str, object], fields: Sequence[str], *, label: str
) -> None:
    try:
        common.require_exact_fields(value, fields, label=label)
    except common.MiningArtifactError as error:
        raise _error_from_common(error) from error


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise E00ExtractionError(f"{label} must be an object")
    return value


def _array(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise E00ExtractionError(f"{label} must be an array")
    return value


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise E00ExtractionError(f"{label} must be an integer >= {minimum}")
    return value


def _text(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or _CLEAN_TEXT.fullmatch(value) is None
    ):
        raise E00ExtractionError(f"{label} must be clean non-empty text")
    return value


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise E00ExtractionError(f"{label} must be a lowercase SHA-256")
    return value


def _digest(namespace: str, value: object) -> str:
    return common.sha256_bytes(
        common.canonical_json_bytes({"namespace": namespace, "value": value})
    )


def trajectory_sha256(
    root_fen: str, moves: Sequence[str], result_white: str
) -> str:
    return _digest(
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
    return _digest(
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


def _canonical_json(payload: bytes, *, label: str) -> dict[str, object]:
    if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise E00ExtractionError(f"{label} is not canonical UTF-8 JSON")
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise E00ExtractionError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise E00ExtractionError(f"{label} must contain one JSON object")
    try:
        canonical = common.canonical_json_bytes(value)
    except (TypeError, ValueError) as error:
        raise E00ExtractionError(f"{label} is not canonicalizable") from error
    if canonical != payload:
        raise E00ExtractionError(f"{label} is not canonical JSON")
    return value


def _canonical_jsonl(
    payload: bytes, *, label: str
) -> tuple[dict[str, object], ...]:
    if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise E00ExtractionError(f"{label} is not canonical UTF-8 JSONL")
    if payload and not payload.endswith(b"\n"):
        raise E00ExtractionError(f"{label} is missing its final LF")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise E00ExtractionError(f"{label} is invalid UTF-8") from error
    if not payload:
        return ()
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(text[:-1].split("\n"), start=1):
        if not line:
            raise E00ExtractionError(f"{label}:{line_number} is empty")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise E00ExtractionError(
                f"{label}:{line_number} is invalid JSON"
            ) from error
        if not isinstance(value, dict):
            raise E00ExtractionError(
                f"{label}:{line_number} must be an object"
            )
        if common.canonical_json_bytes(value) != (line + "\n").encode("utf-8"):
            raise E00ExtractionError(
                f"{label}:{line_number} is not canonical"
            )
        rows.append(value)
    return tuple(rows)


def _read_bound_file(
    root: Path,
    binding: object,
    *,
    expected_path: str,
    label: str,
) -> tuple[Path, bytes]:
    value = _mapping(binding, label=f"{label} binding")
    _require_exact(value, ("path", "sha256", "size_bytes"), label=f"{label} binding")
    if value["path"] != expected_path:
        raise E00ExtractionError(f"{label} path must be {expected_path!r}")
    expected_sha = _sha(value["sha256"], label=f"{label} SHA-256")
    expected_size = _integer(
        value["size_bytes"], label=f"{label} size_bytes"
    )
    path = root / expected_path
    payload = common.read_stable_file_bytes(path, label=label)
    if common.sha256_bytes(payload) != expected_sha or len(payload) != expected_size:
        raise E00ExtractionError(f"{label} hash or size differs from receipt")
    return path, payload


def _safe_relative(value: object, *, label: str) -> PurePosixPath:
    text = _text(value, label=label)
    if "\\" in text:
        raise E00ExtractionError(f"{label} must use canonical POSIX separators")
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise E00ExtractionError(f"{label} escapes the E00 root")
    return path


def _artifact_binding(
    value: object, *, label: str
) -> tuple[Path, str, int]:
    binding = _mapping(value, label=label)
    _require_exact(binding, ("path", "sha256", "size_bytes"), label=label)
    rendered_path = _text(binding["path"], label=f"{label} path")
    path = Path(rendered_path)
    if not path.is_absolute():
        raise E00ExtractionError(f"{label} path must be absolute")
    return (
        path.absolute(),
        _sha(binding["sha256"], label=f"{label} SHA-256"),
        _integer(binding["size_bytes"], label=f"{label} size"),
    )


def _validate_runtime_artifact(value: object, *, label: str) -> dict[str, object]:
    path, sha256, size_bytes = _artifact_binding(value, label=label)
    return {
        "path": str(path),
        "sha256": sha256,
        "size_bytes": size_bytes,
    }


def _windows_file_identity_wire(path: Path, *, directory: bool) -> dict[str, object]:
    if directory:
        handle = _snapshot_kernel32.CreateFileW(
            str(path),
            0,
            _SNAPSHOT_FILE_SHARE_READ | _SNAPSHOT_FILE_SHARE_DELETE,
            None,
            _SNAPSHOT_OPEN_EXISTING,
            (
                _SNAPSHOT_FILE_FLAG_BACKUP_SEMANTICS
                | _SNAPSHOT_FILE_FLAG_OPEN_REPARSE_POINT
            ),
            None,
        )
    else:
        handle = _snapshot_kernel32.CreateFileW(
            str(path),
            _SNAPSHOT_GENERIC_READ,
            _SNAPSHOT_FILE_SHARE_READ | _SNAPSHOT_FILE_SHARE_DELETE,
            None,
            _SNAPSHOT_OPEN_EXISTING,
            _SNAPSHOT_FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
    if handle == _SNAPSHOT_INVALID_HANDLE_VALUE:
        raise E00ExtractionError(
            f"cannot authenticate file identity for {path}: "
            f"{ctypes.get_last_error()}"
        )
    try:
        values = _snapshot_windows_handle_identity(
            handle, label=str(path), directory=directory
        )
    finally:
        if not _snapshot_kernel32.CloseHandle(handle):
            raise E00ExtractionError(
                f"cannot close identity handle for {path}"
            )
    if directory:
        return {
            "schema": "atomic-e00-windows-directory-identity-v1",
            "volume_serial_number": values[0],
            "file_index": values[1],
        }
    return {
        "schema": "atomic-e00-windows-file-identity-v1",
        "volume_serial_number": values[0],
        "file_index": values[1],
        "number_of_links": values[2],
        "size_bytes": values[3],
        "creation_time_100ns": values[4],
        "last_write_time_100ns": values[5],
    }


def _actual_identity(path: Path, *, directory: bool) -> dict[str, object]:
    requested = path.absolute()
    if os.name == "nt":
        return _windows_file_identity_wire(requested, directory=directory)
    metadata = requested.stat()  # pragma: no cover - official platform Windows
    if directory:
        return {
            "schema": "atomic-e00-posix-directory-identity-v1",
            "device": int(metadata.st_dev),
            "inode": int(metadata.st_ino),
        }
    return {
        "schema": "atomic-e00-posix-file-identity-v1",
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "size_bytes": int(metadata.st_size),
        "ctime_ns": int(metadata.st_ctime_ns),
        "mtime_ns": int(metadata.st_mtime_ns),
    }


def _validate_identity(
    value: object,
    *,
    label: str,
    directory: bool,
    expected_size: int | None = None,
) -> dict[str, object]:
    identity = _mapping(value, label=label)
    schema = identity.get("schema")
    if directory and schema == "atomic-e00-windows-directory-identity-v1":
        fields = ("schema", "volume_serial_number", "file_index")
        integers = fields[1:]
    elif not directory and schema == "atomic-e00-windows-file-identity-v1":
        fields = (
            "schema",
            "volume_serial_number",
            "file_index",
            "number_of_links",
            "size_bytes",
            "creation_time_100ns",
            "last_write_time_100ns",
        )
        integers = fields[1:]
    elif directory and schema == "atomic-e00-posix-directory-identity-v1":
        fields = ("schema", "device", "inode")
        integers = fields[1:]
    elif not directory and schema == "atomic-e00-posix-file-identity-v1":
        fields = (
            "schema",
            "device",
            "inode",
            "size_bytes",
            "ctime_ns",
            "mtime_ns",
        )
        integers = fields[1:]
    else:
        raise E00ExtractionError(f"{label} schema differs")
    _require_exact(identity, fields, label=label)
    for field in integers:
        _integer(identity[field], label=f"{label} {field}")
    if (
        expected_size is not None
        and identity.get("size_bytes") != expected_size
    ):
        raise E00ExtractionError(f"{label} size differs")
    return dict(identity)


def _artifact_binding_v2(
    value: object,
    *,
    label: str,
    authenticate_path: bool,
) -> dict[str, object]:
    binding = _mapping(value, label=label)
    _require_exact(
        binding, ("path", "sha256", "size_bytes", "identity"), label=label
    )
    rendered = _text(binding["path"], label=f"{label} path")
    path = Path(rendered)
    if not path.is_absolute():
        raise E00ExtractionError(f"{label} path must be absolute")
    size = _integer(binding["size_bytes"], label=f"{label} size")
    normalized = {
        "path": str(path.absolute()),
        "sha256": _sha(binding["sha256"], label=f"{label} SHA-256"),
        "size_bytes": size,
        "identity": _validate_identity(
            binding["identity"],
            label=f"{label} identity",
            directory=False,
            expected_size=size,
        ),
    }
    if authenticate_path:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise E00ExtractionError(f"{label} path is absent") from error
        if _is_link_or_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise E00ExtractionError(f"{label} path is not a regular file")
        payload = common.read_stable_file_bytes(path, label=label)
        if (
            len(payload) != size
            or common.sha256_bytes(payload) != normalized["sha256"]
            or _actual_identity(path, directory=False)
            != normalized["identity"]
        ):
            raise E00ExtractionError(
                f"{label} bytes or immutable identity differ"
            )
    return normalized


def _directory_inventory_wire(path: Path) -> list[dict[str, object]]:
    root = path.absolute()
    entries: list[dict[str, object]] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for candidate in sorted(
            directory.iterdir(), key=lambda item: item.name.casefold(), reverse=True
        ):
            metadata = candidate.lstat()
            if _is_link_or_reparse(metadata):
                raise E00ExtractionError(
                    "controlled namespace contains a link or reparse point"
                )
            relative = candidate.relative_to(root).as_posix()
            if stat.S_ISDIR(metadata.st_mode):
                entries.append(
                    {
                        "path": relative,
                        "kind": "directory",
                        "device": int(metadata.st_dev),
                        "inode": int(metadata.st_ino),
                    }
                )
                pending.append(candidate)
            elif stat.S_ISREG(metadata.st_mode):
                entries.append(
                    {
                        "path": relative,
                        "kind": "file",
                        "device": int(metadata.st_dev),
                        "inode": int(metadata.st_ino),
                        "size_bytes": int(metadata.st_size),
                    }
                )
            else:
                raise E00ExtractionError(
                    "controlled namespace contains a non-file entry"
                )
    return sorted(entries, key=lambda row: str(row["path"]))


def _validate_child_import_inventory_v2(
    value: object, *, label: str
) -> list[dict[str, object]]:
    rows = _array(value, label=label)
    if not rows:
        raise E00ExtractionError(f"{label} must be non-empty")
    normalized: list[dict[str, object]] = []
    prior: tuple[str, str] | None = None
    module_names: set[str] = set()
    for index, raw in enumerate(rows):
        row = _mapping(raw, label=f"{label} entry {index}")
        _require_exact(
            row,
            ("source", "path", "sha256", "size_bytes", "module_names"),
            label=f"{label} entry {index}",
        )
        source = row["source"]
        rendered = _text(row["path"], label=f"{label} entry {index} path")
        if source not in {"python-installation", "runtime-package", "pyffish"}:
            raise E00ExtractionError(f"{label} entry source differs")
        if source == "python-installation" and not Path(rendered).is_absolute():
            raise E00ExtractionError(f"{label} Python path is not absolute")
        if source == "runtime-package":
            relative = PurePosixPath(rendered)
            if relative.is_absolute() or any(
                part in {"", ".", ".."} for part in relative.parts
            ):
                raise E00ExtractionError(
                    f"{label} runtime-package path is not canonical"
                )
        if source == "pyffish" and rendered != "pyffish":
            raise E00ExtractionError(f"{label} pyffish token differs")
        key = (str(source), rendered)
        if prior is not None and key <= prior:
            raise E00ExtractionError(
                f"{label} entries are duplicated or unsorted"
            )
        prior = key
        names = _array(
            row["module_names"], label=f"{label} entry {index} module_names"
        )
        if (
            not names
            or not all(
                isinstance(name, str) and name and "\x00" not in name
                for name in names
            )
            or names != sorted(set(names))
            or module_names.intersection(names)
        ):
            raise E00ExtractionError(
                f"{label} module names are malformed or duplicated"
            )
        module_names.update(str(name) for name in names)
        normalized.append(
            {
                "source": source,
                "path": rendered,
                "sha256": _sha(
                    row["sha256"], label=f"{label} entry {index} SHA-256"
                ),
                "size_bytes": _integer(
                    row["size_bytes"], label=f"{label} entry {index} size"
                ),
                "module_names": list(names),
            }
        )
    return normalized


_CORE_INPUT_KEYS = {
    "atomic_mining_init",
    "atomic_outcome_helper",
    "binding_builder",
    "book",
    "common",
    "current_net",
    "engine",
    "owned_process",
    "pyffish",
    "pyffish_build_manifest",
    "runner",
    "schedule",
    "schedule_builder",
    "schedule_receipt",
    "teacher_net",
    "tools_init",
    "uci_session",
    "variant_config",
}

_RUNTIME_LAYOUT = {
    "tools_init": PurePosixPath("tools/__init__.py"),
    "atomic_mining_init": PurePosixPath("tools/atomic_mining/__init__.py"),
    "common": PurePosixPath("tools/atomic_mining/common.py"),
    "schedule_builder": PurePosixPath(
        "tools/atomic_mining/build_e00_schedule.py"
    ),
    "runner": PurePosixPath("tools/atomic_mining/run_e00_source.py"),
    "uci_session": PurePosixPath("tools/atomic_mining/uci_session.py"),
    "atomic_outcome_helper": PurePosixPath(
        "tools/atomic_mining/atomic_outcome_helper.py"
    ),
    "binding_builder": PurePosixPath(
        "tools/atomic_mining/build_atomic_outcome_binding.py"
    ),
    "owned_process": PurePosixPath(
        "tools/atomic_mining/owned_process.py"
    ),
}


def _legacy_runtime_contract_shape_removed(
    root: Path,
    receipt: Mapping[str, object],
) -> None:
    expected_input_keys = {
        "current_net",
        "engine",
        "runner",
        "runtime_manifest",
        "schedule",
        "schedule_receipt",
        "teacher_net",
        "variant_config",
    }
    inputs = _mapping(receipt["inputs"], label="receipt inputs")
    snapshots = _mapping(
        receipt["input_snapshots"], label="receipt input_snapshots"
    )
    if set(inputs) != expected_input_keys or set(snapshots) != expected_input_keys:
        raise E00ExtractionError(
            "receipt inputs and snapshots do not match the exact E00 artifact set"
    )

    snapshot_root = (root / ".input-snapshots").absolute()
    try:
        snapshot_root_metadata = snapshot_root.lstat()
    except OSError as error:
        raise E00ExtractionError(
            "E00 input snapshot directory is absent or unsafe"
        ) from error
    if (
        _is_link_or_reparse(snapshot_root_metadata)
        or not stat.S_ISDIR(snapshot_root_metadata.st_mode)
    ):
        raise E00ExtractionError("E00 input snapshot directory is absent or unsafe")
    snapshot_payloads: dict[str, bytes] = {}
    validated_inputs: dict[str, dict[str, object]] = {}
    for key in sorted(expected_input_keys):
        original_path, original_sha, original_size = _artifact_binding(
            inputs[key], label=f"receipt input {key}"
        )
        snapshot_path, snapshot_sha, snapshot_size = _artifact_binding(
            snapshots[key], label=f"receipt input snapshot {key}"
        )
        if snapshot_sha != original_sha or snapshot_size != original_size:
            raise E00ExtractionError(
                f"receipt input snapshot {key} differs from original binding"
            )
        expected_name = f"{key}-{original_sha}{original_path.suffix}"
        if (
            snapshot_path.parent != snapshot_root
            or snapshot_path.name != expected_name
        ):
            raise E00ExtractionError(
                f"receipt input snapshot {key} path is not content-addressed"
            )
        try:
            before = snapshot_path.lstat()
        except OSError as error:
            raise E00ExtractionError(
                f"cannot inspect receipt input snapshot {key}"
            ) from error
        if _is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode):
            raise E00ExtractionError(
                f"receipt input snapshot {key} is not a regular file"
            )
        try:
            payload = common.read_stable_file_bytes(
                snapshot_path, label=f"receipt input snapshot {key}"
            )
        except common.MiningArtifactError as error:
            raise _error_from_common(error) from error
        if common.sha256_bytes(payload) != snapshot_sha or len(payload) != snapshot_size:
            raise E00ExtractionError(
                f"receipt input snapshot {key} hash or size differs"
            )
        snapshot_payloads[key] = payload
        validated_inputs[key] = {
            "path": str(original_path),
            "sha256": original_sha,
            "size_bytes": original_size,
        }

    runtime_receipt = _mapping(receipt["runtime"], label="receipt runtime")
    _require_exact(
        runtime_receipt,
        ("manifest", "observed_pre_and_post_equal"),
        label="receipt runtime",
    )
    if runtime_receipt["observed_pre_and_post_equal"] is not True:
        raise E00ExtractionError("receipt runtime was not stable across execution")
    runtime_manifest = _mapping(
        runtime_receipt["manifest"], label="receipt runtime manifest"
    )
    parsed_runtime_manifest = _canonical_json(
        snapshot_payloads["runtime_manifest"],
        label="snapshotted runtime manifest",
    )
    if parsed_runtime_manifest != dict(runtime_manifest):
        raise E00ExtractionError(
            "receipt runtime manifest differs from its executed snapshot"
        )
    _require_exact(
        runtime_manifest,
        ("schema", "runtime", "execution", "inputs"),
        label="receipt runtime manifest",
    )
    if runtime_manifest["schema"] != RUNTIME_MANIFEST_SCHEMA:
        raise E00ExtractionError("receipt runtime manifest schema differs")

    manifest_inputs = _mapping(
        runtime_manifest["inputs"], label="runtime manifest inputs"
    )
    expected_manifest_inputs = {
        key: value["sha256"]
        for key, value in validated_inputs.items()
        if key != "runtime_manifest"
    }
    if manifest_inputs != expected_manifest_inputs:
        raise E00ExtractionError(
            "runtime manifest input hashes do not match receipt inputs"
        )

    execution = _mapping(receipt["execution"], label="receipt execution")
    _require_exact(
        execution,
        (
            "threads",
            "retry_policy",
            "pair_order",
            "uci_options",
            "network_options",
            "maximum_plies",
            "command_timeout_seconds",
            "maximum_wall_seconds",
            "maximum_game_wall_seconds",
            "time_loss_rule",
        ),
        label="receipt execution",
    )
    if (
        _integer(execution["threads"], label="receipt execution threads", minimum=1)
        != 1
        or execution["retry_policy"] != "none"
        or execution["pair_order"] != "schedule-ordinal-then-leg"
    ):
        raise E00ExtractionError("receipt execution policy differs")
    for key in ("maximum_plies",):
        _integer(execution[key], label=f"receipt execution {key}", minimum=1)
    for key in (
        "command_timeout_seconds",
        "maximum_wall_seconds",
        "maximum_game_wall_seconds",
    ):
        value = execution[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
            or value != value
            or value in {float("inf"), float("-inf")}
        ):
            raise E00ExtractionError(
                f"receipt execution {key} must be finite and positive"
            )
    manifest_execution = _mapping(
        runtime_manifest["execution"], label="runtime manifest execution"
    )
    expected_manifest_execution = {
        key: execution[key]
        for key in (
            "threads",
            "maximum_plies",
            "command_timeout_seconds",
            "maximum_wall_seconds",
            "maximum_game_wall_seconds",
        )
    }
    if manifest_execution != expected_manifest_execution:
        raise E00ExtractionError(
            "runtime manifest execution differs from committed execution"
        )
    uci_options = _array(execution["uci_options"], label="receipt UCI options")
    expected_uci_options = [
        {"name": "UCI_Variant", "value": "atomic"},
        {"name": "Threads", "value": 1},
        {"name": "Hash", "value": 512},
        {"name": "MultiPV", "value": 1},
        {"name": "Ponder", "value": False},
        {"name": "SyzygyPath", "value": ""},
        {"name": "SyzygyProbeLimit", "value": 0},
        {"name": "Use NNUE", "value": "true"},
    ]
    if uci_options != expected_uci_options:
        raise E00ExtractionError("receipt UCI option vector differs")
    network_options = _mapping(
        execution["network_options"], label="receipt network options"
    )
    if set(network_options) != {"current-v3", "run3b"}:
        raise E00ExtractionError("receipt network option roles differ")
    for role, input_key in (
        ("current-v3", "current_net"),
        ("run3b", "teacher_net"),
    ):
        if network_options[role] != [
            {
                "name": "EvalFile",
                "value": validated_inputs[input_key]["path"],
            }
        ]:
            raise E00ExtractionError(
                f"receipt network options for {role} differ"
            )
    if execution["time_loss_rule"] != (
        "draw-if-nonflagging-side-has-insufficient-atomic-mating-material-v1"
    ):
        raise E00ExtractionError("receipt time-loss policy differs")

    runtime = _mapping(
        runtime_manifest["runtime"], label="runtime manifest runtime"
    )
    _require_exact(
        runtime,
        ("python", "chess", "modules", "source"),
        label="runtime manifest runtime",
    )
    python = _mapping(runtime["python"], label="runtime Python")
    _require_exact(
        python,
        (
            "executable",
            "executable_sha256",
            "executable_size_bytes",
            "version",
        ),
        label="runtime Python",
    )
    if not Path(_text(python["executable"], label="runtime Python executable")).is_absolute():
        raise E00ExtractionError("runtime Python executable must be absolute")
    _sha(python["executable_sha256"], label="runtime Python executable SHA-256")
    _integer(
        python["executable_size_bytes"],
        label="runtime Python executable size",
        minimum=1,
    )
    _text(python["version"], label="runtime Python version")

    chess = _mapping(runtime["chess"], label="runtime chess")
    _require_exact(
        chess, ("version", "module", "package_tree"), label="runtime chess"
    )
    _text(chess["version"], label="runtime chess version")
    _validate_runtime_artifact(chess["module"], label="runtime chess module")
    package_tree = _mapping(
        chess["package_tree"], label="runtime chess package tree"
    )
    _require_exact(
        package_tree, ("root", "sha256", "file_count"), label="runtime chess package tree"
    )
    if not Path(_text(package_tree["root"], label="runtime chess package root")).is_absolute():
        raise E00ExtractionError("runtime chess package root must be absolute")
    _sha(package_tree["sha256"], label="runtime chess package SHA-256")
    _integer(
        package_tree["file_count"],
        label="runtime chess package file count",
        minimum=1,
    )

    modules = _mapping(runtime["modules"], label="runtime modules")
    if set(modules) != {"common", "schedule", "wrapper"}:
        raise E00ExtractionError("runtime module set differs")
    module_bindings = {
        key: _validate_runtime_artifact(
            modules[key], label=f"runtime module {key}"
        )
        for key in sorted(modules)
    }
    if module_bindings["wrapper"] != validated_inputs["runner"]:
        raise E00ExtractionError(
            "runtime wrapper identity differs from runner input"
        )
    source = _mapping(runtime["source"], label="runtime source")
    _require_exact(
        source, ("root", "commit", "tree", "clean"), label="runtime source"
    )
    if not Path(_text(source["root"], label="runtime source root")).is_absolute():
        raise E00ExtractionError("runtime source root must be absolute")
    for key in ("commit", "tree"):
        value = source[key]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise E00ExtractionError(f"runtime source {key} is malformed")
    if source["clean"] is not True:
        raise E00ExtractionError("runtime source was not clean")


def _validate_runtime_contract(
    root: Path,
    receipt: Mapping[str, object],
) -> None:
    inputs_raw = _mapping(receipt["inputs"], label="receipt inputs")
    input_keys = set(inputs_raw)
    if not _CORE_INPUT_KEYS.issubset(input_keys) or "runtime_manifest" not in input_keys:
        raise E00ExtractionError("receipt v2 core input set is incomplete")
    library_keys = sorted(
        key for key in input_keys if key.startswith("python_runtime_library_")
    )
    imported_keys = sorted(
        key for key in input_keys if key.startswith("python_imported_module_")
    )
    allowed = (
        _CORE_INPUT_KEYS
        | {"runtime_manifest", "python_executable"}
        | set(library_keys)
        | set(imported_keys)
    )
    if input_keys != allowed or "python_executable" not in input_keys:
        raise E00ExtractionError("receipt v2 input key grammar differs")
    for prefix, keys in (
        ("python_runtime_library_", library_keys),
        ("python_imported_module_", imported_keys),
    ):
        if keys != [f"{prefix}{index:03d}" for index in range(1, len(keys) + 1)]:
            raise E00ExtractionError(
                f"receipt {prefix} keys are not contiguous"
            )
    if not library_keys:
        raise E00ExtractionError("receipt has no Python runtime library")

    inputs = {
        key: _artifact_binding_v2(
            inputs_raw[key],
            label=f"receipt input {key}",
            authenticate_path=False,
        )
        for key in sorted(input_keys)
    }
    snapshots_raw = _mapping(
        receipt["input_snapshots"], label="receipt input_snapshots"
    )
    if set(snapshots_raw) != input_keys:
        raise E00ExtractionError(
            "receipt snapshots do not exactly mirror v2 inputs"
        )
    snapshot_root = (root / ".input-snapshots").absolute()
    if (
        not snapshot_root.is_dir()
        or snapshot_root.is_symlink()
        or _is_link_or_reparse(snapshot_root.lstat())
    ):
        raise E00ExtractionError("E00 input snapshot directory is unsafe")
    snapshots: dict[str, dict[str, object]] = {}
    snapshot_payloads: dict[str, bytes] = {}
    for key in sorted(input_keys):
        snapshot = _artifact_binding_v2(
            snapshots_raw[key],
            label=f"receipt input snapshot {key}",
            authenticate_path=True,
        )
        original = inputs[key]
        original_path = Path(str(original["path"]))
        expected_path = snapshot_root / (
            f"{key}-{original['sha256']}{original_path.suffix}"
        )
        if (
            snapshot["path"] != str(expected_path)
            or snapshot["sha256"] != original["sha256"]
            or snapshot["size_bytes"] != original["size_bytes"]
        ):
            raise E00ExtractionError(
                f"receipt input snapshot {key} is not exact/content-addressed"
            )
        metadata = expected_path.lstat()
        if metadata.st_mode & (
            stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
        ):
            raise E00ExtractionError(
                f"receipt input snapshot {key} remains writable"
            )
        snapshots[key] = snapshot
        snapshot_payloads[key] = common.read_stable_file_bytes(
            expected_path, label=f"receipt input snapshot {key}"
        )

    runtime_receipt = _mapping(receipt["runtime"], label="receipt runtime")
    _require_exact(
        runtime_receipt,
        ("manifest", "observed_pre_and_post_equal"),
        label="receipt runtime",
    )
    if runtime_receipt["observed_pre_and_post_equal"] is not True:
        raise E00ExtractionError("receipt runtime was not stable")
    runtime_manifest = _mapping(
        runtime_receipt["manifest"], label="receipt runtime manifest"
    )
    parsed_manifest = _canonical_json(
        snapshot_payloads["runtime_manifest"],
        label="snapshotted runtime manifest",
    )
    if parsed_manifest != dict(runtime_manifest):
        raise E00ExtractionError(
            "receipt runtime manifest differs from its snapshot"
        )
    _require_exact(
        runtime_manifest,
        ("schema", "runtime", "execution", "inputs"),
        label="receipt runtime manifest",
    )
    if runtime_manifest["schema"] != RUNTIME_MANIFEST_SCHEMA:
        raise E00ExtractionError(
            "receipt requires runtime-manifest-v2 exactly"
        )
    manifest_inputs = _mapping(
        runtime_manifest["inputs"], label="runtime manifest inputs"
    )
    if manifest_inputs != {
        key: inputs[key]["sha256"] for key in sorted(_CORE_INPUT_KEYS)
    }:
        raise E00ExtractionError(
            "runtime manifest does not bind the exact v2 core inputs"
        )

    executed_raw = _mapping(
        receipt["executed_runtime_package"],
        label="receipt executed_runtime_package",
    )
    if set(executed_raw) != set(_RUNTIME_LAYOUT):
        raise E00ExtractionError("executed runtime package layout differs")
    runtime_root = (root / ".runtime-package").absolute()
    executed: dict[str, dict[str, object]] = {}
    for key, relative in sorted(_RUNTIME_LAYOUT.items()):
        binding = _artifact_binding_v2(
            executed_raw[key],
            label=f"executed runtime module {key}",
            authenticate_path=True,
        )
        expected_path = runtime_root.joinpath(*relative.parts)
        if binding["path"] != str(expected_path):
            raise E00ExtractionError(
                f"executed runtime module {key} path differs"
            )
        source = snapshots[key]
        if (
            binding["sha256"] != source["sha256"]
            or binding["size_bytes"] != source["size_bytes"]
        ):
            raise E00ExtractionError(
                f"executed runtime module {key} differs from snapshot"
            )
        executed[key] = binding

    native_raw = _mapping(
        receipt["native_build_artifacts"],
        label="receipt native_build_artifacts",
    )
    if not native_raw:
        raise E00ExtractionError("native build artifact set is empty")
    native_root = Path(str(inputs["pyffish_build_manifest"]["path"])).parent
    native_artifacts: dict[str, dict[str, object]] = {}
    for relative, raw in sorted(native_raw.items()):
        safe = _safe_relative(relative, label="native build artifact key")
        binding = _artifact_binding_v2(
            raw,
            label=f"native build artifact {relative}",
            authenticate_path=True,
        )
        if binding["path"] != str(native_root.joinpath(*safe.parts)):
            raise E00ExtractionError(
                f"native build artifact {relative} path differs"
            )
        native_artifacts[relative] = binding
    for key in ("pyffish", "pyffish_build_manifest"):
        original = inputs[key]
        expected_relative = (
            Path(str(original["path"])).relative_to(native_root).as_posix()
        )
        if (
            expected_relative not in native_artifacts
            or native_artifacts[expected_relative] != original
        ):
            raise E00ExtractionError(
                f"native build does not bind core artifact {key}"
            )

    execution = _mapping(receipt["execution"], label="receipt execution")
    execution_fields = (
        "threads",
        "retry_policy",
        "pair_order",
        "uci_options",
        "network_options",
        "maximum_plies",
        "command_timeout_seconds",
        "maximum_wall_seconds",
        "maximum_game_wall_seconds",
        "time_loss_rule",
        "clock_policy",
        "referee_policy",
        "owned_process_policy",
        "verifier_policy",
    )
    _require_exact(execution, execution_fields, label="receipt execution")
    if (
        _integer(execution["threads"], label="execution threads", minimum=1) != 1
        or execution["retry_policy"] != "none"
        or execution["pair_order"] != "schedule-ordinal-then-leg"
        or execution["time_loss_rule"]
        != (
            "draw-if-nonflagging-side-has-insufficient-atomic-"
            "mating-material-v1"
        )
        or execution["referee_policy"]
        != "fresh-exact-pyffish-root-plus-history-v2"
        or execution["owned_process_policy"]
        != "windows-job-create-suspended-assign-resume-active-zero-v1"
        or execution["verifier_policy"]
        != "independent-fresh-pyffish-owned-process-replay-v2"
    ):
        raise E00ExtractionError("receipt v2 execution policy differs")
    for field in ("maximum_plies",):
        _integer(execution[field], label=f"execution {field}", minimum=1)
    for field in (
        "command_timeout_seconds",
        "maximum_wall_seconds",
        "maximum_game_wall_seconds",
    ):
        value = execution[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 < value < float("inf")
        ):
            raise E00ExtractionError(f"execution {field} is not finite/positive")
    expected_uci = [
        {"name": "UCI_Variant", "value": "atomic"},
        {"name": "Threads", "value": 1},
        {"name": "Hash", "value": 512},
        {"name": "MultiPV", "value": 1},
        {"name": "Ponder", "value": False},
        {"name": "SyzygyPath", "value": ""},
        {"name": "SyzygyProbeLimit", "value": 0},
        {"name": "Use NNUE", "value": "true"},
    ]
    if execution["uci_options"] != expected_uci:
        raise E00ExtractionError("receipt UCI option vector differs")
    clock_policy = _mapping(
        execution["clock_policy"], label="receipt clock policy"
    )
    expected_clock_policy = {
        "charged_interval": (
            "complete-go-write-flush-to-complete-bestmove-newline"
        ),
        "equality": "elapsed-ns-equal-remaining-ns-is-on-time",
        "uci_millisecond_conversion": "floor-nanoseconds",
    }
    if clock_policy != expected_clock_policy:
        raise E00ExtractionError("receipt clock policy differs")
    network_options = _mapping(
        execution["network_options"], label="receipt network options"
    )
    if network_options != {
        "current-v3": [
            {"name": "EvalFile", "value": snapshots["current_net"]["path"]}
        ],
        "run3b": [
            {"name": "EvalFile", "value": snapshots["teacher_net"]["path"]}
        ],
    }:
        raise E00ExtractionError("receipt network options differ from snapshots")
    expected_manifest_execution = {
        "threads": 1,
        "maximum_plies": execution["maximum_plies"],
        "command_timeout_seconds": execution["command_timeout_seconds"],
        "maximum_wall_seconds": execution["maximum_wall_seconds"],
        "maximum_game_wall_seconds": execution["maximum_game_wall_seconds"],
        "uci_options": expected_uci,
        "clock_policy": expected_clock_policy,
    }
    if runtime_manifest["execution"] != expected_manifest_execution:
        raise E00ExtractionError(
            "runtime manifest execution differs from receipt"
        )

    runtime = _mapping(
        runtime_manifest["runtime"], label="runtime manifest runtime"
    )
    _require_exact(
        runtime,
        ("python", "modules", "native_rules", "source"),
        label="runtime manifest runtime",
    )
    python = _mapping(runtime["python"], label="runtime Python")
    _require_exact(
        python,
        (
            "executable",
            "executable_sha256",
            "executable_size_bytes",
            "runtime_libraries",
            "child_import_inventory",
            "version",
        ),
        label="runtime Python",
    )
    if (
        python["executable"] != inputs["python_executable"]["path"]
        or python["executable_sha256"]
        != inputs["python_executable"]["sha256"]
        or python["executable_size_bytes"]
        != inputs["python_executable"]["size_bytes"]
    ):
        raise E00ExtractionError("runtime Python executable binding differs")
    _text(python["version"], label="runtime Python version")
    libraries = _mapping(
        python["runtime_libraries"], label="runtime Python libraries"
    )
    if len(libraries) != len(library_keys) or not libraries:
        raise E00ExtractionError("runtime Python library cardinality differs")
    for ordinal, (_name, raw) in enumerate(sorted(libraries.items()), start=1):
        row = _mapping(raw, label=f"runtime Python library {ordinal}")
        _require_exact(
            row,
            ("bytes", "path", "sha256"),
            label=f"runtime Python library {ordinal}",
        )
        bound = inputs[f"python_runtime_library_{ordinal:03d}"]
        if (
            row["path"] != bound["path"]
            or row["sha256"] != bound["sha256"]
            or row["bytes"] != bound["size_bytes"]
        ):
            raise E00ExtractionError(
                f"runtime Python library {ordinal} binding differs"
            )
    child_inventory = _validate_child_import_inventory_v2(
        python["child_import_inventory"],
        label="runtime child import inventory",
    )
    installation_rows = [
        row for row in child_inventory if row["source"] == "python-installation"
    ]
    if len(installation_rows) != len(imported_keys):
        raise E00ExtractionError(
            "runtime Python imported-module binding count differs"
        )
    for ordinal, row in enumerate(installation_rows, start=1):
        bound = inputs[f"python_imported_module_{ordinal:03d}"]
        if (
            row["path"] != bound["path"]
            or row["sha256"] != bound["sha256"]
            or row["size_bytes"] != bound["size_bytes"]
        ):
            raise E00ExtractionError(
                "runtime Python imported-module binding differs"
            )
    runtime_rows = [
        row for row in child_inventory if row["source"] == "runtime-package"
    ]
    runtime_by_path = {
        _RUNTIME_LAYOUT[key].as_posix(): executed[key]
        for key in _RUNTIME_LAYOUT
    }
    if {str(row["path"]) for row in runtime_rows} != set(runtime_by_path):
        raise E00ExtractionError(
            "runtime-package import inventory is incomplete or excessive"
        )
    for row in runtime_rows:
        bound = runtime_by_path.get(str(row["path"]))
        if (
            bound is None
            or row["sha256"] != bound["sha256"]
            or row["size_bytes"] != bound["size_bytes"]
        ):
            raise E00ExtractionError(
                "runtime-package import inventory differs"
            )
    pyffish_rows = [
        row for row in child_inventory if row["source"] == "pyffish"
    ]
    if (
        len(pyffish_rows) != 1
        or pyffish_rows[0]["path"] != "pyffish"
        or pyffish_rows[0]["sha256"] != snapshots["pyffish"]["sha256"]
        or pyffish_rows[0]["size_bytes"]
        != snapshots["pyffish"]["size_bytes"]
    ):
        raise E00ExtractionError("runtime pyffish import binding differs")

    modules = _mapping(runtime["modules"], label="runtime modules")
    expected_module_keys = {
        "atomic_mining_init",
        "atomic_outcome_helper",
        "binding_builder",
        "common",
        "owned_process",
        "schedule_builder",
        "tools_init",
        "uci_session",
        "wrapper",
    }
    if set(modules) != expected_module_keys:
        raise E00ExtractionError("runtime module set differs")
    for key in sorted(expected_module_keys):
        module = _validate_runtime_artifact(
            modules[key], label=f"runtime module {key}"
        )
        input_key = "runner" if key == "wrapper" else key
        expected = inputs[input_key]
        if (
            module["path"] != expected["path"]
            or module["sha256"] != expected["sha256"]
            or module["size_bytes"] != expected["size_bytes"]
        ):
            raise E00ExtractionError(f"runtime module {key} binding differs")

    native_rules = _mapping(runtime["native_rules"], label="runtime native_rules")
    _require_exact(
        native_rules,
        (
            "binding",
            "build_contract",
            "build_manifest",
            "source_commit",
            "source_root",
        ),
        label="runtime native_rules",
    )
    for field, input_key in (
        ("binding", "pyffish"),
        ("build_manifest", "pyffish_build_manifest"),
    ):
        binding = _validate_runtime_artifact(
            native_rules[field], label=f"runtime native_rules {field}"
        )
        expected = inputs[input_key]
        if (
            binding["path"] != expected["path"]
            or binding["sha256"] != expected["sha256"]
            or binding["size_bytes"] != expected["size_bytes"]
        ):
            raise E00ExtractionError(
                f"runtime native_rules {field} binding differs"
            )
    build_contract = _canonical_json(
        snapshot_payloads["pyffish_build_manifest"],
        label="snapshotted native build manifest",
    )
    if native_rules["build_contract"] != build_contract:
        raise E00ExtractionError("runtime native build contract differs")
    contract_source = _mapping(
        build_contract.get("source"), label="native build contract source"
    )
    _require_exact(
        contract_source,
        ("commit", "files", "root"),
        label="native build contract source",
    )
    source_commit = native_rules["source_commit"]
    source_root = Path(
        _text(native_rules["source_root"], label="native rules source root")
    )
    if (
        not source_root.is_absolute()
        or not isinstance(source_commit, str)
        or re.fullmatch(r"[0-9a-f]{40,64}", source_commit) is None
    ):
        raise E00ExtractionError("native rules source identity is malformed")
    if (
        contract_source["commit"] != source_commit
        or contract_source["root"] != str(source_root)
        or not isinstance(contract_source["files"], Mapping)
        or not contract_source["files"]
    ):
        raise E00ExtractionError(
            "native build contract source identity differs"
        )

    source = _mapping(runtime["source"], label="runtime source")
    _require_exact(
        source, ("root", "commit", "tree", "clean"), label="runtime source"
    )
    if (
        not Path(_text(source["root"], label="runtime source root")).is_absolute()
        or source["clean"] is not True
    ):
        raise E00ExtractionError("runtime source root/clean state differs")
    for field in ("commit", "tree"):
        if (
            not isinstance(source[field], str)
            or re.fullmatch(r"[0-9a-f]{40}", source[field]) is None
        ):
            raise E00ExtractionError(f"runtime source {field} is malformed")
    if (
        source["root"] != str(source_root)
        or source["commit"] != source_commit
    ):
        raise E00ExtractionError(
            "runtime and native rules source identities differ"
        )

    schedule = _mapping(receipt["schedule"], label="receipt schedule")
    if schedule != {
        "sha256": inputs["schedule"]["sha256"],
        "size_bytes": inputs["schedule"]["size_bytes"],
        "receipt_sha256": inputs["schedule_receipt"]["sha256"],
        "receipt_size_bytes": inputs["schedule_receipt"]["size_bytes"],
    }:
        raise E00ExtractionError(
            "receipt schedule header differs from bound inputs"
        )

    guards = _mapping(
        receipt["namespace_guards"], label="receipt namespace_guards"
    )
    expected_guard_paths = {
        "native_build": native_root,
        "output_parent": root.parent,
        "output": root,
        "snapshots": snapshot_root,
        "runtime_package": runtime_root,
    }
    if set(guards) != set(expected_guard_paths):
        raise E00ExtractionError("namespace guard set differs")
    static_inventories = {
        "native_build": _directory_inventory_wire(native_root),
        "snapshots": _directory_inventory_wire(snapshot_root),
        "runtime_package": _directory_inventory_wire(runtime_root),
    }
    for key, expected_path in expected_guard_paths.items():
        guard = _mapping(guards[key], label=f"namespace guard {key}")
        _require_exact(
            guard,
            ("path", "identity", "parent_identity", "enumeration"),
            label=f"namespace guard {key}",
        )
        if guard["path"] != str(expected_path.absolute()):
            raise E00ExtractionError(f"namespace guard {key} path differs")
        identity = _validate_identity(
            guard["identity"],
            label=f"namespace guard {key} identity",
            directory=True,
        )
        parent_identity = _validate_identity(
            guard["parent_identity"],
            label=f"namespace guard {key} parent identity",
            directory=True,
        )
        if (
            identity != _actual_identity(expected_path, directory=True)
            or parent_identity
            != _actual_identity(expected_path.parent, directory=True)
        ):
            raise E00ExtractionError(
                f"namespace guard {key} immutable identity differs"
            )
        expected_inventory = static_inventories.get(key)
        if guard["enumeration"] != expected_inventory:
            raise E00ExtractionError(
                f"namespace guard {key} enumeration differs"
            )
    native_file_rows = {
        str(row["path"])
        for row in static_inventories["native_build"]
        if row["kind"] == "file"
    }
    if native_file_rows != set(native_artifacts):
        raise E00ExtractionError(
            "native artifact mapping and namespace inventory differ"
        )

    trust = _mapping(receipt["trust_boundary"], label="receipt trust_boundary")
    expected_trust = {
        "hermetic": False,
        "guarded_runtime": (
            "sys.executable, native Python runtime libraries, every "
            "precommitted file-backed imported module, executed runtime "
            "package, pyffish, engine, networks, and all other "
            "result-bearing file inputs"
        ),
        "child_import_policy": (
            "canonical exact inventory; no extras, omissions, duplicates, "
            "identity drift, or late imports"
        ),
        "namespace_policy": (
            "no-delete-share directory handles, immutable locks for every "
            "known result-bearing file, exact static enumeration checkpoints, "
            "and per-child output enumeration"
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
    }
    if trust != expected_trust:
        raise E00ExtractionError("receipt trust-boundary declaration differs")


def _staging_tree(root: Path) -> tuple[tuple[str, str], ...]:
    staging = root / ".pair-temporaries"
    if not staging.is_dir() or staging.is_symlink():
        raise E00ExtractionError("E00 pair staging directory is absent or unsafe")
    entries: list[tuple[str, str]] = []
    for current, directories, files in os.walk(staging, followlinks=False):
        current_path = Path(current)
        for name in sorted(directories):
            candidate = current_path / name
            if candidate.is_symlink():
                raise E00ExtractionError("E00 pair staging contains a symlink")
            entries.append(
                (candidate.relative_to(root).as_posix(), "directory")
            )
        for name in sorted(files):
            candidate = current_path / name
            if candidate.is_symlink():
                raise E00ExtractionError("E00 pair staging contains a symlink")
            try:
                observed = candidate.stat()
            except OSError as error:
                raise E00ExtractionError(
                    "cannot stat E00 pair staging artifact"
                ) from error
            if not stat.S_ISREG(observed.st_mode):
                raise E00ExtractionError(
                    "E00 pair staging contains a non-regular artifact"
                )
            entries.append((candidate.relative_to(root).as_posix(), "file"))
    return tuple(sorted(entries))


def _receipt_artifact(
    receipt: Mapping[str, object], group: str, key: str
) -> Mapping[str, object]:
    return _mapping(
        _mapping(receipt[group], label=f"receipt {group}").get(key),
        label=f"receipt {group} {key}",
    )


def _runtime_child_inventory(
    receipt: Mapping[str, object],
) -> list[dict[str, object]]:
    runtime_receipt = _mapping(receipt["runtime"], label="receipt runtime")
    manifest = _mapping(
        runtime_receipt["manifest"], label="receipt runtime manifest"
    )
    runtime = _mapping(manifest["runtime"], label="runtime manifest runtime")
    python = _mapping(runtime["python"], label="runtime Python")
    return _validate_child_import_inventory_v2(
        python["child_import_inventory"],
        label="runtime child import inventory",
    )


def _validate_artifact_evidence_v2(
    value: object,
    expected: Mapping[str, object],
    *,
    label: str,
) -> None:
    evidence = _mapping(value, label=label)
    _require_exact(evidence, ("bytes", "path", "sha256"), label=label)
    if evidence != {
        "bytes": expected["size_bytes"],
        "path": expected["path"],
        "sha256": expected["sha256"],
    }:
        raise E00ExtractionError(f"{label} differs from executed artifact")


def _validate_native_provenance_v2(
    value: object,
    *,
    receipt: Mapping[str, object],
    label: str,
) -> None:
    provenance = _mapping(value, label=label)
    _require_exact(
        provenance,
        (
            "native",
            "builder",
            "uci_session",
            "owned_process",
            "manifest_binding_path",
        ),
        label=label,
    )
    snapshots = _mapping(
        receipt["input_snapshots"], label="receipt input snapshots"
    )
    executed = _mapping(
        receipt["executed_runtime_package"],
        label="receipt executed runtime package",
    )
    inputs = _mapping(receipt["inputs"], label="receipt inputs")
    native = _mapping(provenance["native"], label=f"{label} native")
    _require_exact(
        native,
        (
            "build_manifest",
            "helper",
            "pyffish",
            "python",
            "rules_source",
            "schema",
        ),
        label=f"{label} native",
    )
    if native["schema"] != "atomic-e00-native-outcome-provenance-v1":
        raise E00ExtractionError(f"{label} native schema differs")
    _validate_artifact_evidence_v2(
        native["build_manifest"],
        _mapping(
            snapshots["pyffish_build_manifest"],
            label="snapshot pyffish build manifest",
        ),
        label=f"{label} native build manifest",
    )
    _validate_artifact_evidence_v2(
        native["helper"],
        _mapping(
            executed["atomic_outcome_helper"],
            label="executed native outcome helper",
        ),
        label=f"{label} native helper",
    )
    _validate_artifact_evidence_v2(
        native["pyffish"],
        _mapping(snapshots["pyffish"], label="snapshot pyffish"),
        label=f"{label} native pyffish",
    )
    python = _mapping(native["python"], label=f"{label} native Python")
    _require_exact(
        python,
        (
            "bytes",
            "cache_tag",
            "executable",
            "runtime_libraries",
            "sha256",
            "version",
        ),
        label=f"{label} native Python",
    )
    python_input = _mapping(
        inputs["python_executable"], label="receipt Python executable"
    )
    runtime_python = _mapping(
        _mapping(
            _mapping(
                _mapping(receipt["runtime"], label="receipt runtime")[
                    "manifest"
                ],
                label="runtime manifest",
            )["runtime"],
            label="runtime",
        )["python"],
        label="runtime Python",
    )
    if (
        python["bytes"] != python_input["size_bytes"]
        or python["executable"] != python_input["path"]
        or python["sha256"] != python_input["sha256"]
        or python["runtime_libraries"] != runtime_python["runtime_libraries"]
        or not isinstance(python["cache_tag"], str)
        or not python["cache_tag"]
        or python["version"] != runtime_python["version"]
    ):
        raise E00ExtractionError(f"{label} native Python binding differs")
    rules_source = _mapping(
        native["rules_source"], label=f"{label} rules source"
    )
    _require_exact(
        rules_source, ("commit", "files", "root"), label=f"{label} rules source"
    )
    native_rules = _mapping(
        _mapping(
            _mapping(
                _mapping(receipt["runtime"], label="receipt runtime")[
                    "manifest"
                ],
                label="runtime manifest",
            )["runtime"],
            label="runtime",
        )["native_rules"],
        label="runtime native rules",
    )
    if (
        rules_source["root"] != native_rules["source_root"]
        or rules_source["commit"] != native_rules["source_commit"]
    ):
        raise E00ExtractionError(f"{label} rules source identity differs")
    source_files = _mapping(
        rules_source["files"], label=f"{label} rules source files"
    )
    if not source_files:
        raise E00ExtractionError(f"{label} rules source file set is empty")
    for path, raw in source_files.items():
        _safe_relative(path, label=f"{label} rules source path")
        binding = _mapping(raw, label=f"{label} rules source file {path}")
        _require_exact(
            binding,
            ("bytes", "sha256"),
            label=f"{label} rules source file {path}",
        )
        _integer(binding["bytes"], label=f"{label} source bytes")
        _sha(binding["sha256"], label=f"{label} source SHA-256")
    contract_source = _mapping(
        _mapping(
            native_rules["build_contract"],
            label="runtime native build contract",
        ).get("source"),
        label="runtime native build contract source",
    )
    if (
        contract_source.get("commit") != rules_source["commit"]
        or contract_source.get("root") != rules_source["root"]
        or contract_source.get("files") != source_files
    ):
        raise E00ExtractionError(
            f"{label} rules source differs from native build contract"
        )
    for field, key in (
        ("builder", "binding_builder"),
        ("uci_session", "uci_session"),
        ("owned_process", "owned_process"),
    ):
        _validate_artifact_evidence_v2(
            provenance[field],
            _mapping(executed[key], label=f"executed {key}"),
            label=f"{label} {field}",
        )
    if provenance["manifest_binding_path"] != _mapping(
        inputs["pyffish"], label="receipt pyffish input"
    )["path"]:
        raise E00ExtractionError(f"{label} manifest binding path differs")


def _validate_rules_calls_v2(value: object, *, label: str) -> list[str]:
    calls = _array(value, label=label)
    if not calls:
        raise E00ExtractionError(f"{label} must be non-empty")
    operations: list[str] = []
    for index, raw in enumerate(calls):
        call = _mapping(raw, label=f"{label} call {index}")
        _require_exact(
            call,
            (
                "operation",
                "request",
                "response",
                "request_sha256",
                "response_sha256",
            ),
            label=f"{label} call {index}",
        )
        operation = _text(
            call["operation"], label=f"{label} call {index} operation"
        )
        if call["request_sha256"] != common.sha256_bytes(
            common.canonical_json_bytes(call["request"])
        ) or call["response_sha256"] != common.sha256_bytes(
            common.canonical_json_bytes(call["response"])
        ):
            raise E00ExtractionError(f"{label} call digest differs")
        operations.append(operation)
    return operations


def _validate_process_evidence_v2(value: object, *, label: str) -> None:
    process = _mapping(value, label=label)
    _require_exact(
        process,
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
    if (
        process["schema"] != OWNED_PROCESS_SCHEMA
        or process["containment"] != "windows-job-object"
        or process["kill_on_close"] is not True
        or process["created_suspended_before_assignment"] is not True
        or process["resumed_primary_thread"] is not True
        or process["active_processes_zero"] is not True
        or process["termination_requested"] is not False
    ):
        raise E00ExtractionError(f"{label} does not prove clean owned exit")


def _validate_engine_evidence_v2(
    value: object,
    *,
    row: Mapping[str, object],
    receipt: Mapping[str, object],
    label: str,
) -> None:
    evidence = _mapping(value, label=label)
    _require_exact(
        evidence,
        ("schema", "variant_path_policy", "engines"),
        label=label,
    )
    if evidence["schema"] != ENGINE_EVIDENCE_SCHEMA:
        raise E00ExtractionError(f"{label} schema differs")
    policy = evidence["variant_path_policy"]
    if policy not in {"configured", "unavailable-explicit"}:
        raise E00ExtractionError(f"{label} VariantPath policy differs")
    engines = _array(evidence["engines"], label=f"{label} engines")
    if len(engines) != 2:
        raise E00ExtractionError(f"{label} must bind two engine processes")
    snapshots = _mapping(
        receipt["input_snapshots"], label="receipt input snapshots"
    )
    execution = _mapping(receipt["execution"], label="receipt execution")
    roles = (row["white_network_role"], row["black_network_role"])
    advertised_baseline: object | None = None
    identifier_baseline: object | None = None
    backends = {
        "current-v3": "AtomicNNUEV3",
        "run3b": "Legacy Atomic V1",
    }
    network_keys = {"current-v3": "current_net", "run3b": "teacher_net"}
    for index, raw in enumerate(engines):
        engine = _mapping(raw, label=f"{label} engine {index}")
        _require_exact(
            engine,
            (
                "role",
                "id",
                "advertised_options",
                "advertised_options_sha256",
                "configured_options",
                "network_proof",
            ),
            label=f"{label} engine {index}",
        )
        role = roles[index]
        if engine["role"] != role:
            raise E00ExtractionError(f"{label} role order differs")
        identifier = _mapping(engine["id"], label=f"{label} engine id")
        _require_exact(identifier, ("name", "author"), label=f"{label} engine id")
        if any(
            not isinstance(identifier[field], str) or not identifier[field]
            for field in ("name", "author")
        ):
            raise E00ExtractionError(f"{label} engine id is incomplete")
        advertised = _array(
            engine["advertised_options"], label=f"{label} advertised options"
        )
        if not advertised:
            raise E00ExtractionError(f"{label} advertised options are empty")
        names: list[str] = []
        for option_index, raw_option in enumerate(advertised):
            option = _mapping(
                raw_option,
                label=f"{label} advertised option {option_index}",
            )
            _require_exact(
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
                label=f"{label} advertised option {option_index}",
            )
            name = _text(
                option["name"], label=f"{label} advertised option name"
            )
            if (
                option["kind"] not in {
                    "button",
                    "check",
                    "spin",
                    "combo",
                    "string",
                }
                or not isinstance(option["choices"], list)
                or not isinstance(option["raw"], str)
                or not option["raw"]
            ):
                raise E00ExtractionError(
                    f"{label} advertised option is malformed"
                )
            names.append(name.casefold())
        if names != sorted(set(names)):
            raise E00ExtractionError(
                f"{label} advertised options are duplicated/unsorted"
            )
        if engine["advertised_options_sha256"] != _digest(
            "atomic-e00-advertised-options-v2", advertised
        ):
            raise E00ExtractionError(
                f"{label} advertised option digest differs"
            )
        expected_configured: list[dict[str, object]] = []
        if policy == "configured":
            expected_configured.append(
                {
                    "name": "VariantPath",
                    "value": _mapping(
                        snapshots["variant_config"],
                        label="variant config snapshot",
                    )["path"],
                }
            )
        expected_configured.extend(
            dict(option)
            for option in _array(
                execution["uci_options"], label="receipt UCI options"
            )
        )
        network = _mapping(
            snapshots[network_keys[str(role)]],
            label=f"{role} network snapshot",
        )
        expected_configured.append(
            {"name": "EvalFile", "value": network["path"]}
        )
        if engine["configured_options"] != expected_configured:
            raise E00ExtractionError(
                f"{label} configured options differ"
            )
        if not {
            str(option["name"]).casefold() for option in expected_configured
        }.issubset(set(names)):
            raise E00ExtractionError(
                f"{label} did not advertise every configured option"
            )
        proof = _mapping(
            engine["network_proof"], label=f"{label} network proof"
        )
        _require_exact(
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
            label=f"{label} network proof",
        )
        line = _text(proof["line"], label=f"{label} network proof line")
        expected_prefix = (
            "info string NNUE evaluation using "
            f"{backends[str(role)]} {network['path']} ("
        )
        raw_lines = _array(
            proof["raw_lines"], label=f"{label} network proof raw lines"
        )
        if (
            proof["backend"] != backends[str(role)]
            or proof["evalfile_path"] != network["path"]
            or proof["evalfile_sha256"] != network["sha256"]
            or proof["first_go"] != "preflight-nodes-1"
            or not line.startswith(expected_prefix)
            or not line.endswith(")")
            or proof["line_sha256"]
            != common.sha256_bytes(line.encode("utf-8"))
            or not raw_lines
            or not all(isinstance(item, str) for item in raw_lines)
            or proof["raw_lines_sha256"]
            != _digest("atomic-e00-uci-go-raw-lines-v2", raw_lines)
            or [
                item
                for item in raw_lines
                if str(item).startswith(
                    "info string NNUE evaluation using "
                )
            ]
            != [line]
            or any(
                "ERROR:" in str(item)
                or "Classical Atomic evaluation enabled" in str(item)
                for item in raw_lines
            )
        ):
            raise E00ExtractionError(f"{label} network proof differs")
        if advertised_baseline is None:
            advertised_baseline = advertised
            identifier_baseline = identifier
        elif advertised != advertised_baseline or identifier != identifier_baseline:
            raise E00ExtractionError(
                f"{label} identical engine processes differ"
            )


def _validate_referee_evidence_v2(
    value: object,
    *,
    row: Mapping[str, object],
    receipt: Mapping[str, object],
    label: str,
) -> None:
    referee = _mapping(value, label=label)
    _require_exact(
        referee,
        (
            "schema",
            "provenance",
            "rules_calls",
            "timing_events",
            "child_import_inventory",
        ),
        label=label,
    )
    if referee["schema"] != REFEREE_EVIDENCE_SCHEMA:
        raise E00ExtractionError(f"{label} schema differs")
    _validate_native_provenance_v2(
        referee["provenance"], receipt=receipt, label=f"{label} provenance"
    )
    if _validate_child_import_inventory_v2(
        referee["child_import_inventory"],
        label=f"{label} child import inventory",
    ) != _runtime_child_inventory(receipt):
        raise E00ExtractionError(f"{label} child import inventory differs")
    operations = _validate_rules_calls_v2(
        referee["rules_calls"], label=f"{label} rules calls"
    )
    if operations[0] != "outcome":
        raise E00ExtractionError(f"{label} did not adjudicate root first")
    events = _array(referee["timing_events"], label=f"{label} timing events")
    if not events:
        raise E00ExtractionError(f"{label} timing events are empty")
    time_control = _mapping(row["time_control"], label=f"{label} time control")
    clocks = [
        int(time_control["base_ms"]) * 1_000_000,
        int(time_control["base_ms"]) * 1_000_000,
    ]
    increment = int(time_control["increment_ms"]) * 1_000_000
    moves: list[str] = []
    expected_operations = ["outcome"]
    snapshots = _mapping(
        receipt["input_snapshots"], label="receipt input snapshots"
    )
    network_keys = {"current-v3": "current_net", "run3b": "teacher_net"}
    backends = {
        "current-v3": "AtomicNNUEV3",
        "run3b": "Legacy Atomic V1",
    }
    for ply, raw in enumerate(events):
        event = _mapping(raw, label=f"{label} timing event {ply}")
        _require_exact(
            event,
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
            label=f"{label} timing event {ply}",
        )
        side = ply % 2
        role = (
            row["white_network_role"] if side == 0 else row["black_network_role"]
        )
        network = _mapping(
            snapshots[network_keys[str(role)]],
            label=f"{label} timing network",
        )
        marker = _text(
            event["network_marker"], label=f"{label} timing marker"
        )
        raw_lines = _array(
            event["network_raw_lines"], label=f"{label} timing raw lines"
        )
        expected_marker_prefix = (
            "info string NNUE evaluation using "
            f"{backends[str(role)]} {network['path']} ("
        )
        integer_fields = (
            "remaining_before_ns",
            "white_clock_ms",
            "black_clock_ms",
            "go_started_ns",
            "bestmove_completed_ns",
            "elapsed_ns",
        )
        if (
            event["ply"] != ply
            or event["side_index"] != side
            or event["role"] != role
            or any(
                not isinstance(event[field], int)
                or isinstance(event[field], bool)
                or int(event[field]) < 0
                for field in integer_fields
            )
            or not marker.startswith(expected_marker_prefix)
            or not marker.endswith(")")
            or event["network_marker_sha256"]
            != common.sha256_bytes(marker.encode("utf-8"))
            or not raw_lines
            or event["network_raw_lines_sha256"]
            != _digest("atomic-e00-uci-go-raw-lines-v2", raw_lines)
            or [
                item
                for item in raw_lines
                if str(item).startswith(
                    "info string NNUE evaluation using "
                )
            ]
            != [marker]
        ):
            raise E00ExtractionError(f"{label} timing event differs")
        elapsed = int(event["bestmove_completed_ns"]) - int(
            event["go_started_ns"]
        )
        on_time = elapsed <= clocks[side]
        if (
            elapsed != event["elapsed_ns"]
            or event["remaining_before_ns"] != clocks[side]
            or event["white_clock_ms"] != clocks[0] // 1_000_000
            or event["black_clock_ms"] != clocks[1] // 1_000_000
            or event["on_time"] is not on_time
            or event["move_applied"] is not on_time
        ):
            raise E00ExtractionError(f"{label} clock arithmetic differs")
        if on_time:
            move = event["bestmove"]
            if not isinstance(move, str) or _UCI_MOVE.fullmatch(move) is None:
                raise E00ExtractionError(f"{label} bestmove is malformed")
            expected_after = clocks[side] - elapsed + increment
            if event["remaining_after_ns"] != expected_after:
                raise E00ExtractionError(f"{label} resulting clock differs")
            clocks[side] = expected_after
            moves.append(move)
            expected_operations.extend(("legal-moves", "outcome"))
        else:
            if event["remaining_after_ns"] is not None or ply + 1 != len(events):
                raise E00ExtractionError(f"{label} flag handling differs")
            expected_operations.extend(
                ("legal-moves", "insufficient-material")
            )
    if (
        moves != row["moves"]
        or bool(row["time_loss"]) != (events[-1]["on_time"] is False)
        or operations != expected_operations
    ):
        raise E00ExtractionError(f"{label} trajectory/call journal differs")


def _validate_verifier_evidence_v2(
    value: object,
    *,
    row: Mapping[str, object],
    receipt: Mapping[str, object],
    label: str,
) -> None:
    verifier = _mapping(value, label=label)
    _require_exact(
        verifier,
        (
            "schema",
            "process",
            "provenance",
            "rules_calls",
            "verified",
            "child_import_inventory",
        ),
        label=label,
    )
    if verifier["schema"] != VERIFIER_EVIDENCE_SCHEMA:
        raise E00ExtractionError(f"{label} schema differs")
    _validate_process_evidence_v2(
        verifier["process"], label=f"{label} process"
    )
    _validate_native_provenance_v2(
        verifier["provenance"], receipt=receipt, label=f"{label} provenance"
    )
    if _validate_child_import_inventory_v2(
        verifier["child_import_inventory"],
        label=f"{label} child import inventory",
    ) != _runtime_child_inventory(receipt):
        raise E00ExtractionError(f"{label} child import inventory differs")
    _validate_rules_calls_v2(
        verifier["rules_calls"], label=f"{label} rules calls"
    )
    verified = {
        "moves": row["moves"],
        "result_white": row["result_white"],
        "root_fen": row["root_fen"],
        "terminal_reason": row["terminal_reason"],
        "time_loss": row["time_loss"],
        "trajectory_sha256": row["trajectory_sha256"],
    }
    if verifier["verified"] != verified:
        raise E00ExtractionError(f"{label} verified result differs")


def _validate_game(
    row: Mapping[str, object],
    *,
    receipt: Mapping[str, object],
    row_number: int,
) -> dict[str, object]:
    label = f"games row {row_number}"
    _require_exact(row, _GAME_FIELDS, label=label)
    if row["schema"] != GAME_SCHEMA:
        raise E00ExtractionError(f"{label} schema differs")
    experiment_id = _text(row["experiment_id"], label=f"{label} experiment_id")
    battery_id = _text(row["battery_id"], label=f"{label} battery_id")
    if (
        experiment_id != receipt["experiment_id"]
        or battery_id != receipt["battery_id"]
    ):
        raise E00ExtractionError(f"{label} experiment/battery differs from receipt")
    _text(row["schedule_seed"], label=f"{label} schedule_seed")
    schedule_sha = _sha(row["schedule_sha256"], label=f"{label} schedule_sha256")
    schedule = _mapping(receipt["schedule"], label="receipt schedule")
    if schedule_sha != schedule["sha256"]:
        raise E00ExtractionError(f"{label} schedule SHA differs from receipt")
    pair_id = _sha(row["pair_id"], label=f"{label} pair_id")
    pair_ordinal = _integer(
        row["pair_ordinal"], label=f"{label} pair_ordinal", minimum=1
    )
    leg = _integer(row["leg"], label=f"{label} leg")
    if leg not in {0, 1}:
        raise E00ExtractionError(f"{label} leg must be 0 or 1")
    stratum = _text(row["stratum"], label=f"{label} stratum")
    time_control = _mapping(row["time_control"], label=f"{label} time_control")
    _require_exact(
        time_control, ("base_ms", "increment_ms", "name"), label=f"{label} time_control"
    )
    _integer(
        time_control["base_ms"], label=f"{label} time_control.base_ms", minimum=1
    )
    _integer(
        time_control["increment_ms"],
        label=f"{label} time_control.increment_ms",
    )
    if _text(time_control["name"], label=f"{label} time_control.name") != stratum:
        raise E00ExtractionError(f"{label} stratum differs from time-control name")
    book_sha = _sha(row["book_sha256"], label=f"{label} book_sha256")
    _integer(row["book_line"], label=f"{label} book_line", minimum=1)
    root_fen = _text(row["root_fen"], label=f"{label} root_fen")
    try:
        common.fen_fields(root_fen)
    except common.MiningArtifactError as error:
        raise _error_from_common(error) from error
    root_sha = _sha(row["root_fen_sha256"], label=f"{label} root_fen_sha256")
    if root_sha != common.derive_id("atomic-root-fen-v1", root_fen):
        raise E00ExtractionError(f"{label} root FEN identity does not recompute")

    inputs = _mapping(receipt["inputs"], label="receipt inputs")
    if book_sha != _mapping(
        inputs.get("book"), label="receipt input book"
    ).get("sha256"):
        raise E00ExtractionError(
            f"{label} book_sha256 differs from receipt input"
        )
    for row_field, input_key in (
        ("engine_sha256", "engine"),
        ("current_net_sha256", "current_net"),
        ("teacher_net_sha256", "teacher_net"),
    ):
        observed = _sha(row[row_field], label=f"{label} {row_field}")
        bound = _mapping(inputs.get(input_key), label=f"receipt input {input_key}")
        if observed != bound.get("sha256"):
            raise E00ExtractionError(
                f"{label} {row_field} differs from receipt input"
            )

    white_role = _text(
        row["white_network_role"], label=f"{label} white_network_role"
    )
    black_role = _text(
        row["black_network_role"], label=f"{label} black_network_role"
    )
    expected_roles = NETWORK_ROLES if leg == 0 else NETWORK_ROLES[::-1]
    if (white_role, black_role) != expected_roles:
        raise E00ExtractionError(f"{label} network roles are not color-inverted")
    result_white = row["result_white"]
    if result_white not in RESULTS_WHITE:
        raise E00ExtractionError(f"{label} result_white is invalid")
    expected_current = _result_for_role(
        str(result_white), current_is_white=white_role == "current-v3"
    )
    if row["result_current"] != expected_current:
        raise E00ExtractionError(f"{label} result_current does not recompute")
    if not isinstance(row["time_loss"], bool):
        raise E00ExtractionError(f"{label} time_loss must be boolean")
    _text(row["terminal_reason"], label=f"{label} terminal_reason")

    raw_moves = _array(row["moves"], label=f"{label} moves")
    moves: list[str] = []
    for ply, move in enumerate(raw_moves):
        if not isinstance(move, str) or _UCI_MOVE.fullmatch(move) is None:
            raise E00ExtractionError(f"{label} move {ply} is malformed")
        moves.append(move)
    if _integer(row["ply_count"], label=f"{label} ply_count") != len(moves):
        raise E00ExtractionError(f"{label} ply_count differs from moves")
    trajectory = _sha(
        row["trajectory_sha256"], label=f"{label} trajectory_sha256"
    )
    expected_trajectory = trajectory_sha256(root_fen, moves, str(result_white))
    if trajectory != expected_trajectory:
        raise E00ExtractionError(f"{label} trajectory identity does not recompute")
    game_id = _sha(row["source_game_id"], label=f"{label} source_game_id")
    expected_game_id = source_game_id(
        experiment_id=experiment_id,
        battery_id=battery_id,
        schedule_sha256=schedule_sha,
        pair_id=pair_id,
        leg=leg,
        trajectory_sha256_value=trajectory,
    )
    if game_id != expected_game_id:
        raise E00ExtractionError(f"{label} source-game identity does not recompute")
    _sha(row["stdout_sha256"], label=f"{label} stdout_sha256")
    stderr_sha = _sha(row["stderr_sha256"], label=f"{label} stderr_sha256")
    if stderr_sha != common.sha256_bytes(b""):
        raise E00ExtractionError(f"{label} commits non-empty engine stderr")
    time_loss_reasons = {
        "time-loss",
        "time-loss-insufficient-material-draw",
    }
    if bool(row["time_loss"]) != (row["terminal_reason"] in time_loss_reasons):
        raise E00ExtractionError(
            f"{label} time_loss and terminal_reason differ"
        )
    _validate_engine_evidence_v2(
        row["engine_evidence"], row=row, receipt=receipt, label=f"{label} engine"
    )
    _validate_referee_evidence_v2(
        row["referee_evidence"],
        row=row,
        receipt=receipt,
        label=f"{label} referee",
    )
    _validate_process_evidence_v2(
        row["process_evidence"], label=f"{label} process"
    )
    _validate_verifier_evidence_v2(
        row["verifier_evidence"],
        row=row,
        receipt=receipt,
        label=f"{label} verifier",
    )
    return dict(row)


def _result_for_role(result_white: str, *, current_is_white: bool) -> str:
    if result_white == "1/2-1/2":
        return "draw"
    won = (result_white == "1-0") == current_is_white
    return "win" if won else "loss"


def _reconcile_games(
    games: Sequence[dict[str, object]], receipt: Mapping[str, object]
) -> int:
    if not games:
        raise E00ExtractionError("committed E00 contains no accepted games")
    if len({str(row["source_game_id"]) for row in games}) != len(games):
        raise E00ExtractionError("committed E00 contains duplicate source-game IDs")
    pairs: dict[str, list[dict[str, object]]] = {}
    expected_order: list[tuple[int, int]] = []
    for row in games:
        pairs.setdefault(str(row["pair_id"]), []).append(row)
        expected_order.append((int(row["pair_ordinal"]), int(row["leg"])))
    pair_count = len(pairs)
    if expected_order != [
        (ordinal, leg)
        for ordinal in range(1, pair_count + 1)
        for leg in (0, 1)
    ]:
        raise E00ExtractionError(
            "games must be ordered as contiguous pair ordinals and legs 0,1"
        )
    for pair_id, rows in pairs.items():
        if len(rows) != 2 or [row["leg"] for row in rows] != [0, 1]:
            raise E00ExtractionError(
                f"pair {pair_id} does not contain exactly legs 0 and 1"
            )
        invariant_fields = (
            "experiment_id",
            "battery_id",
            "schedule_seed",
            "schedule_sha256",
            "pair_id",
            "pair_ordinal",
            "stratum",
            "time_control",
            "book_sha256",
            "book_line",
            "root_fen",
            "root_fen_sha256",
            "engine_sha256",
            "current_net_sha256",
            "teacher_net_sha256",
        )
        if any(rows[0][field] != rows[1][field] for field in invariant_fields):
            raise E00ExtractionError(f"pair {pair_id} invariant fields differ")

    wins = sum(row["result_current"] == "win" for row in games)
    losses = sum(row["result_current"] == "loss" for row in games)
    draws = sum(row["result_current"] == "draw" for row in games)
    time_losses = sum(bool(row["time_loss"]) for row in games)
    expected = {
        "accepted_pairs": pair_count,
        "accepted_games": len(games),
        "wins_current": wins,
        "losses_current": losses,
        "draws": draws,
        "time_losses": time_losses,
        "all_pairs_atomic": True,
        "all_trajectories_legally_replayed": True,
        "all_ids_recomputed": True,
        "zero_extra_duplicate_partial_rows": True,
    }
    if receipt["reconciliation"] != expected:
        raise E00ExtractionError("receipt reconciliation does not recompute")
    return pair_count


def _validate_inventory_and_staging(
    source: AuthenticatedE00,
    inventory: Mapping[str, object],
    inventory_payload: bytes,
    games_payload: bytes,
    rejections_payload: bytes,
    staging_before: tuple[tuple[str, str], ...],
) -> None:
    _require_exact(inventory, ("schema", "entries"), label="E00 inventory")
    if inventory["schema"] != INVENTORY_SCHEMA:
        raise E00ExtractionError("E00 inventory schema differs")
    entries = _array(inventory["entries"], label="E00 inventory entries")
    observed_entries: list[dict[str, object]] = []
    payloads: dict[str, bytes] = {
        "games.jsonl": games_payload,
        "rejections.jsonl": rejections_payload,
    }
    seen_paths: set[str] = set()
    for index, raw_entry in enumerate(entries):
        entry = _mapping(raw_entry, label=f"inventory entry {index}")
        _require_exact(
            entry, ("kind", "path", "sha256", "size_bytes"), label=f"inventory entry {index}"
        )
        kind = _text(entry["kind"], label=f"inventory entry {index} kind")
        relative = _safe_relative(
            entry["path"], label=f"inventory entry {index} path"
        )
        rendered = relative.as_posix()
        if rendered in seen_paths:
            raise E00ExtractionError("inventory contains duplicate paths")
        seen_paths.add(rendered)
        if rendered == "games.jsonl":
            expected_kind = "accepted-games"
        elif rendered == "rejections.jsonl":
            expected_kind = "rejection-ledger"
        elif rendered.startswith(".pair-temporaries/"):
            expected_kind = "pair-staging"
        else:
            raise E00ExtractionError("inventory contains an unexpected path")
        if kind != expected_kind:
            raise E00ExtractionError("inventory kind differs from path role")
        if rendered not in payloads:
            candidate = source.root.joinpath(*relative.parts)
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(source.root.resolve(strict=True)):
                raise E00ExtractionError("inventory path escapes E00 root")
            payloads[rendered] = common.read_stable_file_bytes(
                candidate, label=f"inventory artifact {rendered}"
            )
        payload = payloads[rendered]
        expected_sha = _sha(
            entry["sha256"], label=f"inventory entry {index} SHA-256"
        )
        expected_size = _integer(
            entry["size_bytes"], label=f"inventory entry {index} size"
        )
        if common.sha256_bytes(payload) != expected_sha or len(payload) != expected_size:
            raise E00ExtractionError(
                f"inventory artifact {rendered} hash or size differs"
            )
        observed_entries.append(dict(entry))

    expected_entries: list[dict[str, object]] = []
    for rendered, kind in (
        ("games.jsonl", "accepted-games"),
        ("rejections.jsonl", "rejection-ledger"),
    ):
        payload = payloads[rendered]
        expected_entries.append(
            {
                "kind": kind,
                "path": rendered,
                "sha256": common.sha256_bytes(payload),
                "size_bytes": len(payload),
            }
        )
    game_by_pair_leg = {
        (int(row["pair_ordinal"]), int(row["leg"])): row for row in source.games
    }
    expected_staging_tree: list[tuple[str, str]] = []
    for ordinal in range(1, source.pairs + 1):
        rows = [game_by_pair_leg[(ordinal, leg)] for leg in (0, 1)]
        pair_id = str(rows[0]["pair_id"])
        directory = f".pair-temporaries/{ordinal:06d}-{pair_id}"
        expected_staging_tree.append((directory, "directory"))
        expected_names = (
            "leg-0.json",
            "leg-0.stderr.bin",
            "leg-0.stdout.bin",
            "leg-1.json",
            "leg-1.stderr.bin",
            "leg-1.stdout.bin",
            "pair.json",
        )
        for name in expected_names:
            rendered = f"{directory}/{name}"
            if rendered not in payloads:
                raise E00ExtractionError(
                    f"pair {ordinal} staging artifact {name} is absent from inventory"
                )
            payload = payloads[rendered]
            expected_entries.append(
                {
                    "kind": "pair-staging",
                    "path": rendered,
                    "sha256": common.sha256_bytes(payload),
                    "size_bytes": len(payload),
                }
            )
        for leg in (0, 1):
            row_payload = payloads[f"{directory}/leg-{leg}.json"]
            staged = _canonical_json(
                row_payload, label=f"pair {ordinal} leg {leg} staging"
            )
            if staged != rows[leg]:
                raise E00ExtractionError(
                    f"pair {ordinal} staged leg differs from games.jsonl"
                )
            stdout = payloads[f"{directory}/leg-{leg}.stdout.bin"]
            stderr = payloads[f"{directory}/leg-{leg}.stderr.bin"]
            if common.sha256_bytes(stdout) != rows[leg]["stdout_sha256"]:
                raise E00ExtractionError(
                    f"pair {ordinal} staged stdout differs from game row"
                )
            if common.sha256_bytes(stderr) != rows[leg]["stderr_sha256"]:
                raise E00ExtractionError(
                    f"pair {ordinal} staged stderr differs from game row"
                )
        pair_document = _canonical_json(
            payloads[f"{directory}/pair.json"],
            label=f"pair {ordinal} staging receipt",
        )
        expected_pair_document = {
            "schema": PAIR_SCHEMA,
            "pair_id": pair_id,
            "rows_sha256": common.sha256_bytes(
                b"".join(common.canonical_json_bytes(row) for row in rows)
            ),
            "legs": [0, 1],
        }
        if pair_document != expected_pair_document:
            raise E00ExtractionError(
                f"pair {ordinal} staging receipt does not recompute"
            )
        expected_staging_tree.extend(
            (f"{directory}/{name}", "file") for name in expected_names
        )
    if observed_entries != expected_entries:
        raise E00ExtractionError(
            "inventory entries do not exactly cover accepted outputs and pair staging"
        )
    if staging_before != tuple(sorted(expected_staging_tree)):
        raise E00ExtractionError(
            "pair staging contains missing or unexpected paths"
        )
    if common.canonical_json_bytes(dict(inventory)) != inventory_payload:
        raise E00ExtractionError("inventory changed during authentication")
    for rendered, payload in sorted(payloads.items()):
        observed = common.read_stable_file_bytes(
            source.root.joinpath(*PurePosixPath(rendered).parts),
            label=f"inventory artifact {rendered} final check",
        )
        if observed != payload:
            raise E00ExtractionError(
                f"inventory artifact {rendered} changed during authentication"
            )
    if _staging_tree(source.root) != staging_before:
        raise E00ExtractionError("pair staging changed during authentication")


def authenticate_e00(
    source_root: Path,
    expected_execution_receipt_sha256: str,
) -> AuthenticatedE00:
    root = Path(source_root).absolute()
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise E00ExtractionError(
            "E00 source root must be a non-symlink directory"
        ) from error
    if (
        _is_link_or_reparse(root_metadata)
        or not stat.S_ISDIR(root_metadata.st_mode)
    ):
        raise E00ExtractionError("E00 source root must be a non-symlink directory")
    expected_receipt_sha256 = _sha(
        expected_execution_receipt_sha256,
        label="expected execution receipt SHA-256",
    )
    receipt_path = root / "receipt.json"
    receipt_payload = common.read_stable_file_bytes(
        receipt_path, label="E00 execution receipt"
    )
    if common.sha256_bytes(receipt_payload) != expected_receipt_sha256:
        raise E00ExtractionError(
            "E00 execution receipt differs from caller trust anchor"
        )
    receipt = _canonical_json(receipt_payload, label="E00 execution receipt")
    _require_exact(
        receipt,
        (
            "schema",
            "status",
            "experiment_id",
            "battery_id",
            "schedule",
            "inputs",
            "input_snapshots",
            "executed_runtime_package",
            "native_build_artifacts",
            "runtime",
            "namespace_guards",
            "trust_boundary",
            "execution",
            "reconciliation",
            "outputs",
        ),
        label="E00 execution receipt",
    )
    if receipt["schema"] != EXECUTION_RECEIPT_SCHEMA:
        raise E00ExtractionError(
            "E00 extractor accepts execution-receipt-v2 only"
        )
    if receipt["status"] != "committed":
        raise E00ExtractionError("E00 execution receipt is not committed")
    _text(receipt["experiment_id"], label="receipt experiment_id")
    _text(receipt["battery_id"], label="receipt battery_id")
    schedule = _mapping(receipt["schedule"], label="receipt schedule")
    _require_exact(
        schedule,
        ("sha256", "size_bytes", "receipt_sha256", "receipt_size_bytes"),
        label="receipt schedule",
    )
    _sha(schedule["sha256"], label="receipt schedule SHA-256")
    _integer(schedule["size_bytes"], label="receipt schedule size")
    _sha(schedule["receipt_sha256"], label="receipt schedule receipt SHA-256")
    _integer(
        schedule["receipt_size_bytes"], label="receipt schedule receipt size"
    )
    _validate_runtime_contract(root, receipt)
    outputs = _mapping(receipt["outputs"], label="receipt outputs")
    _require_exact(
        outputs, ("games", "rejections", "inventory"), label="receipt outputs"
    )
    _games_path, games_payload = _read_bound_file(
        root, outputs["games"], expected_path="games.jsonl", label="E00 accepted games"
    )
    _rejections_path, rejections_payload = _read_bound_file(
        root,
        outputs["rejections"],
        expected_path="rejections.jsonl",
        label="E00 rejection ledger",
    )
    _inventory_path, inventory_payload = _read_bound_file(
        root,
        outputs["inventory"],
        expected_path="inventory.json",
        label="E00 inventory",
    )
    if rejections_payload != b"":
        raise E00ExtractionError("committed E00 must have zero rejections")
    raw_games = _canonical_jsonl(games_payload, label="E00 accepted games")
    games = tuple(
        _validate_game(row, receipt=receipt, row_number=index)
        for index, row in enumerate(raw_games, start=1)
    )
    pair_count = _reconcile_games(games, receipt)
    staging_before = _staging_tree(root)
    provisional = AuthenticatedE00(
        root=root,
        receipt=receipt,
        receipt_sha256=common.sha256_bytes(receipt_payload),
        receipt_size_bytes=len(receipt_payload),
        games=games,
        games_sha256=common.sha256_bytes(games_payload),
        games_size_bytes=len(games_payload),
        inventory_sha256=common.sha256_bytes(inventory_payload),
        inventory_size_bytes=len(inventory_payload),
        pairs=pair_count,
    )
    inventory = _canonical_json(inventory_payload, label="E00 inventory")
    _validate_inventory_and_staging(
        provisional,
        inventory,
        inventory_payload,
        games_payload,
        rejections_payload,
        staging_before,
    )
    final_receipt = common.read_stable_file_bytes(
        receipt_path, label="E00 execution receipt final check"
    )
    if final_receipt != receipt_payload:
        raise E00ExtractionError("E00 execution receipt changed during authentication")
    return provisional


def _legal_moves(
    engine: EngineLike,
    fen: str,
    *,
    timeout: float | None,
) -> tuple[str, ...]:
    raw = tuple(engine.legal_moves(fen, timeout=timeout))
    if len(raw) != len(set(raw)):
        raise E00ExtractionError("engine returned duplicate legal moves")
    for move in raw:
        if not isinstance(move, str) or _UCI_MOVE.fullmatch(move) is None:
            raise E00ExtractionError("engine returned malformed legal move")
    return tuple(sorted(raw))


def _inspection_fen(inspection: InspectionLike) -> str:
    if not isinstance(inspection.fen, str):
        raise E00ExtractionError("engine inspection FEN must be text")
    try:
        common.fen_fields(inspection.fen)
    except common.MiningArtifactError as error:
        raise _error_from_common(error) from error
    if inspection.key is not None and not isinstance(inspection.key, str):
        raise E00ExtractionError("engine inspection key must be text or null")
    if (
        not isinstance(inspection.checkers, Sequence)
        or isinstance(inspection.checkers, (str, bytes))
        or any(not isinstance(square, str) for square in inspection.checkers)
    ):
        raise E00ExtractionError("engine inspection checkers must be strings")
    return inspection.fen


def _same_inspection(
    incremental: InspectionLike,
    from_root: InspectionLike,
    *,
    game_id: str,
    root_ply: int,
) -> None:
    if _inspection_fen(incremental) != _inspection_fen(from_root):
        raise E00ExtractionError(
            f"game {game_id} ply {root_ply} replay FENs differ"
        )
    if incremental.key != from_root.key:
        raise E00ExtractionError(
            f"game {game_id} ply {root_ply} replay keys differ"
        )
    if tuple(incremental.checkers) != tuple(from_root.checkers):
        raise E00ExtractionError(
            f"game {game_id} ply {root_ply} replay checkers differ"
        )


def _validate_transition(
    before_fen: str, after_fen: str, *, game_id: str, root_ply: int
) -> None:
    before = common.fen_fields(before_fen)
    after = common.fen_fields(after_fen)
    if before_fen == after_fen:
        raise E00ExtractionError(
            f"game {game_id} ply {root_ply} did not change engine state"
        )
    expected_side = "b" if before[1] == "w" else "w"
    if after[1] != expected_side:
        raise E00ExtractionError(
            f"game {game_id} ply {root_ply} did not toggle side to move"
        )
    expected_fullmove = before[5] + (1 if before[1] == "b" else 0)
    if after[5] != expected_fullmove:
        raise E00ExtractionError(
            f"game {game_id} ply {root_ply} has inconsistent fullmove counter"
        )


def _result_side_to_move(fen: str, result_white: str) -> str:
    if result_white == "1/2-1/2":
        return "draw"
    side = common.fen_fields(fen)[1]
    side_won = (result_white == "1-0") == (side == "w")
    return "win" if side_won else "loss"


def _position_row(
    game: Mapping[str, object],
    inspection: InspectionLike,
    *,
    root_ply: int,
    history: Sequence[str],
    played_move: str,
    legal_move_count: int,
    source: AuthenticatedE00,
) -> dict[str, object]:
    fen = _inspection_fen(inspection)
    game_id = str(game["source_game_id"])
    history_sha = _digest(
        POSITION_HISTORY_SCHEMA,
        {"root_fen": game["root_fen"], "moves": list(history)},
    )
    position_id = _digest(
        POSITION_ID_SCHEMA,
        {
            "history_sha256": history_sha,
            "root_ply": root_ply,
            "source_game_id": game_id,
        },
    )
    result_white = str(game["result_white"])
    return {
        "schema": POSITION_SCHEMA,
        "variant": "atomic",
        "scientific_role": "result-bearing-source",
        "source": {
            "execution_receipt_sha256": source.receipt_sha256,
            "games_sha256": source.games_sha256,
            "inventory_sha256": source.inventory_sha256,
            "experiment_id": game["experiment_id"],
            "battery_id": game["battery_id"],
            "schedule_sha256": game["schedule_sha256"],
            "pair_id": game["pair_id"],
            "pair_ordinal": game["pair_ordinal"],
            "leg": game["leg"],
            "leakage_group_id": game["pair_id"],
            "source_game_id": game_id,
            "trajectory_sha256": game["trajectory_sha256"],
        },
        "game": {
            "root_fen": game["root_fen"],
            "root_fen_sha256": game["root_fen_sha256"],
            "ply_count": game["ply_count"],
            "stratum": game["stratum"],
            "time_control": game["time_control"],
            "book_sha256": game["book_sha256"],
            "book_line": game["book_line"],
            "white_network_role": game["white_network_role"],
            "black_network_role": game["black_network_role"],
            "result_current": game["result_current"],
            "time_loss": game["time_loss"],
            "terminal_reason": game["terminal_reason"],
        },
        "position": {
            "position_id": position_id,
            "root_ply": root_ply,
            "fen": fen,
            "side_to_move": common.fen_fields(fen)[1],
            "history_uci": list(history),
            "history_sha256": history_sha,
            "played_move": played_move,
            "result_white": result_white,
            "result_side_to_move": _result_side_to_move(fen, result_white),
            "transposition_key": common.transposition_key(fen),
            "transposition_key_scope": "informational-only",
            "engine_key": inspection.key,
            "checkers": list(inspection.checkers),
            "legal_move_count": legal_move_count,
        },
    }


def _replay_game(
    game: Mapping[str, object],
    engine: EngineLike,
    source: AuthenticatedE00,
    *,
    timeout: float | None,
) -> list[dict[str, object]]:
    root_fen = str(game["root_fen"])
    moves = tuple(str(move) for move in game["moves"])  # type: ignore[arg-type]
    game_id = str(game["source_game_id"])
    current = engine.inspect_fen(root_fen, timeout=timeout)
    current_fen = _inspection_fen(current)
    if current_fen != root_fen:
        raise E00ExtractionError(f"game {game_id} root inspection differs")
    rows: list[dict[str, object]] = []
    for root_ply, move in enumerate(moves):
        legal = _legal_moves(engine, current_fen, timeout=timeout)
        if move not in legal:
            raise E00ExtractionError(
                f"game {game_id} move {move!r} is illegal at ply {root_ply}"
            )
        rows.append(
            _position_row(
                game,
                current,
                root_ply=root_ply,
                history=moves[:root_ply],
                played_move=move,
                legal_move_count=len(legal),
                source=source,
            )
        )
        incremental = engine.inspect_fen(current_fen, (move,), timeout=timeout)
        from_root = engine.inspect_fen(
            root_fen, moves[: root_ply + 1], timeout=timeout
        )
        _same_inspection(
            incremental,
            from_root,
            game_id=game_id,
            root_ply=root_ply,
        )
        next_fen = _inspection_fen(incremental)
        _validate_transition(
            current_fen, next_fen, game_id=game_id, root_ply=root_ply
        )
        current = incremental
        current_fen = next_fen
    return rows


def _validate_output_targets(
    source_root: Path,
    output_path: Path,
    extraction_receipt_path: Path,
) -> tuple[Path, Path, Path]:
    output_path = Path(output_path).absolute()
    extraction_receipt_path = Path(extraction_receipt_path).absolute()
    if output_path == extraction_receipt_path:
        raise ValueError("output and extraction receipt paths must differ")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite mining artifact: {output_path}")
    if extraction_receipt_path.exists():
        raise FileExistsError(
            f"refusing to overwrite mining artifact: {extraction_receipt_path}"
        )
    source_absolute = Path(source_root).absolute()
    for target in (output_path, extraction_receipt_path):
        if target.is_relative_to(source_absolute):
            raise ValueError("extraction outputs must be outside the E00 source root")
    return source_absolute, output_path, extraction_receipt_path


def _prepare_extraction(
    source: AuthenticatedE00,
    engine: EngineLike,
    *,
    replay_engine_binding: ReplayEngineBinding,
    expected_replay_engine_identity: tuple[int, int, int, int, int] | None,
    expected_execution_receipt_sha256: str,
    timeout: float | None,
) -> PreparedExtraction:
    replay_engine_before = _read_bound_replay_engine(
        replay_engine_binding,
        phase="pre-replay authentication",
        expected_identity=expected_replay_engine_identity,
    )
    rows: list[dict[str, object]] = []
    try:
        for game in source.games:
            rows.extend(_replay_game(game, engine, source, timeout=timeout))
    finally:
        replay_engine_after = _read_bound_replay_engine(
            replay_engine_binding,
            phase="post-replay authentication",
            expected_identity=replay_engine_before.identity,
        )
    if replay_engine_after != replay_engine_before:
        raise E00ExtractionError("replay engine identity changed across replay")
    source_after = authenticate_e00(
        source.root, expected_execution_receipt_sha256
    )
    if (
        source_after.receipt_sha256 != source.receipt_sha256
        or source_after.receipt_size_bytes != source.receipt_size_bytes
        or source_after.games_sha256 != source.games_sha256
        or source_after.games_size_bytes != source.games_size_bytes
        or source_after.inventory_sha256 != source.inventory_sha256
        or source_after.inventory_size_bytes != source.inventory_size_bytes
        or source_after.pairs != source.pairs
    ):
        raise E00ExtractionError("E00 source changed across replay")
    expected_positions = sum(int(game["ply_count"]) for game in source.games)
    if len(rows) != expected_positions:
        raise E00ExtractionError("position count differs from accepted trajectory plies")
    if len({row["position"]["position_id"] for row in rows}) != len(rows):  # type: ignore[index]
        raise E00ExtractionError("position identities are not unique without dedup")
    output_payload = b"".join(common.canonical_json_bytes(row) for row in rows)
    return PreparedExtraction(
        source=source,
        rows=tuple(rows),
        output_payload=output_payload,
    )


def _publish_prepared_extraction(
    prepared: PreparedExtraction,
    output_path: Path,
    extraction_receipt_path: Path,
    *,
    replay_engine_provenance: Mapping[str, object],
) -> ExtractionSummary:
    source = prepared.source
    rows = prepared.rows
    output_payload = prepared.output_payload
    common.write_new_bytes(output_path, output_payload)
    observed_output = common.read_stable_file_bytes(
        output_path, label="E00 extracted positions"
    )
    if observed_output != output_payload:
        raise E00ExtractionError("published E00 positions differ from replay output")
    output_sha = common.sha256_bytes(output_payload)
    receipt = {
        "schema": EXTRACTION_RECEIPT_SCHEMA,
        "status": "committed",
        "source": {
            "root": str(source.root),
            "execution_receipt_sha256": source.receipt_sha256,
            "execution_receipt_size_bytes": source.receipt_size_bytes,
            "games_sha256": source.games_sha256,
            "games_size_bytes": source.games_size_bytes,
            "inventory_sha256": source.inventory_sha256,
            "inventory_size_bytes": source.inventory_size_bytes,
        },
        "counts": {
            "pairs": source.pairs,
            "games": len(source.games),
            "positions": len(rows),
        },
        "replay": {
            "legal_every_ply": True,
            "incremental_root_history_equal": True,
            "trajectory_legality_reauthenticated": True,
            "result_adjudication": {
                "independently_reauthenticated": False,
                "status": "pending-exact-rules-helper",
            },
            "engine": dict(replay_engine_provenance),
            "deduplication": "none",
            "output_order": "pair-ordinal-leg-root-ply",
            "minimum_leakage_group": "pair_id",
            "transposition_union_required_downstream": True,
        },
        "output": {
            "path": str(output_path),
            "schema": POSITION_SCHEMA,
            "sha256": output_sha,
            "size_bytes": len(output_payload),
        },
    }
    receipt_payload = common.canonical_json_bytes(receipt)
    parsed_receipt = _canonical_json(
        receipt_payload, label="E00 extraction receipt"
    )
    if parsed_receipt != receipt:
        raise E00ExtractionError("prepared extraction receipt does not recompute")
    summary = ExtractionSummary(
        pairs=source.pairs,
        games=len(source.games),
        positions=len(rows),
        source_receipt_sha256=source.receipt_sha256,
        output_sha256=output_sha,
        receipt_sha256=common.sha256_bytes(receipt_payload),
    )
    common.write_new_bytes(extraction_receipt_path, receipt_payload)
    return summary


def _binding_receipt(binding: ReplayEngineBinding) -> dict[str, object]:
    return {
        "path": str(Path(binding.path).absolute()),
        "sha256": binding.sha256,
        "size_bytes": binding.size_bytes,
    }


def extract_e00_positions(
    source_root: Path,
    output_path: Path,
    extraction_receipt_path: Path,
    engine: EngineLike,
    *,
    replay_engine_binding: ReplayEngineBinding,
    expected_execution_receipt_sha256: str,
    timeout: float | None = None,
) -> ExtractionSummary:
    source_absolute, output_absolute, receipt_absolute = _validate_output_targets(
        source_root, output_path, extraction_receipt_path
    )
    source = authenticate_e00(
        source_absolute, expected_execution_receipt_sha256
    )
    prepared = _prepare_extraction(
        source,
        engine,
        replay_engine_binding=replay_engine_binding,
        expected_replay_engine_identity=None,
        expected_execution_receipt_sha256=expected_execution_receipt_sha256,
        timeout=timeout,
    )
    binding_receipt = _binding_receipt(replay_engine_binding)
    return _publish_prepared_extraction(
        prepared,
        output_absolute,
        receipt_absolute,
        replay_engine_provenance={
            "policy": "injected-preauthenticated-engine-v1",
            "original": binding_receipt,
            "executed_snapshot": binding_receipt,
            "snapshot": {
                "content_addressed": False,
                "exclusive_directory": False,
                "read_only_applied": False,
                "verified_before_launch": False,
                "verified_before_replay": True,
                "verified_after_replay": True,
                "verified_after_clean_close": False,
                "removed_after_clean_close": False,
            },
            "process_cleanup_verified": False,
        },
    )


def _run_cli_extraction(
    *,
    source_root: Path,
    output_path: Path,
    extraction_receipt_path: Path,
    engine_path: Path,
    expected_engine_sha256: str,
    expected_execution_receipt_sha256: str,
    timeout: float | None,
    engine_context_factory: Callable[[Path], ContextManager[EngineLike]],
    before_launch_hook: Callable[[ReplayEngineSnapshot], None] | None = None,
) -> ExtractionSummary:
    source_absolute, output_absolute, receipt_absolute = _validate_output_targets(
        source_root, output_path, extraction_receipt_path
    )
    source = authenticate_e00(
        source_absolute, expected_execution_receipt_sha256
    )
    engine_absolute = Path(engine_path).absolute()
    expected_sha = _sha(
        expected_engine_sha256, label="expected CLI replay engine SHA-256"
    )
    try:
        original_payload = common.read_stable_file_bytes(
            engine_absolute, label="CLI replay engine"
        )
    except common.MiningArtifactError as error:
        raise _error_from_common(error) from error
    original = ReplayEngineBinding(
        path=engine_absolute,
        sha256=expected_sha,
        size_bytes=len(original_payload),
    )
    _read_bound_replay_engine(
        original, phase="CLI original engine authentication"
    )
    snapshot = _create_replay_engine_snapshot(original)
    try:
        snapshot.guard.verify()
        _read_bound_replay_engine(
            snapshot.executed,
            phase="snapshot immediate pre-launch authentication",
            expected_identity=snapshot.identity,
        )
        if before_launch_hook is not None:
            before_launch_hook(snapshot)
            snapshot.guard.verify()
            _read_bound_replay_engine(
                snapshot.executed,
                phase="snapshot final pre-launch authentication",
                expected_identity=snapshot.identity,
            )
        engine_context = engine_context_factory(snapshot.executed.path)
        _verify_replay_engine_launch_target(
            engine_context, snapshot.executed.path
        )
    except BaseException as error:
        # Any pre-launch verification/factory failure preserves the exact
        # snapshot for forensics while releasing process-local live handles.
        snapshot.guard.close()
        if isinstance(error, E00ExtractionError):
            raise
        raise E00ExtractionError(
            "replay snapshot live guard rejected a pre-launch change"
        ) from error
    engine_object: object | None = None
    try:
        with engine_context as engine:
            engine_object = engine
            snapshot.guard.verify()
            _verify_replay_engine_launch_target(
                engine, snapshot.executed.path
            )
            prepared = _prepare_extraction(
                source,
                engine,
                replay_engine_binding=snapshot.executed,
                expected_replay_engine_identity=snapshot.identity,
                expected_execution_receipt_sha256=(
                    expected_execution_receipt_sha256
                ),
                timeout=timeout,
            )
            snapshot.guard.verify()
    except BaseException as error:
        # UciEngine closes itself if startup or replay fails. Authenticate that
        # close and the executable before removing the snapshot; otherwise
        # retain it for forensic inspection. No output is ever published here.
        _finalize_replay_engine_snapshot(
            engine_object if engine_object is not None else engine_context,
            snapshot,
        )
        if isinstance(error, E00ExtractionError):
            raise
        raise E00ExtractionError(
            "replay snapshot live guard rejected a replay-time change"
        ) from error
    if engine_object is None:
        raise E00ExtractionError("replay engine context returned no engine")
    _finalize_replay_engine_snapshot(engine_object, snapshot)

    return _publish_prepared_extraction(
        prepared,
        output_absolute,
        receipt_absolute,
        replay_engine_provenance={
            "policy": REPLAY_SNAPSHOT_POLICY,
            "original": {
                **_binding_receipt(snapshot.original),
                "identity": {
                    "device": snapshot.original_identity[0],
                    "inode": snapshot.original_identity[1],
                    "size_bytes": snapshot.original_identity[2],
                    "mtime_ns": snapshot.original_identity[3],
                    "ctime_ns": snapshot.original_identity[4],
                },
                "mode": snapshot.original_mode,
                "verified_before_snapshot": True,
            },
            "executed_snapshot": {
                **_binding_receipt(snapshot.executed),
                "identity": {
                    "device": snapshot.identity[0],
                    "inode": snapshot.identity[1],
                    "size_bytes": snapshot.identity[2],
                    "mtime_ns": snapshot.identity[3],
                    "ctime_ns": snapshot.identity[4],
                },
                "mode": snapshot.mode,
            },
            "snapshot": {
                "content_addressed": True,
                "exclusive_directory": True,
                "read_only_applied": snapshot.read_only_applied,
                "live_file_lock": True,
                "live_directory_namespace_seal": True,
                "path_reopened_identity_verified": True,
                "verified_before_launch": True,
                "verified_before_replay": True,
                "verified_after_replay": True,
                "verified_after_clean_close": True,
                "guard_closed_before_removal": True,
                "removed_after_clean_close": True,
            },
            "process_cleanup_verified": True,
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--execution-receipt-sha256", required=True)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--engine-sha256", required=True)
    parser.add_argument("--timeout", type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from .uci_session import UciEngine, UciOptionSetting

    summary = _run_cli_extraction(
        source_root=args.source_root,
        output_path=args.output,
        extraction_receipt_path=args.receipt,
        engine_path=args.engine,
        expected_engine_sha256=args.engine_sha256,
        expected_execution_receipt_sha256=(
            args.execution_receipt_sha256
        ),
        timeout=args.timeout,
        engine_context_factory=lambda snapshot: UciEngine(
            [str(snapshot)],
            options=(UciOptionSetting("UCI_Variant", "atomic"),),
        ),
    )
    print(
        json.dumps(
            {
                "pairs": summary.pairs,
                "games": summary.games,
                "positions": summary.positions,
                "source_receipt_sha256": summary.source_receipt_sha256,
                "output_sha256": summary.output_sha256,
                "receipt_sha256": summary.receipt_sha256,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
