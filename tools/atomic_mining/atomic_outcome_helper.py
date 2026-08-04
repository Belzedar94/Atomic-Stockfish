"""Native, provenance-bound Atomic outcome helper for result-bearing E00 games.

The helper deliberately does not use :mod:`chess.variant` for adjudication.
Instead it calls the Atomic-Stockfish ``pyffish`` binding, whose board and
outcome functions are compiled from ``src/api/atomic_*``.  The public client
executes this module in a fresh subprocess so a native crash or hang is bounded
by a caller supplied deadline.

Protocol v1 is one canonical UTF-8 JSON request on stdin and one canonical
UTF-8 JSON response on stdout.  Any non-zero exit, stderr, non-canonical JSON,
hash drift, source drift, or timeout is a terminal trust failure.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import sysconfig
from types import ModuleType
from typing import Mapping, Sequence


REQUEST_SCHEMA = "atomic-e00-native-outcome-request-v1"
RESPONSE_SCHEMA = "atomic-e00-native-outcome-response-v1"
PROVENANCE_SCHEMA = "atomic-e00-native-outcome-provenance-v1"
BUILD_MANIFEST_SCHEMA = "atomic-e00-native-outcome-build-v1"

MAX_REQUEST_BYTES = 1 << 20
MAX_FEN_BYTES = 512
MAX_HISTORY_PLIES = 4096
_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_BINDING_SOURCES = (
    "setup.py",
    "src/pyffish.cpp",
    "src/atomic_init.cpp",
    "src/attacks.cpp",
    "src/bitboard.cpp",
    "src/misc.cpp",
    "src/movegen.cpp",
    "src/position.cpp",
    "src/uci_move.cpp",
    "src/syzygy/tbprobe.cpp",
    "src/api/atomic_board.cpp",
    "src/api/atomic_fen.cpp",
    "src/api/atomic_notation.cpp",
    "src/api/atomic_outcome.cpp",
)
_BUILD_ENVIRONMENT = frozenset(
    {
        "CC",
        "CFLAGS",
        "CL",
        "CPPFLAGS",
        "CXX",
        "CXXFLAGS",
        "LDFLAGS",
        "_CL_",
    }
)
_TERMINATIONS = frozenset(
    {
        "ongoing",
        "atomic-explosion",
        "checkmate",
        "stalemate",
        "insufficient-material",
        "fifty-move-rule",
        "threefold-repetition",
    }
)


class AtomicOutcomeHelperError(RuntimeError):
    """The native outcome helper failed closed."""


class AtomicOutcomeProtocolError(AtomicOutcomeHelperError):
    """The request or response did not match the frozen protocol."""


class AtomicOutcomeProvenanceError(AtomicOutcomeHelperError):
    """A runtime binary or rules source did not match its frozen identity."""


class AtomicOutcomeTimeout(AtomicOutcomeHelperError):
    """The isolated native helper exceeded its deadline."""


@dataclass(frozen=True)
class AtomicOutcome:
    """Canonical Atomic result from White's perspective."""

    terminal: bool
    termination: str
    result_white: str
    value_side_to_move: int
    final_fen: str

    def __post_init__(self) -> None:
        if not isinstance(self.terminal, bool):
            raise AtomicOutcomeProtocolError("terminal flag must be boolean")
        if not isinstance(self.termination, str):
            raise AtomicOutcomeProtocolError("termination must be text")
        if not isinstance(self.result_white, str):
            raise AtomicOutcomeProtocolError("result must be text")
        if (
            not isinstance(self.final_fen, str)
            or len(self.final_fen.split()) != 6
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.final_fen
            )
        ):
            raise AtomicOutcomeProtocolError("final FEN must be clean six-field text")
        if self.termination not in _TERMINATIONS:
            raise AtomicOutcomeProtocolError("unknown Atomic termination")
        if self.terminal != (self.termination != "ongoing"):
            raise AtomicOutcomeProtocolError("terminal flag and termination disagree")
        allowed_results = {"1-0", "0-1", "1/2-1/2"} if self.terminal else {"*"}
        if self.result_white not in allowed_results:
            raise AtomicOutcomeProtocolError("result and terminal flag disagree")
        if isinstance(self.value_side_to_move, bool) or not isinstance(
            self.value_side_to_move, int
        ):
            raise AtomicOutcomeProtocolError("native outcome value must be an integer")

    def payload(self) -> dict[str, object]:
        return {
            "final_fen": self.final_fen,
            "result_white": self.result_white,
            "terminal": self.terminal,
            "termination": self.termination,
            "value_side_to_move": self.value_side_to_move,
        }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _require_regular_file(path: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise AtomicOutcomeProvenanceError(f"{label} is not a regular file")
    return resolved


def _require_sha256(value: str, *, label: str) -> str:
    rendered = str(value).lower()
    if not _SHA256.fullmatch(rendered):
        raise AtomicOutcomeProvenanceError(f"{label} is not a lowercase SHA-256")
    return rendered


def _require_commit(value: str) -> str:
    rendered = str(value).lower()
    if not _COMMIT.fullmatch(rendered):
        raise AtomicOutcomeProvenanceError(
            "source commit must contain 40 to 64 lowercase hex characters"
        )
    return rendered


def _python_runtime_artifacts() -> dict[str, dict[str, object]]:
    candidates: dict[str, Path] = {}
    if os.name == "nt":
        library = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
        for directory in {
            Path(sys.base_prefix),
            Path(sys.prefix),
            Path(sys.executable).resolve().parent,
        }:
            candidate = directory / library
            if candidate.is_file():
                candidates[library] = candidate
        if library not in candidates:
            raise AtomicOutcomeProvenanceError(
                f"cannot authenticate Python runtime library {library}"
            )
    else:
        library = sysconfig.get_config_var("LDLIBRARY")
        library_dir = sysconfig.get_config_var("LIBDIR")
        if isinstance(library, str) and isinstance(library_dir, str):
            candidate = Path(library_dir) / library
            if candidate.is_file():
                candidates[library] = candidate
    return {
        name: {
            "bytes": path.stat().st_size,
            "path": str(path.resolve()),
            "sha256": _sha256_file(path),
        }
        for name, path in sorted(candidates.items())
    }


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _validate_artifact_identity(
    value: object, *, label: str, expected_path: Path | None = None
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "bytes",
        "path",
        "sha256",
    }:
        raise AtomicOutcomeProvenanceError(
            f"{label} artifact fields differ"
        )
    size = value["bytes"]
    digest = value["sha256"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise AtomicOutcomeProvenanceError(f"{label} byte size is malformed")
    expected_digest = _require_sha256(str(digest), label=f"{label} SHA-256")
    path = _require_regular_file(Path(str(value["path"])), label=label)
    if expected_path is not None and path != expected_path.resolve():
        raise AtomicOutcomeProvenanceError(f"{label} path differs")
    if path.stat().st_size != size or _sha256_file(path) != expected_digest:
        raise AtomicOutcomeProvenanceError(f"{label} artifact changed")
    return {
        "bytes": size,
        "path": str(path),
        "sha256": expected_digest,
    }


def _validated_request(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "claim_draw",
        "moves",
        "root_fen",
        "schema",
    }:
        raise AtomicOutcomeProtocolError("request fields differ from protocol v1")
    if value["schema"] != REQUEST_SCHEMA:
        raise AtomicOutcomeProtocolError("request schema differs")
    root_fen = value["root_fen"]
    if (
        not isinstance(root_fen, str)
        or not root_fen
        or len(root_fen.encode("utf-8")) > MAX_FEN_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in root_fen)
    ):
        raise AtomicOutcomeProtocolError("root_fen is not clean bounded text")
    moves = value["moves"]
    if (
        not isinstance(moves, list)
        or len(moves) > MAX_HISTORY_PLIES
        or any(not isinstance(move, str) or not _MOVE.fullmatch(move) for move in moves)
    ):
        raise AtomicOutcomeProtocolError("moves are not a bounded Atomic UCI history")
    if not isinstance(value["claim_draw"], bool):
        raise AtomicOutcomeProtocolError("claim_draw must be boolean")
    return {
        "claim_draw": value["claim_draw"],
        "moves": list(moves),
        "root_fen": root_fen,
        "schema": REQUEST_SCHEMA,
    }


def parse_request_bytes(value: bytes) -> dict[str, object]:
    """Parse one canonical request, rejecting BOMs, trailing data and drift."""

    if not value or len(value) > MAX_REQUEST_BYTES:
        raise AtomicOutcomeProtocolError("request byte length is invalid")
    if value.startswith(b"\xef\xbb\xbf"):
        raise AtomicOutcomeProtocolError("request UTF-8 BOM is forbidden")
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise AtomicOutcomeProtocolError("request is not strict UTF-8") from error
    if not text.endswith("\n") or "\n" in text[:-1] or "\r" in text:
        raise AtomicOutcomeProtocolError(
            "request must be exactly one LF-terminated line"
        )
    try:
        decoded = json.loads(text[:-1])
    except json.JSONDecodeError as error:
        raise AtomicOutcomeProtocolError("request is not valid JSON") from error
    request = _validated_request(decoded)
    if value != _canonical_json_bytes(request) + b"\n":
        raise AtomicOutcomeProtocolError("request is not canonical JSON")
    return request


def _native_result(value: int, final_fen: str, *, terminal: bool) -> str:
    if not terminal:
        return "*"
    if value == 0:
        return "1/2-1/2"
    fields = final_fen.split()
    if len(fields) != 6 or fields[1] not in {"w", "b"}:
        raise AtomicOutcomeProtocolError("native binding returned malformed final FEN")
    side_to_move_is_white = fields[1] == "w"
    winner_is_white = side_to_move_is_white if value > 0 else not side_to_move_is_white
    return "1-0" if winner_is_white else "0-1"


def _call_tuple(function: object, *arguments: object, label: str) -> tuple[object, ...]:
    if not callable(function):
        raise AtomicOutcomeProvenanceError(f"native binding lacks {label}")
    result = function(*arguments)
    if not isinstance(result, (tuple, list)):
        raise AtomicOutcomeProtocolError(f"native {label} returned the wrong type")
    return tuple(result)


def evaluate_atomic_outcome(
    native: ModuleType,
    root_fen: str,
    moves: Sequence[str],
    *,
    claim_draw: bool = True,
) -> AtomicOutcome:
    """Evaluate using the exact ordering frozen in ``Atomic::outcome()``.

    ``root_fen`` plus the complete reversible history is supplied on every
    native call.  This preserves the C++ ``StateInfo`` chain needed for
    current-position repetition, instead of reconstructing only the final FEN.
    """

    request = _validated_request(
        {
            "claim_draw": claim_draw,
            "moves": list(moves),
            "root_fen": root_fen,
            "schema": REQUEST_SCHEMA,
        }
    )
    history = request["moves"]
    assert isinstance(history, list)

    fen_ok = getattr(native, "FEN_OK", 1)
    validate_fen = getattr(native, "validate_fen", None)
    if not callable(validate_fen):
        raise AtomicOutcomeProvenanceError("native binding lacks validate_fen")
    if validate_fen(root_fen, "atomic", False) != fen_ok:
        raise AtomicOutcomeProtocolError(
            "root FEN is invalid under native Atomic rules"
        )

    get_fen = getattr(native, "get_fen", None)
    if not callable(get_fen):
        raise AtomicOutcomeProvenanceError("native binding lacks get_fen")
    final_fen = get_fen("atomic", root_fen, history, False)
    if not isinstance(final_fen, str):
        raise AtomicOutcomeProtocolError("native get_fen returned the wrong type")

    # This is the exact C++ order: explosion, insufficient material,
    # checkmate/stalemate, then optional current-position draws.
    immediate = _call_tuple(
        getattr(native, "is_immediate_game_end", None),
        "atomic",
        root_fen,
        history,
        False,
        label="is_immediate_game_end",
    )
    if len(immediate) != 2 or not isinstance(immediate[0], bool):
        raise AtomicOutcomeProtocolError("native immediate outcome is malformed")
    if immediate[0]:
        value = immediate[1]
        if isinstance(value, bool) or not isinstance(value, int) or value == 0:
            raise AtomicOutcomeProtocolError("native explosion value is malformed")
        return AtomicOutcome(
            True,
            "atomic-explosion",
            _native_result(value, final_fen, terminal=True),
            value,
            final_fen,
        )

    insufficient = _call_tuple(
        getattr(native, "has_insufficient_material", None),
        "atomic",
        root_fen,
        history,
        False,
        label="has_insufficient_material",
    )
    if (
        len(insufficient) != 2
        or not isinstance(insufficient[0], bool)
        or not isinstance(insufficient[1], bool)
    ):
        raise AtomicOutcomeProtocolError("native material outcome is malformed")
    if insufficient == (True, True):
        return AtomicOutcome(
            True, "insufficient-material", "1/2-1/2", 0, final_fen
        )

    legal_moves = getattr(native, "legal_moves", None)
    if not callable(legal_moves):
        raise AtomicOutcomeProvenanceError("native binding lacks legal_moves")
    legal = legal_moves("atomic", root_fen, history, False)
    if not isinstance(legal, list) or any(not isinstance(move, str) for move in legal):
        raise AtomicOutcomeProtocolError("native legal_moves returned the wrong type")
    if not legal:
        in_check = getattr(native, "gives_check", None)
        game_result = getattr(native, "game_result", None)
        if not callable(in_check) or not callable(game_result):
            raise AtomicOutcomeProvenanceError(
                "native binding lacks terminal outcome primitives"
            )
        checked = in_check("atomic", root_fen, history, False)
        value = game_result("atomic", root_fen, history, False)
        if not isinstance(checked, bool) or isinstance(value, bool) or not isinstance(
            value, int
        ):
            raise AtomicOutcomeProtocolError("native terminal outcome is malformed")
        termination = "checkmate" if checked else "stalemate"
        return AtomicOutcome(
            True,
            termination,
            _native_result(value, final_fen, terminal=True),
            value,
            final_fen,
        )

    if claim_draw:
        optional = _call_tuple(
            getattr(native, "is_optional_game_end", None),
            "atomic",
            root_fen,
            history,
            False,
            0,
            label="is_optional_game_end",
        )
        if (
            len(optional) != 2
            or type(optional[0]) is not bool
            or type(optional[1]) is not int
            or optional[1] != 0
        ):
            raise AtomicOutcomeProtocolError("native optional outcome is malformed")
        if optional[0]:
            fields = final_fen.split()
            if len(fields) != 6:
                raise AtomicOutcomeProtocolError("native final FEN is malformed")
            try:
                halfmove = int(fields[4])
            except ValueError as error:
                raise AtomicOutcomeProtocolError(
                    "native final FEN has a malformed rule50 counter"
                ) from error
            termination = (
                "fifty-move-rule" if halfmove >= 100 else "threefold-repetition"
            )
            return AtomicOutcome(True, termination, "1/2-1/2", 0, final_fen)

    return AtomicOutcome(False, "ongoing", "*", 0, final_fen)


def _load_native_binding(
    path: Path, expected_sha256: str
) -> tuple[ModuleType, dict[str, object]]:
    if "pyffish" in sys.modules:
        raise AtomicOutcomeProvenanceError(
            "ambient pyffish import is forbidden"
        )
    resolved = _require_regular_file(path, label="pyffish binding")
    expected = _require_sha256(expected_sha256, label="expected pyffish SHA-256")
    actual = _sha256_file(resolved)
    if actual != expected:
        raise AtomicOutcomeProvenanceError("pyffish binding SHA-256 differs")
    spec = importlib.util.spec_from_file_location("pyffish", resolved)
    if spec is None or spec.loader is None:
        raise AtomicOutcomeProvenanceError("cannot construct pyffish import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyffish"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("pyffish", None)
        raise
    return module, {
        "bytes": resolved.stat().st_size,
        "path": str(resolved),
        "sha256": actual,
    }


def _load_build_manifest(
    path: Path,
    expected_sha256: str,
    *,
    native_binding: Mapping[str, object],
    source_root: Path,
    source_commit: str,
    manifest_binding_path: Path | None = None,
) -> dict[str, object]:
    resolved = _require_regular_file(path, label="native binding build manifest")
    expected = _require_sha256(
        expected_sha256, label="expected build manifest SHA-256"
    )
    wire = resolved.read_bytes()
    if _sha256_bytes(wire) != expected:
        raise AtomicOutcomeProvenanceError("build manifest SHA-256 differs")
    if not wire.endswith(b"\n") or b"\n" in wire[:-1] or b"\r" in wire:
        raise AtomicOutcomeProvenanceError(
            "build manifest must be one canonical JSON line"
        )
    try:
        manifest = json.loads(wire[:-1].decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AtomicOutcomeProvenanceError(
            "build manifest is not strict JSON"
        ) from error
    if wire != _canonical_json_bytes(manifest) + b"\n":
        raise AtomicOutcomeProvenanceError("build manifest is not canonical JSON")
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "binding",
            "build",
            "python",
            "python_build_dependencies",
            "schema",
            "source",
            "toolchain",
        }
        or manifest.get("schema") != BUILD_MANIFEST_SCHEMA
    ):
        raise AtomicOutcomeProvenanceError("build manifest schema differs")
    binding = manifest["binding"]
    build = manifest["build"]
    python = manifest["python"]
    dependencies = manifest["python_build_dependencies"]
    source = manifest["source"]
    toolchain = manifest["toolchain"]
    bound_path = (
        Path(manifest_binding_path).expanduser().resolve()
        if manifest_binding_path is not None
        else Path(str(native_binding.get("path"))).resolve()
    )
    validated_binding = _validate_artifact_identity(
        binding, label="manifest pyffish binding", expected_path=bound_path
    )
    if (
        validated_binding["sha256"] != native_binding.get("sha256")
        or validated_binding["bytes"] != native_binding.get("bytes")
    ):
        raise AtomicOutcomeProvenanceError(
            "build manifest does not bind the loaded pyffish artifact"
        )

    if not isinstance(build, dict) or set(build) != {
        "command",
        "environment",
        "returncode",
        "stderr",
        "stdout",
    }:
        raise AtomicOutcomeProvenanceError("build invocation fields differ")
    command = build["command"]
    environment = build["environment"]
    if (
        not isinstance(command, list)
        or len(command) < 2
        or any(
            not isinstance(argument, str)
            or not argument
            or "\x00" in argument
            or "\r" in argument
            or "\n" in argument
            for argument in command
        )
        or type(build["returncode"]) is not int
        or build["returncode"] != 0
    ):
        raise AtomicOutcomeProvenanceError("build invocation is malformed")
    if (
        not isinstance(environment, dict)
        or not set(environment).issubset(_BUILD_ENVIRONMENT)
        or any(
            not isinstance(value, str) or "\x00" in value
            for value in environment.values()
        )
    ):
        raise AtomicOutcomeProvenanceError("build environment is malformed")
    _validate_artifact_identity(build["stdout"], label="build stdout")
    _validate_artifact_identity(build["stderr"], label="build stderr")

    executable = _require_regular_file(
        Path(sys.executable), label="Python executable"
    )
    if not isinstance(python, dict) or set(python) != {
        "bytes",
        "cache_tag",
        "compiler",
        "path",
        "platform",
        "runtime_libraries",
        "sha256",
    }:
        raise AtomicOutcomeProvenanceError("build Python fields differ")
    validated_python = _validate_artifact_identity(
        {
            "bytes": python["bytes"],
            "path": python["path"],
            "sha256": python["sha256"],
        },
        label="build Python executable",
        expected_path=executable,
    )
    if (
        validated_python["sha256"] != _sha256_file(executable)
        or validated_python["bytes"] != executable.stat().st_size
        or python.get("runtime_libraries") != _python_runtime_artifacts()
        or python.get("cache_tag") != sys.implementation.cache_tag
        or python.get("compiler") != sys.version
        or python.get("platform") != sysconfig.get_platform()
    ):
        raise AtomicOutcomeProvenanceError(
            "build manifest does not bind the active Python runtime"
        )
    expected_dependencies = {
        "setuptools": _distribution_version("setuptools"),
        "wheel": _distribution_version("wheel"),
    }
    if dependencies != expected_dependencies:
        raise AtomicOutcomeProvenanceError(
            "build dependency identity differs"
        )

    if (
        not isinstance(toolchain, dict)
        or not toolchain
        or any(not isinstance(name, str) or not name for name in toolchain)
    ):
        raise AtomicOutcomeProvenanceError("build toolchain is malformed")
    if os.name == "nt" and set(toolchain) != {"cl.exe", "link.exe"}:
        raise AtomicOutcomeProvenanceError(
            "MSVC compiler/linker identity is incomplete"
        )
    for name, identity in sorted(toolchain.items()):
        _validate_artifact_identity(identity, label=f"toolchain {name}")

    if not isinstance(source, dict) or set(source) != {
        "commit",
        "files",
        "root",
    }:
        raise AtomicOutcomeProvenanceError("build source fields differ")
    commit, source_files = _rules_source_provenance(source_root, source_commit)
    if (
        source.get("commit") != commit
        or source.get("files") != source_files
        or Path(str(source.get("root"))).resolve()
        != source_root.expanduser().resolve()
    ):
        raise AtomicOutcomeProvenanceError(
            "build manifest does not bind the active rules source"
        )
    return {
        "bytes": resolved.stat().st_size,
        "path": str(resolved),
        "sha256": expected,
    }


def _rules_source_provenance(
    source_root: Path, expected_commit: str
) -> tuple[str, dict[str, dict[str, object]]]:
    root = source_root.expanduser().resolve()
    if not root.is_dir():
        raise AtomicOutcomeProvenanceError("source root is not a directory")
    expected = _require_commit(expected_commit)
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5.0,
        ).stdout.strip().lower()
        dirty = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                "setup.py",
                "src",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5.0,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise AtomicOutcomeProvenanceError(
            "cannot authenticate Atomic rules source"
        ) from error
    if head != expected:
        raise AtomicOutcomeProvenanceError("Atomic rules source commit differs")
    if dirty:
        raise AtomicOutcomeProvenanceError("Atomic rules source files are dirty")

    header_paths = tuple(
        path.relative_to(root).as_posix()
        for path in sorted((root / "src").rglob("*.h"))
    )
    source_paths = tuple(sorted(set(_BINDING_SOURCES + header_paths)))
    files: dict[str, dict[str, object]] = {}
    for relative in source_paths:
        path = _require_regular_file(root / relative, label=f"rules source {relative}")
        files[relative] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    return head, files


def _provenance(
    *,
    helper_path: Path,
    native_binding: Mapping[str, object],
    build_manifest: Mapping[str, object],
    source_root: Path,
    source_commit: str,
) -> dict[str, object]:
    helper = _require_regular_file(helper_path, label="outcome helper")
    python = _require_regular_file(Path(sys.executable), label="Python executable")
    commit, source_files = _rules_source_provenance(source_root, source_commit)
    return {
        "build_manifest": dict(build_manifest),
        "helper": {
            "bytes": helper.stat().st_size,
            "path": str(helper),
            "sha256": _sha256_file(helper),
        },
        "pyffish": dict(native_binding),
        "python": {
            "bytes": python.stat().st_size,
            "cache_tag": sys.implementation.cache_tag,
            "executable": str(python),
            "runtime_libraries": _python_runtime_artifacts(),
            "sha256": _sha256_file(python),
            "version": sys.version,
        },
        "rules_source": {
            "commit": commit,
            "files": source_files,
            "root": str(source_root.expanduser().resolve()),
        },
        "schema": PROVENANCE_SCHEMA,
    }


def _validated_response(value: object, request_sha256: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "outcome",
        "provenance",
        "request_sha256",
        "schema",
    }:
        raise AtomicOutcomeProtocolError("response fields differ from protocol v1")
    if value["schema"] != RESPONSE_SCHEMA or value["request_sha256"] != request_sha256:
        raise AtomicOutcomeProtocolError("response identity differs")
    outcome = value["outcome"]
    if not isinstance(outcome, dict) or set(outcome) != {
        "final_fen",
        "result_white",
        "terminal",
        "termination",
        "value_side_to_move",
    }:
        raise AtomicOutcomeProtocolError("response outcome fields differ")
    AtomicOutcome(
        terminal=outcome["terminal"],  # type: ignore[arg-type]
        termination=outcome["termination"],  # type: ignore[arg-type]
        result_white=outcome["result_white"],  # type: ignore[arg-type]
        value_side_to_move=outcome["value_side_to_move"],  # type: ignore[arg-type]
        final_fen=outcome["final_fen"],  # type: ignore[arg-type]
    )
    provenance = value["provenance"]
    if (
        not isinstance(provenance, dict)
        or provenance.get("schema") != PROVENANCE_SCHEMA
        or set(provenance)
        != {
            "build_manifest",
            "helper",
            "pyffish",
            "python",
            "rules_source",
            "schema",
        }
    ):
        raise AtomicOutcomeProtocolError("response provenance differs")
    for label in ("build_manifest", "helper", "pyffish"):
        artifact = provenance[label]
        if not isinstance(artifact, dict) or set(artifact) != {
            "bytes",
            "path",
            "sha256",
        }:
            raise AtomicOutcomeProtocolError(
                f"response {label} provenance differs"
            )
    python = provenance["python"]
    if not isinstance(python, dict) or set(python) != {
        "bytes",
        "cache_tag",
        "executable",
        "runtime_libraries",
        "sha256",
        "version",
    }:
        raise AtomicOutcomeProtocolError("response Python provenance differs")
    rules_source = provenance["rules_source"]
    if not isinstance(rules_source, dict) or set(rules_source) != {
        "commit",
        "files",
        "root",
    }:
        raise AtomicOutcomeProtocolError("response source provenance differs")
    return value


def invoke_atomic_outcome_helper(
    *,
    pyffish_path: Path,
    pyffish_sha256: str,
    build_manifest_path: Path,
    build_manifest_sha256: str,
    source_root: Path,
    source_commit: str,
    root_fen: str,
    moves: Sequence[str],
    claim_draw: bool = True,
    timeout_seconds: float = 5.0,
    python_executable: Path | None = None,
) -> dict[str, object]:
    """Run the native helper in an isolated, deadline-bounded subprocess."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive")
    request = _validated_request(
        {
            "claim_draw": claim_draw,
            "moves": list(moves),
            "root_fen": root_fen,
            "schema": REQUEST_SCHEMA,
        }
    )
    request_body = _canonical_json_bytes(request)
    request_wire = request_body + b"\n"
    executable = Path(python_executable or sys.executable).expanduser().resolve()
    command = [
        str(executable),
        str(Path(__file__).resolve()),
        "--pyffish",
        str(Path(pyffish_path).expanduser().resolve()),
        "--expected-pyffish-sha256",
        _require_sha256(pyffish_sha256, label="pyffish SHA-256"),
        "--build-manifest",
        str(Path(build_manifest_path).expanduser().resolve()),
        "--expected-build-manifest-sha256",
        _require_sha256(
            build_manifest_sha256, label="build manifest SHA-256"
        ),
        "--source-root",
        str(Path(source_root).expanduser().resolve()),
        "--expected-source-commit",
        _require_commit(source_commit),
    ]
    startup: dict[str, object] = {}
    if os.name == "nt":
        startup["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            command,
            input=request_wire,
            capture_output=True,
            check=False,
            timeout=float(timeout_seconds),
            **startup,
        )
    except subprocess.TimeoutExpired as error:
        raise AtomicOutcomeTimeout("native Atomic outcome helper timed out") from error
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AtomicOutcomeHelperError(
            f"native Atomic outcome helper failed with exit {completed.returncode}: "
            f"{diagnostic}"
        )
    if completed.stderr:
        raise AtomicOutcomeProtocolError("native outcome helper emitted stderr")
    if not completed.stdout.endswith(b"\n") or b"\n" in completed.stdout[:-1]:
        raise AtomicOutcomeProtocolError("response must be exactly one JSON line")
    try:
        decoded = json.loads(completed.stdout[:-1].decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AtomicOutcomeProtocolError("response is not strict JSON") from error
    response = _validated_response(decoded, _sha256_bytes(request_body))
    if completed.stdout != _canonical_json_bytes(response) + b"\n":
        raise AtomicOutcomeProtocolError("response is not canonical JSON")
    provenance = response["provenance"]
    assert isinstance(provenance, dict)
    binding_identity = provenance["pyffish"]
    manifest_identity = provenance["build_manifest"]
    source_identity = provenance["rules_source"]
    assert isinstance(binding_identity, dict)
    assert isinstance(manifest_identity, dict)
    assert isinstance(source_identity, dict)
    returned_sha = binding_identity["sha256"]
    if returned_sha != pyffish_sha256.lower():
        raise AtomicOutcomeProvenanceError("response pyffish identity differs")
    returned_manifest_sha = manifest_identity["sha256"]
    if returned_manifest_sha != build_manifest_sha256.lower():
        raise AtomicOutcomeProvenanceError(
            "response build manifest identity differs"
        )
    returned_commit = source_identity["commit"]
    if returned_commit != source_commit.lower():
        raise AtomicOutcomeProvenanceError("response source identity differs")
    return response


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Return one native, provenance-bound Atomic outcome"
    )
    parser.add_argument("--pyffish", required=True, type=Path)
    parser.add_argument("--expected-pyffish-sha256", required=True)
    parser.add_argument("--build-manifest", required=True, type=Path)
    parser.add_argument("--expected-build-manifest-sha256", required=True)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        request_wire = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        request = parse_request_bytes(request_wire)
        native, binding = _load_native_binding(
            arguments.pyffish, arguments.expected_pyffish_sha256
        )
        build_manifest = _load_build_manifest(
            arguments.build_manifest,
            arguments.expected_build_manifest_sha256,
            native_binding=binding,
            source_root=arguments.source_root,
            source_commit=arguments.expected_source_commit,
        )
        outcome = evaluate_atomic_outcome(
            native,
            request["root_fen"],  # type: ignore[arg-type]
            request["moves"],  # type: ignore[arg-type]
            claim_draw=request["claim_draw"],  # type: ignore[arg-type]
        )
        request_body = _canonical_json_bytes(request)
        response = {
            "outcome": outcome.payload(),
            "provenance": _provenance(
                helper_path=Path(__file__),
                native_binding=binding,
                build_manifest=build_manifest,
                source_root=arguments.source_root,
                source_commit=arguments.expected_source_commit,
            ),
            "request_sha256": _sha256_bytes(request_body),
            "schema": RESPONSE_SCHEMA,
        }
        sys.stdout.buffer.write(_canonical_json_bytes(response) + b"\n")
        sys.stdout.buffer.flush()
        return 0
    except (AtomicOutcomeHelperError, ImportError, OSError, ValueError) as error:
        print(f"atomic outcome helper: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AtomicOutcome",
    "AtomicOutcomeHelperError",
    "AtomicOutcomeProtocolError",
    "AtomicOutcomeProvenanceError",
    "AtomicOutcomeTimeout",
    "PROVENANCE_SCHEMA",
    "REQUEST_SCHEMA",
    "RESPONSE_SCHEMA",
    "evaluate_atomic_outcome",
    "invoke_atomic_outcome_helper",
    "parse_request_bytes",
]
