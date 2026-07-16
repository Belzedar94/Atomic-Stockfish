#!/usr/bin/env python3
"""Assemble authenticated Atomic-Stockfish release assets.

Every asset must arrive with a sibling ``.provenance.json`` descriptor.  The
assembler refuses ambiguous names, mutable inputs and existing destinations,
then copies and re-hashes every byte before writing the manifest and checksum
file last.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any, Dict, List, Optional, Sequence, Tuple


MANIFEST_NAME = "atomic-stockfish-release-manifest.json"
CHECKSUM_NAME = "SHA256SUMS"
PROVENANCE_SUFFIX = ".provenance.json"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
PROVENANCE_KEYS = {
    "schemaVersion",
    "asset",
    "version",
    "commit",
    "sourceDateEpoch",
    "kind",
    "platform",
    "architecture",
    "toolchain",
    "buildCommand",
    "sha256",
}
KINDS = {"native", "python", "board-wasm", "uci-wasm", "source"}
INVENTORY_RULE_KEYS = {"namePattern", "kind", "platform", "architecture"}
LINUX_NATIVE_IMAGE = (
    "gcc:14.2.0-bookworm@sha256:"
    "b99b86a28812b1e6453a231a947dc43d76fe192788a12f344a9b568bf9f5d24c"
)
LINUX_NATIVE_AMD64_MANIFEST = (
    "sha256:82549aa8f90ada3236a8be70c74543132a76662ef33f0c3271ed802b81584a82"
)
WINDOWS_NATIVE_IMAGE = (
    "dockcross/windows-static-x64@sha256:"
    "e5fde458b54dda21d0265516f0310bc017532dd6f4fdad0b7239dc6ccd0f8ca9"
)
PYTHON_MANYLINUX_X86_64_IMAGE = (
    "quay.io/pypa/manylinux_2_28_x86_64:2026.03.20-1@sha256:"
    "853663dc8253b62be437bb52a5caecffd020792af4442f55d927d22e0ea795ae"
)
RELEASE_CI_REQUIREMENTS_SHA256 = (
    "33f274924a8f41ca9cf4ddc891c0d488dc30491c29d2b034de9088d9d032dd28"
)
RELEASE_BUILD_REQUIREMENTS_SHA256 = (
    "1e4f6c667fd5fab07e2016986bfd330bca57a9562875869323c8c1fd09245f34"
)
RELEASE_WHEEL_TEST_REQUIREMENTS_SHA256 = (
    "b877081ac9f4a6aa56eff9c5ed6c7b832a9fc02ca2dca39f786401e1a03f842b"
)


class ReleaseContractError(RuntimeError):
    """The candidate release bundle violates the frozen publication contract."""


def expected_inventory_policy(version: str) -> Dict[str, Any]:
    """Return the machine-readable release gate frozen for this inventory."""

    return {
        "abi3AuditVersion": "0.0.26",
        "draftOnly": True,
        "immutableReleasesReadSecret": "ATOMIC_RELEASE_POLICY_TOKEN",
        "immutableReleasesRequired": True,
        "linuxNativeAmd64Manifest": LINUX_NATIVE_AMD64_MANIFEST,
        "linuxNativeImage": LINUX_NATIVE_IMAGE,
        "nativeBuildRepetitions": 2,
        "pythonManylinuxX86_64Image": PYTHON_MANYLINUX_X86_64_IMAGE,
        "releaseBuildRequirementsSha256": RELEASE_BUILD_REQUIREMENTS_SHA256,
        "releaseCiRequirementsSha256": RELEASE_CI_REQUIREMENTS_SHA256,
        "releaseWheelTestRequirementsSha256": (
            RELEASE_WHEEL_TEST_REQUIREMENTS_SHA256
        ),
        "pythonWheelBuildRepetitions": 2,
        "pythonWheelRuntimeSmoke": {
            "operatingSystems": ["ubuntu-24.04", "windows-2022"],
            "pythonVersions": ["3.9", "3.12", "3.14"],
        },
        "requiredWorkflow": {
            "event": "push",
            "name": "Atomic CI",
            "path": ".github/workflows/atomic.yml",
            "ref": "refs/tags/v" + version,
        },
        "sdistAuthority": "source-job-normalized-artifact",
        "tagObjectType": "annotated",
        "windowsMakeComp": "mingw",
        "windowsNativeImage": WINDOWS_NATIVE_IMAGE,
        "windowsRuntimeSmoke": "windows-2022",
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseContractError("duplicate JSON key in provenance: " + key)
        value[key] = item
    return value


def _load_provenance(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseContractError("invalid provenance %s: %s" % (path, error)) from error
    if not isinstance(value, dict):
        raise ReleaseContractError("provenance must be one JSON object: " + str(path))
    return value


def _is_regular_unlinked(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _validate_provenance(
    value: Dict[str, Any],
    asset_name: str,
    version: str,
    commit: str,
    source_date_epoch: int,
) -> None:
    if set(value) != PROVENANCE_KEYS:
        missing = sorted(PROVENANCE_KEYS - set(value))
        extra = sorted(set(value) - PROVENANCE_KEYS)
        raise ReleaseContractError(
            "provenance keys differ for %s (missing=%s extra=%s)"
            % (asset_name, missing, extra)
        )
    if value["schemaVersion"] != 2:
        raise ReleaseContractError("unsupported provenance schema for " + asset_name)
    expected = {
        "asset": asset_name,
        "version": version,
        "commit": commit,
        "sourceDateEpoch": source_date_epoch,
    }
    for key, wanted in expected.items():
        if value[key] != wanted:
            raise ReleaseContractError(
                "%s provenance %s mismatch: %r != %r"
                % (asset_name, key, value[key], wanted)
            )
    if value["kind"] not in KINDS:
        raise ReleaseContractError("invalid release asset kind for " + asset_name)
    for key in ("platform", "architecture", "toolchain"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ReleaseContractError("empty provenance %s for %s" % (key, asset_name))
    digest = value["sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ReleaseContractError("invalid provenance SHA-256 for " + asset_name)
    command = value["buildCommand"]
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise ReleaseContractError("invalid buildCommand for " + asset_name)


def discover_assets(
    input_root: Path, version: str, commit: str, source_date_epoch: int
) -> List[Tuple[Path, Dict[str, Any]]]:
    root = input_root.resolve(strict=True)
    candidates: List[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.endswith(PROVENANCE_SUFFIX):
            continue
        candidates.append(path)
    if not candidates:
        raise ReleaseContractError("release input contains no assets")

    seen = set()
    discovered: List[Tuple[Path, Dict[str, Any]]] = []
    for asset in sorted(candidates, key=lambda item: item.name.casefold()):
        name = asset.name
        if (
            name in {MANIFEST_NAME, CHECKSUM_NAME}
            or not SAFE_NAME.fullmatch(name)
            or name.casefold() in seen
        ):
            raise ReleaseContractError("unsafe or duplicate release asset name: " + name)
        seen.add(name.casefold())
        if not _is_regular_unlinked(asset):
            raise ReleaseContractError("release asset is not a regular file: " + str(asset))
        try:
            asset.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise ReleaseContractError("release asset escapes input root: " + str(asset)) from error

        provenance_path = asset.with_name(name + PROVENANCE_SUFFIX)
        if not _is_regular_unlinked(provenance_path):
            raise ReleaseContractError("missing regular provenance for " + name)
        provenance = _load_provenance(provenance_path)
        _validate_provenance(provenance, name, version, commit, source_date_epoch)
        if sha256(asset) != provenance["sha256"]:
            raise ReleaseContractError("provenance SHA-256 mismatch for " + name)
        discovered.append((asset, provenance))

    orphaned = sorted(
        path.name
        for path in root.rglob("*" + PROVENANCE_SUFFIX)
        if path.is_file()
        and path.name[: -len(PROVENANCE_SUFFIX)].casefold() not in seen
    )
    if orphaned:
        raise ReleaseContractError("orphaned provenance descriptors: " + repr(orphaned))
    return discovered


def validate_inventory(
    inventory_path: Path,
    assets: Sequence[Tuple[Path, Dict[str, Any]]],
    version: str,
) -> str:
    if not _is_regular_unlinked(inventory_path):
        raise ReleaseContractError("release inventory is not a regular file")
    value = _load_provenance(inventory_path)
    if set(value) != {
        "schemaVersion",
        "project",
        "version",
        "releasePolicy",
        "assets",
    }:
        raise ReleaseContractError("release inventory has unexpected keys")
    if (
        value["schemaVersion"] != 2
        or value["project"] != "Atomic-Stockfish"
        or value["version"] != version
        or value["releasePolicy"] != expected_inventory_policy(version)
        or not isinstance(value["assets"], list)
        or not value["assets"]
    ):
        raise ReleaseContractError("release inventory identity is invalid")

    matched_names = set()
    for index, rule in enumerate(value["assets"]):
        if not isinstance(rule, dict) or set(rule) != INVENTORY_RULE_KEYS:
            raise ReleaseContractError("invalid release inventory rule %d" % index)
        if (
            not isinstance(rule["namePattern"], str)
            or not rule["namePattern"]
            or rule["kind"] not in KINDS
            or any(
                not isinstance(rule[key], str) or not rule[key]
                for key in ("platform", "architecture")
            )
        ):
            raise ReleaseContractError("invalid release inventory rule %d" % index)
        matching = [
            (path, provenance)
            for path, provenance in assets
            if fnmatch.fnmatchcase(path.name, rule["namePattern"])
            and provenance["kind"] == rule["kind"]
            and provenance["platform"] == rule["platform"]
            and provenance["architecture"] == rule["architecture"]
        ]
        if len(matching) != 1:
            raise ReleaseContractError(
                "release inventory rule %d matched %d assets" % (index, len(matching))
            )
        name = matching[0][0].name
        if name in matched_names:
            raise ReleaseContractError("release inventory rules overlap on " + name)
        matched_names.add(name)
    actual_names = {path.name for path, _ in assets}
    if matched_names != actual_names:
        raise ReleaseContractError(
            "release inventory did not authenticate every asset: "
            + repr(sorted(actual_names - matched_names))
        )
    return sha256(inventory_path)


def _copy_authenticated(source: Path, destination: Path) -> Tuple[int, str]:
    before = source.lstat()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(source), flags)
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
    ):
        os.close(descriptor)
        raise ReleaseContractError("release asset changed before copying: " + source.name)
    source_digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb") as reader, destination.open("xb") as writer:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                source_digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    after = source.lstat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        destination.unlink(missing_ok=True)
        raise ReleaseContractError("release asset changed while copying: " + source.name)
    copied_digest = sha256(destination)
    if copied_digest != source_digest.hexdigest() or destination.stat().st_size != before.st_size:
        destination.unlink(missing_ok=True)
        raise ReleaseContractError("release asset changed after copying: " + source.name)
    return before.st_size, copied_digest


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def assemble(
    input_root: Path,
    output_dir: Path,
    version: str,
    commit: str,
    source_date_epoch: int,
    inventory_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if not SEMVER.fullmatch(version):
        raise ReleaseContractError("release version must be x.y.z")
    commit = commit.lower()
    if not COMMIT.fullmatch(commit):
        raise ReleaseContractError("release commit must be a full lowercase SHA-1")
    if source_date_epoch < 0:
        raise ReleaseContractError("source-date epoch must be non-negative")

    assets = discover_assets(input_root, version, commit, source_date_epoch)
    inventory_sha256 = (
        validate_inventory(inventory_path, assets, version)
        if inventory_path is not None
        else None
    )
    output = output_dir.resolve()
    if output.exists():
        raise ReleaseContractError("release output already exists: " + str(output))
    output.mkdir(parents=True, exist_ok=False)

    try:
        entries = []
        for source, provenance in assets:
            size, digest = _copy_authenticated(source, output / source.name)
            if digest != provenance["sha256"]:
                raise ReleaseContractError(
                    "release asset changed after provenance authentication: "
                    + source.name
                )
            entries.append(
                {
                    "name": source.name,
                    "bytes": size,
                    "sha256": digest,
                    "provenance": provenance,
                }
            )
        entries.sort(key=lambda item: item["name"])
        manifest: Dict[str, Any] = {
            "schemaVersion": 1,
            "project": "Atomic-Stockfish",
            "version": version,
            "tag": "v" + version,
            "commit": commit,
            "sourceDateEpoch": source_date_epoch,
            "artifacts": entries,
        }
        if inventory_sha256 is not None:
            manifest["inventorySha256"] = inventory_sha256
        manifest_bytes = (
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        ).encode("utf-8")
        _write_new(output / MANIFEST_NAME, manifest_bytes)

        checksum_entries = [(item["name"], item["sha256"]) for item in entries]
        checksum_entries.append((MANIFEST_NAME, sha256(output / MANIFEST_NAME)))
        checksum_entries.sort()
        checksum_bytes = "".join(
            "%s  %s\n" % (digest, name) for name, digest in checksum_entries
        ).encode("ascii")
        _write_new(output / CHECKSUM_NAME, checksum_bytes)
        return manifest
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--inventory", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    manifest = assemble(
        args.input_root,
        args.output_dir,
        args.version,
        args.commit,
        args.source_date_epoch,
        args.inventory,
    )
    print(
        "assembled %d authenticated Atomic-Stockfish %s assets"
        % (len(manifest["artifacts"]), manifest["version"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
