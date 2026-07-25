from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.atomic_mining import common
from tools.atomic_mining import extract_match_positions as extractor


ROOT_A = "8/8/8/8/8/8/P7/K6k w - - 0 1"
ROOT_B = "8/8/8/8/8/7p/8/K6k b - - 0 1"
MOVES_A = ("a2a3", "h1h2", "a3a4", "h2h3", "a4a5", "h3h4", "a5a6")
MOVES_B = ("h3h2", "a1a2", "h2h1q", "a2a3")
SOURCE_TEXT = f"""\
unrelated header
Game (atomic):
fen {ROOT_A}
{" ".join(MOVES_A)}
intermediate stats that must be ignored
Game (atomic):
fen {ROOT_B}
{" ".join(MOVES_B)}
trailing stats
"""


@dataclass(frozen=True)
class FakeInspection:
    fen: str
    key: str | None
    checkers: tuple[str, ...] = ()
    raw_lines: tuple[str, ...] = ()


class FakeEngine:
    def __init__(
        self,
        *,
        games: dict[str, tuple[str, ...]] | None = None,
        fail_move: str | None = None,
        illegal_move: str | None = None,
        silent_move: str | None = None,
        mismatch_move: str | None = None,
        checker_mismatch_move: str | None = None,
    ) -> None:
        games = games or {ROOT_A: MOVES_A, ROOT_B: MOVES_B}
        self.inspect_calls: list[
            tuple[str, tuple[str, ...], float | None]
        ] = []
        self.legal_calls: list[
            tuple[str, tuple[str, ...], float | None]
        ] = []
        self.fail_move = fail_move
        self.illegal_move = illegal_move
        self.silent_move = silent_move
        self.mismatch_move = mismatch_move
        self.checker_mismatch_move = checker_mismatch_move
        self.transitions: dict[tuple[str, str], str] = {}
        self.legal_by_fen: dict[str, tuple[str, ...]] = {}
        self.ply_by_fen: dict[str, int] = {}
        for root_fen, game_moves in games.items():
            fen = root_fen
            self.ply_by_fen[fen] = 0
            for ply, move in enumerate(game_moves):
                self.legal_by_fen[fen] = (move,)
                next_fen = self._advance_fen(fen)
                self.transitions[(fen, move)] = next_fen
                self.ply_by_fen[next_fen] = ply + 1
                fen = next_fen
            self.legal_by_fen.setdefault(fen, ())

    @staticmethod
    def _advance_fen(fen: str) -> str:
        fields = fen.split()
        before_side = fields[1]
        fields[1] = "b" if before_side == "w" else "w"
        fields[4] = str(int(fields[4]) + 1)
        if before_side == "b":
            fields[5] = str(int(fields[5]) + 1)
        return " ".join(fields)

    def _inspection(self, fen: str) -> FakeInspection:
        ply = self.ply_by_fen.get(fen, 0)
        key = hashlib.sha256(fen.encode("utf-8")).hexdigest()[:16]
        return FakeInspection(
            fen=fen,
            key=key,
            checkers=("a1",) if ply % 2 else (),
        )

    def inspect_fen(
        self,
        root_fen: str,
        moves: tuple[str, ...] = (),
        *,
        timeout: float | None = None,
    ) -> FakeInspection:
        requested = tuple(moves)
        self.inspect_calls.append((root_fen, requested, timeout))
        if self.fail_move is not None and self.fail_move in requested:
            raise RuntimeError("synthetic illegal trajectory")
        fen = root_fen
        for move in requested:
            next_fen = self.transitions.get((fen, move))
            if next_fen is None:
                # Simulate the dangerous UCI behaviour under test: an invalid
                # suffix is silently ignored rather than producing an error.
                break
            if self.silent_move == move and len(requested) == 1:
                return self._inspection(fen)
            if self.mismatch_move == move and len(requested) == 1:
                fields = next_fen.split()
                fields[1] = fen.split()[1]
                return self._inspection(" ".join(fields))
            fen = next_fen
        inspection = self._inspection(fen)
        if (
            self.checker_mismatch_move is not None
            and requested
            and requested[-1] == self.checker_mismatch_move
            and len(requested) > 1
        ):
            return FakeInspection(
                fen=inspection.fen,
                key=inspection.key,
                checkers=("h8",),
            )
        return inspection

    def legal_moves(
        self,
        root_fen: str,
        moves: tuple[str, ...] = (),
        *,
        timeout: float | None = None,
    ) -> tuple[str, ...]:
        requested = tuple(moves)
        self.legal_calls.append((root_fen, requested, timeout))
        inspection = (
            self.inspect_fen(root_fen, requested, timeout=timeout)
            if requested
            else self._inspection(root_fen)
        )
        legal = self.legal_by_fen.get(inspection.fen, ())
        if self.illegal_move is not None:
            legal = tuple(move for move in legal if move != self.illegal_move)
        return legal


def _write_source(tmp_path: Path, text: str = SOURCE_TEXT) -> Path:
    source = tmp_path / "match.log"
    source.write_text(text, encoding="utf-8", newline="\n")
    return source


def test_linear_parser_accepts_only_atomic_marker_fen_and_uci_line() -> None:
    games = extractor.parse_variantfishtest_log(SOURCE_TEXT)
    assert [(game.game_index, game.marker_line) for game in games] == [
        (1, 2),
        (2, 6),
    ]
    assert games[0].root_fen == ROOT_A
    assert games[0].moves[:3] == ("a2a3", "h1h2", "a3a4")
    assert games[1].moves[-1] == "a2a3"


@pytest.mark.parametrize(
    "text, message",
    (
        ("Game (atomic):\nnot-a-fen\n", "must start with 'fen '"),
        (f"Game (atomic):\nfen {ROOT_A}\n\n", "empty UCI move line"),
        (
            f"Game (atomic):\nfen {ROOT_A}\na2a9\n",
            "malformed UCI move token",
        ),
        ("header only\n", "no marker"),
    ),
)
def test_strict_parser_or_extractor_fails_closed(
    tmp_path: Path, text: str, message: str
) -> None:
    if message == "no marker":
        source = _write_source(tmp_path, text)
        with pytest.raises(extractor.MatchLogError, match="contains no"):
            extractor.extract_match_positions(
                source, tmp_path / "out.jsonl", FakeEngine()
            )
        assert not (tmp_path / "out.jsonl").exists()
    else:
        with pytest.raises(extractor.MatchLogError, match=message):
            extractor.parse_variantfishtest_log(text)


def test_extracts_selected_pre_move_positions_with_canonical_provenance(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "positions.jsonl"
    engine = FakeEngine()

    summary = extractor.extract_match_positions(
        source,
        output,
        engine,
        min_ply=1,
        stride=2,
        max_per_game=2,
        timeout=3.0,
    )

    rows = common.load_jsonl(output)
    assert summary.games_seen == 2
    assert summary.games_emitted == 2
    assert summary.games_quarantined == 0
    assert summary.positions_emitted == 4
    assert len(engine.legal_calls) == len(MOVES_A) + len(MOVES_B)
    assert all(moves == () and timeout == 3.0 for _fen, moves, timeout in engine.legal_calls)
    observed_expected_moves = [
        engine.legal_by_fen[fen][0] for fen, _moves, _timeout in engine.legal_calls
    ]
    assert observed_expected_moves == list(MOVES_A + MOVES_B)

    first = rows[0]
    assert first["schema"] == "atomic-match-position-v1"
    assert first["variant"] == "atomic"
    assert first["scientific_role"] == "discovery-only"
    assert first["outcome"] is None
    assert first["source"]["sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert first["game"]["root_fen"] == ROOT_A
    assert first["game"]["game_index"] == 1
    assert first["position"]["root_ply"] == 1
    assert first["position"]["played_move"] == "h1h2"
    assert first["position"]["history_uci"] == ["a2a3"]
    assert first["position"]["fen"].endswith("1 1")
    assert isinstance(first["position"]["engine_key"], str)
    assert first["position"]["checkers"] == ["a1"]
    assert first["position"]["legal_move_count"] == 1
    assert first["position"]["terminal_state"] == "nonterminal"
    assert first["position"]["transposition_key_scope"] == "informational-only"

    expected_history_hash = common.sha256_bytes(
        common.canonical_json_bytes(
            {
                "namespace": "atomic-position-full-history-v1",
                "value": {"root_fen": ROOT_A, "moves": ["a2a3"]},
            }
        )
    )
    assert first["position"]["full_history_sha256"] == expected_history_hash
    assert rows[0]["position"]["transposition_key"] == rows[1]["position"][
        "transposition_key"
    ]

    raw_lines = output.read_text(encoding="utf-8").splitlines(keepends=True)
    assert all(line.endswith("\n") and "\r" not in line for line in raw_lines)
    assert all(
        json.dumps(
            json.loads(line),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        == line
        for line in raw_lines
    )


def test_output_is_no_overwrite_even_before_engine_use(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "positions.jsonl"
    output.write_text("sentinel", encoding="utf-8")
    engine = FakeEngine()

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        extractor.extract_match_positions(source, output, engine)

    assert output.read_text(encoding="utf-8") == "sentinel"
    assert engine.inspect_calls == []
    assert engine.legal_calls == []


def test_inspection_error_aborts_without_publishing_partial_output(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "positions.jsonl"
    engine = FakeEngine(fail_move="h3h2")

    with pytest.raises(extractor.MatchLogError, match="inspection failed"):
        extractor.extract_match_positions(
            source,
            output,
            engine,
            min_ply=1,
            max_per_game=1,
        )

    assert not output.exists()


def test_explicit_quarantine_drops_complete_bad_game_and_records_error(
    tmp_path: Path,
) -> None:
    malformed = (
        f"Game (atomic):\nfen {ROOT_A}\na2a3 h1h2\n"
        f"Game (atomic):\nfen {ROOT_B}\nh3h9\n"
    )
    source = _write_source(tmp_path, malformed)
    output = tmp_path / "positions.jsonl"
    quarantine = tmp_path / "quarantine.jsonl"

    summary = extractor.extract_match_positions(
        source,
        output,
        FakeEngine(),
        min_ply=0,
        max_per_game=1,
        quarantine_path=quarantine,
    )

    rows = common.load_jsonl(output)
    quarantined = common.load_jsonl(quarantine)
    assert summary.games_seen == 2
    assert summary.games_emitted == 1
    assert summary.games_quarantined == 1
    assert len(rows) == 1
    assert quarantined[0]["schema"] == "atomic-match-position-quarantine-v1"
    assert quarantined[0]["game_index"] == 2
    assert quarantined[0]["stage"] == "parse"
    assert "h3h9" in quarantined[0]["error"]


def test_quarantine_on_inspection_failure_keeps_no_rows_from_that_game(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "positions.jsonl"
    quarantine = tmp_path / "quarantine.jsonl"
    engine = FakeEngine(fail_move="a3a4")

    summary = extractor.extract_match_positions(
        source,
        output,
        engine,
        min_ply=1,
        stride=2,
        max_per_game=2,
        quarantine_path=quarantine,
    )

    rows = common.load_jsonl(output)
    quarantined = common.load_jsonl(quarantine)
    assert summary.games_emitted == 1
    assert summary.games_quarantined == 1
    assert all(row["game"]["game_index"] == 2 for row in rows)
    assert quarantined[0]["game_index"] == 1
    assert quarantined[0]["stage"] == "inspect"


def test_failed_output_publish_never_unlinks_swapped_quarantine_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "positions.jsonl"
    quarantine = tmp_path / "quarantine.jsonl"
    real_write = common.write_new_jsonl
    calls = 0

    def publish_then_race(path: Path, rows: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_write(path, rows)  # type: ignore[arg-type]
            return
        quarantine.unlink()
        quarantine.write_bytes(b"attacker-path-replacement")
        raise RuntimeError("synthetic output publication failure")

    monkeypatch.setattr(common, "write_new_jsonl", publish_then_race)

    with pytest.raises(RuntimeError, match="publication failure"):
        extractor.extract_match_positions(
            source,
            output,
            FakeEngine(),
            quarantine_path=quarantine,
        )

    assert quarantine.read_bytes() == b"attacker-path-replacement"
    assert not output.exists()


def test_illegal_intermediate_move_is_rejected_even_when_not_selected(
    tmp_path: Path,
) -> None:
    text = f"Game (atomic):\nfen {ROOT_A}\n{' '.join(MOVES_A)}\n"
    source = _write_source(tmp_path, text)
    output = tmp_path / "positions.jsonl"
    engine = FakeEngine(
        games={ROOT_A: MOVES_A},
        illegal_move="a3a4",
    )

    with pytest.raises(
        extractor.MatchLogError, match=r"a3a4.*not legal at ply 2"
    ):
        extractor.extract_match_positions(
            source,
            output,
            engine,
            min_ply=6,
            max_per_game=1,
        )

    assert not output.exists()
    assert len(engine.legal_calls) == 3


def test_silently_truncated_position_move_is_rejected(
    tmp_path: Path,
) -> None:
    text = f"Game (atomic):\nfen {ROOT_A}\n{' '.join(MOVES_A)}\n"
    source = _write_source(tmp_path, text)
    output = tmp_path / "positions.jsonl"
    engine = FakeEngine(
        games={ROOT_A: MOVES_A},
        silent_move="h1h2",
    )

    with pytest.raises(
        extractor.MatchLogError, match="did not change engine state"
    ):
        extractor.extract_match_positions(
            source,
            output,
            engine,
            min_ply=6,
            max_per_game=1,
        )
    assert not output.exists()


def test_state_mismatch_after_legal_move_is_rejected(
    tmp_path: Path,
) -> None:
    text = f"Game (atomic):\nfen {ROOT_A}\n{' '.join(MOVES_A)}\n"
    source = _write_source(tmp_path, text)
    output = tmp_path / "positions.jsonl"
    engine = FakeEngine(
        games={ROOT_A: MOVES_A},
        mismatch_move="h1h2",
    )

    with pytest.raises(
        extractor.MatchLogError, match="side-to-move did not toggle"
    ):
        extractor.extract_match_positions(
            source,
            output,
            engine,
            min_ply=6,
            max_per_game=1,
        )
    assert not output.exists()


def test_checker_mismatch_between_replay_paths_is_rejected(
    tmp_path: Path,
) -> None:
    text = f"Game (atomic):\nfen {ROOT_A}\n{' '.join(MOVES_A)}\n"
    source = _write_source(tmp_path, text)
    output = tmp_path / "positions.jsonl"
    engine = FakeEngine(
        games={ROOT_A: MOVES_A},
        checker_mismatch_move="h1h2",
    )

    with pytest.raises(
        extractor.MatchLogError, match="checker sets differ"
    ):
        extractor.extract_match_positions(
            source,
            output,
            engine,
            min_ply=6,
            max_per_game=1,
        )
    assert not output.exists()


def test_source_is_consumed_from_one_stable_common_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "positions.jsonl"
    real_reader = common.read_stable_file_bytes
    observed: list[tuple[Path, str]] = []

    def recording_reader(path: Path, *, label: str = "input") -> bytes:
        observed.append((path, label))
        return real_reader(path, label=label)

    def forbidden_path_read_bytes(_path: Path) -> bytes:
        raise AssertionError("extractor reopened source via Path.read_bytes")

    monkeypatch.setattr(common, "read_stable_file_bytes", recording_reader)
    monkeypatch.setattr(Path, "read_bytes", forbidden_path_read_bytes)

    summary = extractor.extract_match_positions(
        source,
        output,
        FakeEngine(),
        min_ply=100,
    )

    assert summary.games_seen == 2
    assert observed == [(source.resolve(), "source match log")]


def test_game_with_zero_selected_plies_is_still_fully_replayed(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "positions.jsonl"
    engine = FakeEngine()

    summary = extractor.extract_match_positions(
        source,
        output,
        engine,
        min_ply=100,
    )

    assert summary.games_seen == 2
    assert summary.games_emitted == 2
    assert summary.positions_emitted == 0
    assert common.load_jsonl(output) == []
    assert len(engine.legal_calls) == len(MOVES_A) + len(MOVES_B)
    incremental_applications = [
        call for call in engine.inspect_calls if len(call[1]) == 1
    ]
    assert len(incremental_applications) == len(MOVES_A) + len(MOVES_B)
    assert any(call[1] == (MOVES_A[-1],) for call in incremental_applications)
    assert any(call[1] == (MOVES_B[-1],) for call in incremental_applications)
