from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import build_e00_schedule as schedule
from tools.atomic_mining import common


def _fen(index: int) -> str:
    halfmove = index
    fullmove = index + 1
    return (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR "
        f"{'w' if index % 2 == 0 else 'b'} KQkq - {halfmove} {fullmove}"
    )


def _book(count: int, *, crlf: bool = False) -> bytes:
    newline = "\r\n" if crlf else "\n"
    return (newline.join(_fen(index) for index in range(count)) + newline).encode()


def _controls() -> list[schedule.TimeControl]:
    return [
        schedule.TimeControl("VSTC", 2_000, 20, 4),
        schedule.TimeControl("STC", 10_000, 100, 3),
        schedule.TimeControl("LTC", 30_000, 300, 2),
    ]


def _publish(
    directory: Path,
    *,
    book_payload: bytes | None = None,
    seed: str = "atomic-e00-test-seed",
) -> tuple[Path, Path, schedule.ScheduleSummary]:
    directory.mkdir(parents=True, exist_ok=True)
    book_path = directory / "book.epd"
    output_path = directory / "schedule.jsonl"
    receipt_path = directory / "schedule.receipt.json"
    book_path.write_bytes(book_payload or _book(16))
    summary = schedule.build_schedule_file(
        book_path,
        seed=seed,
        time_controls=_controls(),
        output_path=output_path,
        receipt_path=receipt_path,
    )
    return output_path, receipt_path, summary


def test_schedule_is_deterministic_canonical_paired_and_disjoint(
    tmp_path: Path,
) -> None:
    first_output, first_receipt, first_summary = _publish(tmp_path / "first")
    second_output, second_receipt, second_summary = _publish(
        tmp_path / "second"
    )

    assert first_output.read_bytes() == second_output.read_bytes()
    assert first_receipt.read_bytes() == second_receipt.read_bytes()
    assert first_summary == second_summary
    loaded = schedule.load_schedule(first_output, first_receipt)
    assert len(loaded.rows) == 18
    assert [row["pair_ordinal"] for row in loaded.rows] == [
        value for value in range(1, 10) for _ in range(2)
    ]
    assert [row["leg"] for row in loaded.rows] == [0, 1] * 9
    identities = [
        row["root"]["root_identity"] for row in loaded.rows[::2]
    ]
    assert len(identities) == len(set(identities)) == 9
    for first, second in zip(loaded.rows[::2], loaded.rows[1::2]):
        assert first["root"] == second["root"]
        assert first["white_role"] == schedule.CURRENT_ROLE
        assert first["black_role"] == schedule.TEACHER_ROLE
        assert second["white_role"] == schedule.TEACHER_ROLE
        assert second["black_role"] == schedule.CURRENT_ROLE


def test_schedule_is_relocatable_and_accepts_realistic_crlf_book(
    tmp_path: Path,
) -> None:
    payload = _book(16, crlf=True)
    left = _publish(tmp_path / "deep" / "left", book_payload=payload)
    right = _publish(tmp_path / "elsewhere", book_payload=payload)
    assert left[0].read_bytes() == right[0].read_bytes()
    assert left[1].read_bytes() == right[1].read_bytes()
    receipt = json.loads(left[1].read_text(encoding="utf-8"))
    assert "path" not in receipt["book"]
    assert "path" not in receipt["schedule"]


def test_duplicate_normalized_roots_are_rejected(tmp_path: Path) -> None:
    duplicate = (
        _fen(0) + "\n" + "  " + _fen(0).replace(" 0 1", " 00 01") + " \n"
    ).encode()
    book_path = tmp_path / "duplicate.epd"
    book_path.write_bytes(duplicate)
    with pytest.raises(
        schedule.E00ScheduleError, match="duplicate normalized root"
    ):
        schedule.build_schedule_file(
            book_path,
            seed="seed",
            time_controls=[schedule.TimeControl("VSTC", 2000, 20, 1)],
            output_path=tmp_path / "schedule.jsonl",
            receipt_path=tmp_path / "receipt.json",
        )


def test_insufficient_roots_and_duplicate_strata_fail_before_write(
    tmp_path: Path,
) -> None:
    book_path = tmp_path / "book.epd"
    book_path.write_bytes(_book(2))
    output = tmp_path / "schedule.jsonl"
    receipt = tmp_path / "receipt.json"
    with pytest.raises(schedule.E00ScheduleError, match="insufficient"):
        schedule.build_schedule_file(
            book_path,
            seed="seed",
            time_controls=[
                schedule.TimeControl("VSTC", 2000, 20, 2),
                schedule.TimeControl("STC", 10000, 100, 1),
            ],
            output_path=output,
            receipt_path=receipt,
        )
    assert not output.exists()
    assert not receipt.exists()

    with pytest.raises(
        schedule.E00ScheduleError, match="duplicate time-control name"
    ):
        schedule.build_schedule_file(
            book_path,
            seed="seed",
            time_controls=[
                schedule.TimeControl("same", 2000, 20, 1),
                schedule.TimeControl("same", 10000, 100, 1),
            ],
            output_path=output,
            receipt_path=receipt,
        )


def test_postflight_failure_leaves_schedule_uncommitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_path = tmp_path / "book.epd"
    book_path.write_bytes(_book(4))
    output = tmp_path / "schedule.jsonl"
    receipt = tmp_path / "schedule.receipt.json"

    def reject_postflight(
        schedule_path: Path, receipt_path: Path | None = None
    ) -> schedule.ScheduleSnapshot:
        assert schedule_path == output.absolute()
        assert receipt_path is None
        assert output.is_file()
        raise schedule.E00ScheduleError("injected schedule postflight failure")

    monkeypatch.setattr(schedule, "load_schedule", reject_postflight)
    with pytest.raises(
        schedule.E00ScheduleError,
        match="injected schedule postflight failure",
    ):
        schedule.build_schedule_file(
            book_path,
            seed="postflight-failure",
            time_controls=[
                schedule.TimeControl("VSTC", 2_000, 20, 1)
            ],
            output_path=output,
            receipt_path=receipt,
        )
    assert output.is_file()
    assert not receipt.exists()


def test_schedule_validator_rejects_semantic_tampering(tmp_path: Path) -> None:
    output, receipt, _summary = _publish(tmp_path / "source")
    rows = list(schedule.load_schedule(output, receipt).rows)
    tampered = copy.deepcopy(rows)
    tampered[0]["root"]["fen_sha256"] = "0" * 64
    tampered_path = tmp_path / "tampered.jsonl"
    common.write_new_jsonl(tampered_path, tampered)
    with pytest.raises(schedule.E00ScheduleError, match="does not match"):
        schedule.load_schedule(tampered_path)

    partial = copy.deepcopy(rows[:-1])
    partial_path = tmp_path / "partial.jsonl"
    common.write_new_jsonl(partial_path, partial)
    with pytest.raises(schedule.E00ScheduleError, match="exactly legs"):
        schedule.load_schedule(partial_path)

    reused = copy.deepcopy(rows)
    reused[2]["root"] = copy.deepcopy(reused[0]["root"])
    reused[3]["root"] = copy.deepcopy(reused[0]["root"])
    for row in reused[2:4]:
        row["pair_id"] = schedule._pair_id(
            seed=row["seed"],
            book_sha256=row["book_sha256"],
            pair_ordinal=row["pair_ordinal"],
            time_control=row["time_control"],
            root_identity=row["root"]["root_identity"],
        )
    reused_path = tmp_path / "reused.jsonl"
    common.write_new_jsonl(reused_path, reused)
    with pytest.raises(schedule.E00ScheduleError, match="reuses a root"):
        schedule.load_schedule(reused_path)


def test_receipt_authenticates_exact_schedule_and_book_hash(
    tmp_path: Path,
) -> None:
    output, receipt, summary = _publish(tmp_path / "published")
    loaded = schedule.load_schedule(output, receipt)
    receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_value["book"]["sha256"] == summary.book_sha256
    assert receipt_value["schedule"]["sha256"] == summary.schedule_sha256
    assert loaded.sha256 == common.sha256_file(output)
    assert loaded.receipt_sha256 == common.sha256_file(receipt)

    bad_receipt = copy.deepcopy(receipt_value)
    bad_receipt["schedule"]["sha256"] = "f" * 64
    bad_receipt_path = tmp_path / "bad-receipt.json"
    common.write_new_json(bad_receipt_path, bad_receipt)
    with pytest.raises(schedule.E00ScheduleError, match="hash does not match"):
        schedule.load_schedule(output, bad_receipt_path)


def test_create_new_semantics_preserve_existing_artifacts(tmp_path: Path) -> None:
    output, receipt, _summary = _publish(tmp_path / "published")
    schedule_bytes = output.read_bytes()
    receipt_bytes = receipt.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        schedule.build_schedule_file(
            tmp_path / "published" / "book.epd",
            seed="atomic-e00-test-seed",
            time_controls=_controls(),
            output_path=output,
            receipt_path=receipt,
        )
    assert output.read_bytes() == schedule_bytes
    assert receipt.read_bytes() == receipt_bytes

    orphan_receipt = tmp_path / "orphan-receipt.json"
    orphan_receipt.write_text("existing", encoding="utf-8")
    absent_schedule = tmp_path / "absent-schedule.jsonl"
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        schedule.build_schedule_file(
            tmp_path / "published" / "book.epd",
            seed="atomic-e00-test-seed",
            time_controls=_controls(),
            output_path=absent_schedule,
            receipt_path=orphan_receipt,
        )
    assert not absent_schedule.exists()


@pytest.mark.parametrize(
    "specification",
    (
        "VSTC:2000:20",
        "VSTC:0:20:1",
        "VSTC:2000:-1:1",
        "VSTC:2000:20:0",
        "bad name:2000:20:1",
        "VSTC:+2000:20:1",
    ),
)
def test_time_control_parser_is_strict(specification: str) -> None:
    with pytest.raises(schedule.E00ScheduleError):
        schedule.parse_time_control_spec(specification)


def test_book_and_fen_validation_is_fail_closed() -> None:
    with pytest.raises(schedule.E00ScheduleError, match="at least one root"):
        schedule.parse_book_snapshot(b"", seed="seed")
    with pytest.raises(schedule.E00ScheduleError, match="eight ranks"):
        schedule.parse_book_snapshot(
            b"8/8/8/8/8/8/8 w - - 0 1\n",
            seed="seed",
        )
    with pytest.raises(schedule.E00ScheduleError, match="stray CR"):
        schedule.parse_book_snapshot(
            (_fen(0) + "\rgarbage\n").encode(),
            seed="seed",
        )
