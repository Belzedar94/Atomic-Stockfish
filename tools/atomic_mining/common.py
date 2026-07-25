"""Shared, deterministic helpers for the Atomic match-mining tools.

The mining pipeline is deliberately standard-library only.  Every durable
artifact is canonical JSON/JSONL, is written through a create-new temporary
file, and refuses to replace an existing path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Iterable, Mapping, Sequence


class MiningArtifactError(RuntimeError):
    """A durable mining artifact violates the fail-closed contract."""


@dataclass(frozen=True)
class JsonlSnapshot:
    """One immutable read of a canonical JSONL artifact.

    ``rows``, ``sha256`` and ``size_bytes`` are all derived from the same byte
    payload.  Consumers should retain this object for both processing and
    provenance instead of reopening ``path``.
    """

    path: Path
    rows: tuple[dict[str, object], ...]
    sha256: str
    size_bytes: int


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    if is_dataclass(value):
        value = asdict(value)
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def canonical_json_line(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _stable_file_bytes(path: Path, *, label: str) -> tuple[bytes, os.stat_result]:
    """Read a regular file once and reject mutations observed during the read."""

    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise MiningArtifactError(f"{label} must be a regular file")
            payload = stream.read()
            after = os.fstat(stream.fileno())
    except OSError as error:
        raise MiningArtifactError(f"cannot read {label}: {path}") from error

    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity or len(payload) != before.st_size:
        raise MiningArtifactError(f"{label} changed while it was read")
    return payload, after


def read_stable_file_bytes(path: Path, *, label: str = "input") -> bytes:
    """Return one authenticated byte snapshot of a regular file.

    The public helper deliberately exposes only the bytes, not the mutable
    pathname or implementation-specific stat record.  A caller can therefore
    derive both its parsed content and provenance from the same single read.
    """

    payload, _identity = _stable_file_bytes(Path(path).absolute(), label=label)
    return payload


def _parse_canonical_jsonl(
    payload: bytes, *, source: Path
) -> tuple[dict[str, object], ...]:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise MiningArtifactError(f"{source}: UTF-8 BOM is forbidden")
    if b"\r" in payload:
        raise MiningArtifactError(f"{source}: CR characters are forbidden")
    if payload and not payload.endswith(b"\n"):
        raise MiningArtifactError(f"{source}: JSONL line is missing LF")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise MiningArtifactError(f"{source}: invalid UTF-8") from error
    if not payload:
        return ()

    rows: list[dict[str, object]] = []
    lines = text.split("\n")
    if payload:
        lines.pop()  # The required final LF produces one trailing empty item.
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise MiningArtifactError(
                f"{source}:{line_number}: empty JSONL rows are forbidden"
            )
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise MiningArtifactError(
                f"{source}:{line_number}: invalid JSON"
            ) from error
        if not isinstance(value, dict):
            raise MiningArtifactError(
                f"{source}:{line_number}: JSONL row must be an object"
            )
        try:
            canonical = canonical_json_line(value)
        except (TypeError, ValueError) as error:
            raise MiningArtifactError(
                f"{source}:{line_number}: JSON value is not canonicalizable"
            ) from error
        if canonical != line + "\n":
            raise MiningArtifactError(
                f"{source}:{line_number}: JSONL row is not canonical"
            )
        rows.append(value)
    return tuple(rows)


def load_jsonl_snapshot(path: Path) -> JsonlSnapshot:
    """Load canonical JSONL and bind its rows and provenance to one byte read."""

    requested = Path(path).absolute()
    payload, _identity = _stable_file_bytes(requested, label="JSONL input")
    rows = _parse_canonical_jsonl(payload, source=requested)
    return JsonlSnapshot(
        path=requested,
        rows=rows,
        sha256=sha256_bytes(payload),
        size_bytes=len(payload),
    )


def load_jsonl(path: Path) -> list[dict[str, object]]:
    """Compatibility wrapper; new consumers should retain JsonlSnapshot."""

    return list(load_jsonl_snapshot(path).rows)


def _verify_exact_payload(path: Path, expected: bytes, *, label: str) -> os.stat_result:
    observed, identity = _stable_file_bytes(path, label=label)
    if observed != expected:
        raise MiningArtifactError(f"{label} bytes differ from requested payload")
    return identity


def _fsync_directory(path: Path) -> None:
    """Durably flush directory metadata where the platform supports it."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        # Some filesystems do not permit directory fsync.  File contents have
        # already been fsynced and publication still remains atomic.
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _same_file_identity(path: Path, identity: os.stat_result) -> bool:
    try:
        observed = path.stat()
    except FileNotFoundError:
        return False
    return (
        observed.st_dev == identity.st_dev
        and observed.st_ino == identity.st_ino
    )


def write_new_bytes(path: Path, payload: bytes) -> None:
    """Publish exact bytes atomically, with create-new/no-overwrite semantics.

    The temporary file is fsynced and reread byte-for-byte before publication.
    A same-directory hard link then creates the destination atomically and
    fails if another writer won the destination race.  The published link is
    verified again before the temporary name is removed.
    """

    path = path.absolute()
    if path.exists():
        raise FileExistsError(f"refusing to overwrite mining artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    temporary_identity: os.stat_result | None = None
    published = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = -1
        temporary_identity = _verify_exact_payload(
            temporary,
            payload,
            label="temporary mining artifact",
        )
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise FileExistsError(
                f"refusing to overwrite mining artifact: {path}"
            ) from error
        published = True
        published_identity = _verify_exact_payload(
            path,
            payload,
            label="published mining artifact",
        )
        if (
            published_identity.st_dev != temporary_identity.st_dev
            or published_identity.st_ino != temporary_identity.st_ino
        ):
            raise MiningArtifactError(
                "published mining artifact is not the verified temporary inode"
            )
        _fsync_directory(path.parent)
        temporary.unlink()
        _fsync_directory(path.parent)
    except BaseException as error:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        cleanup_errors: list[OSError] = []
        if (
            published
            and temporary_identity is not None
            and _same_file_identity(path, temporary_identity)
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                cleanup_errors.append(cleanup_error)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup_error:
            cleanup_errors.append(cleanup_error)
        if cleanup_errors:
            raise MiningArtifactError(
                "failed to clean up an unpublished mining artifact"
            ) from error
        raise


def write_new_json(path: Path, value: object) -> None:
    write_new_bytes(path, canonical_json_bytes(value))


def write_new_jsonl(path: Path, rows: Iterable[object]) -> None:
    payload = b"".join(canonical_json_bytes(row) for row in rows)
    write_new_bytes(path, payload)


def require_exact_fields(
    value: Mapping[str, object], expected: Sequence[str], *, label: str
) -> None:
    actual = set(value)
    wanted = set(expected)
    if actual != wanted:
        missing = sorted(wanted - actual)
        extra = sorted(actual - wanted)
        raise MiningArtifactError(
            f"{label} fields differ; missing={missing}, extra={extra}"
        )


def require_lower_hex_sha256(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise MiningArtifactError(f"{label} must be a lowercase SHA-256")
    return value


def derive_id(namespace: str, *parts: str) -> str:
    document = {"namespace": namespace, "parts": list(parts)}
    return sha256_bytes(canonical_json_bytes(document))


def fen_fields(fen: str) -> tuple[str, str, str, str, int, int]:
    fields = fen.split()
    if len(fields) != 6:
        raise MiningArtifactError("Atomic FEN must contain exactly six fields")
    board, side, castling, en_passant, halfmove, fullmove = fields
    if side not in {"w", "b"}:
        raise MiningArtifactError("Atomic FEN side-to-move must be w or b")
    if castling != "-" and any(
        character not in "KQABCDEFGHkqabcdefgh" for character in castling
    ):
        raise MiningArtifactError("Atomic FEN has malformed castling rights")
    if en_passant != "-":
        if (
            len(en_passant) != 2
            or en_passant[0] not in "abcdefgh"
            or en_passant[1] not in "36"
        ):
            raise MiningArtifactError("Atomic FEN has malformed en-passant square")
    try:
        halfmove_value = int(halfmove)
        fullmove_value = int(fullmove)
    except ValueError as error:
        raise MiningArtifactError("Atomic FEN counters must be integers") from error
    if halfmove_value < 0 or fullmove_value <= 0:
        raise MiningArtifactError("Atomic FEN counters are out of range")
    return (
        board,
        side,
        castling,
        en_passant,
        halfmove_value,
        fullmove_value,
    )


def transposition_key(fen: str) -> str:
    """Return an analysis key, never a sufficient scientific identity.

    The key intentionally omits move counters so trajectories that transpose
    can be unioned into the same split component later.  Callers must retain
    the full FEN and history identity alongside this key.
    """

    board, side, castling, en_passant, _halfmove, _fullmove = fen_fields(fen)
    return derive_id(
        "atomic-mining-transposition-v1",
        board,
        side,
        castling,
        en_passant,
    )


__all__ = [
    "JsonlSnapshot",
    "MiningArtifactError",
    "canonical_json_bytes",
    "canonical_json_line",
    "derive_id",
    "fen_fields",
    "load_jsonl",
    "load_jsonl_snapshot",
    "read_stable_file_bytes",
    "require_exact_fields",
    "require_lower_hex_sha256",
    "sha256_bytes",
    "sha256_file",
    "transposition_key",
    "write_new_bytes",
    "write_new_json",
    "write_new_jsonl",
]
