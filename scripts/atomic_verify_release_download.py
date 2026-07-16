#!/usr/bin/env python3
"""Require a GitHub-downloaded draft to match the local upload byte for byte."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import stat
from typing import Dict, Optional, Sequence, Tuple


class ReleaseDownloadError(RuntimeError):
    """The downloaded draft is not the exact candidate that was uploaded."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> Dict[str, Tuple[int, str]]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ReleaseDownloadError("release asset root is not a directory")
    inventory: Dict[str, Tuple[int, str]] = {}
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise ReleaseDownloadError(
                "release asset root must contain regular files only: " + path.name
            )
        inventory[path.name] = (path.stat().st_size, _sha256(path))
    if not inventory:
        raise ReleaseDownloadError("release asset root is empty")
    return inventory


def _checksum_contract(root: Path, inventory: Dict[str, Tuple[int, str]]) -> None:
    checksum_path = root / "SHA256SUMS"
    if "SHA256SUMS" not in inventory:
        raise ReleaseDownloadError("draft omits SHA256SUMS")
    expected_names = set(inventory) - {"SHA256SUMS"}
    declared: Dict[str, str] = {}
    try:
        lines = checksum_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReleaseDownloadError("SHA256SUMS is not strict ASCII") from error
    for line in lines:
        if len(line) < 67 or line[64:66] != "  ":
            raise ReleaseDownloadError("invalid SHA256SUMS line")
        digest, name = line[:64], line[66:]
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not name
            or "/" in name
            or "\\" in name
            or name in declared
        ):
            raise ReleaseDownloadError("invalid SHA256SUMS entry")
        declared[name] = digest
    if set(declared) != expected_names:
        raise ReleaseDownloadError(
            "SHA256SUMS file list differs from the downloaded draft"
        )
    for name, expected_digest in declared.items():
        if inventory[name][1] != expected_digest:
            raise ReleaseDownloadError("SHA256SUMS mismatch for " + name)


def verify_release_download(local: Path, downloaded: Path) -> int:
    local_inventory = _inventory(local)
    downloaded_inventory = _inventory(downloaded)
    if local_inventory != downloaded_inventory:
        local_names = set(local_inventory)
        downloaded_names = set(downloaded_inventory)
        if local_names != downloaded_names:
            raise ReleaseDownloadError(
                "GitHub draft file list differs (missing=%r extra=%r)"
                % (
                    sorted(local_names - downloaded_names),
                    sorted(downloaded_names - local_names),
                )
            )
        differing = sorted(
            name
            for name in local_names
            if local_inventory[name] != downloaded_inventory[name]
        )
        raise ReleaseDownloadError(
            "GitHub draft bytes differ for: " + ", ".join(differing)
        )
    _checksum_contract(downloaded.resolve(strict=True), downloaded_inventory)
    return len(downloaded_inventory)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--downloaded", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    count = verify_release_download(args.local, args.downloaded)
    print("authenticated %d GitHub-downloaded draft assets" % count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
