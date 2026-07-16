#!/usr/bin/env python3
"""Normalize one trusted setuptools sdist into a reproducible tar.gz.

Setuptools currently records wall-clock mtimes in both gzip and PAX headers on
some supported platforms.  This helper rejects surprising archive members,
sorts the single package tree, normalizes ownership/mode/time metadata and
writes a gzip stream whose timestamp and filename fields are fixed.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
from typing import Optional, Sequence


class SdistContractError(RuntimeError):
    """The generated sdist is outside the frozen release contract."""


def _safe_member_name(name: str, expected_root: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and ".." not in path.parts
        and bool(path.parts)
        and path.parts[0] == expected_root
    )


def _normalized_mode(member: tarfile.TarInfo) -> int:
    if member.isdir():
        return 0o755
    return 0o755 if member.mode & 0o111 else 0o644


def normalize_sdist(
    source: Path,
    destination: Path,
    expected_root: str,
    source_date_epoch: int,
) -> str:
    source = Path(os.path.abspath(source))
    try:
        source_stat = source.lstat()
    except OSError as error:
        raise SdistContractError("source sdist must be one regular file") from error
    if not stat.S_ISREG(source_stat.st_mode):
        raise SdistContractError("source sdist must be one regular file")
    if not expected_root or "/" in expected_root or "\\" in expected_root:
        raise SdistContractError("expected root must be one safe path component")
    if source_date_epoch < 0:
        raise SdistContractError("source-date epoch must be non-negative")

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise SdistContractError("normalized sdist destination already exists")

    try:
        with tarfile.open(source, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise SdistContractError("sdist contains duplicate member names")
            if not members or any(
                not _safe_member_name(member.name, expected_root)
                for member in members
            ):
                raise SdistContractError("sdist contains an unsafe or foreign member")
            if any(not (member.isdir() or member.isfile()) for member in members):
                raise SdistContractError("sdist may contain only directories and files")

            with destination.open("xb") as raw_output:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=9,
                    fileobj=raw_output,
                    mtime=0,
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                    ) as normalized:
                        for member in sorted(members, key=lambda item: item.name):
                            output_member = copy.copy(member)
                            output_member.uid = 0
                            output_member.gid = 0
                            output_member.uname = ""
                            output_member.gname = ""
                            output_member.mtime = source_date_epoch
                            output_member.mode = _normalized_mode(member)
                            output_member.pax_headers = {}
                            if member.isfile():
                                payload = archive.extractfile(member)
                                if payload is None:
                                    raise SdistContractError(
                                        "regular sdist member has no payload"
                                    )
                                normalized.addfile(output_member, payload)
                            else:
                                normalized.addfile(output_member)
                raw_output.flush()
                os.fsync(raw_output.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return digest


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    digest = normalize_sdist(
        args.source, args.destination, args.root, args.source_date_epoch
    )
    print("normalized reproducible sdist sha256=" + digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
