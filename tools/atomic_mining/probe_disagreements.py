"""Produce authenticated current-V3 versus run3b fixed-node probes.

The pure core accepts any object implementing :class:`EngineLike`; the CLI is
the only layer that starts real UCI processes.  Every independent search
clears the hash explicitly, uses Threads=1, and is bounded by a fixed node
count.  Durable output is canonical JSONL with create-new semantics.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Callable, ContextManager, Mapping, Protocol, Sequence

from tools.atomic_mining import common
from tools.atomic_mining import split_components
from tools.atomic_mining.uci_session import (
    FenInspection,
    ProbeResult,
    UciEngine,
    UciInfo,
    UciOptionSetting,
)


if os.name == "nt":  # pragma: no branch - production platform
    import ctypes
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
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


INPUT_SCHEMA = "atomic-match-position-v1"
ASSIGNMENT_SCHEMA = "atomic-mining-assignment-v1"
GLOBAL_ASSIGNMENT_SCHEMA = split_components.ASSIGNMENT_SCHEMA
OUTPUT_SCHEMA = "atomic-dual-probe-v1"
EXECUTION_RECEIPT_SCHEMA = "atomic-dual-probe-execution-receipt-v1"
SNAPSHOT_POLICY = (
    "exclusive-content-addressed-create-new-read-only-clean-close-v1"
)
CLASSIFIER = "atomic-tactical-strata-v1"
TECHNICAL_SMOKE_SPLIT = "technical-smoke"
ASSIGNMENT_SPLITS = frozenset({"calibration", "confirmation"})
UCI_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")

_CANDIDATE_FIELDS = (
    "schema",
    "variant",
    "scientific_role",
    "outcome",
    "source",
    "game",
    "position",
)
_SOURCE_FIELDS = ("path", "sha256", "size_bytes")
_GAME_FIELDS = (
    "game_index",
    "marker_line",
    "root_fen",
    "root_fen_sha256",
    "source_game_id",
    "full_game_history_sha256",
    "move_count",
)
_POSITION_FIELDS = (
    "root_ply",
    "fen",
    "played_move",
    "legal_move_count",
    "terminal_state",
    "history_uci",
    "full_history_sha256",
    "transposition_key",
    "transposition_key_scope",
    "engine_key",
    "checkers",
)
_ASSIGNMENT_FIELDS = (
    "schema",
    "split",
    "position_id",
    "component_id",
    "candidate",
)
_GLOBAL_ASSIGNMENT_FIELDS = (
    "schema",
    "partition",
    "component_id",
    "node_id",
    "node_kind",
    "candidate",
    "inventory_entry",
)
_GLOBAL_PROBE_PARTITIONS = {
    "CAL": "calibration",
    "CONF": "confirmation",
}
_SPLIT_RECEIPT_FIELDS = (
    "schema",
    "mode",
    "seed",
    "fractions",
    "counts",
    "source_sha256s",
    "inventory_contracts",
    "nodes",
    "edges",
    "components",
    "zero_overlap",
    "inputs",
    "outputs",
)
_SPLIT_OUTPUT_FIELDS = ("path", "sha256", "size_bytes", "row_count")
_SPLIT_NODE_FIELDS = ("node_id", "node_kind", "component_id", "partition")
_SPLIT_COMPONENT_FIELDS = (
    "component_id",
    "partition",
    "node_ids",
    "node_kinds",
    "source_game_ids",
    "root_fen_sha256s",
    "transposition_keys",
)
_SPLIT_COUNT_FIELDS = (
    "input_positions",
    "nodes",
    "edges",
    "components",
    "nodes_by_partition",
    "components_by_partition",
)
_ZERO_OVERLAP_FIELDS = (
    "all_nodes_assigned_once",
    "all_components_assigned_once",
    "node_overlap_count",
    "component_overlap_count",
    "node_ids_by_partition",
    "component_ids_by_partition",
)
_PROBE_CONFIG_FIELDS = (
    "engine",
    "current_net",
    "teacher_net",
    "nodes",
    "multipv",
    "threads",
    "hash_mb",
    "clear_hash",
    "teacher_repeat_count",
    "engine_options",
    "probe_order_policy",
)
_ARTIFACT_FIELDS = ("path", "sha256", "size_bytes")
_OUTPUT_FIELDS = (
    "schema",
    "split",
    "position_id",
    "component_id",
    "candidate",
    "probe_config",
    "features",
    "current",
    "teacher_repeats",
    "teacher_on_current_move",
)
_FEATURE_FIELDS = (
    "classifier",
    "in_check",
    "post_capture",
    "post_capture_in_check",
    "teacher_move_capture",
    "teacher_move_gives_check",
    "teacher_move_promotion",
    "forced_chain",
    "primary_stratum",
    "strata",
)


class ProbeError(common.MiningArtifactError):
    """A candidate or probe cannot satisfy the dual-probe contract."""


class EngineLike(Protocol):
    def new_game(self, *, timeout: float | None = None) -> None: ...

    def search(
        self,
        root_fen: str,
        *,
        nodes: int,
        moves: Sequence[str] = (),
        searchmoves: Sequence[str] = (),
        pre_search_options: Sequence[UciOptionSetting] = (),
        timeout: float | None = None,
    ) -> ProbeResult: ...

    def inspect_fen(
        self,
        root_fen: str,
        moves: Sequence[str] = (),
        *,
        timeout: float | None = None,
    ) -> FenInspection: ...


@dataclass(frozen=True)
class ProbeSummary:
    positions: int
    current_searches: int
    teacher_searches: int
    restricted_teacher_searches: int


@dataclass(frozen=True)
class _Assignment:
    split: str
    position_id: str
    component_id: str
    candidate: dict[str, object]
    identity: split_components.PositionIdentity


@dataclass(frozen=True)
class AuthenticatedSplitArtifact:
    """One CAL/CONF artifact authenticated against a caller-trusted receipt."""

    path: Path
    partition: str
    sha256: str
    size_bytes: int
    row_count: int
    receipt_path: Path
    receipt_sha256: str
    receipt_size_bytes: int
    rows: tuple[dict[str, object], ...]
    assignments: tuple[_Assignment, ...]
    row_payloads: frozenset[bytes]


@dataclass(frozen=True)
class ArtifactBinding:
    """Exact content identity of one executable or network artifact."""

    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ArtifactObservation:
    """One stable, non-reparse observation of a bound artifact."""

    payload: bytes
    identity: tuple[int, int, int, int, int]
    mode: int


@dataclass(frozen=True)
class ArtifactSnapshot:
    """Original and immutable executed identity for one artifact role."""

    role: str
    original: ArtifactBinding
    original_identity: tuple[int, int, int, int, int]
    original_mode: int
    snapshot: ArtifactBinding
    snapshot_identity: tuple[int, int, int, int, int]
    snapshot_mode: int
    read_only_applied: bool


@dataclass(frozen=True)
class ProbeSnapshotBundle:
    """Exclusive directory containing the engine and both NNUE snapshots."""

    directory: Path
    artifacts: tuple[ArtifactSnapshot, ...]

    def artifact(self, role: str) -> ArtifactSnapshot:
        matches = tuple(item for item in self.artifacts if item.role == role)
        if len(matches) != 1:
            raise ProbeError(f"snapshot bundle has no unique {role} artifact")
        return matches[0]


def _windows_filetime(value: object) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


class _ImmutableSnapshotGuard:
    """Windows no-write/no-delete lock plus by-handle path identity proof."""

    def __init__(
        self,
        path: Path,
        *,
        label: str,
        directory: bool = False,
    ) -> None:
        if os.name != "nt":  # pragma: no cover - production is Windows
            raise ProbeError(
                "immutable snapshot guards require Windows no-share semantics"
            )
        self.path = Path(path).absolute()
        self.label = label
        self.directory = directory
        self._handle: object | None = None
        self._closed = False
        self._validate_path_components()
        self._handle = self._open_path()
        self.identity = self._identity(self._handle)
        self.verify()

    def _validate_path_components(self) -> None:
        for candidate in (self.path, *self.path.parents):
            attributes = _guard_kernel32.GetFileAttributesW(str(candidate))
            if attributes == _INVALID_FILE_ATTRIBUTES:
                raise ProbeError(
                    f"cannot authenticate path component for {self.label}"
                )
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise ProbeError(
                    f"{self.label} path contains a reparse point"
                )

    def _open_path(self) -> object:
        flags = _FILE_FLAG_OPEN_REPARSE_POINT
        if self.directory:
            flags |= _FILE_FLAG_BACKUP_SEMANTICS
        handle = _guard_kernel32.CreateFileW(
            str(self.path),
            _GENERIC_READ,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            flags,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            raise ProbeError(
                f"cannot acquire immutable guard for {self.label}: "
                f"{ctypes.get_last_error()}"
            )
        return handle

    def _identity(self, handle: object) -> dict[str, object]:
        information = _BY_HANDLE_FILE_INFORMATION()
        if not _guard_kernel32.GetFileInformationByHandle(
            handle, ctypes.byref(information)
        ):
            raise ProbeError(
                f"cannot read immutable identity for {self.label}: "
                f"{ctypes.get_last_error()}"
            )
        attributes = int(information.dwFileAttributes)
        is_directory = bool(attributes & stat.FILE_ATTRIBUTE_DIRECTORY)
        if is_directory != self.directory:
            raise ProbeError(f"{self.label} path kind changed")
        return {
            "schema": "atomic-dual-probe-windows-file-identity-v1",
            "file_attributes": attributes,
            "volume_serial_number": int(
                information.dwVolumeSerialNumber
            ),
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
            "directory": self.directory,
        }

    def verify(self) -> None:
        if self._closed or self._handle is None:
            raise ProbeError(f"{self.label} immutable guard is closed")
        if self._identity(self._handle) != self.identity:
            raise ProbeError(f"{self.label} guarded identity changed")
        self._validate_path_components()
        path_handle = self._open_path()
        close_failed = False
        try:
            if self._identity(path_handle) != self.identity:
                raise ProbeError(f"{self.label} path identity changed")
        finally:
            if not _guard_kernel32.CloseHandle(path_handle):
                close_failed = True
        if close_failed:
            raise ProbeError(
                f"cannot close path identity probe for {self.label}"
            )

    def close(self) -> None:
        if self._closed:
            return
        failed = (
            self._handle is not None
            and not _guard_kernel32.CloseHandle(self._handle)
        )
        self._closed = True
        if failed:
            raise ProbeError(
                f"cannot release immutable guard for {self.label}"
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
class SnapshotGuardBinding:
    role: str
    path: Path
    identity: Mapping[str, object]
    _guard: _ImmutableSnapshotGuard = field(
        repr=False, compare=False
    )

    def verify(self) -> None:
        self._guard.verify()

    def close(self) -> None:
        self._guard.close()


@dataclass(frozen=True)
class SnapshotGuardSet:
    directory: SnapshotGuardBinding
    artifacts: tuple[SnapshotGuardBinding, ...]

    def artifact(self, role: str) -> SnapshotGuardBinding:
        matches = tuple(item for item in self.artifacts if item.role == role)
        if len(matches) != 1:
            raise ProbeError(f"guard set has no unique {role} binding")
        return matches[0]


def _require_dict(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProbeError(f"{label} must be an object")
    return value


def _require_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProbeError(f"{label} must be a non-empty string")
    if any(character in value for character in "\r\n\x00"):
        raise ProbeError(f"{label} contains a forbidden control character")
    return value


def _require_positive_int(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProbeError(f"{label} must be a positive integer")
    return value


def _require_nonnegative_int(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProbeError(f"{label} must be a non-negative integer")
    return value


def _require_list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ProbeError(f"{label} must be an array")
    return value


def _require_partition_mapping(
    value: object,
    *,
    label: str,
) -> dict[str, object]:
    mapping = _require_dict(value, label=label)
    try:
        common.require_exact_fields(
            mapping,
            split_components.PARTITIONS,
            label=label,
        )
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    return mapping


def _validate_move(value: object, *, label: str) -> str:
    move = _require_string(value, label=label)
    if UCI_MOVE.fullmatch(move) is None:
        raise ProbeError(f"{label} must be standard 8x8 UCI notation")
    return move


def _validate_artifact(value: object, *, label: str) -> dict[str, object]:
    artifact = _require_dict(value, label=label)
    try:
        common.require_exact_fields(artifact, _ARTIFACT_FIELDS, label=label)
        common.require_lower_hex_sha256(
            artifact["sha256"], label=f"{label}.sha256"
        )
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    _require_string(artifact["path"], label=f"{label}.path")
    _require_positive_int(artifact["size_bytes"], label=f"{label}.size_bytes")
    return artifact


def validate_probe_config(value: Mapping[str, object]) -> dict[str, object]:
    config = _require_dict(value, label="probe_config")
    try:
        common.require_exact_fields(
            config, _PROBE_CONFIG_FIELDS, label="probe_config"
        )
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    _validate_artifact(config["engine"], label="probe_config.engine")
    _validate_artifact(config["current_net"], label="probe_config.current_net")
    _validate_artifact(config["teacher_net"], label="probe_config.teacher_net")
    _require_positive_int(config["nodes"], label="probe_config.nodes")
    multipv = _require_positive_int(
        config["multipv"], label="probe_config.multipv"
    )
    if multipv < 2:
        raise ProbeError("probe_config.multipv must be at least 2")
    if config["threads"] != 1 or isinstance(config["threads"], bool):
        raise ProbeError("probe_config.threads must be exactly 1")
    _require_positive_int(config["hash_mb"], label="probe_config.hash_mb")
    if config["clear_hash"] is not True:
        raise ProbeError("probe_config.clear_hash must be exactly true")
    repeats = _require_positive_int(
        config["teacher_repeat_count"],
        label="probe_config.teacher_repeat_count",
    )
    if repeats < 2:
        raise ProbeError("probe_config.teacher_repeat_count must be at least 2")
    expected_options = _expected_engine_options(
        hash_mb=int(config["hash_mb"]),
        multipv=multipv,
    )
    if config["engine_options"] != expected_options:
        raise ProbeError(
            "probe_config.engine_options must equal the exact ordered "
            "Atomic mining option contract"
        )
    if config["probe_order_policy"] != "position-id-parity-alternating-v1":
        raise ProbeError(
            "probe_config.probe_order_policy must be "
            "'position-id-parity-alternating-v1'"
        )
    return config


def _expected_engine_options(
    *, hash_mb: int, multipv: int
) -> list[dict[str, object]]:
    return [
        {"name": "UCI_Variant", "value": "atomic"},
        {"name": "Threads", "value": 1},
        {"name": "Hash", "value": hash_mb},
        {"name": "MultiPV", "value": multipv},
        {"name": "Ponder", "value": False},
        {"name": "SyzygyPath", "value": "<empty>"},
        {"name": "SyzygyProbeLimit", "value": 0},
        {"name": "Use NNUE", "value": "pure"},
    ]


def _engine_settings(
    probe_config: Mapping[str, object], eval_file: Path
) -> tuple[UciOptionSetting, ...]:
    """Render only the hash-bound ordered option contract plus EvalFile."""

    config = validate_probe_config(probe_config)
    options = config["engine_options"]
    assert isinstance(options, list)
    settings = tuple(
        UciOptionSetting(str(option["name"]), option["value"])
        for option in options
        if isinstance(option, dict)
    )
    if len(settings) != len(options):
        raise AssertionError("validated engine_options changed shape")
    return settings + (UciOptionSetting("EvalFile", str(eval_file.resolve())),)


def _validate_candidate(
    value: object,
) -> tuple[dict[str, object], split_components.PositionIdentity]:
    candidate = _require_dict(value, label="candidate")
    try:
        identity = split_components.validate_candidate(candidate)
    except split_components.SplitContractError as error:
        raise ProbeError(str(error)) from error
    return candidate, identity


def _unwrap_assignment(
    row: object,
    *,
    global_membership_authenticated: bool = False,
) -> _Assignment:
    document = _require_dict(row, label="input row")
    if document.get("schema") == GLOBAL_ASSIGNMENT_SCHEMA:
        if not global_membership_authenticated:
            raise ProbeError(
                "global assignments require an authenticated split receipt "
                "and exact CAL/CONF artifact membership"
            )
        try:
            common.require_exact_fields(
                document,
                _GLOBAL_ASSIGNMENT_FIELDS,
                label="global assignment",
            )
            node_id = common.require_lower_hex_sha256(
                document["node_id"], label="global assignment.node_id"
            )
            component_id = common.require_lower_hex_sha256(
                document["component_id"],
                label="global assignment.component_id",
            )
        except common.MiningArtifactError as error:
            raise ProbeError(str(error)) from error
        partition = document["partition"]
        if partition not in _GLOBAL_PROBE_PARTITIONS:
            raise ProbeError(
                "global assignment.partition must be CAL or CONF before probing"
            )
        if document["node_kind"] != "E00_CANDIDATE":
            raise ProbeError(
                "only E00_CANDIDATE global assignments may be probed"
            )
        if document["inventory_entry"] is not None:
            raise ProbeError(
                "candidate global assignments must have null inventory_entry"
            )
        candidate, identity = _validate_candidate(document["candidate"])
        if node_id != identity.position_id:
            raise ProbeError(
                "global assignment.node_id does not match structural candidate "
                "identity"
            )
        return _Assignment(
            split=_GLOBAL_PROBE_PARTITIONS[str(partition)],
            position_id=node_id,
            component_id=component_id,
            candidate=candidate,
            identity=identity,
        )

    if document.get("schema") == ASSIGNMENT_SCHEMA:
        try:
            common.require_exact_fields(
                document, _ASSIGNMENT_FIELDS, label="assignment"
            )
            position_id = common.require_lower_hex_sha256(
                document["position_id"], label="assignment.position_id"
            )
            component_id = common.require_lower_hex_sha256(
                document["component_id"], label="assignment.component_id"
            )
        except common.MiningArtifactError as error:
            raise ProbeError(str(error)) from error
        split = document["split"]
        if split not in ASSIGNMENT_SPLITS:
            raise ProbeError(
                "assignment.split must be 'calibration' or 'confirmation'"
            )
        candidate, identity = _validate_candidate(document["candidate"])
        if identity.candidate_schema != split_components.DISCOVERY_INPUT_SCHEMA:
            raise ProbeError(
                "legacy assignments accept discovery-only candidates; "
                "result-bearing E00 candidates require a global assignment"
            )
        return _Assignment(
            split=str(split),
            position_id=position_id,
            component_id=component_id,
            candidate=candidate,
            identity=identity,
        )

    candidate, identity = _validate_candidate(document)
    if identity.candidate_schema != split_components.DISCOVERY_INPUT_SCHEMA:
        raise ProbeError(
            "raw technical-smoke input accepts discovery-only candidates; "
            "result-bearing E00 candidates require the five-way global split"
        )
    position_id = common.derive_id(
        "atomic-dual-probe-position-v1",
        identity.full_history_sha256,
        identity.fen,
    )
    component_id = identity.source_game_id
    return _Assignment(
        split=TECHNICAL_SMOKE_SPLIT,
        position_id=position_id,
        component_id=component_id,
        candidate=candidate,
        identity=identity,
    )


def _canonical_receipt_snapshot(
    path: Path,
    expected_sha256: str,
) -> tuple[Path, bytes, dict[str, object], str]:
    try:
        trusted_sha256 = common.require_lower_hex_sha256(
            expected_sha256,
            label="expected split receipt SHA-256",
        )
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    requested = Path(path).absolute()
    if requested.is_symlink():
        raise ProbeError("split receipt must not be a symlink")
    try:
        payload = common.read_stable_file_bytes(
            requested,
            label="split receipt",
        )
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    observed_sha256 = common.sha256_bytes(payload)
    if observed_sha256 != trusted_sha256:
        raise ProbeError("split receipt differs from the caller trust anchor")
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
        canonical = common.canonical_json_bytes(value)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ProbeError(
            "split receipt must be one canonical UTF-8 JSON object"
        ) from error
    if not isinstance(value, dict) or payload != canonical:
        raise ProbeError(
            "split receipt must be one canonical UTF-8 JSON object"
        )
    try:
        common.require_exact_fields(
            value,
            _SPLIT_RECEIPT_FIELDS,
            label="split receipt",
        )
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    if value["schema"] != split_components.RECEIPT_SCHEMA:
        raise ProbeError(
            f"split receipt.schema must be {split_components.RECEIPT_SCHEMA}"
        )
    if value["mode"] != "scientific":
        raise ProbeError("split receipt.mode must be scientific")
    return requested, payload, value, observed_sha256


def _validated_sha_list(value: object, *, label: str) -> list[str]:
    items = _require_list(value, label=label)
    result: list[str] = []
    for index, item in enumerate(items):
        try:
            result.append(
                common.require_lower_hex_sha256(
                    item,
                    label=f"{label}[{index}]",
                )
            )
        except common.MiningArtifactError as error:
            raise ProbeError(str(error)) from error
    if result != sorted(result) or len(set(result)) != len(result):
        raise ProbeError(f"{label} must be sorted and unique")
    return result


def authenticate_split_artifact(
    input_path: Path,
    split_receipt_path: Path,
    expected_split_receipt_sha256: str,
) -> AuthenticatedSplitArtifact:
    """Authenticate exact CAL/CONF membership before any engine is touched."""

    requested_input = Path(input_path).absolute()
    if requested_input.is_symlink():
        raise ProbeError("split artifact must not be a symlink")
    try:
        snapshot = common.load_jsonl_snapshot(requested_input)
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    if not snapshot.rows:
        raise ProbeError("split artifact must contain at least one row")

    (
        receipt_path,
        receipt_payload,
        receipt,
        receipt_sha256,
    ) = _canonical_receipt_snapshot(
        split_receipt_path,
        expected_split_receipt_sha256,
    )

    outputs = _require_partition_mapping(
        receipt["outputs"],
        label="split receipt.outputs",
    )
    output_paths: list[str] = []
    matching_partitions: list[str] = []
    output_records: dict[str, dict[str, object]] = {}
    for partition in split_components.PARTITIONS:
        record = _require_dict(
            outputs[partition],
            label=f"split receipt.outputs.{partition}",
        )
        try:
            common.require_exact_fields(
                record,
                _SPLIT_OUTPUT_FIELDS,
                label=f"split receipt.outputs.{partition}",
            )
            digest = common.require_lower_hex_sha256(
                record["sha256"],
                label=f"split receipt.outputs.{partition}.sha256",
            )
        except common.MiningArtifactError as error:
            raise ProbeError(str(error)) from error
        rendered_path = _require_string(
            record["path"],
            label=f"split receipt.outputs.{partition}.path",
        )
        size_bytes = _require_nonnegative_int(
            record["size_bytes"],
            label=f"split receipt.outputs.{partition}.size_bytes",
        )
        row_count = _require_nonnegative_int(
            record["row_count"],
            label=f"split receipt.outputs.{partition}.row_count",
        )
        output_paths.append(rendered_path)
        if rendered_path == str(snapshot.path):
            matching_partitions.append(partition)
        output_records[partition] = {
            "path": rendered_path,
            "sha256": digest,
            "size_bytes": size_bytes,
            "row_count": row_count,
        }
    if len(set(output_paths)) != len(output_paths):
        raise ProbeError("split receipt output paths must be unique")
    if len(matching_partitions) != 1:
        raise ProbeError(
            "split artifact path must match exactly one receipt output"
        )
    partition = matching_partitions[0]
    if partition not in _GLOBAL_PROBE_PARTITIONS:
        raise ProbeError("probe input must be the receipt CAL or CONF artifact")
    selected_output = output_records[partition]
    if selected_output["sha256"] != snapshot.sha256:
        raise ProbeError("split receipt does not bind the artifact SHA-256")
    if selected_output["size_bytes"] != snapshot.size_bytes:
        raise ProbeError("split receipt does not bind the artifact size")
    if selected_output["row_count"] != len(snapshot.rows):
        raise ProbeError("split receipt does not bind the artifact row count")

    assignments: list[_Assignment] = []
    artifact_nodes: list[tuple[str, str, str, str]] = []
    payloads: list[bytes] = []
    for index, row in enumerate(snapshot.rows):
        assignment = _unwrap_assignment(
            row,
            global_membership_authenticated=True,
        )
        if row["partition"] != partition:
            raise ProbeError(
                f"split artifact row[{index}] partition differs from its "
                "receipt output"
            )
        assignments.append(assignment)
        artifact_nodes.append(
            (
                str(row["node_id"]),
                str(row["node_kind"]),
                str(row["component_id"]),
                str(row["partition"]),
            )
        )
        payloads.append(common.canonical_json_bytes(row))
    artifact_node_ids = [node_id for node_id, _kind, _component, _part in artifact_nodes]
    if len(set(artifact_node_ids)) != len(artifact_node_ids):
        raise ProbeError("split artifact node_id values must be unique")
    if len(set(payloads)) != len(payloads):
        raise ProbeError("split artifact rows must be unique")

    receipt_nodes_value = _require_list(
        receipt["nodes"],
        label="split receipt.nodes",
    )
    receipt_nodes: list[tuple[str, str, str, str]] = []
    for index, value in enumerate(receipt_nodes_value):
        node = _require_dict(value, label=f"split receipt.nodes[{index}]")
        try:
            common.require_exact_fields(
                node,
                _SPLIT_NODE_FIELDS,
                label=f"split receipt.nodes[{index}]",
            )
            node_id = common.require_lower_hex_sha256(
                node["node_id"],
                label=f"split receipt.nodes[{index}].node_id",
            )
            component_id = common.require_lower_hex_sha256(
                node["component_id"],
                label=f"split receipt.nodes[{index}].component_id",
            )
        except common.MiningArtifactError as error:
            raise ProbeError(str(error)) from error
        node_kind = _require_string(
            node["node_kind"],
            label=f"split receipt.nodes[{index}].node_kind",
        )
        node_partition = node["partition"]
        if node_partition not in split_components.PARTITIONS:
            raise ProbeError(
                f"split receipt.nodes[{index}].partition is invalid"
            )
        receipt_nodes.append(
            (node_id, node_kind, component_id, str(node_partition))
        )
    if len({node[0] for node in receipt_nodes}) != len(receipt_nodes):
        raise ProbeError("split receipt node_id values must be globally unique")
    selected_receipt_nodes = sorted(
        node for node in receipt_nodes if node[3] == partition
    )
    if sorted(artifact_nodes) != selected_receipt_nodes:
        raise ProbeError(
            "split artifact membership differs from receipt node manifest"
        )

    receipt_components_value = _require_list(
        receipt["components"],
        label="split receipt.components",
    )
    selected_components: dict[
        str,
        tuple[list[str], list[str], list[str], list[str], list[str]],
    ] = {}
    component_ids_seen: set[str] = set()
    for index, value in enumerate(receipt_components_value):
        component = _require_dict(
            value,
            label=f"split receipt.components[{index}]",
        )
        try:
            common.require_exact_fields(
                component,
                _SPLIT_COMPONENT_FIELDS,
                label=f"split receipt.components[{index}]",
            )
            component_id = common.require_lower_hex_sha256(
                component["component_id"],
                label=f"split receipt.components[{index}].component_id",
            )
        except common.MiningArtifactError as error:
            raise ProbeError(str(error)) from error
        if component_id in component_ids_seen:
            raise ProbeError(
                "split receipt component_id values must be globally unique"
            )
        component_ids_seen.add(component_id)
        component_partition = component["partition"]
        if component_partition not in split_components.PARTITIONS:
            raise ProbeError(
                f"split receipt.components[{index}].partition is invalid"
            )
        node_ids = _validated_sha_list(
            component["node_ids"],
            label=f"split receipt.components[{index}].node_ids",
        )
        node_kinds = [
            _require_string(
                item,
                label=f"split receipt.components[{index}].node_kinds[{item_index}]",
            )
            for item_index, item in enumerate(
                _require_list(
                    component["node_kinds"],
                    label=f"split receipt.components[{index}].node_kinds",
                )
            )
        ]
        if node_kinds != sorted(set(node_kinds)):
            raise ProbeError(
                f"split receipt.components[{index}].node_kinds must be "
                "sorted and unique"
            )
        source_game_ids = _validated_sha_list(
            component["source_game_ids"],
            label=f"split receipt.components[{index}].source_game_ids",
        )
        root_fen_sha256s = _validated_sha_list(
            component["root_fen_sha256s"],
            label=f"split receipt.components[{index}].root_fen_sha256s",
        )
        transposition_keys = _validated_sha_list(
            component["transposition_keys"],
            label=f"split receipt.components[{index}].transposition_keys",
        )
        if component_partition == partition:
            selected_components[component_id] = (
                node_ids,
                node_kinds,
                source_game_ids,
                root_fen_sha256s,
                transposition_keys,
            )

    artifact_components: dict[
        str,
        tuple[list[str], list[str], list[str], list[str], list[str]],
    ] = {}
    for component_id in sorted(
        {component_id for _node, _kind, component_id, _part in artifact_nodes}
    ):
        component_rows = [
            node
            for node in artifact_nodes
            if node[2] == component_id
        ]
        artifact_components[component_id] = (
            sorted(node[0] for node in component_rows),
            sorted({node[1] for node in component_rows}),
            sorted(
                {
                    assignment.identity.source_game_id
                    for assignment in assignments
                    if assignment.component_id == component_id
                }
            ),
            sorted(
                {
                    assignment.identity.root_fen_sha256
                    for assignment in assignments
                    if assignment.component_id == component_id
                }
            ),
            sorted(
                {
                    assignment.identity.transposition_key
                    for assignment in assignments
                    if assignment.component_id == component_id
                }
            ),
        )
    if selected_components != artifact_components:
        raise ProbeError(
            "split artifact membership differs from receipt component manifest"
        )

    zero_overlap = _require_dict(
        receipt["zero_overlap"],
        label="split receipt.zero_overlap",
    )
    try:
        common.require_exact_fields(
            zero_overlap,
            _ZERO_OVERLAP_FIELDS,
            label="split receipt.zero_overlap",
        )
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    if (
        zero_overlap["all_nodes_assigned_once"] is not True
        or zero_overlap["all_components_assigned_once"] is not True
        or zero_overlap["node_overlap_count"] != 0
        or zero_overlap["component_overlap_count"] != 0
    ):
        raise ProbeError("split receipt zero-overlap proof is not exact")
    node_ids_by_partition = _require_partition_mapping(
        zero_overlap["node_ids_by_partition"],
        label="split receipt.zero_overlap.node_ids_by_partition",
    )
    component_ids_by_partition = _require_partition_mapping(
        zero_overlap["component_ids_by_partition"],
        label="split receipt.zero_overlap.component_ids_by_partition",
    )
    selected_node_ids = _validated_sha_list(
        node_ids_by_partition[partition],
        label=(
            "split receipt.zero_overlap.node_ids_by_partition."
            f"{partition}"
        ),
    )
    selected_component_ids = _validated_sha_list(
        component_ids_by_partition[partition],
        label=(
            "split receipt.zero_overlap.component_ids_by_partition."
            f"{partition}"
        ),
    )
    if selected_node_ids != sorted(artifact_node_ids):
        raise ProbeError(
            "split artifact node IDs differ from zero-overlap proof"
        )
    if selected_component_ids != sorted(artifact_components):
        raise ProbeError(
            "split artifact component IDs differ from zero-overlap proof"
        )

    counts = _require_dict(receipt["counts"], label="split receipt.counts")
    try:
        common.require_exact_fields(
            counts,
            _SPLIT_COUNT_FIELDS,
            label="split receipt.counts",
        )
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    for field in ("input_positions", "nodes", "edges", "components"):
        _require_nonnegative_int(
            counts[field],
            label=f"split receipt.counts.{field}",
        )
    nodes_by_partition = _require_partition_mapping(
        counts["nodes_by_partition"],
        label="split receipt.counts.nodes_by_partition",
    )
    components_by_partition = _require_partition_mapping(
        counts["components_by_partition"],
        label="split receipt.counts.components_by_partition",
    )
    for current_partition in split_components.PARTITIONS:
        _require_nonnegative_int(
            nodes_by_partition[current_partition],
            label=(
                "split receipt.counts.nodes_by_partition."
                f"{current_partition}"
            ),
        )
        _require_nonnegative_int(
            components_by_partition[current_partition],
            label=(
                "split receipt.counts.components_by_partition."
                f"{current_partition}"
            ),
        )
    if nodes_by_partition[partition] != len(artifact_nodes):
        raise ProbeError("split receipt node count differs from artifact")
    if components_by_partition[partition] != len(artifact_components):
        raise ProbeError("split receipt component count differs from artifact")

    return AuthenticatedSplitArtifact(
        path=snapshot.path,
        partition=partition,
        sha256=snapshot.sha256,
        size_bytes=snapshot.size_bytes,
        row_count=len(snapshot.rows),
        receipt_path=receipt_path,
        receipt_sha256=receipt_sha256,
        receipt_size_bytes=len(receipt_payload),
        rows=snapshot.rows,
        assignments=tuple(assignments),
        row_payloads=frozenset(payloads),
    )


def _board_map(fen: str) -> tuple[dict[str, str], str]:
    board_field, _side, _castling, en_passant, _halfmove, _fullmove = (
        common.fen_fields(fen)
    )
    ranks = board_field.split("/")
    if len(ranks) != 8:
        raise ProbeError("Atomic FEN board must contain eight ranks")
    board: dict[str, str] = {}
    for rank_index, encoded_rank in enumerate(ranks):
        file_index = 0
        rank = 8 - rank_index
        for token in encoded_rank:
            if token.isdigit():
                empty = int(token)
                if not 1 <= empty <= 8:
                    raise ProbeError("Atomic FEN contains an invalid empty run")
                file_index += empty
                continue
            if token not in "pnbrqkPNBRQK":
                raise ProbeError(f"Atomic FEN contains invalid piece {token!r}")
            if file_index >= 8:
                raise ProbeError("Atomic FEN rank expands beyond eight files")
            square = f"{chr(ord('a') + file_index)}{rank}"
            board[square] = token
            file_index += 1
        if file_index != 8:
            raise ProbeError("Atomic FEN rank does not expand to eight files")
    return board, en_passant


def _move_capture_and_promotion(fen: str, move: str) -> tuple[bool, bool]:
    move = _validate_move(move, label="move")
    board, en_passant = _board_map(fen)
    source = move[:2]
    destination = move[2:4]
    piece = board.get(source)
    if piece is None:
        raise ProbeError(f"move source {source!r} is empty in supplied FEN")
    ordinary_capture = destination in board
    en_passant_capture = (
        piece.lower() == "p"
        and destination == en_passant
        and source[0] != destination[0]
        and not ordinary_capture
    )
    return ordinary_capture or en_passant_capture, len(move) == 5


def _info_to_wire(info: UciInfo, *, label: str) -> dict[str, object]:
    if (
        info.depth is None
        or info.seldepth is None
        or info.nodes is None
        or info.score is None
        or not info.pv
    ):
        raise ProbeError(
            f"{label} lacks depth, seldepth, nodes, score, or principal variation"
        )
    multipv = info.multipv or 1
    if multipv <= 0:
        raise ProbeError(f"{label}.multipv must be positive")
    return {
        "depth": info.depth,
        "seldepth": info.seldepth,
        "nodes": info.nodes,
        "multipv": multipv,
        "score": {
            "kind": info.score.kind,
            "value": info.score.value,
            "bound": info.score.bound,
        },
        "wdl": list(info.wdl) if info.wdl is not None else None,
        "pv": list(info.pv),
    }


def _snapshot(result: ProbeResult, *, label: str) -> dict[str, object]:
    if result.bestmove is None:
        raise ProbeError(f"{label} did not return a best move")
    if not result.raw_lines:
        raise ProbeError(f"{label} has no raw UCI transcript")
    latest = result.latest_by_multipv
    if 1 not in latest:
        raise ProbeError(f"{label} has no principal MultiPV lane")
    principal = _info_to_wire(latest[1], label=f"{label}.principal")
    if principal["pv"][0] != result.bestmove:  # type: ignore[index]
        raise ProbeError(f"{label} bestmove differs from its principal PV")
    multipv = [
        _info_to_wire(latest[lane], label=f"{label}.multipv[{lane}]")
        for lane in sorted(latest)
    ]
    raw_payload = ("\n".join(result.raw_lines) + "\n").encode("utf-8")
    return {
        "bestmove": result.bestmove,
        "ponder": result.ponder,
        "principal": principal,
        "multipv": multipv,
        "raw_sha256": common.sha256_bytes(raw_payload),
        "raw_line_count": len(result.raw_lines),
    }


def _search(
    engine: EngineLike,
    fen: str,
    *,
    nodes: int,
    searchmoves: Sequence[str] = (),
    timeout: float | None,
) -> ProbeResult:
    engine.new_game(timeout=timeout)
    return engine.search(
        fen,
        nodes=nodes,
        searchmoves=searchmoves,
        pre_search_options=(UciOptionSetting("Clear Hash"),),
        timeout=timeout,
    )


def _inspection_matches_candidate(
    inspection: FenInspection,
    *,
    expected_fen: str,
    expected_checkers: Sequence[str],
) -> None:
    if " ".join(inspection.fen.split()) != expected_fen:
        raise ProbeError("replayed history does not reproduce candidate FEN")
    if tuple(inspection.checkers) != tuple(expected_checkers):
        raise ProbeError("replayed checkers differ from candidate checkers")


def _feature_document(
    assignment: _Assignment,
    *,
    current_engine: EngineLike,
    teacher_engine: EngineLike,
    teacher_result: ProbeResult,
    timeout: float | None,
) -> dict[str, object]:
    identity = assignment.identity
    root_fen = identity.root_fen
    current_fen = identity.fen
    history = identity.history_uci
    expected_checkers = identity.checkers

    current_inspection = current_engine.inspect_fen(
        root_fen, history, timeout=timeout
    )
    _inspection_matches_candidate(
        current_inspection,
        expected_fen=current_fen,
        expected_checkers=expected_checkers,
    )
    in_check = bool(current_inspection.checkers)

    post_capture = False
    if history:
        previous = current_engine.inspect_fen(
            root_fen, history[:-1], timeout=timeout
        )
        post_capture, _promotion = _move_capture_and_promotion(
            previous.fen, history[-1]
        )
    post_capture_in_check = post_capture and in_check

    if teacher_result.bestmove is None:
        raise ProbeError("teacher did not return a best move")
    teacher_capture, teacher_promotion = _move_capture_and_promotion(
        current_fen, teacher_result.bestmove
    )
    teacher_child = teacher_engine.inspect_fen(
        current_fen, (teacher_result.bestmove,), timeout=timeout
    )
    teacher_gives_check = bool(teacher_child.checkers)
    forced_chain = len(teacher_result.latest_by_multipv) == 1

    strata: list[str] = []
    for name, active in (
        ("post_capture_in_check", post_capture_in_check),
        ("in_check", in_check),
        ("post_capture", post_capture),
        ("teacher_move_gives_check", teacher_gives_check),
        ("teacher_move_capture", teacher_capture),
        ("teacher_move_promotion", teacher_promotion),
        ("forced_chain", forced_chain),
    ):
        if active:
            strata.append(name)
    if not strata:
        strata.append("quiet")

    return {
        "classifier": CLASSIFIER,
        "in_check": in_check,
        "post_capture": post_capture,
        "post_capture_in_check": post_capture_in_check,
        "teacher_move_capture": teacher_capture,
        "teacher_move_gives_check": teacher_gives_check,
        "teacher_move_promotion": teacher_promotion,
        "forced_chain": forced_chain,
        "primary_stratum": strata[0],
        "strata": strata,
    }


def _probe_assignment(
    assignment: _Assignment,
    current_engine: EngineLike,
    teacher_engine: EngineLike,
    *,
    probe_config: Mapping[str, object],
    timeout: float | None = None,
) -> dict[str, object]:
    config = validate_probe_config(probe_config)
    nodes = int(config["nodes"])
    repeat_count = int(config["teacher_repeat_count"])

    fen = assignment.identity.fen
    if int(assignment.position_id[0], 16) % 2 == 0:
        current_result = _search(
            current_engine,
            fen,
            nodes=nodes,
            timeout=timeout,
        )
        current_snapshot = _snapshot(current_result, label="current")
        teacher_results = tuple(
            _search(
                teacher_engine,
                fen,
                nodes=nodes,
                timeout=timeout,
            )
            for _repeat in range(repeat_count)
        )
    else:
        first_teacher = _search(
            teacher_engine,
            fen,
            nodes=nodes,
            timeout=timeout,
        )
        current_result = _search(
            current_engine,
            fen,
            nodes=nodes,
            timeout=timeout,
        )
        current_snapshot = _snapshot(current_result, label="current")
        remaining_teacher = tuple(
            _search(
                teacher_engine,
                fen,
                nodes=nodes,
                timeout=timeout,
            )
            for _repeat in range(repeat_count - 1)
        )
        teacher_results = (first_teacher,) + remaining_teacher
    teacher_snapshots = [
        _snapshot(result, label=f"teacher_repeats[{index}]")
        for index, result in enumerate(teacher_results)
    ]

    # Authenticate the replay and derive engine-backed strata before spending
    # the additional restricted-search budget.
    features = _feature_document(
        assignment,
        current_engine=current_engine,
        teacher_engine=teacher_engine,
        teacher_result=teacher_results[0],
        timeout=timeout,
    )
    try:
        common.require_exact_fields(features, _FEATURE_FIELDS, label="features")
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error

    teacher_on_current_move: dict[str, object] | None = None
    if current_result.bestmove is None or teacher_results[0].bestmove is None:
        raise ProbeError("current and teacher probes must return best moves")
    restricted = _search(
        teacher_engine,
        assignment.identity.fen,
        nodes=nodes,
        searchmoves=(current_result.bestmove,),
        timeout=timeout,
    )
    if restricted.bestmove != current_result.bestmove:
        raise ProbeError(
            "teacher restricted probe did not return the requested current move"
        )
    teacher_on_current_move = _snapshot(
        restricted, label="teacher_on_current_move"
    )

    output = {
        "schema": OUTPUT_SCHEMA,
        "split": assignment.split,
        "position_id": assignment.position_id,
        "component_id": assignment.component_id,
        "candidate": assignment.candidate,
        "probe_config": config,
        "features": features,
        "current": current_snapshot,
        "teacher_repeats": teacher_snapshots,
        "teacher_on_current_move": teacher_on_current_move,
    }
    try:
        common.require_exact_fields(output, _OUTPUT_FIELDS, label="dual probe")
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    return output


def probe_position(
    row: object,
    current_engine: EngineLike,
    teacher_engine: EngineLike,
    *,
    probe_config: Mapping[str, object],
    timeout: float | None = None,
) -> dict[str, object]:
    """Probe discovery-only unit/smoke input.

    Scientific global rows are deliberately unavailable through this
    single-row API: only :func:`probe_file` may execute them after authenticating
    their complete CAL/CONF artifact and caller-trusted split receipt.
    """

    assignment = _unwrap_assignment(row)
    return _probe_assignment(
        assignment,
        current_engine,
        teacher_engine,
        probe_config=probe_config,
        timeout=timeout,
    )


def probe_file(
    input_path: Path,
    split_receipt_path: Path,
    expected_split_receipt_sha256: str,
    output_path: Path,
    current_engine: EngineLike,
    teacher_engine: EngineLike,
    *,
    probe_config: Mapping[str, object],
    timeout: float | None = None,
) -> ProbeSummary:
    """Probe every canonical input row and publish only after full success."""

    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite mining artifact: {output_path}")
    authenticated = authenticate_split_artifact(
        input_path,
        split_receipt_path,
        expected_split_receipt_sha256,
    )
    output_rows, summary = _probe_authenticated_artifact(
        authenticated,
        current_engine,
        teacher_engine,
        probe_config=probe_config,
        timeout=timeout,
    )
    common.write_new_jsonl(output_path, output_rows)
    return summary


def _probe_authenticated_artifact(
    authenticated: AuthenticatedSplitArtifact,
    current_engine: EngineLike,
    teacher_engine: EngineLike,
    *,
    probe_config: Mapping[str, object],
    timeout: float | None = None,
) -> tuple[list[dict[str, object]], ProbeSummary]:
    """Probe an already authenticated complete CAL/CONF artifact in memory."""

    if not isinstance(authenticated, AuthenticatedSplitArtifact):
        raise TypeError(
            "authenticated must be an AuthenticatedSplitArtifact"
        )
    output_rows = [
        _probe_assignment(
            assignment,
            current_engine,
            teacher_engine,
            probe_config=probe_config,
            timeout=timeout,
        )
        for assignment in authenticated.assignments
    ]
    restricted = sum(
        row["teacher_on_current_move"] is not None for row in output_rows
    )
    repeat_count = int(probe_config["teacher_repeat_count"])
    return (
        output_rows,
        ProbeSummary(
            positions=len(output_rows),
            current_searches=len(output_rows),
            teacher_searches=len(output_rows) * repeat_count,
            restricted_teacher_searches=restricted,
        ),
    )


def _file_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def _safe_directory_identity(path: Path, *, label: str) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ProbeError(f"cannot inspect {label}: {path}") from error
    if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ProbeError(f"{label} must be a non-reparse directory")
    return metadata.st_dev, metadata.st_ino


def _observe_regular_artifact(
    path: Path, *, label: str
) -> ArtifactObservation:
    requested = Path(path).absolute()
    parent_identity = _safe_directory_identity(
        requested.parent, label=f"{label} parent"
    )
    try:
        before = requested.lstat()
    except OSError as error:
        raise ProbeError(f"cannot inspect {label}: {requested}") from error
    if _is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode):
        raise ProbeError(f"{label} must be a non-reparse regular file")
    try:
        payload = common.read_stable_file_bytes(requested, label=label)
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    try:
        after = requested.lstat()
    except OSError as error:
        raise ProbeError(f"cannot re-inspect {label}: {requested}") from error
    if (
        _is_link_or_reparse(after)
        or not stat.S_ISREG(after.st_mode)
        or _file_identity(before) != _file_identity(after)
    ):
        raise ProbeError(f"{label} changed while it was authenticated")
    if _safe_directory_identity(
        requested.parent, label=f"{label} parent"
    ) != parent_identity:
        raise ProbeError(f"{label} parent changed while it was authenticated")
    return ArtifactObservation(
        payload=payload,
        identity=_file_identity(after),
        mode=stat.S_IMODE(after.st_mode),
    )


def _read_bound_artifact(
    binding: ArtifactBinding,
    *,
    label: str,
    expected_identity: tuple[int, int, int, int, int] | None = None,
) -> ArtifactObservation:
    if not isinstance(binding, ArtifactBinding):
        raise TypeError("binding must be an ArtifactBinding")
    observation = _observe_regular_artifact(binding.path, label=label)
    if (
        expected_identity is not None
        and observation.identity != expected_identity
    ):
        raise ProbeError(f"{label} identity differs from the trusted binding")
    if (
        len(observation.payload) != binding.size_bytes
        or common.sha256_bytes(observation.payload) != binding.sha256
    ):
        raise ProbeError(f"{label} hash or size differs from the trusted binding")
    return observation


def _bind_original_artifact(
    path: Path, expected_sha256: str, *, label: str
) -> tuple[ArtifactBinding, ArtifactObservation]:
    try:
        expected = common.require_lower_hex_sha256(
            expected_sha256, label=f"{label} expected SHA-256"
        )
    except common.MiningArtifactError as error:
        raise ProbeError(str(error)) from error
    requested = Path(path).absolute()
    observation = _observe_regular_artifact(requested, label=label)
    actual = common.sha256_bytes(observation.payload)
    if actual != expected:
        raise ProbeError(
            f"{label} SHA-256 mismatch: expected {expected}, observed {actual}"
        )
    size = len(observation.payload)
    if size <= 0:
        raise ProbeError(f"{label} must not be empty")
    return (
        ArtifactBinding(
            path=requested,
            sha256=actual,
            size_bytes=size,
        ),
        observation,
    )


def _binding_wire(binding: ArtifactBinding) -> dict[str, object]:
    return {
        "path": str(binding.path),
        "sha256": binding.sha256,
        "size_bytes": binding.size_bytes,
    }


def _identity_wire(
    identity: tuple[int, int, int, int, int]
) -> dict[str, int]:
    return {
        "device": identity[0],
        "inode": identity[1],
        "size_bytes": identity[2],
        "mtime_ns": identity[3],
        "ctime_ns": identity[4],
    }


def _best_effort_remove_owned_snapshot_directory(
    directory: Path, snapshot_paths: Sequence[Path]
) -> None:
    """Remove only ordinary files in our private directory after setup failure."""

    for path in reversed(tuple(snapshot_paths)):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return
        if _is_link_or_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
            return
        try:
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            path.unlink()
        except OSError:
            return
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        return
    try:
        directory.rmdir()
    except OSError:
        return


def _create_snapshot_bundle(
    originals: Mapping[
        str, tuple[ArtifactBinding, ArtifactObservation]
    ],
    *,
    snapshot_parent: Path | None = None,
) -> ProbeSnapshotBundle:
    expected_roles = ("engine", "current_net", "teacher_net")
    if tuple(sorted(originals)) != tuple(sorted(expected_roles)):
        raise ProbeError("snapshot originals must contain exactly three roles")
    authenticated: dict[str, ArtifactObservation] = {}
    for role in expected_roles:
        binding, initial = originals[role]
        authenticated[role] = _read_bound_artifact(
            binding,
            label=f"{role} original pre-snapshot authentication",
            expected_identity=initial.identity,
        )

    parent = (
        Path(snapshot_parent).absolute()
        if snapshot_parent is not None
        else Path(tempfile.gettempdir()).absolute()
    )
    _safe_directory_identity(parent, label="snapshot parent")
    directory = Path(
        tempfile.mkdtemp(
            prefix="atomic-dual-probe-snapshot-",
            dir=parent,
        )
    ).absolute()
    _safe_directory_identity(directory, label="snapshot directory")

    snapshots: list[ArtifactSnapshot] = []
    created_paths: list[Path] = []
    try:
        for role in expected_roles:
            original, initial = originals[role]
            observation = authenticated[role]
            suffix = original.path.suffix
            snapshot_path = (
                directory / f"{role}-{original.sha256}{suffix}"
            )
            common.write_new_bytes(snapshot_path, observation.payload)
            created_paths.append(snapshot_path)
            desired_mode = (
                observation.mode
                | stat.S_IRUSR
                | (
                    observation.mode
                    & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    if role == "engine"
                    else 0
                )
            ) & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
            os.chmod(snapshot_path, desired_mode)
            executed = ArtifactBinding(
                path=snapshot_path,
                sha256=original.sha256,
                size_bytes=original.size_bytes,
            )
            sealed = _read_bound_artifact(
                executed,
                label=f"{role} snapshot post-publication authentication",
            )
            if sealed.mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                raise ProbeError(f"{role} snapshot remains writable")
            snapshots.append(
                ArtifactSnapshot(
                    role=role,
                    original=original,
                    original_identity=initial.identity,
                    original_mode=initial.mode,
                    snapshot=executed,
                    snapshot_identity=sealed.identity,
                    snapshot_mode=sealed.mode,
                    read_only_applied=True,
                )
            )
        return ProbeSnapshotBundle(
            directory=directory,
            artifacts=tuple(snapshots),
        )
    except BaseException:
        _best_effort_remove_owned_snapshot_directory(
            directory, created_paths
        )
        raise


def _verify_snapshot_directory_membership(
    bundle: ProbeSnapshotBundle,
) -> None:
    _safe_directory_identity(bundle.directory, label="snapshot directory")
    try:
        observed = tuple(sorted(bundle.directory.iterdir()))
    except OSError as error:
        raise ProbeError("cannot enumerate snapshot directory") from error
    expected = tuple(
        sorted(artifact.snapshot.path for artifact in bundle.artifacts)
    )
    if observed != expected:
        raise ProbeError("snapshot directory membership changed")


def _authenticate_snapshot_bundle(
    bundle: ProbeSnapshotBundle, *, phase: str
) -> None:
    _verify_snapshot_directory_membership(bundle)
    for artifact in bundle.artifacts:
        observation = _read_bound_artifact(
            artifact.snapshot,
            label=f"{artifact.role} snapshot {phase}",
            expected_identity=artifact.snapshot_identity,
        )
        if (
            observation.mode
            & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise ProbeError(
                f"{artifact.role} snapshot became writable during {phase}"
            )


def _acquire_snapshot_guards(
    bundle: ProbeSnapshotBundle,
) -> SnapshotGuardSet:
    acquired: list[_ImmutableSnapshotGuard] = []
    try:
        directory_guard = _ImmutableSnapshotGuard(
            bundle.directory,
            label="snapshot directory",
            directory=True,
        )
        acquired.append(directory_guard)
        artifact_bindings: list[SnapshotGuardBinding] = []
        for artifact in bundle.artifacts:
            guard = _ImmutableSnapshotGuard(
                artifact.snapshot.path,
                label=f"{artifact.role} snapshot",
            )
            acquired.append(guard)
            if guard.identity["size_bytes"] != artifact.snapshot.size_bytes:
                raise ProbeError(
                    f"{artifact.role} guarded size differs from snapshot"
                )
            artifact_bindings.append(
                SnapshotGuardBinding(
                    role=artifact.role,
                    path=artifact.snapshot.path,
                    identity=dict(guard.identity),
                    _guard=guard,
                )
            )
        guards = SnapshotGuardSet(
            directory=SnapshotGuardBinding(
                role="snapshot_directory",
                path=bundle.directory,
                identity=dict(directory_guard.identity),
                _guard=directory_guard,
            ),
            artifacts=tuple(artifact_bindings),
        )
        _verify_snapshot_guards(guards)
        _authenticate_snapshot_bundle(
            bundle, phase="guarded acquisition authentication"
        )
        _verify_snapshot_guards(guards)
        return guards
    except BaseException:
        close_failure: BaseException | None = None
        for guard in reversed(acquired):
            try:
                guard.close()
            except BaseException as error:
                close_failure = close_failure or error
        if close_failure is not None:
            raise ProbeError(
                "partial immutable guard acquisition did not close cleanly"
            ) from close_failure
        raise


def _verify_snapshot_guards(guards: SnapshotGuardSet) -> None:
    guards.directory.verify()
    for binding in guards.artifacts:
        binding.verify()


def _close_snapshot_guards(guards: SnapshotGuardSet) -> None:
    failure: BaseException | None = None
    for binding in reversed(guards.artifacts):
        try:
            binding.close()
        except BaseException as error:
            failure = failure or error
    try:
        guards.directory.close()
    except BaseException as error:
        failure = failure or error
    if failure is not None:
        raise ProbeError(
            "immutable snapshot guards did not close cleanly"
        ) from failure


def _remove_snapshot_bundle(bundle: ProbeSnapshotBundle) -> None:
    """Remove a fully authenticated bundle; any doubt retains evidence."""

    _authenticate_snapshot_bundle(bundle, phase="pre-removal authentication")
    for artifact in reversed(bundle.artifacts):
        try:
            os.chmod(
                artifact.snapshot.path,
                artifact.snapshot_mode | stat.S_IWUSR,
            )
            artifact.snapshot.path.unlink()
        except OSError as error:
            raise ProbeError(
                "could not remove authenticated snapshot bundle"
            ) from error
    try:
        bundle.directory.rmdir()
    except OSError as error:
        raise ProbeError(
            "could not remove authenticated snapshot directory"
        ) from error
    if bundle.directory.exists():
        raise ProbeError("snapshot directory remains after cleanup")


def _verify_engine_clean_close(engine: object, *, role: str) -> None:
    if getattr(engine, "_closed", None) is not True:
        raise ProbeError(f"{role} engine did not report a clean close")
    process = getattr(engine, "_process", None)
    if process is None or not callable(getattr(process, "poll", None)):
        raise ProbeError(f"{role} engine process identity is unavailable")
    if process.poll() is None:
        raise ProbeError(f"{role} engine process remains alive after close")
    for name in ("_stdout_thread", "_stderr_thread"):
        thread = getattr(engine, name, None)
        if thread is not None and callable(getattr(thread, "is_alive", None)):
            if thread.is_alive():
                raise ProbeError(
                    f"{role} engine protocol thread remains alive after close"
                )


def _verify_engine_launch_contract(
    engine: object,
    *,
    role: str,
    engine_path: Path,
    network_path: Path,
    probe_config: Mapping[str, object],
) -> None:
    expected_command = (os.fspath(engine_path),)
    command = getattr(engine, "command", None)
    if not isinstance(command, (tuple, list)) or tuple(command) != expected_command:
        raise ProbeError(
            f"{role} engine command does not target exactly the engine snapshot"
        )
    expected_options = _engine_settings(probe_config, network_path)
    options = getattr(engine, "initial_options", None)
    if not isinstance(options, (tuple, list)) or tuple(options) != expected_options:
        raise ProbeError(
            f"{role} engine EvalFile/options do not target exactly its snapshots"
        )


def _finalize_snapshot_bundle(
    lifecycle_engines: Sequence[tuple[str, object]],
    bundle: ProbeSnapshotBundle,
    guards: SnapshotGuardSet,
) -> None:
    for role, engine in lifecycle_engines:
        _verify_engine_clean_close(engine, role=role)
    _verify_snapshot_guards(guards)
    _authenticate_snapshot_bundle(
        bundle, phase="post-clean-close authentication"
    )
    _verify_snapshot_guards(guards)
    _close_snapshot_guards(guards)
    _remove_snapshot_bundle(bundle)


def _snapshot_provenance(
    artifact: ArtifactSnapshot,
    guard: SnapshotGuardBinding,
) -> dict[str, object]:
    snapshot_wire = {
        **_binding_wire(artifact.snapshot),
        "identity": _identity_wire(artifact.snapshot_identity),
        "mode": artifact.snapshot_mode,
        "read_only_applied": artifact.read_only_applied,
    }
    return {
        "original": {
            **_binding_wire(artifact.original),
            "identity": _identity_wire(artifact.original_identity),
            "mode": artifact.original_mode,
            "verified_before_snapshot": True,
        },
        "snapshot": snapshot_wire,
        "executed": {
            **_binding_wire(artifact.snapshot),
            "same_as_snapshot": True,
        },
        "guard": {
            "policy": "windows-live-handle-read-share-only-v1",
            "identity": dict(guard.identity),
            "held_from_post_create_through_clean_close": True,
            "write_share_denied": True,
            "delete_share_denied": True,
            "path_reopened_and_identity_matched": True,
            "released_before_snapshot_cleanup": True,
        },
    }


def _publish_cli_probe(
    *,
    authenticated: AuthenticatedSplitArtifact,
    output_rows: Sequence[Mapping[str, object]],
    summary: ProbeSummary,
    output_path: Path,
    execution_receipt_path: Path,
    bundle: ProbeSnapshotBundle,
    guards: SnapshotGuardSet,
) -> None:
    output_payload = b"".join(
        common.canonical_json_bytes(row) for row in output_rows
    )
    output_sha256 = common.sha256_bytes(output_payload)
    receipt = {
        "schema": EXECUTION_RECEIPT_SCHEMA,
        "snapshot_policy": SNAPSHOT_POLICY,
        "split_artifact": {
            "path": str(authenticated.path),
            "partition": authenticated.partition,
            "sha256": authenticated.sha256,
            "size_bytes": authenticated.size_bytes,
            "row_count": authenticated.row_count,
        },
        "split_receipt": {
            "path": str(authenticated.receipt_path),
            "sha256": authenticated.receipt_sha256,
            "size_bytes": authenticated.receipt_size_bytes,
        },
        "artifacts": {
            artifact.role: _snapshot_provenance(
                artifact, guards.artifact(artifact.role)
            )
            for artifact in bundle.artifacts
        },
        "execution": {
            "snapshots_verified_before_launch": True,
            "snapshots_verified_after_clean_close": True,
            "engines_clean_closed": True,
            "snapshot_directory": str(bundle.directory),
            "snapshots_removed_after_clean_close": True,
            "directory_guard": {
                "policy": "windows-live-handle-read-share-only-v1",
                "identity": dict(guards.directory.identity),
                "held_from_post_create_through_clean_close": True,
                "delete_share_denied": True,
                "path_reopened_and_identity_matched": True,
                "released_before_snapshot_cleanup": True,
            },
        },
        "output": {
            "path": str(output_path),
            "sha256": output_sha256,
            "size_bytes": len(output_payload),
            "row_count": len(output_rows),
        },
        "summary": {
            "positions": summary.positions,
            "current_searches": summary.current_searches,
            "teacher_searches": summary.teacher_searches,
            "restricted_teacher_searches": (
                summary.restricted_teacher_searches
            ),
        },
    }
    common.write_new_bytes(output_path, output_payload)
    try:
        common.write_new_json(execution_receipt_path, receipt)
    except BaseException as error:
        try:
            metadata = output_path.lstat()
            payload = common.read_stable_file_bytes(
                output_path, label="uncommitted dual-probe output"
            )
            if (
                _is_link_or_reparse(metadata)
                or not stat.S_ISREG(metadata.st_mode)
                or payload != output_payload
            ):
                raise ProbeError(
                    "cannot safely roll back uncommitted dual-probe output"
                )
            output_path.unlink()
        except BaseException as rollback_error:
            raise ProbeError(
                "receipt publication failed and output rollback was not proven"
            ) from rollback_error
        raise error


EngineContextFactory = Callable[
    [
        str,
        Path,
        Path,
        Mapping[str, object],
        float | None,
    ],
    ContextManager[EngineLike],
]


def _run_cli_probe(
    *,
    input_path: Path,
    split_receipt_path: Path,
    expected_split_receipt_sha256: str,
    output_path: Path,
    execution_receipt_path: Path,
    engine_path: Path,
    expected_engine_sha256: str,
    current_net_path: Path,
    expected_current_net_sha256: str,
    teacher_net_path: Path,
    expected_teacher_net_sha256: str,
    nodes: int,
    multipv: int,
    hash_mb: int,
    teacher_repeats: int,
    timeout: float | None,
    engine_context_factory: EngineContextFactory,
    snapshot_parent: Path | None = None,
    before_launch_hook: Callable[[ProbeSnapshotBundle], None] | None = None,
    after_probe_hook: Callable[[ProbeSnapshotBundle], None] | None = None,
) -> ProbeSummary:
    """Run the CLI lifecycle without ever executing mutable original paths."""

    output_absolute = Path(output_path).absolute()
    receipt_absolute = Path(execution_receipt_path).absolute()
    if output_absolute == receipt_absolute:
        raise ProbeError("output and execution receipt paths must differ")
    for label, path in (
        ("output", output_absolute),
        ("execution receipt", receipt_absolute),
    ):
        if os.path.lexists(path):
            raise FileExistsError(
                f"refusing to overwrite {label} artifact: {path}"
            )

    authenticated = authenticate_split_artifact(
        input_path,
        split_receipt_path,
        expected_split_receipt_sha256,
    )
    originals = {
        "engine": _bind_original_artifact(
            engine_path,
            expected_engine_sha256,
            label="engine",
        ),
        "current_net": _bind_original_artifact(
            current_net_path,
            expected_current_net_sha256,
            label="current network",
        ),
        "teacher_net": _bind_original_artifact(
            teacher_net_path,
            expected_teacher_net_sha256,
            label="teacher network",
        ),
    }
    bundle = _create_snapshot_bundle(
        originals, snapshot_parent=snapshot_parent
    )
    guards = _acquire_snapshot_guards(bundle)
    engine_snapshot = bundle.artifact("engine").snapshot.path
    current_snapshot = bundle.artifact("current_net").snapshot.path
    teacher_snapshot = bundle.artifact("teacher_net").snapshot.path
    config: dict[str, object] = {
        "engine": _binding_wire(bundle.artifact("engine").snapshot),
        "current_net": _binding_wire(
            bundle.artifact("current_net").snapshot
        ),
        "teacher_net": _binding_wire(
            bundle.artifact("teacher_net").snapshot
        ),
        "nodes": nodes,
        "multipv": multipv,
        "threads": 1,
        "hash_mb": hash_mb,
        "clear_hash": True,
        "teacher_repeat_count": teacher_repeats,
        "engine_options": _expected_engine_options(
            hash_mb=hash_mb,
            multipv=multipv,
        ),
        "probe_order_policy": "position-id-parity-alternating-v1",
    }
    validate_probe_config(config)

    lifecycle_engines: list[tuple[str, object]] = []
    output_rows: list[dict[str, object]] | None = None
    summary: ProbeSummary | None = None
    primary_error: BaseException | None = None
    try:
        _verify_snapshot_guards(guards)
        _authenticate_snapshot_bundle(
            bundle, phase="immediate pre-launch authentication"
        )
        if before_launch_hook is not None:
            before_launch_hook(bundle)
        _authenticate_snapshot_bundle(
            bundle, phase="final pre-launch authentication"
        )
        _verify_snapshot_guards(guards)
        current_context = engine_context_factory(
            "current",
            engine_snapshot,
            current_snapshot,
            config,
            timeout,
        )
        _authenticate_snapshot_bundle(
            bundle, phase="after current context construction"
        )
        _verify_snapshot_guards(guards)
        _verify_engine_launch_contract(
            current_context,
            role="current",
            engine_path=engine_snapshot,
            network_path=current_snapshot,
            probe_config=config,
        )
        with ExitStack() as stack:
            lifecycle_engines.append(("current", current_context))
            current_engine = stack.enter_context(current_context)
            lifecycle_engines[-1] = ("current", current_engine)
            _verify_snapshot_guards(guards)
            _authenticate_snapshot_bundle(
                bundle, phase="after current engine launch"
            )
            _verify_engine_launch_contract(
                current_engine,
                role="current",
                engine_path=engine_snapshot,
                network_path=current_snapshot,
                probe_config=config,
            )
            _authenticate_snapshot_bundle(
                bundle, phase="before teacher launch"
            )
            _verify_snapshot_guards(guards)
            teacher_context = engine_context_factory(
                "teacher",
                engine_snapshot,
                teacher_snapshot,
                config,
                timeout,
            )
            _authenticate_snapshot_bundle(
                bundle, phase="after teacher context construction"
            )
            _verify_snapshot_guards(guards)
            _verify_engine_launch_contract(
                teacher_context,
                role="teacher",
                engine_path=engine_snapshot,
                network_path=teacher_snapshot,
                probe_config=config,
            )
            lifecycle_engines.append(("teacher", teacher_context))
            teacher_engine = stack.enter_context(teacher_context)
            lifecycle_engines[-1] = ("teacher", teacher_engine)
            _verify_snapshot_guards(guards)
            _authenticate_snapshot_bundle(
                bundle, phase="after teacher engine launch"
            )
            _verify_engine_launch_contract(
                teacher_engine,
                role="teacher",
                engine_path=engine_snapshot,
                network_path=teacher_snapshot,
                probe_config=config,
            )
            output_rows, summary = _probe_authenticated_artifact(
                authenticated,
                current_engine,
                teacher_engine,
                probe_config=config,
                timeout=timeout,
            )
            if after_probe_hook is not None:
                after_probe_hook(bundle)
    except BaseException as error:
        primary_error = error

    try:
        _finalize_snapshot_bundle(lifecycle_engines, bundle, guards)
    except BaseException as finalization_error:
        if primary_error is not None:
            raise ProbeError(
                "dual probe failed and snapshot finalization was not proven; "
                "snapshots retained"
            ) from finalization_error
        raise ProbeError(
            "snapshot finalization was not proven; snapshots retained"
        ) from finalization_error
    if primary_error is not None:
        raise primary_error.with_traceback(primary_error.__traceback__)
    if output_rows is None or summary is None:
        raise ProbeError("dual probe completed without an in-memory result")

    _publish_cli_probe(
        authenticated=authenticated,
        output_rows=output_rows,
        summary=summary,
        output_path=output_absolute,
        execution_receipt_path=receipt_absolute,
        bundle=bundle,
        guards=guards,
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe current V3 versus run3b on Atomic mining candidates."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--split-receipt", type=Path, required=True)
    parser.add_argument("--split-receipt-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execution-receipt", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--engine-sha256", required=True)
    parser.add_argument("--current-net", type=Path, required=True)
    parser.add_argument("--current-net-sha256", required=True)
    parser.add_argument("--teacher-net", type=Path, required=True)
    parser.add_argument("--teacher-net-sha256", required=True)
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--multipv", type=int, required=True)
    parser.add_argument("--hash-mb", type=int, required=True)
    parser.add_argument("--teacher-repeats", type=int, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    summary = _run_cli_probe(
        input_path=arguments.input,
        split_receipt_path=arguments.split_receipt,
        expected_split_receipt_sha256=(
            arguments.split_receipt_sha256
        ),
        output_path=arguments.output,
        execution_receipt_path=arguments.execution_receipt,
        engine_path=arguments.engine,
        expected_engine_sha256=arguments.engine_sha256,
        current_net_path=arguments.current_net,
        expected_current_net_sha256=arguments.current_net_sha256,
        teacher_net_path=arguments.teacher_net,
        expected_teacher_net_sha256=arguments.teacher_net_sha256,
        nodes=arguments.nodes,
        multipv=arguments.multipv,
        hash_mb=arguments.hash_mb,
        teacher_repeats=arguments.teacher_repeats,
        timeout=arguments.timeout,
        engine_context_factory=(
            lambda _role, engine, network, config, timeout: UciEngine(
                [str(engine)],
                options=_engine_settings(config, network),
                command_timeout=(
                    timeout if timeout is not None else 120.0
                ),
            )
        ),
    )
    print(
        json.dumps(
            {
                "positions": summary.positions,
                "current_searches": summary.current_searches,
                "teacher_searches": summary.teacher_searches,
                "restricted_teacher_searches": (
                    summary.restricted_teacher_searches
                ),
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ASSIGNMENT_SCHEMA",
    "ArtifactBinding",
    "ArtifactObservation",
    "ArtifactSnapshot",
    "AuthenticatedSplitArtifact",
    "CLASSIFIER",
    "EXECUTION_RECEIPT_SCHEMA",
    "INPUT_SCHEMA",
    "OUTPUT_SCHEMA",
    "ProbeSnapshotBundle",
    "SNAPSHOT_POLICY",
    "EngineLike",
    "ProbeError",
    "ProbeSummary",
    "authenticate_split_artifact",
    "main",
    "probe_file",
    "probe_position",
    "validate_probe_config",
]
