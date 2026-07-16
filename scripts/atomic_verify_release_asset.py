#!/usr/bin/env python3
"""Verify one downloaded workflow artifact against frozen release metadata."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

try:
    from .atomic_release_manifest import (
        PROVENANCE_SUFFIX,
        ReleaseContractError,
        _is_regular_unlinked,
        _load_provenance,
        _validate_provenance,
        sha256,
    )
except ImportError:  # Direct script execution puts this directory on sys.path.
    from atomic_release_manifest import (
        PROVENANCE_SUFFIX,
        ReleaseContractError,
        _is_regular_unlinked,
        _load_provenance,
        _validate_provenance,
        sha256,
    )


def verify_release_asset(
    asset: Path,
    expected_sha256: str,
    version: str,
    commit: str,
    source_date_epoch: int,
    kind: str,
    platform: str,
    architecture: str,
) -> str:
    """Authenticate a downloaded asset and its sibling provenance descriptor."""

    asset = Path(os.path.abspath(asset))
    if not _is_regular_unlinked(asset):
        raise ReleaseContractError("downloaded asset is not one regular file")
    if len(expected_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        raise ReleaseContractError("expected SHA-256 must be 64 lowercase hex digits")

    provenance_path = asset.with_name(asset.name + PROVENANCE_SUFFIX)
    if not _is_regular_unlinked(provenance_path):
        raise ReleaseContractError("downloaded asset has no regular provenance descriptor")
    provenance = _load_provenance(provenance_path)
    _validate_provenance(
        provenance, asset.name, version, commit.lower(), source_date_epoch
    )
    expected_identity = {
        "kind": kind,
        "platform": platform,
        "architecture": architecture,
    }
    for key, expected in expected_identity.items():
        if provenance[key] != expected:
            raise ReleaseContractError(
                "downloaded asset provenance %s mismatch: %r != %r"
                % (key, provenance[key], expected)
            )
    if provenance["sha256"] != expected_sha256:
        raise ReleaseContractError(
            "downloaded asset provenance SHA-256 mismatch: %s != %s"
            % (provenance["sha256"], expected_sha256)
        )

    actual_sha256 = sha256(asset)
    if actual_sha256 != expected_sha256:
        raise ReleaseContractError(
            "downloaded asset SHA-256 mismatch: %s != %s"
            % (actual_sha256, expected_sha256)
        )
    return actual_sha256


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    digest = verify_release_asset(
        args.asset,
        args.sha256,
        args.version,
        args.commit,
        args.source_date_epoch,
        args.kind,
        args.platform,
        args.architecture,
    )
    print("authenticated downloaded release asset sha256=" + digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
