import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from scripts.atomic_verify_uci_wasm_archive import (
    UciWasmArchiveError,
    verify_uci_wasm_archive,
)


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "docs" / "atomic" / "node-uci-wasm-release.md"
VERSION = "1.0.0"
EPOCH = 1_700_000_000
ARCHIVE_ROOT = "Atomic-Stockfish-1.0.0-node-uci-nnue-wasm"


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def archive_payloads() -> dict[str, bytes]:
    runtime = {
        "atomic-stockfish-nnue-node.mjs": b"// launcher\n",
        "atomic-stockfish-nnue.js": b"// runtime glue\n",
        "atomic-stockfish-nnue.wasm": b"wasm fixture\n",
        "atomic-stockfish-nnue.worker.js": b"// pthread worker\n",
    }
    manifest = {
        "schemaVersion": 2,
        "target": "node-uci-nnue",
        "sourceDateEpoch": EPOCH,
        "initialMemoryBytes": 536870912,
        "memoryGrowth": False,
        "pthreadPoolSize": 4,
        "supportedEntrypoint": "atomic-stockfish-nnue-node.mjs",
        "generatedRuntimeGlue": "atomic-stockfish-nnue.js",
        "directRuntimeGlueSupported": False,
        "supportedNetworkBackends": ["Legacy Atomic V1", "AtomicNNUEV2"],
        "networkFileVersions": ["0x7AF32F20", "0xA70C0002"],
        "externalNetwork": True,
        "artifacts": [
            {"name": name, "bytes": len(payload), "sha256": digest(payload)}
            for name, payload in sorted(runtime.items())
        ],
    }
    return {
        **runtime,
        "AUTHORS": b"authors\n",
        "CITATION.cff": b"cff-version: 1.2.0\n",
        "Copying.txt": b"GPL\n",
        "README.md": README.read_bytes(),
        "manifest.json": (json.dumps(manifest, sort_keys=True) + "\n").encode(),
    }


def write_archive(
    path: Path,
    payloads: dict[str, bytes],
    *,
    extra_members: list[tarfile.TarInfo] | None = None,
) -> None:
    with tarfile.open(path, "w:xz") as archive:
        root = tarfile.TarInfo(ARCHIVE_ROOT)
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        archive.addfile(root)
        for name, payload in sorted(payloads.items()):
            member = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{name}")
            member.size = len(payload)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(payload))
        for member in extra_members or []:
            archive.addfile(member)


def test_authenticates_complete_release_archive(tmp_path: Path) -> None:
    archive = tmp_path / "uci-wasm.tar.xz"
    payloads = archive_payloads()
    write_archive(archive, payloads)

    assert verify_uci_wasm_archive(archive, VERSION, EPOCH, README) == len(payloads)


def test_rejects_development_readme_even_when_manifest_is_valid(tmp_path: Path) -> None:
    archive = tmp_path / "uci-wasm.tar.xz"
    payloads = archive_payloads()
    payloads["README.md"] = b"node ../tests/wasm-engine/run-engine-tests.mjs\n"
    write_archive(archive, payloads)

    with pytest.raises(UciWasmArchiveError, match="differs from the release README"):
        verify_uci_wasm_archive(archive, VERSION, EPOCH, README)


def test_rejects_runtime_bytes_that_do_not_match_manifest(tmp_path: Path) -> None:
    archive = tmp_path / "uci-wasm.tar.xz"
    payloads = archive_payloads()
    payloads["atomic-stockfish-nnue.wasm"] += b"tampered"
    write_archive(archive, payloads)

    with pytest.raises(UciWasmArchiveError, match="runtime bytes differ"):
        verify_uci_wasm_archive(archive, VERSION, EPOCH, README)


def test_rejects_unmanifested_archive_file(tmp_path: Path) -> None:
    archive = tmp_path / "uci-wasm.tar.xz"
    payloads = archive_payloads()
    payloads["development-test.mjs"] = b"not a release runtime\n"
    write_archive(archive, payloads)

    with pytest.raises(UciWasmArchiveError, match="file set differs"):
        verify_uci_wasm_archive(archive, VERSION, EPOCH, README)


def test_rejects_symlink_and_traversal_members(tmp_path: Path) -> None:
    symlink_archive = tmp_path / "symlink.tar.xz"
    symlink = tarfile.TarInfo(f"{ARCHIVE_ROOT}/runtime-link")
    symlink.type = tarfile.SYMTYPE
    symlink.linkname = "atomic-stockfish-nnue.js"
    write_archive(symlink_archive, archive_payloads(), extra_members=[symlink])
    with pytest.raises(UciWasmArchiveError, match="regular files"):
        verify_uci_wasm_archive(symlink_archive, VERSION, EPOCH, README)

    traversal_archive = tmp_path / "traversal.tar.xz"
    traversal = tarfile.TarInfo(f"{ARCHIVE_ROOT}/../escape")
    traversal.size = 0
    write_archive(traversal_archive, archive_payloads(), extra_members=[traversal])
    with pytest.raises(UciWasmArchiveError, match="unsafe archive member path"):
        verify_uci_wasm_archive(traversal_archive, VERSION, EPOCH, README)


def test_rejects_manifest_epoch_or_runtime_contract_drift(tmp_path: Path) -> None:
    for field, replacement in (
        ("sourceDateEpoch", EPOCH + 1),
        ("initialMemoryBytes", 268435456),
        ("memoryGrowth", True),
        ("pthreadPoolSize", 2),
        ("supportedNetworkBackends", ["Legacy Atomic V1"]),
        ("networkFileVersions", ["0x7AF32F20"]),
    ):
        payloads = archive_payloads()
        manifest = json.loads(payloads["manifest.json"])
        manifest[field] = replacement
        payloads["manifest.json"] = (
            json.dumps(manifest, sort_keys=True) + "\n"
        ).encode()
        archive = tmp_path / f"contract-{field}.tar.xz"
        write_archive(archive, payloads)
        with pytest.raises(
            UciWasmArchiveError, match="invalid manifest contract field"
        ):
            verify_uci_wasm_archive(archive, VERSION, EPOCH, README)
