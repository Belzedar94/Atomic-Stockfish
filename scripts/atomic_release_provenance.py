#!/usr/bin/env python3
"""Write one frozen provenance descriptor next to a release asset."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
from typing import Optional, Sequence

try:
    from .atomic_release_manifest import (
        COMMIT,
        KINDS,
        PROVENANCE_SUFFIX,
        SAFE_NAME,
        SEMVER,
        sha256,
    )
except ImportError:  # Direct script execution puts this directory on sys.path.
    from atomic_release_manifest import (
        COMMIT,
        KINDS,
        PROVENANCE_SUFFIX,
        SAFE_NAME,
        SEMVER,
        sha256,
    )


def write_provenance(
    asset: Path,
    version: str,
    commit: str,
    source_date_epoch: int,
    kind: str,
    platform: str,
    architecture: str,
    toolchain: str,
    build_command: Sequence[str],
) -> Path:
    asset = Path(os.path.abspath(asset))
    commit = commit.lower()
    try:
        asset_stat = asset.lstat()
    except OSError as error:
        raise ValueError("asset must be a regular file with a safe basename") from error
    if not stat.S_ISREG(asset_stat.st_mode) or not SAFE_NAME.fullmatch(asset.name):
        raise ValueError("asset must be a regular file with a safe basename")
    if not SEMVER.fullmatch(version):
        raise ValueError("version must be x.y.z")
    if not COMMIT.fullmatch(commit):
        raise ValueError("commit must be one full lowercase SHA-1")
    if source_date_epoch < 0:
        raise ValueError("source-date epoch must be non-negative")
    if kind not in KINDS:
        raise ValueError("unsupported release asset kind")
    for name, value in (
        ("platform", platform),
        ("architecture", architecture),
        ("toolchain", toolchain),
    ):
        if not value or not value.strip():
            raise ValueError(name + " must be non-empty")
    if not build_command or any(not part for part in build_command):
        raise ValueError("build command must contain non-empty arguments")

    value = {
        "schemaVersion": 2,
        "asset": asset.name,
        "version": version,
        "commit": commit,
        "sourceDateEpoch": source_date_epoch,
        "kind": kind,
        "platform": platform,
        "architecture": architecture,
        "toolchain": toolchain,
        "buildCommand": list(build_command),
        "sha256": sha256(asset),
    }
    output = asset.with_name(asset.name + PROVENANCE_SUFFIX)
    payload = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    with output.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return output


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--kind", choices=sorted(KINDS), required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("build_command", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.build_command[:1] == ["--"]:
        args.build_command = args.build_command[1:]
    output = write_provenance(
        args.asset,
        args.version,
        args.commit,
        args.source_date_epoch,
        args.kind,
        args.platform,
        args.architecture,
        args.toolchain,
        args.build_command,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
