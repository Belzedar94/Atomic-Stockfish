# -*- coding: utf-8 -*-
"""Atomic-only compatibility suite for the historical ``pyffish`` API.

Build the extension in place with ``python setup.py build_ext --inplace``, then
run this file directly with ``python test.py``.  The fixture and inventory files
are shared with the JavaScript/WASM binding tests so all public surfaces assert
the same Atomic and Atomic960 behavior.
"""

from __future__ import annotations

import faulthandler
import json
import unittest
from pathlib import Path

try:
    import pyffish as sf
except ImportError as exc:  # pragma: no cover - actionable bootstrap failure
    raise ImportError(
        "pyffish is not built in place; run "
        "`python setup.py build_ext --inplace` before `python test.py`"
    ) from exc


faulthandler.enable()

ROOT = Path(__file__).resolve().parent
FIXTURE_DOCUMENT = json.loads(
    (ROOT / "tests" / "bindings" / "atomic-fixtures.json").read_text(
        encoding="utf-8"
    )
)
INVENTORY_DOCUMENT = json.loads(
    (ROOT / "tests" / "bindings" / "inventory.json").read_text(encoding="utf-8")
)

FIXTURES = {fixture["id"]: fixture for fixture in FIXTURE_DOCUMENT["fixtures"]}
PYTHON_CONTRACTS = {
    contract["sourceId"]: contract for contract in INVENTORY_DOCUMENT["pythonTests"]
}

NOT_APPLICABLE = {"test_piece_to_partner", "test_get_fog_fen"}
ADDITIONAL_REQUIRED = {"test_perft", "test_errors_are_explicit"}


def fixtures_for(source_id: str) -> list[dict]:
    """Return the frozen fixtures assigned to one historical test contract."""

    return [FIXTURES[fixture_id] for fixture_id in PYTHON_CONTRACTS[source_id]["fixtures"]]


def call_fixture(function, fixture: dict, *, move: bool = False):
    """Call a stateless pyffish probe using the common fixture fields."""

    arguments = [
        fixture["variant"],
        fixture["fen"],
        fixture.get("moves", []),
    ]
    if move:
        arguments.append(fixture["move"])
    arguments.append(fixture.get("chess960", False))
    return function(*arguments)


class TestPyffish(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The frozen source had 22 named tests. The two removed-variant APIs are
        # classified in inventory.json instead of becoming permanent skips.
        if len(PYTHON_CONTRACTS) != 22:
            raise AssertionError("the frozen Python inventory must contain 22 contracts")

        not_applicable = {
            source_id
            for source_id, contract in PYTHON_CONTRACTS.items()
            if contract["status"] == "not-applicable"
        }
        if not_applicable != NOT_APPLICABLE:
            raise AssertionError(
                f"unexpected not-applicable Python contracts: {sorted(not_applicable)}"
            )

        applicable = set(PYTHON_CONTRACTS) - NOT_APPLICABLE
        implemented = {
            name for name in cls.__dict__ if name.startswith("test_")
        }
        expected = applicable | ADDITIONAL_REQUIRED
        if implemented != expected:
            raise AssertionError(
                "test.py and inventory.json disagree: "
                f"missing={sorted(expected - implemented)}, "
                f"extra={sorted(implemented - expected)}"
            )

    def test_version(self) -> None:
        expected = FIXTURES["contract.version-shape"]["expected"]
        version = sf.version()
        self.assertIsInstance(version, tuple)
        self.assertEqual(len(version), expected["length"])
        self.assertTrue(all(isinstance(component, int) for component in version))

    def test_info(self) -> None:
        prefix = FIXTURES["contract.engine-info"]["expected"]["prefix"]
        self.assertTrue(sf.info().startswith(prefix))

    def test_variants_loaded(self) -> None:
        self.assertEqual(sf.variants(), FIXTURES["contract.variant-list"]["expected"])

    def test_set_option(self) -> None:
        options = (
            ("UCI_Variant", "atomic"),
            ("Threads", 2),
            ("Hash", "64"),
            ("UCI_Chess960", True),
            ("Use NNUE", "pure"),
        )
        for name, value in options:
            with self.subTest(option=name, value=value):
                self.assertIsNone(sf.set_option(name, value))

        # Do not leak mutable engine options into callers that import this suite.
        self.assertIsNone(sf.set_option("Threads", 1))
        self.assertIsNone(sf.set_option("Hash", 16))
        self.assertIsNone(sf.set_option("UCI_Chess960", False))
        self.assertIsNone(sf.set_option("Use NNUE", False))

    def test_two_boards(self) -> None:
        fixture = FIXTURES["contract.single-board"]
        self.assertIs(sf.two_boards(fixture["variant"]), fixture["expected"])

    def test_captures_to_hand(self) -> None:
        fixture = FIXTURES["contract.no-pockets"]
        self.assertIs(sf.captures_to_hand(fixture["variant"]), fixture["expected"])

    def test_start_fen(self) -> None:
        fixture = FIXTURES["contract.start-fen"]
        self.assertEqual(sf.start_fen(fixture["variant"]), fixture["expected"])

    def test_legal_moves(self) -> None:
        for fixture in fixtures_for("test_legal_moves"):
            with self.subTest(fixture=fixture["id"]):
                actual = call_fixture(sf.legal_moves, fixture)
                self.assertEqual(sorted(actual), sorted(fixture["expected"]))

    def test_castling(self) -> None:
        for fixture in fixtures_for("test_castling"):
            with self.subTest(fixture=fixture["id"]):
                actual = call_fixture(sf.legal_moves, fixture)
                self.assertEqual(sorted(actual), sorted(fixture["expected"]))

        legal = call_fixture(sf.legal_moves, FIXTURES["atomic960.castle-legal"])
        illegal = call_fixture(sf.legal_moves, FIXTURES["atomic960.castle-illegal"])
        self.assertIn("c1b1", legal)
        self.assertNotIn("c1b1", illegal)

    def test_get_fen(self) -> None:
        for fixture in fixtures_for("test_get_fen"):
            with self.subTest(fixture=fixture["id"]):
                expected = fixture["expected"]
                if fixture["id"] == "lifecycle.quiet-opening":
                    self.assertEqual(call_fixture(sf.get_fen, fixture), expected["fen"])
                    self.assertEqual(
                        sf.get_fen(
                            fixture["variant"], fixture["fen"], fixture["moves"][:-1]
                        ),
                        expected["fenAfterPop"],
                    )
                    self.assertEqual(
                        sf.get_fen(fixture["variant"], fixture["fen"], []),
                        expected["fenAfterReset"],
                    )
                else:
                    self.assertEqual(call_fixture(sf.get_fen, fixture), expected)

    def test_get_san(self) -> None:
        for fixture in fixtures_for("test_get_san"):
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(
                    sf.get_san(
                        fixture["variant"],
                        fixture["fen"],
                        fixture["move"],
                        fixture.get("chess960", False),
                    ),
                    fixture["expected"],
                )

        quiet = FIXTURES["san.quiet"]
        self.assertEqual(
            sf.get_san(
                quiet["variant"],
                quiet["fen"],
                quiet["move"],
                False,
                sf.NOTATION_SAN,
            ),
            quiet["expected"],
        )
        self.assertEqual(
            sf.get_san(
                quiet["variant"],
                quiet["fen"],
                quiet["move"],
                False,
                sf.NOTATION_LAN,
            ),
            "Ng1-f3",
        )

    def test_get_san_moves(self) -> None:
        fixture = fixtures_for("test_get_san_moves")[0]
        expected = fixture["expected"]
        self.assertEqual(
            sf.get_san_moves(fixture["variant"], fixture["fen"], fixture["moves"]),
            expected["sanMoves"],
        )
        self.assertEqual(
            sf.get_san_moves(
                fixture["variant"],
                fixture["fen"],
                fixture["moves"],
                False,
                sf.NOTATION_LAN,
            ),
            ["e2-e4", "e7-e5", "Ng1-f3"],
        )

    def test_gives_check(self) -> None:
        for fixture in fixtures_for("test_gives_check"):
            with self.subTest(fixture=fixture["id"]):
                self.assertIs(call_fixture(sf.gives_check, fixture), fixture["expected"])

    def test_is_capture(self) -> None:
        for fixture in fixtures_for("test_is_capture"):
            with self.subTest(fixture=fixture["id"]):
                self.assertIs(
                    call_fixture(sf.is_capture, fixture, move=True), fixture["expected"]
                )

    def test_game_result(self) -> None:
        for fixture in fixtures_for("test_game_result"):
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(call_fixture(sf.game_result, fixture), fixture["expected"])

    def test_is_immediate_game_end(self) -> None:
        for fixture in fixtures_for("test_is_immediate_game_end"):
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(
                    list(call_fixture(sf.is_immediate_game_end, fixture)),
                    fixture["expected"],
                )

    def test_is_optional_game_end(self) -> None:
        policy, fifty_move, repetition = fixtures_for("test_is_optional_game_end")
        expected_policy = policy["expected"]
        self.assertEqual(
            int(fifty_move["fen"].split()[4]),
            expected_policy["fiftyMoveClaimAtHalfmove"],
        )
        self.assertEqual(
            repetition["moves"], expected_policy["repetitionClaimAfterMoves"]
        )

        for fixture in (fifty_move, repetition):
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(
                    list(call_fixture(sf.is_optional_game_end, fixture)),
                    fixture["expected"],
                )

    def test_has_insufficient_material(self) -> None:
        for fixture in fixtures_for("test_has_insufficient_material"):
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(
                    list(call_fixture(sf.has_insufficient_material, fixture)),
                    fixture["expected"],
                )

    def test_validate_fen(self) -> None:
        for fixture in fixtures_for("test_validate_fen"):
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(
                    sf.validate_fen(
                        fixture["fen"],
                        fixture["variant"],
                        fixture.get("chess960", False),
                    ),
                    fixture["expected"],
                )

    def test_validate_fen_promoted_pieces(self) -> None:
        for fixture in fixtures_for("test_validate_fen_promoted_pieces"):
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(
                    sf.validate_fen(
                        fixture["fen"],
                        fixture["variant"],
                        fixture.get("chess960", False),
                    ),
                    fixture["expected"],
                )

    def test_perft(self) -> None:
        for fixture in FIXTURE_DOCUMENT["fixtures"]:
            if fixture.get("probe") != "perft":
                continue
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(
                    sf.perft(
                        fixture["variant"],
                        fixture["position"],
                        fixture["depth"],
                        fixture.get("chess960", False),
                    ),
                    fixture["expected"],
                )

    def test_errors_are_explicit(self) -> None:
        atomic_only = "only the 'atomic' variant"
        for call in (
            lambda: sf.start_fen("chess"),
            lambda: sf.start_fen(""),
            lambda: sf.set_option("UCI_Variant", ""),
            lambda: sf.legal_moves("crazyhouse", "startpos", []),
        ):
            with self.subTest(error="removed variant"):
                with self.assertRaisesRegex(ValueError, atomic_only):
                    call()

        with self.assertRaisesRegex(ValueError, "invalid Atomic UCI move"):
            sf.get_fen("atomic", "startpos", ["e2e5"])
        with self.assertRaisesRegex(ValueError, "no such Atomic-Stockfish option"):
            sf.set_option("UCCI_Variant", "atomic")
        with self.assertRaisesRegex(ValueError, "Use NNUE"):
            sf.set_option("Use NNUE", "mixed")
        with self.assertRaisesRegex(TypeError, "movelist must be a list"):
            sf.legal_moves("atomic", "startpos", ("e2e4",))

        # Failed calls cannot leak a partial move or variant into the next call.
        start = FIXTURES["contract.start-fen"]["expected"]
        self.assertEqual(sf.get_fen("atomic", "startpos", []), start)
        self.assertEqual(len(sf.legal_moves("atomic", "startpos", [])), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
