from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pytest

import pyffish


FIXTURES = json.loads(
    (Path(__file__).parents[1] / "bindings" / "atomic-fixtures.json").read_text(
        encoding="utf-8"
    )
)["fixtures"]
BY_ID = {fixture["id"]: fixture for fixture in FIXTURES}


def fixture_rows(probe: str):
    return [
        pytest.param(fixture, id=fixture["id"])
        for fixture in FIXTURES
        if fixture.get("probe") == probe
    ]


def test_contract_is_atomic_only():
    start = BY_ID["contract.start-fen"]["expected"]

    assert len(pyffish.version()) == 3
    assert all(isinstance(component, int) for component in pyffish.version())
    assert pyffish.info().startswith("Atomic-Stockfish")
    assert pyffish.variants() == ["atomic"]
    assert pyffish.start_fen("atomic") == start
    assert pyffish.two_boards("atomic") is False
    assert pyffish.captures_to_hand("atomic") is False

    assert pyffish.set_option("UCI_Variant", "atomic") is None
    assert pyffish.set_option("Threads", 2) is None
    assert pyffish.set_option("Hash", "64") is None
    assert pyffish.set_option("UCI_Chess960", True) is None
    assert pyffish.set_option("Use NNUE", "pure") is None


@pytest.mark.parametrize("fixture", fixture_rows("legal_moves"))
def test_legal_moves(fixture):
    result = pyffish.legal_moves(
        fixture["variant"],
        fixture["fen"],
        fixture.get("moves", []),
        fixture.get("chess960", False),
    )
    assert sorted(result) == sorted(fixture["expected"])


@pytest.mark.parametrize("fixture", fixture_rows("get_fen"))
def test_fen_transitions(fixture):
    assert (
        pyffish.get_fen(
            fixture["variant"],
            fixture["fen"],
            fixture["moves"],
            fixture.get("chess960", False),
        )
        == fixture["expected"]
    )


@pytest.mark.parametrize("fixture", fixture_rows("get_san"))
def test_san(fixture):
    assert (
        pyffish.get_san(
            fixture["variant"],
            fixture["fen"],
            fixture["move"],
            fixture.get("chess960", False),
        )
        == fixture["expected"]
    )


def test_san_move_list_and_lan():
    fixture = BY_ID["lifecycle.quiet-opening"]
    assert pyffish.get_san_moves("atomic", fixture["fen"], fixture["moves"]) == fixture[
        "expected"
    ]["sanMoves"]
    assert pyffish.get_san(
        "atomic", fixture["fen"], "g1f3", False, pyffish.NOTATION_LAN
    ) == "Ng1-f3"


@pytest.mark.parametrize("fixture", fixture_rows("gives_check"))
def test_atomic_check_state(fixture):
    assert (
        pyffish.gives_check(
            fixture["variant"],
            fixture["fen"],
            fixture.get("moves", []),
            fixture.get("chess960", False),
        )
        is fixture["expected"]
    )


@pytest.mark.parametrize("fixture", fixture_rows("is_capture"))
def test_capture_classification(fixture):
    assert (
        pyffish.is_capture(
            fixture["variant"],
            fixture["fen"],
            fixture.get("moves", []),
            fixture["move"],
            fixture.get("chess960", False),
        )
        is fixture["expected"]
    )


@pytest.mark.parametrize("fixture", fixture_rows("game_result"))
def test_terminal_game_result(fixture):
    assert (
        pyffish.game_result(
            fixture["variant"],
            fixture["fen"],
            fixture.get("moves", []),
            fixture.get("chess960", False),
        )
        == fixture["expected"]
    )


@pytest.mark.parametrize("fixture", fixture_rows("is_immediate_game_end"))
def test_immediate_end(fixture):
    assert list(
        pyffish.is_immediate_game_end(
            fixture["variant"],
            fixture["fen"],
            fixture.get("moves", []),
            fixture.get("chess960", False),
        )
    ) == fixture["expected"]


@pytest.mark.parametrize("fixture", fixture_rows("is_optional_game_end"))
def test_optional_end(fixture):
    assert list(
        pyffish.is_optional_game_end(
            fixture["variant"],
            fixture["fen"],
            fixture.get("moves", []),
            fixture.get("chess960", False),
        )
    ) == fixture["expected"]


@pytest.mark.parametrize("fixture", fixture_rows("has_insufficient_material"))
def test_insufficient_material(fixture):
    assert list(
        pyffish.has_insufficient_material(
            fixture["variant"],
            fixture["fen"],
            fixture.get("moves", []),
            fixture.get("chess960", False),
        )
    ) == fixture["expected"]


@pytest.mark.parametrize("fixture", fixture_rows("validate_fen"))
def test_fen_validation(fixture):
    assert (
        pyffish.validate_fen(
            fixture["fen"], fixture["variant"], fixture.get("chess960", False)
        )
        == fixture["expected"]
    )


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        (
            "7k/8/8/8/8/8/8/K7 w - - not-a-number 1",
            pyffish.FEN_INVALID_HALF_MOVE_COUNTER,
        ),
        (
            "7k/8/8/8/8/8/8/K7 w - - 0 not-a-number",
            pyffish.FEN_INVALID_MOVE_COUNTER,
        ),
    ],
)
def test_fen_validation_rejects_nonnumeric_move_counters(fen, expected):
    assert pyffish.validate_fen(fen, "atomic") == expected


@pytest.mark.parametrize(
    "fen",
    [
        "7k/8/8/8/8/8/8/K7 w - -",
        "7k/8/8/8/8/8/8/K7 w - - not-a-number 1",
    ],
)
def test_board_backed_apis_reject_invalid_fen(fen):
    with pytest.raises(ValueError, match="Atomic FEN validation failed"):
        pyffish.legal_moves("atomic", fen, [])
    with pytest.raises(ValueError, match="Atomic FEN validation failed"):
        pyffish.get_fen("atomic", fen, [])


def test_fen_validation_reports_the_earliest_invalid_field():
    assert (
        pyffish.validate_fen(
            "7x/8/8/8/8/8/8/7K w - - not-a-number 1", "atomic"
        )
        == pyffish.FEN_INVALID_CHAR
    )
    assert (
        pyffish.validate_fen(
            "7k/8/8/8/8/8/8/K7 w K - not-a-number 1", "atomic"
        )
        == pyffish.FEN_INVALID_CASTLING_INFO
    )


def test_fen_validation_preserves_atomic_analysis_positions():
    assert pyffish.validate_fen(
        "4k3/8/8/1B6/8/8/8/4K3 w - - 0 1", "atomic"
    ) == pyffish.FEN_OK
    assert (
        pyffish.validate_fen("7k/7R/8/8/8/8/8/K7 w - - 0 1", "atomic")
        == pyffish.FEN_OK
    )


@pytest.mark.parametrize("fixture", fixture_rows("perft"))
def test_perft(fixture):
    fen = fixture["position"]
    assert (
        pyffish.perft(
            fixture["variant"],
            fen,
            fixture["depth"],
            fixture.get("chess960", False),
        )
        == fixture["expected"]
    )


def test_lifecycle_is_stateless_and_non_mutating():
    fixture = BY_ID["lifecycle.quiet-opening"]
    start = fixture["fen"]
    moves = fixture["moves"]

    assert pyffish.get_fen("atomic", start, moves) == fixture["expected"]["fen"]
    assert len(pyffish.legal_moves("atomic", start, moves)) == fixture["expected"][
        "legalMoveCount"
    ]
    assert pyffish.get_fen("atomic", start, []) == start


def test_errors_are_explicit_and_transactional():
    with pytest.raises(ValueError, match="only the 'atomic' variant"):
        pyffish.start_fen("chess")
    with pytest.raises(ValueError, match="only the 'atomic' variant"):
        pyffish.start_fen("")
    with pytest.raises(ValueError, match="only the 'atomic' variant"):
        pyffish.set_option("UCI_Variant", "")
    with pytest.raises(ValueError, match="only the 'atomic' variant"):
        pyffish.legal_moves("crazyhouse", "startpos", [])
    with pytest.raises(ValueError, match="invalid Atomic UCI move"):
        pyffish.get_fen("atomic", "startpos", ["e2e5"])
    with pytest.raises(ValueError, match="no such Atomic-Stockfish option"):
        pyffish.set_option("UCCI_Variant", "atomic")
    with pytest.raises(ValueError, match="Use NNUE"):
        pyffish.set_option("Use NNUE", "mixed")
    with pytest.raises(TypeError, match="movelist must be a list"):
        pyffish.legal_moves("atomic", "startpos", ("e2e4",))

    # A failed call cannot leak partial state into the next stateless call.
    assert len(pyffish.legal_moves("atomic", "startpos", [])) == 20


def test_concurrent_independent_calls():
    jobs = [
        ("startpos", ["e2e4", "e7e5"]),
        ("startpos", ["d2d4", "d7d5"]),
        (BY_ID["transition.explosion"]["fen"], []),
        (BY_ID["legal.adjacent-kings"]["fen"], []),
    ] * 16

    def probe(job):
        fen, moves = job
        return pyffish.get_fen("atomic", fen, moves), tuple(
            sorted(pyffish.legal_moves("atomic", fen, moves))
        )

    expected = [probe(job) for job in jobs]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(probe, jobs)) == expected
