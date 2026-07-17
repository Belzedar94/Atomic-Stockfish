#!/usr/bin/env python3
"""Authenticate the release-facing contents of the Node UCI WASM archive."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Any, Dict, Sequence, Tuple


SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SOURCE_DATE_EPOCH = re.compile(r"^(?:0|[1-9][0-9]*)$")
RUNTIME_NAME = re.compile(r"^atomic-stockfish-nnue[A-Za-z0-9._+-]*$")
METADATA_FILES = {
    "AUTHORS",
    "CITATION.cff",
    "Copying.txt",
    "README.md",
    "manifest.json",
}
REQUIRED_RUNTIME_FILES = {
    "atomic-stockfish-nnue-node.mjs",
    "atomic-stockfish-nnue.js",
    "atomic-stockfish-nnue.wasm",
}
README_MARKERS = {
    "node ./atomic-stockfish-nnue-node.mjs",
    "node .\\atomic-stockfish-nnue-node.mjs",
    "`manifest.json`",
    "`atomic-stockfish-nnue.js`",
    "`atomic-stockfish-nnue.wasm`",
}
FORBIDDEN_README_MARKERS = {
    "tests/wasm-engine",
    "tests\\wasm-engine",
    "build/wasm-engine",
    "build\\wasm-engine",
    "/src/",
    "..\\tests",
}


class UciWasmArchiveError(RuntimeError):
    """The packaged Node UCI WASM artifact is incomplete or ambiguous."""


def _reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise UciWasmArchiveError("duplicate JSON key in UCI WASM manifest: " + key)
        value[key] = item
    return value


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    stream = archive.extractfile(member)
    if stream is None:
        raise UciWasmArchiveError("cannot read archive member: " + member.name)
    with stream:
        return stream.read()


def _hash_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> Tuple[int, str]:
    stream = archive.extractfile(member)
    if stream is None:
        raise UciWasmArchiveError("cannot hash archive member: " + member.name)
    digest = hashlib.sha256()
    total = 0
    with stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
    return total, digest.hexdigest()


def _safe_relative_name(name: str, root: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] != root
    ):
        raise UciWasmArchiveError("unsafe archive member path: " + name)
    relative = PurePosixPath(*path.parts[1:])
    if not relative.parts:
        return ""
    if len(relative.parts) != 1:
        raise UciWasmArchiveError(
            "archive files must be flat below the release root: " + name
        )
    return relative.as_posix()


def _load_manifest(payload: bytes) -> Dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise UciWasmArchiveError("invalid UCI WASM manifest: %s" % error) from error
    if not isinstance(value, dict):
        raise UciWasmArchiveError("UCI WASM manifest must be one JSON object")
    return value


def verify_uci_wasm_archive(
    archive_path: Path,
    version: str,
    source_date_epoch: int,
    expected_readme: Path,
) -> int:
    """Verify archive layout, release documentation and every runtime digest."""

    if SEMVER.fullmatch(version) is None:
        raise UciWasmArchiveError("invalid release version")
    if (
        not isinstance(source_date_epoch, int)
        or isinstance(source_date_epoch, bool)
        or source_date_epoch < 0
    ):
        raise UciWasmArchiveError("invalid source date epoch")
    if not archive_path.is_file() or archive_path.is_symlink():
        raise UciWasmArchiveError("UCI WASM archive must be one regular file")
    if not expected_readme.is_file() or expected_readme.is_symlink():
        raise UciWasmArchiveError("release README must be one regular file")

    root = "Atomic-Stockfish-%s-node-uci-nnue-wasm" % version
    try:
        archive = tarfile.open(archive_path, mode="r:xz")
    except (OSError, tarfile.TarError) as error:
        raise UciWasmArchiveError("invalid UCI WASM tar.xz: %s" % error) from error

    with archive:
        files: Dict[str, tarfile.TarInfo] = {}
        seen_paths = set()
        seen_casefold = set()
        root_directory_seen = False
        for member in archive.getmembers():
            relative = _safe_relative_name(member.name.rstrip("/"), root)
            normalized = member.name.rstrip("/")
            folded = normalized.casefold()
            if normalized in seen_paths or folded in seen_casefold:
                raise UciWasmArchiveError(
                    "duplicate archive member path: " + member.name
                )
            seen_paths.add(normalized)
            seen_casefold.add(folded)
            if relative == "":
                if not member.isdir():
                    raise UciWasmArchiveError("release root must be a directory")
                root_directory_seen = True
                continue
            if not member.isfile():
                raise UciWasmArchiveError(
                    "archive payload members must be regular files: " + member.name
                )
            files[relative] = member

        if not root_directory_seen:
            raise UciWasmArchiveError("archive is missing its release root directory")
        missing_metadata = METADATA_FILES.difference(files)
        if missing_metadata:
            raise UciWasmArchiveError(
                "archive is missing release metadata: "
                + ", ".join(sorted(missing_metadata))
            )

        readme = _read_member(archive, files["README.md"])
        try:
            expected_readme_bytes = expected_readme.read_bytes()
        except OSError as error:
            raise UciWasmArchiveError(
                "cannot read release README: %s" % error
            ) from error
        if readme != expected_readme_bytes:
            raise UciWasmArchiveError("archive README differs from the release README")
        try:
            readme_text = readme.decode("utf-8")
        except UnicodeError as error:
            raise UciWasmArchiveError("archive README is not UTF-8") from error
        missing_markers = [
            marker for marker in README_MARKERS if marker not in readme_text
        ]
        if missing_markers:
            raise UciWasmArchiveError(
                "release README is missing runnable archive commands: "
                + ", ".join(sorted(missing_markers))
            )
        forbidden = [
            marker for marker in FORBIDDEN_README_MARKERS if marker in readme_text
        ]
        if forbidden:
            raise UciWasmArchiveError(
                "release README references development-only paths: "
                + ", ".join(sorted(forbidden))
            )

        manifest = _load_manifest(_read_member(archive, files["manifest.json"]))
        expected_contract = {
            "schemaVersion": 2,
            "target": "node-uci-nnue",
            "sourceDateEpoch": source_date_epoch,
            "initialMemoryBytes": 536870912,
            "memoryGrowth": False,
            "pthreadPoolSize": 4,
            "supportedEntrypoint": "atomic-stockfish-nnue-node.mjs",
            "generatedRuntimeGlue": "atomic-stockfish-nnue.js",
            "directRuntimeGlueSupported": False,
            "supportedNetworkBackends": [
                "Legacy Atomic V1",
                "AtomicNNUEV2",
                "AtomicNNUEV3",
            ],
            "networkFileVersions": ["0x7AF32F20", "0xA70C0002", "0xA70C0003"],
            "externalNetwork": True,
        }
        for key, expected in expected_contract.items():
            if manifest.get(key) != expected:
                raise UciWasmArchiveError("invalid manifest contract field: " + key)

        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise UciWasmArchiveError("manifest artifacts must be a non-empty array")
        runtime_names = set()
        runtime_casefold = set()
        for artifact in artifacts:
            if not isinstance(artifact, dict) or set(artifact) != {
                "name",
                "bytes",
                "sha256",
            }:
                raise UciWasmArchiveError("invalid manifest artifact descriptor")
            name = artifact["name"]
            size = artifact["bytes"]
            digest = artifact["sha256"]
            if (
                not isinstance(name, str)
                or RUNTIME_NAME.fullmatch(name) is None
                or name in runtime_names
                or name.casefold() in runtime_casefold
            ):
                raise UciWasmArchiveError("unsafe or duplicate runtime artifact name")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise UciWasmArchiveError(
                    "invalid runtime artifact byte count: " + name
                )
            if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
                raise UciWasmArchiveError("invalid runtime artifact SHA-256: " + name)
            if name not in files:
                raise UciWasmArchiveError(
                    "manifest runtime is missing from archive: " + name
                )
            actual_size, actual_digest = _hash_member(archive, files[name])
            if actual_size != size or actual_digest != digest:
                raise UciWasmArchiveError("runtime bytes differ from manifest: " + name)
            runtime_names.add(name)
            runtime_casefold.add(name.casefold())

        missing_runtime = REQUIRED_RUNTIME_FILES.difference(runtime_names)
        if missing_runtime:
            raise UciWasmArchiveError(
                "archive is missing required runtime files: "
                + ", ".join(sorted(missing_runtime))
            )
        expected_files = METADATA_FILES.union(runtime_names)
        if set(files) != expected_files:
            unexpected = sorted(set(files).difference(expected_files))
            missing = sorted(expected_files.difference(files))
            raise UciWasmArchiveError(
                "archive file set differs from manifest; missing=%s unexpected=%s"
                % (missing, unexpected)
            )

    return len(files)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-date-epoch", required=True)
    parser.add_argument("--readme", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if SOURCE_DATE_EPOCH.fullmatch(args.source_date_epoch) is None:
        raise UciWasmArchiveError("source date epoch must be canonical decimal")
    count = verify_uci_wasm_archive(
        args.archive, args.version, int(args.source_date_epoch), args.readme
    )
    print("authenticated release UCI WASM archive files=%d" % count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
