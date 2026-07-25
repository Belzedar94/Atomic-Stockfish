"""Extract replay-authenticated pre-move positions from variantfishtest logs.

The source format is deliberately narrow::

    Game (atomic):
    fen <six-field Atomic FEN>
    <space-separated UCI moves>

Everything else in a variantfishtest log is ignored.  A malformed Atomic game
is fatal by default.  Supplying a quarantine artifact changes that policy
explicitly: the complete malformed game is omitted and a canonical diagnostic
row is emitted.  Positions are never deduplicated here; in particular, the
transposition key is informational and cannot replace the trajectory identity.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterator, Protocol, Sequence

from tools.atomic_mining import common


SCHEMA = "atomic-match-position-v1"
QUARANTINE_SCHEMA = "atomic-match-position-quarantine-v1"
ATOMIC_GAME_MARKER = "Game (atomic):"
UCI_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class MatchLogError(common.MiningArtifactError):
    """A variantfishtest Atomic game cannot be parsed or authenticated."""


class InspectionLike(Protocol):
    fen: str
    key: str | None
    checkers: Sequence[str]


class EngineLike(Protocol):
    def inspect_fen(
        self,
        root_fen: str,
        moves: Sequence[str] = (),
        *,
        timeout: float | None = None,
    ) -> InspectionLike: ...

    def legal_moves(
        self,
        root_fen: str,
        moves: Sequence[str] = (),
        *,
        timeout: float | None = None,
    ) -> Sequence[str]: ...


@dataclass(frozen=True)
class ParsedGame:
    game_index: int
    marker_line: int
    root_fen: str
    moves: tuple[str, ...]


@dataclass(frozen=True)
class ParseIssue:
    game_index: int
    marker_line: int
    error: str


@dataclass(frozen=True)
class ExtractionSummary:
    source_sha256: str
    games_seen: int
    games_emitted: int
    games_quarantined: int
    positions_emitted: int


def _digest(namespace: str, value: object) -> str:
    payload = {"namespace": namespace, "value": value}
    return common.sha256_bytes(common.canonical_json_bytes(payload))


def _validate_root_fen(fen: str) -> str:
    normalized = " ".join(fen.split())
    common.fen_fields(normalized)
    return normalized


def _parse_game_block(
    lines: Sequence[str],
    *,
    game_index: int,
    marker_line: int,
) -> ParsedGame:
    if not lines:
        raise MatchLogError(
            f"game {game_index} at line {marker_line}: "
            "expected FEN and UCI move lines"
        )

    fen_line = lines[0].strip()
    if not fen_line.startswith("fen "):
        raise MatchLogError(
            f"game {game_index} at line {marker_line}: "
            "first line after marker must start with 'fen '"
        )
    root_fen = _validate_root_fen(fen_line[4:])

    if len(lines) < 2:
        raise MatchLogError(
            f"game {game_index} at line {marker_line}: expected UCI move line"
        )
    move_line = lines[1].strip()
    if not move_line:
        raise MatchLogError(
            f"game {game_index} at line {marker_line}: empty UCI move line"
        )
    moves = tuple(move_line.split())
    invalid = [move for move in moves if UCI_MOVE.fullmatch(move) is None]
    if invalid:
        raise MatchLogError(
            f"game {game_index} at line {marker_line}: "
            f"malformed UCI move token {invalid[0]!r}"
        )
    return ParsedGame(
        game_index=game_index,
        marker_line=marker_line,
        root_fen=root_fen,
        moves=moves,
    )


def scan_variantfishtest_log(
    text: str,
) -> Iterator[ParsedGame | ParseIssue]:
    """Scan Atomic game records in source order.

    A marker starts a two-line record.  Empty lines are not skipped: allowing
    them would make a truncated log look valid by consuming unrelated status
    output.  Recovery, when requested by the caller, happens only at the next
    explicit ``Game (atomic):`` marker.
    """

    lines = text.splitlines()
    markers = [
        index for index, line in enumerate(lines) if line.strip() == ATOMIC_GAME_MARKER
    ]
    for zero_based_game, marker_index in enumerate(markers):
        game_index = zero_based_game + 1
        marker_line = marker_index + 1
        next_marker = (
            markers[zero_based_game + 1]
            if zero_based_game + 1 < len(markers)
            else len(lines)
        )
        block = lines[marker_index + 1 : next_marker]
        try:
            yield _parse_game_block(
                block,
                game_index=game_index,
                marker_line=marker_line,
            )
        except (MatchLogError, common.MiningArtifactError) as error:
            yield ParseIssue(
                game_index=game_index,
                marker_line=marker_line,
                error=str(error),
            )


def parse_variantfishtest_log(text: str) -> list[ParsedGame]:
    """Parse a log strictly, raising on the first malformed Atomic game."""

    games: list[ParsedGame] = []
    for item in scan_variantfishtest_log(text):
        if isinstance(item, ParseIssue):
            raise MatchLogError(item.error)
        games.append(item)
    return games


def _source_provenance(source: Path, payload: bytes) -> dict[str, object]:
    return {
        "path": str(source.resolve()),
        "sha256": common.sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _game_provenance(
    game: ParsedGame,
    *,
    source_sha256: str,
) -> dict[str, object]:
    full_history = {"root_fen": game.root_fen, "moves": list(game.moves)}
    return {
        "game_index": game.game_index,
        "marker_line": game.marker_line,
        "root_fen": game.root_fen,
        "root_fen_sha256": _digest("atomic-root-fen-v1", game.root_fen),
        "source_game_id": _digest(
            "atomic-source-game-v1",
            {
                "source_sha256": source_sha256,
                "game_index": game.game_index,
                "marker_line": game.marker_line,
            },
        ),
        "full_game_history_sha256": _digest(
            "atomic-full-game-history-v1", full_history
        ),
        "move_count": len(game.moves),
    }


def _position_row(
    *,
    source: dict[str, object],
    game: ParsedGame,
    game_provenance: dict[str, object],
    root_ply: int,
    inspection: InspectionLike,
    legal_move_count: int,
    terminal_state: str,
) -> dict[str, object]:
    fen = " ".join(inspection.fen.split())
    common.fen_fields(fen)
    history_moves = list(game.moves[:root_ply])
    history = {"root_fen": game.root_fen, "moves": history_moves}
    engine_key = inspection.key
    if engine_key is not None and not isinstance(engine_key, str):
        raise MatchLogError("UCI inspection key must be a string or null")
    checkers = list(inspection.checkers)
    if any(not isinstance(square, str) for square in checkers):
        raise MatchLogError("UCI inspection checkers must be strings")

    return {
        "schema": SCHEMA,
        "variant": "atomic",
        "scientific_role": "discovery-only",
        "outcome": None,
        "source": source,
        "game": game_provenance,
        "position": {
            "root_ply": root_ply,
            "fen": fen,
            "played_move": game.moves[root_ply],
            "history_uci": history_moves,
            "full_history_sha256": _digest(
                "atomic-position-full-history-v1", history
            ),
            "transposition_key": common.transposition_key(fen),
            "transposition_key_scope": "informational-only",
            "engine_key": engine_key,
            "checkers": checkers,
            "legal_move_count": legal_move_count,
            "terminal_state": terminal_state,
        },
    }


def _quarantine_row(
    *,
    source: dict[str, object],
    game_index: int,
    marker_line: int,
    stage: str,
    error: BaseException | str,
) -> dict[str, object]:
    return {
        "schema": QUARANTINE_SCHEMA,
        "scientific_role": "discovery-only",
        "source": source,
        "game_index": game_index,
        "marker_line": marker_line,
        "stage": stage,
        "error_type": (
            type(error).__name__ if isinstance(error, BaseException) else "ParseError"
        ),
        "error": str(error),
    }


def _selected_plies(
    game: ParsedGame,
    *,
    min_ply: int,
    stride: int,
    max_per_game: int | None,
) -> Iterator[int]:
    selected = 0
    for root_ply in range(min_ply, len(game.moves), stride):
        if max_per_game is not None and selected >= max_per_game:
            break
        yield root_ply
        selected += 1


def _normalized_inspection_fen(inspection: InspectionLike) -> str:
    fen = " ".join(inspection.fen.split())
    common.fen_fields(fen)
    return fen


def _terminal_state(
    *, legal_move_count: int, checkers: Sequence[str]
) -> str:
    if legal_move_count > 0:
        return "nonterminal"
    if checkers:
        return "no-legal-moves-in-check"
    return "no-legal-moves"


def _validated_legal_moves(
    engine: EngineLike,
    fen: str,
    *,
    timeout: float | None,
) -> tuple[str, ...]:
    moves = tuple(engine.legal_moves(fen, timeout=timeout))
    if len(set(moves)) != len(moves):
        raise MatchLogError("engine returned duplicate legal moves")
    for move in moves:
        if not isinstance(move, str) or UCI_MOVE.fullmatch(move) is None:
            raise MatchLogError(
                f"engine returned malformed legal move token {move!r}"
            )
    return moves


def _validate_transition(
    before_fen: str,
    after_fen: str,
    *,
    move: str,
    root_ply: int,
) -> None:
    before = common.fen_fields(before_fen)
    after = common.fen_fields(after_fen)
    if before_fen == after_fen:
        raise MatchLogError(
            f"move {move!r} at ply {root_ply} did not change engine state; "
            "position command may have been silently truncated"
        )
    expected_side = "b" if before[1] == "w" else "w"
    if after[1] != expected_side:
        raise MatchLogError(
            f"state mismatch after move {move!r} at ply {root_ply}: "
            "side-to-move did not toggle"
        )
    expected_fullmove = before[5] + (1 if before[1] == "b" else 0)
    if after[5] != expected_fullmove:
        raise MatchLogError(
            f"state mismatch after move {move!r} at ply {root_ply}: "
            "fullmove counter is inconsistent"
        )


def _same_state(
    left: InspectionLike,
    right: InspectionLike,
    *,
    move: str,
    root_ply: int,
) -> None:
    left_fen = _normalized_inspection_fen(left)
    right_fen = _normalized_inspection_fen(right)
    if left_fen != right_fen:
        raise MatchLogError(
            f"state mismatch after move {move!r} at ply {root_ply}: "
            "incremental and root-history replay FENs differ"
        )
    if (
        left.key is not None
        and right.key is not None
        and left.key != right.key
    ):
        raise MatchLogError(
            f"state mismatch after move {move!r} at ply {root_ply}: "
            "incremental and root-history engine keys differ"
        )
    if tuple(left.checkers) != tuple(right.checkers):
        raise MatchLogError(
            f"state mismatch after move {move!r} at ply {root_ply}: "
            "incremental and root-history checker sets differ"
        )


def _replay_game(
    game: ParsedGame,
    engine: EngineLike,
    *,
    selected_plies: set[int],
    source: dict[str, object],
    game_provenance: dict[str, object],
    timeout: float | None,
) -> list[dict[str, object]]:
    """Replay and authenticate the complete game, retaining selected rows."""

    current = engine.inspect_fen(game.root_fen, timeout=timeout)
    current_fen = _normalized_inspection_fen(current)
    if current_fen != game.root_fen:
        raise MatchLogError(
            "engine root inspection differs from the authenticated root FEN"
        )

    rows: list[dict[str, object]] = []
    for root_ply, move in enumerate(game.moves):
        legal_moves = _validated_legal_moves(
            engine, current_fen, timeout=timeout
        )
        if move not in legal_moves:
            raise MatchLogError(
                f"move {move!r} is not legal at ply {root_ply}"
            )
        terminal_state = _terminal_state(
            legal_move_count=len(legal_moves),
            checkers=current.checkers,
        )
        if root_ply in selected_plies:
            rows.append(
                _position_row(
                    source=source,
                    game=game,
                    game_provenance=game_provenance,
                    root_ply=root_ply,
                    inspection=current,
                    legal_move_count=len(legal_moves),
                    terminal_state=terminal_state,
                )
            )

        next_state = engine.inspect_fen(
            current_fen, (move,), timeout=timeout
        )
        next_fen = _normalized_inspection_fen(next_state)
        _validate_transition(
            current_fen,
            next_fen,
            move=move,
            root_ply=root_ply,
        )

        # From ply two onward this is an independent command path.  It is not
        # used to establish legality; it detects parser truncation or state
        # drift relative to the authenticated game root.
        if root_ply > 0:
            from_root = engine.inspect_fen(
                game.root_fen,
                game.moves[: root_ply + 1],
                timeout=timeout,
            )
            _same_state(
                next_state,
                from_root,
                move=move,
                root_ply=root_ply,
            )
        current = next_state
        current_fen = next_fen
    return rows


def extract_match_positions(
    source_path: Path,
    output_path: Path,
    engine: EngineLike,
    *,
    min_ply: int = 5,
    stride: int = 1,
    max_per_game: int | None = 2,
    timeout: float | None = None,
    quarantine_path: Path | None = None,
) -> ExtractionSummary:
    """Extract canonical JSONL without ever replacing an existing artifact.

    Without ``quarantine_path`` every parse or replay/inspection error aborts
    before the output is published.  With it, the *entire* affected game is
    excluded and one diagnostic is written to the separate quarantine JSONL.
    This is the only supported recovery policy because retaining a partial
    trajectory would silently weaken split isolation.
    """

    if min_ply < 0:
        raise ValueError("min_ply must be non-negative")
    if stride <= 0:
        raise ValueError("stride must be positive")
    if max_per_game is not None and max_per_game <= 0:
        raise ValueError("max_per_game must be positive or null")
    if quarantine_path is not None and quarantine_path.absolute() == output_path.absolute():
        raise ValueError("quarantine_path and output_path must differ")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite mining artifact: {output_path}")
    if quarantine_path is not None and quarantine_path.exists():
        raise FileExistsError(
            f"refusing to overwrite mining artifact: {quarantine_path}"
        )

    source_path = source_path.resolve(strict=True)
    payload = common.read_stable_file_bytes(
        source_path, label="source match log"
    )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MatchLogError("source log must be strict UTF-8") from error

    source = _source_provenance(source_path, payload)
    source_sha256 = str(source["sha256"])
    rows: list[dict[str, object]] = []
    quarantine_rows: list[dict[str, object]] = []
    games_seen = 0
    games_emitted = 0

    for item in scan_variantfishtest_log(text):
        games_seen += 1
        if isinstance(item, ParseIssue):
            if quarantine_path is None:
                raise MatchLogError(item.error)
            quarantine_rows.append(
                _quarantine_row(
                    source=source,
                    game_index=item.game_index,
                    marker_line=item.marker_line,
                    stage="parse",
                    error=item.error,
                )
            )
            continue

        game = item
        game_info = _game_provenance(game, source_sha256=source_sha256)
        try:
            selected_plies = set(
                _selected_plies(
                    game,
                    min_ply=min_ply,
                    stride=stride,
                    max_per_game=max_per_game,
                )
            )
            game_rows = _replay_game(
                game,
                engine,
                selected_plies=selected_plies,
                source=source,
                game_provenance=game_info,
                timeout=timeout,
            )
        except Exception as error:
            if quarantine_path is None:
                raise MatchLogError(
                    f"game {game.game_index} at line {game.marker_line}: "
                    f"inspection failed: {error}"
                ) from error
            quarantine_rows.append(
                _quarantine_row(
                    source=source,
                    game_index=game.game_index,
                    marker_line=game.marker_line,
                    stage="inspect",
                    error=error,
                )
            )
            continue

        rows.extend(game_rows)
        games_emitted += 1

    if games_seen == 0:
        raise MatchLogError("source log contains no 'Game (atomic):' records")

    # Both writers are create-new.  Publish quarantine first so a late output
    # collision never loses the diagnostic artifact.  If output publication
    # fails, intentionally retain the already-authenticated quarantine file:
    # deleting it by pathname would be unsafe if an adversary swapped that
    # pathname between publication and rollback.
    if quarantine_path is not None:
        common.write_new_jsonl(quarantine_path, quarantine_rows)
        common.write_new_jsonl(output_path, rows)
    else:
        common.write_new_jsonl(output_path, rows)

    return ExtractionSummary(
        source_sha256=source_sha256,
        games_seen=games_seen,
        games_emitted=games_emitted,
        games_quarantined=len(quarantine_rows),
        positions_emitted=len(rows),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--min-ply", type=int, default=5)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--max-per-game", type=int, default=2)
    parser.add_argument("--timeout", type=float)
    parser.add_argument(
        "--quarantine",
        type=Path,
        help="explicitly quarantine complete malformed games instead of failing",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    from tools.atomic_mining.uci_session import UciEngine, UciOptionSetting

    with UciEngine(
        [str(args.engine)],
        options=(UciOptionSetting("UCI_Variant", "atomic"),),
    ) as engine:
        summary = extract_match_positions(
            args.source,
            args.output,
            engine,
            min_ply=args.min_ply,
            stride=args.stride,
            max_per_game=args.max_per_game,
            timeout=args.timeout,
            quarantine_path=args.quarantine,
        )
    print(
        json.dumps(
            {
                "source_sha256": summary.source_sha256,
                "games_seen": summary.games_seen,
                "games_emitted": summary.games_emitted,
                "games_quarantined": summary.games_quarantined,
                "positions_emitted": summary.positions_emitted,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
