import hashlib
import json
from pathlib import Path

import pytest

from scripts.atomic_release_manifest import (
    CHECKSUM_NAME,
    MANIFEST_NAME,
    ReleaseContractError,
    assemble,
    expected_inventory_policy,
)
from scripts.atomic_release_provenance import write_provenance


VERSION = "1.0.3"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
EPOCH = 1_700_000_000


def write_asset(root: Path, relative: str, content: bytes, **overrides: object) -> Path:
    asset = root / relative
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(content)
    provenance = {
        "schemaVersion": 2,
        "asset": asset.name,
        "version": VERSION,
        "commit": COMMIT,
        "sourceDateEpoch": EPOCH,
        "kind": "native",
        "platform": "linux",
        "architecture": "x86-64",
        "toolchain": "gcc fixture",
        "buildCommand": ["make", "ARCH=x86-64", "build"],
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    provenance.update(overrides)
    asset.with_name(asset.name + ".provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )
    return asset


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_assembles_sorted_assets_and_writes_manifest_then_checksums(tmp_path: Path) -> None:
    source = tmp_path / "input"
    first = write_asset(source, "job-b/zeta.zip", b"zeta", platform="windows")
    second = write_asset(source, "job-a/alpha.tar.xz", b"alpha")
    output = tmp_path / "release"

    manifest = assemble(source, output, VERSION, COMMIT, EPOCH)

    assert [item["name"] for item in manifest["artifacts"]] == [
        "alpha.tar.xz",
        "zeta.zip",
    ]
    assert (output / first.name).read_bytes() == b"zeta"
    assert (output / second.name).read_bytes() == b"alpha"
    assert not list(output.glob("*.provenance.json"))
    on_disk = json.loads((output / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert on_disk == manifest
    checksum_lines = (output / CHECKSUM_NAME).read_text(encoding="ascii").splitlines()
    expected = {
        "alpha.tar.xz": digest(output / "alpha.tar.xz"),
        "zeta.zip": digest(output / "zeta.zip"),
        MANIFEST_NAME: digest(output / MANIFEST_NAME),
    }
    assert checksum_lines == [
        f"{expected[name]}  {name}" for name in sorted(expected)
    ]


def test_refuses_existing_output_without_touching_it(tmp_path: Path) -> None:
    source = tmp_path / "input"
    write_asset(source, "asset.zip", b"candidate")
    output = tmp_path / "release"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ReleaseContractError, match="already exists"):
        assemble(source, output, VERSION, COMMIT, EPOCH)

    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    "overrides,marker",
    [
        ({"asset": "other.zip"}, "asset mismatch"),
        ({"version": "9.9.9"}, "version mismatch"),
        ({"commit": "f" * 40}, "commit mismatch"),
        ({"sourceDateEpoch": EPOCH + 1}, "sourceDateEpoch mismatch"),
        ({"kind": "mystery"}, "invalid release asset kind"),
        ({"buildCommand": []}, "invalid buildCommand"),
        ({"sha256": "not-a-digest"}, "invalid provenance SHA-256"),
        ({"sha256": "f" * 64}, "provenance SHA-256 mismatch"),
    ],
)
def test_refuses_mismatched_or_incomplete_provenance(
    tmp_path: Path, overrides: dict, marker: str
) -> None:
    source = tmp_path / "input"
    write_asset(source, "asset.zip", b"candidate", **overrides)
    output = tmp_path / "release"
    with pytest.raises(ReleaseContractError, match=marker):
        assemble(source, output, VERSION, COMMIT, EPOCH)
    assert not output.exists()


def test_refuses_casefold_duplicate_asset_names(tmp_path: Path) -> None:
    source = tmp_path / "input"
    write_asset(source, "one/Atomic.zip", b"one", asset="Atomic.zip")
    write_asset(source, "two/atomic.zip", b"two", asset="atomic.zip")
    with pytest.raises(ReleaseContractError, match="duplicate"):
        assemble(source, tmp_path / "release", VERSION, COMMIT, EPOCH)


def test_refuses_orphaned_and_duplicate_key_provenance(tmp_path: Path) -> None:
    source = tmp_path / "input"
    asset = write_asset(source, "asset.zip", b"candidate")
    descriptor = asset.with_name(asset.name + ".provenance.json")
    descriptor.write_text(
        '{"schemaVersion":2,"schemaVersion":2}', encoding="utf-8"
    )
    with pytest.raises(ReleaseContractError, match="duplicate JSON key"):
        assemble(source, tmp_path / "release-one", VERSION, COMMIT, EPOCH)

    descriptor.unlink()
    orphan = source / "ghost.zip.provenance.json"
    orphan.write_text("{}", encoding="utf-8")
    with pytest.raises(ReleaseContractError, match="missing regular provenance"):
        assemble(source, tmp_path / "release-two", VERSION, COMMIT, EPOCH)


def test_refuses_reserved_or_unsafe_names(tmp_path: Path) -> None:
    source = tmp_path / "input"
    write_asset(source, MANIFEST_NAME, b"forged")
    with pytest.raises(ReleaseContractError, match="unsafe"):
        assemble(source, tmp_path / "release", VERSION, COMMIT, EPOCH)


def test_refuses_invalid_release_identity_before_creating_output(tmp_path: Path) -> None:
    source = tmp_path / "input"
    write_asset(source, "asset.zip", b"candidate")
    output = tmp_path / "release"
    with pytest.raises(ReleaseContractError, match="version"):
        assemble(source, output, "v1", COMMIT, EPOCH)
    assert not output.exists()
    with pytest.raises(ReleaseContractError, match="commit"):
        assemble(source, output, VERSION, "abc", EPOCH)
    assert not output.exists()


def test_provenance_writer_is_exclusive_and_round_trips(tmp_path: Path) -> None:
    asset = tmp_path / "Atomic-Stockfish-linux-x86-64.tar.xz"
    asset.write_bytes(b"release")
    descriptor = write_provenance(
        asset,
        VERSION,
        COMMIT.upper(),
        EPOCH,
        "native",
        "linux",
        "x86-64",
        "gcc fixture",
        ["make", "-C", "src", "ARCH=x86-64", "build"],
    )
    value = json.loads(descriptor.read_text(encoding="utf-8"))
    assert value["commit"] == COMMIT
    assert value["asset"] == asset.name
    assert value["schemaVersion"] == 2
    assert value["sha256"] == digest(asset)
    assert value["buildCommand"][-1] == "build"
    with pytest.raises(FileExistsError):
        write_provenance(
            asset,
            VERSION,
            COMMIT,
            EPOCH,
            "native",
            "linux",
            "x86-64",
            "gcc fixture",
            ["make", "build"],
        )


def test_provenance_writer_rejects_symlink_asset(tmp_path: Path) -> None:
    target = tmp_path / "target.tar.xz"
    target.write_bytes(b"release")
    link = tmp_path / "Atomic-Stockfish-linux-x86-64.tar.xz"
    try:
        link.symlink_to(target.name)
    except OSError as error:
        pytest.skip("platform cannot create test symlink: %s" % error)

    with pytest.raises(ValueError, match="regular file"):
        write_provenance(
            link,
            VERSION,
            COMMIT,
            EPOCH,
            "native",
            "linux",
            "x86-64",
            "gcc fixture",
            ["make", "build"],
        )
    assert not link.with_name(link.name + ".provenance.json").exists()


def write_inventory(path: Path, patterns: list) -> None:
    value = {
        "schemaVersion": 2,
        "project": "Atomic-Stockfish",
        "version": VERSION,
        "releasePolicy": expected_inventory_policy(VERSION),
        "assets": patterns,
    }
    path.write_text(json.dumps(value), encoding="utf-8")


def test_inventory_authenticates_exactly_one_rule_per_asset(tmp_path: Path) -> None:
    source = tmp_path / "input"
    write_asset(source, "linux.tar.xz", b"linux")
    write_asset(
        source,
        "windows.zip",
        b"windows",
        platform="windows",
        architecture="x86-64-bmi2",
    )
    inventory = tmp_path / "inventory.json"
    write_inventory(
        inventory,
        [
            {
                "namePattern": "linux.tar.xz",
                "kind": "native",
                "platform": "linux",
                "architecture": "x86-64",
            },
            {
                "namePattern": "windows.zip",
                "kind": "native",
                "platform": "windows",
                "architecture": "x86-64-bmi2",
            },
        ],
    )
    manifest = assemble(source, tmp_path / "release", VERSION, COMMIT, EPOCH, inventory)
    assert manifest["inventorySha256"] == digest(inventory)


def test_inventory_rejects_missing_extra_and_overlapping_assets(tmp_path: Path) -> None:
    source = tmp_path / "input"
    write_asset(source, "one.zip", b"one")
    write_asset(source, "two.zip", b"two")
    inventory = tmp_path / "inventory.json"
    base_rule = {
        "namePattern": "one.zip",
        "kind": "native",
        "platform": "linux",
        "architecture": "x86-64",
    }
    write_inventory(inventory, [base_rule])
    with pytest.raises(ReleaseContractError, match="every asset"):
        assemble(source, tmp_path / "missing", VERSION, COMMIT, EPOCH, inventory)

    write_inventory(inventory, [base_rule, base_rule])
    with pytest.raises(ReleaseContractError, match="overlap"):
        assemble(source, tmp_path / "overlap", VERSION, COMMIT, EPOCH, inventory)
