from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import common


def test_canonical_json_is_stable_utf8_and_single_lf() -> None:
    value = {"z": 2, "a": "peón", "nested": {"b": False}}
    payload = common.canonical_json_bytes(value)
    assert payload == (
        '{"a":"peón","nested":{"b":false},"z":2}\n'.encode("utf-8")
    )
    assert json.loads(payload) == value


def test_write_new_jsonl_refuses_overwrite_and_loads_canonical_rows(
    tmp_path: Path,
) -> None:
    target = tmp_path / "rows.jsonl"
    rows = [{"id": 1}, {"id": 2}]
    common.write_new_jsonl(target, rows)
    assert common.load_jsonl(target) == rows
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        common.write_new_jsonl(target, rows)


def test_jsonl_snapshot_binds_rows_hash_and_size_to_the_same_bytes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "snapshot.jsonl"
    rows = [{"id": 1}, {"id": 2, "name": "peón"}]
    payload = b"".join(common.canonical_json_bytes(row) for row in rows)
    target.write_bytes(payload)

    snapshot = common.load_jsonl_snapshot(target)

    assert snapshot.path == target.absolute()
    assert snapshot.rows == tuple(rows)
    assert snapshot.sha256 == common.sha256_bytes(payload)
    assert snapshot.size_bytes == len(payload)

    # Consumers retain the authenticated snapshot; later pathname changes do
    # not silently alter the rows or provenance they already accepted.
    target.write_bytes(common.canonical_json_bytes({"replacement": True}))
    assert snapshot.rows == tuple(rows)
    assert snapshot.sha256 == common.sha256_bytes(payload)
    assert snapshot.size_bytes == len(payload)


@pytest.mark.parametrize(
    "payload",
    (
        b'{"id":1}\r\n',
        b'{"id":1}',
        b'{ "id": 1 }\n',
        b"[]\n",
        b"\xef\xbb\xbf" + b'{"id":1}\n',
        b'{"id":"\xff"}\n',
        b'{"id":1}\n\n',
        b'{"id":NaN}\n',
    ),
)
def test_load_jsonl_rejects_noncanonical_or_nonobject_rows(
    tmp_path: Path, payload: bytes
) -> None:
    target = tmp_path / "rows.jsonl"
    target.write_bytes(payload)
    with pytest.raises(common.MiningArtifactError):
        common.load_jsonl(target)


def test_empty_jsonl_snapshot_is_canonical_and_authenticated(tmp_path: Path) -> None:
    target = tmp_path / "empty.jsonl"
    target.write_bytes(b"")

    snapshot = common.load_jsonl_snapshot(target)

    assert snapshot.rows == ()
    assert snapshot.sha256 == common.sha256_bytes(b"")
    assert snapshot.size_bytes == 0


def test_stable_file_reader_rejects_mutation_observed_during_single_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "source.log"
    target.write_bytes(b"immutable input\n")
    real_fstat = common.os.fstat
    calls = 0

    def changing_fstat(descriptor: int) -> object:
        nonlocal calls
        calls += 1
        observed = real_fstat(descriptor)
        if calls == 1:
            return observed
        return SimpleNamespace(
            st_mode=observed.st_mode,
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_size=observed.st_size,
            st_mtime_ns=observed.st_mtime_ns + 1,
            st_ctime_ns=observed.st_ctime_ns,
        )

    monkeypatch.setattr(common.os, "fstat", changing_fstat)

    with pytest.raises(common.MiningArtifactError, match="changed while"):
        common.read_stable_file_bytes(target, label="source match log")


def test_write_new_bytes_publishes_exact_payload_and_leaves_no_temp(
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.bin"
    payload = b"\x00atomic\nartifact\xff"

    common.write_new_bytes(target, payload)

    assert target.read_bytes() == payload
    assert list(tmp_path.glob(f".{target.name}.tmp-*")) == []


def test_write_new_bytes_never_overwrites_existing_destination(
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        common.write_new_bytes(target, b"replacement")

    assert target.read_bytes() == b"existing"
    assert list(tmp_path.glob(f".{target.name}.tmp-*")) == []


def test_write_new_bytes_rejects_corrupt_temp_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "artifact.bin"
    real_verify = common._verify_exact_payload

    def corrupt_then_verify(
        path: Path, expected: bytes, *, label: str
    ) -> object:
        if label == "temporary mining artifact":
            path.write_bytes(b"corrupt-before-publish")
        return real_verify(path, expected, label=label)

    monkeypatch.setattr(common, "_verify_exact_payload", corrupt_then_verify)

    with pytest.raises(common.MiningArtifactError, match="bytes differ"):
        common.write_new_bytes(target, b"expected")

    assert not target.exists()
    assert list(tmp_path.glob(f".{target.name}.tmp-*")) == []


def test_write_new_bytes_rejects_temp_changed_between_verify_and_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "artifact.bin"
    real_link = common.os.link

    def corrupting_link(source: object, destination: object) -> None:
        Path(source).write_bytes(b"changed-after-verification")
        real_link(source, destination)

    monkeypatch.setattr(common.os, "link", corrupting_link)

    with pytest.raises(common.MiningArtifactError, match="bytes differ"):
        common.write_new_bytes(target, b"expected")

    assert not target.exists()
    assert list(tmp_path.glob(f".{target.name}.tmp-*")) == []


def test_write_new_bytes_destination_race_preserves_race_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "artifact.bin"
    real_link = common.os.link

    def racing_link(source: object, destination: object) -> None:
        Path(destination).write_bytes(b"race-winner")
        real_link(source, destination)

    monkeypatch.setattr(common.os, "link", racing_link)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        common.write_new_bytes(target, b"our-payload")

    assert target.read_bytes() == b"race-winner"
    assert list(tmp_path.glob(f".{target.name}.tmp-*")) == []


def test_transposition_key_ignores_counters_but_full_fen_remains_available() -> None:
    a = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    b = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 42 19"
    assert common.transposition_key(a) == common.transposition_key(b)
    assert a != b


@pytest.mark.parametrize(
    "fen",
    (
        "8/8/8/8/8/8/8/8 x - - 0 1",
        "8/8/8/8/8/8/8/8 w X - 0 1",
        "8/8/8/8/8/8/8/8 w - e4 0 1",
        "8/8/8/8/8/8/8/8 w - - -1 1",
        "8/8/8/8/8/8/8/8 w - - 0 0",
        "8/8/8/8/8/8/8/8 w - - 0",
    ),
)
def test_fen_fields_rejects_malformed_contract(fen: str) -> None:
    with pytest.raises(common.MiningArtifactError):
        common.fen_fields(fen)
