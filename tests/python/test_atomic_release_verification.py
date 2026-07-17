import hashlib
import json
from pathlib import Path

import pytest

from scripts.atomic_release_provenance import write_provenance
from scripts.atomic_verify_release_asset import verify_release_asset
from scripts.atomic_verify_release_download import (
    ReleaseDownloadError,
    verify_release_download,
)


VERSION = "1.0.1"
COMMIT = "a" * 40
EPOCH = 1_700_000_000


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_downloaded_source_asset_requires_hash_and_exact_provenance(tmp_path: Path) -> None:
    asset = tmp_path / "atomic_pyffish-1.0.1.tar.gz"
    asset.write_bytes(b"normalized sdist")
    write_provenance(
        asset,
        VERSION,
        COMMIT,
        EPOCH,
        "python",
        "source",
        "source",
        "setuptools 80.9.0",
        ["python", "scripts/atomic_reproducible_sdist.py"],
    )

    assert (
        verify_release_asset(
            asset,
            digest(asset.read_bytes()),
            VERSION,
            COMMIT,
            EPOCH,
            "python",
            "source",
            "source",
        )
        == digest(asset.read_bytes())
    )

    asset.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        verify_release_asset(
            asset,
            digest(b"normalized sdist"),
            VERSION,
            COMMIT,
            EPOCH,
            "python",
            "source",
            "source",
        )


def test_downloaded_source_asset_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "atomic_pyffish-1.0.1.tar.gz"
    target.write_bytes(b"normalized sdist")
    link = tmp_path / "downloaded.tar.gz"
    try:
        link.symlink_to(target.name)
    except OSError as error:
        pytest.skip("platform cannot create test symlink: %s" % error)

    with pytest.raises(RuntimeError, match="regular file"):
        verify_release_asset(
            link,
            digest(target.read_bytes()),
            VERSION,
            COMMIT,
            EPOCH,
            "python",
            "source",
            "source",
        )


def test_downloaded_asset_rejects_digest_not_frozen_by_producer(tmp_path: Path) -> None:
    asset = tmp_path / "atomic_pyffish-1.0.1.tar.gz"
    asset.write_bytes(b"producer bytes")
    write_provenance(
        asset,
        VERSION,
        COMMIT,
        EPOCH,
        "python",
        "source",
        "source",
        "setuptools 80.9.0",
        ["python", "setup.py", "sdist"],
    )

    with pytest.raises(RuntimeError, match="provenance SHA-256 mismatch"):
        verify_release_asset(
            asset,
            digest(b"consumer-selected bytes"),
            VERSION,
            COMMIT,
            EPOCH,
            "python",
            "source",
            "source",
        )


def write_release(root: Path, payloads: dict[str, bytes]) -> None:
    root.mkdir()
    hashes = []
    for name, payload in sorted(payloads.items()):
        (root / name).write_bytes(payload)
        hashes.append("%s  %s\n" % (digest(payload), name))
    (root / "SHA256SUMS").write_text("".join(hashes), encoding="ascii")


def test_github_download_requires_exact_names_bytes_and_checksums(tmp_path: Path) -> None:
    payloads = {
        "Atomic-Stockfish-1.0.1-source.tar.xz": b"source",
        "atomic-stockfish-release-manifest.json": json.dumps(
            {"version": VERSION}, sort_keys=True
        ).encode("ascii"),
    }
    local = tmp_path / "local"
    downloaded = tmp_path / "downloaded"
    write_release(local, payloads)
    write_release(downloaded, payloads)

    assert verify_release_download(local, downloaded) == 3

    (downloaded / "unexpected.bin").write_bytes(b"unexpected")
    with pytest.raises(ReleaseDownloadError, match="file list differs"):
        verify_release_download(local, downloaded)


def test_github_download_rejects_checksum_file_that_omits_an_asset(tmp_path: Path) -> None:
    payloads = {"one.bin": b"one", "two.bin": b"two"}
    local = tmp_path / "local"
    downloaded = tmp_path / "downloaded"
    write_release(local, payloads)
    write_release(downloaded, payloads)
    incomplete = "%s  one.bin\n" % digest(b"one")
    (local / "SHA256SUMS").write_text(incomplete, encoding="ascii")
    (downloaded / "SHA256SUMS").write_text(incomplete, encoding="ascii")

    with pytest.raises(ReleaseDownloadError, match="file list differs"):
        verify_release_download(local, downloaded)
